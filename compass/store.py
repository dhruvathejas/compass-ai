"""A small, safe JSON document store.

* One file per store, written atomically (temp file + fsync + replace) so a crash can't corrupt it.
* A lock serialises access; callers use ``read(fn)`` or ``with transaction() as data``.
* A damaged file is moved aside (``.broken-<time>``) instead of being overwritten.
* ``schema`` versioning with ordered migrations; the old file is backed up before the first migration.
* The parsed document is cached and re-read only when the file changes on disk.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("compass.store")


class StoreError(RuntimeError):
    pass


class JsonStore:
    def __init__(self, path: Path, default: Callable[[], dict], *, version: int = 1,
                 migrations: dict[int, Callable[[dict], None]] | None = None):
        self.path = Path(path)
        self._default = default
        self._version = version
        self._migrations = migrations or {}
        self._lock = threading.RLock()
        self._cache: dict | None = None
        self._sig: tuple | None = None

    # ------------------------------------------------------------ public API

    def read(self, fn: Callable[[dict], object]):
        """Run ``fn(data)`` under the lock and return its result. ``fn`` must not mutate ``data``."""
        with self._lock:
            return fn(self._load())

    @contextlib.contextmanager
    def transaction(self):
        """Yield the document for mutation; it is saved on success and discarded on error."""
        with self._lock:
            data = self._load()
            try:
                yield data
                self._save(data)
            except BaseException:
                self._cache = self._sig = None   # drop partial edits, re-read from disk next time
                raise

    # ------------------------------------------------------------ internals

    def _signature(self):
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _load(self) -> dict:
        sig = self._signature()
        if self._cache is not None and sig == self._sig:
            return self._cache

        data = None
        if sig is not None:
            try:
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("top level is not an object")
            except (OSError, ValueError) as exc:     # JSONDecodeError is a ValueError
                broken = self.path.with_name(f"{self.path.name}.broken-{int(time.time())}")
                log.warning("%s is damaged (%s); keeping it as %s and starting fresh", self.path.name, exc, broken.name)
                with contextlib.suppress(OSError):
                    os.replace(self.path, broken)
                data = None

        if data is None:
            data = self._default()
            data["schema"] = self._version
        else:
            data = self._migrate(data)

        self._cache, self._sig = data, self._signature()
        return data

    def _migrate(self, data: dict) -> dict:
        found = int(data.get("schema", 1))
        if found > self._version:
            raise StoreError(f"{self.path.name} was written by a newer Compass AI (schema {found}); refusing to touch it.")
        if found == self._version:
            return data
        backup = self.path.with_name(f"{self.path.name}.bak-v{found}")
        if not backup.exists():
            with contextlib.suppress(OSError):
                shutil.copy2(self.path, backup)
        for step in range(found, self._version):
            fn = self._migrations.get(step)
            if fn is None:
                raise StoreError(f"No migration from schema {step} for {self.path.name}")
            fn(data)
            data["schema"] = step + 1
        self._save(data)
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            self._replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        self._cache, self._sig = data, self._signature()

    @staticmethod
    def _replace(src, dst, attempts: int = 6):
        # On Windows the destination can be briefly locked by antivirus or an indexer.
        for i in range(attempts):
            try:
                os.replace(src, dst)
                return
            except PermissionError:
                if i == attempts - 1:
                    raise
                time.sleep(0.05 * (i + 1))
