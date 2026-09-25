from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import InputError

_TIME_RE = re.compile(
    r"(?P<hours>\d{1,3}):(?P<minutes>\d{2}):(?P<seconds>\d{2})"
    r"[,.](?P<milliseconds>\d{1,3})"
)
_UNSAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_run_id() -> str:
    return str(uuid.uuid4())


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: Any, length: int = 64) -> str:
    payload = canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:length]


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, content: str) -> None:
    atomic_write_bytes(path, content.encode("utf-8"))


def append_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = content.encode("utf-8")
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0, os.SEEK_END)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def append_jsonl(path: Path, value: Any) -> None:
    append_text(path, canonical_json(value) + "\n")


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def parse_timestamp_ms(value: str) -> int:
    match = _TIME_RE.search(value.strip())
    if not match:
        raise ValueError(f"Invalid timestamp: {value!r}")
    milliseconds = match.group("milliseconds").ljust(3, "0")
    return (
        int(match.group("hours")) * 3_600_000
        + int(match.group("minutes")) * 60_000
        + int(match.group("seconds")) * 1_000
        + int(milliseconds)
    )


def format_timestamp_ms(value: int | float) -> str:
    value = max(0, int(round(value)))
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def parse_scope(value: str | None) -> int | None:
    if value is None:
        return None
    if value.strip().startswith("-"):
        raise InputError("Timecode cannot be negative")
    try:
        parsed = parse_timestamp_ms(value)
    except ValueError as exc:
        raise InputError(str(exc)) from exc
    if parsed < 0:
        raise InputError("Timecode cannot be negative")
    return parsed


def sanitize_component(value: str) -> str:
    cleaned = _UNSAFE_COMPONENT_RE.sub("_", value).strip("._")
    return cleaned or "audio"


def source_relative_path(path: Path, input_root: Path | None) -> str:
    resolved = path.resolve()
    if input_root is not None:
        root = input_root.resolve()
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            pass
    return resolved.name


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"Cannot read JSON file {path}: {exc}") from exc


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise InputError(f"Cannot read text file {path}: {exc}") from exc
