"""Импорт каталога в БД: из синтетического файла или из API ekt.kz.

Правила обхода API (контракт партнёра неполный):
- пустая страница считается концом каталога, run получает статус complete;
- если страница повторяет предыдущую, или упёрлись в max_pages, или источник
  упал, run получает статус partial, уже загруженные товары остаются;
- ошибка одной карточки не останавливает импорт, она считается в products_failed.
Отсутствие товара в partial-импорте не значит, что его нет в магазине.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from psycopg_pool import ConnectionPool

from app.adapters.ekt.client import EktAuthError, EktClient, EktError
from app.adapters.postgres.catalog import upsert_product

log = logging.getLogger(__name__)


@dataclass
class SyncReport:
    run_id: int
    status: str
    pages_read: int = 0
    upserted: int = 0
    failed: int = 0
    error: str | None = None


def _start(pool: ConnectionPool, source: str) -> int:
    with pool.connection() as conn:
        return conn.execute("INSERT INTO catalog_sync_runs (source, status) VALUES (%s, 'running') RETURNING id",
                            (source,)).fetchone()[0]


def _finish(pool: ConnectionPool, report: SyncReport, last_page: int | None = None) -> SyncReport:
    with pool.connection() as conn:
        conn.execute(
            "UPDATE catalog_sync_runs SET status=%s, finished_at=now(), pages_read=%s, products_upserted=%s, "
            "products_failed=%s, last_page=%s, error=%s WHERE id=%s",
            (report.status, report.pages_read, report.upserted, report.failed, last_page, report.error,
             report.run_id),
        )
    return report


def import_file(pool: ConnectionPool, path: Path, source: str = "synthetic") -> SyncReport:
    report = SyncReport(_start(pool, source), "running")
    items = json.loads(Path(path).read_text(encoding="utf-8"))["items"]
    with pool.connection() as conn:
        for raw in items:
            try:
                with conn.transaction():
                    upsert_product(conn, raw, source)
                report.upserted += 1
            except (KeyError, TypeError, ValueError) as e:
                log.warning("skip product %s: %s", raw.get("id"), e)
                report.failed += 1
    report.status = "complete"
    return _finish(pool, report)


def import_ekt(pool: ConnectionPool, client: EktClient, max_pages: int = 5, start_page: int = 1) -> SyncReport:
    report = SyncReport(_start(pool, "ekt"), "running")
    previous_ids: list[int] | None = None
    page = start_page
    reached_end = False
    try:
        while page < start_page + max_pages:
            data = client.list_page(page)
            ids = [item["id"] for item in data["items"]]
            report.pages_read += 1
            if not ids:
                reached_end = True
                break
            if ids == previous_ids:
                report.error = f"page {page} repeats previous page"
                break
            previous_ids = ids
            for product_id in ids:
                try:
                    raw = client.get_detail(product_id)
                except EktAuthError:
                    raise
                except EktError as e:
                    log.warning("detail %s failed: %s", product_id, e)
                    report.failed += 1
                    continue
                with pool.connection() as conn:
                    upsert_product(conn, raw, "ekt")
                report.upserted += 1
            page += 1
    except EktError as e:
        report.error = f"{type(e).__name__}: {e}"
        report.status = "failed" if report.upserted == 0 else "partial"
        return _finish(pool, report, page)
    report.status = "complete" if reached_end and report.failed == 0 else "partial"
    return _finish(pool, report, page)


def coverage(pool: ConnectionPool) -> dict:
    """Что сейчас лежит в каталоге и насколько полон последний импорт."""
    with pool.connection() as conn:
        counts = dict(conn.execute("SELECT source, count(*) FROM products GROUP BY source").fetchall())
        last = conn.execute(
            "SELECT source, status, finished_at FROM catalog_sync_runs WHERE status <> 'running' "
            "ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "products": counts,
        "last_sync": {"source": last[0], "status": last[1], "finished_at": last[2].isoformat() if last[2] else None}
        if last else None,
        "coverage": "complete" if last and last[1] == "complete" else "partial",
    }
