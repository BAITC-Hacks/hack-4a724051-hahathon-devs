import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.bootstrap import build_container
from app.core.config import Settings
from app.main import create_app
from conftest import authenticate

CATALOG = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "ekt_products.json"


@pytest.fixture
def client(tmp_path):
    services = build_container(Settings(_env_file=None, app_env="test", integration_mode="synthetic",
                                        local_db_path=tmp_path / "demo.sqlite3"))
    services.store.initialize()
    with TestClient(create_app(container=services)) as c:
        yield c


def test_every_synthetic_product_has_an_existing_image():
    items = json.loads(CATALOG.read_text(encoding="utf-8"))["items"]
    root = CATALOG.parent / "images"
    for item in items:
        if item["image"].startswith("https://ekt.kz/upload/"):
            continue  # фото товара с сайта партнёра, ссылкой
        name = item["image"].rsplit("/", 1)[-1]
        assert item["image"] == f"/api/v1/product-images/SYN-{item['id']}.svg"
        svg = (root / name).read_text(encoding="utf-8")
        assert svg.startswith("<svg") and "<script" not in svg and "демо-иллюстрация" in svg


def test_image_is_served_without_session(client):
    response = client.get("/api/v1/product-images/SYN-900001.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "ВА47-29" in response.text


@pytest.mark.parametrize("name", ["SYN-1.svg", "..%2F..%2Fconfig.py", "SYN-900001.pdf", "x.svg"])
def test_bad_image_names_are_not_found(client, name):
    assert client.get(f"/api/v1/product-images/{name}").status_code == 404


def test_catalog_returns_image_url(client):
    authenticate(client)
    item = client.get("/api/v1/products", params={"query": "990100001_"}).json()["data"]["items"][0]
    assert item["image_url"] == "/api/v1/product-images/SYN-900001.svg"
