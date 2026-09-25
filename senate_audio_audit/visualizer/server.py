"""Loopback-only, read-only HTTP server backing the local metrics visualizer.

Three properties matter more than features here.

*No write path.* Only GET and HEAD are implemented, and only for a fixed route
table. The handler never accepts a filesystem path from the request, because any
web page open in the user's browser can issue requests to ``127.0.0.1``; a server
that turned a query parameter into a file read would be a local file-disclosure
primitive. The media path is resolved once at startup from the committed snapshot.

*Range support.* :class:`http.server.SimpleHTTPRequestHandler` does not implement
``Range``, and without it an 85 MB, 89-minute recording cannot be scrubbed: Safari
will not seek at all and other browsers buffer the whole file. This handler answers
``206 Partial Content`` and streams bounded blocks from a seekable handle.

*Localhost hardening.* The server binds loopback only, validates the ``Host``
header against the bound address to blunt DNS rebinding, sends no CORS headers, and
carries a per-session token so a random local page cannot read the payload.
"""

from __future__ import annotations

import json
import mimetypes
import re
import secrets
import threading
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from ..errors import InputError

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
STREAM_BLOCK = 256 * 1024
DEFAULT_IDLE_TIMEOUT = 1_800.0
SERVER_NAME = "senate-audio-audit-visualizer"

#: Snapshot keys that hold absolute local paths and must never reach the page.
_PATH_KEYS = frozenset({"path", "transcript_path", "model_path"})

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


class _RangeUnsatisfiable(Exception):
    """The client asked for a byte range that cannot exist in the file."""


@dataclass(frozen=True, slots=True)
class VisualizerConfig:
    host: str = "127.0.0.1"
    port: int = 0
    open_browser: bool = True
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT
    require_token: bool = True
    verbose: bool = False

    def validated(self) -> VisualizerConfig:
        host = self.host.strip().lower()
        if host not in LOOPBACK_HOSTS:
            raise InputError(
                f"Refusing to bind {self.host!r}: the visualizer serves local paths and "
                "copyrighted-audio-derived measurements, so it binds loopback only"
            )
        if not 0 <= int(self.port) <= 65_535:
            raise InputError(f"Invalid port: {self.port}")
        if float(self.idle_timeout) < 0:
            raise InputError("idle timeout cannot be negative")
        return VisualizerConfig(
            host=host,
            port=int(self.port),
            open_browser=self.open_browser,
            idle_timeout=float(self.idle_timeout),
            require_token=self.require_token,
            verbose=self.verbose,
        )


@dataclass(slots=True)
class VisualizerState:
    """Everything the handler needs, resolved once before the socket is bound."""

    media_path: Path
    media_size: int
    media_content_type: str
    snapshot: dict[str, Any]
    timeline: dict[str, Any] | None
    token: str
    require_token: bool = True
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT
    verbose: bool = False
    on_activity: Any = field(default=None, repr=False)

    def payload(self) -> dict[str, Any]:
        return {
            "snapshot": scrub_paths(self.snapshot),
            "timeline": scrub_paths(self.timeline) if self.timeline else None,
        }


def _basename(value: Any) -> Any:
    """Reduce an absolute local path to its file name."""
    if isinstance(value, str) and (value.startswith("/") or value.startswith("\\")):
        return PurePosixPath(value.replace("\\", "/")).name or value
    if isinstance(value, str) and len(value) > 2 and value[1] == ":":
        return PurePosixPath(value.replace("\\", "/")).name or value
    return value


#: An absolute value that also has several segments and a file extension is a path.
#: Requiring all three keeps French transcript text such as "/etc/hosts" style
#: fragments and slash-prefixed prose from being mangled by the value-based scrub.
_PATH_LIKE = re.compile(r"^(?:/|[A-Za-z]:[\\/]).*[\\/].+\.[A-Za-z0-9]{1,8}$")


def _looks_like_absolute_path(value: Any) -> bool:
    if not isinstance(value, str) or len(value) < 8:
        return False
    if not (value[0] in "/\\" or (value[1] == ":" and value[0].isalpha())):
        return False
    return bool(_PATH_LIKE.match(value))


def scrub_paths(value: Any, keys: frozenset[str] = _PATH_KEYS) -> Any:
    """Recursively replace absolute path values with base names.

    The on-disk snapshot is never modified; only the served copy is scrubbed, so the
    page cannot display ``/Users/<name>/...`` for this machine. Keyed denylists alone
    proved insufficient: the VAD provenance nested under ``quality.vad_model`` leaked a
    home directory, so any absolute-looking value is scrubbed regardless of its key.
    """
    if isinstance(value, dict):
        return {key: scrub_paths(item, keys) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_paths(item, keys) for item in value]
    if _looks_like_absolute_path(value):
        return _basename(value)
    return value


def parse_byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Parse a single-range ``Range`` header into inclusive ``(start, end)`` offsets.

    Returns ``None`` when no usable range was requested, in which case the caller
    serves the whole entity with ``200``. Per RFC 9110 an unsatisfiable *numeric*
    range is a ``416``, but a header this parser does not understand is ignored
    rather than treated as an error, so a curious client degrades instead of
    receiving a spurious failure.
    """
    if not header:
        return None
    value = header.strip()
    if not value.lower().startswith("bytes="):
        return None
    spec = value[len("bytes=") :].strip()
    if "," in spec:
        return None
    start_text, separator, end_text = spec.partition("-")
    if not separator:
        return None
    start_text = start_text.strip()
    end_text = end_text.strip()

    if not start_text:
        # Suffix form: the last N bytes.
        if not end_text.isdigit():
            return None
        suffix = int(end_text)
        if suffix == 0 or size == 0:
            raise _RangeUnsatisfiable(header)
        return max(0, size - suffix), size - 1

    if not start_text.isdigit():
        return None
    start = int(start_text)
    if start >= size:
        raise _RangeUnsatisfiable(header)
    if not end_text:
        return start, size - 1
    if not end_text.isdigit():
        return None
    # An end beyond the entity is clamped rather than rejected.
    return start, min(int(end_text), size - 1)


def _ui_asset(name: str) -> bytes:
    return (resources.files("senate_audio_audit.visualizer") / "ui" / name).read_bytes()


class VisualizerRequestHandler(BaseHTTPRequestHandler):
    server_version = SERVER_NAME
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------
    @property
    def state(self) -> VisualizerState:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if getattr(self.server, "state", None) is not None and self.server.state.verbose:
            super().log_message(format, *args)

    def log_error(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _host_is_allowed(self) -> bool:
        raw = self.headers.get("Host", "")
        if not raw:
            return False
        candidate = raw.strip()
        if candidate.startswith("["):  # bracketed IPv6 literal
            hostname = candidate.split("]", 1)[0] + "]"
        else:
            hostname = candidate.rsplit(":", 1)[0] if ":" in candidate else candidate
        return hostname.strip().lower() in LOOPBACK_HOSTS

    def _token_is_valid(self, query: str) -> bool:
        if not self.state.require_token:
            return True
        for pair in query.split("&"):
            key, _, value = pair.partition("=")
            if key == "t" and secrets.compare_digest(value, self.state.token):
                return True
        return False

    def _send(
        self,
        status: HTTPStatus | int,
        body: bytes,
        content_type: str,
        *,
        extra: dict[str, str] | None = None,
    ) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # No Access-Control-Allow-Origin: the page is same-origin only.
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: HTTPStatus, message: str) -> None:
        body = f"{int(status)} {HTTPStatus(status).phrase}\n{message}\n".encode()
        self._send(status, body, "text/plain; charset=utf-8")

    def _record_activity(self) -> None:
        callback = self.state.on_activity
        if callable(callback):
            callback()

    # -- routing ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        route = parts.path.rstrip("/") or "/"
        query = parts.query

        if not self._host_is_allowed():
            self._error(HTTPStatus.FORBIDDEN, "Unexpected Host header")
            return

        if route in {"/", "/index.html"}:
            self._serve_asset("index.html")
            return
        if route == "/favicon.ico":
            # Without this the browser logs a 404 on every page load.
            self._send(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
            return
        if route in {"/app.css", "/app.js"}:
            self._serve_asset(route.lstrip("/"))
            return
        if route == "/api/payload":
            if not self._token_is_valid(query):
                self._error(HTTPStatus.FORBIDDEN, "Missing or invalid session token")
                return
            self._serve_payload()
            return
        if route == "/audio":
            if not self._token_is_valid(query):
                self._error(HTTPStatus.FORBIDDEN, "Missing or invalid session token")
                return
            self._serve_audio()
            return
        self._error(HTTPStatus.NOT_FOUND, "Unknown route")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    # -- handlers ---------------------------------------------------------
    def _serve_asset(self, name: str) -> None:
        try:
            body = _ui_asset(name)
        except (FileNotFoundError, ModuleNotFoundError):
            self._error(HTTPStatus.NOT_FOUND, f"Asset not packaged: {name}")
            return
        suffix = PurePosixPath(name).suffix
        self._record_activity()
        self._send(
            HTTPStatus.OK,
            body,
            _CONTENT_TYPES.get(suffix)
            or mimetypes.guess_type(name)[0]
            or "application/octet-stream",
            extra={"Cache-Control": "no-store"},
        )

    def _serve_payload(self) -> None:
        try:
            body = json.dumps(self.state.payload(), ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Payload is not serializable: {exc}")
            return
        self._record_activity()
        self._send(
            HTTPStatus.OK,
            body,
            "application/json; charset=utf-8",
            extra={"Cache-Control": "no-store"},
        )

    def _serve_audio(self) -> None:
        path = self.state.media_path
        size = self.state.media_size
        try:
            requested = parse_byte_range(self.headers.get("Range"), size)
        except _RangeUnsatisfiable:
            self.send_response(int(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE))
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self._record_activity()
        if requested is None:
            start, end = 0, size - 1
            status = HTTPStatus.OK
        else:
            start, end = requested
            status = HTTPStatus.PARTIAL_CONTENT

        length = 0 if size == 0 else end - start + 1
        self.send_response(int(status))
        self.send_header("Content-Type", self.state.media_content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD" or length <= 0:
            return

        try:
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    block = handle.read(min(STREAM_BLOCK, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError):
            # The browser seeks by abandoning the stream; that is normal, not an error.
            return


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], state: VisualizerState) -> None:
        self.state = state
        super().__init__(address, VisualizerRequestHandler)


def resolve_media_path(snapshot: dict[str, Any], media_path: Path | None = None) -> Path:
    """Resolve the media to serve, preferring an explicit local path.

    The snapshot records an absolute path that may no longer be valid on this
    machine, so a caller-supplied path wins when it exists.
    """
    if media_path is not None:
        candidate = Path(media_path).expanduser()
        if not candidate.is_file():
            raise InputError(f"Media does not exist: {candidate}")
        return candidate.resolve()
    recorded = str((snapshot.get("source") or {}).get("path") or "")
    if not recorded:
        raise InputError("The snapshot records no source path to play")
    candidate = Path(recorded).expanduser()
    if not candidate.is_file():
        raise InputError(
            f"The analyzed media is not present at {candidate}. Pass the media path "
            "explicitly to play a relocated recording."
        )
    return candidate.resolve()


def media_content_type(path: Path) -> str:
    guessed, _encoding = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def build_state(
    snapshot: dict[str, Any],
    *,
    media_path: Path,
    timeline: dict[str, Any] | None,
    config: VisualizerConfig,
) -> VisualizerState:
    resolved = resolve_media_path(snapshot, media_path)
    return VisualizerState(
        media_path=resolved,
        media_size=resolved.stat().st_size,
        media_content_type=media_content_type(resolved),
        snapshot=snapshot,
        timeline=timeline,
        token=secrets.token_urlsafe(24) if config.require_token else "",
        require_token=config.require_token,
        idle_timeout=config.idle_timeout,
        verbose=config.verbose,
    )


def create_server(state: VisualizerState, config: VisualizerConfig) -> _Server:
    return _Server((config.host, config.port), state)


def token_query(state: VisualizerState) -> str:
    return f"?t={state.token}" if state.require_token else ""


class VisualizerSession:
    """Context manager that serves until interrupted or the idle timeout elapses."""

    def __init__(self, state: VisualizerState, config: VisualizerConfig) -> None:
        self.config = config
        self.state = state
        self._server: _Server | None = None
        self._timer: threading.Timer | None = None
        #: Set when the server stops for any reason. Callers wait on this rather than
        #: on ``BaseServer`` internals, which are name-mangled and not a public API.
        self.closed = threading.Event()

    @property
    def url(self) -> str:
        assert self._server is not None, "session is not running"
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/{token_query(self.state)}"

    def _reset_idle_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        if self.config.idle_timeout > 0 and self._server is not None:
            self._timer = threading.Timer(self.config.idle_timeout, self._stop)
            self._timer.daemon = True
            self._timer.start()

    def _stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
        self.closed.set()

    def __enter__(self) -> VisualizerSession:
        self.state.on_activity = self._reset_idle_timer
        self._server = create_server(self.state, self.config)
        self._reset_idle_timer()
        thread = threading.Thread(target=self._server.serve_forever, name="visualizer", daemon=True)
        thread.start()
        if self.config.open_browser:
            webbrowser.open(self.url)
        return self

    def wait_closed(self, poll_seconds: float = 0.5) -> None:
        """Block until the server stops, so callers need not reach into internals."""
        if self._server is None:
            return
        deadline_poll = max(0.05, float(poll_seconds))
        while not self.closed.wait(deadline_poll):
            continue

    @property
    def port(self) -> int:
        assert self._server is not None, "session is not running"
        return int(self._server.server_address[1])

    def __exit__(self, *_exc: object) -> None:
        if self._timer is not None:
            self._timer.cancel()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        self.closed.set()


def free_port() -> int:
    """Ask the OS for an unused loopback port (used by tests and the CLI)."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


__all__ = [
    "DEFAULT_IDLE_TIMEOUT",
    "LOOPBACK_HOSTS",
    "VisualizerConfig",
    "VisualizerSession",
    "VisualizerState",
    "build_state",
    "create_server",
    "media_content_type",
    "parse_byte_range",
    "resolve_media_path",
    "scrub_paths",
    "token_query",
]
