#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "artifactcopilot" / "examples" / "ae_config.example.yaml"


def read_manifest(path: Path) -> list[dict[str, str]]:
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def discover_paths(input_dir: Path) -> tuple[Path, Path | None]:
    artifact_root = input_dir / "artifact"
    repo_root = artifact_root / "repo" if (artifact_root / "repo").exists() else artifact_root
    paper_candidates: list[Path] = []
    direct_paper = input_dir / "paper.pdf"
    if direct_paper.exists():
        paper_candidates.append(direct_paper)
    if repo_root.exists():
        paper_candidates.extend(sorted(repo_root.glob("*.pdf")))
        paper_candidates.extend(sorted(repo_root.glob("*/*.pdf")))
    seen: set[Path] = set()
    deduped: list[Path] = []
    for path in paper_candidates:
        resolved = path.resolve()
        if resolved in seen or not path.exists():
            continue
        seen.add(resolved)
        deduped.append(path)
    paper_path = deduped[0] if deduped else None
    return repo_root, paper_path


def run_case(row: dict[str, str], run_root: Path, max_steps: int, config_path: Path) -> dict[str, str | int]:
    suite_id = row["suite_id"]
    input_dir = Path(row["input_dir"]).resolve()
    case_run_root = run_root / suite_id
    repo_root, paper_path = discover_paths(input_dir)
    if paper_path is None:
        return {
            "suite_id": suite_id,
            "status": "skipped",
            "note": "paper path not found; provide --paper-map or place paper.pdf in input_artifact",
        }

    cmd = [
        sys.executable,
        "-m",
        "artifactcopilot",
        str(repo_root),
        "--paper",
        str(paper_path),
        "--config",
        str(config_path),
        "--run-root",
        str(case_run_root),
        "--max-steps",
        str(max_steps),
    ]
    proc = subprocess.run(cmd, cwd=ROOT, env=os.environ.copy(), text=True)
    return {
        "suite_id": suite_id,
        "status": "completed" if proc.returncode == 0 else "failed",
        "returncode": proc.returncode,
        "run_root": str(case_run_root),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared-manifest", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--max-steps", type=int, default=150)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--suite-ids", default="", help="Comma-separated subset such as S02,S17")
    args = parser.parse_args()

    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY must be set before running batch reviews")

    rows = read_manifest(Path(args.prepared_manifest).resolve())
    if args.suite_ids:
        wanted = {item.strip() for item in args.suite_ids.split(",") if item.strip()}
        rows = [row for row in rows if row["suite_id"] in wanted]

    run_root = Path(args.run_root).resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    config_path = Path(args.config).resolve()

    results = [run_case(row, run_root, args.max_steps, config_path) for row in rows]
    summary_path = run_root / "batch_summary.json"
    summary_path.write_text(json.dumps(results, indent=2, ensure_ascii=True), encoding="utf-8")
    print(str(summary_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
