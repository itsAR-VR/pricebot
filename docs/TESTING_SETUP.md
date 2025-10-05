# Testing Setup Guide

Use this guide to stand up a reproducible Python 3.11 environment, install dependencies, and run the chat tool test suite. The current machine has system-level SSL interception that prevents pip from downloading wheels without extra configuration—see "Troubleshooting" below.

## 1. Create a Clean Virtual Environment

```bash
cd /Users/AR180/Desktop/Codespace/pricebot
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade --trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org pip setuptools wheel
```

> If pip reports SSL certificate issues, export the CA bundle explicitly before running the upgrade:
>
> ```bash
> export SSL_CERT_FILE="$(python -c 'import certifi; print(certifi.where())')"
> export REQUESTS_CA_BUNDLE="$SSL_CERT_FILE"
> python -m pip install --upgrade pip setuptools wheel
> ```

## 2. Install Project Dependencies

Install the project in editable mode with the dev/test extras. This pulls in FastAPI, SQLModel, pytest, and optional OCR/PDF helpers used by ingestion tests.

```bash
python -m pip install --no-cache-dir --no-build-isolation \
  --trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org \
  -e .[dev,pdf,ocr]
```

If you continue to see certificate errors (OSStatus -26276), add the `--trusted-host` flags:

```bash
python -m pip install --no-cache-dir --no-build-isolation \
  --trusted-host pypi.org \
  --trusted-host files.pythonhosted.org \
  --trusted-host pypi.python.org \
  -e .[dev,pdf,ocr]
```

You may also need to import the corporate root CA into the macOS keychain and mark it as trusted for SSL. Once macOS trusts the proxy certificate, pip will reuse the system keychain and the install will succeed.

## 3. Run the Chat Tool Tests

Once dependencies are installed:

```bash
python -m pytest tests/test_chat_tools_api.py
```

Run the full suite when ready:

```bash
python -m pytest
```

A convenience script (`scripts/setup_and_test.sh`) automates the previous steps:

```bash
./scripts/setup_and_test.sh
```

## Troubleshooting SSL Errors

- **Error:** `SSLCertVerificationError('OSStatus -26276')`
  - Export the CA bundle via `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` as shown above.
  - If the proxy uses a custom root certificate, install it into the System/Log-in keychain and trust it for SSL.
  - As a last resort (not recommended for production), pass `--trusted-host` flags or set `PIP_NO_VERIFY=pypi.org,files.pythonhosted.org` for the install command.
- **Error:** `externally-managed-environment`
  - Ensure you are inside the virtual environment (`source .venv/bin/activate`). Avoid installing into the system Python managed by Homebrew.

## Verifying the Setup

1. `python -m pip list` should show `fastapi`, `sqlmodel`, and `pytest` installed inside `.venv`.
2. `python -m pytest tests/test_chat_tools_api.py` should pass without import errors.
3. Record the environment (Python version, pip version, any `SSL_CERT_FILE` overrides) in QA notes for reproducibility.

Once the virtual environment is working, add `source .venv/bin/activate` to your shell profile or run it per-session before executing tests.
