#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${1:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
ROOT_ABS="$(cd "$ROOT" && pwd)"

echo "== cache artifacts =="
find "$ROOT" \( -type d -name '__pycache__' -o -type f -name '*.pyc' \) -print

echo
echo "== sensitive path / historical name scan =="
python - "$ROOT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
needles = [
    "/" + "home" + "/",
    "/" + "Users" + "/",
    "C:" + "\\",
    "file" + "://",
    "vscode" + "://",
]

for path in sorted(root.rglob("*")):
    if not path.is_file():
        continue
    text = None
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        continue
    for lineno, line in enumerate(text.splitlines(), start=1):
        if any(needle in line for needle in needles):
            print(f"{path}:{lineno}:{line}")
PY

echo
echo "== placeholder / unfinished wording scan =="
rg -n \
  "under construction|Planned contents|Do not treat this directory as finalized|TODO|TBD|FIXME" \
  "$ROOT" \
  -g '!scripts/*' \
  -g '!*/scripts/*' || true

echo
echo "== non-ascii scan =="
python - "$ROOT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
for path in sorted(root.rglob('*')):
    if not path.is_file():
        continue
    if path.name in ("README_zh.md", "README.md"):
        continue
    try:
        text = path.read_text(encoding='utf-8')
    except Exception:
        continue
    for i, ch in enumerate(text):
        if ord(ch) > 127:
            line = text.count('\n', 0, i) + 1
            print(f"{path}:{line}:{ord(ch)}:{ch!r}")
            break
PY
