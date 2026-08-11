"""Dependency graph for batch audits (read-only).

Builds the dependency tree of every calculation in a batch root — what
each node needs (upstream dependencies), who it blocks (downstream
nodes waiting on it), each node's runtime state, and a global bottleneck
ranking (nodes whose unmet dependency blocks the most downstream work).
Nothing here writes to JobStore, files, or crisp.

Node kinds:
    system        — one project directory (root of the tree)
    phase         — a position in the phase chain (STRUCTURE_OPT → … → COMPLETE)
    unitcell-task — structure_opt / band / dos / dielectric
    cpd-dir       — one competing-phase directory
    defect-chain  — a defect group (charge suffix stripped, ADR 0010)
    defect-dir    — one charge-state directory
    wave3         — chempot / analysis gate (formation energies)

Edges are *upstream dependencies*: node.deps lists what must be ready
before the node can finish.  A node whose deps are not all ready is
``waiting``; each such blocked node adds 1 to the bottleneck score of
every unmet dep.

States (disk-first, crisp second, JobStore third):
    converged / running / waiting / waiting-seed / failed / no-input /
    not-run / complete / blocked
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from vasp_sop.core.config import PipelineConfig
from vasp_sop.vasp.convergence import convergence_verdict
from vasp_sop.vasp.io import input_ready

PHASE_CHAIN = (
    "STRUCTURE_OPT", "COMPETING", "CHEM_POT_DIAGRAM",
    "UNITCELL_DEFECT", "COMPLETE",
)

# ── crisp / JobStore lookups (best-effort, never fatal) ──────────────


def _crisp_status(local_dir: Path) -> str | None:
    """Latest crisp job status for *local_dir*, or None when unavailable."""
    try:
        import sqlite3
        import os

        db = Path(os.path.expanduser("~/.crisp/data/agent.db"))
        if not db.is_file():
            return None
        con = sqlite3.connect(db, timeout=5)
        try:
            row = con.execute(
                "select status from jobs where local_dir = ? "
                "order by rowid desc limit 1", (str(local_dir),)
            ).fetchone()
            return row[0] if row else None
        finally:
            con.close()
    except Exception:
        return None


def _jobstore_latest(local_dir: Path) -> str | None:
    """Latest JobStore status for *local_dir*, or None."""
    try:
        import sqlite3
        import os

        db = Path(os.path.expanduser("~/.vasp_sop/jobs.db"))
        if not db.is_file():
            return None
        con = sqlite3.connect(db, timeout=5)
        try:
            row = con.execute(
                "select status from job_history where dir_path = ? "
                "order by timestamp desc limit 1", (str(local_dir),)
            ).fetchone()
            return row[0] if row else None
        finally:
            con.close()
    except Exception:
        return None


def _jobstore_history(local_dir: Path) -> list[dict]:
    try:
        import sqlite3
        import os

        db = Path(os.path.expanduser("~/.vasp_sop/jobs.db"))
        if not db.is_file():
            return []
        con = sqlite3.connect(db, timeout=5)
        try:
            rows = con.execute(
                "select status, source, reason from job_history "
                "where dir_path = ? order by timestamp",
                (str(local_dir),),
            ).fetchall()
            return [{"status": r[0], "source": r[1], "reason": r[2]}
                    for r in rows]
        finally:
            con.close()
    except Exception:
        return []


# ── nodes ─────────────────────────────────────────────────────────────


@dataclass
class Node:
    id: str
    kind: str            # system|phase|unitcell-task|cpd-dir|defect-chain|defect-dir|wave3
    label: str
    path: str | None = None
    deps: list[str] = field(default_factory=list)   # upstream node ids
    status: str = "not-run"
    detail: str = ""
    n_ok: int = 0        # children converged
    n_total: int = 0
    children: list[str] = field(default_factory=list)
    bottleneck: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "label": self.label,
            "path": self.path, "deps": self.deps, "status": self.status,
            "detail": self.detail, "n_ok": self.n_ok, "n_total": self.n_total,
            "children": self.children, "bottleneck": self.bottleneck,
        }


def _dir_status(path: Path, *, jobstore_fallback: bool = False) -> tuple[str, str]:
    """Disk-first status for a single calculation directory.

    *jobstore_fallback*: unitcell tasks are never re-run after their
    phase passed — a converged/pending JobStore record with no usable
    disk inputs is an archival artifact, not a missing calculation.
    """
    if jobstore_fallback:
        js = _jobstore_latest(path)
        if js in ("converged", "pending"):
            return "converged", f"jobstore:{js} (phase passed)"
    if not input_ready(path):
        return "no-input", "missing INCAR/POSCAR/POTCAR/KPOINTS"
    v = convergence_verdict(path)
    if v.converged:
        return "converged", v.reason or ""
    crisp = _crisp_status(path)
    if crisp in ("running", "submitted", "submit", "ready_fetch"):
        return "running", f"crisp:{crisp}"
    js = _jobstore_latest(path)
    if v.reason == "electronic_not_conv":
        return "failed", "NELM exhaustion (not auto-retried)"
    if js == "failed":
        return "failed", v.reason or "failed"
    return "not-run", v.reason or ""


# ── graph builder ─────────────────────────────────────────────────────


def build_graph(root: Path, *, system_filter: str | None = None) -> dict:
    """Build the dependency graph for every system under *root*.

    Read-only: uses disk verdicts, crisp agent.db and the vasp-sop
    JobStore.  Returns a JSON-serialisable dict.
    """
    from vasp_sop.core.system import System
    from vasp_sop.defect import is_valid_defect_dir
    import re

    nodes: dict[str, Node] = {}
    systems: list[str] = []

    for sys_dir in sorted(root.iterdir()):
        if not sys_dir.is_dir() or not (sys_dir / "plan.yaml").is_file():
            continue
        name = sys_dir.name
        if system_filter and name != system_filter:
            continue
        systems.append(name)
        try:
            cfg = PipelineConfig.from_yaml(sys_dir / "plan.yaml", root=sys_dir)
            sys_model = System(sys_dir, cfg)
        except Exception as exc:
            nodes[f"sys:{name}"] = Node(
                id=f"sys:{name}", kind="system", label=name,
                status="blocked", detail=f"plan parse failed: {exc}",
            )
            continue

        sid = f"sys:{name}"
        snode = Node(id=sid, kind="system", label=name, path=str(sys_dir))
        nodes[sid] = snode

        def _group(gid: str, label: str, child_ids: list[str],
                   kind: str = "task-group") -> str:
            g = Node(id=gid, kind=kind, label=label)
            g.children = child_ids
            g.n_total = len(child_ids)
            g.n_ok = sum(1 for i in child_ids
                         if nodes.get(i) and nodes[i].status == "converged")
            g.status = (
                "converged" if g.n_ok == g.n_total and g.n_total else
                "running" if any(nodes.get(i) and nodes[i].status == "running"
                                 for i in child_ids) else
                "not-run"
            )
            nodes[gid] = g
            snode.children.append(gid)
            return gid

        # ── unitcell tasks ────────────────────────────────────────────
        uc_root = sys_dir / "unitcell"
        uc_ids: list[str] = []
        if uc_root.is_dir():
            for task in ("structure_opt", "band", "dos", "dielectric"):
                td = uc_root / task
                if not td.is_dir():
                    continue
                nid = f"{sid}:uc:{task}"
                st, det = _dir_status(td, jobstore_fallback=True)
                n = Node(id=nid, kind="unitcell-task", label=task,
                         path=str(td), status=st, detail=det)
                nodes[nid] = n
                uc_ids.append(nid)
        if uc_ids:
            _group(f"{sid}:group:uc", f"unitcell ({len(uc_ids)})", uc_ids)

        # ── cpd phases ────────────────────────────────────────────────
        cpd_root = sys_dir / "cpd"
        cpd_ids: list[str] = []
        if cpd_root.is_dir():
            for pd in sorted(cpd_root.iterdir()):
                if not pd.is_dir() or pd.name == "combos":
                    continue
                nid = f"{sid}:cpd:{pd.name}"
                st, det = _dir_status(pd)
                n = Node(id=nid, kind="cpd-dir", label=pd.name, path=str(pd),
                         status=st, detail=det)
                nodes[nid] = n
                cpd_ids.append(nid)
        if cpd_ids:
            gid = _group(f"{sid}:group:cpd", f"cpd ({len(cpd_ids)})", cpd_ids)
            g = nodes[gid]
            g.detail = f"{g.n_ok}/{g.n_total} converged"

        # ── defect chains (ADR 0010 grouping) ─────────────────────────
        df_root = sys_dir / "defect"
        chain_ids: list[str] = []
        if df_root.is_dir():
            chains: dict[str, list[Path]] = {}
            for dd in sorted(df_root.iterdir()):
                if not dd.is_dir() or not is_valid_defect_dir(dd):
                    continue
                if dd.name == "perfect":
                    continue
                key = re.sub(r"_(-?\d+)$", "", dd.name)
                chains.setdefault(key, []).append(dd)
            for key, dirs in sorted(chains.items()):
                nid = f"{sid}:df:{key}"
                cn = Node(id=nid, kind="defect-chain", label=key)
                nodes[nid] = cn
                chain_ids.append(nid)
                charges = sorted(
                    int(m.group(1))
                    for m in (re.search(r"_(-?\d+)$", d.name) for d in dirs)
                    if m
                )
                roots = _chain_roots(charges)
                dir_ids = []
                for dd in sorted(dirs):
                    did = f"{nid}:{dd.name}"
                    st, det = _dir_status(dd)
                    q = _defect_charge(dd.name)
                    dn = Node(id=did, kind="defect-dir", label=dd.name,
                              path=str(dd), status=st, detail=det)
                    # seeding dependency: non-root charges wait on a
                    # converged sibling (ADR 0010)
                    if q is not None and q not in roots:
                        dn.deps = [f"{nid}:{s.name}" for s in dirs
                                   if _defect_charge(s.name) in roots]
                        # never submitted + no converged sibling -> waiting
                        if st == "not-run" and not _jobstore_history(dd):
                            dn.status = "waiting-seed"
                            dn.detail = "waits for root charge (ADR 0010)"
                    nodes[did] = dn
                    cn.children.append(did)
                    dir_ids.append(did)
                cn.n_total = len(dir_ids)
                cn.n_ok = sum(1 for i in dir_ids
                              if nodes[i].status == "converged")
                cn.status = (
                    "converged" if cn.n_ok == cn.n_total and cn.n_total
                    else "running" if any(nodes[i].status == "running"
                                          for i in dir_ids)
                    else "waiting-seed" if any(
                        nodes[i].status == "waiting-seed" for i in dir_ids
                    ) or (cn.n_total and cn.n_ok == 0)
                    else "not-run"
                )
            if chain_ids:
                gid = _group(f"{sid}:group:df",
                             f"defects ({len(chain_ids)} chains)",
                             chain_ids)
                g = nodes[gid]
                g.detail = f"{g.n_ok}/{g.n_total} chains converged"
                # chains that are all waiting-seed make the group wait too
                if g.status == "not-run" and all(
                    nodes[i].status == "waiting-seed"
                    for i in chain_ids if nodes.get(i)
                ):
                    g.status = "waiting-seed"

        # ── phase chain ───────────────────────────────────────────────
        try:
            phase = sys_model.derive_phase(_FakeStore())
        except Exception:
            phase = "?"
        snode.status = "complete" if phase == "COMPLETE" else "blocked"
        # phase chain nodes: current phase and everything downstream
        start = PHASE_CHAIN.index(phase) if phase in PHASE_CHAIN else 0
        for ph in PHASE_CHAIN[start:]:
            pid = f"{sid}:phase:{ph}"
            pn = Node(id=pid, kind="phase", label=ph)
            nodes[pid] = pn
            snode.children.append(pid)
        # A system past STRUCTURE_OPT has finished its unitcell
        # relaxation — a structure_opt dir without disk inputs is an
        # archival artifact, not a missing calculation (do not let it
        # bottleneck every cpd/defect below).
        if phase != "STRUCTURE_OPT":
            so_id = f"{sid}:uc:structure_opt"
            so = nodes.get(so_id)
            if so is not None and so.status != "converged":
                so.status = "converged"
                so.detail = "phase passed (archival)"

        # wave3 gates: chempot needs all cpd; analysis needs defects + UC
        w3 = Node(id=f"{sid}:wave3", kind="wave3",
                  label="formation energies (wave3)")
        w3.deps = cpd_ids + chain_ids + uc_ids
        nodes[w3.id] = w3
        snode.children.append(w3.id)
        snode.detail = f"phase={phase}"

    # ── dependency edges: defect input needs perfect; cpd needs UC ────
    for nid, node in nodes.items():
        if node.kind in ("cpd-dir", "defect-chain", "defect-dir"):
            uc_so = f"{nid.split(':')[0]}:{nid.split(':')[1]}:uc:structure_opt"
            if uc_so in nodes and uc_so not in node.deps:
                node.deps.append(uc_so)

    # ── bottleneck scoring ───────────────────────────────────────────
    # For every node with an unmet upstream dependency, that dependency
    # earns +1 (it is blocking this node).  The global bottleneck list is
    # the sorted ranking — nodes whose unmet state blocks the most
    # downstream work.
    bottlenecks: Counter[str] = Counter()
    for nid, n in nodes.items():
        if n.kind in ("system", "phase"):
            continue
        for dep in n.deps:
            dn = nodes.get(dep)
            if dn is not None and dn.status not in ("converged", "complete"):
                bottlenecks[dep] += 1
    for nid, score in bottlenecks.items():
        if nid in nodes:
            nodes[nid].bottleneck = score

    return {
        "generated_at": _now_iso(),
        "root": str(root),
        "nodes": [n.to_dict() for n in nodes.values()],
        "systems": systems,
        "bottlenecks": [
            {"id": nid, "label": nodes[nid].label, "score": s,
             "status": nodes[nid].status}
            for nid, s in bottlenecks.most_common()
        ],
    }


class _FakeStore:
    """Minimal JobStore stand-in for System.derive_phase (read-only)."""

    def latest(self, path: str) -> str | None:
        js = _jobstore_latest(Path(path))
        return js if js in ("submitted", "converged", "failed") else None

    def history(self, path: str) -> list[dict]:
        return _jobstore_history(Path(path))


def _defect_charge(name: str) -> int | None:
    import re

    m = re.search(r"_(-?\d+)$", name)
    return int(m.group(1)) if m else None


def _chain_roots(charges: list[int]) -> set[int]:
    """Median charge states — the chain's starting points (ADR 0010)."""
    qs = sorted(charges)
    n = len(qs)
    if n == 0:
        return set()
    if n % 2 == 1:
        return {qs[n // 2]}
    return {qs[n // 2 - 1], qs[n // 2]}


def _now_iso() -> str:
    import datetime

    return datetime.datetime.now().isoformat(timespec="seconds")


# ── rendering ─────────────────────────────────────────────────────────

_STATUS_CHAR = {
    "converged": "✓", "complete": "✓", "running": "▶", "waiting": "⏳",
    "waiting-seed": "⏳", "failed": "✗", "no-input": "!", "not-run": "·",
    "blocked": "⛔",
}


def render_tree(graph: dict) -> str:
    """Plain-text indented tree of the dependency graph."""
    nodes = {n["id"]: n for n in graph["nodes"]}
    lines: list[str] = []
    for n in graph["nodes"]:
        if n["kind"] != "system":
            continue
        lines.append(
            f"{n['label']}  [{n['detail']}]  "
            f"{n['n_ok']}/{n['n_total']} converged"
        )
        for cid in n["children"]:
            if cid in nodes:
                _render_node(lines, nodes, nodes[cid], "  ")

    if graph["bottlenecks"]:
        lines.append("")
        lines.append("bottlenecks (blocked downstream count):")
        for b in graph["bottlenecks"][:10]:
            lines.append(
                f"  {b['score']:>3}  {b['label']:<40} [{b['status']}]"
            )
    return "\n".join(lines)


def _render_node(lines: list[str], nodes: dict, n: dict, indent: str) -> None:
    ch = _STATUS_CHAR.get(n["status"], "?")
    deps = ""
    if n["deps"]:
        short = [nodes[d]["label"] for d in n["deps"] if d in nodes]
        shown = short[:5]
        more = f" …+{len(short) - 5}" if len(short) > 5 else ""
        deps = f"  ← needs: {', '.join(shown)}{more}"
    frac = f" {n['n_ok']}/{n['n_total']}" if n["n_total"] else ""
    bn = f"  [blocks {n['bottleneck']}]" if n["bottleneck"] else ""
    lines.append(f"{indent}{ch} {n['label']}{frac}{bn}{deps}")
    for cid in n.get("children", []):
        if cid in nodes:
            _render_node(lines, nodes, nodes[cid], indent + "  ")


def render_mermaid(graph: dict) -> str:
    """Mermaid graph of systems → nodes, edges = upstream deps."""
    nodes = {n["id"]: n for n in graph["nodes"]}
    out = ["graph TD"]
    ids: dict[str, str] = {}
    for i, n in enumerate(graph["nodes"]):
        mid = f"n{i}"
        ids[n["id"]] = mid
        label = n["label"].replace('"', "'")
        if n["kind"] == "system":
            out.append(f'    {mid}["{label}"]:::sys')
        else:
            out.append(f'    {mid}["{label}"]')
    for nid, n in nodes.items():
        for dep in n["deps"]:
            if dep in ids:
                out.append(f"    {ids[dep]} --> {ids[nid]}")
    out.append("    classDef sys fill:#eef;")
    return "\n".join(out)


def to_json(graph: dict) -> str:
    return json.dumps(graph, ensure_ascii=False, indent=2)
