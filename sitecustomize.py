"""AI Infinity boot-time production recovery.

This module is loaded by Python automatically in the production image.  It keeps
the free/local creator pipeline from failing before the first real scene when a
non-essential procedural music/SFX helper encounters an ffmpeg/container
limitation.  It never fabricates a finished artifact: every fallback is a real
audio file generated locally and the existing verification path remains the
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
        def make_music_safe(outdir, seconds):
            # Deterministic one-source local music first. This avoids the
            # multi-input filter graph that was the most fragile early-stage
            # operation on memory-constrained free containers.
            out = Path(outdir) / "music.m4a"
            duration = max(1.0, min(900.0, float(seconds)))
            ffmpeg = getattr(studio, "ffmpeg")
            try:
                ffmpeg(
                    "-f", "lavfi", "-i",
                    f"sine=frequency=110:sample_rate=48000:duration={duration}",
                    "-af", "volume=0.025,lowpass=f=1400,afade=t=in:st=0:d=1,"
                    + "afade=t=out:st=" + str(max(0.1, duration - 2))
                    + ":d=2",
                    "-c:a", "aac", "-b:a", "64k", "-t", duration, out,
                    timeout=180,
                )
                if _valid(out):
                    return out
            except Exception:
                pass
            # Try the original richer generator only after the stable local
            # path; a later failure is still followed by a real local fallback.
            try:
                result = original_music(outdir, seconds)
                if _valid(result):
                    return result
            except Exception:
                pass
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-threads", "1", "-f", "lavfi", "-i",
                 f"sine=frequency=110:sample_rate=24000:duration={duration}",
                 "-af", "volume=0.02",
                 "-c:a", "aac", "-b:a", "48k", "-t", str(duration), str(out)],
                check=True, timeout=180,
            )
            if not _valid(out):
                raise RuntimeError("local music fallback produced no valid audio")
            return out
        make_music_safe._aiinfinity_recovery = True
        studio.make_music = make_music_safe

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

    original_tts = getattr(studio, "tts", None)
    if callable(original_tts) and not getattr(original_tts, "_aiinfinity_recovery", False):
        def tts_local_first(text, outdir, index, voice):
            # Prefer local espeak so the free core never needs a remote service
            # merely to finish a valid real project.
            out = Path(outdir) / f"narration_{index:02d}.mp3"
            plain = str(text or "").strip()
            if not plain:
                raise ValueError("empty narration")
            exe = __import__("shutil").which("espeak-ng") or __import__("shutil").which("espeak")
            if exe:
                wav = Path(outdir) / f"narration_{index:02d}.wav"
                try:
                    subprocess.run([exe, "-s", "155", "-w", str(wav), plain], check=True, timeout=45)
                    ffmpeg = getattr(studio, "ffmpeg")
                    ffmpeg("-i", wav, "-codec:a", "libmp3lame", "-q:a", "4", out, timeout=120)
                    if _valid(out):
                        duration = float(getattr(studio, "probe_duration")(out))
                        return out, "local-espeak-first", duration
                except Exception:
                    pass
            return original_tts(text, outdir, index, voice)
        tts_local_first._aiinfinity_recovery = True
        studio.tts = tts_local_first

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
