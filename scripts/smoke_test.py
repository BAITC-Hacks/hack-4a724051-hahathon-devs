"""Exercise a running prototype through HTTP; writes only to a new demo session.

Run with backend's Python: scripts/smoke_test.py --base-url http://127.0.0.1:3000
API and worker must be running. Real checkout adapters are deliberately refused.
"""
import argparse
import time
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--origin", default=None)
    parser.add_argument("--article", default="990100003_")
    args = parser.parse_args()
    origin = args.origin or args.base_url.rstrip("/")
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=15, follow_redirects=False) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code >= 400:
                try:
                    code = response.json().get("error", {}).get("code", "unknown")
                except ValueError:
                    code = "non_json_response"
                raise RuntimeError(f"{method} {path}: HTTP {response.status_code} ({code})")
            return response.json()["data"]

        caps = request("GET", "/api/v1/capabilities")
        assert caps["catalog"] in {"ready", "synthetic_demo"}, "Catalog is not configured"
        assert caps["cart"] == "synthetic_demo", "Smoke test only supports the local demo cart"
        session = request("POST", "/api/v1/session", headers={"Origin": origin})
        headers = {"Origin": origin, "X-CSRF-Token": session["csrf_token"]}

        def post(path, payload=None, key=None):
            return request("POST", path, headers={**headers, "Idempotency-Key": key or str(uuid4())},
                           **({"json": payload} if payload is not None else {}))

        categories = request("GET", "/api/v1/catalog/categories")
        listing = request("GET", "/api/v1/catalog/products")
        assert listing["total"] > 0
        assert sum(item["count"] for item in categories["items"]) == listing["total"]
        matches = request("GET", "/api/v1/products", params={"query": args.article})
        product = next((item for item in matches["items"] if item["article_original"] == args.article), None)
        assert product is not None, "Article is absent from this catalog; supply --article from an available card"
        detail = request("GET", f"/api/v1/products/{product['id']}")
        assert detail["id"] == product["id"]
        print(f"PASS catalog: {listing['total']} products; exact article found")

        conversation = post("/api/v1/conversations")
        submitted = post(f"/api/v1/conversations/{conversation['id']}/turns", {"text": args.article})
        turn_id = submitted["turn"]["id"]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            turn = request("GET", f"/api/v1/turns/{turn_id}")
            if turn["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Worker did not finish in 90 seconds; check worker and shared LOCAL_DB_PATH")
        assert turn["status"] == "completed", f"Turn failed: {turn.get('error_code')}"
        assert any(item["id"] == product["id"] for item in turn["output"]["products"])
        print("PASS chat: queued turn completed by worker using catalog facts")

        initial = request("GET", "/api/v1/cart")
        assert not initial["items"]
        quantity = max(1, int(product.get("min_order") or 1))
        assert product["stock_status"] == "available" and int(product["sellable_quantity"]) >= quantity
        proposal = post("/api/v1/cart/proposals", {"items": [{"product_id": product["id"], "quantity": quantity}]})
        assert proposal["status"] == "proposed"
        assert not request("GET", "/api/v1/cart")["items"], "Proposal mutated cart before consent"
        key = str(uuid4())
        path = f"/api/v1/proposals/{proposal['id']}/confirm"
        result = post(path, {"version": proposal["version"]}, key)
        assert result["status"] == "applied"
        confirmed = request("GET", "/api/v1/cart")
        post(path, {"version": proposal["version"]}, key)
        assert request("GET", "/api/v1/cart") == confirmed, "Confirmation replay duplicated cart"
        assert len(confirmed["items"]) == 1 and confirmed["items"][0]["quantity"] == quantity
        assert result["cart_url"] and client.get(result["cart_url"]).status_code == 200
        post("/api/v1/session/logout")
        print("PASS cart: consent required, replay is safe, cart link works; test session removed")


if __name__ == "__main__":
    main()
