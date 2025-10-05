import pytest

pytestmark = pytest.mark.skip(reason="Integration scenario requires seeded DB and ingestion pipeline; enable once fixtures are ready")


def test_upload_then_chat_flow():
    """Placeholder for end-to-end upload → ingest → chat validation."""
    # This will become a real integration test once we have deterministic fixtures.
    assert True
