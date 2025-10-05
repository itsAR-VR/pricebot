from app.ingestion.text_utils import parse_offer_line


def test_parse_offer_line_basic():
    offer, error = parse_offer_line(
        "WTB 100 Laptops $70",
        vendor_name="Ali",
        default_currency="USD",
    )
    assert error is None
    assert offer is not None
    assert offer.product_name == "Laptops"
    assert offer.quantity == 100
    assert offer.price == 70.0


def test_parse_offer_line_currency_suffix():
    offer, error = parse_offer_line(
        "Pixel 8 128GB 520 USD",
        vendor_name="Vendor",
        default_currency="USD",
    )
    assert error is None
    assert offer is not None
    assert offer.currency == "USD"
    assert offer.price == 520.0


def test_parse_offer_line_requires_product():
    offer, error = parse_offer_line(
        "$1200",
        vendor_name="Vendor",
        default_currency="USD",
    )
    assert offer is None
    assert error is not None


def test_parse_offer_line_ignores_leading_identifier_as_quantity():
    offer, error = parse_offer_line(
        "840023255922 Motorola G5 164 USD",
        vendor_name="Vendor",
        default_currency="USD",
    )

    assert error is None
    assert offer is not None
    assert offer.quantity is None
    assert offer.product_name == "Motorola G5"
    assert offer.raw_payload["identifiers"] == ["840023255922"]
