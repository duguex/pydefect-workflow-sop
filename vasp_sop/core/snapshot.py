"""Per-round batch state snapshots."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_SNAPSHOT = "batch_snapshot.json"
_TIMELINE = "batch_timeline.jsonl"


class SnapshotWriter:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._snapshot = root / _SNAPSHOT
        self._timeline = root / _TIMELINE

    def write(self, state: dict) -> None:
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        state["timestamp"] = ts
        payload = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
        self._snapshot.write_text(payload, encoding="utf-8")
        line = json.dumps(state, ensure_ascii=False) + "\n"
        with self._timeline.open("a", encoding="utf-8") as f:
            f.write(line)

    def last(self) -> dict | None:
        if not self._snapshot.is_file():
            return None
        try:
            return json.loads(self._snapshot.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
