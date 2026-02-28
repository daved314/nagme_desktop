#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

needs_setup=0

if [[ ! -x ".venv/bin/python" ]]; then
  needs_setup=1
else
  if ! .venv/bin/python -c "import requests; from PIL import Image, ImageTk" >/dev/null 2>&1; then
    needs_setup=1
  fi
fi

if [[ "${needs_setup}" -eq 1 ]]; then
  echo "Preparing virtual environment and dependencies..."
  bash ./setup_venv.sh
fi

source ".venv/bin/activate"
exec python nagme_desktop.py "$@"
