"""Read-only runtime dependency audit for a vasp-sop batch.

The report distinguishes four relation layers:

* ``runtime_gate`` — a condition that currently gates downstream work;
* ``lineage`` — a POSCAR/seed/result provenance relation;
* ``dispatch`` — an actual orchestrator fan-out/fan-in relation;
* ``containment`` — system/group/chain ownership, not a blocking edge.

Only runtime gates contribute to blocking-root scoring. This module never
writes JobStore, calculation directories, or crisp state.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from vasp_sop.core.config import PipelineConfig
from vasp_sop.core.retry_policy import (
    evaluate_cpd,
    evaluate_defect,
    has_zbrent_failure,
)
from vasp_sop.vasp.convergence import convergence_verdict
from vasp_sop.vasp.io import input_ready

RUNTIME_GATE = "runtime_gate"
LINEAGE = "lineage"
DISPATCH = "dispatch"
CONTAINMENT = "containment"
_RELATION_TYPES = (RUNTIME_GATE, LINEAGE, DISPATCH, CONTAINMENT)
_READY = frozenset(("converged", "complete"))


# ── crisp / JobStore lookups (best effort, never fatal) ───────────────
def _crisp_status(local_dir: Path) -> str | None:
    try:
        db = Path(os.path.expanduser("~/.crisp/data/agent.db"))
        if not db.is_file():
            return None
        con = sqlite3.connect(db, timeout=5)
        try:
            row = con.execute(
                "select status from jobs where local_dir = ? "
                "order by rowid desc limit 1",
                (str(local_dir),),
            ).fetchone()
            return row[0] if row else None
        finally:
            con.close()
    except Exception:
        return None


def _jobstore_latest(local_dir: Path) -> str | None:
    try:
        db = Path(os.path.expanduser("~/.vasp_sop/jobs.db"))
        if not db.is_file():
            return None
        con = sqlite3.connect(db, timeout=5)
        try:
            row = con.execute(
                "select status from job_history where dir_path = ? "
                "order by timestamp desc limit 1",
                (str(local_dir),),
            ).fetchone()
            return row[0] if row else None
        finally:
            con.close()
    except Exception:
        return None


def _jobstore_history(local_dir: Path) -> list[dict]:
    try:
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
            return [{"status": r[0], "source": r[1], "reason": r[2]} for r in rows]
        finally:
            con.close()
    except Exception:
        return []


# ── node model ────────────────────────────────────────────────────────
@dataclass
class Node:
    id: str
    kind: str
    label: str
    path: str | None = None
    deps: list[str] = field(default_factory=list)  # runtime-gate upstream IDs
    status: str = "not-run"
    detail: str = ""
    disposition: str = "none"  # wait | automatic | manual | none
    explanation: str = ""  # canonical retry-policy reason (from Decision)
    n_ok: int = 0
    n_total: int = 0
    children: list[str] = field(default_factory=list)  # containment only
    bottleneck: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "path": self.path,
            "deps": self.deps,
            "status": self.status,
            "detail": self.detail,
            "disposition": self.disposition,
            "explanation": self.explanation,
            "n_ok": self.n_ok,
            "n_total": self.n_total,
            "children": self.children,
            "bottleneck": self.bottleneck,
        }


def _dir_evidence(path: Path) -> dict:
    """Raw retry-policy evidence for one calculation directory (read-only).

    Mirrors the executor's evidence hand-off in ``orchestrator.wave2_submit``:
    the latest JobStore state, the convergence verdict (converged flag +
    reason), CONTCAR existence, ionic-restart history, and the ZBRENT probe.
    The verdict is evaluated without touching the persistent verdict memo
    (``cache=False``), so graph construction never writes anything — no
    JobStore, inputs, crisp rows, or verdict-cache sidecar.
    """
    verdict = convergence_verdict(path, cache=False)
    latest = _jobstore_latest(path)
    history = _jobstore_history(path)
    return {
        "latest_state": latest,
        "verdict_converged": verdict.converged,
        "verdict_reason": getattr(verdict, "reason", None),
        "ionic_restarts": sum(
            1 for row in history if row.get("source") == "ionic_restart"
        ),
        "has_conticar": (path / "CONTCAR").is_file(),
        "has_zbrent": has_zbrent_failure(path),
        "history": history,
    }


def _dir_status(path: Path, *, jobstore_fallback: bool = False) -> tuple[str, str]:
    """Disk-first status; fallback is reserved for staging structure_opt."""
    if jobstore_fallback:
        js = _jobstore_latest(path)
        if js in ("converged", "pending"):
            return "converged", f"jobstore:{js} (staging phase passed)"
    if not input_ready(path):
        return "no-input", "missing INCAR/POSCAR/POTCAR/KPOINTS"
    evidence = _dir_evidence(path)
    if evidence["verdict_converged"]:
        return "converged", evidence["verdict_reason"] or ""
    crisp = _crisp_status(path)
    if crisp in ("running", "submitted", "submit", "ready_fetch"):
        return "running", f"crisp:{crisp}"
    latest = evidence["latest_state"]
    if latest == "submitted":
        # In flight in JobStore but invisible to crisp (fresh submit before
        # the daemon poll); retry policy waits for its outcome either way.
        return "running", "jobstore:submitted (in flight)"
    if evidence["verdict_reason"] == "electronic_not_conv":
        return "failed", "NELM exhaustion (not auto-retried)"
    if latest == "converged":
        # ADR 0016: the record is stale — disk truth says unconverged.
        return "failed", (
            "stale JobStore 'converged' record; disk verdict "
            f"{evidence['verdict_reason'] or 'unconverged'}"
        )
    if latest == "failed":
        return "failed", evidence["verdict_reason"] or "failed"
    return "not-run", evidence["verdict_reason"] or ""


def _defect_charge(name: str) -> int | None:
    match = re.search(r"_(-?\d+)$", name)
    return int(match.group(1)) if match else None


def _chain_roots(charges: list[int]) -> set[int]:
    qs = sorted(charges)
    if not qs:
        return set()
    n = len(qs)
    return {qs[n // 2]} if n % 2 else {qs[n // 2 - 1], qs[n // 2]}


def _disposition(path: Path | None, kind: str, status: str, detail: str) -> str:
    """Generic (non-CPD/non-defect) disposition for the remaining node kinds.

    CPD and defect retry decisions come from the unified retry-policy module
    (:func:`vasp_sop.core.retry_policy.evaluate_cpd` / :func:`evaluate_defect`)
    applied to raw evidence in :func:`build_graph`; this function only covers
    unitcell tasks, gate artifacts, and never-run defect first submissions.
    """
    if status in ("running", "submitted", "waiting", "waiting-seed"):
        return "wait"
    if status in _READY:
        return "none"
    if kind == "gate-artifact":
        return "automatic" if status == "not-run" else "manual"
    if status == "no-input":
        return "manual"
    if status == "not-run":
        return "automatic" if path is not None and input_ready(path) else "manual"
    return "manual"


def _add_edge(
    edges: list[dict],
    source: str,
    target: str,
    relation: str,
    *,
    label: str = "",
    hard: bool = False,
) -> None:
    if relation not in _RELATION_TYPES:
        raise ValueError(f"unknown relation type: {relation}")
    edge_id = f"{relation}:{source}->{target}"
    if any(e["id"] == edge_id for e in edges):
        return
    edges.append(
        {
            "id": edge_id,
            "source": source,
            "target": target,
            "type": relation,
            "label": label,
            "hard": hard,
        }
    )


def _add_gate_artifact(
    nodes: dict[str, Node],
    edges: list[dict],
    sid: str,
    path: Path,
    label: str,
) -> str:
    aid = f"{sid}:artifact:{label}"
    exists = path.is_file() and path.stat().st_size > 0
    nodes[aid] = Node(
        id=aid,
        kind="gate-artifact",
        label=label,
        path=str(path),
        status="complete" if exists else "not-run",
        detail="present" if exists else "missing gate artifact",
        disposition=_disposition(
            path,
            "gate-artifact",
            "complete" if exists else "not-run",
            "present" if exists else "missing gate artifact",
        ),
    )
    return aid


def _group(
    nodes: dict[str, Node], sid: str, gid: str, label: str, child_ids: list[str]
) -> str:
    node = Node(id=gid, kind="task-group", label=label, children=child_ids)
    node.n_total = len(child_ids)
    node.n_ok = sum(nodes[c].status in _READY for c in child_ids if c in nodes)
    node.status = (
        "converged"
        if node.n_total and node.n_ok == node.n_total
        else (
            "running"
            if any(
                nodes.get(c, Node("", "", "")).status == "running" for c in child_ids
            )
            else (
                "waiting-seed"
                if any(
                    nodes.get(c, Node("", "", "")).status == "waiting-seed"
                    for c in child_ids
                )
                else "not-run"
            )
        )
    )
    nodes[gid] = node
    nodes[sid].children.append(gid)
    return gid


def _blocks_defect_analysis(path: Path) -> bool:
    """True exactly when wave3's defect-VASP truth has not settled *path*.

    ``wave3_postprocess`` accepts terminal JobStore states plus disk
    evidence; a failed defect is therefore not automatically an analysis
    blocker.  This predicate prevents the old all-defect fan-in lie.
    """
    terminal = _jobstore_latest(path) in ("converged", "failed", "unconverged")
    has_outcar = (path / "OUTCAR").is_file() or (path / "output" / "OUTCAR").is_file()
    return not (terminal and (has_outcar or _jobstore_latest(path) == "failed"))


def build_graph(root: Path, *, system_filter: str | None = None) -> dict:
    """Build a JSON-serialisable, read-only runtime relation graph."""
    from vasp_sop.core.system import System
    from vasp_sop.defect import is_valid_defect_dir

    nodes: dict[str, Node] = {}
    edges: list[dict] = []
    systems: list[dict] = []
    system_models: dict[str, tuple[System, str]] = {}

    for sys_dir in sorted(root.iterdir()):
        if not sys_dir.is_dir() or not (sys_dir / "plan.yaml").is_file():
            continue
        name = sys_dir.name
        if system_filter and name != system_filter:
            continue
        sid = f"sys:{name}"
        systems.append({"id": sid, "label": name})
        try:
            cfg = PipelineConfig.from_yaml(sys_dir / "plan.yaml", root=sys_dir)
            sys_model = System(sys_dir, cfg)
            phase = sys_model.derive_phase(_FakeStore())
        except Exception as exc:
            nodes[sid] = Node(
                id=sid,
                kind="system",
                label=name,
                status="blocked",
                detail=f"plan parse failed: {exc}",
                disposition="manual",
            )
            continue
        system_models[sid] = (sys_model, phase)
        nodes[sid] = Node(
            id=sid,
            kind="system",
            label=name,
            path=str(sys_dir),
            status="complete" if phase == "COMPLETE" else "blocked",
            detail=f"phase={phase}",
            disposition="none",
        )

        uc_ids: list[str] = []
        uc_root = sys_dir / "unitcell"
        for task in ("structure_opt", "band", "dos", "dielectric"):
            td = uc_root / task
            if not td.is_dir():
                continue
            nid = f"{sid}:uc:{task}"
            st, detail = _dir_status(td, jobstore_fallback=(task == "structure_opt"))
            nodes[nid] = Node(
                id=nid,
                kind="unitcell-task",
                label=task,
                path=str(td),
                status=st,
                detail=detail,
                disposition=_disposition(td, "unitcell-task", st, detail),
            )
            uc_ids.append(nid)
        if uc_ids:
            _group(nodes, sid, f"{sid}:group:uc", f"unitcell ({len(uc_ids)})", uc_ids)

        cpd_ids: list[str] = []
        cpd_root = sys_dir / "cpd"
        for pd in sorted(cpd_root.iterdir()) if cpd_root.is_dir() else []:
            if not pd.is_dir() or pd.name == "combos":
                continue
            nid = f"{sid}:cpd:{pd.name}"
            st, detail = _dir_status(pd)
            evidence = _dir_evidence(pd)
            # The shared CPD decision is the single authority (ADR 0017):
            # submitted → wait, converged → none, truncated → automatic,
            # budget exhaustion / NELM / missing CONTCAR → manual.  ZBRENT
            # is decision metadata only and never overrides the budget.
            decision = evaluate_cpd(
                verdict_reason=evidence["verdict_reason"],
                verdict_converged=evidence["verdict_converged"],
                latest_state=evidence["latest_state"],
                ionic_restarts=evidence["ionic_restarts"],
                has_conticar=evidence["has_conticar"],
                has_zbrent=evidence["has_zbrent"],
            )
            nodes[nid] = Node(
                id=nid,
                kind="cpd-dir",
                label=pd.name,
                path=str(pd),
                status=st,
                detail=detail,
                disposition=decision.disposition,
                explanation=decision.explanation,
            )
            cpd_ids.append(nid)
        if cpd_ids:
            _group(nodes, sid, f"{sid}:group:cpd", f"cpd ({len(cpd_ids)})", cpd_ids)

        chain_ids: list[str] = []
        df_root = sys_dir / "defect"
        if df_root.is_dir():
            chains: dict[str, list[Path]] = {}
            for dd in sorted(df_root.iterdir()):
                if (
                    not dd.is_dir()
                    or dd.name == "perfect"
                    or not is_valid_defect_dir(dd)
                ):
                    continue
                key = re.sub(r"_(-?\d+)$", "", dd.name)
                chains.setdefault(key, []).append(dd)
            for key, dirs in sorted(chains.items()):
                cid = f"{sid}:df:{key}"
                chain = Node(id=cid, kind="defect-chain", label=key)
                nodes[cid] = chain
                chain_ids.append(cid)
                roots = _chain_roots(
                    [q for q in (_defect_charge(d.name) for d in dirs) if q is not None]
                )
                for dd in sorted(dirs):
                    did = f"{cid}:{dd.name}"
                    st, detail = _dir_status(dd)
                    q = _defect_charge(dd.name)
                    evidence = _dir_evidence(dd)
                    has_history = bool(evidence["history"])
                    if (
                        q is not None
                        and q not in roots
                        and st == "not-run"
                        and not has_history
                    ):
                        st, detail = "waiting-seed", "waits for root charge (ADR 0010)"
                    disposition: str
                    explanation = ""
                    if st == "waiting-seed":
                        # The ADR 0010 seed upstream gate owns this node; it
                        # is not a retry decision at all.
                        disposition = "wait"
                    elif has_history:
                        # Has run before: the shared defect decision is the
                        # authority (state-driven, reason-blind — ADR 0010
                        # revision / ADR 0016): submitted → wait, verdict
                        # converged → none, restart-eligible (failed /
                        # unconverged / pending / stale-converged) →
                        # automatic, shaped by own CONTCAR (continuation vs
                        # fresh submission).
                        decision = evaluate_defect(
                            latest_state=evidence["latest_state"],
                            verdict_converged=evidence["verdict_converged"],
                            verdict_reason=evidence["verdict_reason"],
                            has_conticar=evidence["has_conticar"],
                            has_zbrent=evidence["has_zbrent"],
                        )
                        disposition = decision.disposition
                        explanation = decision.explanation
                    else:
                        # Never-run dir: first submission (including ADR 0010
                        # seeding) is the executor's call — a pending fresh
                        # submit, not a retry.
                        disposition = _disposition(dd, "defect-dir", st, detail)
                    nodes[did] = Node(
                        id=did,
                        kind="defect-dir",
                        label=dd.name,
                        path=str(dd),
                        status=st,
                        detail=detail,
                        disposition=disposition,
                        explanation=explanation,
                    )
                    chain.children.append(did)
                    if q is not None and q not in roots:
                        for root_charge in sorted(roots):
                            root_dirs = [
                                d for d in dirs if _defect_charge(d.name) == root_charge
                            ]
                            for rd in root_dirs:
                                rid = f"{cid}:{rd.name}"
                                _add_edge(
                                    edges,
                                    rid,
                                    did,
                                    RUNTIME_GATE,
                                    label="seed root",
                                    hard=True,
                                )
                                _add_edge(
                                    edges,
                                    rid,
                                    did,
                                    LINEAGE,
                                    label="CONTCAR seed source",
                                    hard=False,
                                )
                chain.n_total = len(chain.children)
                chain.n_ok = sum(nodes[c].status in _READY for c in chain.children)
                chain.status = (
                    "converged"
                    if chain.n_total and chain.n_ok == chain.n_total
                    else (
                        "running"
                        if any(nodes[c].status == "running" for c in chain.children)
                        else (
                            "waiting-seed"
                            if any(
                                nodes[c].status == "waiting-seed"
                                for c in chain.children
                            )
                            else "not-run"
                        )
                    )
                )
            if chain_ids:
                gid = _group(
                    nodes,
                    sid,
                    f"{sid}:group:df",
                    f"defects ({len(chain_ids)} chains)",
                    chain_ids,
                )
                nodes[gid].detail = (
                    f"{nodes[gid].n_ok}/{nodes[gid].n_total} chains converged"
                )

        # Provenance: defects are built from the target POSCAR, not from
        # structure_opt convergence. This is deliberately not a hard gate.
        target = sys_model.target_dir
        if target is not None:
            target_poscar = target / "POSCAR"
            aid = f"{sid}:artifact:target-poscar"
            nodes[aid] = Node(
                id=aid,
                kind="data-artifact",
                label="target POSCAR",
                path=str(target_poscar),
                status="complete" if target_poscar.is_file() else "not-run",
                detail="defect builder source",
            )
            nodes[sid].children.append(aid)
            for cid in chain_ids:
                for did in nodes[cid].children:
                    _add_edge(edges, aid, did, LINEAGE, label="defect input source")

        # The actual wave2 dispatch fans out CPD and defects/perfect in
        # COMPETING; this is a relation layer, not a blocking gate.
        if phase in ("COMPETING", "UNITCELL_DEFECT"):
            did = f"{sid}:dispatch:wave2"
            nodes[did] = Node(
                id=did, kind="dispatch", label="wave2 dispatch", status="running"
            )
            nodes[sid].children.append(did)
            for task_id in cpd_ids + chain_ids + uc_ids:
                _add_edge(edges, did, task_id, DISPATCH, label="same-cycle dispatch")

        # Analysis gate: actual wave3 waits on UC disk completion and
        # defect VASP truth; it does not wait on every CPD directory.
        wave3_id = f"{sid}:wave3"
        nodes[wave3_id] = Node(
            id=wave3_id,
            kind="analysis-gate",
            label="analysis / formation energies",
            status=(
                "complete"
                if (sys_dir / "defect" / "defect_energy_summary.json").is_file()
                else "not-run"
            ),
            disposition="none",
        )
        nodes[sid].children.append(wave3_id)
        for task_id in uc_ids:
            if nodes[task_id].label in ("band", "dos", "dielectric"):
                nodes[wave3_id].deps.append(task_id)
                _add_edge(
                    edges,
                    task_id,
                    wave3_id,
                    RUNTIME_GATE,
                    label="UC analysis gate",
                    hard=True,
                )
        for cid in chain_ids:
            for did in nodes[cid].children:
                defect_path = Path(nodes[did].path or "")
                if not _blocks_defect_analysis(defect_path):
                    continue
                nodes[wave3_id].deps.append(did)
                _add_edge(
                    edges,
                    did,
                    wave3_id,
                    RUNTIME_GATE,
                    label="defect VASP gate",
                    hard=True,
                )
        for label in ("target_vertices", "standard_energies"):
            path = cpd_root / f"{label}.yaml"
            aid = _add_gate_artifact(nodes, edges, sid, path, label)
            nodes[sid].children.append(aid)
            nodes[wave3_id].deps.append(aid)
            _add_edge(edges, aid, wave3_id, RUNTIME_GATE, label="phase gate", hard=True)

    # Materialise containment relations separately; containment never scores.
    for parent in nodes.values():
        for child in parent.children:
            _add_edge(edges, parent.id, child, CONTAINMENT, label="contains")

    node_map = nodes
    for edge in edges:
        if edge["type"] == RUNTIME_GATE:
            target = node_map.get(edge["target"])
            source = node_map.get(edge["source"])
            if (
                target is not None
                and source is not None
                and source.id not in target.deps
            ):
                target.deps.append(source.id)

    roots = _blocking_roots(nodes, edges)
    for item in roots:
        nodes[item["id"]].bottleneck = item["affected"]

    return {
        "generated_at": _now_iso(),
        "root": str(root),
        "nodes": [n.to_dict() for n in nodes.values()],
        "systems": systems,
        "edges": edges,
        "blocking_roots": roots,
        # Compatibility alias for existing consumers; it is now the
        # disposition-partitioned blocking-root ranking, not raw deps.
        "bottlenecks": [
            {
                "id": r["id"],
                "label": r["label"],
                "score": r["affected"],
                "status": r["status"],
                "disposition": r["disposition"],
            }
            for r in roots
        ],
    }


def _blocking_roots(nodes: dict[str, Node], edges: list[dict]) -> list[dict]:
    outgoing: dict[str, list[str]] = {}
    incoming_unmet: dict[str, set[str]] = {}
    for e in edges:
        if e["type"] != RUNTIME_GATE or not e["hard"]:
            continue
        outgoing.setdefault(e["source"], []).append(e["target"])
        source = nodes.get(e["source"])
        target = nodes.get(e["target"])
        if (
            source
            and target
            and source.status not in _READY
            and target.status not in _READY
        ):
            incoming_unmet.setdefault(e["source"], set()).add(e["target"])
    candidates = set(incoming_unmet)
    ancestors: dict[str, set[str]] = {}
    for e in edges:
        if e["type"] == RUNTIME_GATE and e["hard"]:
            ancestors.setdefault(e["target"], set()).add(e["source"])
    roots = [
        nid
        for nid in candidates
        if not any(parent in candidates for parent in ancestors.get(nid, set()))
    ]
    result: list[dict] = []
    for nid in roots:
        affected: set[str] = set()
        queue = deque(outgoing.get(nid, []))
        while queue:
            child = queue.popleft()
            if child in affected:
                continue
            child_node = nodes.get(child)
            if child_node is None or child_node.status in _READY:
                continue
            affected.add(child)
            queue.extend(outgoing.get(child, []))
        node = nodes[nid]
        result.append(
            {
                "id": nid,
                "label": node.label,
                "status": node.status,
                "disposition": node.disposition,
                "detail": node.detail,
                "affected": len(affected),
                "downstream": sorted(affected),
            }
        )
    disposition_order = {"manual": 0, "automatic": 1, "wait": 2, "none": 3}
    result.sort(
        key=lambda r: (
            disposition_order.get(r["disposition"], 9),
            -r["affected"],
            r["label"],
        )
    )
    return result


class _FakeStore:
    """Minimal JobStore stand-in for System.derive_phase (read-only)."""

    def latest(self, path: str) -> str | None:
        js = _jobstore_latest(Path(path))
        return js if js in ("submitted", "converged", "failed") else None

    def history(self, path: str) -> list[dict]:
        return _jobstore_history(Path(path))


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


# ── rendering ─────────────────────────────────────────────────────────
_STATUS_CHAR = {
    "converged": "✓",
    "complete": "✓",
    "running": "▶",
    "waiting": "⏳",
    "waiting-seed": "⏳",
    "failed": "✗",
    "no-input": "!",
    "not-run": "·",
    "blocked": "⛔",
}


def render_tree(graph: dict) -> str:
    nodes = {n["id"]: n for n in graph["nodes"]}
    lines: list[str] = []
    for n in graph["nodes"]:
        if n["kind"] != "system":
            continue
        lines.append(f"{n['label']}  [{n['detail']}]")
        for cid in n["children"]:
            if cid in nodes:
                _render_node(lines, nodes, nodes[cid], "  ")
    if graph.get("blocking_roots"):
        lines.append("")
        lines.append("blocking roots (disposition / affected downstream):")
        for b in graph["blocking_roots"][:20]:
            lines.append(
                f"  {b['affected']:>3}  {b['label']:<40} "
                f"[{b['disposition']}/{b['status']}]"
            )
    return "\n".join(lines)


def _render_node(lines: list[str], nodes: dict, n: dict, indent: str) -> None:
    ch = _STATUS_CHAR.get(n["status"], "?")
    deps = ""
    if n["deps"]:
        labels = [nodes[d]["label"] for d in n["deps"] if d in nodes]
        shown = labels[:5]
        more = f" …+{len(labels) - 5}" if len(labels) > 5 else ""
        deps = f"  ← needs: {', '.join(shown)}{more}"
    disp = (
        f" ({n['disposition']})" if n.get("disposition") not in (None, "none") else ""
    )
    frac = f" {n['n_ok']}/{n['n_total']}" if n["n_total"] else ""
    lines.append(f"{indent}{ch} {n['label']}{frac}{disp}{deps}")
    for cid in n.get("children", []):
        if cid in nodes:
            _render_node(lines, nodes, nodes[cid], indent + "  ")


def render_mermaid(graph: dict) -> str:
    """CLI-only relation export; the WebUI uses cytoscape."""
    nodes = {n["id"]: n for n in graph["nodes"]}
    ids = {nid: f"n{i}" for i, nid in enumerate(nodes)}
    out = ["graph TD"]
    for nid, n in nodes.items():
        out.append(f'    {ids[nid]}["{n["label"].replace(chr(34), chr(39))}"]')
    for e in graph.get("edges", []):
        if e["source"] in ids and e["target"] in ids and e["type"] != CONTAINMENT:
            out.append(f"    {ids[e['source']]} -->|{e['type']}| {ids[e['target']]}")
    return "\n".join(out)


def to_json(graph: dict) -> str:
    return json.dumps(graph, ensure_ascii=False, indent=2)
