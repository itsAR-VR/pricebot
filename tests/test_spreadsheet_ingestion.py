from pathlib import Path

import pandas as pd

from app.ingestion.spreadsheet import SpreadsheetIngestionProcessor


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
