#!/usr/bin/env bash
set -euo pipefail
python3.11 -m venv .venv
source .venv/bin/activate

# Trusted hosts bypass the corporate TLS proxy that injects custom certificates.
PYPI_HOSTS=(pypi.org files.pythonhosted.org pypi.python.org)
TRUST_ARGS=()
for host in "${PYPI_HOSTS[@]}"; do
  TRUST_ARGS+=("--trusted-host" "$host")
done

python -m pip install --upgrade "${TRUST_ARGS[@]}" pip setuptools wheel
python -m pip install --no-cache-dir "${TRUST_ARGS[@]}" --no-build-isolation -e .[dev,pdf,ocr]
python -m pytest tests/test_chat_tools_api.py
