"""Run from backend: python scripts/export_openapi.py"""
import json
from pathlib import Path

from app.core.config import Settings
from app.main import create_app

root = Path(__file__).resolve().parents[2]
app = create_app(Settings(_env_file=None, app_env="test"))
target = root / "docs" / "openapi.json"
target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(target)
