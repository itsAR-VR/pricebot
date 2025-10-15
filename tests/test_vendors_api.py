from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_db
from app.db import models
from app.main import app


def _override_get_db(session: Session):
    def _get_db():
        yield session

    return _get_db


def test_list_vendors_filters_by_query_and_limit(session: Session):
    vendor_alpha = models.Vendor(name="Alpha Supplies")
    vendor_beta = models.Vendor(name="Beta Tech")
    vendor_gamma = models.Vendor(name="Gamma Corp")
    session.add_all([vendor_alpha, vendor_beta, vendor_gamma])
    session.commit()

    app.dependency_overrides[get_db] = _override_get_db(session)
    client = TestClient(app)
    response = client.get("/vendors", params={"q": "beta", "limit": 1})
    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.json()
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Beta Tech"
    assert set(data[0].keys()) == {"id", "name"}


def test_list_vendors_returns_sorted_results(session: Session):
    vendor_a = models.Vendor(name="zulu phones")
    vendor_b = models.Vendor(name="Acme Wireless")
    vendor_c = models.Vendor(name="beta gadgets")
    session.add_all([vendor_a, vendor_b, vendor_c])
    session.commit()

    app.dependency_overrides[get_db] = _override_get_db(session)
    client = TestClient(app)
    response = client.get("/vendors", params={"limit": 10})
    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.json()
    data = response.json()
    assert [item["name"] for item in data] == ["Acme Wireless", "beta gadgets", "zulu phones"]
