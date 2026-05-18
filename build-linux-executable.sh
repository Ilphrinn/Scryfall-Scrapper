#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

if command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
else
  echo "Python n'est pas installe. Installe Python 3.11+ puis relance ce script." >&2
  exit 1
fi

"$PYTHON" -m venv .venv-build
. .venv-build/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt
python -m PyInstaller --clean --noconfirm ScryfallArtworkDownloader.spec

echo
echo "Executable cree: $PROJECT_ROOT/dist/ScryfallArtworkDownloader"

