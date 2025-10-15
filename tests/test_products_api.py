from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_db
from app.db import models
from app.main import app


def _override_get_db(session: Session):
    def _get_db():
        yield session

    return _get_db


def test_product_suggest_returns_matches(session: Session):
    product = models.Product(canonical_name="iPhone 17 Pro 256GB", model_number="A1234")
    session.add(product)
    session.flush()
    session.add(models.ProductAlias(product_id=product.id, alias_text="iPhone Pro 256"))
    session.commit()

    app.dependency_overrides[get_db] = _override_get_db(session)
    client = TestClient(app)
    response = client.get("/products/suggest", params={"q": "iphone pro", "limit": 5})
    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.json()
    data = response.json()
    assert len(data) == 1
    assert data[0]["canonical_name"] == "iPhone 17 Pro 256GB"
    assert data[0]["match_source"] in {"canonical_name", "alias"}
    assert set(data[0].keys()) == {"id", "canonical_name", "model_number", "match_source"}


def test_product_suggest_rejects_blank_query(session: Session):
    app.dependency_overrides[get_db] = _override_get_db(session)
    client = TestClient(app)
    response = client.get("/products/suggest", params={"q": "   "})
    app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 422
