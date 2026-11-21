#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "evaluation-data" / "manifests" / "final60_artifact_access_manifest.csv"


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "ArtifactCopilot-SubmissionDownloader/1.0"})
    with urlopen(req, timeout=120) as response, dest.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def clone_repo(url: str, dest: Path) -> None:
    subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        check=True,
        cwd=ROOT,
    )


def prepare_case(row: dict[str, str], out_root: Path) -> dict[str, str]:
    suite_id = row["suite_id"]
    host_type = str(row.get("host_type", "github" if "github.com" in str(row.get("artifact_access_link", "")) else "unknown"))
    url = row["artifact_access_link"]
    case_root = out_root / suite_id / "input_artifact"
    artifact_root = case_root / "artifact"
    artifact_root.mkdir(parents=True, exist_ok=True)

    result = {
        "suite_id": suite_id,
        "conference": row.get("conference", ""),
        "year": row.get("year", ""),
        "host_type": host_type,
        "artifact_access_link": url,
        "input_dir": str(case_root),
        "artifact_dir": str(artifact_root),
        "download_status": "skipped",
        "download_note": "",
    }

    try:
        if host_type == "github":
            clone_repo(url, artifact_root / "repo")
            result["download_status"] = "downloaded"
            result["download_note"] = "git clone --depth 1"
        else:
            landing = artifact_root / "artifact_link.txt"
            landing.write_text(url + "\n", encoding="utf-8")
            result["download_status"] = "metadata_only"
            result["download_note"] = f"host_type={host_type}; reviewer should download from recorded public link"
    except Exception as exc:  # noqa: BLE001
        result["download_status"] = "failed"
        result["download_note"] = f"{type(exc).__name__}: {exc}"
        return result

    paper_source_link = str(row.get("paper_source_link", "")).strip()
    paper_source_type = str(row.get("paper_source_type", "")).strip().lower()
    if paper_source_link:
        paper_dest = case_root / ("paper.pdf" if paper_source_type == "pdf" else "paper_source.txt")
        try:
            download_file(paper_source_link, paper_dest)
            result["paper_source_link"] = paper_source_link
            result["paper_source_type"] = paper_source_type or "downloaded"
            result["paper_source_path"] = str(paper_dest)
        except Exception as exc:  # noqa: BLE001
            result["paper_source_link"] = paper_source_link
            result["paper_source_type"] = paper_source_type or "downloaded"
            result["paper_source_path"] = ""
            result["paper_download_note"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--out-root", required=True)
    parser.add_argument("--suite-ids", default="", help="Comma-separated subset such as S02,S17")
    args = parser.parse_args()

    manifest = Path(args.manifest).resolve()
    out_root = Path(args.out_root).resolve()
    rows = read_rows(manifest)
    if args.suite_ids:
        wanted = {item.strip() for item in args.suite_ids.split(",") if item.strip()}
        rows = [row for row in rows if row["suite_id"] in wanted]

    out_root.mkdir(parents=True, exist_ok=True)
    prepared: list[dict[str, str]] = []
    for row in rows:
        prepared.append(prepare_case(row, out_root))

    manifest_out = out_root / "prepared_manifest.json"
    manifest_out.write_text(json.dumps(prepared, indent=2, ensure_ascii=True), encoding="utf-8")
    print(str(manifest_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
