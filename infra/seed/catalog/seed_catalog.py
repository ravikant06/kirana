"""
Load a realistic demo catalog into Kirana, through Kirana's own API (AI track, Phase 4 prep).

    python3 infra/seed/catalog/seed_catalog.py              # users + 150 products + images
    python3 infra/seed/catalog/seed_catalog.py --no-images  # skip the image downloads
    python3 infra/seed/catalog/seed_catalog.py --images-only  # add photos to products that have none

Needs the backend on :8080 (with the category API) and MinIO on :9000. Standard library only.

Everything goes through the same endpoints the shop uses, so validation, the cache, and later
(Phase 4) the outbox's product events all see this data like any admin edit:
  POST /users                                 18 shoppers with Indian names (Ravi and Puja exist)
  POST /products                              name, description, category, price
  PUT  /products/{id}/inventory               stock
  POST /products/{id}/images/upload-url       then POST the file straight to MinIO (D7),
  POST /products/{id}/images/{imgId}/confirm  then confirm

Images come from Wikimedia Commons: freely licensed photos, fetched with its public API (no key).
Google Images is not used: scraping it breaks its terms, and its results are mostly copyrighted.
Commons licences (CC BY / CC BY-SA) require attribution: see credits.json, written next to this file.
"""
import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).parent
API = "http://localhost:8080"
COMMONS = "https://commons.wikimedia.org/w/api.php"
# Wikimedia asks for a descriptive User-Agent. No personal contact details are sent.
UA = "KiranaLearningProject/1.0 (local development; demo catalog seeding)"

SHOPPERS = [
    "Aarav Sharma", "Ananya Iyer", "Rohan Mehta", "Priya Nair", "Vikram Singh", "Kavya Reddy",
    "Arjun Patel", "Sneha Kulkarni", "Rahul Verma", "Meera Krishnan", "Aditya Joshi", "Divya Menon",
    "Karan Malhotra", "Neha Gupta", "Siddharth Rao", "Pooja Deshpande", "Imran Khan", "Lakshmi Subramanian",
]


_admin_token: str | None = None


def _admin_header() -> dict:
    """AI Phase 5: product and user admin calls need an admin token (user 1, the demo password)."""
    global _admin_token
    if _admin_token is None:
        req = urllib.request.Request(API + "/auth/login", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"email": os.getenv("KIRANA_ADMIN_EMAIL", "kumar.ravee101@gmail.com"),
                                                      "password": os.getenv("KIRANA_ADMIN_PASSWORD", "kirana123")}).encode())
        with urllib.request.urlopen(req, timeout=30) as r:
            _admin_token = json.loads(r.read())["accessToken"]
    return {"Authorization": f"Bearer {_admin_token}"}


def call(method: str, path: str, body=None) -> dict | None:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          **_admin_header()})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            text = r.read().decode()
            return json.loads(text) if text else None
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode()[:300]}") from None


PAUSE = 1.5          # seconds between products: Commons rate-limits bursts (HTTP 429)


def fetch(req: urllib.request.Request, timeout: int = 60, attempts: int = 6):
    """urlopen that respects 429 Too Many Requests: wait Retry-After (or back off), then retry."""
    for attempt in range(1, attempts + 1):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == attempts:
                raise
            wait = int(e.headers.get("Retry-After") or 0) or min(60, 2 ** attempt)
            print(f"      429 from {urllib.parse.urlparse(req.full_url).netloc}: waiting {wait} s", flush=True)
            time.sleep(wait)


def find_image(query: str) -> dict | None:
    """Best freely-licensed JPEG/PNG on Commons for a query: a 600 px thumbnail URL plus credits."""
    params = {
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": 8,
        "gsrsearch": f"{query} filetype:bitmap", "prop": "imageinfo",
        "iiprop": "url|mime|extmetadata", "iiurlwidth": 600,
    }
    req = urllib.request.Request(COMMONS + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA})
    with fetch(req, timeout=30) as r:
        pages = json.load(r).get("query", {}).get("pages", {})
    # Search order is relevance: keep it, take the first photo with a usable licence.
    for page in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        info = (page.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata", {})
        licence = meta.get("LicenseShortName", {}).get("value", "")
        if info.get("mime") in ("image/jpeg", "image/png") and info.get("thumburl") and licence:
            artist = meta.get("Artist", {}).get("value", "")
            return {"url": info["thumburl"], "mime": info["mime"], "title": page["title"],
                    "page": info.get("descriptionurl"), "licence": licence,
                    "artist": urllib.parse.unquote(artist)[:300]}
    return None


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with fetch(req) as r:
        return r.read()


def upload_to_minio(ticket: dict, data: bytes, file_name: str, content_type: str) -> None:
    """A multipart/form-data POST, policy fields first and the file last (S3's rule), like the browser."""
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in ticket["formFields"].items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
                 f"Content-Type: {content_type}\r\n\r\n".encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(ticket["uploadUrl"], data=b"".join(parts), method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60):
        pass


def add_image(product_id: int, query: str, credits: list) -> str:
    found = find_image(query)
    if not found:
        return "no image found"
    data = download(found["url"])
    ext = mimetypes.guess_extension(found["mime"]) or ".jpg"
    file_name = f"product-{product_id}{ext}"
    ticket = call("POST", f"/products/{product_id}/images/upload-url",
                  {"fileName": file_name, "contentType": found["mime"], "sizeBytes": len(data)})
    upload_to_minio(ticket, data, file_name, found["mime"])
    call("POST", f"/products/{product_id}/images/{ticket['imageId']}/confirm")
    credits.append({"product_id": product_id, "query": query, **{k: found[k] for k in ("title", "page", "licence", "artist")}})
    return f"image {len(data) // 1024} KB ({found['licence']})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-images", action="store_true")
    parser.add_argument("--images-only", action="store_true", help="only add photos to products without one")
    args = parser.parse_args()
    credits_file = HERE / "credits.json"
    credits = json.loads(credits_file.read_text()) if credits_file.exists() else []

    try:
        call("GET", "/products?page=0&size=1")
    except Exception as e:
        sys.exit(f"Kirana backend not reachable on {API}: {e}")

    catalog = json.loads((HERE / "products.json").read_text())["products"]
    if args.images_only:
        query_by_name = {p["name"]: p["image_query"] for p in catalog}
        todo, page = [], 0
        while True:
            batch = call("GET", f"/products?page={page}&size=50")
            todo += [p for p in batch["content"] if not p.get("thumbnailUrl") and p["name"] in query_by_name]
            page += 1
            if page >= batch["totalPages"]:
                break
        print(f"{len(todo)} product(s) without a photo")
        for i, p in enumerate(todo, 1):
            try:
                note = add_image(p["id"], query_by_name[p["name"]], credits)
            except Exception as e:
                note = f"image skipped: {type(e).__name__}: {str(e)[:80]}"
            print(f"[{i:3}/{len(todo)}] #{p['id']:<4} {p['name'][:42]:<42} {note}", flush=True)
            credits_file.write_text(json.dumps(credits, indent=1, ensure_ascii=False))
            time.sleep(PAUSE)
        print(f"{len(credits)} photos credited in infra/seed/catalog/credits.json")
        return

    existing = {u["email"].lower() for u in call("GET", "/users?limit=50")}
    for name in SHOPPERS:
        first, last = name.lower().split(" ", 1)
        email = f"{first}.{last.replace(' ', '')}@example.com"
        if email not in existing:
            call("POST", "/users", {"name": name, "email": email, "password": "kirana123"})   # the demo password
    print(f"shoppers: {len(call('GET', '/users?limit=50'))} in total")

    for i, p in enumerate(catalog, 1):
        created = call("POST", "/products", {"name": p["name"], "description": p["description"],
                                             "category": p["category"], "price": str(p["price"])})
        pid = created["id"]
        call("PUT", f"/products/{pid}/inventory", {"quantity": p["stock"]})
        note = ""
        if not args.no_images:
            try:
                note = add_image(pid, p["image_query"], credits)
            except Exception as e:          # one missing photo must not stop the catalog
                note = f"image skipped: {type(e).__name__}: {str(e)[:80]}"
            time.sleep(PAUSE)               # be polite to Wikimedia's API
        print(f"[{i:3}/{len(catalog)}] #{pid:<4} {p['category']:<20} {p['name'][:42]:<42} {note}")

    if credits:
        credits_file.write_text(json.dumps(credits, indent=1, ensure_ascii=False))
        print(f"\n{len(credits)} images; licences and authors in infra/seed/catalog/credits.json")


if __name__ == "__main__":
    main()
