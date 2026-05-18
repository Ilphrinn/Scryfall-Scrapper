#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -x "./dist/ScryfallArtworkDownloader" ]]; then
  exec "./dist/ScryfallArtworkDownloader"
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 main.py
fi

if command -v python >/dev/null 2>&1; then
  exec python main.py
fi

echo "Python n'est pas installe et aucun binaire Linux n'a ete trouve dans ./dist." >&2
echo "Lance d'abord: ./build-linux-executable.sh" >&2
exit 1
