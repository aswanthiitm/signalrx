"""Polite HTTP with an on-disk cache. Every fetch is recorded so a live run can
be frozen into a replay bundle (see live.freeze_bundle)."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 SignalRx-research/1.0")


@dataclass
class Fetched:
    url: str
    status: int
    content_type: str
    body: bytes
    retrieved_at: str

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


@dataclass
class Fetcher:
    cache_dir: Path = Path(".cache/http")
    min_interval: float = 0.8
    timeout: float = 25.0
    log: list[dict] = field(default_factory=list)
    _last: dict = field(default_factory=dict)

    def __post_init__(self):
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"})

    def _key(self, url: str, params: dict | None) -> Path:
        h = hashlib.sha256((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()[:24]
        return self.cache_dir / h

    def get(self, url: str, params: dict | None = None, headers: dict | None = None, use_cache: bool = True,
            retries: int = 3) -> Fetched:
        key = self._key(url, params)
        meta = key.with_suffix(".json")
        if use_cache and meta.exists():
            m = json.loads(meta.read_text())
            f = Fetched(m["url"], m["status"], m["content_type"], key.with_suffix(".bin").read_bytes(), m["retrieved_at"])
            self.log.append({"url": url, "cached": True, "status": f.status})
            return f
        host = requests.utils.urlparse(url).netloc
        wait = self.min_interval - (time.time() - self._last.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        err = None
        for attempt in range(retries):
            try:
                r = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
                self._last[host] = time.time()
                f = Fetched(r.url, r.status_code, r.headers.get("content-type", ""), r.content,
                            datetime.now(timezone.utc).isoformat(timespec="seconds"))
                if r.status_code in (429, 502, 503, 504):
                    time.sleep(2 ** attempt * 2)
                    continue
                if r.ok:
                    key.with_suffix(".bin").write_bytes(f.body)
                    meta.write_text(json.dumps({"url": f.url, "status": f.status, "content_type": f.content_type,
                                                "retrieved_at": f.retrieved_at}))
                self.log.append({"url": url, "cached": False, "status": f.status})
                return f
            except requests.RequestException as e:  # network errors: back off and retry
                err = e
                time.sleep(2 ** attempt * 2)
        self.log.append({"url": url, "error": str(err)})
        raise ConnectionError(f"GET {url} failed: {err}")

    def json(self, url: str, params: dict | None = None, headers: dict | None = None):
        f = self.get(url, params=params, headers=headers)
        if f.status != 200:
            raise ConnectionError(f"GET {url} -> {f.status}")
        return json.loads(f.body)
