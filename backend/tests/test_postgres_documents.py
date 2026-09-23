"""Файлы клиентов на настоящем PostgreSQL. Нужен TEST_DATABASE_URL."""

import asyncio
import uuid

import pytest

from app.core.errors import AppError
from test_documents_extraction import docx_bytes, xlsx
from test_postgres_catalog import URL, pool  # noqa: F401 - фикстура pool

pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL не задан")


class FakeScanner:
    def __init__(self, verdict="clean"):
        self.verdict = verdict

    def scan(self, data):
        return self.verdict


@pytest.fixture
def docs(pool, tmp_path):  # noqa: F811
    from app.adapters.postgres.documents import LocalBlobStore, PostgresDocuments
    return PostgresDocuments(pool, LocalBlobStore(tmp_path / "assets"), FakeScanner())


def resolve(docs, session_id, ids):
    return asyncio.run(docs.resolve(session_id, ids))


def test_upload_and_resolve_in_order_with_duplicates(docs):
    a = docs.upload("s1", "spec.xlsx", xlsx([["990100001_", 5]]))
    b = docs.upload("s1", "letter.docx", docx_bytes(["Нужны автоматы 16А"]))
    assert a.status == b.status == "ready"
    result = resolve(docs, "s1", [b.id, a.id, b.id])
    assert [str(d.asset_id) for d in result] == [b.id, a.id, b.id]
    assert "990100001_ | 5" in result[1].text


def test_other_session_gets_not_found(docs):
    asset = docs.upload("s1", "spec.xlsx", xlsx([["x"]]))
    with pytest.raises(AppError) as e:
        resolve(docs, "intruder", [asset.id])
    assert e.value.status == 404
    with pytest.raises(AppError):
        resolve(docs, "s1", [asset.id, str(uuid.uuid4())])
    with pytest.raises(AppError):
        docs.status("intruder", asset.id)


def test_unreadable_file_is_stored_as_failed_and_not_resolved(docs):
    asset = docs.upload("s1", "photo.jpg", b"\xff\xd8\xff\xe0" + b"0" * 100)
    assert (asset.status, asset.error) == ("failed", "needs_ocr")
    with pytest.raises(AppError) as e:
        resolve(docs, "s1", [asset.id])
    assert e.value.status == 409


def test_scanner_rules(pool, tmp_path):  # noqa: F811
    from app.adapters.postgres.documents import LocalBlobStore, PostgresDocuments
    blobs = LocalBlobStore(tmp_path / "a")
    data = xlsx([["x"]])
    with pytest.raises(AppError) as e:
        PostgresDocuments(pool, blobs, FakeScanner("unavailable")).upload("s1", "f.xlsx", data)
    assert e.value.code == "scanner_unavailable"
    with pytest.raises(AppError) as e:
        PostgresDocuments(pool, blobs, FakeScanner("infected")).upload("s1", "f.xlsx", data)
    assert e.value.code == "infected"
    demo = PostgresDocuments(pool, blobs, FakeScanner("unavailable"), allow_unscanned=True)
    asset = demo.upload("s1", "f.xlsx", data)
    assert "not_virus_scanned" in resolve(demo, "s1", [asset.id])[0].warnings
    assert any((tmp_path / "a").iterdir())


def test_rejected_type_is_not_stored(docs, tmp_path):
    with pytest.raises(AppError) as e:
        docs.upload("s1", "virus.exe", b"MZ\x90\x00")
    assert e.value.status == 415
    assert not any((tmp_path / "assets").iterdir())


def test_expired_assets_are_removed(docs, pool, tmp_path):  # noqa: F811
    asset = docs.upload("s1", "spec.xlsx", xlsx([["x"]]))
    with pool.connection() as conn:
        conn.execute("UPDATE assets SET expires_at = now() - interval '1 minute'")
    with pytest.raises(AppError):
        resolve(docs, "s1", [asset.id])
    assert docs.cleanup_expired() == 1
    assert not any((tmp_path / "assets").iterdir())
