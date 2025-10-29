from __future__ import annotations

import json
from pathlib import Path

import jsonschema


BASE_DIR = Path(__file__).resolve().parents[1]
SCHEMA_DIR = BASE_DIR / "docs" / "schemas"
SAMPLE_PATH = BASE_DIR / "docs" / "whatsapp_ingest_contract_sample.json"

SCHEMA_STORE: dict[str, dict] = {}
for schema_file in SCHEMA_DIR.glob("*.json"):
    schema_data = json.loads(schema_file.read_text(encoding="utf-8"))
    SCHEMA_STORE[schema_file.resolve().as_uri()] = schema_data
    schema_id = schema_data.get("$id")
    if isinstance(schema_id, str):
        SCHEMA_STORE[schema_id] = schema_data


def _load_schema_validator(schema_path: Path) -> jsonschema.Draft202012Validator:
    with schema_path.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    resolver = jsonschema.RefResolver(
        base_uri=schema_path.resolve().as_uri(),
        referrer=schema,
        store=SCHEMA_STORE,
    )
    return jsonschema.Draft202012Validator(schema, resolver=resolver)


def test_whatsapp_ingest_sample_matches_batch_schema() -> None:
    validator = _load_schema_validator(SCHEMA_DIR / "whatsapp_ingest_batch.schema.json")
    payload = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    validator.validate(payload)


def test_whatsapp_ingest_sample_messages_match_message_schema() -> None:
    validator = _load_schema_validator(SCHEMA_DIR / "whatsapp_message_in.schema.json")
    payload = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    messages = payload.get("messages", [])
    assert messages, "Sample payload should include at least one message"
    for idx, message in enumerate(messages):
        try:
            validator.validate(message)
        except jsonschema.ValidationError as exc:  # pragma: no cover - assertion detail
            raise AssertionError(f"Message #{idx} failed schema validation: {exc.message}") from exc
