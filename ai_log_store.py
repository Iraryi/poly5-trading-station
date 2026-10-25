"""Reference: narrow AI write tool for advice files only.

This module demonstrates file allowlisting + backups + local audit, not a complete
security sandbox. The production Windows build still needs ACLs, tool isolation,
reparse-point-safe file APIs, size quotas and explicit user consent.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import threading
import uuid


_ALLOWED_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}\.(md|json|diff)$")
_ALLOWED_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LOCK = threading.RLock()
_MAX_LENGTH_BYTES = 512_000


def _utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _is_reparse(path: Path) -> bool:
    isjunction = getattr(os.path, "isjunction", lambda _p: False)
    try:
        info = path.lstat()
        attributes = getattr(info, 'st_file_attributes', 0)
        return (path.is_symlink() or isjunction(path)
                or bool(attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024))
                or (path.is_file() and info.st_nlink > 1))
    except FileNotFoundError:
        return False


class AIAdviceStore:
    """Users may create/revise advice ONLY inside <base>/ai log/YYYY-MM-DD/.

    Rejects ../, symlink/junction paths and unknown suffixes. The LLM must never
    directly receive a filesystem/write-shell tool; its tool host calls only
    create()/revise() with validated arguments after schema enforcement.
    """

    def __init__(self, base_dir: str | Path):
        self.base = Path(base_dir).absolute()
        if any(_is_reparse(p) for p in (self.base, *self.base.parents)):
            raise ValueError("data root ancestors cannot contain links")
        self.base.mkdir(parents=True, exist_ok=True)
        if _is_reparse(self.base):
            raise ValueError("data root cannot be symlink or junction")
        self.root = self.base / "ai log"
        self.audit = self.base / "log" / "ai_file_edits.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        self.audit.parent.mkdir(parents=True, exist_ok=True)
        if _is_reparse(self.root) or _is_reparse(self.audit.parent) or _is_reparse(self.audit):
            raise ValueError("file path includes link/junction")

    def _file(self, relpath: str, must_exist: bool | None) -> Path:
        if "\\" in relpath or relpath.startswith(("/", "~")):
            raise ValueError("only forward slash relative paths are accepted")
        parts = relpath.split("/")
        if (len(parts) != 2 or not _ALLOWED_DAY.fullmatch(parts[0])
                or not _ALLOWED_NAME.fullmatch(parts[1]) or parts[1].startswith("..")):
            raise ValueError("path format must be YYYY-MM-DD/slug.md|json|diff")
        target = self.root / parts[0] / parts[1]
        for parent in (self.base, self.root, target.parent, target):
            if parent.exists() and _is_reparse(parent):
                raise ValueError("symlink/junction blocked")
        if target.resolve(strict=False).parent != (self.root / parts[0]).resolve(strict=False):
            raise ValueError("path escapes ai log")
        if must_exist is True and not target.is_file():
            raise FileNotFoundError(target)
        if must_exist is False and target.exists():
            raise FileExistsError(target)
        return target

    def _audit(self, kind: str, relpath: str, data: bytes, prior_digest: str | None):
        if any(_is_reparse(p) for p in (self.base, self.audit.parent, self.audit)):
            raise ValueError("audit path includes a link")
        event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "ai.file_" + kind,
            "observed_at_utc": _utc(),
            "relpath": relpath,
            "sha256": _sha256(data),
            "previous_sha256": prior_digest,
            "size_bytes": len(data)
        }
        with self.audit.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def create(self, relpath: str, content: str) -> Path:
        data = content.encode("utf-8")
        if len(data) > _MAX_LENGTH_BYTES:
            raise ValueError("proposal exceeds max byte budget")
        with _LOCK:
            target = self._file(relpath, must_exist=False)
            target.parent.mkdir(parents=True, exist_ok=True)
            target = self._file(relpath, must_exist=False)
            with target.open("xb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            self._audit("created", relpath, data, prior_digest=None)
            return target

    def revise(self, relpath: str, content: str) -> Path:
        data = content.encode("utf-8")
        if len(data) > _MAX_LENGTH_BYTES:
            raise ValueError("proposal exceeds max byte budget")
        with _LOCK:
            target = self._file(relpath, must_exist=True)
            old_data = target.read_bytes()
            versions = self.root / "versions"
            if versions.exists() and _is_reparse(versions):
                raise ValueError("versions path is a link")
            versions.mkdir(parents=True, exist_ok=True)
            prior_digest = _sha256(old_data)
            backup = versions / f"{target.stem}-{uuid.uuid4().hex}-{prior_digest[:12]}{target.suffix}"
            with backup.open("xb") as f:
                f.write(old_data)
                f.flush()
                os.fsync(f.fileno())
            fd, tmpname = tempfile.mkstemp(dir=target.parent, prefix=".ai-", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                target = self._file(relpath, must_exist=True)
                os.replace(tmpname, target)
            finally:
                if os.path.exists(tmpname):
                    os.unlink(tmpname)
            self._audit("revised", relpath, data, prior_digest=prior_digest)
            return target

    def read(self, relpath: str) -> str:
        with _LOCK:
            path = self._file(relpath, must_exist=True)
            if path.stat().st_size > _MAX_LENGTH_BYTES:
                raise ValueError("proposal exceeds max byte budget")
            return path.read_text(encoding="utf-8")
