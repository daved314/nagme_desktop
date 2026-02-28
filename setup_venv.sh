#!/usr/bin/env bash
set -euo pipefail

# Run from repository root (directory of this script).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if [[ ! -d ".venv" ]]; then
  echo "Creating virtual environment at .venv"
  python3 -m venv .venv
else
  echo "Using existing virtual environment at .venv"
fi

source ".venv/bin/activate"

# Ensure pip is available and reasonably current in the virtual environment.
python -m pip install --upgrade pip

# Idempotent: installs only missing / incompatible packages from requirements.txt.
python -m pip install -r requirements.txt

echo
echo "Environment ready."
echo "Activate with: source .venv/bin/activate"
