"""Disk cache for SEC responses: one body file plus one metadata JSON per URL."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path


def default_cache_dir() -> Path:
    configured = os.environ.get("SEC_CACHE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".cache" / "sec-edgar-mcp"


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


class DiskCache:
    """Key = URL (sha256 file name). A missing, expired or corrupt entry is a miss.

    ``ttl`` is in seconds; ``None`` means the entry never expires (immutable Archives documents).
    """

    def __init__(self, directory: Path, *, now: Callable[[], float] = time.time) -> None:
        self.directory = Path(directory)
        self._now = now

    def _paths(self, url: str) -> tuple[Path, Path]:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.directory / f"{key}.body", self.directory / f"{key}.json"

    def get(self, url: str, ttl: float | None) -> bytes | None:
        body_path, meta_path = self._paths(url)
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            body = body_path.read_bytes()
        except (OSError, ValueError):
            return None
        if not isinstance(meta, dict) or meta.get("url") != url:
            return None
        if meta.get("sha256") != hashlib.sha256(body).hexdigest():
            return None
        fetched_at = meta.get("fetched_at")
        if not isinstance(fetched_at, int | float):
            return None
        if ttl is not None and self._now() - fetched_at > ttl:
            return None
        return body

    def put(self, url: str, body: bytes, status: int = 200) -> None:
        body_path, meta_path = self._paths(url)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            meta = {
                "url": url,
                "fetched_at": self._now(),
                "status": status,
                "sha256": hashlib.sha256(body).hexdigest(),
            }
            _atomic_write(body_path, body)
            _atomic_write(meta_path, json.dumps(meta).encode("utf-8"))
        except OSError:
            return
