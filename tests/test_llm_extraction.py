import pytest

from app.ingestion.document import DocumentExtractionProcessor
from app.ingestion.types import RawOffer
from app.ingestion.whatsapp import WhatsAppTextProcessor
from app.services.llm_extraction import ExtractionContext, LLMUnavailableError, OfferLLMExtractor


class StubResponse:
    def __init__(self, content: str) -> None:
        self.choices = [StubChoice(content)]


class StubChoice:
    def __init__(self, content: str) -> None:
        self.message = StubMessage(content)


class StubMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class StubCompletions:
    def __init__(self, content: str) -> None:
        self._content = content

    def create(self, **_: dict) -> StubResponse:
        return StubResponse(self._content)


class StubChat:
    def __init__(self, content: str) -> None:
        self.completions = StubCompletions(content)


class StubClient:
    def __init__(self, content: str) -> None:
        self.chat = StubChat(content)


def test_llm_extractor_parses_valid_json():
    client = StubClient(
        """
        {"offers": [{"product_name": "AMPACE P600 Jumper Cable", "price": 179.0, "currency": "usd", "quantity": 44, "vendor_name": "cellntell", "raw_lines": [1]}], "warnings": ["normalized via llm"]}
        """.strip()
    )
    extractor = OfferLLMExtractor(client=client, model="test-model")

    offers, warnings = extractor.extract_offers_from_lines(
        ["AMPACE P600 JUMPER CABLE 179 USD 44 qty"],
        context=ExtractionContext(vendor_hint="cellntell", currency_hint="USD"),
    )

    assert len(offers) == 1
    offer = offers[0]
    assert offer.product_name == "AMPACE P600 Jumper Cable"
    assert offer.price == 179.0
    assert offer.quantity == 44
    assert offer.currency == "USD"
    assert offer.raw_payload["raw_lines"] == [1]
    assert warnings == ["normalized via llm"]


def test_llm_extractor_invalid_json_raises():
    client = StubClient("not-json")
    extractor = OfferLLMExtractor(client=client)

    with pytest.raises(LLMUnavailableError):
        extractor.extract_offers_from_lines(
            ["Pixel 8 520"],
            context=ExtractionContext(vendor_hint="Vendor", currency_hint="USD"),
        )


def test_document_processor_prefers_llm(monkeypatch, tmp_path):
    file_path = tmp_path / "document.pdf"
    file_path.write_bytes(b"dummy")

    stub_offer = RawOffer(vendor_name="Cellntell", product_name="Motorola G54", price=164.0, currency="USD")

    class StubExtractor:
        def extract_offers_from_lines(self, lines, *, context):
            assert "Motorola" in " ".join(lines)
            assert context.vendor_hint == "Cellntell"
            return [stub_offer], ["llm used"]

    processor = DocumentExtractionProcessor(llm_extractor=StubExtractor())
    monkeypatch.setattr(processor, "_extract_lines", lambda _: ["Motorola G54 164 USD 500pcs"])

    result = processor.process(file_path, context={"vendor_name": "Cellntell"})

    assert result.offers == [stub_offer]
    assert result.errors == ["llm used"]


def test_whatsapp_processor_short_circuits_to_llm(tmp_path):
    file_path = tmp_path / "chat.txt"
    file_path.write_text("Selling Pixel 8 for 520 USD\n")

    stub_offer = RawOffer(vendor_name="Cellntell", product_name="Pixel 8", price=520.0, currency="USD")

    class StubExtractor:
        def extract_offers_from_lines(self, lines, *, context):
            assert context.document_kind == "whatsapp_transcript"
            return [stub_offer], []

    processor = WhatsAppTextProcessor(llm_extractor=StubExtractor())
    result = processor.process(file_path, context={"vendor_name": "Cellntell", "prefer_llm": True})

    assert result.offers == [stub_offer]
    assert result.errors == []
