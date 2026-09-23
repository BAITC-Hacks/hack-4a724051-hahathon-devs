"""Команды для БД каталога.

python -m app.catalog_cli migrate
python -m app.catalog_cli import-synthetic
python -m app.catalog_cli import-ekt --pages 3
python -m app.catalog_cli status
python -m app.catalog_cli embed [--source synthetic|db]   # индекс смыслового поиска (NVIDIA)

Адрес БД берётся из DATABASE_URL, доступ к EKT из EKT_API_USER / EKT_API_PASSWORD.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

SYNTHETIC = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "ekt_products.json"


def _load_env() -> None:
    env = Path(__file__).resolve().parents[2] / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _load_env()
    parser = argparse.ArgumentParser(prog="catalog_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    sub.add_parser("import-synthetic")
    ekt = sub.add_parser("import-ekt")
    ekt.add_argument("--pages", type=int, default=3)
    ekt.add_argument("--start-page", type=int, default=1)
    sub.add_parser("status")
    embed = sub.add_parser("embed")
    embed.add_argument("--source", choices=["synthetic", "db"], default="synthetic")
    embed.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.command == "embed":
        return _embed(args)

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL не задан. Эти команды нужны только для PostgreSQL "
              "(INTEGRATION_MODE=catalog_db), пример: postgresql://postgres:ПАРОЛЬ@127.0.0.1:5432/ekt. "
              "Для демо без базы достаточно INTEGRATION_MODE=synthetic в .env, эти команды не нужны. "
              "См. data/db/README.md", file=sys.stderr)
        return 2

    from app.infrastructure.db import create_pool, migrate
    if args.command == "migrate":
        print("applied:", migrate(url) or "nothing new")
        return 0

    from app.modules.catalog import sync
    pool = create_pool(url)
    try:
        if args.command == "import-synthetic":
            report = sync.import_file(pool, SYNTHETIC)
        elif args.command == "import-ekt":
            from app.adapters.ekt.client import EktClient
            client = EktClient(os.environ.get("EKT_API_USER", ""), os.environ.get("EKT_API_PASSWORD", ""))
            try:
                report = sync.import_ekt(pool, client, args.pages, args.start_page)
            finally:
                client.close()
        else:
            print(json.dumps(sync.coverage(pool), ensure_ascii=False, indent=1))
            return 0
        print(json.dumps(report.__dict__, ensure_ascii=False))
        return 0 if report.status in ("complete", "partial") else 1
    finally:
        pool.close()


def _embed(args) -> int:
    """Считает эмбеддинги всех карточек и сохраняет индекс для SEMANTIC_SEARCH_ENABLED."""
    from app.adapters.nvidia.client import NvidiaClient
    from app.core.config import Settings
    from app.modules.catalog.quality import product_from_source
    from app.modules.search.semantic import SemanticIndex

    settings = Settings()
    key = settings.nvidia_api_key.get_secret_value()
    if not key:
        print("NVIDIA_API_KEY не задан в .env", file=sys.stderr)
        return 2
    if args.source == "synthetic":
        products = [product_from_source(raw) for raw in json.loads(SYNTHETIC.read_text(encoding="utf-8"))["items"]]
    else:
        from app.infrastructure.db import create_pool
        pool = create_pool(settings.database_url.get_secret_value())
        with pool.connection() as conn:
            products = [product_from_source(raw, ts) for raw, ts in conn.execute("SELECT raw, fetched_at FROM products")]
        pool.close()
    client = NvidiaClient(key)
    try:
        index = SemanticIndex.build(products, client, settings.nvidia_embed_model)
    finally:
        client.close()
    out = args.out or settings.semantic_index_path
    index.save(out)
    print(json.dumps({"model": index.model, "products": len(index.vectors), "file": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
