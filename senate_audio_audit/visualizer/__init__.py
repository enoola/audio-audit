"""Local, read-only visualizer for committed per-audio metrics.

The visualizer renders an existing ``*.metrics.json`` snapshot and a display-only
timeline sidecar. It performs no inference, changes no metric, and offers no way
to record a review decision: every other command in this package remains the only
way to produce or amend analysis output.
"""

from __future__ import annotations

from .timeline import (
    ARTIFACT_ROLE,
    TIMELINE_SCHEMA_VERSION,
    TimelineArtifact,
    TimelineConfig,
    activity_buckets,
    bucket_count,
    build_timeline,
    load_or_build_timeline,
    reduce_envelope,
    timeline_cache_key,
    timeline_path,
)

__all__ = [
    "ARTIFACT_ROLE",
    "TIMELINE_SCHEMA_VERSION",
    "TimelineArtifact",
    "TimelineConfig",
    "activity_buckets",
    "bucket_count",
    "build_timeline",
    "load_or_build_timeline",
    "reduce_envelope",
    "timeline_cache_key",
    "timeline_path",
]
