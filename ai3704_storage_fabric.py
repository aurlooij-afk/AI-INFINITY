from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
import requests

try:
    import boto3
    from botocore.config import Config as BotoConfig
except Exception:
    boto3 = None
    BotoConfig = None

VERSION = "TARGET-2050.3704"
BUILD = "FIVE-PROVIDER-DURABLE-STORAGE-FABRIC"

APP = globals()["app"]
DB = globals()["db"]
LOCK = globals()["_db_lock"]
NOW = globals().get("now", time.time)
ROOT = Path(os.getenv("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity"))
STAGING = ROOT / "storage-staging"
STAGING.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_MB = max(1, int(os.getenv("AI_INFINITY_STORAGE_MAX_UPLOAD_MB", "250")))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
PRESIGN_SECONDS = max(60, min(86400, int(os.getenv("AI_INFINITY_STORAGE_PRESIGN_SECONDS", "3600"))))
PRIMARY = os.getenv("AI_INFINITY_STORAGE_PRIMARY", "cloudflare_r2").strip().lower()
REPLICAS = [x.strip().lower() for x in os.getenv(
    "AI_INFINITY_STORAGE_REPLICAS",
    "oracle_object_storage,backblaze_b2,tigris,supabase"
).split(",") if x.strip()]
PROVIDERS = ["cloudflare_r2", "oracle_object_storage", "backblaze_b2", "tigris", "supabase"]
S3_PROVIDERS = {"cloudflare_r2", "oracle_object_storage", "backblaze_b2", "tigris"}

def _j(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"), default=str)

def _uid(request: Request) -> str:
    fn = globals().get("uid")
    if callable(fn):
        return str(fn(request))
    alt = globals().get("_3603_session_user")
    if callable(alt):
        return str(alt(request))
    return "anonymous"

def _safe_name(value: str) -> str:
    value = os.path.basename(value or "artifact.bin")
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return (value or "artifact.bin")[:180]

def _user_prefix(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:24]

def _config(provider: str) -> Dict[str, Any]:
    p = provider
    if p == "cloudflare_r2":
        account = os.getenv("CLOUDFLARE_R2_ACCOUNT_ID", "").strip()
        endpoint = os.getenv("CLOUDFLARE_R2_ENDPOINT", "").strip()
        if not endpoint and account:
            endpoint = f"https://{account}.r2.cloudflarestorage.com"
        return {
            "name": p,
            "label": "Cloudflare R2",
            "kind": "s3",
            "endpoint": endpoint,
            "bucket": os.getenv("CLOUDFLARE_R2_BUCKET", "").strip(),
            "access": os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID", "").strip(),
            "secret": os.getenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "").strip(),
            "region": "auto",
        }
    if p == "oracle_object_storage":
        return {
            "name": p,
            "label": "Oracle Object Storage",
            "kind": "s3",
            "endpoint": os.getenv("ORACLE_OBJECT_STORAGE_ENDPOINT", "").strip(),
            "bucket": os.getenv("ORACLE_OBJECT_STORAGE_BUCKET", "").strip(),
            "access": os.getenv("ORACLE_OBJECT_STORAGE_ACCESS_KEY_ID", "").strip(),
            "secret": os.getenv("ORACLE_OBJECT_STORAGE_SECRET_ACCESS_KEY", "").strip(),
            "region": os.getenv("ORACLE_OBJECT_STORAGE_REGION", "").strip() or "us-phoenix-1",
        }
    if p == "backblaze_b2":
        endpoint = os.getenv("BACKBLAZE_B2_ENDPOINT", "").strip()
        region = os.getenv("BACKBLAZE_B2_REGION", "").strip()
        if not endpoint and region:
            endpoint = f"https://s3.{region}.backblazeb2.com"
        return {
            "name": p,
            "label": "Backblaze B2",
            "kind": "s3",
            "endpoint": endpoint,
            "bucket": os.getenv("BACKBLAZE_B2_BUCKET", "").strip(),
            "access": os.getenv("BACKBLAZE_B2_KEY_ID", "").strip(),
            "secret": os.getenv("BACKBLAZE_B2_APPLICATION_KEY", "").strip(),
            "region": region or "us-west-004",
        }
    if p == "tigris":
        return {
            "name": p,
            "label": "Tigris",
            "kind": "s3",
            "endpoint": os.getenv("TIGRIS_ENDPOINT", "https://fly.storage.tigris.dev").strip(),
            "bucket": os.getenv("TIGRIS_BUCKET", "").strip(),
            "access": os.getenv("TIGRIS_ACCESS_KEY_ID", "").strip(),
            "secret": os.getenv("TIGRIS_SECRET_ACCESS_KEY", "").strip(),
            "region": os.getenv("TIGRIS_REGION", "auto").strip(),
        }
    if p == "supabase":
        return {
            "name": p,
            "label": "Supabase Storage",
            "kind": "supabase",
            "base": os.getenv("SUPABASE_URL", "").strip().rstrip("/"),
            "bucket": os.getenv("SUPABASE_STORAGE_BUCKET", "").strip(),
            "key": os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
        }
    return {"name": p, "label": p, "kind": "unknown"}

def _configured(cfg: Dict[str, Any]) -> bool:
    if cfg.get("kind") == "s3":
        return all([cfg.get("endpoint"), cfg.get("bucket"), cfg.get("access"), cfg.get("secret")]) and boto3 is not None
    if cfg.get("kind") == "supabase":
        return all([cfg.get("base"), cfg.get("bucket"), cfg.get("key")])
    return False

def _client(cfg: Dict[str, Any]):
    if boto3 is None or BotoConfig is None:
        raise RuntimeError("boto3 is not installed")
    return boto3.client(
        "s3",
        endpoint_url=cfg["endpoint"],
        region_name=cfg.get("region") or "auto",
        aws_access_key_id=cfg["access"],
        aws_secret_access_key=cfg["secret"],
        config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 4, "mode": "standard"}),
    )

def _storage_key(user_id: str, asset_id: str, filename: str) -> str:
    stamp = time.strftime("%Y/%m", time.gmtime(NOW()))
    return f"ai-infinity/{_user_prefix(user_id)}/{stamp}/{asset_id}/{_safe_name(filename)}"

def _db_init() -> None:
    with LOCK, DB() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS ai_storage_assets(
              asset_id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              object_key TEXT NOT NULL,
              filename TEXT NOT NULL,
              content_type TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              sha256 TEXT NOT NULL,
              primary_provider TEXT,
              status TEXT NOT NULL,
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_storage_assets_user
              ON ai_storage_assets(user_id,updated_at DESC);
            CREATE TABLE IF NOT EXISTS ai_storage_replicas(
              asset_id TEXT NOT NULL,
              provider TEXT NOT NULL,
              status TEXT NOT NULL,
              size_bytes INTEGER,
              sha256 TEXT,
              etag TEXT,
              error TEXT,
              verified_at REAL,
              updated_at REAL NOT NULL,
              PRIMARY KEY(asset_id,provider)
            );
            CREATE INDEX IF NOT EXISTS idx_ai_storage_replicas_asset
              ON ai_storage_replicas(asset_id,status);
            """
        )

_db_init()

def _sha256_file(path: Path) -> tuple[int, str]:
    h = hashlib.sha256()
    total = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"storage upload exceeds {MAX_UPLOAD_MB} MB")
            h.update(chunk)
    return total, h.hexdigest()

def _s3_put(cfg: Dict[str, Any], key: str, path: Path, content_type: str, sha256: str):
    client = _client(cfg)
    with path.open("rb") as fh:
        result = client.upload_fileobj(
            fh,
            cfg["bucket"],
            key,
            ExtraArgs={
                "ContentType": content_type or "application/octet-stream",
                "Metadata": {"ai-infinity-sha256": sha256},
            },
        )
    head = client.head_object(Bucket=cfg["bucket"], Key=key)
    meta_sha = str((head.get("Metadata") or {}).get("ai-infinity-sha256") or "")
    return {
        "etag": str(head.get("ETag") or "").strip('"'),
        "size_bytes": int(head.get("ContentLength") or 0),
        "sha256": meta_sha or sha256,
    }

def _supabase_headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    return {"Authorization": f"Bearer {cfg['key']}", "apikey": cfg["key"]}

def _supabase_path(bucket: str, key: str) -> str:
    encoded = "/".join(requests.utils.quote(part, safe="") for part in key.split("/"))
    return f"/storage/v1/object/{requests.utils.quote(bucket, safe='')}/{encoded}"

def _supabase_put(cfg: Dict[str, Any], key: str, path: Path, content_type: str):
    url = cfg["base"] + _supabase_path(cfg["bucket"], key)
    headers = _supabase_headers(cfg)
    headers["Content-Type"] = content_type or "application/octet-stream"
    headers["x-upsert"] = "true"
    with path.open("rb") as fh:
        resp = requests.post(url, headers=headers, data=fh, timeout=300)
    if resp.status_code >= 300:
        raise RuntimeError(f"Supabase upload failed: HTTP {resp.status_code} {resp.text[:300]}")
    size = path.stat().st_size
    return {"etag": "", "size_bytes": size, "sha256": ""}

def _put_provider(provider: str, key: str, path: Path, content_type: str, sha256: str) -> Dict[str, Any]:
    cfg = _config(provider)
    if not _configured(cfg):
        raise RuntimeError(f"{cfg.get('label', provider)} is not configured")
    if cfg["kind"] == "s3":
        return _s3_put(cfg, key, path, content_type, sha256)
    return _supabase_put(cfg, key, path, content_type)

def _s3_head(cfg: Dict[str, Any], key: str) -> Dict[str, Any]:
    h = _client(cfg).head_object(Bucket=cfg["bucket"], Key=key)
    return {
        "exists": True,
        "size_bytes": int(h.get("ContentLength") or 0),
        "sha256": str((h.get("Metadata") or {}).get("ai-infinity-sha256") or ""),
        "etag": str(h.get("ETag") or "").strip('"'),
    }

def _supabase_head(cfg: Dict[str, Any], key: str) -> Dict[str, Any]:
    # Storage object reads are used for a lightweight existence/size probe because
    # the public Storage API is centered on GET/POST/PUT/DELETE object operations.
    url = cfg["base"] + _supabase_path(cfg["bucket"], key)
    headers = _supabase_headers(cfg)
    headers["Range"] = "bytes=0-0"
    resp = requests.get(url, headers=headers, stream=True, timeout=30)
    try:
        if resp.status_code == 404:
            return {"exists": False}
        if resp.status_code >= 300:
            raise RuntimeError(f"Supabase object probe failed: HTTP {resp.status_code}")
        content_range = str(resp.headers.get("Content-Range") or "")
        size = int(resp.headers.get("Content-Length") or 0)
        if "/" in content_range:
            try:
                size = int(content_range.rsplit("/", 1)[1])
            except Exception:
                pass
        return {"exists": True, "size_bytes": size, "sha256": "", "etag": ""}
    finally:
        resp.close()

def _head_provider(provider: str, key: str) -> Dict[str, Any]:
    cfg = _config(provider)
    if not _configured(cfg):
        return {"exists": False, "configured": False}
    out = _s3_head(cfg, key) if cfg["kind"] == "s3" else _supabase_head(cfg, key)
    out["configured"] = True
    return out

def _presign_provider(provider: str, key: str) -> Optional[str]:
    cfg = _config(provider)
    if not _configured(cfg):
        return None
    if cfg["kind"] == "s3":
        return _client(cfg).generate_presigned_url(
            "get_object",
            Params={"Bucket": cfg["bucket"], "Key": key},
            ExpiresIn=PRESIGN_SECONDS,
        )
    url = cfg["base"] + _supabase_path(cfg["bucket"], key).replace("/storage/v1/object/", "/storage/v1/object/sign/", 1)
    resp = requests.post(
        url,
        headers=_supabase_headers(cfg),
        json={"expiresIn": PRESIGN_SECONDS},
        timeout=30,
    )
    if resp.status_code >= 300:
        return None
    data = resp.json()
    token = data.get("signedURL") or data.get("signedUrl") or ""
    if not token:
        return None
    if token.startswith("http"):
        return token
    return cfg["base"] + "/storage/v1" + token

def _record_asset(asset_id: str, user_id: str, key: str, filename: str, content_type: str,
                  size: int, sha256: str, primary: str, status: str, metadata: Dict[str, Any]) -> None:
    t = NOW()
    with LOCK, DB() as c:
        c.execute(
            """
            INSERT INTO ai_storage_assets
            (asset_id,user_id,object_key,filename,content_type,size_bytes,sha256,primary_provider,status,metadata_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(asset_id) DO UPDATE SET
              status=excluded.status,metadata_json=excluded.metadata_json,updated_at=excluded.updated_at
            """,
            (asset_id,user_id,key,filename,content_type,size,sha256,primary,status,_j(metadata),t,t),
        )

def _record_replica(asset_id: str, provider: str, status: str, result: Optional[Dict[str, Any]]=None,
                    error: str="") -> None:
    result = result or {}
    with LOCK, DB() as c:
        c.execute(
            """
            INSERT INTO ai_storage_replicas
            (asset_id,provider,status,size_bytes,sha256,etag,error,verified_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(asset_id,provider) DO UPDATE SET
              status=excluded.status,size_bytes=excluded.size_bytes,sha256=excluded.sha256,
              etag=excluded.etag,error=excluded.error,verified_at=excluded.verified_at,updated_at=excluded.updated_at
            """,
            (
                asset_id, provider, status, result.get("size_bytes"), result.get("sha256"),
                result.get("etag"), str(error)[:800], NOW() if status == "verified" else None, NOW()
            ),
        )

def _asset_row(asset_id: str, user_id: Optional[str]=None) -> Dict[str, Any]:
    with LOCK, DB() as c:
        if user_id is None:
            row = c.execute("SELECT * FROM ai_storage_assets WHERE asset_id=?", (asset_id,)).fetchone()
        else:
            row = c.execute("SELECT * FROM ai_storage_assets WHERE asset_id=? AND user_id=?", (asset_id, user_id)).fetchone()
        if not row:
            raise HTTPException(404, "storage asset not found")
        d = dict(row)
        try:
            d["metadata"] = json.loads(d.pop("metadata_json", "{}"))
        except Exception:
            d["metadata"] = {}
        replicas = c.execute(
            "SELECT provider,status,size_bytes,sha256,etag,error,verified_at,updated_at FROM ai_storage_replicas WHERE asset_id=?",
            (asset_id,),
        ).fetchall()
    d["replicas"] = [dict(r) for r in replicas]
    return d

def _configured_provider_names() -> list[str]:
    names = []
    for p in PROVIDERS:
        if _configured(_config(p)):
            names.append(p)
    return names

def _preferred_order() -> list[str]:
    order = []
    for p in [PRIMARY] + REPLICAS + PROVIDERS:
        if p in PROVIDERS and p not in order:
            order.append(p)
    return [p for p in order if p in PROVIDERS]

def _durability_status(results: Dict[str, Dict[str, Any]], configured: list[str]) -> str:
    ok = [p for p, r in results.items() if r.get("status") == "verified"]
    if not configured:
        return "unconfigured"
    if len(ok) == len(configured) and len(ok) >= 2:
        return "durable_5_provider" if len(ok) == 5 else "durable_multi_provider"
    if len(ok) >= 2:
        return "durable_partial"
    if len(ok) == 1:
        return "primary_only"
    return "failed"

def _persist_path(path: Path, user_id: str, filename: str, content_type: str,
                  metadata: Optional[Dict[str, Any]]=None) -> Dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "local file not found")
    size, digest = _sha256_file(path)
    asset_id = "ast_" + uuid.uuid4().hex
    key = _storage_key(user_id, asset_id, filename)
    configured = _configured_provider_names()
    if not configured:
        return {
            "status": "unconfigured",
            "asset_id": asset_id,
            "configured_providers": 0,
            "required_configuration": PROVIDERS,
            "message": "No durable provider credentials are configured. Nothing was falsely marked persistent.",
            "truthful": True,
        }

    if PRIMARY not in configured:
        primary = configured[0]
    else:
        primary = PRIMARY

    order = [primary] + [p for p in configured if p != primary]
    metadata = dict(metadata or {})
    metadata.update({"filename": filename, "source": "ai-infinity", "provider_count": len(configured)})

    results: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(5, len(order)), thread_name_prefix="ai-storage") as pool:
        future_map = {
            pool.submit(_put_provider, provider, key, path, content_type, digest): provider
            for provider in order
        }
        for fut in as_completed(future_map):
            provider = future_map[fut]
            try:
                out = fut.result()
                verification = _head_provider(provider, key)
                verified = bool(verification.get("exists")) and int(verification.get("size_bytes", -1)) == size
                if verification.get("sha256"):
                    verified = verified and verification.get("sha256") == digest
                results[provider] = {"status": "verified" if verified else "uploaded_unverified", **out, "verification": verification}
            except Exception as exc:
                results[provider] = {"status": "failed", "error": str(exc)[:800]}

    status = _durability_status(results, configured)
    _record_asset(asset_id, user_id, key, filename, content_type, size, digest, primary, status, metadata)
    for provider in configured:
        r = results.get(provider, {"status": "failed", "error": "provider was not attempted"})
        _record_replica(asset_id, provider, r.get("status", "failed"), r, r.get("error", ""))
    return {
        "status": status,
        "asset_id": asset_id,
        "object_key": key,
        "size_bytes": size,
        "sha256": digest,
        "primary_provider": primary,
        "configured_providers": configured,
        "successful_providers": [p for p, r in results.items() if r.get("status") == "verified"],
        "replica_results": results,
        "truthful": True,
    }

def _download_to(provider: str, key: str, destination: Path) -> Dict[str, Any]:
    cfg = _config(provider)
    if not _configured(cfg):
        raise RuntimeError(f"{provider} is not configured")
    if cfg["kind"] == "s3":
        obj = _client(cfg).get_object(Bucket=cfg["bucket"], Key=key)
        body = obj["Body"]
        with destination.open("wb") as f:
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        return {"size_bytes": destination.stat().st_size}
    url = cfg["base"] + _supabase_path(cfg["bucket"], key)
    with requests.get(url, headers=_supabase_headers(cfg), stream=True, timeout=300) as resp:
        if resp.status_code >= 300:
            raise RuntimeError(f"Supabase download failed: HTTP {resp.status_code}")
        with destination.open("wb") as f:
            for chunk in resp.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)
    return {"size_bytes": destination.stat().st_size}

def _repair(asset_id: str, user_id: str) -> Dict[str, Any]:
    asset = _asset_row(asset_id, user_id)
    successful = [r["provider"] for r in asset["replicas"] if r.get("status") in ("verified", "uploaded_unverified") and _configured(_config(r["provider"]))]
    configured = _configured_provider_names()
    missing = [p for p in configured if p not in successful]
    if not missing:
        return {"status": asset["status"], "asset_id": asset_id, "repaired": [], "message": "No configured provider is missing.", "truthful": True}
    if not successful:
        raise HTTPException(409, "No verified source replica is available for repair")
    source = successful[0]
    tmp = STAGING / f"repair-{uuid.uuid4().hex}.bin"
    try:
        _download_to(source, asset["object_key"], tmp)
        results = {}
        for provider in missing:
            try:
                out = _put_provider(provider, asset["object_key"], tmp, asset["content_type"], asset["sha256"])
                verification = _head_provider(provider, asset["object_key"])
                verified = bool(verification.get("exists")) and int(verification.get("size_bytes",-1)) == int(asset["size_bytes"])
                if verification.get("sha256"):
                    verified = verified and verification.get("sha256") == asset["sha256"]
                results[provider] = "verified" if verified else "uploaded_unverified"
                _record_replica(asset_id, provider, results[provider], {**out, **verification})
            except Exception as exc:
                results[provider] = "failed: " + str(exc)[:300]
                _record_replica(asset_id, provider, "failed", {}, str(exc))
        refreshed = _asset_row(asset_id, user_id)
        ok = sum(1 for r in refreshed["replicas"] if r.get("status") == "verified")
        new_status = "durable_5_provider" if ok == 5 else ("durable_multi_provider" if ok >= 2 else "primary_only")
        with LOCK, DB() as c:
            c.execute("UPDATE ai_storage_assets SET status=?,updated_at=? WHERE asset_id=?", (new_status,NOW(),asset_id))
        return {"status": new_status, "asset_id": asset_id, "source_provider": source, "repaired": results, "truthful": True}
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass

@APP.get("/infinity/storage/v1/status")
def storage_status(request: Request):
    configured = []
    providers = []
    for p in PROVIDERS:
        cfg = _config(p)
        ready = _configured(cfg)
        providers.append({"id": p, "name": cfg.get("label"), "type": cfg.get("kind"), "configured": ready})
        if ready:
            configured.append(p)
    with LOCK, DB() as c:
        counts = {}
        try:
            counts["assets"] = c.execute("SELECT COUNT(*) FROM ai_storage_assets").fetchone()[0]
            counts["durable_assets"] = c.execute("SELECT COUNT(*) FROM ai_storage_assets WHERE status IN ('durable_5_provider','durable_multi_provider')").fetchone()[0]
        except Exception:
            counts = {"assets": 0, "durable_assets": 0}
    return {
        "version": VERSION,
        "build": BUILD,
        "primary": PRIMARY,
        "configured_count": len(configured),
        "configured_providers": configured,
        "providers": providers,
        "policy": {
            "multi_provider_write": True,
            "verify_after_write": True,
            "automatic_fallback_reads": True,
            "repair_missing_replicas": True,
            "ephemeral_local_disk_is_not_authoritative": True,
        },
        "counts": counts,
        "truthful": True,
    }

@APP.get("/infinity/storage/v1/assets")
def storage_assets(request: Request, limit: int = 100):
    user_id = _uid(request)
    limit = max(1, min(200, int(limit)))
    with LOCK, DB() as c:
        rows = [
            dict(r) for r in c.execute(
                "SELECT asset_id,object_key,filename,content_type,size_bytes,sha256,primary_provider,status,created_at,updated_at FROM ai_storage_assets WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        ]
        for row in rows:
            row["replicas"] = [
                dict(r) for r in c.execute(
                    "SELECT provider,status,size_bytes,sha256,etag,error,verified_at FROM ai_storage_replicas WHERE asset_id=?",
                    (row["asset_id"],),
                ).fetchall()
            ]
    return {"assets": rows, "truthful": True}

@APP.get("/infinity/storage/v1/assets/{asset_id}")
def storage_asset(asset_id: str, request: Request):
    return {"asset": _asset_row(asset_id, _uid(request)), "truthful": True}

@APP.post("/infinity/storage/v1/upload")
async def storage_upload(request: Request, file: UploadFile = File(...)):
    user_id = _uid(request)
    filename = _safe_name(file.filename or "upload.bin")
    content_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    fd, tmp_name = tempfile.mkstemp(prefix="upload-", suffix=".bin", dir=str(STAGING))
    os.close(fd)
    tmp = Path(tmp_name)
    total = 0
    try:
        with tmp.open("wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, f"storage upload exceeds {MAX_UPLOAD_MB} MB")
                f.write(chunk)
        result = _persist_path(tmp, user_id, filename, content_type, {"source": "user_upload"})
        return JSONResponse(result)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass

@APP.post("/infinity/storage/v1/local")
def storage_local(payload: Dict[str, Any], request: Request):
    user_id = _uid(request)
    raw = str(payload.get("path") or "").strip()
    if not raw:
        raise HTTPException(400, "path is required")
    candidate = Path(raw).expanduser().resolve()
    root = ROOT.resolve()
    allowed = candidate == root or str(candidate).startswith(str(root) + os.sep)
    if not allowed:
        raise HTTPException(403, "local persistence is restricted to AI_INFINITY_DATA_DIR")
    filename = _safe_name(str(payload.get("filename") or candidate.name))
    ctype = str(payload.get("content_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return _persist_path(candidate, user_id, filename, ctype, metadata)

@APP.post("/infinity/storage/v1/repair/{asset_id}")
def storage_repair(asset_id: str, request: Request):
    return _repair(asset_id, _uid(request))

@APP.post("/infinity/storage/v1/verify/{asset_id}")
def storage_verify(asset_id: str, request: Request, full: bool = False):
    asset = _asset_row(asset_id, _uid(request))
    results = []
    for replica in asset["replicas"]:
        p = replica["provider"]
        try:
            head = _head_provider(p, asset["object_key"])
            ok = bool(head.get("exists")) and int(head.get("size_bytes",-1)) == int(asset["size_bytes"])
            sha = head.get("sha256") or ""
            if sha:
                ok = ok and sha == asset["sha256"]
            if full and ok:
                tmp = STAGING / f"verify-{uuid.uuid4().hex}.bin"
                try:
                    _download_to(p, asset["object_key"], tmp)
                    _, digest = _sha256_file(tmp)
                    ok = digest == asset["sha256"]
                finally:
                    tmp.unlink(missing_ok=True)
            status = "verified" if ok else "mismatch"
            _record_replica(asset_id, p, status, head, "" if ok else "size/checksum mismatch")
            results.append({"provider": p, "status": status, "details": head})
        except Exception as exc:
            _record_replica(asset_id, p, "failed", {}, str(exc))
            results.append({"provider": p, "status": "failed", "error": str(exc)[:500]})
    verified = sum(1 for x in results if x["status"] == "verified")
    status = "durable_5_provider" if verified == 5 else ("durable_multi_provider" if verified >= 2 else ("primary_only" if verified == 1 else "failed"))
    with LOCK, DB() as c:
        c.execute("UPDATE ai_storage_assets SET status=?,updated_at=? WHERE asset_id=?", (status,NOW(),asset_id))
    return {"status": status, "asset_id": asset_id, "full_verification": bool(full), "results": results, "truthful": True}

@APP.get("/infinity/storage/v1/url/{asset_id}")
def storage_url(asset_id: str, request: Request):
    asset = _asset_row(asset_id, _uid(request))
    preferred = []
    if asset.get("primary_provider"):
        preferred.append(asset["primary_provider"])
    preferred.extend(r["provider"] for r in asset["replicas"] if r["provider"] not in preferred and r.get("status") == "verified")
    for provider in preferred:
        try:
            url = _presign_provider(provider, asset["object_key"])
            if url:
                return {"asset_id": asset_id, "provider": provider, "url": url, "expires_in": PRESIGN_SECONDS, "truthful": True}
        except Exception:
            continue
    raise HTTPException(503, "no verified provider can issue a download URL")

@APP.delete("/infinity/storage/v1/assets/{asset_id}")
def storage_delete(asset_id: str, request: Request):
    asset = _asset_row(asset_id, _uid(request))
    results = {}
    for r in asset["replicas"]:
        p = r["provider"]
        try:
            cfg = _config(p)
            if not _configured(cfg):
                results[p] = "not_configured"
                continue
            if cfg["kind"] == "s3":
                _client(cfg).delete_object(Bucket=cfg["bucket"], Key=asset["object_key"])
            else:
                url = cfg["base"] + _supabase_path(cfg["bucket"], asset["object_key"])
                resp = requests.delete(url, headers=_supabase_headers(cfg), timeout=60)
                if resp.status_code >= 300 and resp.status_code != 404:
                    raise RuntimeError(f"HTTP {resp.status_code}")
            _record_replica(asset_id, p, "deleted", {}, "")
            results[p] = "deleted"
        except Exception as exc:
            results[p] = "failed: " + str(exc)[:300]
    with LOCK, DB() as c:
        c.execute("DELETE FROM ai_storage_replicas WHERE asset_id=?", (asset_id,))
        c.execute("DELETE FROM ai_storage_assets WHERE asset_id=?", (asset_id,))
    return {"status": "deleted", "asset_id": asset_id, "provider_results": results, "truthful": True}

def persist_bytes(data: bytes, user_id: str, filename: str, content_type: str = "application/octet-stream",
                  metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"storage object exceeds {MAX_UPLOAD_MB} MB")
    fd, tmp_name = tempfile.mkstemp(prefix="bytes-", suffix=".bin", dir=str(STAGING))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_bytes(data)
        return _persist_path(tmp, user_id, filename, content_type, metadata)
    finally:
        tmp.unlink(missing_ok=True)

# Persist source-grounded Notebook/Studio text artifacts automatically after the
# existing 3702 function has created its SQLite record. Storage is optional until
# provider credentials are configured, and failures never turn a correct local
# artifact into a false success.
try:
    _existing_create_artifact = globals().get("_create_artifact")
    if callable(_existing_create_artifact) and not getattr(APP.state, "ai_storage_artifact_hook", False):
        def _storage_aware_create_artifact(kind: str, notebook_id: str, user_id: str, title: str, prompt: str = ""):
            artifact = _existing_create_artifact(kind, notebook_id, user_id, title, prompt)
            try:
                stored = persist_bytes(
                    str(artifact.get("content") or "").encode("utf-8"),
                    user_id,
                    _safe_name(str(title)[:150] or "artifact") + ".txt",
                    "text/plain; charset=utf-8",
                    {"artifact_id": artifact.get("artifact_id"), "kind": kind, "notebook_id": notebook_id},
                )
                artifact["storage"] = stored
            except Exception as exc:
                artifact["storage"] = {"status": "not_persisted", "reason": str(exc)[:500], "truthful": True}
            return artifact
        globals()["_create_artifact"] = _storage_aware_create_artifact
        APP.state.ai_storage_artifact_hook = True
except Exception as _hook_error:
    APP.state.ai_storage_artifact_hook_error = str(_hook_error)[:500]

APP.state.ai_infinity_storage = True
APP.state.ai_infinity_storage_version = VERSION
APP.state.ai_infinity_storage_build = BUILD
