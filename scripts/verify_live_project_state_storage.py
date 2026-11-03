from __future__ import annotations

"""Verify a real B2 project-state round-trip and fresh-process restore.

This script uses a unique object key below the configured project-state key
prefix. It never overwrites the live project-state snapshot. Missing credentials
are reported as BLOCKED; configured storage that fails a real operation is FAIL.
"""

import argparse
import atexit
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict


def _emit(result: Dict[str, Any], output: Path | None) -> None:
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded + "\n", encoding="utf-8")
    print("PROJECT_STATE_STORAGE_RESULT=" + json.dumps(result, ensure_ascii=False, sort_keys=True))


def _missing_credentials() -> list[str]:
    aliases = (
        (("BACKBLAZE_B2_ENDPOINT", "B2_ENDPOINT"), "endpoint"),
        (("BACKBLAZE_B2_BUCKET", "B2_BUCKET"), "bucket"),
        (("BACKBLAZE_B2_KEY_ID", "B2_KEY_ID"), "key_id"),
        (("BACKBLAZE_B2_APPLICATION_KEY", "B2_APPLICATION_KEY"), "application_key"),
    )
    return [
        label
        for names, label in aliases
        if not any(os.getenv(name, "").strip() for name in names)
    ]


def _restore_in_clean_process(database: Path) -> Dict[str, Any]:
    child = r"""
import atexit, json, os, sqlite3, sys
from pathlib import Path
from ai_infinity import project_state_backend as backend

database = Path(os.environ["AI_INFINITY_STORAGE_VERIFY_DB"])
atexit.unregister(backend.snapshot_now)
state = backend.configure(database)
last_restore = state.get("last_restore") or {}
if state.get("active") is not True or state.get("verified") is not True:
    raise RuntimeError("fresh process could not verify the configured object-store backend")
if last_restore.get("restored") is not True:
    raise RuntimeError("fresh process did not restore the exact remote snapshot: " + str(last_restore.get("reason")))
with sqlite3.connect(database) as con:
    row = con.execute(
        "SELECT project_marker, payload_sha256 FROM storage_recovery_canary WHERE id=1"
    ).fetchone()
if not row or row[0] != os.environ["AI_INFINITY_STORAGE_VERIFY_MARKER"]:
    raise RuntimeError("restored project-state row did not match the canary marker")
expected = os.environ["AI_INFINITY_STORAGE_VERIFY_PAYLOAD_SHA256"]
if row[1] != expected:
    raise RuntimeError("restored project-state payload checksum did not match")
print(json.dumps({
    "status": "PASS",
    "restore": "restored",
    "project_marker": row[0],
    "payload_sha256": row[1],
    "snapshot_sha256": state.get("last_sha256"),
    "truthful": True,
}, sort_keys=True))
"""
    env = os.environ.copy()
    env["AI_INFINITY_STORAGE_VERIFY_DB"] = str(database)
    completed = subprocess.run(
        [sys.executable, "-c", child],
        capture_output=True,
        text=True,
        timeout=240,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "fresh-process restore failed")[-1600:]
        raise RuntimeError("fresh-process restore failed: " + " ".join(message.split()))
    rows = [line for line in (completed.stdout or "").splitlines() if line.strip().startswith("{")]
    if not rows:
        raise RuntimeError("fresh-process restore returned no structured evidence")
    result = json.loads(rows[-1])
    if result.get("status") != "PASS" or result.get("restore") != "restored":
        raise RuntimeError("fresh-process restore did not produce a PASS evidence record")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    missing = _missing_credentials()
    if missing:
        _emit({
            "status": "BLOCKED",
            "reason": "required B2 credentials are not configured in this execution environment",
            "missing_configuration": missing,
            "round_trip": "not_attempted",
            "fresh_process_restore": "not_attempted",
            "truthful": True,
        }, args.output)
        return 3

    os.environ["AI_INFINITY_PERSISTENCE_MODE"] = "durable"
    os.environ["AI_INFINITY_PROJECT_STATE_DURABLE"] = "true"
    os.environ["AI_INFINITY_PROJECT_STATE_BACKEND"] = "backblaze_b2"
    os.environ.setdefault("B2_REGION", "")

    from ai_infinity import project_state_backend as backend

    # The suffix remains inside the operator's project-state key namespace while
    # isolating this verification from the production snapshot object.
    production_key_prefix = backend.object_key()
    verification_key = production_key_prefix + ".verification-" + uuid.uuid4().hex
    os.environ["AI_INFINITY_PROJECT_STATE_OBJECT_KEY"] = verification_key

    marker = "ai-infinity-storage-verification-" + uuid.uuid4().hex
    payload = ("fresh-process-restore:" + marker).encode("utf-8")
    payload_sha256 = hashlib.sha256(payload).hexdigest()

    with tempfile.TemporaryDirectory(prefix="ai-infinity-b2-verify-") as raw_dir:
        root = Path(raw_dir)
        source_db = root / "source.db"
        restored_db = root / "clean-runtime" / "restored.db"
        restored_db.parent.mkdir(parents=True, exist_ok=True)

        # Avoid an atexit checkpoint after the verification object has been
        # removed; this test owns its own explicit snapshot lifecycle.
        atexit.unregister(backend.snapshot_now)

        try:
            state = backend.configure(source_db)
            if state.get("active") is not True or state.get("verified") is not True:
                raise RuntimeError("B2 canary PUT/HEAD/GET/SHA did not verify: " + str(state.get("last_error") or state.get("last_restore", {}).get("reason")))

            with sqlite3.connect(source_db) as con:
                con.execute(
                    "CREATE TABLE storage_recovery_canary "
                    "(id INTEGER PRIMARY KEY, project_marker TEXT NOT NULL, payload_sha256 TEXT NOT NULL)"
                )
                con.execute(
                    "INSERT INTO storage_recovery_canary(id, project_marker, payload_sha256) VALUES(1, ?, ?)",
                    (marker, payload_sha256),
                )

            snapshot = backend.snapshot_now()
            if snapshot.get("status") != "verified":
                raise RuntimeError("B2 project-state snapshot PUT/HEAD/GET/SHA failed: " + str(snapshot.get("error") or snapshot.get("status")))

            # Independently compare the object-store body bytes, ContentLength,
            # and SHA-256 metadata to the exact snapshot produced by the adapter.
            client = backend._client()
            head = client.head_object(Bucket=backend._cfg()["bucket"], Key=verification_key)
            body = client.get_object(Bucket=backend._cfg()["bucket"], Key=verification_key).get("Body")
            if body is None:
                raise RuntimeError("B2 snapshot GET returned no body")
            try:
                remote_bytes = body.read()
            finally:
                close = getattr(body, "close", None)
                if callable(close):
                    close()
            remote_sha256 = hashlib.sha256(remote_bytes).hexdigest()
            expected_sha256 = str(snapshot.get("sha256") or "")
            if (
                len(remote_bytes) != int(head.get("ContentLength") or -1)
                or remote_sha256 != expected_sha256
                or str((head.get("Metadata") or {}).get("ai-infinity-sha256") or "") != expected_sha256
            ):
                raise RuntimeError("independent B2 snapshot read-back did not match size and SHA-256")

            os.environ["AI_INFINITY_STORAGE_VERIFY_MARKER"] = marker
            os.environ["AI_INFINITY_STORAGE_VERIFY_PAYLOAD_SHA256"] = payload_sha256
            fresh_restore = _restore_in_clean_process(restored_db)

            result = {
                "status": "PASS",
                "backend": "backblaze_b2",
                "canary": "PUT_HEAD_GET_SHA_PASS",
                "snapshot": "PUT_HEAD_GET_SHA_PASS",
                "fresh_process_restore": "PASS",
                "object_key": verification_key,
                "snapshot_size_bytes": len(remote_bytes),
                "snapshot_sha256": remote_sha256,
                "restored_project_marker": fresh_restore["project_marker"],
                "restored_payload_sha256": fresh_restore["payload_sha256"],
                "production_snapshot_overwritten": False,
                "truthful": True,
            }
            _emit(result, args.output)
            return 0
        except Exception as exc:
            result = {
                "status": "FAIL",
                "backend": "backblaze_b2",
                "error_type": type(exc).__name__,
                "error": backend._safe_error_text(exc, 700),
                "object_key": verification_key,
                "production_snapshot_overwritten": False,
                "truthful": True,
            }
            _emit(result, args.output)
            return 2
        finally:
            # The key is unique to this run and never aliases the live snapshot.
            try:
                backend._client().delete_object(
                    Bucket=backend._cfg()["bucket"],
                    Key=verification_key,
                )
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
