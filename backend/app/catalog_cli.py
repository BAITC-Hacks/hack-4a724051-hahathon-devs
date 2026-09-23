"""Команды для БД каталога.

python -m app.catalog_cli migrate
python -m app.catalog_cli import-synthetic
python -m app.catalog_cli import-ekt --pages 3
python -m app.catalog_cli status

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
    args = parser.parse_args(argv)

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL не задан", file=sys.stderr)
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


if __name__ == "__main__":
    sys.exit(main())
