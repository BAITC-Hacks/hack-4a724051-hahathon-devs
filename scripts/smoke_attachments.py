"""Test real uploads via the frontend; --analyze makes paid OpenAI calls on synthetic fixtures."""
import argparse
import json
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
MIME = {".txt": "text/plain", ".csv": "text/csv", ".pdf": "application/pdf",
        ".jpg": "image/jpeg", ".png": "image/png",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
VISION = {"08_scanned_spec.pdf": {"990100001_", "990100003_"},
          "09_photo_spec.jpg": {"990100001_"}, "06_specification.docx": {"990100001_", "990100003_"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--analyze", action="store_true", help="Send synthetic PDF/photo/DOCX to the configured paid model")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    for path in sorted((ROOT / "test-files/assistant").iterdir()):
        with httpx.Client(base_url=base, timeout=65, trust_env=False) as client:
            def request(method, route, **kwargs):
                response = client.request(method, route, **kwargs)
                if response.is_error:
                    raise RuntimeError(f"{route}: HTTP {response.status_code} {response.json().get('error', {}).get('code')}")
                return response.json()["data"]

            session = request("POST", "/api/v1/session", headers={"Origin": base})
            headers = {"Origin": base, "X-CSRF-Token": session["csrf_token"]}
            try:
                response = client.post("/api/v1/assets/upload", params={"filename": path.name},
                                       headers={**headers, "Content-Type": MIME[path.suffix]}, content=path.read_bytes())
                if path.stat().st_size > 10485760:
                    assert response.status_code == 413, response.status_code
                    print(f"PASS {path.name}: oversized upload rejected", flush=True)
                    continue
                assert response.is_success, (path.name, response.status_code, response.text)
                asset = response.json()["data"]
                assert asset["status"] in {"ready", "partial"}, (path.name, asset["status"], asset["warnings"])
                print(f"PASS {path.name}: scanner + parser = {asset['status']}", flush=True)
                if args.analyze and path.name in VISION:
                    conv = request("POST", "/api/v1/conversations", headers=headers)
                    turn = request("POST", f"/api/v1/conversations/{conv['id']}/turns",
                                   headers={**headers, "Idempotency-Key": str(uuid4())},
                                   json={"text": "Найди товары по артикулам из вложенного файла", "asset_ids": [asset["id"]],
                                         "allow_external_analysis": True})["turn"]
                    deadline = time.monotonic() + 100
                    while turn["status"] in {"queued", "running"} and time.monotonic() < deadline:
                        time.sleep(2)
                        turn = request("GET", f"/api/v1/turns/{turn['id']}")
                    output = turn.get("output") or {}
                    articles = {p["article_original"] for p in output.get("products", [])}
                    print(json.dumps({"file": path.name, "status": turn["status"], "mode": output.get("mode"),
                                      "articles": sorted(articles), "warnings": output.get("warnings")}, ensure_ascii=False), flush=True)
                    assert turn["status"] == "completed" and output.get("mode") == "grounded"
                    assert VISION[path.name] == articles, "Recognized articles did not reach catalog lookup or unrelated items leaked in"
            finally:
                request("POST", "/api/v1/session/logout", headers=headers)


if __name__ == "__main__":
    main()
