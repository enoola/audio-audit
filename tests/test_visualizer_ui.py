"""Static guards for the visualizer's UI assets.

These tests do not render anything. They exist because two layout bugs shipped
past the Python suite and were only caught by looking at the page in a browser:

1. ``canvas { width: 100% }`` with no explicit height made each lane derive its
   rendered height from the canvas's intrinsic aspect ratio. ``prep()`` rewrites
   the backing store on every redraw, so the lanes drifted to the wrong heights
   (envelope 86 -> 111 px, activity 42 -> 54, events 58 -> 75).
2. The playhead is a ``<canvas>`` too, so the shared ``canvas`` rule painted it
   with an opaque ``#0b0e13`` background. Sitting at ``z-index: 3`` over the lane
   stack, it hid all three lanes and their labels completely. Every lane was
   drawn correctly the whole time and simply invisible.

Both are invisible to unit tests and obvious on sight, so they are pinned here.
"""

from __future__ import annotations

import re

import pytest

from senate_audio_audit.visualizer.server import _ui_asset

LANE_CANVASES = ("cEnv", "cAct", "cEvents", "cRuler")


@pytest.fixture(scope="module")
def css() -> str:
    return _ui_asset("app.css").decode("utf-8")


@pytest.fixture(scope="module")
def js() -> str:
    return _ui_asset("app.js").decode("utf-8")


@pytest.fixture(scope="module")
def html() -> str:
    return _ui_asset("index.html").decode("utf-8")


def _rule_for(css: str, selector: str) -> str | None:
    """Return the declarations of the last rule whose selector list contains `selector`."""
    pattern = re.compile(rf"(^|}})\s*{re.escape(selector)}\s*\{{([^}}]*)\}}", re.MULTILINE)
    found = pattern.findall(css)
    return found[-1][1] if found else None


def _code_only(source: str) -> str:
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL))


# -- the two shipped layout bugs -----------------------------------------
@pytest.mark.parametrize("canvas_id", LANE_CANVASES)
def test_every_lane_canvas_has_an_explicit_height(css: str, canvas_id: str) -> None:
    """Without a fixed height a canvas derives one from its own aspect ratio."""
    rule = _rule_for(css, f"#{canvas_id}")
    assert rule is not None, f"#{canvas_id} has no CSS rule"
    assert re.search(r"\bheight\s*:", rule), f"#{canvas_id} has no explicit height"


def test_playhead_canvas_is_transparent(css: str) -> None:
    """Regression: an opaque playhead canvas covers every lane it overlays."""
    rule = _rule_for(css, ".playhead")
    assert rule is not None, ".playhead has no CSS rule"
    assert "background" in rule, ".playhead must override the shared canvas background"
    assert re.search(r"background\s*:\s*transparent", rule), (
        ".playhead must be transparent or it hides the lanes beneath it"
    )
    assert re.search(r"pointer-events\s*:\s*none", rule), (
        ".playhead must not swallow clicks meant for the lanes"
    )


def test_playhead_height_is_derived_not_hard_coded(css: str, js: str) -> None:
    """The playhead spans the lane stack, so a hard-coded height desynchronises it."""
    rule = _rule_for(css, ".playhead")
    assert rule is not None
    assert not re.search(r"\bheight\s*:", rule), (
        "the playhead height must be measured from the lanes in JS, not fixed in CSS"
    )
    assert "canvas.style.height" in js, "drawPlayhead must size the playhead from the lanes"


# -- asset integrity -----------------------------------------------------
def test_ui_assets_are_importable_from_the_package() -> None:
    """A wheel that omits the assets would make every page load a 404."""
    for name, marker in (
        ("index.html", '<audio id="audio"'),
        ("app.css", "--bg"),
        ("app.js", "boot();"),
    ):
        assert marker in _ui_asset(name).decode("utf-8"), f"{name} lacks {marker!r}"


def test_app_js_records_the_no_decode_invariant(js: str) -> None:
    """The 2 GB decode trap is documented in the file that must never walk into it."""
    assert "must never call decodeAudioData" in js
    assert "decodeAudioData" not in _code_only(js)


def test_index_page_has_no_external_references(html: str) -> None:
    """The page must render fully offline, so no CDN or remote asset is allowed."""
    for scheme in ("http://", "https://", "//cdn"):
        assert scheme not in html, f"index.html references {scheme}"


def test_every_element_the_script_touches_exists_in_the_page(html: str, js: str) -> None:
    """A renamed id would throw at runtime and leave the page half-rendered."""
    declared = set(re.findall(r'id="([^"]+)"', html))
    referenced = set(re.findall(r'\$\("([^"]+)"\)', js))
    assert referenced, "expected the script to reference element ids"
    assert not referenced - declared, f"referenced but absent: {sorted(referenced - declared)}"


# -- required behaviour --------------------------------------------------
def test_playback_stops_at_the_event_end(js: str) -> None:
    """The core loop: play only the event span, then pause."""
    assert "state.stopAt" in js
    assert "event ended · paused" in js
    assert re.search(r"seconds\s*>=\s*state\.stopAt", js), (
        "the timeupdate guard must pause playback at the event end"
    )


def test_tolerance_is_applied_to_the_played_span(js: str) -> None:
    """Event boundaries are 30 ms-frame estimates, so a tolerance must exist."""
    assert 'id="tol"' in _ui_asset("index.html").decode("utf-8")
    assert "event.start_ms / 1000 - state.tolerance / 1000" in js
    assert "(event.end_ms + state.tolerance) / 1000" in js


def test_short_events_keep_a_visible_minimum_bar(js: str) -> None:
    """A 5 s event is ~1.35 px at 1440 px across an 89-minute timeline."""
    assert re.search(r"Math\.max\(2,\s*right - left\)", js), (
        "event bars need a 2 px floor or short events become invisible"
    )


@pytest.mark.parametrize("key", [" ", "ArrowLeft", "ArrowRight", "j", "k", "Enter", "Escape", "0"])
def test_documented_keyboard_shortcuts_are_bound(js: str, key: str) -> None:
    assert f'case "{key}"' in js, f"keyboard shortcut {key!r} is not bound"


def test_page_labels_the_activity_lane_provenance(js: str) -> None:
    """A transcript-fallback lane must never be presented as VAD-detected speech."""
    assert "activity.method" in js
    assert "derived from transcript cue intervals" in js
    assert "agrees_with_snapshot === false" in js


def test_vividness_is_never_rendered_as_a_bare_score(js: str) -> None:
    """The rubric is uncalibrated, so the status must always accompany the number."""
    assert "`${v.score}/10`" in js
    assert "uncalibrated" in js
    assert "provisional_rubric" in js


def test_zoom_anchor_is_measured_not_assumed(js: str) -> None:
    """A hard-coded container padding would skew the zoom anchor on restyle."""
    assert '$("cEnv").getBoundingClientRect()' in js
    assert not re.search(r"rect\.left - 9\.6", js), "zoom anchor must not assume padding"
