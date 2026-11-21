from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .specs import ToolError


def resolve_repo_path(repo_root: Path, relative_path: str) -> Path | None:
    candidate = (repo_root / relative_path).resolve()
    try:
        candidate.relative_to(repo_root.resolve())
    except Exception:
        return None
    return candidate


def resolve_run_path(run_root: Path, path_text: str) -> Path | None:
    raw = Path(path_text)
    candidate = raw.resolve() if raw.is_absolute() else (run_root / raw).resolve()
    try:
        candidate.relative_to(run_root.resolve())
    except Exception:
        return None
    return candidate


def bounded_int(value: Any, *, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except Exception as exc:
        raise ToolError("Expected integer parameter.") from exc
    return max(lower, min(upper, parsed))


def coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def dedupe_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def safe_artifact_filename(path_text: str, *, suffix: str) -> str:
    base = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in path_text)
    return f"{base[:120] or 'file'}{suffix}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

