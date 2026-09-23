"""Выборка реальных товаров ekt.kz для пополнения демо-каталога.

Берём из API партнёра название, фото (ссылкой на ekt.kz, файл не копируем),
раздел каталога, характеристики и описание. Цены, остатки, ID и наши артикулы
потом генерирует generate.py, поэтому данные остаются демонстрационными.

Запуск (нужны EKT_API_USER и EKT_API_PASSWORD в .env):
    python data/synthetic/fetch_ekt_selection.py
Результат: data/synthetic/ekt_selection.json
"""

import base64
import json
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent / "ekt_selection.json"

# Сколько добавить в каждый раздел: пустые до 20, где было 3-5 товаров примерно до 25.
QUOTAS = {
    "kabel_provod": 21, "svetilniki_lampy": 20, "rozetki_vyklyuchateli_korobki": 21,
    "kabelenesushchie_sistemy": 20, "izdeliya_dlya_montazha_i_instrument": 20, "prochee_oborudovanie": 20,
    "shkafy_shchity": 20, "avtomatizatsiya": 20, "videonablyudenie_skud_signalizatsiya": 20,
    "instrument_kip": 20, "korzina_elektrika": 20,
}
PER_SUBCATEGORY = 4  # чтобы раздел не состоял из одной акции или одной серии
MAX_PAGES = 1500


def _env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    token = base64.b64encode(f"{os.environ['EKT_API_USER']}:{os.environ['EKT_API_PASSWORD']}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Accept": "application/json"}


HEADERS = _env()


def get(path: str) -> dict:
    for attempt in range(3):
        try:
            req = urllib.request.Request(f"https://ekt.kz{path}", headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception:  # noqa: BLE001 - сеть партнёра, пробуем ещё раз
            time.sleep(1 + attempt)
    raise RuntimeError(f"failed {path}")


def category(url: str) -> tuple[str, ...]:
    path = re.sub(r"^https?://[^/]+", "", url or "")
    if "/catalog/" not in path:
        return ()
    return tuple(p for p in path.split("/catalog/", 1)[1].split("/") if p)[:-1]


def main() -> None:
    picked: dict[str, list[dict]] = {k: [] for k in QUOTAS}
    per_sub: dict[tuple, int] = {}
    seen_names: set[str] = set()
    previous = None
    with ThreadPoolExecutor(3) as pool:
        for start in range(1, MAX_PAGES + 1, 3):
            pages = list(pool.map(lambda p: get(f"/api/products?page={p}"), range(start, start + 3)))
            stop = False
            for page in pages:
                ids = [i["id"] for i in page["items"]]
                if not ids or ids == previous:
                    stop = True
                    break
                previous = ids
                for item in page["items"]:
                    path = category(item.get("url", ""))
                    if len(path) < 2 or path[0] not in QUOTAS or not item.get("image"):
                        continue
                    if len(picked[path[0]]) >= QUOTAS[path[0]] or per_sub.get(path[:2], 0) >= PER_SUBCATEGORY:
                        continue
                    key = re.sub(r"\W+", "", item["name"].lower())
                    if key in seen_names:
                        continue
                    seen_names.add(key)
                    per_sub[path[:2]] = per_sub.get(path[:2], 0) + 1
                    picked[path[0]].append(item)
            done = sum(len(v) for v in picked.values())
            print(f"pages {start}-{start + 2}: picked {done}/{sum(QUOTAS.values())}", flush=True)
            if stop or all(len(picked[k]) >= QUOTAS[k] for k in QUOTAS):
                break

        chosen = [item for items in picked.values() for item in items]
        details = list(pool.map(lambda i: get(f"/api/products/detail?id={i['id']}"), chosen))

    selection = []
    for item, detail in zip(chosen, details):
        props = detail.get("properties") or {}
        selection.append({
            "ekt_id": item["id"],
            "name": detail.get("name") or item["name"],
            "image": item["image"],
            "category": list(category(item["url"])),
            "price": detail.get("price"),
            "description": detail.get("description") or "",
            # Только характеристики товара, без служебных полей учётной системы.
            "properties": {k: v for k, v in props.items() if isinstance(v, str) and not k.startswith("CML2_")
                           and k not in {"BRAND_PRIORITY", "NOVINKA", "SPETSPREDLOZHENIE", "IMYAKARTINKI",
                                         "BLOG_POST_ID", "insta", "POKAZYVAT_TSENY", "KOL_VO_U_POSTAVSHCHIKA",
                                         "KOLICHESTVOVREZERVE", "KOL_VO_CHASOV_DOSTAVKI_OT_POSTAVSHCHIKA"}},
        })
    OUT.write_text(json.dumps({"source": "https://ekt.kz/api/products", "fetched": time.strftime("%Y-%m-%d"),
                               "items": selection}, ensure_ascii=False, indent=1), encoding="utf-8")
    counts = {k: len(v) for k, v in picked.items()}
    print(json.dumps(counts, ensure_ascii=False))


def labels() -> None:
    """Русские названия подразделов из заголовков страниц каталога ekt.kz."""
    import html
    items = json.loads(OUT.read_text(encoding="utf-8"))["items"]
    target = ROOT / "data" / "curated" / "category_labels.json"
    known = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    for top, sub in sorted({tuple(i["category"][:2]) for i in items} - {tuple(k.split("/")) for k in known}):
        try:
            req = urllib.request.Request(f"https://ekt.kz/catalog/{top}/{sub}/", headers={"User-Agent": "Mozilla/5.0"})
            page = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            continue
        match = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
        if match:
            known[f"{top}/{sub}"] = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", match[1]))).strip()
        time.sleep(0.3)
    target.write_text(json.dumps(dict(sorted(known.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(known)} labels")


if __name__ == "__main__":
    import sys
    labels() if sys.argv[1:] == ["labels"] else main()
