from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..shared.fs import ensure_dir, now_iso


class TrajectoryLogger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(
        self,
        *,
        phase: str,
        event_type: str,
        step_id: str,
        summary: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        ensure_dir(self.path.parent)
        event = {
            "timestamp": now_iso(),
            "phase": phase,
            "event_type": event_type,
            "step_id": step_id,
            "summary": summary,
            "payload": payload or {},
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
