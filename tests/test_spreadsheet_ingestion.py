from pathlib import Path

import pandas as pd

from app.ingestion.spreadsheet import SpreadsheetIngestionProcessor
from app.ingestion.types import RawOffer


def _write_excel(path: Path, data: dict[str, list]) -> None:
    df = pd.DataFrame(data)
    df.to_excel(path, index=False)


def test_spreadsheet_processor_extracts_basic_columns(tmp_path: Path) -> None:
    file_path = tmp_path / "basic.xlsx"
    _write_excel(
        file_path,
        {
            "Item": ["iPhone 13", "Galaxy S22"],
            "Price": ["799", "$699.00"],
            "Qty": ["10", "5"],
        },
    )

    processor = SpreadsheetIngestionProcessor()
    result = processor.process(file_path, context={"vendor_name": "SampleCo"})

    assert not result.errors
    assert len(result.offers) == 2

    first_offer = result.offers[0]
    assert first_offer.product_name == "iPhone 13"
    assert first_offer.price == 799.0
    assert first_offer.quantity == 10
    assert first_offer.vendor_name == "SampleCo"
    assert first_offer.raw_payload["row_index"] == 1
    assert first_offer.raw_payload["raw_lines"] == [1]
    assert first_offer.raw_payload["source"] == "spreadsheet_heuristic"

    second_offer = result.offers[1]
    assert second_offer.price == 699.0
    assert second_offer.quantity == 5


def test_spreadsheet_processor_flags_missing_price(tmp_path: Path) -> None:
    file_path = tmp_path / "missing_price.xlsx"
    _write_excel(
        file_path,
        {
            "Item": ["Widget"],
            "Price": [None],
        },
    )

    processor = SpreadsheetIngestionProcessor()
    result = processor.process(file_path, context={"vendor_name": "Acme"})

    assert len(result.offers) == 0
    assert result.errors
    assert "missing critical fields" in result.errors[0]


def test_spreadsheet_processor_handles_headerless_files(tmp_path: Path) -> None:
    file_path = tmp_path / "headerless.xlsx"
    df = pd.DataFrame([
        ["PlayStation 5", "499", "15"],
        ["Xbox Series X", "$549", "8"],
    ])
    df.to_excel(file_path, index=False, header=False)

    processor = SpreadsheetIngestionProcessor()
    result = processor.process(file_path, context={"vendor_name": "GameHub"})

    assert not result.errors
    assert len(result.offers) == 2
    assert result.offers[0].price == 499.0
    assert result.offers[1].price == 549.0


def test_spreadsheet_processor_prefers_llm_when_requested(tmp_path: Path) -> None:
    file_path = tmp_path / "llm.xlsx"
    _write_excel(
        file_path,
        {
            "Item": ["AMPACE P600"],
            "Price": ["179"],
            "Qty": ["44"],
        },
    )

    stub_offer = RawOffer(
        vendor_name="Cellntell",
        product_name="AMPACE P600 Jumper Cable",
        price=179.0,
        currency="USD",
        quantity=44,
        raw_payload={"raw_lines": [1]},
    )

    class StubExtractor:
        def __init__(self) -> None:
            self.calls = 0

        def extract_offers_from_lines(self, lines, *, context):
            self.calls += 1
            assert lines and lines[0].startswith("Row 1:")
            assert context.document_kind == "spreadsheet"
            return [stub_offer], ["normalized via llm"]

        @property
        def model(self) -> str:
            return "stub-model"

    extractor = StubExtractor()
    processor = SpreadsheetIngestionProcessor(llm_extractor=extractor)
    result = processor.process(
        file_path,
        context={"vendor_name": "Cellntell", "prefer_llm": True},
    )

    assert extractor.calls == 1
    assert result.offers == [stub_offer]
    assert result.errors == ["normalized via llm"]


def test_spreadsheet_processor_falls_back_when_llm_returns_nothing(tmp_path: Path) -> None:
    file_path = tmp_path / "fallback.xlsx"
    _write_excel(
        file_path,
        {
            "Item": ["Pixel 8"],
            "Price": ["520"],
        },
    )

    class EmptyExtractor:
        def extract_offers_from_lines(self, lines, *, context):
            return [], ["no llm offers"]

        @property
        def model(self) -> str:
            return "empty-model"

    processor = SpreadsheetIngestionProcessor(llm_extractor=EmptyExtractor())
    result = processor.process(
        file_path,
        context={"vendor_name": "Vendor", "prefer_llm": True},
    )

    # Falls back to heuristics result
    assert len(result.offers) == 1
    assert result.offers[0].product_name == "Pixel 8"
    assert "no llm offers" in result.errors
