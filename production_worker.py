from __future__ import annotations

"""Dedicated AI Infinity Creator Studio worker process."""

import os
import signal
import time
import traceback

os.environ.setdefault("AI_INFINITY_EXTERNAL_WORKER", "true")

import studio_ultimate  # noqa: E402

STOP = False


def _stop(*_args):
    global STOP
    STOP = True
    try:
        studio_ultimate.STOP.set()
    except Exception:
        pass


signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)


def run() -> int:
    while not STOP:
        try:
            # Reuse the Creator Studio's durable queue, atomic claim, lease
            # recovery, checkpoints, rendering and QC instead of duplicating
            # production logic in a second engine.
            studio_ultimate.worker_loop(None)
        except Exception:
            traceback.print_exc()
            if STOP:
                break
            time.sleep(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
