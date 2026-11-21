from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..shared.fs import ensure_dir, write_json, write_text
from .common import bounded_int, resolve_repo_path, resolve_run_path
from .specs import ToolContext, ToolError, ToolResult


def extract_paper_source(context: ToolContext, args: dict[str, Any]) -> ToolResult:
    source_text = str(args.get("paper_source", "")).strip()
    if not source_text:
        raise ToolError("`paper_source` must be a non-empty path.")
    source_path = _resolve_paper_source_path(context, source_text)
    if not source_path.exists():
        profile = _paper_source_profile(source_path=source_path, status="missing", extractor="none", text="", error="Paper source path does not exist.", max_chars=0)
        return _paper_source_result(context, profile, "", ok=False, summary="Paper source path does not exist.")
    if not source_path.is_file():
        profile = _paper_source_profile(source_path=source_path, status="failed", extractor="none", text="", error="Paper source path is not a file.", max_chars=0)
        return _paper_source_result(context, profile, "", ok=False, summary="Paper source path is not a file.")

    max_chars = bounded_int(args.get("max_chars", 12000), lower=1000, upper=250000)
    raw_max_pages = args.get("max_pages")
    max_pages = None if raw_max_pages in (None, "", "all", "full") else bounded_int(raw_max_pages, lower=1, upper=500)
    suffix = source_path.suffix.lower()
    extracted_text = ""
    extractor = "none"
    error = ""
    status = "failed"
    if suffix in {".txt", ".md", ".rst", ".tex"}:
        extracted_text = source_path.read_text(encoding="utf-8", errors="replace")
        extractor = "plain_text"
        status = "loaded" if extracted_text.strip() else "empty"
    elif suffix == ".pdf":
        extracted_text, extractor, error = _extract_pdf_text(source_path, max_pages=max_pages)
        status = "loaded" if extracted_text.strip() else "failed"
    else:
        try:
            extracted_text = source_path.read_text(encoding="utf-8", errors="replace")
            extractor = "plain_text_fallback"
            status = "loaded" if extracted_text.strip() else "empty"
        except Exception as exc:
            error = f"Unsupported or unreadable paper source type: {source_path.suffix or '(no suffix)'}; {exc}"
    if status == "failed" and not error:
        error = "No paper text could be extracted."
    profile = _paper_source_profile(source_path=source_path, status=status, extractor=extractor, text=extracted_text, error=error, max_chars=max_chars)
    ok = status in {"loaded", "empty"}
    summary = f"Extracted paper source with {extractor}; chars={len(extracted_text)}." if status == "loaded" else f"Paper source extraction {status}: {error or 'no text extracted'}"
    return _paper_source_result(context, profile, extracted_text, ok=ok, summary=summary)


def _resolve_paper_source_path(context: ToolContext, source_text: str) -> Path:
    source_path = Path(source_text)
    if source_path.is_absolute():
        return source_path.resolve()

    repo_resolved = resolve_repo_path(context.repo_root, source_text)
    if repo_resolved is not None and repo_resolved.exists():
        return repo_resolved

    run_resolved = resolve_run_path(context.run_root, source_text)
    if run_resolved is not None and run_resolved.exists():
        return run_resolved

    config_input = context.run_root / "artifacts" / "config_inputs" / source_text
    try:
        config_input_resolved = config_input.resolve()
        config_input_resolved.relative_to((context.run_root / "artifacts" / "config_inputs").resolve())
        if config_input_resolved.exists():
            return config_input_resolved
    except Exception:
        pass

    return repo_resolved or (context.repo_root / source_text).resolve()


def _extract_pdf_text(source_path: Path, *, max_pages: int | None) -> tuple[str, str, str]:
    errors: list[str] = []
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(str(source_path))
        pages = reader.pages if max_pages is None else reader.pages[:max_pages]
        text = "\n\n".join((page.extract_text() or "") for page in pages).strip()
        if text:
            return text, "pypdf", ""
    except Exception as exc:
        errors.append(f"pypdf unavailable/failed: {exc}")
    try:
        import PyPDF2  # type: ignore

        reader = PyPDF2.PdfReader(str(source_path))
        pages = reader.pages if max_pages is None else reader.pages[:max_pages]
        text = "\n\n".join((page.extract_text() or "") for page in pages).strip()
        if text:
            return text, "PyPDF2", ""
    except Exception as exc:
        errors.append(f"PyPDF2 unavailable/failed: {exc}")
    try:
        from pdfminer.high_level import extract_text  # type: ignore

        kwargs = {} if max_pages is None else {"maxpages": max_pages}
        text = (extract_text(str(source_path), **kwargs) or "").strip()
        if text:
            return text, "pdfminer.six", ""
    except Exception as exc:
        errors.append(f"pdfminer.six unavailable/failed: {exc}")
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        try:
            cmd = [pdftotext]
            if max_pages is not None:
                cmd.extend(["-f", "1", "-l", str(max_pages)])
            cmd.extend([str(source_path), "-"])
            completed = subprocess.run(cmd, text=True, capture_output=True, timeout=60, check=False)
            text = (completed.stdout or "").strip()
            if completed.returncode == 0 and text:
                return text, "pdftotext", ""
            errors.append(f"pdftotext failed rc={completed.returncode}: {(completed.stderr or '').strip()[:500]}")
        except Exception as exc:
            errors.append(f"pdftotext failed: {exc}")
    else:
        errors.append("pdftotext executable not found")
    return "", "none", "; ".join(errors)


def _paper_source_profile(*, source_path: Path, status: str, extractor: str, text: str, error: str, max_chars: int) -> dict[str, Any]:
    preview = _inline_paper_preview(text, max_chars=max_chars) if text else {"text": "", "truncated": False, "original_chars": 0}
    return {
        "schema": "paper_source_profile_v1",
        "paper_path": str(source_path),
        "paper_loaded": status == "loaded",
        "status": status,
        "extractor": extractor,
        "source_suffix": source_path.suffix.lower(),
        "size_bytes": source_path.stat().st_size if source_path.exists() and source_path.is_file() else 0,
        "text_chars": len(text or ""),
        "preview_chars": len(preview["text"]),
        "preview_truncated": bool(preview["truncated"]),
        "error": error,
        "text_preview": preview["text"],
        "evidence_refs": [],
    }


def _paper_source_result(context: ToolContext, profile: dict[str, Any], full_text: str, *, ok: bool, summary: str) -> ToolResult:
    paper_dir = context.run_root / "artifacts" / "paper_source"
    ensure_dir(paper_dir)
    profile_path = paper_dir / "paper_source_profile.json"
    text_path = paper_dir / "paper_text.txt"
    profile = dict(profile)
    profile["profile_path"] = str(profile_path)
    if full_text:
        write_text(text_path, full_text)
        profile["text_path"] = str(text_path)
        profile["evidence_refs"] = [str(profile_path), str(text_path)]
    else:
        profile["text_path"] = ""
        profile["evidence_refs"] = [str(profile_path)]
    write_json(profile_path, profile)
    return ToolResult(
        tool_name="extract_paper_source",
        ok=ok,
        summary=summary,
        data={"paper_source_profile": profile, "text_preview": profile.get("text_preview", ""), "profile_path": str(profile_path), "text_path": str(text_path) if full_text else ""},
        error="" if ok else str(profile.get("error") or summary),
    )


def _inline_paper_preview(text: str, *, max_chars: int) -> dict[str, Any]:
    raw = str(text or "")
    original_chars = len(raw)
    if original_chars <= max_chars:
        return {"text": raw, "original_chars": original_chars, "truncated": False}
    head_chars = max(400, max_chars // 2)
    tail_chars = max(400, max_chars - head_chars)
    omitted = max(0, original_chars - head_chars - tail_chars)
    return {"text": f"{raw[:head_chars]}\n\n...[truncated {omitted} chars; full text written under run root]...\n\n{raw[-tail_chars:]}", "original_chars": original_chars, "truncated": True}
