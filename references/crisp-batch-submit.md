# Crisp Batch Submit (Python Script)

How to batch-submit many VASP directories to crisp without going through the HTTP MCP layer.

## Correct Approach: Python Script via `get_job_db()`

```python
# Run with: ~/.conda/envs/paramiko/bin/python batch_submit.py
import os, sys, uuid
from pathlib import Path

CRISP_ROOT = os.path.expanduser("~/crisp")
sys.path.insert(0, CRISP_ROOT)
sys.path.insert(0, os.path.join(CRISP_ROOT, "scripts"))

from utils.db import get_job_db

manager = get_job_db()
BASE = "/path/to/defect_dir"

dirs = [d for d in Path(BASE).iterdir() if d.is_dir() and d.name.endswith("_0")]

for d in sorted(dirs):
    local_dir = str(d.resolve())
    task_name = uuid.uuid4().hex[:8]
    ok, _ = manager.register_job(task_name=task_name, local_dir=local_dir, status="submit")
    print(f"[{i+1}/{len(dirs)}] {d.name} → {task_name}")
```

## Why Not curl/HTTP MCP?

The crisp MCP server at `localhost:23129` does not accept JSON-RPC over HTTP POST. It uses stdio transport. Use the Python API directly instead.

## Duplicate Prevention

- Use `uuid.uuid4().hex[:8]` for unique task names.
- Never run the same batch script twice against the same directory set.
- If duplicates occur (e.g., from a failed curl attempt that partially wrote DB entries before failing), clean up with:

```python
from collections import Counter, defaultdict
from utils.db import get_job_db

mgr = get_job_db()
jobs = mgr.list_jobs(show_all=True)

# Group by local_dir, keep highest-priority status
by_dir = defaultdict(list)
for j in jobs:
    if 'defect_new' in j.get('local_dir', ''):
        by_dir[j['local_dir']].append(j)

priority = {'completed': 4, 'running': 3, 'ready_fetch': 3, 'submitted': 2, 'submit': 1}
for ld, entries in by_dir.items():
    if len(entries) <= 1:
        continue
    entries.sort(key=lambda j: priority.get(j['status'], 0), reverse=True)
    for j in entries[1:]:
        mgr.delete_job(j['task_name'])
```

`delete_job()` returns `{"success": True}` or `{"success": False, "error": "..."}` — handle as dict, not tuple.

## Two Databases

| Location | Used by |
|----------|---------|
| `~/.crisp/data/agent.db` | CLI / Python API (`get_job_db()`) |
| `/tmp/crisp_agent.db` | MCP server (agent daemon) |

MCP stats aggregated across both. For cleanup, target `~/.crisp/data/agent.db` (the CLI DB).

## Common Pitfalls

- **`local_dir` must be a specific subdirectory**, not the parent. Old failures: `.../diamond/defect` (root). Correct: `.../diamond/defect_new/2Va_C1.001_0`.
- **Don't use `os.chdir()`** — the cmd_submit() CLI code reads `Path.cwd()` for local_dir, but the Python API accepts it as a parameter.
- **PYTHONPATH** must include both `~/crisp` and `~/crisp/scripts`.
- **Python env**: `~/.conda/envs/paramiko/bin/python` (has paramiko + crisp deps). Other envs won't find the `crisp` or `utils` modules.