"""Download and cache the CMS source files.

Polite by design: one request at a time, a pause between requests, retries with
backoff, and everything cached in data/raw/ so nothing is fetched twice.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

USER_AGENT = (
    "medicare-cost-report-benchmarks "
    "(+https://github.com/soriaga-associates/medicare-cost-report-benchmarks)"
)
PAUSE_SECONDS = 2.0
RETRIES = 4

CATALOG_URL = "https://data.cms.gov/data.json"
HOSPICE_ZIP_URL = "https://downloads.cms.gov/Files/hcris/HOSPC14-ALL-YEARS.zip"
HOSPICE_DOC_URL = "https://www.cms.gov/files/zip/hospc14-documentation.zip"
HOSPICE_ZIP = "hospice/HOSPC14-ALL-YEARS.zip"
HOSPICE_DOC = "hospice/hospc14-documentation.zip"
HHA_YEARS = (2020, 2021, 2022, 2023, 2024)
HHA_TITLE = re.compile(r"^Home Health Agency Cost Report : (\d{4})-")


def hha_key(year: int) -> str:
    return f"home-health/hha_{year}.csv"


class Fetcher:
    """Sequential HTTP client: pause between requests, retry with backoff."""

    def __init__(self, pause: float = PAUSE_SECONDS, log=print):
        self.pause = pause
        self.log = log
        self._last = 0.0

    def _wait(self):
        gap = self.pause - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)

    def request(self, url: str, method: str = "GET", dest: Path | None = None):
        """Return (headers, body_bytes_or_None, sha256_or_None). Streams to dest if given."""
        last_err = None
        for attempt in range(RETRIES):
            self._wait()
            try:
                req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=120) as resp:
                    headers = dict(resp.headers.items())
                    if method == "HEAD":
                        return headers, None, None
                    if dest is None:
                        return headers, resp.read(), None
                    part = dest.with_suffix(dest.suffix + ".part")
                    part.parent.mkdir(parents=True, exist_ok=True)
                    h = hashlib.sha256()
                    with open(part, "wb") as out:
                        while chunk := resp.read(1 << 20):
                            out.write(chunk)
                            h.update(chunk)
                    part.replace(dest)
                    return headers, None, h.hexdigest()
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e
                if isinstance(e, urllib.error.HTTPError) and e.code in (400, 401, 403, 404):
                    break
                wait = 2 ** (attempt + 1)
                self.log(f"  request failed ({e}); retrying in {wait}s")
                time.sleep(wait)
            finally:
                self._last = time.monotonic()
        raise RuntimeError(f"could not fetch {url}: {last_err}")


def load_manifest(data_dir: Path) -> dict:
    p = data_dir / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_manifest(data_dir: Path, manifest: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def http_date_to_iso(text: str | None) -> str | None:
    if not text:
        return None
    try:
        return datetime.strptime(text, "%a, %d %b %Y %H:%M:%S %Z").date().isoformat()
    except ValueError:
        return text


def hha_sources_from_catalog(catalog: dict) -> dict[int, dict]:
    """{year: {"url", "modified", "title"}} for the CSV distribution of each year's dataset."""
    out = {}
    for ds in catalog.get("dataset", []):
        m = HHA_TITLE.match(ds.get("title", ""))
        if not m:
            continue
        for dist in ds.get("distribution", []):
            if dist.get("format") == "CSV" and dist.get("downloadURL"):
                out[int(m.group(1))] = {
                    "url": dist["downloadURL"],
                    "modified": ds.get("modified"),
                    "title": ds["title"],
                }
    return out


def ensure_file(f: Fetcher, data_dir: Path, key: str, url: str, force: bool, manifest: dict, extra=None) -> bool:
    """Make sure data_dir/key exists and is recorded in the manifest. Returns True if downloaded."""
    path = data_dir / key
    entry = manifest.get(key)
    if path.exists() and not force:
        if (
            entry
            and entry.get("url") == url
            and entry.get("bytes") == path.stat().st_size
            and all(k in entry for k in (extra or {}))
        ):
            f.log(f"  cached: {key}")
            return False
        # File is there (for example copied in by hand) but not in the manifest: check it.
        headers, _, _ = f.request(url, method="HEAD")
        expected = headers.get("Content-Length")
        if expected and int(expected) != path.stat().st_size:
            f.log(f"  {key}: size differs from the server ({path.stat().st_size} vs {expected}); downloading again")
        else:
            manifest[key] = {
                "url": url,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "retrieved": date.fromtimestamp(path.stat().st_mtime).isoformat(),
                "last_modified": http_date_to_iso(headers.get("Last-Modified")),
                **(extra or {}),
            }
            f.log(f"  verified existing file: {key}")
            return False
    f.log(f"  downloading: {key}")
    headers, _, digest = f.request(url, dest=path)
    manifest[key] = {
        "url": url,
        "bytes": path.stat().st_size,
        "sha256": digest,
        "retrieved": datetime.now(timezone.utc).date().isoformat(),
        "last_modified": http_date_to_iso(headers.get("Last-Modified")),
        **(extra or {}),
    }
    return True


def download_all(data_dir: Path, force: bool = False, log=print, pause: float = PAUSE_SECONDS) -> dict:
    f = Fetcher(pause=pause, log=log)
    manifest = load_manifest(data_dir)

    log("Hospice (HCRIS):")
    ensure_file(f, data_dir, HOSPICE_ZIP, HOSPICE_ZIP_URL, force, manifest)
    save_manifest(data_dir, manifest)
    ensure_file(f, data_dir, HOSPICE_DOC, HOSPICE_DOC_URL, force, manifest)
    save_manifest(data_dir, manifest)

    log("Home health (data.cms.gov):")
    need_catalog = force or any(
        not (data_dir / hha_key(y)).exists() or hha_key(y) not in manifest or "cms_modified" not in manifest[hha_key(y)]
        for y in HHA_YEARS
    )
    catalog_path = data_dir / "catalog.json"
    if need_catalog:
        log("  reading the data.cms.gov catalog")
        _, body, _ = f.request(CATALOG_URL)
        data_dir.mkdir(parents=True, exist_ok=True)
        catalog_path.write_bytes(body)
        sources = hha_sources_from_catalog(json.loads(body))
        for y in HHA_YEARS:
            if y not in sources:
                raise RuntimeError(f"no CSV found in the CMS catalog for home health {y}")
            ensure_file(
                f, data_dir, hha_key(y), sources[y]["url"], force, manifest,
                extra={"cms_modified": sources[y]["modified"], "title": sources[y]["title"]},
            )
            save_manifest(data_dir, manifest)
    else:
        for y in HHA_YEARS:
            log(f"  cached: {hha_key(y)}")
    save_manifest(data_dir, manifest)
    return manifest
