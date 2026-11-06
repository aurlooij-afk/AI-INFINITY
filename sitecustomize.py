"""AI Infinity boot-time production recovery.

This module is loaded by Python automatically in the production image. It adds
bounded local recovery for non-essential SFX and media assembly. Music remains
on the canonical provenance-aware generator so query selection, source
attribution, and its truthful fallback manifest are preserved. It never
fabricates a finished artifact; the existing verification path remains the
source of truth.
"""
from __future__ import annotations

import importlib
import subprocess
from pathlib import Path


def _valid(path: object, minimum: int = 1000) -> bool:
    try:
        p = Path(path)
        return p.is_file() and p.stat().st_size >= minimum
    except Exception:
        return False


def _apply() -> None:
    try:
        studio = importlib.import_module("studio_ultimate")
    except Exception:
        return

    original_music = getattr(studio, "make_music", None)
    original_sfx = getattr(studio, "make_sfx", None)
    original_concat = getattr(studio, "concat_segments", None)

    if callable(original_music) and not getattr(original_music, "_aiinfinity_recovery", False):
        try:
            from ai_infinity.music_recovery import wrap_music_generator
            studio.make_music = wrap_music_generator(original_music, _valid)
        except Exception:
            # The canonical generator remains usable without the optional guard.
            pass

    if callable(original_sfx) and not getattr(original_sfx, "_aiinfinity_recovery", False):
        def make_sfx_safe(outdir, duration, index):
            out = Path(outdir) / f"transition_{index:02d}.wav"
            d = max(0.15, min(1.1, float(duration) * 0.15))
            try:
                ffmpeg = getattr(studio, "ffmpeg")
                ffmpeg(
                    "-f", "lavfi", "-i",
                    f"anoisesrc=color=white:amplitude=0.06:duration={d}",
                    "-af", "highpass=f=900,lowpass=f=6500,afade=t=in:st=0:d=0.03,"
                    + "afade=t=out:st=" + str(max(0.03, d-0.08)) + ":d=0.08",
                    "-c:a", "pcm_s16le", "-t", d, out, timeout=45,
                )
                if _valid(out):
                    return out
            except Exception:
                pass
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-threads", "1", "-f", "lavfi", "-i",
                 f"anullsrc=r=24000:cl=stereo:d={d}",
                 "-c:a", "pcm_s16le", "-t", str(d), str(out)],
                check=True, timeout=30,
            )
            if not _valid(out):
                raise RuntimeError("local SFX fallback produced no valid audio")
            return out
        make_sfx_safe._aiinfinity_recovery = True
        studio.make_sfx = make_sfx_safe

    if callable(original_concat) and not getattr(original_concat, "_aiinfinity_recovery", False):
        def concat_safe(paths, out):
            try:
                result = original_concat(paths, out)
                if _valid(result, 10000):
                    return result
            except Exception:
                pass
            # Re-encode fallback handles small differences between scene
            # streams that make concat-copy reject an otherwise valid project.
            manifest = Path(out).with_suffix(".recovery.txt")
            manifest.write_text(
                "".join(
                    "file '" + str(Path(p)).replace("'", "'\\''") + "'\n"
                    for p in paths if Path(p).is_file()
                ),
                encoding="utf-8",
            )
            ffmpeg = getattr(studio, "ffmpeg")
            ffmpeg(
                "-f", "concat", "-safe", "0", "-i", manifest,
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "25",
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out,
                timeout=900,
            )
            if not _valid(out, 10000):
                raise RuntimeError("media assembly produced no valid master video")
            return out
        concat_safe._aiinfinity_recovery = True
        studio.concat_segments = concat_safe


_apply()


# TARGET-2050.3901 — install the independent Reality Kernel after the
# lightweight boot-time media fallbacks above have patched studio_ultimate.
try:
    import reality_first_3901 as _ai_infinity_reality_first
    _ai_infinity_reality_first.install()
except Exception:
    # Startup must remain fail-open. The Creator Studio still runs and its own
    # truthful status exposes whether the independent proof layer is active.
    pass
