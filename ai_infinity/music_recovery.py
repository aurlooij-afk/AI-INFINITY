"""Side-effect-free adapters for boot-time media recovery."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

DEFAULT_MUSIC_QUERY = "cinematic ambient instrumental soundtrack no vocals"


def wrap_music_generator(
    original_music: Callable[..., Any],
    valid_file: Callable[[Any], bool],
) -> Callable[..., Path]:
    """Preserve the canonical query/provenance path while validating its output."""
    if not callable(original_music):
        raise TypeError("original music generator must be callable")
    if not callable(valid_file):
        raise TypeError("music output validator must be callable")

    def make_music_safe(
        outdir: str | Path,
        seconds: float,
        query: str = DEFAULT_MUSIC_QUERY,
    ) -> Path:
        result = original_music(outdir, seconds, query=query)
        if not valid_file(result):
            raise RuntimeError("canonical music generator produced no valid audio file")
        return Path(result)

    make_music_safe._aiinfinity_recovery = True
    return make_music_safe
