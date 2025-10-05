from pathlib import Path

from app.ingestion.whatsapp import WhatsAppTextProcessor
from app.ingestion.types import RawOffer


def test_whatsapp_processor_extracts_price(tmp_path: Path) -> None:
    transcript = """
    10:30 a.m.
    Ali:
    WTB 100 Laptops $70 each
    10:45 a.m.
    Sara:
    Selling Pixel 8 128GB - $520 net
    """
    file_path = tmp_path / "chat.txt"
    file_path.write_text(transcript, encoding="utf-8")

    processor = WhatsAppTextProcessor()
    result = processor.process(file_path, context={})

    assert len(result.offers) == 2
    first, second = result.offers
    assert first.vendor_name == "Ali"
    assert first.product_name == "Laptops"
    assert first.quantity == 100
    assert first.price == 70.0

    assert second.vendor_name == "Sara"
    assert second.product_name == "Pixel 8 128GB"
    assert second.price == 520.0


def test_whatsapp_processor_falls_back_to_llm(tmp_path: Path) -> None:
    transcript = """
    Hello team,
    Inventory update coming later.
    """
    file_path = tmp_path / "chat.txt"
    file_path.write_text(transcript, encoding="utf-8")

    stub_offer = RawOffer(vendor_name="Cellntell", product_name="Redmi 12", price=189.0)

    class StubExtractor:
        def extract_offers_from_lines(self, lines, *, context):
            assert context.document_kind == "whatsapp_transcript"
            assert any("Hello" in line for line in lines)
            return [stub_offer], ["llm normalized"]

    processor = WhatsAppTextProcessor(llm_extractor=StubExtractor())
    result = processor.process(file_path, context={})

    assert result.offers == [stub_offer]
    assert result.errors == ["llm normalized"]
