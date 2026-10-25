from __future__ import annotations

"""Authoritative durable SQLite state snapshot backend.

The Creator Studio uses SQLite for its rich transactional state model. On
ephemeral hosts, the database file itself is not durable. This adapter keeps the
SQLite transaction model local while checkpointing a consistent database
snapshot to a real S3-compatible object store (Backblaze B2 by default). On a
fresh instance it restores the last verified snapshot before schema creation.

Fail-closed rules:
- missing credentials never imply durability;
- failed upload/verification never implies durability;
- restore failures never destroy an existing local database;
- secrets never appear in status responses;
- object-store state is verified by checksum/size before being considered saved.
"""

import atexit
import hashlib
import os
import re
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

try:
    import boto3
    from botocore.config import Config as BotoConfig
except Exception:  # pragma: no cover
    boto3 = None
    BotoConfig = None


_LOCK = threading.RLock()
_SNAPSHOT_LOCK = threading.Lock()
_DB_PATH: Optional[Path] = None
_SYNC_TIMER: Optional[threading.Timer] = None
_CHANGE_GENERATION = 0
_SYNC_DIRTY = False
_SYNC_FAILURES = 0
_MAX_SNAPSHOT_RETRIES = 3
_MAX_CANARY_VERIFY_ATTEMPTS = 3

_STATUS: Dict[str, Any] = {
    "configured": False,
    "active": False,
    "verified": False,
    "snapshot_verified": False,
    "backend": None,
    "last_sync_at": None,
    "last_sha256": None,
    "last_error": None,
    "last_restore": {
        "attempted": False,
        "restored": False,
        "status": "not_attempted",
        "reason": "startup_restore_not_attempted",
        "size_bytes": None,
        "sha256": None,
        "completed_at": None,
        "truthful": True,
    },
}

TRUE = {"1", "true", "yes", "on"}


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in TRUE


def _cfg() -> Dict[str, str]:
    # Use the same variable precedence as ai3704_storage_fabric. Deployments
    # commonly configure BACKBLAZE_B2_* for artifact storage and the legacy
    # B2_* aliases for older integrations; using a different key pair here can
    # make media durability work while project-state snapshots fail (for
    # example, HeadObject 403 from a restricted legacy key).
    endpoint = (
        os.getenv("BACKBLAZE_B2_ENDPOINT", "").strip()
        or os.getenv("B2_ENDPOINT", "").strip()
    )
    region = (
        os.getenv("BACKBLAZE_B2_REGION", "").strip()
        or os.getenv("B2_REGION", "").strip()
    )
    if not endpoint and region:
        endpoint = f"https://s3.{region}.backblazeb2.com"
    # B2 endpoints encode the bucket's S3 signing region. When an operator has
    # configured an endpoint but omitted both region aliases, the historical
    # us-west-004 fallback can sign requests for the wrong region (B2 may reject
    # HEAD/GET with 403 even though credentials and bucket are otherwise valid).
    # Explicit region configuration remains authoritative.
    if not region and endpoint:
        try:
            hostname = (urlsplit(endpoint).hostname or "").lower()
        except ValueError:
            hostname = ""
        match = re.fullmatch(r"s3\.([a-z0-9-]+)\.backblazeb2\.com", hostname)
        if match:
            region = match.group(1)
    return {
        "endpoint": endpoint,
        "bucket": (
            os.getenv("BACKBLAZE_B2_BUCKET", "").strip()
            or os.getenv("B2_BUCKET", "").strip()
        ),
        "access": (
            os.getenv("BACKBLAZE_B2_KEY_ID", "").strip()
            or os.getenv("B2_KEY_ID", "").strip()
        ),
        "secret": (
            os.getenv("BACKBLAZE_B2_APPLICATION_KEY", "").strip()
            or os.getenv("B2_APPLICATION_KEY", "").strip()
        ),
        "region": region or "us-west-004",
    }


def configured() -> bool:
    cfg = _cfg()
    return bool(
        boto3 is not None
        and BotoConfig is not None
        and cfg["endpoint"]
        and cfg["bucket"]
        and cfg["access"]
        and cfg["secret"]
    )


def contract_enabled() -> bool:
    return (
        os.getenv("AI_INFINITY_PERSISTENCE_MODE", "volatile").strip().lower() == "durable"
        and _flag("AI_INFINITY_PROJECT_STATE_DURABLE")
        and os.getenv("AI_INFINITY_PROJECT_STATE_BACKEND", "backblaze_b2").strip().lower()
        in {"backblaze_b2", "b2"}
    )


def ready() -> bool:
    """Return true only when the backend and current project-state snapshot are verified."""
    with _LOCK:
        return bool(
            contract_enabled()
            and configured()
            and _STATUS.get("active")
            and _STATUS.get("verified")
            and _STATUS.get("snapshot_verified")
            and _STATUS.get("last_sha256")
            and _STATUS.get("last_sync_at")
        )


def object_key() -> str:
    return (
        os.getenv(
            "AI_INFINITY_PROJECT_STATE_OBJECT_KEY",
            "ai-infinity/project-state/ai_infinity.db.snapshot",
        ).strip()
        or "ai-infinity/project-state/ai_infinity.db.snapshot"
    )


def _client():
    if not configured():
        raise RuntimeError("Backblaze B2 project-state backend is not configured")
    cfg = _cfg()
    return boto3.client(
        "s3",
        endpoint_url=cfg["endpoint"],
        region_name=cfg["region"],
        aws_access_key_id=cfg["access"],
        aws_secret_access_key=cfg["secret"],
        config=BotoConfig(
            signature_version="s3v4",
            connect_timeout=8,
            read_timeout=15,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def _head(key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    h = _client().head_object(Bucket=_cfg()["bucket"], Key=key or object_key())
    metadata = h.get("Metadata") or {}
    return {
        "size_bytes": int(h.get("ContentLength") or 0),
        "sha256": str(metadata.get("ai-infinity-sha256") or ""),
        "snapshot_at": str(metadata.get("ai-infinity-snapshot-at") or ""),
    }


def _verify_roundtrip() -> bool:
    """Prove credentials support a real write/read-back using an isolated canary key."""
    cfg = _cfg()
    # Keep the default canary under the configured snapshot key prefix. B2
    # application keys may be restricted to a name prefix that covers the
    # snapshot object but not a sibling ".canary" path. Appending the canary
    # suffix lets the startup read/write probe exercise the same grant without
    # changing the durable snapshot object itself. Operators can still override
    # the canary path explicitly when their key policy requires a separate prefix.
    default_canary_key = f"{object_key()}.canary"
    base_key = (
        os.getenv(
            "AI_INFINITY_PROJECT_STATE_CANARY_KEY",
            default_canary_key,
        ).strip()
        or default_canary_key
    )
    # Never overwrite or delete an operator-managed canary object.
    key = f"{base_key}.{uuid.uuid4().hex}"
    payload = b"ai-infinity-project-state-canary-v1\n"
    digest = hashlib.sha256(payload).hexdigest()
    client = _client()
    try:
        client.put_object(
            Bucket=cfg["bucket"],
            Key=key,
            Body=payload,
            ContentType="text/plain",
            Metadata={"ai-infinity-canary": "v1", "ai-infinity-sha256": digest},
        )
        head = client.head_object(Bucket=cfg["bucket"], Key=key)
        body = client.get_object(Bucket=cfg["bucket"], Key=key).get("Body")
        if body is None:
            return False
        try:
            actual = body.read()
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
        metadata = head.get("Metadata") or {}
        return bool(
            int(head.get("ContentLength") or -1) == len(payload)
            and actual == payload
            and hashlib.sha256(actual).hexdigest() == digest
            and str(metadata.get("ai-infinity-sha256") or "") == digest
        )
    finally:
        try:
            client.delete_object(Bucket=cfg["bucket"], Key=key)
        except Exception:
            pass



def _safe_error_text(value: Any, limit: int = 180) -> str:
    """Keep diagnostics actionable without exposing credentials or signed URLs."""
    text = str(value or "")
    text = re.sub(r"(?i)https?://[^\s\"']+", "[redacted-url]", text)
    text = re.sub(
        r"(?i)\b(authorization|x-amz-security-token|access[_-]?key|secret[_-]?key|application[_-]?key|api[_-]?key|token)\b\s*[:=]\s*[^\s,;]+",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(r"(?i)\bAKIA[0-9A-Z]{16}\b", "[redacted-access-key]", text)
    return " ".join(text.split())[:limit]


def _describe_storage_error(exc: BaseException) -> str:
    """Return provider error code, HTTP status and operation with secrets scrubbed."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error") if isinstance(response.get("Error"), dict) else {}
        metadata = response.get("ResponseMetadata") if isinstance(response.get("ResponseMetadata"), dict) else {}
        code = re.sub(r"[^A-Za-z0-9_.-]", "", str(error.get("Code") or ""))[:80]
        operation = re.sub(r"[^A-Za-z0-9_.-]", "", str(getattr(exc, "operation_name", "") or ""))[:80]
        try:
            http_status = int(metadata.get("HTTPStatusCode") or 0)
        except (TypeError, ValueError):
            http_status = 0
        message = _safe_error_text(error.get("Message") or "")
        details = []
        if code:
            details.append("code=" + code)
        if http_status:
            details.append("http_status=" + str(http_status))
        if operation:
            details.append("operation=" + operation)
        if message:
            details.append("message=" + message)
        return type(exc).__name__ + (": " + " ".join(details) if details else "")
    return type(exc).__name__ + ": " + _safe_error_text(exc, 220)


def configure(db_path: Path | str) -> Dict[str, Any]:
    """Attach the backend and restore remote state before local schema init."""
    global _DB_PATH, _SYNC_FAILURES
    path = Path(db_path).resolve()
    with _LOCK:
        _SYNC_FAILURES = 0
    path.parent.mkdir(parents=True, exist_ok=True)

    with _LOCK:
        _DB_PATH = path
        _STATUS.update(
            {
                "configured": configured(),
                "active": bool(contract_enabled() and configured()),
                "verified": False,
                "snapshot_verified": False,
                "backend": "backblaze_b2" if contract_enabled() else None,
                "last_sync_at": None,
                "last_sha256": None,
                "last_error": None,
                "last_restore": {
                    "attempted": False,
                    "restored": False,
                    "status": "not_attempted",
                    "reason": "backend_not_active",
                    "size_bytes": None,
                    "sha256": None,
                    "completed_at": None,
                    "truthful": True,
                },
            }
        )

    if not _STATUS["active"]:
        return status()

    # A transient startup network/5xx failure must not permanently disable
    # durable state for the lifetime of this process. Keep the backend fail-closed
    # while retrying its real PUT/HEAD/GET canary a small, bounded number of times.
    # Once verified, restore the last remote snapshot before any app schema init.
    last_error: Optional[str] = None
    verified = False
    for attempt in range(_MAX_CANARY_VERIFY_ATTEMPTS):
        try:
            verified = bool(_verify_roundtrip())
            if verified:
                break
            last_error = "B2 canary verification failed"
        except Exception as exc:
            last_error = _describe_storage_error(exc)
        if attempt + 1 < _MAX_CANARY_VERIFY_ATTEMPTS:
            time.sleep(float(min(2 ** attempt, 4)))

    with _LOCK:
        _STATUS["verified"] = verified
        _STATUS["active"] = verified
        if not verified:
            _STATUS["last_error"] = last_error or "B2 canary verification failed"

    if not verified:
        # Do not claim persistence or restore from an unverified backend.
        return status()

    restore_result = restore_on_startup()
    restored = bool(restore_result.get("restored"))
    with _LOCK:
        _STATUS["last_restore"] = {
            "attempted": True,
            "restored": restored,
            "status": "restored" if restored else "not_restored",
            "reason": None if restored else str(restore_result.get("reason") or "not_restored")[:100],
            "size_bytes": int(restore_result.get("size_bytes") or 0) if restored else None,
            "sha256": str(restore_result.get("sha256") or "") if restored else None,
            "completed_at": time.time(),
            "truthful": True,
        }
    return status()


def restore_on_startup() -> Dict[str, Any]:
    global _CHANGE_GENERATION, _SYNC_DIRTY
    with _LOCK:
        path = _DB_PATH
        active = bool(_STATUS.get("active") and _STATUS.get("verified"))
    if path is None or not active:
        return {"restored": False, "reason": "backend_not_active", "truthful": True}

    # A nonempty local database is authoritative; it is not proof of a current
    # remote recovery point, so leave snapshot_verified false until synced.
    if path.exists() and path.stat().st_size > 0:
        return {"restored": False, "reason": "local_database_present", "truthful": True}

    tmp_path: Optional[Path] = None
    body = None
    try:
        head = _head()
        if not head or int(head.get("size_bytes") or 0) <= 0:
            return {"restored": False, "reason": "no_remote_snapshot", "truthful": True}
        expected = str(head.get("sha256") or "")
        if len(expected) != 64:
            raise RuntimeError("remote project-state snapshot has no trustworthy SHA-256 metadata")

        response = _client().get_object(Bucket=_cfg()["bucket"], Key=object_key())
        body = response.get("Body")
        if body is None:
            raise RuntimeError("remote project-state snapshot download returned no body")
        digest = hashlib.sha256()
        size = 0
        with tempfile.NamedTemporaryFile(
            prefix="ai-infinity-state-", suffix=".db", dir=str(path.parent), delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                tmp.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        actual_digest = digest.hexdigest()
        if size != int(head.get("size_bytes") or -1) or actual_digest != expected:
            raise RuntimeError("remote project-state snapshot size or SHA-256 verification failed")

        check_db = sqlite3.connect(str(tmp_path), timeout=10)
        try:
            check = check_db.execute("PRAGMA quick_check").fetchone()
        finally:
            check_db.close()
        if not check or str(check[0]).lower() != "ok":
            raise RuntimeError("remote project-state snapshot failed SQLite quick_check")

        tmp_path.replace(path)
        tmp_path = None
        for suffix in ("-wal", "-shm", "-journal"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)

        with _LOCK:
            _STATUS["snapshot_verified"] = True
            _STATUS["last_sync_at"] = head.get("snapshot_at") or str(time.time())
            _STATUS["last_sha256"] = actual_digest
            _STATUS["last_error"] = None
            _CHANGE_GENERATION = 0
            _SYNC_DIRTY = False

        return {"restored": True, "size_bytes": size, "sha256": actual_digest, "truthful": True}
    except Exception as exc:
        with _LOCK:
            _STATUS["snapshot_verified"] = False
            _STATUS["last_error"] = _describe_storage_error(exc)
        return {
            "restored": False,
            "reason": "restore_failed",
            "error": _describe_storage_error(exc),
            "truthful": True,
        }
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _snapshot_database() -> Path:
    with _LOCK:
        path = _DB_PATH
    if path is None:
        raise RuntimeError("project-state database path is not configured")
    if not path.exists():
        raise RuntimeError("project-state database does not exist")

    fd, raw_tmp = tempfile.mkstemp(
        prefix="ai-infinity-db-", suffix=".snapshot", dir=str(path.parent)
    )
    os.close(fd)
    tmp = Path(raw_tmp)

    try:
        source = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        target = sqlite3.connect(str(tmp), timeout=30, check_same_thread=False)
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()
        return tmp
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_now() -> Dict[str, Any]:
    """Publish a consistent SQLite snapshot only after verified object-store read-back."""
    global _SYNC_DIRTY, _SYNC_FAILURES
    with _SNAPSHOT_LOCK:
        with _LOCK:
            active = bool(_STATUS.get("active") and _STATUS.get("verified"))
            path = _DB_PATH
            generation = _CHANGE_GENERATION
        if not active or path is None:
            return {"status": "disabled", "truthful": True}

        tmp: Optional[Path] = None
        try:
            tmp = _snapshot_database()
            size = tmp.stat().st_size
            if size <= 0:
                raise RuntimeError("project-state snapshot is empty")
            digest = _sha256_path(tmp)
            stamp = str(time.time())
            client = _client()
            with tmp.open("rb") as stream:
                client.put_object(
                    Bucket=_cfg()["bucket"],
                    Key=object_key(),
                    Body=stream,
                    ContentType="application/x-sqlite3",
                    Metadata={
                        "ai-infinity-sha256": digest,
                        "ai-infinity-snapshot-at": stamp,
                        "ai-infinity-schema": "studio-state-v1",
                    },
                )

            head = _head()
            if not head or int(head.get("size_bytes") or -1) != size:
                raise RuntimeError("B2 project-state snapshot size verification failed")
            if str(head.get("sha256") or "") != digest:
                raise RuntimeError("B2 project-state snapshot SHA-256 metadata verification failed")

            body = client.get_object(Bucket=_cfg()["bucket"], Key=object_key()).get("Body")
            if body is None:
                raise RuntimeError("B2 project-state snapshot read-back returned no body")
            remote_hash = hashlib.sha256()
            remote_size = 0
            try:
                while True:
                    chunk = body.read(1024 * 1024)
                    if not chunk:
                        break
                    remote_hash.update(chunk)
                    remote_size += len(chunk)
            finally:
                close = getattr(body, "close", None)
                if callable(close):
                    close()
            if remote_size != size or remote_hash.hexdigest() != digest:
                raise RuntimeError("B2 project-state snapshot read-back integrity check failed")

            with _LOCK:
                if _CHANGE_GENERATION != generation:
                    _STATUS["snapshot_verified"] = False
                    _STATUS["last_error"] = "Database changed during snapshot; a newer checkpoint is required"
                    _SYNC_DIRTY = True
                    return {"status": "stale_snapshot", "size_bytes": size, "sha256": digest, "truthful": True}
                _STATUS["snapshot_verified"] = True
                _STATUS["last_sync_at"] = stamp
                _STATUS["last_sha256"] = digest
                _STATUS["last_error"] = None
                _SYNC_DIRTY = False
                _SYNC_FAILURES = 0

            return {"status": "verified", "size_bytes": size, "sha256": digest, "snapshot_at": stamp, "truthful": True}
        except Exception as exc:
            with _LOCK:
                _STATUS["snapshot_verified"] = False
                _STATUS["last_error"] = _describe_storage_error(exc)
            return {"status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:300]}", "truthful": True}
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)


def _snapshot_timer_job() -> None:
    """Run one checkpoint and retry transient failures with bounded backoff."""
    global _SYNC_TIMER, _SYNC_DIRTY, _SYNC_FAILURES
    with _LOCK:
        generation = _CHANGE_GENERATION
        _SYNC_DIRTY = False

    result: Dict[str, Any] = {"status": "failed", "truthful": True}
    try:
        result = snapshot_now()
    finally:
        with _LOCK:
            changed_while_running = _CHANGE_GENERATION != generation or _SYNC_DIRTY
            _SYNC_TIMER = None
            active = bool(_STATUS.get("active") and _STATUS.get("verified"))
            retry_delay: Optional[float] = None

            if changed_while_running and active:
                # A stale snapshot is never declared durable. The changed
                # generation needs an immediate fresh checkpoint.
                _SYNC_DIRTY = False
                _SYNC_FAILURES = 0
                retry_delay = 0.25
            elif active and str(result.get("status") or "").lower() == "failed":
                # B2/network errors can be transient even when no further
                # SQLite write arrives to reschedule the debounced timer.
                # Retry a bounded number of times; after that, remain fail-closed
                # until the next committed write requests a new checkpoint.
                if _SYNC_FAILURES < _MAX_SNAPSHOT_RETRIES:
                    _SYNC_FAILURES += 1
                    retry_delay = float(min(8, 1 << (_SYNC_FAILURES - 1)))
            elif str(result.get("status") or "").lower() == "verified":
                _SYNC_FAILURES = 0

            if retry_delay is not None:
                _SYNC_TIMER = threading.Timer(retry_delay, _snapshot_timer_job)
                _SYNC_TIMER.daemon = True
                _SYNC_TIMER.start()


def schedule_sync(delay: float = 0.75) -> None:
    """Debounce committed writes and schedule another checkpoint for concurrent changes."""
    global _SYNC_TIMER, _CHANGE_GENERATION, _SYNC_DIRTY, _SYNC_FAILURES
    with _LOCK:
        if not (_STATUS.get("active") and _STATUS.get("verified")):
            return
        _CHANGE_GENERATION += 1
        _SYNC_FAILURES = 0
        _STATUS["snapshot_verified"] = False
        _SYNC_DIRTY = True
        if _SYNC_TIMER is not None and _SYNC_TIMER.is_alive():
            return
        _SYNC_TIMER = threading.Timer(max(0.2, float(delay)), _snapshot_timer_job)
        _SYNC_TIMER.daemon = True
        _SYNC_TIMER.start()


class DurableConnection(sqlite3.Connection):
    """SQLite connection that schedules a durable snapshot after committed data or schema changes."""

    def __enter__(self):
        self._ai_initial_changes = self.total_changes
        self._ai_initial_schema_version = int(
            self.execute("PRAGMA schema_version").fetchone()[0]
        )
        return super().__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        before = getattr(self, "_ai_initial_changes", self.total_changes)
        schema_before = getattr(
            self,
            "_ai_initial_schema_version",
            int(self.execute("PRAGMA schema_version").fetchone()[0]),
        )
        result = super().__exit__(exc_type, exc_value, traceback)
        if exc_type is None:
            schema_after = int(self.execute("PRAGMA schema_version").fetchone()[0])
            if self.total_changes > before or schema_after != schema_before:
                schedule_sync()
        return result


def status() -> Dict[str, Any]:
    with _LOCK:
        out = dict(_STATUS)
    # Defend status consumers even if an older code path stored a raw exception.
    if out.get("last_error"):
        out["last_error"] = _safe_error_text(out["last_error"], 260)
    enabled = contract_enabled()
    out.update(
        {
            "snapshot_verified": bool(out.get("snapshot_verified")),
            "backend": "backblaze_b2" if enabled else None,
            "object_key": object_key() if enabled else None,
            "contract_enabled": enabled,
            "truthful": True,
        }
    )
    return out


atexit.register(snapshot_now)
