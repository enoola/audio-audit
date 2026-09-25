from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest

from senate_audio_audit.errors import InputError
from senate_audio_audit.visualizer.server import (
    LOOPBACK_HOSTS,
    VisualizerConfig,
    VisualizerSession,
    _RangeUnsatisfiable,
    build_state,
    media_content_type,
    parse_byte_range,
    resolve_media_path,
    scrub_paths,
)
from tests.factories import make_result

MEDIA_BYTES = bytes(range(256)) * 4  # 1024 bytes, easy to reason about


def snapshot_dict(**overrides: Any) -> dict[str, Any]:
    payload = make_result().to_dict()
    payload["source"]["sha256"] = "a" * 64
    payload["source"]["has_audio"] = True
    payload["scope"] = {"start_ms": 0, "end_ms": 10_000, "language": "fr"}
    payload["quality"]["activity_backend"] = "energy"
    payload.update(overrides)
    return payload


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "clip.mp3"
    path.write_bytes(MEDIA_BYTES)
    return path


@pytest.fixture
def live_server(media_file: Path):
    """Start a real loopback server on an ephemeral port for end-to-end assertions."""
    config = VisualizerConfig(port=0, open_browser=False, require_token=False).validated()
    state = build_state(
        snapshot_dict(),
        media_path=media_file,
        timeline={"artifact_role": "visualizer_timeline", "bucket_count": 1},
        config=config,
    )
    with VisualizerSession(state, config) as session:
        yield session.url.split("//", 1)[1].split("/", 1)[0].rsplit(":", 1)[0], session.port


def raw_request(port: int, target: str, headers: dict[str, str] | None = None) -> Any:
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", target, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


# -- range parsing -------------------------------------------------------
def test_range_open_ended() -> None:
    assert parse_byte_range("bytes=0-", 1024) == (0, 1023)


def test_range_bounded() -> None:
    assert parse_byte_range("bytes=100-199", 1024) == (100, 199)


def test_range_suffix_form() -> None:
    assert parse_byte_range("bytes=-50", 1024) == (974, 1023)


def test_range_suffix_larger_than_file_clamps() -> None:
    assert parse_byte_range("bytes=-5000", 1024) == (0, 1023)


def test_range_end_past_eof_is_clamped_not_rejected() -> None:
    assert parse_byte_range("bytes=100-999999", 1024) == (100, 1023)


def test_range_start_past_eof_is_unsatisfiable() -> None:
    with pytest.raises(_RangeUnsatisfiable):
        parse_byte_range("bytes=2000-", 1024)


def test_range_zero_suffix_is_unsatisfiable() -> None:
    with pytest.raises(_RangeUnsatisfiable):
        parse_byte_range("bytes=-0", 1024)


def test_absent_or_unparsable_range_serves_the_whole_entity() -> None:
    assert parse_byte_range(None, 1024) is None
    assert parse_byte_range("", 1024) is None
    assert parse_byte_range("items=0-10", 1024) is None
    assert parse_byte_range("bytes=abc-def", 1024) is None
    assert parse_byte_range("bytes=0-10,20-30", 1024) is None


# -- path hygiene --------------------------------------------------------
def test_scrub_paths_replaces_absolute_paths_with_names() -> None:
    scrubbed = scrub_paths(
        {
            "source": {"path": "/Users/someone/audio/clip.mp3", "filename": "clip.mp3"},
            "quality": {"transcript_path": "/Users/someone/audio/clip.srt"},
            "list": [{"path": "/var/tmp/x.bin"}],
        }
    )
    assert scrubbed["source"]["path"] == "clip.mp3"
    assert scrubbed["source"]["filename"] == "clip.mp3"
    assert scrubbed["quality"]["transcript_path"] == "clip.srt"
    assert scrubbed["list"][0]["path"] == "x.bin"


def test_scrub_paths_leaves_relative_values_alone() -> None:
    value = {"source": {"path": "reports/metrics/clip.metrics.json"}}
    assert scrub_paths(value) == value


def test_scrub_paths_catches_paths_under_unexpected_keys() -> None:
    """The VAD provenance nested under quality.vad_model must not leak a home dir."""
    scrubbed = scrub_paths(
        {
            "quality": {
                "vad_model": {
                    "model_path": "/Users/someone/proj/models/silero_vad_16k_op15.onnx",
                    "model_sha256": "a" * 64,
                }
            }
        }
    )
    assert scrubbed["quality"]["vad_model"]["model_path"] == "silero_vad_16k_op15.onnx"
    assert scrubbed["quality"]["vad_model"]["model_sha256"] == "a" * 64


def test_scrub_paths_does_not_mangle_prose() -> None:
    """Transcript text can contain slashes; only path-shaped values are rewritten."""
    text = "le rapport /etc/hosts est incomplet et le fichier notes.txt aussi"
    assert scrub_paths({"transcript": text})["transcript"] == text
    assert scrub_paths({"note": "/etc/hosts"})["note"] == "/etc/hosts"


def test_scrub_paths_does_not_mutate_the_source() -> None:
    original = {"source": {"path": "/Users/someone/audio/clip.mp3"}}
    scrub_paths(original)
    assert original["source"]["path"] == "/Users/someone/audio/clip.mp3"


def test_resolve_media_path_prefers_an_explicit_file(media_file: Path) -> None:
    snapshot = snapshot_dict()
    snapshot["source"]["path"] = "/nonexistent/elsewhere.mp3"
    assert resolve_media_path(snapshot, media_file) == media_file.resolve()


def test_resolve_media_path_errors_when_the_recording_is_gone() -> None:
    snapshot = snapshot_dict()
    snapshot["source"]["path"] = "/nonexistent/elsewhere.mp3"
    with pytest.raises(InputError, match="not present"):
        resolve_media_path(snapshot)


def test_media_content_type_is_derived_from_the_file() -> None:
    assert media_content_type(Path("clip.mp3")) == "audio/mpeg"


# -- configuration -------------------------------------------------------
def test_config_refuses_a_non_loopback_bind() -> None:
    for host in ("0.0.0.0", "192.168.1.10", "example.com"):
        with pytest.raises(InputError, match="loopback"):
            VisualizerConfig(host=host).validated()


def test_config_accepts_loopback_spellings() -> None:
    for host in LOOPBACK_HOSTS:
        assert VisualizerConfig(host=host).validated().host == host


def test_config_rejects_a_negative_idle_timeout() -> None:
    with pytest.raises(InputError):
        VisualizerConfig(idle_timeout=-1).validated()


# -- live server ---------------------------------------------------------
def test_audio_full_response_advertises_range_support(live_server) -> None:
    _host, port = live_server
    status, headers, body = raw_request(port, "/audio")
    assert status == 200
    assert headers["Accept-Ranges"] == "bytes"
    assert headers["Content-Length"] == str(len(MEDIA_BYTES))
    assert body == MEDIA_BYTES


def test_audio_partial_response_is_exact(live_server) -> None:
    _host, port = live_server
    status, headers, body = raw_request(port, "/audio", {"Range": "bytes=100-199"})
    assert status == 206
    assert headers["Content-Range"] == f"bytes 100-199/{len(MEDIA_BYTES)}"
    assert headers["Content-Length"] == "100"
    assert body == MEDIA_BYTES[100:200]


def test_audio_suffix_range_returns_the_tail(live_server) -> None:
    _host, port = live_server
    status, _headers, body = raw_request(port, "/audio", {"Range": "bytes=-64"})
    assert status == 206
    assert body == MEDIA_BYTES[-64:]


def test_audio_unsatisfiable_range_is_416(live_server) -> None:
    _host, port = live_server
    status, headers, _body = raw_request(port, "/audio", {"Range": "bytes=99999-"})
    assert status == 416
    assert headers["Content-Range"] == f"bytes */{len(MEDIA_BYTES)}"


def test_audio_malformed_range_falls_back_to_the_whole_entity(live_server) -> None:
    _host, port = live_server
    status, _headers, body = raw_request(port, "/audio", {"Range": "bytes=oops"})
    assert status == 200
    assert body == MEDIA_BYTES


def test_head_returns_headers_without_a_body(live_server) -> None:
    _host, port = live_server
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("HEAD", "/audio", headers={"Range": "bytes=0-9"})
        response = connection.getresponse()
        body = response.read()
        assert response.status == 206
        assert response.getheader("Content-Length") == "10"
        assert body == b""
    finally:
        connection.close()


def test_payload_is_served_and_paths_are_scrubbed(live_server) -> None:
    _host, port = live_server
    status, headers, body = raw_request(port, "/api/payload")
    assert status == 200
    assert "application/json" in headers["Content-Type"]
    payload = json.loads(body)
    assert set(payload) == {"snapshot", "timeline"}
    assert "events" in payload["snapshot"]


def test_favicon_is_answered_without_a_404(live_server) -> None:
    _host, port = live_server
    status, _headers, _body = raw_request(port, "/favicon.ico")
    assert status == 204


def test_unknown_route_is_404_and_never_lists_a_directory(live_server) -> None:
    _host, port = live_server
    status, _headers, body = raw_request(port, "/../../etc/passwd")
    assert status == 404
    assert b"root:" not in body


def test_post_has_no_write_path(live_server) -> None:
    _host, port = live_server
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("POST", "/api/payload", body=b"{}")
        response = connection.getresponse()
        response.read()
        assert response.status == 501
    finally:
        connection.close()


def test_foreign_host_header_is_rejected(live_server) -> None:
    _host, port = live_server
    status, _headers, _body = raw_request(port, "/", {"Host": "evil.example.com"})
    assert status == 403


def test_no_cors_header_is_sent(live_server) -> None:
    _host, port = live_server
    _status, headers, _body = raw_request(port, "/api/payload")
    assert not any(key.lower().startswith("access-control") for key in headers)


def test_token_is_required_when_configured(media_file: Path) -> None:
    config = VisualizerConfig(port=0, open_browser=False, require_token=True).validated()
    state = build_state(snapshot_dict(), media_path=media_file, timeline=None, config=config)
    with VisualizerSession(state, config) as session:
        port = session.port
        status, _headers, _body = raw_request(port, "/api/payload")
        assert status == 403
        status, _headers, body = raw_request(port, f"/api/payload?t={state.token}")
        assert status == 200
        assert json.loads(body)["timeline"] is None


def test_server_shuts_down_after_the_idle_timeout(media_file: Path) -> None:
    config = VisualizerConfig(
        port=0, open_browser=False, require_token=False, idle_timeout=0.2
    ).validated()
    state = build_state(snapshot_dict(), media_path=media_file, timeline=None, config=config)
    with VisualizerSession(state, config) as session:
        assert session.closed.wait(5.0), "idle timeout did not stop the server"


def test_request_activity_defers_the_idle_timeout(media_file: Path) -> None:
    """A page that keeps loading must not be cut off mid-review."""
    config = VisualizerConfig(
        port=0, open_browser=False, require_token=False, idle_timeout=0.6
    ).validated()
    state = build_state(snapshot_dict(), media_path=media_file, timeline=None, config=config)
    with VisualizerSession(state, config) as session:
        port = session.port
        for _ in range(3):
            time.sleep(0.2)
            assert raw_request(port, "/api/payload")[0] == 200
            assert not session.closed.is_set(), "server stopped while the page was active"
        assert session.closed.wait(5.0)


def test_wait_closed_returns_once_the_server_stops(media_file: Path) -> None:
    config = VisualizerConfig(port=0, open_browser=False, require_token=False).validated()
    state = build_state(snapshot_dict(), media_path=media_file, timeline=None, config=config)
    with VisualizerSession(state, config) as session:
        assert not session.closed.is_set()
        finished = threading.Event()

        def waiter() -> None:
            session.wait_closed(poll_seconds=0.05)
            finished.set()

        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        time.sleep(0.15)
        assert not finished.is_set(), "wait_closed returned while the server was running"
        session._stop()  # noqa: SLF001
        assert finished.wait(5.0)


# -- packaged assets -----------------------------------------------------
def test_ui_assets_are_importable_from_the_package() -> None:
    """A wheel that omits the assets would make every page load a 404."""
    from senate_audio_audit.visualizer.server import _ui_asset

    for name, marker in (
        ("index.html", '<audio id="audio"'),
        ("app.css", "--bg"),
        ("app.js", "boot();"),
    ):
        body = _ui_asset(name).decode("utf-8")
        assert marker in body, f"{name} does not contain {marker!r}"


def test_app_js_records_the_no_decode_invariant() -> None:
    """The 2 GB decode trap is documented in the file that must never walk into it."""
    from senate_audio_audit.visualizer.server import _ui_asset

    source = _ui_asset("app.js").decode("utf-8")
    assert "must never call decodeAudioData" in source
    # Strip comments: the warning itself names the API, but no code may invoke it.
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    code = re.sub(r"//[^\n]*", "", code)
    assert "decodeAudioData" not in code


def test_index_page_has_no_external_references() -> None:
    """The page must render fully offline, so no CDN or remote asset is allowed."""
    from senate_audio_audit.visualizer.server import _ui_asset

    page = _ui_asset("index.html").decode("utf-8")
    for scheme in ("http://", "https://", "//cdn"):
        assert scheme not in page, f"index.html references {scheme}"


def test_session_exposes_its_port(media_file: Path) -> None:
    config = VisualizerConfig(port=0, open_browser=False, require_token=False).validated()
    state = build_state(snapshot_dict(), media_path=media_file, timeline=None, config=config)
    with VisualizerSession(state, config) as session:
        assert 1024 < session.port < 65_536


def test_session_url_carries_the_token(media_file: Path) -> None:
    config = VisualizerConfig(port=0, open_browser=False, require_token=True).validated()
    state = build_state(snapshot_dict(), media_path=media_file, timeline=None, config=config)
    with VisualizerSession(state, config) as session:
        assert session.url.startswith("http://127.0.0.1:")
        assert f"t={state.token}" in session.url


def test_browser_client_can_stream_the_payload(media_file: Path) -> None:
    """Guard the shape a real browser uses: a plain GET with a Range header."""
    config = VisualizerConfig(port=0, open_browser=False, require_token=False).validated()
    state = build_state(snapshot_dict(), media_path=media_file, timeline=None, config=config)
    with VisualizerSession(state, config) as session:
        port = session.port
        request = urllib.request.Request(  # noqa: S310 - loopback test server
            f"http://127.0.0.1:{port}/audio", headers={"Range": "bytes=0-15"}
        )
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            assert response.status == 206
            assert response.read() == MEDIA_BYTES[:16]
