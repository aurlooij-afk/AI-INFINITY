AI Infinity TARGET-2050.3605 — WEBSITE-FIRST PRODUCTION FACTORY

Base preserved: TARGET-2050.3604 cumulative main.py.

Implemented:
- Canonical root / and /genius use the full reference-aligned AI Infinity workspace UI.
- Home flow includes the 10-stage Create/Automate/Earn/Grow concept from the supplied reference.
- Website-first operation; Android companion is no longer required for server-side work.
- One-command launcher: POST /infinity/3605/launch.
- Fixed-price discovery/preparation remains integrated through the existing 3601/3603 engines.
- Local production factory: storyboard PNG frames + eSpeak narration + FFmpeg soundtrack + automatic MP4 editing.
- New media endpoint: POST /infinity/3605/media/factory.
- Downloadable MP4, narration, script and storyboard routes.
- Existing missions, chat, work, organization, economy, wallet, memory, activity and connection routes preserved.
- Docker now installs ffmpeg, espeak and DejaVu fonts.

Verification:
- main.py compiles.
- Existing regression suite: 20/20 passed.
- New 3605 suite: 3/3 passed.
- Combined: 23/23 passed.
- Real TestClient smoke render produced a downloadable MP4 (HTTP 200, ~658 KB in test run).

External account actions still use real provider authorization already present in the project; no fake external completion is emitted.
