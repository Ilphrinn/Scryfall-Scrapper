#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

VENV_PATH="$PROJECT_ROOT/.venv-build"
VENV_PYTHON="$VENV_PATH/bin/python"
EXE_PATH="$PROJECT_ROOT/dist/ScryfallArtworkDownloader"

# --- Détection Python ---
if command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
else
  echo "Erreur : Python n'est pas installé. Installe Python 3.11+ puis relance ce script." >&2
  exit 1
fi

# --- Environnement virtuel (créé seulement si absent) ---
if [ ! -f "$VENV_PYTHON" ]; then
  echo "Création de l'environnement virtuel..."
  "$PYTHON" -m venv "$VENV_PATH"
fi

echo "Installation des dépendances..."
"$VENV_PYTHON" -m pip install --quiet --disable-pip-version-check -r requirements-build.txt

# --- Compilation ---
run_build() {
  "$VENV_PYTHON" -m PyInstaller --clean --noconfirm ScryfallArtworkDownloader.spec
}

echo "Compilation..."
if ! run_build; then
  echo "Compilation standard échouée. Tentative en mode compatibilité..."
  export SCRYFALL_SAFE_BUILD=1
  if ! run_build; then
    unset SCRYFALL_SAFE_BUILD
    echo "Erreur : compilation PyInstaller impossible, même en mode compatibilité." >&2
    exit 1
  fi
  unset SCRYFALL_SAFE_BUILD
fi

# --- Résultat ---
SIZE=$(du -sh "$EXE_PATH" 2>/dev/null | cut -f1 || echo "?")
if command -v sha256sum >/dev/null 2>&1; then
  HASH=$(sha256sum "$EXE_PATH" | cut -d' ' -f1)
elif command -v shasum >/dev/null 2>&1; then
  HASH=$(shasum -a 256 "$EXE_PATH" | cut -d' ' -f1)
else
  HASH="indisponible"
fi

echo ""
echo "Exécutable créé : $EXE_PATH"
echo "Taille  : $SIZE"
echo "SHA256  : $HASH"
