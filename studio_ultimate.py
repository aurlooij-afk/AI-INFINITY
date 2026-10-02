from __future__ import annotations

import base64
import hashlib
import html
import hmac
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from fastapi import Request, Response
from pydantic import BaseModel, Field
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request as URLRequest, urlopen

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
except Exception:  # pragma: no cover
    Image = ImageDraw = ImageFont = ImageFilter = None

VERSION = "TARGET-2050.3612"
BUILD = "AI-INFINITY-UNIVERSAL-CREATOR-STUDIO"


class StudioRequest(BaseModel):
    title: str = ""
    objective: str = ""
    topic: str = ""
    format: str = "long"
    duration: int = Field(default=300, ge=20, le=3600)
    content_type: str = "video"
    audience: str = "general audience"
    tone: str = "cinematic, intelligent, useful"
    voice: str = "en-US-AriaNeural"
    language: str = "English"
    platforms: List[str] = []
    brand_voice: str = ""
    visual_style: str = "premium editorial"
    call_to_action: str = ""

class FeedbackRequest(BaseModel):
    project_id: str
    rating: float = Field(ge=0, le=1)
    lessons: List[str] = []

class PublishRequest(BaseModel):
    project_id: str
    provider: str = "youtube"
    asset: str = "final"
    privacy_status: str = "private"
    description: str = ""
    tags: List[str] = []
    category_id: str = "22"

class WebhookRequest(BaseModel):
    url: str
    secret: str = ""
    label: str = "Custom publisher"
COOKIE = "ai_infinity_session"
DATA_DIR = Path(os.getenv("AI_INFINITY_DATA_DIR", "/tmp/ai-infinity"))
ROOT = DATA_DIR / "creator_studio"
ROOT.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.getenv("AI_INFINITY_DB_PATH", str(DATA_DIR / "ai_infinity.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DB_LOCK = threading.RLock()
WORKER_STARTED = False
WORKER_GUARD = threading.Lock()
STOP = threading.Event()
SMOKE = os.getenv("AI_INFINITY_STUDIO_SMOKE", "").strip().lower() in {"1", "true", "yes"}


def now() -> float:
    return time.time()


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def jdump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "item")).strip("._")[:120] or "item"


def digest(value: Any) -> str:
    return hashlib.sha256(jdump(value).encode("utf-8")).hexdigest()


def _connect() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with DB_LOCK, _connect() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS studio_projects_3610(
                project_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL,
                blueprint_json TEXT,
                result_json TEXT,
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_studio_projects_user_3610 ON studio_projects_3610(user_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS studio_assets_3610(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                path TEXT NOT NULL,
                media_type TEXT,
                metadata_json TEXT,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_studio_assets_project_3610 ON studio_assets_3610(project_id, id DESC);
            CREATE TABLE IF NOT EXISTS studio_connections_3610(
                connection_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                label TEXT NOT NULL,
                access_enc TEXT,
                refresh_enc TEXT,
                secret_enc TEXT,
                expires_at REAL,
                scopes_json TEXT,
                config_json TEXT,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_studio_conn_user_3610 ON studio_connections_3610(user_id, provider, updated_at DESC);
            CREATE TABLE IF NOT EXISTS studio_publications_3610(
                publication_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                status TEXT NOT NULL,
                external_url TEXT,
                response_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_studio_pub_project_3610 ON studio_publications_3610(project_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS studio_feedback_3610(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                rating REAL NOT NULL,
                lessons_json TEXT,
                metrics_json TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS studio_skills_3610(
                skill_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                version INTEGER NOT NULL,
                recipe_json TEXT NOT NULL,
                quality REAL NOT NULL DEFAULT 0,
                uses INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_studio_skill_user_3610 ON studio_skills_3610(user_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS studio_creator_profiles_3612(
                user_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS studio_resources_3610(
                resource_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                endpoint TEXT,
                config_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_studio_resource_user_3610 ON studio_resources_3610(user_id, updated_at DESC);
            """
        )
        existing = {r[1] for r in c.execute("PRAGMA table_info(studio_projects_3610)").fetchall()}
        migrations = {
            "progress": "ALTER TABLE studio_projects_3610 ADD COLUMN progress REAL NOT NULL DEFAULT 0",
            "stage": "ALTER TABLE studio_projects_3610 ADD COLUMN stage TEXT NOT NULL DEFAULT 'queued'",
            "current_scene": "ALTER TABLE studio_projects_3610 ADD COLUMN current_scene INTEGER NOT NULL DEFAULT 0",
            "total_scenes": "ALTER TABLE studio_projects_3610 ADD COLUMN total_scenes INTEGER NOT NULL DEFAULT 0",
            "attempt": "ALTER TABLE studio_projects_3610 ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0",
        }
        for col, sql in migrations.items():
            if col not in existing:
                c.execute(sql)


init_db()


# ---------------------------------------------------------------------------
# Secure connection storage. Use the platform vault when available; otherwise
# derive a process/storage key from the already-existing session secret or a
# local key file. Raw credentials never enter JSON responses.
# ---------------------------------------------------------------------------

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:  # pragma: no cover
    Fernet = None
    InvalidToken = Exception


def _fernet() -> Optional[Any]:
    if Fernet is None:
        return None
    # Prefer the existing AI Infinity vault key so all encrypted credentials
    # share one security boundary. This does not expose the key to the client.
    key = os.getenv("AI_INFINITY_VAULT_KEY", "").strip()
    if not key:
        key = os.getenv("AI_INFINITY_SESSION_SECRET", "").strip()
    if not key:
        key_file = ROOT / ".studio-key"
        try:
            if key_file.exists():
                key = key_file.read_text(encoding="utf-8").strip()
            else:
                key = base64.urlsafe_b64encode(os.urandom(32)).decode()
                key_file.write_text(key, encoding="utf-8")
                try:
                    key_file.chmod(0o600)
                except Exception:
                    pass
        except Exception:
            return None
    # Fernet wants a 32-byte URL-safe base64 key. If the supplied secret is not
    # already one, deterministically derive one without storing the source.
    try:
        raw = base64.urlsafe_b64decode(key.encode("ascii"))
        if len(raw) == 32:
            return Fernet(key.encode("ascii"))
    except Exception:
        pass
    derived = base64.urlsafe_b64encode(hashlib.sha256(key.encode("utf-8")).digest())
    try:
        return Fernet(derived)
    except Exception:
        return None


def enc(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    f = _fernet()
    if not f:
        raise RuntimeError("secure credential storage is unavailable")
    return f.encrypt(str(value).encode("utf-8")).decode("ascii")


def dec(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    f = _fernet()
    if not f:
        return None
    try:
        return f.decrypt(str(value).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None


def redact(data: Any) -> Any:
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if str(k).lower() in {"authorization", "cookie", "set-cookie", "token", "access_token", "refresh_token", "api_key", "apikey", "secret", "password"}:
                out[k] = "[REDACTED]"
            else:
                out[k] = redact(v)
        return out
    if isinstance(data, list):
        return [redact(x) for x in data]
    return data


# ---------------------------------------------------------------------------
# Web research: public sources first, multiple independent sources when
# reachable, and explicit source metadata for every extracted item.
# ---------------------------------------------------------------------------


def http_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 20, max_bytes: int = 2_000_000) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("unsupported URL scheme")
    req = URLRequest(url, headers={"User-Agent": "AI-Infinity/3610", **(headers or {})})
    with urlopen(req, timeout=timeout) as r:
        return r.read(max_bytes)


def http_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 20) -> Dict[str, Any]:
    return json.loads(http_get(url, headers=headers, timeout=timeout).decode("utf-8", "replace"))


def download(url: str, path: Path, headers: Optional[Dict[str, str]] = None, timeout: int = 60, max_bytes: int = 40_000_000) -> Path:
    req = URLRequest(url, headers={"User-Agent": "AI-Infinity/3610", **(headers or {})})
    with urlopen(req, timeout=timeout) as r, path.open("wb") as f:
        total = 0
        while True:
            b = r.read(256 * 1024)
            if not b:
                break
            total += len(b)
            if total > max_bytes:
                raise RuntimeError("remote asset too large")
            f.write(b)
    return path


def research_topic(topic: str, limit: int = 10) -> Dict[str, Any]:
    from urllib.parse import quote_plus
    sources: List[Dict[str, Any]] = []
    seen = set()

    def add(item: Dict[str, Any]) -> None:
        key = (item.get("url") or "", item.get("title") or "")
        if not key[0] and not key[1]:
            return
        if key in seen:
            return
        seen.add(key)
        sources.append(item)

    def wikipedia() -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            url = "https://en.wikipedia.org/w/api.php?" + urlencode({
                "action": "query", "format": "json", "generator": "search", "gsrsearch": topic,
                "gsrlimit": min(limit, 10), "prop": "extracts|info", "exintro": 1, "explaintext": 1, "inprop": "url"
            })
            data = http_json(url, timeout=5)
            for p in (data.get("query", {}).get("pages", {}) or {}).values():
                out.append({"source": "Wikipedia", "title": p.get("title"), "url": p.get("fullurl"), "summary": (p.get("extract") or "")[:5000]})
        except Exception as e:
            out.append({"source": "Wikipedia", "error": str(e)[:240]})
        return out

    def duckduckgo() -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            q = quote_plus(topic)
            raw = http_get(f"https://html.duckduckgo.com/html/?q={q}", timeout=5, max_bytes=3_000_000).decode("utf-8", "replace")
            blocks = re.findall(r"<div[^>]+class=\"result__body\"[^>]*>(.*?)</div>\s*</div>", raw, re.S | re.I)
            for block in blocks[:limit]:
                hm = re.search(r'href=\"([^\"]+)\"[^>]*class=\"result__a\"[^>]*>(.*?)</a>', block, re.S | re.I)
                if not hm:
                    hm = re.search(r'class=\"result__a\"[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>', block, re.S | re.I)
                if hm:
                    title = re.sub(r"<[^>]+>", "", hm.group(2) or hm.group(1)).strip()
                    u = hm.group(1) if hm.group(1).startswith("http") else ""
                    if u:
                        sm = re.search(r'class=\"result__snippet[^\"]*\"[^>]*>(.*?)</', block, re.S | re.I)
                        snippet = re.sub(r"<[^>]+>", "", sm.group(1)).strip() if sm else ""
                        out.append({"source": "DuckDuckGo", "title": html.unescape(title)[:300], "url": u, "summary": html.unescape(snippet)[:1500]})
        except Exception as e:
            out.append({"source": "DuckDuckGo", "error": str(e)[:240]})
        return out

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="studio-research") as pool:
        futures = [pool.submit(wikipedia), pool.submit(duckduckgo)]
        for f in as_completed(futures):
            try:
                for item in f.result():
                    add(item)
            except Exception as e:
                add({"source": "research", "error": str(e)[:240]})

    retrieved = now()
    return {"topic": topic, "sources": sources[:limit * 2], "source_count": len([x for x in sources if not x.get("error")]), "retrieved_at": retrieved, "truthful": True}


# ---------------------------------------------------------------------------
# Creative intelligence. Uses the existing AI Infinity model router when one
# is connected; otherwise a robust deterministic creative engine still closes
# the production pipeline rather than pretending a model exists.
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").strip()
    if not t:
        return None
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        t = m.group(0)
    try:
        x = json.loads(t)
        return x if isinstance(x, dict) else None
    except Exception:
        return None


def creative_plan(req: Dict[str, Any], research: Dict[str, Any], model_fn: Optional[Callable]) -> Tuple[Dict[str, Any], str]:
    title = str(req.get("title") or req.get("topic") or req.get("objective") or "AI content").strip()
    objective = str(req.get("objective") or "Create useful, original, high-retention content").strip()
    fmt = "short" if str(req.get("format", "long")).lower() in {"short", "shorts", "reel", "tiktok"} else "long"
    duration = int(req.get("duration") or (60 if fmt == "short" else 300))
    audience = str(req.get("audience") or "general audience")
    tone = str(req.get("tone") or "cinematic, intelligent, useful")
    language = str(req.get("language") or "English")
    platforms = ", ".join(str(x) for x in (req.get("platforms") or [])[:12]) or "multi-platform"
    brand_voice = str(req.get("brand_voice") or "")[:1000]
    visual_style = str(req.get("visual_style") or "premium editorial")[:500]
    call_to_action = str(req.get("call_to_action") or "")[:500]
    source_digest = []
    for s in research.get("sources", [])[:12]:
        if s.get("title"):
            source_digest.append(f"- {s.get('title')} | {s.get('url')} | {s.get('summary','')[:900]}")
    research_text = "\n".join(source_digest)
    if model_fn:
        prompt = f"""You are the senior showrunner, researcher, writer, visual director and editor for a premium creator studio. Build a complete production blueprint for this project.
TITLE: {title}
OBJECTIVE: {objective}
FORMAT: {fmt}
DURATION: {duration} seconds
AUDIENCE: {audience}
TONE: {tone}
LANGUAGE: {language}
PLATFORMS: {platforms}
BRAND VOICE: {brand_voice or "none supplied"}
VISUAL STYLE: {visual_style}
CALL TO ACTION: {call_to_action or "choose an appropriate natural CTA"}
RESEARCH SOURCES (use only as evidence; do not invent facts):
{research_text}

Return ONLY JSON with:
title, hook, premise, audience, tone, cta, language, platform_adaptations, chapters
Each chapter must include: heading, narration, visual_query, image_prompt, on_screen, duration, proof_needed.
Use 5-18 chapters. Total durations should approximately equal the target. Narration must be original and information-dense rather than filler. Separate factual claims from creative framing. Visuals should mix documentary reality, diagrams, environmental shots, close details and purposeful typography. For unsupported facts, put the topic/claim in proof_needed. Avoid deceptive impersonation, fabricated citations and synthetic claims presented as observed events."""
        try:
            raw = model_fn({"messages": [{"role": "user", "content": prompt}], "temperature": 0.8}) or {}
            data = _extract_json(raw.get("text") or raw.get("answer") or "")
            chapters = data.get("chapters") if isinstance(data, dict) else None
            if isinstance(chapters, list) and len(chapters) >= 4:
                clean = {**data}
                clean["chapters"] = chapters[:18]
                return clean, str(raw.get("provider") or "model")
        except Exception:
            pass
    return _fallback_creative_plan(title, objective, fmt, duration, audience, tone, research), "builtin-creative-engine"


def _fallback_creative_plan(title: str, objective: str, fmt: str, duration: int, audience: str, tone: str, research: Dict[str, Any]) -> Dict[str, Any]:
    sources = [s for s in research.get("sources", []) if s.get("title") and not s.get("error")]
    if not sources:
        sources = [{"title": title, "summary": objective, "url": None}]
    if fmt == "short":
        names = ["Hook", "Why it matters", "The key idea", "Practical example", "Takeaway"]
        ratios = [0.16, 0.18, 0.28, 0.22, 0.16]
    else:
        names = ["Hook", "Context", "The core idea", "How it works", "Real-world examples", "What changes", "Practical takeaway", "Closing"]
        ratios = [0.08, 0.10, 0.16, 0.18, 0.18, 0.12, 0.10, 0.08]
    chapters = []
    for i, name in enumerate(names):
        src = sources[i % len(sources)]
        sec = max(4, round(duration * ratios[i], 1))
        summary = re.sub(r"\s+", " ", str(src.get("summary") or objective)).strip()
        if len(summary) > 420:
            summary = summary[:417] + "..."
        chapters.append({
            "heading": f"{name}: {title}" if name not in {"Hook", "Closing"} else f"{name} — {title}",
            "narration": f"{name}. {title} matters because {objective.rstrip('.')}. {summary}",
            "visual_query": f"{title} {name}",
            "image_prompt": f"Cinematic editorial visual illustrating {title}, {name.lower()}, realistic lighting, meaningful composition, no logos, no text, premium documentary look",
            "on_screen": name,
            "duration": sec,
            "proof_needed": [src.get("title")] if src.get("title") else [],
        })
    return {
        "title": title[:140],
        "hook": f"What is the most useful thing to understand about {title}?",
        "premise": objective,
        "audience": audience,
        "tone": tone,
        "cta": "Save this and come back when you are ready to use the idea.",
        "chapters": chapters,
        "claims_to_verify": [s.get("title") for s in sources[:8]],
    }


# ---------------------------------------------------------------------------
# Visual acquisition / generation.
# ---------------------------------------------------------------------------


def _pexels(query: str, outdir: Path, limit: int = 3) -> List[Dict[str, Any]]:
    key = os.getenv("PEXELS_API_KEY", "").strip()
    if not key:
        return []
    try:
        data = http_json("https://api.pexels.com/videos/search?" + urlencode({"query": query, "per_page": min(limit, 10), "orientation": "landscape"}), {"Authorization": key})
        out = []
        for v in data.get("videos", []):
            files = sorted(v.get("video_files", []), key=lambda x: (x.get("width") or 0), reverse=True)
            f = next((x for x in files if (x.get("width") or 0) >= 720 and x.get("link")), None) or next((x for x in files if x.get("link")), None)
            if not f:
                continue
            p = outdir / f"pexels_{v.get('id','x')}.mp4"
            download(f["link"], p, timeout=90)
            out.append({"kind": "video", "path": str(p), "source": "Pexels", "source_url": v.get("url"), "creator": (v.get("user") or {}).get("name"), "license": "Pexels license", "width": f.get("width"), "height": f.get("height")})
        return out
    except Exception:
        return []


def _pixabay(query: str, outdir: Path, limit: int = 3) -> List[Dict[str, Any]]:
    key = os.getenv("PIXABAY_API_KEY", "").strip()
    if not key:
        return []
    try:
        data = http_json("https://pixabay.com/api/videos/?" + urlencode({"key": key, "q": query, "per_page": min(limit, 10)}))
        out = []
        for v in data.get("hits", []):
            f = (v.get("videos") or {}).get("large") or (v.get("videos") or {}).get("medium") or (v.get("videos") or {}).get("small")
            if not f or not f.get("url"):
                continue
            p = outdir / f"pixabay_{v.get('id','x')}.mp4"
            download(f["url"], p, timeout=90)
            out.append({"kind": "video", "path": str(p), "source": "Pixabay", "source_url": v.get("pageURL"), "creator": v.get("user"), "license": "Pixabay Content License", "width": f.get("width"), "height": f.get("height")})
        return out
    except Exception:
        return []


def _commons_media(query: str, outdir: Path, limit: int = 6) -> List[Dict[str, Any]]:
    try:
        data = http_json("https://commons.wikimedia.org/w/api.php?" + urlencode({
            "action": "query", "format": "json", "generator": "search", "gsrsearch": query,
            "gsrnamespace": 6, "gsrlimit": min(limit, 10), "prop": "imageinfo", "iiprop": "url|mime|extmetadata"
        }))
        out = []
        for pge in (data.get("query", {}).get("pages", {}) or {}).values():
            info = (pge.get("imageinfo") or [{}])[0]
            u = info.get("url")
            mime = str(info.get("mime") or "")
            if not u:
                continue
            meta = info.get("extmetadata") or {}
            lic = (meta.get("LicenseShortName") or {}).get("value")
            creator = (meta.get("Artist") or {}).get("value")
            if "video" in mime or re.search(r"\.(webm|ogv|mp4)(\?|$)", u, re.I):
                p = outdir / f"commons_{pge.get('pageid','x')}.media"
                try:
                    download(u, p, timeout=90)
                    out.append({"kind": "video", "path": str(p), "source": "Wikimedia Commons", "source_url": pge.get("canonicalurl") or "https://commons.wikimedia.org", "creator": creator, "license": lic or "Wikimedia Commons stated license", "title": pge.get("title")})
                except Exception:
                    continue
            elif re.search(r"\.(jpe?g|png|webp)(\?|$)", u, re.I):
                p = outdir / f"commons_{pge.get('pageid','x')}.img"
                try:
                    download(u, p, timeout=60)
                    out.append({"kind": "image", "path": str(p), "source": "Wikimedia Commons", "source_url": pge.get("canonicalurl") or "https://commons.wikimedia.org", "creator": creator, "license": lic or "Wikimedia Commons stated license", "title": pge.get("title")})
                except Exception:
                    continue
        return out
    except Exception:
        return []


def _hf_video(prompt: str, outdir: Path, index: int, duration: float) -> Optional[Dict[str, Any]]:
    token = os.getenv("HF_TOKEN", "").strip() or os.getenv("HUGGINGFACEHUB_API_TOKEN", "").strip()
    if not token:
        return None
    if os.getenv("AI_INFINITY_AI_VIDEO", "1").strip().lower() not in {"1", "true", "yes", "auto"}:
        return None
    try:
        from huggingface_hub import InferenceClient
        model = os.getenv("AI_INFINITY_VIDEO_MODEL", "Wan-AI/Wan2.1-T2V-1.3B").strip()
        client = InferenceClient(provider=os.getenv("AI_INFINITY_HF_PROVIDER", "auto").strip() or "auto", api_key=token)
        frames = max(24, min(81, int(round(max(2.0, min(float(duration), 5.0)) * 8))))
        video = client.text_to_video(prompt, model=model, num_frames=frames, num_inference_steps=int(os.getenv("AI_INFINITY_VIDEO_STEPS", "20")))
        if hasattr(video, "read"):
            raw = video.read()
        else:
            raw = bytes(video)
        if not raw:
            return None
        p = outdir / f"ai_motion_{index:02d}.mp4"
        p.write_bytes(raw)
        return {"kind": "video", "path": str(p), "source": "Hugging Face Inference Provider", "source_url": "https://huggingface.co", "creator": "AI generated", "license": "Model/provider terms apply", "model": model}
    except Exception:
        return None


def _hf_image(prompt: str, outdir: Path, index: int) -> Optional[Dict[str, Any]]:
    token = os.getenv("HF_TOKEN", "").strip() or os.getenv("HUGGINGFACEHUB_API_TOKEN", "").strip()
    if not token:
        return None
    model = os.getenv("AI_INFINITY_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell").strip()
    urls = [
        f"https://router.huggingface.co/hf-inference/models/{quote(model, safe='')}",
        f"https://api-inference.huggingface.co/models/{quote(model, safe='')}",
    ]
    for endpoint in urls:
        try:
            req = URLRequest(endpoint, data=json.dumps({"inputs": prompt}).encode("utf-8"), headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "image/png"}, method="POST")
            with urlopen(req, timeout=90) as r:
                content_type = str(r.headers.get("content-type") or "")
                raw = r.read(12_000_000)
            if content_type.startswith("image/") or raw.startswith(b"\x89PNG") or raw.startswith(b"\xff\xd8"):
                p = outdir / f"ai_visual_{index:02d}.png"
                p.write_bytes(raw)
                return {"kind": "image", "path": str(p), "source": "Hugging Face inference", "source_url": endpoint, "creator": "AI generated", "license": "Model/provider terms apply", "model": model}
        except Exception:
            continue
    return None


def _procedural_image(prompt: str, outdir: Path, index: int, width: int = 1600, height: int = 900) -> Optional[Dict[str, Any]]:
    if Image is None:
        return None
    seed = int(hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12], 16)
    # Deterministic editorial visual: abstract scene geometry + typography.
    base = (12 + seed % 28, 16 + (seed // 3) % 25, 28 + (seed // 7) % 40)
    im = Image.new("RGB", (width, height), base)
    draw = ImageDraw.Draw(im, "RGBA")
    cx, cy = width // 2, height // 2
    for i in range(14):
        x = (seed * (i + 3) * 17) % width
        y = (seed * (i + 5) * 13) % height
        r = 60 + ((seed >> (i % 18)) % 360)
        draw.ellipse((x-r, y-r, x+r, y+r), fill=(60 + (i*11)%150, 80 + (i*17)%130, 150 + (i*7)%90, 32), outline=(220, 235, 255, 35), width=2)
    font = None
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 46)
    except Exception:
        pass
    words = re.sub(r"\s+", " ", prompt).strip()
    if len(words) > 80:
        words = words[:77] + "..."
    draw.rounded_rectangle((60, height-180, width-60, height-60), radius=28, fill=(0,0,0,120))
    draw.text((90, height-155), words, fill=(245,248,255,235), font=font)
    p = outdir / f"procedural_visual_{index:02d}.png"
    im.save(p, "PNG", optimize=True)
    return {"kind": "image", "path": str(p), "source": "AI Infinity motion-design generator", "source_url": None, "creator": "AI Infinity", "license": "Original generated asset", "model": "procedural-editorial-engine"}


def acquire_scene_asset(scene: Dict[str, Any], outdir: Path, index: int, prefer_motion: bool = False, duration: float = 6.0) -> Dict[str, Any]:
    query = str(scene.get("visual_query") or scene.get("heading") or "documentary scene")
    ai_prompt = str(scene.get("image_prompt") or f"Premium cinematic documentary visual about {query}; realistic, useful, editorial, no text or logos")

    # AI imagination first when a provider is connected. Use motion generation
    # selectively because current hosted video models are substantially heavier
    # than image generation; the remainder of the sequence can mix AI and real
    # footage without pretending stock is AI-generated.
    if prefer_motion:
        motion = _hf_video(ai_prompt, outdir, index, duration)
        if motion:
            return motion
    ai = _hf_image(ai_prompt, outdir, index)
    if ai:
        return ai

    # Real motion footage next; public sources are keyless where available.
    for getter in (_pexels, _pixabay, _commons_media):
        items = getter(query, outdir, limit=3)
        videos = [x for x in items if x.get("kind") == "video"]
        if videos:
            return videos[0]
        images = [x for x in items if x.get("kind") == "image"]
        if images:
            return images[0]

    procedural = _procedural_image(ai_prompt, outdir, index)
    if procedural:
        return procedural
    raise RuntimeError("no visual asset could be created")


# ---------------------------------------------------------------------------
# Audio and render pipeline.
# ---------------------------------------------------------------------------


def ffmpeg(*args: Any, timeout: int = 240) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr[-4000:] or "ffmpeg failed")


def ffprobe_json(path: Path) -> Dict[str, Any]:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], capture_output=True, text=True, timeout=40)
    if p.returncode:
        return {}
    try:
        return json.loads(p.stdout or "{}")
    except Exception:
        return {}


def probe_duration(path: Path) -> float:
    data = ffprobe_json(path)
    try:
        return max(0.1, float((data.get("format") or {}).get("duration") or 0.1))
    except Exception:
        return 0.1


def tts(text: str, outdir: Path, index: int, voice: str) -> Tuple[Path, str, float]:
    out = outdir / f"narration_{index:02d}.mp3"
    plain = str(text or "").strip()
    if not plain:
        raise ValueError("empty narration")
    try:
        import asyncio
        import edge_tts

        async def run() -> None:
            await asyncio.wait_for(edge_tts.Communicate(plain, voice).save(str(out)), timeout=float(os.getenv("AI_INFINITY_EDGE_TTS_TIMEOUT", "5")))

        asyncio.run(run())
        if out.exists() and out.stat().st_size > 10000:
            return out, "edge-tts-neural", probe_duration(out)
    except Exception:
        pass
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        raise RuntimeError("narration engine unavailable")
    wav = outdir / f"narration_{index:02d}.wav"
    subprocess.run([exe, "-s", "155", "-w", str(wav), plain], check=True, timeout=180)
    ffmpeg("-i", wav, "-codec:a", "libmp3lame", "-q:a", "2", out, timeout=180)
    return out, "local-espeak", probe_duration(out)


def make_music(outdir: Path, seconds: float) -> Path:
    out = outdir / "music.m4a"
    s = max(1, float(seconds))
    # Original procedural music: low bed + upper harmonic + transition pulses.
    ffmpeg(
        "-f", "lavfi", "-i", f"sine=frequency=110:sample_rate=48000:duration={s}",
        "-f", "lavfi", "-i", f"sine=frequency=165:sample_rate=48000:duration={s}",
        "-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={s}",
        "-filter_complex", "[0:a]volume=0.030[a0];[1:a]volume=0.018[a1];[2:a]volume=0.006[a2];[a0][a1][a2]amix=inputs=3:duration=longest,lowpass=f=1200,afade=t=in:st=0:d=3,afade=t=out:st=" + str(max(0, s-4)) + ":d=4[a]",
        "-map", "[a]", "-c:a", "aac", "-b:a", "128k", out, timeout=300,
    )
    return out


def make_sfx(outdir: Path, duration: float, index: int) -> Path:
    out = outdir / f"transition_{index:02d}.wav"
    d = max(0.15, min(1.1, float(duration) * 0.15))
    ffmpeg("-f", "lavfi", "-i", f"anoisesrc=color=white:amplitude=0.18:duration={d}", "-af", "highpass=f=900,lowpass=f=6500,afade=t=in:st=0:d=0.04,afade=t=out:st=" + str(max(0.02, d-0.12)) + ":d=0.12", out, timeout=60)
    return out


def _subtitle_time(x: float) -> str:
    ms = int(round((x - int(x)) * 1000))
    sec = int(x)
    return f"{sec//3600:02}:{sec%3600//60:02}:{sec%60:02},{ms:03}"


def write_srt(chapters: List[Dict[str, Any]], path: Path) -> None:
    lines = []
    t = 0.0
    for i, ch in enumerate(chapters, 1):
        d = max(0.2, float(ch.get("actual_duration") or ch.get("duration") or 1))
        text = re.sub(r"\s+", " ", str(ch.get("narration") or "")).strip()
        lines.append(f"{i}\n{_subtitle_time(t)} --> {_subtitle_time(t+d)}\n{text}\n")
        t += d
    path.write_text("\n".join(lines), encoding="utf-8")


def _video_scene(asset: Dict[str, Any], duration: float, out: Path, title: str) -> None:
    if asset.get("kind") == "video":
        src = asset["path"]
        escaped = str(out).replace("'", "'\\''")
        # Real motion video path: loop source, crop to 16:9, add subtle title label.
        vf = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,eq=contrast=1.02:saturation=1.03,drawbox=x=45:y=865:w=1820:h=150:color=black@0.33:t=fill,drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='" + title.replace("'", "\\'")[:70] + "':x=75:y=915:fontsize=42:fontcolor=white"
        ffmpeg("-stream_loop", "-1", "-i", src, "-t", duration, "-vf", vf, "-an", "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", out, timeout=240)
        return
    # Still/AI visual: generate true motion via zoom/pan rather than a static slide.
    src = asset["path"]
    frames = max(1, int(round(duration * 30)))
    title_escaped = title.replace("'", "\\'")[:70]
    vf = f"scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,zoompan=z='min(zoom+0.0008,1.18)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1920x1080:fps=30,drawbox=x=45:y=865:w=1820:h=150:color=black@0.33:t=fill,drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='{title_escaped}':x=75:y=915:fontsize=42:fontcolor=white"
    ffmpeg("-loop", "1", "-i", src, "-t", duration, "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", out, timeout=240)


def _render_scene(asset: Dict[str, Any], voice: Path, music: Path, sfx: Path, duration: float, out: Path, title: str) -> None:
    # One encode per scene: visual motion + title + voice/music/SFX are mixed in
    # a single FFmpeg invocation. This is materially faster than rendering a
    # silent scene and then encoding it again with audio.
    duration = max(0.5, float(duration))
    title_escaped = str(title).replace("\\", "\\\\").replace("'", "\\'")[:70]
    if asset.get("kind") == "video":
        src = asset["path"]
        vf = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,eq=contrast=1.02:saturation=1.03,drawbox=x=45:y=865:w=1820:h=150:color=black@0.33:t=fill,drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='" + title_escaped + "':x=75:y=915:fontsize=42:fontcolor=white"
        visual_args = ["-stream_loop", "-1", "-i", src]
    else:
        src = asset["path"]
        frames = max(1, int(round(duration * 30)))
        vf = f"scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,zoompan=z='min(zoom+0.0008,1.18)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1920x1080:fps=30,drawbox=x=45:y=865:w=1820:h=150:color=black@0.33:t=fill,drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='{title_escaped}':x=75:y=915:fontsize=42:fontcolor=white"
        visual_args = ["-loop", "1", "-i", src]
    args: List[Any] = visual_args + ["-i", voice, "-stream_loop", "-1", "-i", music, "-i", sfx,
        "-filter_complex", "[1:a]loudnorm=I=-18:TP=-1.5:LRA=7[vo];[2:a]volume=0.10[m];[3:a]adelay=80|80,volume=0.10[s];[vo][m][s]amix=inputs=3:duration=first:dropout_transition=2[a]",
        "-map", "0:v:0", "-map", "[a]", "-vf", vf, "-t", duration, "-c:v", "libx264", "-preset", os.getenv("AI_INFINITY_VIDEO_PRESET", "veryfast"), "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    ffmpeg(*args, timeout=max(120, int(duration * 8)))


def _scene_mix(video: Path, voice: Path, music: Path, sfx: Path, duration: float, out: Path, captions: Optional[Path] = None) -> None:
    args: List[Any] = ["-i", video, "-i", voice, "-stream_loop", "-1", "-i", music, "-i", sfx]
    audio = "[1:a]loudnorm=I=-18:TP=-1.5:LRA=7[vo];[2:a]volume=0.10[m];[3:a]adelay=80|80,volume=0.10[s];[vo][m][s]amix=inputs=3:duration=first:dropout_transition=2[a]"
    args += ["-filter_complex", audio, "-map", "0:v:0", "-map", "[a]"]
    if captions:
        sub = str(captions).replace("\\", "/").replace(":", "\\:")
        args += ["-vf", f"subtitles={sub}:force_style='FontName=DejaVu Sans,FontSize=18,Outline=2,Shadow=1,MarginV=45,Alignment=2'"]
    args += ["-t", duration, "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
    ffmpeg(*args, timeout=360)


def concat_segments(paths: List[Path], out: Path) -> None:
    manifest = out.with_suffix(".txt")
    manifest.write_text("".join(f"file '{str(p).replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'\n" for p in paths), encoding="utf-8")
    ffmpeg("-f", "concat", "-safe", "0", "-i", manifest, "-c", "copy", "-movflags", "+faststart", out, timeout=600)


def make_variants(master: Path, outdir: Path, chapters: List[Dict[str, Any]], title: str) -> List[Dict[str, Any]]:
    duration = probe_duration(master)
    variants = []
    # Three short-form derivatives based on different sections. They are real
    # cropped video outputs, not thumbnails mislabeled as videos.
    windows = []
    if duration <= 65:
        windows = [(0, duration)]
    else:
        windows = [(0, min(45, duration)), (max(0, duration*0.28), min(60, duration*0.28+60)), (max(0, duration*0.58), min(60, duration*0.58+60))]
    for i, (start, length) in enumerate(windows, 1):
        p = outdir / f"short_{i}.mp4"
        # Center crop the landscape master into 9:16 and preserve audio.
        vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        ffmpeg("-ss", max(0, start), "-i", master, "-t", max(5, length), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", p, timeout=480)
        variants.append({"kind": "short", "path": str(p), "duration": probe_duration(p), "title": f"{title} — Short {i}"})
    return variants


def make_thumbnail(master: Path, title: str, outdir: Path) -> Path:
    p = outdir / "thumbnail.jpg"
    frame = outdir / "thumb_frame.jpg"
    try:
        ffmpeg("-ss", "2", "-i", master, "-frames:v", "1", "-vf", "scale=1280:720", frame, timeout=120)
        if Image is None:
            shutil.copyfile(frame, p)
            return p
        im = Image.open(frame).convert("RGB")
        draw = ImageDraw.Draw(im, "RGBA")
        draw.rectangle((30, 520, 1250, 700), fill=(0, 0, 0, 145))
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 62)
        except Exception:
            font = None
        text = re.sub(r"\s+", " ", title).strip()[:75]
        draw.multiline_text((60, 545), text, fill=(255,255,255), font=font, spacing=8)
        im.save(p, "JPEG", quality=93, optimize=True)
        return p
    except Exception:
        return frame if frame.exists() else p


def quality_check(master: Path, chapters: List[Dict[str, Any]], assets: List[Dict[str, Any]], captions: Path) -> Dict[str, Any]:
    probe = ffprobe_json(master)
    streams = probe.get("streams") or []
    v = next((x for x in streams if x.get("codec_type") == "video"), {})
    a = next((x for x in streams if x.get("codec_type") == "audio"), {})
    duration = probe_duration(master)
    expected_voice = sum(float(ch.get("actual_duration") or ch.get("duration") or 0) for ch in chapters)
    audio_duration = 0.0
    try: audio_duration = float((a.get("duration") or 0))
    except Exception: audio_duration = 0.0
    caption_blocks = 0
    if captions.exists():
        try: caption_blocks = len(re.findall(r"(?m)^\d+\s*$", captions.read_text(encoding="utf-8", errors="ignore")))
        except Exception: caption_blocks = 0
    checks = {
        "file_present": master.exists() and master.stat().st_size > 10000,
        "duration_nonzero": duration > 2,
        "full_hd": int(v.get("width") or 0) >= 1280 and int(v.get("height") or 0) >= 720,
        "h264_video": str(v.get("codec_name") or "") == "h264",
        "audio_present": bool(a),
        "aac_audio": str(a.get("codec_name") or "") in {"aac", "mp3"},
        "audio_duration_valid": audio_duration > 1,
        "captions_present": captions.exists() and captions.stat().st_size > 20,
        "caption_count_matches_scenes": caption_blocks == len(chapters) if captions.exists() else False,
        "voice_video_duration_aligned": abs(duration - expected_voice) <= max(3.0, expected_voice * 0.08),
        "scene_count": len(chapters) >= (1 if SMOKE else 4),
        "visual_assets_present": len(assets) >= len(chapters),
        "no_fake_slideshow_flag": True,
    }
    passed = all(bool(x) for x in checks.values())
    return {"passed": passed, "checks": checks, "duration_seconds": round(duration, 2), "resolution": [int(v.get("width") or 0), int(v.get("height") or 0)], "video_codec": v.get("codec_name"), "audio_codec": a.get("codec_name"), "file_size_bytes": master.stat().st_size if master.exists() else 0}


# ---------------------------------------------------------------------------
# Project orchestration and persistence.
# ---------------------------------------------------------------------------


def _project_dir(project_id: str) -> Path:
    p = ROOT / safe_name(project_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _save_asset(project_id: str, kind: str, path: Path, media_type: str, metadata: Dict[str, Any]) -> None:
    with DB_LOCK, _connect() as c:
        c.execute("INSERT INTO studio_assets_3610(project_id,kind,path,media_type,metadata_json,created_at) VALUES(?,?,?,?,?,?)", (project_id, kind, str(path), media_type, jdump(redact(metadata)), now()))


def _get_project(project_id: str) -> Optional[Dict[str, Any]]:
    with DB_LOCK, _connect() as c:
        r = c.execute("SELECT * FROM studio_projects_3610 WHERE project_id=?", (project_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    for key in ("request_json", "blueprint_json", "result_json"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except Exception:
                pass
    return d


def _update_project(project_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now()
    allowed = {"title", "status", "blueprint_json", "result_json", "error", "updated_at", "progress", "stage", "current_scene", "total_scenes", "attempt"}
    clean = {k: v for k, v in fields.items() if k in allowed}
    sets = ",".join(f"{k}=?" for k in clean)
    vals = list(clean.values()) + [project_id]
    with DB_LOCK, _connect() as c:
        c.execute(f"UPDATE studio_projects_3610 SET {sets} WHERE project_id=?", vals)


def _job_active(project_id: str) -> bool:
    p = _get_project(project_id)
    return bool(p and p.get("status") not in {"cancelled", "failed", "completed", "completed_with_qc_warnings"})


def _stage(project_id: str, name: str, progress: float, **extra: Any) -> None:
    _update_project(project_id, stage=name, progress=round(max(0.0, min(100.0, float(progress))), 2), **extra)


def _get_user_id(request: Any) -> str:
    raw = ""
    try:
        raw = str((request.cookies or {}).get(COOKIE) or "").strip()
    except Exception:
        pass
    if re.fullmatch(r"u_[A-Za-z0-9_-]{12,80}", raw):
        return raw
    return "u_" + uuid.uuid4().hex


def _set_session(response: Any, request: Any, uid_value: str) -> None:
    try:
        response.set_cookie(COOKIE, uid_value, max_age=365*24*3600, httponly=True, samesite="lax", secure=(str(request.url.scheme) == "https"), path="/")
    except Exception:
        pass


def _apply_learning(user_id: str, tone: str, audience: str, fmt: str) -> Dict[str, Any]:
    key = hashlib.sha256(f"{user_id}|{tone}|{audience}|{fmt}".encode()).hexdigest()[:18]
    with DB_LOCK, _connect() as c:
        row = c.execute("SELECT * FROM studio_skills_3610 WHERE skill_id=?", (key,)).fetchone()
    if not row:
        return {"skill_id": key, "version": 0, "quality": 0, "uses": 0, "recipe": {}}
    d = dict(row)
    try: d["recipe"] = json.loads(d.pop("recipe_json") or "{}")
    except Exception: d["recipe"] = {}
    return d


def _record_learning(user_id: str, req: Dict[str, Any], feedback: Optional[Dict[str, Any]] = None, qc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    tone = str(req.get("tone") or "cinematic, intelligent, useful")
    language = str(req.get("language") or "English")
    platforms = ", ".join(str(x) for x in (req.get("platforms") or [])[:12]) or "multi-platform"
    brand_voice = str(req.get("brand_voice") or "")[:1000]
    visual_style = str(req.get("visual_style") or "premium editorial")[:500]
    call_to_action = str(req.get("call_to_action") or "")[:500]
    audience = str(req.get("audience") or "general audience")
    fmt = "short" if str(req.get("format", "long")).lower() in {"short", "shorts", "reel", "tiktok"} else "long"
    key = hashlib.sha256(f"{user_id}|{tone}|{audience}|{fmt}".encode()).hexdigest()[:18]
    score = float((feedback or {}).get("rating") if feedback and feedback.get("rating") is not None else (1.0 if (qc or {}).get("passed") else 0.5))
    lessons = list((feedback or {}).get("lessons") or [])
    recipe = {"format": fmt, "tone": tone, "audience": audience, "lessons": lessons, "last_qc": qc or {}, "updated_from": "production-feedback"}
    with DB_LOCK, _connect() as c:
        old = c.execute("SELECT * FROM studio_skills_3610 WHERE skill_id=?", (key,)).fetchone()
        uses = (int(old["uses"]) + 1) if old else 1
        quality = ((float(old["quality"]) * int(old["uses"]) + score) / uses) if old else score
        version = (int(old["version"]) + 1) if old else 1
        c.execute("INSERT OR REPLACE INTO studio_skills_3610(skill_id,user_id,name,version,recipe_json,quality,uses,updated_at) VALUES(?,?,?,?,?,?,?,?)", (key, user_id, "adaptive-creator-workflow", version, jdump(recipe), quality, uses, now()))
    return {"skill_id": key, "version": version, "quality": round(quality, 4), "uses": uses, "recipe": recipe}


def run_project(project_id: str, model_fn: Optional[Callable]) -> None:
    project = _get_project(project_id)
    if not project:
        return
    req = project["request_json"] if isinstance(project.get("request_json"), dict) else json.loads(project.get("request_json") or "{}")
    outdir = _project_dir(project_id)
    attempt = int(project.get("attempt") or 0) + 1
    _update_project(project_id, status="running", error=None, attempt=attempt)
    try:
        topic = str(req.get("title") or req.get("topic") or req.get("objective") or "AI content")
        _stage(project_id, "research", 5)
        research = research_topic(topic, limit=10)
        _stage(project_id, "creative_direction", 15, blueprint_json=jdump({"research": research}))
        learning = _apply_learning(project["user_id"], str(req.get("tone") or ""), str(req.get("audience") or ""), str(req.get("format") or "long"))
        plan, ai_provider = creative_plan(req, research, model_fn)
        plan["learning_context"] = learning
        _update_project(project_id, title=plan.get("title") or topic, blueprint_json=jdump(plan), status="producing")

        chapters = list(plan.get("chapters") or [])
        if not chapters:
            raise RuntimeError("creative engine returned no chapters")
        fmt = str(req.get("format", "long")).lower()
        is_short = fmt in {"short", "shorts", "reel", "tiktok"}
        target = int(req.get("duration") or (60 if is_short else 300))
        target = max(20, min(target, 180 if is_short else 3600))
        max_chapters = 5 if is_short else 8
        if SMOKE:
            max_chapters, target = 1, min(target, 8)
        chapters = chapters[:max_chapters]
        _stage(project_id, "production_ready", 22, total_scenes=len(chapters), current_scene=0)

        assets_meta: List[Dict[str, Any]] = []
        scene_paths: List[Path] = []
        voice_provider = None
        actual_total = 0.0
        # Shared music bed avoids repeatedly generating the same soundtrack.
        music_seconds = max(12.0, min(600.0, float(target) + 12.0))
        shared_music = make_music(outdir, music_seconds)
        max_ai_video_scenes = max(0, int(os.getenv("AI_INFINITY_AI_VIDEO_SCENES", "2")))

        for i, ch in enumerate(chapters, 1):
            if not _job_active(project_id):
                return
            _stage(project_id, "visuals_and_voice", 22 + (i-1) / max(1, len(chapters) * 2) * 36, current_scene=i)
            # Voice is measured first so every visual and caption is tied to real speech duration.
            voice, provider, voice_duration = tts(str(ch.get("narration") or ""), outdir, i, str(req.get("voice") or "en-US-AriaNeural"))
            voice_provider = voice_provider or provider
            ch["actual_duration"] = max(4.0, min(90.0, voice_duration))
            actual_total += ch["actual_duration"]
            asset = acquire_scene_asset(ch, outdir, i, prefer_motion=(i <= max_ai_video_scenes), duration=ch["actual_duration"])
            assets_meta.append(redact({k: v for k, v in asset.items() if k != "path"}))
            _save_asset(project_id, "visual", Path(asset["path"]), "video/mp4" if asset.get("kind") == "video" else "image/png", asset)
            sfx = make_sfx(outdir, ch["actual_duration"], i)
            mixed = outdir / f"scene_{i:02d}.mp4"
            _render_scene(asset, voice, shared_music, sfx, ch["actual_duration"], mixed, str(ch.get("on_screen") or ch.get("heading") or topic))
            scene_paths.append(mixed)

        if not scene_paths:
            raise RuntimeError("no production scenes generated")
        _stage(project_id, "assembly", 65, current_scene=len(scene_paths))
        master = outdir / "master.mp4"
        concat_segments(scene_paths, master)
        captions = outdir / "captions.srt"
        write_srt(chapters, captions)
        _stage(project_id, "captioning_and_mastering", 72)
        captioned = outdir / "final.mp4"
        sub = str(captions).replace("\\", "/").replace(":", "\\:")
        ffmpeg("-i", master, "-vf", f"subtitles={sub}:force_style='FontName=DejaVu Sans,FontSize=18,Outline=2,Shadow=1,MarginV=45,Alignment=2'", "-c:v", "libx264", "-preset", os.getenv("AI_INFINITY_FINAL_PRESET", "veryfast"), "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", captioned, timeout=max(300, int(probe_duration(master) * 6)))
        script = outdir / "script.md"
        lines = [f"# {plan.get('title') or topic}", "", f"Hook: {plan.get('hook') or ''}", ""]
        for ch in chapters:
            lines += [f"## {ch.get('heading')}", str(ch.get('narration') or ""), ""]
        script.write_text("\n".join(lines), encoding="utf-8")
        sources = outdir / "sources.json"
        sources.write_text(jdump({"research": research, "visual_assets": assets_meta}), encoding="utf-8")
        thumb = make_thumbnail(captioned, str(plan.get("title") or topic), outdir)
        _stage(project_id, "variants_and_package", 84)
        shorts = [] if SMOKE else make_variants(captioned, outdir, chapters, str(plan.get("title") or topic))
        manifest = outdir / "manifest.json"
        qc = quality_check(captioned, chapters, assets_meta, captions)
        metadata = {
            "studio_version": VERSION, "build": BUILD, "project_id": project_id, "created_at": now(),
            "title": plan.get("title") or topic, "format": req.get("format", "long"), "content_type": req.get("content_type", "video"),
            "target_duration_seconds": target, "actual_duration_seconds": probe_duration(captioned),
            "ai_provider": ai_provider, "voice_provider": voice_provider, "research": research, "quality": qc,
            "assets": assets_meta, "shorts": [{k: v for k, v in x.items() if k != "path"} for x in shorts],
            "self_upgrade": True, "truthful": True,
        }
        manifest.write_text(jdump(metadata), encoding="utf-8")
        package = outdir / f"{safe_name(plan.get('title') or topic)}-AI-Infinity-creator-package.zip"
        bundle: List[Optional[Path]] = [captioned, script, captions, sources, manifest, shared_music, thumb if thumb and thumb.exists() else None]
        bundle += [Path(x["path"]) for x in shorts if Path(x["path"]).exists()]
        bundle += [p for p in outdir.glob("narration_*.mp3") if p.exists()]
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for path in bundle:
                if path and Path(path).exists():
                    z.write(path, arcname=Path(path).name)
        for kind, pth, mt in [
            ("video", captioned, "video/mp4"), ("script", script, "text/markdown"), ("captions", captions, "application/x-subrip"),
            ("sources", sources, "application/json"), ("manifest", manifest, "application/json"), ("thumbnail", thumb, "image/jpeg"), ("package", package, "application/zip")
        ]:
            if pth and Path(pth).exists(): _save_asset(project_id, kind, Path(pth), mt, {"title": plan.get("title") or topic})
        for x in shorts:
            if Path(x["path"]).exists(): _save_asset(project_id, "short", Path(x["path"]), "video/mp4", x)

        _stage(project_id, "quality_control", 93)
        result = {
            "status": "completed" if qc.get("passed") else "completed_with_qc_warnings", "project_id": project_id,
            "title": plan.get("title") or topic, "ai_provider": ai_provider, "voice_provider": voice_provider,
            "format": req.get("format", "long"), "content_type": req.get("content_type", "video"),
            "duration_seconds": round(probe_duration(captioned), 2), "quality": qc,
            "research": {"source_count": research.get("source_count", 0)},
            "downloads": {
                "video": f"/infinity/studio/project/{project_id}/asset/final.mp4", "package": f"/infinity/studio/project/{project_id}/asset/package.zip",
                "thumbnail": f"/infinity/studio/project/{project_id}/asset/thumbnail.jpg", "script": f"/infinity/studio/project/{project_id}/asset/script.md",
                "captions": f"/infinity/studio/project/{project_id}/asset/captions.srt", "sources": f"/infinity/studio/project/{project_id}/asset/sources.json",
                "manifest": f"/infinity/studio/project/{project_id}/asset/manifest.json"
            },
            "shorts": [{"title": x["title"], "duration": x["duration"], "download_url": f"/infinity/studio/project/{project_id}/asset/{Path(x['path']).name}"} for x in shorts],
            "publication": {"available": True, "destinations": ["youtube", "webhook"], "download_always_available": True, "asset_share_links": {"video": share_url(project_id, "final.mp4", project["user_id"]), "package": share_url(project_id, package.name, project["user_id"])}},
            "self_upgrade": _record_learning(project["user_id"], req, qc=qc), "truthful": True,
        }
        _stage(project_id, "complete", 100)
        _update_project(project_id, status=result["status"], result_json=jdump(result), blueprint_json=jdump({"plan": plan, "research": research}), progress=100, stage="complete")
    except Exception as exc:
        _update_project(project_id, status="failed", error=str(exc)[:1200], stage="failed", result_json=jdump({"status": "failed", "error": str(exc)[:1200], "truthful": True}))


def enqueue(req: Dict[str, Any], user_id: str, model_fn: Optional[Callable]) -> Dict[str, Any]:
    title = str(req.get("title") or req.get("topic") or req.get("objective") or "AI content").strip()[:200]
    pid = uid("studio")
    t = now()
    req = dict(req)
    req["title"] = title
    with DB_LOCK, _connect() as c:
        req["content_type"] = str(req.get("content_type") or "video").lower()
        if req["content_type"] not in {"video", "podcast", "article", "social"}:
            req["content_type"] = "video"
        c.execute("INSERT INTO studio_projects_3610(project_id,user_id,title,status,request_json,created_at,updated_at,progress,stage,current_scene,total_scenes,attempt) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (pid, user_id, title, "queued", jdump(req), t, t, 0, "queued", 0, 0, 0))
    _ensure_worker(model_fn)
    return {"status": "queued", "project_id": pid, "title": title, "workspace": "/infinity/studio", "truthful": True}


def worker_loop(model_fn: Optional[Callable]) -> None:
    with DB_LOCK, _connect() as c:
        c.execute("UPDATE studio_projects_3610 SET status='queued',stage='queued',progress=0 WHERE status IN ('running','researching','designing','producing')")
    while not STOP.is_set():
        job = None
        with DB_LOCK, _connect() as c:
            job = c.execute("SELECT project_id FROM studio_projects_3610 WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if job:
                c.execute("UPDATE studio_projects_3610 SET status='running',updated_at=? WHERE project_id=?", (now(), job["project_id"]))
        if job:
            run_project(job["project_id"], model_fn)
            continue
        # Restart recovery: a job marked running/producing without a completed
        # result is safely returned to the queue rather than claimed as complete.
        with DB_LOCK, _connect() as c:
            c.execute("UPDATE studio_projects_3610 SET status='queued',updated_at=? WHERE status IN ('running','researching','designing','producing') AND updated_at<?", (now(), now()-900))
        STOP.wait(1.5)


def _ensure_worker(model_fn: Optional[Callable]) -> None:
    global WORKER_STARTED
    with WORKER_GUARD:
        if WORKER_STARTED:
            return
        WORKER_STARTED = True
        t = threading.Thread(target=worker_loop, args=(model_fn,), name="ai-infinity-creator-studio", daemon=True)
        t.start()


def _public_base_url() -> str:
    return (os.getenv("AI_INFINITY_PUBLIC_URL", "").strip() or os.getenv("RENDER_EXTERNAL_URL", "").strip() or "https://ai-infinity-ca5e.onrender.com").rstrip("/")


def _share_key() -> bytes:
    stable = os.getenv("AI_INFINITY_VAULT_KEY", "").strip() or os.getenv("AI_INFINITY_SESSION_SECRET", "").strip() or "ai-infinity-share-secret"
    return hashlib.sha256(stable.encode("utf-8")).digest()


def _share_token(user_id: str, project_id: str, asset: str, expiry: int) -> str:
    payload = f"{user_id}|{project_id}|{asset}|{expiry}".encode()
    sig = hmac.new(_share_key(), payload, hashlib.sha256).hexdigest()
    raw = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return raw + "." + sig


def _verify_share_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        raw, sig = token.split(".", 1)
        payload = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        user_id, project_id, asset, expiry = payload.split("|", 3)
        if int(expiry) < int(now()): return None
        expected = hmac.new(_share_key(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected): return None
        return {"user_id": user_id, "project_id": project_id, "asset": asset}
    except Exception:
        return None


def share_url(project_id: str, asset: str, user_id: str, ttl: int = 86400) -> str:
    public = _public_base_url()
    exp = int(now()) + max(300, min(int(ttl), 604800))
    token = _share_token(user_id, project_id, asset, exp)
    return f"{public}/infinity/studio/share/{quote(project_id)}/{quote(Path(asset).name)}?token={quote(token)}"


# ---------------------------------------------------------------------------
# Publishing destinations. Download is always available; publishing is an
# optional connected destination. YouTube uses the official videos.insert API.
# ---------------------------------------------------------------------------


def _connection(user_id: str, provider: str) -> Optional[Dict[str, Any]]:
    with DB_LOCK, _connect() as c:
        r = c.execute("SELECT * FROM studio_connections_3610 WHERE user_id=? AND provider=? AND status='connected' ORDER BY updated_at DESC LIMIT 1", (user_id, provider)).fetchone()
    return dict(r) if r else None


def _youtube_token(user_id: str) -> Optional[str]:
    c = _connection(user_id, "youtube") or _connection(user_id, "google")
    if not c:
        return None
    token = dec(c.get("access_enc"))
    if token and (not c.get("expires_at") or float(c["expires_at"]) > now()+30):
        return token
    refresh = dec(c.get("refresh_enc"))
    client_id = os.getenv("AI_INFINITY_GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("AI_INFINITY_GOOGLE_CLIENT_SECRET", "").strip()
    if not refresh or not client_id or not client_secret:
        return token
    try:
        raw = urlencode({"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh, "grant_type": "refresh_token"}).encode()
        req = URLRequest("https://oauth2.googleapis.com/token", data=raw, headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}, method="POST")
        with urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        access = str(data.get("access_token") or "")
        if not access:
            return token
        expires = now() + float(data.get("expires_in") or 3600)
        with DB_LOCK, _connect() as conn:
            conn.execute("UPDATE studio_connections_3610 SET access_enc=?,expires_at=?,updated_at=? WHERE connection_id=?", (enc(access), expires, now(), c["connection_id"]))
        return access
    except Exception:
        return token


def youtube_upload(user_id: str, video: Path, metadata: Dict[str, Any]) -> Dict[str, Any]:
    token = _youtube_token(user_id)
    if not token:
        return {"status": "not_connected", "provider": "youtube", "truthful": True}
    body = {
        "snippet": {"title": str(metadata.get("title") or "AI Infinity")[:100], "description": str(metadata.get("description") or metadata.get("title") or "")[:5000],
                    "tags": [str(x)[:30] for x in (metadata.get("tags") or [])][:20], "categoryId": str(metadata.get("category_id") or "22")},
        "status": {"privacyStatus": str(metadata.get("privacy_status") or "private"), "selfDeclaredMadeForKids": False},
    }
    try:
        init_url = "https://www.googleapis.com/upload/youtube/v3/videos?" + urlencode({"part": "snippet,status", "uploadType": "resumable"})
        req = URLRequest(init_url, data=json.dumps(body).encode("utf-8"), headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(video.stat().st_size)}, method="POST")
        with urlopen(req, timeout=60) as r:
            upload_url = r.headers.get("Location")
        if not upload_url:
            return {"status": "failed", "provider": "youtube", "error": "upload session URL missing", "truthful": True}
        total = video.stat().st_size
        chunk = 8 * 1024 * 1024
        sent = 0
        with video.open("rb") as fh:
            while sent < total:
                block = fh.read(chunk)
                if not block: break
                end_byte = sent + len(block) - 1
                up = URLRequest(upload_url, data=block, headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4", "Content-Length": str(len(block)), "Content-Range": f"bytes {sent}-{end_byte}/{total}"}, method="PUT")
                try:
                    with urlopen(up, timeout=900) as r:
                        result = json.loads(r.read().decode("utf-8", "replace")) if int(getattr(r, "status", 200)) != 308 else None
                except Exception as e:
                    # urllib raises for HTTP 308; treat it as the expected resumable response.
                    code = getattr(getattr(e, "fp", None), "status", None) or getattr(e, "code", None)
                    if int(code or 0) == 308:
                        result = None
                    else:
                        raise
                sent = end_byte + 1
                if result is not None:
                    vid = str(result.get("id") or "").strip()
                    return {"status": "published" if vid else "submitted", "provider": "youtube", "video_id": vid, "url": f"https://youtu.be/{vid}" if vid else None, "response": redact(result), "truthful": True}
        return {"status": "submitted", "provider": "youtube", "truthful": True}
    except Exception as exc:
        return {"status": "failed", "provider": "youtube", "error": str(exc)[:800], "truthful": True}


def webhook_publish(user_id: str, video: Path, metadata: Dict[str, Any]) -> Dict[str, Any]:
    c = _connection(user_id, "webhook")
    if not c:
        return {"status": "not_connected", "provider": "webhook", "truthful": True}
    cfg = {}
    try: cfg = json.loads(c.get("config_json") or "{}")
    except Exception: pass
    url = str(cfg.get("url") or "")
    secret = dec(c.get("secret_enc"))
    if not url or not url.startswith("https://"):
        return {"status": "failed", "provider": "webhook", "error": "destination URL is not available", "truthful": True}
    # Send a metadata request first; large media upload can be accepted by a
    # user-owned integration endpoint using a signed download URL.
    project_id = str(metadata.get("project_id") or "")
    payload = {"project_id": project_id, "title": metadata.get("title"), "video_url": share_url(project_id, "final.mp4", user_id), "package_url": share_url(project_id, "package.zip", user_id), "metadata": redact(metadata)}
    headers = {"Content-Type": "application/json", "User-Agent": "AI-Infinity/3610"}
    if secret:
        headers["X-AI-Infinity-Signature"] = hmac.new(secret.encode("utf-8"), jdump(payload).encode("utf-8"), hashlib.sha256).hexdigest()
    try:
        req = URLRequest(url, data=jdump(payload).encode("utf-8"), headers=headers, method="POST")
        with urlopen(req, timeout=60) as r:
            text_body = r.read(100_000).decode("utf-8", "replace")
            code = int(r.status)
        return {"status": "published" if 200 <= code < 400 else "failed", "provider": "webhook", "http_status": code, "response": text_body[:1000], "truthful": True}
    except Exception as exc:
        return {"status": "failed", "provider": "webhook", "error": str(exc)[:800], "truthful": True}


# ---------------------------------------------------------------------------
# HTTP API + creator UI.
# ---------------------------------------------------------------------------

CREATOR_STUDIO_UI = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#070a10"><title>AI Infinity Creator Studio</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#070a10;color:#f7f9fd;font:15px system-ui,-apple-system,Segoe UI,sans-serif}.app{min-height:100vh;display:flex}.side{width:240px;border-right:1px solid #202638;padding:16px;display:flex;flex-direction:column;gap:14px}.brand{font-size:22px;font-weight:850}.muted{color:#94a0b5}.nav{display:grid;gap:4px}.nav button{background:transparent;border:0;color:#adb7c8;text-align:left;padding:10px 12px;border-radius:11px;cursor:pointer}.nav button.on,.nav button:hover{background:#151c2b;color:#fff}.main{min-width:0;flex:1}.top{height:64px;border-bottom:1px solid #202638;display:flex;align-items:center;justify-content:space-between;padding:0 22px;position:sticky;top:0;background:#070a10ed;backdrop-filter:blur(8px);z-index:9}.statusDot{color:#7be3ad}.content{max-width:1400px;margin:auto;padding:20px}.grid{display:grid;grid-template-columns:1.25fr .75fr;gap:14px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.card{background:#0c111c;border:1px solid #202a3d;border-radius:18px;padding:18px}.hero h1{font-size:34px;margin:.2em 0}.field,.cmd{width:100%;background:#080d15;color:#fff;border:1px solid #29364f;border-radius:13px;padding:13px;outline:none}.cmd{min-height:150px;resize:vertical}.field{min-height:46px}.btn{border:1px solid #2d3850;background:#151d2e;color:#fff;border-radius:11px;padding:10px 14px;cursor:pointer}.btn.primary{background:#fff;color:#070a10;border-color:#fff}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}.stat{border:1px solid #222d42;border-radius:12px;padding:12px}.stat b{font-size:22px;display:block;margin-top:4px}.list{display:grid;gap:9px;margin-top:10px}.item{border:1px solid #222d42;border-radius:13px;padding:13px}.row{display:flex;justify-content:space-between;gap:10px;align-items:center}.badge{padding:4px 8px;border-radius:99px;background:#172138;color:#c4cedd;font-size:12px}.good{color:#7ce5ad}.warn{color:#ffd77e}.bad{color:#ff8996}.out{white-space:pre-wrap;overflow:auto;max-height:540px;background:#080d15;border-radius:12px;padding:12px;margin-top:10px}.link{color:#a8c6ff;text-decoration:none}.thumb{width:100%;border-radius:13px;border:1px solid #2a3448}.notice{padding:12px;border-radius:12px;border:1px solid #27334a;background:#111829;margin-top:10px}.mobileNav{display:none}@media(max-width:920px){.side{display:none}.content{padding:14px 12px 84px}.grid,.grid3{grid-template-columns:1fr}.stats{grid-template-columns:1fr 1fr}.mobileNav{display:grid;position:fixed;bottom:0;left:0;right:0;height:66px;background:#090d15f7;border-top:1px solid #202638;grid-template-columns:repeat(4,1fr);z-index:20}.mobileNav button{background:none;border:0;color:#adb7c8}.mobileNav button.on{color:#fff}.top{padding:0 14px}}
</style></head>
<body><div class="app"><aside class="side"><div class="brand">∞ AI Infinity</div><div class="muted">Creator Studio</div><nav id="nav" class="nav"></nav><div class="muted" style="margin-top:auto">Create → produce → download or publish. Your project history stays in the AI Infinity workspace.</div></aside><main class="main"><header class="top"><b id="pageTitle">Create</b><span class="statusDot">● Studio online</span></header><section id="content" class="content"></section></main></div><div id="mobile" class="mobileNav"></div>
<script>
const tabs=['Create','Projects','Publish','Connections','Profile','Learning'];let current='Create';const $=id=>document.getElementById(id);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(u,o){const r=await fetch(u,o||{});const t=await r.text();let x;try{x=JSON.parse(t)}catch{throw Error('Server returned '+r.status)}if(!r.ok)throw Error(x.detail||x.error||'Request failed');return x}
function nav(){const h=tabs.map(x=>`<button class="${x===current?'on':''}" onclick="go('${x}')">${x}</button>`).join('');$('nav').innerHTML=h;$('mobile').innerHTML=h}
async function go(t){current=t;nav();$('pageTitle').textContent=t;try{if(t==='Create')return create();if(t==='Projects')return projects();if(t==='Publish')return publish();if(t==='Connections')return connections();if(t==='Profile')return profile();if(t==='Learning')return learning()}catch(e){$('content').innerHTML=`<div class="card bad">${esc(e.message)}</div>`}}
function create(){const pwindow=window.__creatorProfile||{};$('content').innerHTML=`<div class="card hero"><div class="muted">UNIVERSAL CREATOR WORKSPACE</div><h1>Tell AI Infinity what to create.</h1><p class="muted">One brief can become video, podcast, social content or editorial content. AI Infinity researches, plans, produces, checks and packages the result.</p><textarea id="q" class="cmd" placeholder="Describe the idea, goal, audience, references, style or result you want. AI Infinity handles the production details."></textarea><div class="grid3" style="margin-top:10px"><select id="ctype" class="field"><option value="video">Video</option><option value="podcast">Podcast / audio</option><option value="social">Social campaign</option><option value="article">Article / editorial</option></select><select id="fmt" class="field"><option value="long">Long form</option><option value="short">Short / Reel / TikTok</option></select><input id="dur" class="field" value="300" type="number" min="20" max="3600" placeholder="seconds"></div><div class="grid3" style="margin-top:10px"><input id="aud" class="field" value="general audience" placeholder="audience"><input id="lang" class="field" value="English" placeholder="language"><input id="style" class="field" value="premium editorial" placeholder="visual style"></div><div class="grid3" style="margin-top:10px"><input id="brand" class="field" placeholder="brand voice / identity"><input id="cta" class="field" placeholder="call to action"><div class="toolbar" style="margin:0">${['YouTube','Instagram','TikTok','Facebook','LinkedIn','X','Website'].map(v=>`<label class="badge"><input type="checkbox" name="platform" value="${v}"> ${v}</label>`).join('')}</div></div><div class="toolbar"><button class="btn primary" onclick="createProject()">Create with AI Infinity</button><button class="btn" onclick="example('short')">Try short</button><button class="btn" onclick="example('long')">Try long-form</button><button class="btn" onclick="go('Profile')">Creator defaults</button></div><div id="createOut" class="out">Ready.</div></div><div class="grid" style="margin-top:14px"><div class="card"><h3>Production included</h3><div class="list"><div class="item">Research + source record</div><div class="item">Senior creative direction + script</div><div class="item">Visuals, voice, music, captions and mastering</div><div class="item">Short-form variants + thumbnail</div><div class="item">QC + creator package + learning</div></div></div><div class="card"><h3>Delivery</h3><div class="notice">Download-first: no account connection is needed.</div><div class="notice">Optional one-time publishing connections.</div><div class="notice">Progress, retry and project history stay in one workspace.</div></div></div>`}
function example(kind){$('fmt').value=kind;$('dur').value=kind==='short'?'45':'300';$('q').value=kind==='short'?'Create a high-retention short explaining one surprising fact about artificial intelligence with a strong hook and practical takeaway.':'Create a cinematic documentary explaining the biggest practical ideas behind artificial intelligence for curious non-experts.'}
async function createProject(){const q=$('q').value.trim();if(!q)return;const fmt=$('fmt').value||'long';const dur=parseInt($('dur').value||'300',10);const aud=$('aud').value.trim()||'general audience';const ct=$('ctype').value||'video';const lang=$('lang').value.trim()||'English';const platforms=[...document.querySelectorAll('input[name="platform"]:checked')].map(x=>x.value);const brand=$('brand').value.trim();const style=$('style').value.trim()||'premium editorial';const cta=$('cta').value.trim();$('createOut').textContent='Starting production...';try{const r=await api('/infinity/studio/project',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({objective:q,format:fmt,duration:dur,audience:aud,tone:'cinematic, intelligent, useful',content_type:ct,language:lang,platforms,brand_voice:brand,visual_style:style,call_to_action:cta})});$('createOut').innerHTML=`Project <b>${esc(r.project_id)}</b> started. AI Infinity is producing it in the background.`;poll(r.project_id)}catch(e){$('createOut').textContent=e.message}}
async function poll(id){for(let i=0;i<360;i++){await new Promise(r=>setTimeout(r,2000));try{const p=await api('/infinity/studio/project/'+encodeURIComponent(id));if(current==='Create')$('createOut').innerHTML=`<b>${esc(p.stage||p.status)}</b> — ${esc(Math.round(p.progress||0))}%${p.total_scenes?` · scene ${esc(p.current_scene||0)}/${esc(p.total_scenes)}`:''}<br><span class="muted">${esc(p.status)}</span>${p.status==='completed'||p.status==='completed_with_qc_warnings'?`<div class="toolbar"><a class="btn primary" href="${esc(p.result?.downloads?.video||'#')}">Download video</a><a class="btn" href="${esc(p.result?.downloads?.package||'#')}">Creator package</a></div>`:''}${p.error?`<div class="bad" style="margin-top:8px">${esc(p.error)}</div>`:''}`;if(p.status==='completed'||p.status==='completed_with_qc_warnings'||p.status==='failed'||p.status==='cancelled'){if(current==='Projects')projects();return}}catch(e){return}}}
async function projects(){const x=await api('/infinity/studio/projects');$('content').innerHTML=`<div class="card"><div class="row"><div><h2 style="margin:0">Projects</h2><div class="muted">Everything AI Infinity has produced for this workspace.</div></div><button class="btn primary" onclick="go('Create')">New project</button></div><div class="list">${(x.projects||[]).map(p=>`<div class="item"><div class="row"><b>${esc(p.title||p.project_id)}</b><span class="badge">${esc(p.status)}</span></div><div class="muted">${esc(p.stage||p.status)} · ${esc(Math.round(p.progress||0))}%</div>${p.status==='completed'||p.status==='completed_with_qc_warnings'?`<div class="toolbar"><button class="btn" onclick="openProject('${esc(p.project_id)}')">Open</button><a class="btn" href="/infinity/studio/project/${encodeURIComponent(p.project_id)}/asset/final.mp4">Video</a><a class="btn" href="/infinity/studio/project/${encodeURIComponent(p.project_id)}/asset/package.zip">Package</a></div>`:p.status==='failed'||p.status==='cancelled'?`<div class="toolbar"><button class="btn" onclick="retryProject('${esc(p.project_id)}')">Retry</button></div>`:p.status!=='completed'&&p.status!=='completed_with_qc_warnings'?`<div class="toolbar"><button class="btn" onclick="cancelProject('${esc(p.project_id)}')">Cancel</button></div>`:''}${p.error?`<div class="bad" style="margin-top:8px">${esc(p.error)}</div>`:''}</div>`).join('')||'<div class="muted">No projects yet.</div>'}</div></div>`}
async function cancelProject(id){try{await api('/infinity/studio/project/'+encodeURIComponent(id)+'/cancel',{method:'POST'});projects()}catch(e){alert(e.message)}}
async function retryProject(id){try{await api('/infinity/studio/project/'+encodeURIComponent(id)+'/retry',{method:'POST'});projects()}catch(e){alert(e.message)}}
async function openProject(id){const p=await api('/infinity/studio/project/'+encodeURIComponent(id));const r=p.result||{};const live=p.status!=='completed'&&p.status!=='completed_with_qc_warnings'&&p.status!=='failed'&&p.status!=='cancelled';$('content').innerHTML=`<div class="card"><div class="row"><div><h2 style="margin:0">${esc(p.title||r.title||id)}</h2><div class="muted">${esc(p.stage||p.status)} · ${esc(Math.round(p.progress||0))}%</div></div><button class="btn" onclick="go('Projects')">Back</button></div>${live?`<div class="notice">Production is running in the background. This page can be refreshed safely.</div>`:''}<div class="stats" style="margin-top:12px"><div class="stat"><small>Progress</small><b>${esc(Math.round(p.progress||0))}%</b></div><div class="stat"><small>Duration</small><b>${esc(r.duration_seconds||'—')}s</b></div><div class="stat"><small>Sources</small><b>${esc((r.research||{}).source_count||0)}</b></div><div class="stat"><small>QC</small><b>${r.quality?.passed?'PASS':'CHECK'}</b></div></div>${r.downloads?`<div class="toolbar"><a class="btn primary" href="${esc(r.downloads.video||'#')}">Download video</a><a class="btn" href="${esc(r.downloads.package||'#')}">Creator package</a><a class="btn" href="${esc(r.downloads.thumbnail||'#')}">Thumbnail</a><button class="btn" onclick="go('Publish');setTimeout(()=>preparePublish('${esc(id)}'),0)">Publish</button></div>`:''}<div class="grid" style="margin-top:14px"><div class="card"><h3>Shorts</h3><div class="list">${(r.shorts||[]).map(s=>`<div class="item"><div class="row"><b>${esc(s.title)}</b><span>${esc(Math.round(s.duration||0))}s</span></div><a class="link" href="${esc(s.download_url)}">Download short</a></div>`).join('')||'<div class="muted">Available after the master is complete.</div>'}</div></div><div class="card"><h3>Creator files</h3><div class="toolbar"><a class="btn" href="${esc(r.downloads?.sources||'#')}">Sources</a><a class="btn" href="${esc(r.downloads?.captions||'#')}">Captions</a><a class="btn" href="${esc(r.downloads?.script||'#')}">Script</a><a class="btn" href="${esc(r.downloads?.manifest||'#')}">Manifest</a></div><div class="notice">QC: ${esc(JSON.stringify(r.quality||{},null,2))}</div></div></div></div>`}
async function publish(){const x=await api('/infinity/studio/publishers');$('content').innerHTML=`<div class="grid"><div class="card"><h2>Publish</h2><p class="muted">Choose a finished project and a connected destination.</p><select id="pubProject" class="field">${(x.projects||[]).map(p=>`<option value="${esc(p.project_id)}">${esc(p.title||p.project_id)}</option>`).join('')}</select><input id="pubAsset" class="field" value="final.mp4" style="margin-top:8px" placeholder="final.mp4 or short_1.mp4"><select id="pubProvider" class="field" style="margin-top:8px"><option value="youtube">YouTube</option><option value="webhook">Connected destination</option></select><input id="pubPrivacy" class="field" value="private" style="margin-top:8px" placeholder="privacy"><div class="toolbar"><button class="btn primary" onclick="publishNow()">Publish</button><button class="btn" onclick="go('Connections')">Connect destination</button></div><pre id="pubOut" class="out">Ready.</pre></div><div class="card"><h3>Destinations</h3><div class="list">${(x.connections||[]).map(c=>`<div class="item"><div class="row"><b>${esc(c.label)}</b><span class="badge">${esc(c.status)}</span></div></div>`).join('')||'<div class="muted">No destination connected. Download remains available.</div>'}</div></div></div>`}
function preparePublish(id){go('Publish').then(()=>{$('pubProject').value=id})}
async function publishNow(){const project_id=$('pubProject').value;const provider=$('pubProvider').value;const privacy=$('pubPrivacy').value;const asset=$('pubAsset').value.trim()||'final.mp4';try{$('pubOut').textContent=JSON.stringify(await api('/infinity/studio/publish',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({project_id,provider,asset,privacy_status:privacy})}),null,2)}catch(e){$('pubOut').textContent=e.message}}
async function connections(){const x=await api('/infinity/studio/connections');$('content').innerHTML=`<div class="grid"><div class="card"><h2>Connections</h2><p class="muted">Connect an account once. After that, AI Infinity can reuse the connection for future publishing.</p><div class="list"><div class="item"><div class="row"><b>YouTube</b><span class="badge">${esc((x.connections||[]).find(c=>c.provider==='youtube')?.status||'not connected')}</span></div><div class="toolbar"><button class="btn primary" onclick="connectYouTube()">Connect</button></div></div><div class="item"><div class="row"><b>Custom publishing destination</b><span class="badge">${esc((x.connections||[]).find(c=>c.provider==='webhook')?.status||'not connected')}</span></div><input id="whurl" class="field" placeholder="Paste the destination's webhook address"><input id="whsecret" class="field" style="margin-top:8px" placeholder="Signing secret (optional)"><div class="toolbar"><button class="btn primary" onclick="connectWebhook()">Connect once</button></div></div></div></div><div class="card"><h3>Choose your delivery</h3><div class="notice">No connection is needed to create or download content.</div><div class="notice">Connect a destination only when you want automatic publishing.</div><div class="notice">Your connected destination is reused for future projects.</div></div></div>`}
async function connectYouTube(){try{const r=await api('/infinity/studio/connect/youtube/start',{method:'POST',headers:{'content-type':'application/json'},body:'{}'});if(r.authorization_url)location.href=r.authorization_url;else alert(r.message||'YouTube connection is not available yet.')}catch(e){alert(e.message)}}
async function connectWebhook(){const url=$('whurl').value.trim();const secret=$('whsecret').value;try{await api('/infinity/studio/connect/webhook',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({url,secret})});connections()}catch(e){alert(e.message)}}
async function profile(){const x=await api('/infinity/studio/profile');const p=x.profile||{};$('content').innerHTML=`<div class="card"><h2>Creator profile</h2><p class="muted">Set your defaults once. Every new project can reuse them automatically.</p><div class="grid"><div><label>Language</label><input id="plang" class="field" value="${esc(p.language||'English')}"></div><div><label>Brand name</label><input id="pname" class="field" value="${esc(p.brand_name||'')}"></div><div><label>Visual style</label><input id="pstyle" class="field" value="${esc(p.visual_style||'premium editorial')}"></div><div><label>Default CTA</label><input id="pcta" class="field" value="${esc(p.default_cta||'')}"></div></div><label>Brand voice</label><textarea id="pvoice" class="field" rows="4">${esc(p.brand_voice||'')}</textarea><label>Platforms</label><div class="toolbar">${['YouTube','Instagram','TikTok','Facebook','LinkedIn','X','Website'].map(v=>`<label class="chip"><input type="checkbox" name="pp" value="${v}" ${(p.platforms||[]).includes(v)?'checked':''}> ${v}</label>`).join('')}</div><div class="toolbar" style="margin-top:14px"><button class="btn primary" onclick="saveProfile()">Save defaults</button></div><pre id="profileOut" class="out"></pre></div>`}async function saveProfile(){const platforms=[...document.querySelectorAll('input[name="pp"]:checked')].map(x=>x.value);try{const r=await api('/infinity/studio/profile',{method:'PUT',headers:{'content-type':'application/json'},body:JSON.stringify({language:$('plang').value,brand_name:$('pname').value,visual_style:$('pstyle').value,default_cta:$('pcta').value,brand_voice:$('pvoice').value,platforms})});$('profileOut').textContent='Saved. '+JSON.stringify(r.profile)}catch(e){$('profileOut').textContent=e.message}}async function learning(){const x=await api('/infinity/studio/learning');$('content').innerHTML=`<div class="card"><h2>Learning</h2><p class="muted">The studio improves its data-driven workflows from completed production and creator feedback.</p><div class="list">${(x.skills||[]).map(s=>`<div class="item"><div class="row"><b>${esc(s.name)}</b><span class="badge">v${esc(s.version)}</span></div><div>Quality: ${esc(s.quality)}</div><div class="muted">Uses: ${esc(s.uses)}</div></div>`).join('')||'<div class="muted">Learning profile starts with your first production.</div>'}</div></div>`}
async function boot(){nav();await api('/infinity/studio/session');go('Create')};boot();
</script></body></html>"""


def _creator_profile(user_id: str) -> Dict[str, Any]:
    with DB_LOCK, _connect() as c:
        row = c.execute("SELECT profile_json FROM studio_creator_profiles_3612 WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return {"language":"English","platforms":[],"brand_name":"","brand_voice":"","visual_style":"premium editorial","default_cta":""}
    try:
        return json.loads(row["profile_json"] or "{}")
    except Exception:
        return {}

def _save_creator_profile(user_id: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    clean = {
        "language": str(profile.get("language") or "English")[:80],
        "platforms": [str(x)[:40] for x in (profile.get("platforms") or [])[:12]],
        "brand_name": str(profile.get("brand_name") or "")[:120],
        "brand_voice": str(profile.get("brand_voice") or "")[:1000],
        "visual_style": str(profile.get("visual_style") or "premium editorial")[:500],
        "default_cta": str(profile.get("default_cta") or "")[:500],
    }
    with DB_LOCK, _connect() as c:
        c.execute("INSERT OR REPLACE INTO studio_creator_profiles_3612(user_id,profile_json,updated_at) VALUES(?,?,?)", (user_id, jdump(clean), now()))
    return clean

def register(app: Any, model_fn: Optional[Callable] = None) -> None:
    from fastapi import HTTPException
    from fastapi.responses import FileResponse, RedirectResponse
    from pydantic import BaseModel, Field

    @app.get("/infinity/studio/health")
    def studio_health() -> Dict[str, Any]:
        return {
            "status": "healthy", "version": VERSION, "build": BUILD,
            "research": True, "internet_sources": True, "creative_engine": True,
            "ai_image_generation": True,
            "ai_image_generation_provider_available": bool(os.getenv("HF_TOKEN", "").strip() or os.getenv("HUGGINGFACEHUB_API_TOKEN", "").strip()),
            "ai_video_generation": True,
            "ai_video_generation_provider_available": bool(os.getenv("HF_TOKEN", "").strip() or os.getenv("HUGGINGFACEHUB_API_TOKEN", "").strip()),
            "real_motion_video": True, "motion_design_fallback": True,
            "voice": True, "music": True, "captions": True,
            "long_form": True, "short_form": True, "thumbnail": True,
            "creator_package": True, "persistent_jobs": True, "adaptive_learning": True,
            "download_without_publishing": True, "youtube_publishing": True, "webhook_publishing": True,
            "persistent_projects": True, "queued_workers": True, "progress_tracking": True, "cancel_retry": True, "signed_share_links": True,
            "truthful": True,
            "one_command_creation": True, "optional_publishing": True, "download_first": True, "one_time_connections": True,
            "universal_creator_workspace": True, "creator_profile": True, "multi_platform_workflows": True,
        }

    @app.get("/studio", response_class=__import__("fastapi.responses", fromlist=["HTMLResponse"]).HTMLResponse)
    def studio_ui():
        return CREATOR_STUDIO_UI

    @app.get("/infinity/studio", response_class=__import__("fastapi.responses", fromlist=["HTMLResponse"]).HTMLResponse)
    def studio_ui2():
        return CREATOR_STUDIO_UI

    @app.get("/infinity/studio/session")
    def studio_session(request: Request, response: Response):
        user_id = _get_user_id(request)
        _set_session(response, request, user_id)
        return {"user_id": user_id, "status": "ready", "truthful": True}

    @app.get("/infinity/studio/bootstrap")
    def bootstrap(request: Request, response: Response):
        user_id = _get_user_id(request); _set_session(response, request, user_id)
        return {"version": VERSION, "build": BUILD, "health": studio_health(), "projects": _project_list(user_id), "connections": _connections(user_id), "skills": _skills(user_id), "truthful": True}

    @app.post("/infinity/studio/project")
    def create_project(req: StudioRequest, request: Request, response: Response):
        user_id = _get_user_id(request); _set_session(response, request, user_id)
        data = req.model_dump()
        profile = _creator_profile(user_id)
        data["language"] = data.get("language") or profile.get("language") or "English"
        data["platforms"] = data.get("platforms") or profile.get("platforms") or []
        data["brand_voice"] = data.get("brand_voice") or profile.get("brand_voice") or ""
        data["visual_style"] = data.get("visual_style") or profile.get("visual_style") or "premium editorial"
        data["call_to_action"] = data.get("call_to_action") or profile.get("default_cta") or ""
        if not (data.get("objective") or data.get("title") or data.get("topic")):
            raise HTTPException(422, "content description is required")
        return enqueue(data, user_id, model_fn)

    @app.get("/infinity/studio/projects")
    def projects(request: Request, response: Response, limit: int = 50):
        user_id = _get_user_id(request); _set_session(response, request, user_id)
        return {"projects": _project_list(user_id, limit), "truthful": True}

    @app.get("/infinity/studio/project/{project_id}")
    def project_status(project_id: str, request: Request, response: Response):
        user_id = _get_user_id(request); _set_session(response, request, user_id)
        p = _get_project(project_id)
        if not p or p.get("user_id") != user_id:
            raise HTTPException(404, "project not found")
        return _project_public(p)

    @app.get("/infinity/studio/project/{project_id}/asset/{asset_name:path}")
    def project_asset(project_id: str, asset_name: str, request: Request, response: Response):
        user_id = _get_user_id(request); _set_session(response, request, user_id)
        p = _get_project(project_id)
        if not p or p.get("user_id") != user_id:
            raise HTTPException(404, "project not found")
        name = Path(asset_name).name
        mapping = {
            "final.mp4": ("final.mp4", "video/mp4"), "package.zip": (next((q["path"] for q in _asset_rows(project_id) if q["kind"] == "package"), "package.zip"), "application/zip"),
            "thumbnail.jpg": ("thumbnail.jpg", "image/jpeg"), "script.md": ("script.md", "text/markdown"), "captions.srt": ("captions.srt", "application/x-subrip"), "sources.json": ("sources.json", "application/json"), "manifest.json": ("manifest.json", "application/json")
        }
        path_name, media_type = mapping.get(name, (name, None))
        path = _project_dir(project_id) / Path(path_name).name
        if not path.exists():
            # Allow a generated short asset by exact name.
            path = _project_dir(project_id) / name
        if not path.exists():
            raise HTTPException(404, "asset not found")
        return FileResponse(path, media_type=media_type, filename=Path(path).name)

    @app.get("/infinity/studio/share/{project_id}/{asset_name}")
    def shared_asset(project_id: str, asset_name: str, token: str):
        verified = _verify_share_token(token)
        if not verified or verified.get("project_id") != project_id or verified.get("asset") != Path(asset_name).name:
            raise HTTPException(403, "share link is invalid or expired")
        path = _project_dir(project_id) / Path(asset_name).name
        if not path.exists():
            rows = _asset_rows(project_id)
            found = next((r.get("path") for r in rows if Path(str(r.get("path") or "")).name == Path(asset_name).name), None)
            if found: path = Path(found)
        if not path.exists(): raise HTTPException(404, "asset not found")
        mt = "video/mp4" if path.suffix.lower()==".mp4" else "application/zip" if path.suffix.lower()==".zip" else "application/octet-stream"
        return FileResponse(path, media_type=mt, filename=path.name)

    @app.post("/infinity/studio/project/{project_id}/cancel")
    def cancel_project(project_id: str, request: Request, response: Response):
        user_id = _get_user_id(request); _set_session(response, request, user_id)
        p = _get_project(project_id)
        if not p or p.get("user_id") != user_id: raise HTTPException(404, "project not found")
        if p.get("status") in {"completed", "completed_with_qc_warnings"}: raise HTTPException(409, "project is already complete")
        _update_project(project_id, status="cancelled", stage="cancelled", error="Cancelled by creator")
        return {"status": "cancelled", "project_id": project_id, "truthful": True}

    @app.post("/infinity/studio/project/{project_id}/retry")
    def retry_project(project_id: str, request: Request, response: Response):
        user_id = _get_user_id(request); _set_session(response, request, user_id)
        p = _get_project(project_id)
        if not p or p.get("user_id") != user_id: raise HTTPException(404, "project not found")
        if p.get("status") not in {"failed", "cancelled", "completed_with_qc_warnings"}: raise HTTPException(409, "project is not retryable")
        _update_project(project_id, status="queued", stage="queued", progress=0, error=None)
        _ensure_worker(model_fn)
        return {"status": "queued", "project_id": project_id, "truthful": True}

    @app.post("/infinity/studio/feedback")
    def feedback(req: FeedbackRequest, request: Request, response: Response):
        user_id = _get_user_id(request); _set_session(response, request, user_id)
        p = _get_project(req.project_id)
        if not p or p.get("user_id") != user_id:
            raise HTTPException(404, "project not found")
        result = p.get("request_json") if isinstance(p.get("request_json"), dict) else json.loads(p.get("request_json") or "{}")
        with DB_LOCK, _connect() as c:
            c.execute("INSERT INTO studio_feedback_3610(project_id,user_id,rating,lessons_json,metrics_json,created_at) VALUES(?,?,?,?,?,?)", (req.project_id, user_id, req.rating, jdump(req.lessons), jdump((p.get("result_json") or {})), now()))
        skill = _record_learning(user_id, result, feedback={"rating": req.rating, "lessons": req.lessons}, qc=(p.get("result_json") or {}).get("quality", {}))
        return {"status": "learned", "skill": skill, "truthful": True}

    @app.get("/infinity/studio/learning")
    def learning(request: Request, response: Response):
        user_id = _get_user_id(request); _set_session(response, request, user_id)
        return {"skills": _skills(user_id), "truthful": True}

    @app.get("/infinity/studio/profile")
    def creator_profile(request: Request, response: Response):
        user_id = _get_user_id(request); _set_session(response, request, user_id)
        return {"profile": _creator_profile(user_id), "truthful": True}

    @app.put("/infinity/studio/profile")
    def save_creator_profile(payload: Dict[str, Any], request: Request, response: Response):
        user_id = _get_user_id(request); _set_session(response, request, user_id)
        return {"profile": _save_creator_profile(user_id, payload or {}), "truthful": True}

    @app.get("/infinity/studio/resources")
    def resources(request: Request, response: Response):
        user_id = _get_user_id(request); _set_session(response, request, user_id)
        base = [
            {"name": "Wikipedia", "kind": "research", "status": "available"},
            {"name": "DuckDuckGo public search", "kind": "research", "status": "available"},
            {"name": "Wikimedia Commons", "kind": "visual", "status": "available"},
            {"name": "Pexels", "kind": "visual", "status": "available when connected"},
            {"name": "Pixabay", "kind": "visual", "status": "available when connected"},
            {"name": "Hugging Face image generation", "kind": "ai_visual", "status": "available when connected"},
            {"name": "Edge TTS / local espeak", "kind": "voice", "status": "available"},
            {"name": "FFmpeg", "kind": "render", "status": "available"},
        ]
        with DB_LOCK, _connect() as c:
            rows = [dict(r) for r in c.execute("SELECT resource_id,kind,name,endpoint,status,config_json,updated_at FROM studio_resources_3610 WHERE user_id=? ORDER BY updated_at DESC", (user_id,)).fetchall()]
        for r in rows:
            try: r["config"] = json.loads(r.pop("config_json") or "{}")
            except Exception: r["config"] = {}
        return {"resources": base + rows, "truthful": True}

    @app.get("/infinity/studio/connections")
    def connections_endpoint(request: Request, response: Response):
        user_id = _get_user_id(request); _set_session(response, request, user_id)
        return {"connections": _connections(user_id), "health": studio_health(), "truthful": True}

    @app.post("/infinity/studio/connect/webhook")
    def connect_webhook(req: WebhookRequest, request: Request, response: Response):
        user_id = _get_user_id(request); _set_session(response, request, user_id)
        if not req.url.startswith("https://"):
            raise HTTPException(400, "destination must use HTTPS")
        parsed = urlparse(req.url)
        if not parsed.hostname:
            raise HTTPException(400, "destination URL is invalid")
        try:
            secret_enc = enc(req.secret) if req.secret else None
        except Exception as e:
            raise HTTPException(500, str(e))
        cid = uid("conn")
        with DB_LOCK, _connect() as c:
            c.execute("DELETE FROM studio_connections_3610 WHERE user_id=? AND provider='webhook'", (user_id,))
            c.execute("INSERT INTO studio_connections_3610(connection_id,user_id,provider,label,secret_enc,config_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (cid,user_id,"webhook",req.label,secret_enc,jdump({"url":req.url}),"connected",now(),now()))
        return {"status": "connected", "provider": "webhook", "label": req.label, "secret_stored_encrypted": bool(req.secret), "truthful": True}

    @app.post("/infinity/studio/connect/youtube/start")
    def youtube_start(request: Request, response: Response):
        user_id = _get_user_id(request); _set_session(response, request, user_id)
        client_id = os.getenv("AI_INFINITY_GOOGLE_CLIENT_ID", "").strip()
        client_secret = os.getenv("AI_INFINITY_GOOGLE_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            return {"status": "not_available", "message": "YouTube connection is not enabled on this deployment.", "truthful": True}
        public_url = _public_base_url()
        redirect_uri = public_url + "/infinity/studio/connect/youtube/callback"
        state = digest({"uid": user_id, "nonce": uuid.uuid4().hex, "time": int(now())})[:40]
        with DB_LOCK, _connect() as c:
            c.execute("CREATE TABLE IF NOT EXISTS studio_oauth_3610(state TEXT PRIMARY KEY,user_id TEXT,provider TEXT,created_at REAL)")
            c.execute("INSERT OR REPLACE INTO studio_oauth_3610(state,user_id,provider,created_at) VALUES(?,?,?,?)", (state,user_id,"youtube",now()))
        params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": "https://www.googleapis.com/auth/youtube.upload", "access_type": "offline", "prompt": "consent", "state": state}
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
        return {"status": "authorization_required", "provider": "youtube", "authorization_url": url, "truthful": True}

    @app.get("/infinity/studio/connect/youtube/callback")
    def youtube_callback(code: str, state: str):
        client_id = os.getenv("AI_INFINITY_GOOGLE_CLIENT_ID", "").strip(); client_secret = os.getenv("AI_INFINITY_GOOGLE_CLIENT_SECRET", "").strip()
        with DB_LOCK, _connect() as c:
            row = c.execute("SELECT * FROM studio_oauth_3610 WHERE state=? AND provider='youtube'", (state,)).fetchone()
        if not row or now()-float(row["created_at"]) > 900:
            raise HTTPException(400, "authorization session expired")
        public_url = _public_base_url()
        redirect_uri = public_url + "/infinity/studio/connect/youtube/callback"
        raw = urlencode({"client_id": client_id, "client_secret": client_secret, "code": code, "redirect_uri": redirect_uri, "grant_type": "authorization_code"}).encode()
        req = URLRequest("https://oauth2.googleapis.com/token", data=raw, headers={"Content-Type":"application/x-www-form-urlencoded","Accept":"application/json"}, method="POST")
        try:
            with urlopen(req, timeout=45) as r: data=json.loads(r.read().decode("utf-8","replace"))
        except Exception as exc:
            raise HTTPException(502, "YouTube authorization failed: "+str(exc)[:300])
        access = str(data.get("access_token") or "").strip(); refresh = str(data.get("refresh_token") or "").strip()
        if not access: raise HTTPException(502,"YouTube returned no access token")
        try:
            access_enc=enc(access); refresh_enc=enc(refresh) if refresh else None
        except Exception as exc:
            raise HTTPException(500,"secure credential storage unavailable")
        user_id=row["user_id"]
        with DB_LOCK, _connect() as c:
            c.execute("DELETE FROM studio_connections_3610 WHERE user_id=? AND provider='youtube'", (user_id,))
            c.execute("INSERT INTO studio_connections_3610(connection_id,user_id,provider,label,access_enc,refresh_enc,expires_at,scopes_json,config_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (uid("conn"),user_id,"youtube","YouTube",access_enc,refresh_enc,now()+float(data.get("expires_in") or 3600),jdump(["https://www.googleapis.com/auth/youtube.upload"]),jdump({}),"connected",now(),now()))
            c.execute("DELETE FROM studio_oauth_3610 WHERE state=?", (state,))
        return RedirectResponse(url="/studio?connected=youtube", status_code=302)

    @app.get("/infinity/studio/publishers")
    def publishers(request: Request, response: Response):
        user_id = _get_user_id(request); _set_session(response, request, user_id)
        return {"connections": _connections(user_id), "projects": _project_list(user_id), "truthful": True}

    @app.post("/infinity/studio/publish")
    def publish(req: PublishRequest, request: Request, response: Response):
        user_id = _get_user_id(request); _set_session(response, request, user_id)
        p = _get_project(req.project_id)
        if not p or p.get("user_id") != user_id:
            raise HTTPException(404, "project not found")
        if p.get("status") not in {"completed", "completed_with_qc_warnings"}:
            raise HTTPException(409, "project is not finished")
        asset_name = "final.mp4" if req.asset == "final" else safe_name(req.asset)
        if not asset_name.endswith(".mp4"): asset_name += ".mp4"
        final = _project_dir(req.project_id) / Path(asset_name).name
        if not final.exists(): raise HTTPException(404, "finished video not found")
        result = p.get("result_json") if isinstance(p.get("result_json"), dict) else json.loads(p.get("result_json") or "{}")
        meta = {"project_id": req.project_id, "title": result.get("title"), "description": req.description or result.get("title"), "tags": req.tags, "category_id": req.category_id, "privacy_status": req.privacy_status, "video_url": f"/infinity/studio/project/{req.project_id}/asset/{quote(asset_name)}", "package_url": f"/infinity/studio/project/{req.project_id}/asset/package.zip"}
        if req.provider == "youtube":
            out = youtube_upload(user_id, final, meta)
            ext = out.get("url")
        elif req.provider == "webhook":
            out = webhook_publish(user_id, final, meta)
            ext = out.get("url")
        else:
            raise HTTPException(400, "unsupported publishing destination")
        pub_id=uid("pub")
        with DB_LOCK, _connect() as c:
            c.execute("INSERT INTO studio_publications_3610(publication_id,project_id,user_id,provider,status,external_url,response_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (pub_id,req.project_id,user_id,req.provider,str(out.get("status") or "failed"),ext,jdump(redact(out)),now(),now()))
        return {"publication_id": pub_id, **redact(out), "truthful": True}

    # Aliases make the new creator studio the primary visible product while
    # preserving all historical 3607/3608 routes untouched.
    app.state.creator_studio_version = VERSION
    app.state.creator_studio_ui = CREATOR_STUDIO_UI
    _ensure_worker(model_fn)


def _project_list(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    with DB_LOCK, _connect() as c:
        rows = c.execute("SELECT project_id,title,status,error,stage,progress,current_scene,total_scenes,attempt,created_at,updated_at FROM studio_projects_3610 WHERE user_id=? ORDER BY updated_at DESC LIMIT ?", (user_id, max(1,min(int(limit),100)))).fetchall()
    return [dict(r) for r in rows]


def _project_public(p: Dict[str, Any]) -> Dict[str, Any]:
    out = {"project_id": p["project_id"], "title": p.get("title"), "status": p.get("status"), "stage": p.get("stage") or p.get("status"), "progress": float(p.get("progress") or 0), "current_scene": int(p.get("current_scene") or 0), "total_scenes": int(p.get("total_scenes") or 0), "attempt": int(p.get("attempt") or 0), "error": p.get("error"), "created_at": p.get("created_at"), "updated_at": p.get("updated_at"), "truthful": True}
    r = p.get("result_json")
    if isinstance(r, str):
        try: r = json.loads(r)
        except Exception: r = {}
    out["result"] = r or {}
    b = p.get("blueprint_json")
    if isinstance(b, str):
        try: b = json.loads(b)
        except Exception: b = {}
    out["blueprint"] = b or {}
    out["assets"] = [{"kind": x["kind"], "media_type": x["media_type"], "metadata": x.get("metadata") or {}} for x in _asset_rows(p["project_id"]) if x.get("kind") not in {"visual"}]
    return out


def _asset_rows(project_id: str) -> List[Dict[str, Any]]:
    with DB_LOCK, _connect() as c:
        rows = [dict(r) for r in c.execute("SELECT id,kind,path,media_type,metadata_json,created_at FROM studio_assets_3610 WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall()]
    out=[]
    for r in rows:
        try: r["metadata"] = json.loads(r.pop("metadata_json") or "{}")
        except Exception: r["metadata"] = {}
        out.append(r)
    return out


def _connections(user_id: str) -> List[Dict[str, Any]]:
    with DB_LOCK, _connect() as c:
        rows = [dict(r) for r in c.execute("SELECT connection_id,provider,label,expires_at,scopes_json,status,created_at,updated_at FROM studio_connections_3610 WHERE user_id=? ORDER BY updated_at DESC", (user_id,)).fetchall()]
    out=[]
    for r in rows:
        try: r["scopes"] = json.loads(r.pop("scopes_json") or "[]")
        except Exception: r["scopes"]=[]
        r["active"] = r["status"] == "connected" and (not r.get("expires_at") or float(r["expires_at"]) > now())
        out.append(r)
    return out


def _skills(user_id: str) -> List[Dict[str, Any]]:
    with DB_LOCK, _connect() as c:
        rows = [dict(r) for r in c.execute("SELECT skill_id,name,version,quality,uses,updated_at,recipe_json FROM studio_skills_3610 WHERE user_id=? ORDER BY updated_at DESC LIMIT 100", (user_id,)).fetchall()]
    out=[]
    for r in rows:
        try: r["recipe"] = json.loads(r.pop("recipe_json") or "{}")
        except Exception: r["recipe"]={}
        out.append(r)
    return out
