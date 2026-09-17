import os
import re
import json
import time
import uuid
import hashlib
import threading
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY v11
# Evidence-First Autonomous Intelligence Core
# ============================================================

VERSION = "11.0"

APP_NAME = "AI Infinity"

BASE = Path(os.getenv("AI_INFINITY_DATA", "/tmp/ai-infinity"))
TASK_DIR = BASE / "tasks"
MEMORY_DIR = BASE / "memory"
KNOWLEDGE_DIR = BASE / "knowledge"

for directory in (BASE, TASK_DIR, MEMORY_DIR, KNOWLEDGE_DIR):
    directory.mkdir(parents=True, exist_ok=True)


# ============================================================
# CONFIGURATION
# ============================================================

HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "").strip()

HF_BASE_URL = "https://router.huggingface.co/v1/chat/completions"
POLLINATIONS_BASE_URL = "https://gen.pollinations.ai/v1/chat/completions"

HF_MODEL = os.getenv(
    "HF_MODEL",
    "openai/gpt-oss-120b:fastest"
)

HF_BACKUP_MODEL = os.getenv(
    "HF_BACKUP_MODEL",
    "deepseek-ai/DeepSeek-R1:fastest"
)

POLLINATIONS_MODEL = os.getenv(
    "POLLINATIONS_MODEL",
    "openai"
)

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "45"))
RESEARCH_TIMEOUT = int(os.getenv("RESEARCH_TIMEOUT", "15"))

MAX_RESEARCH_RESULTS = int(os.getenv("MAX_RESEARCH_RESULTS", "8"))
MAX_SOURCE_CHARS = int(os.getenv("MAX_SOURCE_CHARS", "7000"))
MAX_MEMORY_ITEMS = int(os.getenv("MAX_MEMORY_ITEMS", "100"))
MAX_TASKS = int(os.getenv("MAX_TASKS", "200"))

MAX_COMMAND_LENGTH = 12000


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description=(
        "AI Infinity v11 - evidence-first multi-mind AI "
        "orchestration and autonomous reasoning platform."
    ),
)


# ============================================================
# TELEMETRY
# ============================================================

telemetry_lock = threading.Lock()

telemetry = {
    "started_at": time.time(),
    "tasks": 0,
    "completed": 0,
    "failed": 0,
    "research_requests": 0,
    "sources_found": 0,
    "sources_verified": 0,
    "memory_writes": 0,
    "provider_attempts": {
        "huggingface": 0,
        "pollinations": 0,
        "local_fallback": 0,
    },
    "provider_successes": {
        "huggingface": 0,
        "pollinations": 0,
        "local_fallback": 0,
    },
    "provider_failures": {
        "huggingface": 0,
        "pollinations": 0,
        "local_fallback": 0,
    },
}


def metric(group, key):
    with telemetry_lock:
        telemetry[group][key] += 1


# ============================================================
# MODELS
# ============================================================

class TaskRequest(BaseModel):
    command: str | None = Field(
        default=None,
        max_length=MAX_COMMAND_LENGTH
    )

    objective: str | None = Field(
        default=None,
        max_length=MAX_COMMAND_LENGTH
    )

    research: bool = True
    verify: bool = True
    remember: bool = True

    max_sources: int = Field(
        default=MAX_RESEARCH_RESULTS,
        ge=1,
        le=15
    )


class ResearchRequest(BaseModel):
    query: str = Field(
        min_length=2,
        max_length=5000
    )

    max_results: int = Field(
        default=8,
        ge=1,
        le=15
    )


# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def safe_filename(value):
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value)
    return value[:100]


def atomic_write(path: Path, data):
    temp = path.with_suffix(path.suffix + ".tmp")

    with open(temp, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            indent=2,
            ensure_ascii=False
        )

    temp.replace(path)


def read_json(path: Path, default=None):
    if not path.exists():
        return default

    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return default


def normalize_url(url):
    try:
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return None

        query = parse_qs(parsed.query)

        # Remove common tracking parameters.
        blocked = {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "fbclid",
            "gclid",
        }

        clean_query = []

        for key, values in query.items():
            if key.lower() not in blocked:
                for value in values:
                    clean_query.append(
                        f"{quote_plus(key)}={quote_plus(value)}"
                    )

        path = parsed.path or "/"

        result = (
            f"{parsed.scheme}://"
            f"{parsed.netloc}"
            f"{path}"
        )

        if clean_query:
            result += "?" + "&".join(clean_query)

        return result

    except Exception:
        return None


def source_id(url):
    return hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()[:16]


def clean_text(text):
    text = re.sub(r"\s+", " ", text or "")
    return text.strip()


def extract_domain(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


# ============================================================
# INTENT
# ============================================================

def classify_intent(objective):
    text = objective.lower()

    if any(word in text for word in [
        "build",
        "create",
        "develop",
        "implement",
        "code",
        "upgrade",
        "fix",
        "deploy",
    ]):
        return "build"

    if any(word in text for word in [
        "research",
        "investigate",
        "analyze",
        "analysis",
        "compare",
        "find out",
        "study",
    ]):
        return "research"

    if any(word in text for word in [
        "plan",
        "roadmap",
        "strategy",
        "next",
        "future",
    ]):
        return "strategy"

    if any(word in text for word in [
        "test",
        "verify",
        "check",
        "audit",
        "validate",
    ]):
        return "verification"

    return "general"


# ============================================================
# WEB RESEARCH ENGINE
# ============================================================

def search_duckduckgo(query, max_results=8):
    """
    Evidence retrieval layer.

    Uses DuckDuckGo's HTML interface so AI Infinity can
    operate without requiring a paid search API.
    """

    metric("research_requests", "value") if False else None

    url = (
        "https://html.duckduckgo.com/html/?q="
        + quote_plus(query)
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Linux; Android 10) "
            "AppleWebKit/537.36 "
            "Chrome/120 Safari/537.36"
        )
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=RESEARCH_TIMEOUT
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        results = []
        seen = set()

        for item in soup.select(".result"):
            link = item.select_one(".result__a")

            if not link:
                continue

            href = link.get("href", "").strip()

            if not href:
                continue

            # DuckDuckGo sometimes wraps destination URLs.
            if "uddg=" in href:
                try:
                    href = parse_qs(
                        urlparse(href).query
                    ).get("uddg", [href])[0]
                except Exception:
                    pass

            clean_url = normalize_url(href)

            if not clean_url:
                continue

            if clean_url in seen:
                continue

            seen.add(clean_url)

            title = clean_text(
                link.get_text(" ", strip=True)
            )

            snippet_node = item.select_one(
                ".result__snippet"
            )

            snippet = clean_text(
                snippet_node.get_text(
                    " ",
                    strip=True
                ) if snippet_node else ""
            )

            results.append({
                "id": source_id(clean_url),
                "title": title,
                "url": clean_url,
                "domain": extract_domain(clean_url),
                "snippet": snippet,
                "retrieved_at": now_iso(),
            })

            if len(results) >= max_results:
                break

        with telemetry_lock:
            telemetry["sources_found"] += len(results)

        return results

    except Exception as exc:
        return [{
            "error": "research_search_failed",
            "message": str(exc)
        }]


def fetch_source(url):
    """
    Fetch and extract readable text from a source.
    """

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(compatible; AI-Infinity/11.0)"
        )
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=RESEARCH_TIMEOUT,
            allow_redirects=True
        )

        response.raise_for_status()

        content_type = (
            response.headers
            .get("content-type", "")
            .lower()
        )

        if "text/html" not in content_type:
            return {
                "success": False,
                "url": url,
                "reason": "not_html"
            }

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        for element in soup([
            "script",
            "style",
            "noscript",
            "svg",
            "nav",
            "footer",
            "header",
            "form",
        ]):
            element.decompose()

        title = clean_text(
            soup.title.get_text(
                " ",
                strip=True
            )
            if soup.title
            else ""
        )

        main = (
            soup.find("main")
            or soup.find("article")
            or soup.body
        )

        text = clean_text(
            main.get_text(
                " ",
                strip=True
            )
            if main
            else ""
        )

        text = text[:MAX_SOURCE_CHARS]

        if not text:
            return {
                "success": False,
                "url": url,
                "reason": "empty"
            }

        return {
            "success": True,
            "url": url,
            "final_url": response.url,
            "title": title,
            "domain": extract_domain(response.url),
            "text": text,
            "status_code": response.status_code,
            "retrieved_at": now_iso(),
        }

    except Exception as exc:
        return {
            "success": False,
            "url": url,
            "reason": str(exc)
        }


def verify_source(source):
    """
    Verification means the URL responds and contains
    readable material. It does not mean the source's
    claims are automatically true.
    """

    url = source.get("url")

    if not url:
        return {
            **source,
            "verified": False,
            "verification_reason": "missing_url",
        }

    fetched = fetch_source(url)

    if fetched.get("success"):
        return {
            **source,
            **fetched,
            "verified": True,
            "verification_reason": (
                "URL reachable and readable content extracted"
            ),
        }

    return {
        **source,
        "verified": False,
        "verification_reason": fetched.get(
            "reason",
            "source_unavailable"
        ),
    }


def research(objective, max_results=8, verify=True):
    """
    Full evidence pipeline:

    query
      ↓
    search
      ↓
    deduplicate
      ↓
    fetch
      ↓
    verify
      ↓
    evidence package
    """

    metric("research_requests", "research_requests")

    search_results = search_duckduckgo(
        objective,
        max_results=max_results
    )

    valid_results = [
        item for item in search_results
        if item.get("url")
    ]

    if not valid_results:
        return {
            "query": objective,
            "search_results": [],
            "verified_sources": [],
            "evidence": [],
            "sources_found": 0,
            "sources_verified": 0,
        }

    verified = []

    if verify:
        workers = min(6, len(valid_results))

        with ThreadPoolExecutor(
            max_workers=workers
        ) as executor:

            futures = [
                executor.submit(
                    verify_source,
                    item
                )
                for item in valid_results
            ]

            for future in as_completed(futures):
                try:
                    result = future.result()

                    if result.get("verified"):
                        verified.append(result)

                except Exception:
                    continue

    else:
        verified = valid_results

    with telemetry_lock:
        telemetry["sources_verified"] += len(
            verified
        )

    evidence = []

    for item in verified:
        evidence.append({
            "source_id": item.get("id"),
            "title": item.get("title"),
            "domain": item.get("domain"),
            "url": item.get("url"),
            "snippet": item.get("snippet", ""),
            "text": item.get("text", "")[:MAX_SOURCE_CHARS],
            "retrieved_at": item.get(
                "retrieved_at",
                now_iso()
            ),
        })

    return {
        "query": objective,
        "search_results": search_results,
        "verified_sources": verified,
        "evidence": evidence,
        "sources_found": len(valid_results),
        "sources_verified": len(verified),
    }


# ============================================================
# EVIDENCE PACK
# ============================================================

def build_evidence_pack(research_result):
    evidence = research_result.get(
        "evidence",
        []
    )

    if not evidence:
        return (
            "NO VERIFIED EXTERNAL EVIDENCE WAS FOUND.\n"
            "Do not invent sources or claim that web research "
            "confirmed something."
        )

    blocks = []

    for index, item in enumerate(evidence, 1):
        blocks.append(
            f"""
SOURCE {index}
Title: {item.get('title', '')}
Domain: {item.get('domain', '')}
URL: {item.get('url', '')}
Snippet: {item.get('snippet', '')}
Content:
{item.get('text', '')}
"""
        )

    return "\n".join(blocks)


# ============================================================
# LOCAL FALLBACK
# ============================================================

def local_fallback(prompt, role="AI Infinity"):
    metric("provider_attempts", "local_fallback")

    text = (
        f"{role} local fallback.\n\n"
        f"Objective:\n{prompt[:3000]}\n\n"
        "External model inference was unavailable. "
        "This result is a fallback and should not be "
        "treated as independently verified."
    )

    metric("provider_successes", "local_fallback")

    return {
        "success": True,
        "provider": "local_fallback",
        "text": text,
    }


# ============================================================
# LLM PROVIDERS
# ============================================================

def call_huggingface(
    messages,
    model=None,
    temperature=0.3,
    max_tokens=1400
):
    if not HF_TOKEN:
        return None

    metric("provider_attempts", "huggingface")

    payload = {
        "model": model or HF_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            HF_BASE_URL,
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        text = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )

        if not text:
            raise ValueError(
                "Hugging Face returned empty content"
            )

        metric(
            "provider_successes",
            "huggingface"
        )

        return {
            "success": True,
            "provider": "huggingface",
            "model": model or HF_MODEL,
            "text": text,
        }

    except Exception as exc:
        metric(
            "provider_failures",
            "huggingface"
        )

        return {
            "success": False,
            "provider": "huggingface",
            "error": str(exc),
        }


def call_pollinations(
    messages,
    temperature=0.3,
    max_tokens=1400
):
    if not POLLINATIONS_API_KEY:
        return None

    metric(
        "provider_attempts",
        "pollinations"
    )

    payload = {
        "model": POLLINATIONS_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    headers = {
        "Authorization": (
            f"Bearer {POLLINATIONS_API_KEY}"
        ),
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            POLLINATIONS_BASE_URL,
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        text = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )

        if not text:
            raise ValueError(
                "Pollinations returned empty content"
            )

        metric(
            "provider_successes",
            "pollinations"
        )

        return {
            "success": True,
            "provider": "pollinations",
            "model": POLLINATIONS_MODEL,
            "text": text,
        }

    except Exception as exc:
        metric(
            "provider_failures",
            "pollinations"
        )

        return {
            "success": False,
            "provider": "pollinations",
            "error": str(exc),
        }


def ask_ai(
    messages,
    temperature=0.3,
    max_tokens=1400
):
    """
    Provider router:

    1. Hugging Face primary
    2. Hugging Face backup model
    3. Pollinations
    4. Local fallback
    """

    result = call_huggingface(
        messages,
        model=HF_MODEL,
        temperature=temperature,
        max_tokens=max_tokens
    )

    if result and result.get("success"):
        return result

    result = call_huggingface(
        messages,
        model=HF_BACKUP_MODEL,
        temperature=temperature,
        max_tokens=max_tokens
    )

    if result and result.get("success"):
        return result

    result = call_pollinations(
        messages,
        temperature=temperature,
        max_tokens=max_tokens
    )

    if result and result.get("success"):
        return result

    return local_fallback(
        messages[-1].get("content", ""),
        role="AI Infinity Router"
    )


# ============================================================
# SPECIALIST MINDS
# ============================================================

MINDS = [
    {
        "name": "Research Mind",
        "mission": (
            "Extract the strongest factual findings from "
            "the supplied evidence. Separate facts from "
            "inference."
        ),
    },
    {
        "name": "Builder Mind",
        "mission": (
            "Turn the objective and evidence into concrete "
            "technical implementation steps."
        ),
    },
    {
        "name": "Critical Mind",
        "mission": (
            "Find contradictions, missing evidence, risks, "
            "failure modes, and unsupported claims."
        ),
    },
    {
        "name": "Optimizer Mind",
        "mission": (
            "Find the simplest, cheapest, fastest path "
            "to useful real-world results."
        ),
    },
    {
        "name": "Verification Mind",
        "mission": (
            "Check whether conclusions are actually supported "
            "by the evidence. Reject fabricated certainty."
        ),
    },
    {
        "name": "Future Mind",
        "mission": (
            "Explore scalable next-generation possibilities "
            "while clearly distinguishing ideas from facts."
        ),
    },
]


def run_mind(mind, objective, intent, evidence_pack):
    system_prompt = f"""
You are the {mind['name']} inside AI Infinity.

Your role:
{mind['mission']}

Rules:
1. Never fabricate evidence.
2. Never pretend an unverified claim is verified.
3. Use the supplied evidence when available.
4. Clearly label inference, recommendation, and uncertainty.
5. Produce practical output.
6. Keep the answer focused on the user's objective.
"""

    user_prompt = f"""
OBJECTIVE:
{objective}

INTENT:
{intent}

EVIDENCE PACKAGE:
{evidence_pack}

Return:

1. Key finding
2. Evidence used
3. Important uncertainty
4. Concrete recommendation
5. One actionable next step
"""

    result = ask_ai(
        [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        temperature=0.25,
        max_tokens=1500
    )

    return {
        "mind": mind["name"],
        "provider": result.get("provider"),
        "model": result.get("model"),
        "success": result.get("success", False),
        "analysis": result.get("text", ""),
    }


# ============================================================
# SYNTHESIS
# ============================================================

def synthesize(
    objective,
    intent,
    research_result,
    specialists
):
    evidence_pack = build_evidence_pack(
        research_result
    )

    specialist_text = "\n\n".join(
        [
            (
                f"### {item['mind']}\n"
                f"{item['analysis']}"
            )
            for item in specialists
            if item.get("success")
        ]
    )

    system_prompt = """
You are the central synthesis intelligence of AI Infinity.

Your job is to combine independent specialist analyses
into one accurate, practical answer.

Evidence rules:
- Verified evidence outranks speculation.
- Specialist opinions are not automatically facts.
- If evidence is missing, explicitly say so.
- Never invent citations.
- Do not claim that a URL proves a statement merely
  because the URL was reachable.
- Distinguish facts, inference, recommendations,
  and uncertainty.

Produce:
1. Executive answer
2. Evidence-backed findings
3. Recommended actions
4. Risks and uncertainties
5. Immediate next action
"""

    user_prompt = f"""
OBJECTIVE:
{objective}

INTENT:
{intent}

EVIDENCE:
{evidence_pack}

SPECIALIST ANALYSES:
{specialist_text}
"""

    result = ask_ai(
        [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        temperature=0.2,
        max_tokens=2200
    )

    return {
        "success": result.get("success", False),
        "provider": result.get("provider"),
        "model": result.get("model"),
        "text": result.get("text", ""),
    }


# ============================================================
# VERIFICATION
# ============================================================

def verify_synthesis(
    objective,
    synthesis,
    research_result
):
    evidence_count = research_result.get(
        "sources_verified",
        0
    )

    evidence_text = build_evidence_pack(
        research_result
    )

    system_prompt = """
You are the final verification gate for AI Infinity.

Check the proposed answer against the supplied evidence.

Return exactly these sections:

VERDICT:
SUPPORTED / NEEDS_REVIEW

EVIDENCE_COVERAGE:
Explain how much of the answer is supported.

UNSUPPORTED_OR_WEAK:
List claims that need caution.

CORRECTIONS:
State corrections if necessary.

FINAL_NOTE:
One concise statement about reliability.

Never create new evidence.
"""

    user_prompt = f"""
OBJECTIVE:
{objective}

VERIFIED SOURCE COUNT:
{evidence_count}

EVIDENCE:
{evidence_text}

SYNTHESIS:
{synthesis}
"""

    result = ask_ai(
        [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        temperature=0.1,
        max_tokens=1200
    )

    return {
        "success": result.get("success", False),
        "provider": result.get("provider"),
        "model": result.get("model"),
        "text": result.get("text", ""),
    }


# ============================================================
# CONFIDENCE
# ============================================================

def calculate_confidence(
    research_result,
    specialists,
    synthesis,
    verification
):
    verified = research_result.get(
        "sources_verified",
        0
    )

    successful_minds = sum(
        1
        for item in specialists
        if item.get("success")
    )

    synthesis_ok = bool(
        synthesis.get("success")
    )

    verification_ok = bool(
        verification.get("success")
    )

    score = 0.30

    score += min(
        verified * 0.07,
        0.35
    )

    score += min(
        successful_minds * 0.04,
        0.24
    )

    if synthesis_ok:
        score += 0.06

    if verification_ok:
        score += 0.05

    score = min(score, 0.99)

    return {
        "score": round(score, 2),
        "percentage": f"{round(score * 100)}%",
        "basis": {
            "verified_evidence": verified,
            "successful_specialist_minds": successful_minds,
            "synthesis_success": synthesis_ok,
            "verification_success": verification_ok,
        },
        "note": (
            "Heuristic confidence, not a probability."
        ),
    }


# ============================================================
# MEMORY
# ============================================================

def memory_path():
    return MEMORY_DIR / "memory.json"


def load_memory():
    return read_json(
        memory_path(),
        default=[]
    )


def save_memory(
    objective,
    result,
    task_id
):
    memory = load_memory()

    item = {
        "id": str(uuid.uuid4()),
        "task_id": task_id,
        "objective": objective,
        "summary": result.get(
            "synthesis",
            {}
        ).get("text", "")[:6000],
        "verified_sources": result.get(
            "research",
            {}
        ).get(
            "sources_verified",
            0
        ),
        "created_at": now_iso(),
    }

    memory.insert(0, item)

    memory = memory[:MAX_MEMORY_ITEMS]

    atomic_write(
        memory_path(),
        memory
    )

    metric(
        "memory_writes",
        "memory_writes"
    )

    return item


def find_related_memory(objective):
    """
    Lightweight local memory retrieval.
    No embeddings or paid database required.
    """

    memory = load_memory()

    if not memory:
        return []

    words = {
        word.lower()
        for word in re.findall(
            r"[a-zA-Z0-9]{4,}",
            objective
        )
    }

    scored = []

    for item in memory:
        text = (
            item.get("objective", "")
            + " "
            + item.get("summary", "")
        ).lower()

        overlap = sum(
            1
            for word in words
            if word in text
        )

        if overlap:
            scored.append(
                (
                    overlap,
                    item
                )
            )

    scored.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return [
        item
        for _, item in scored[:5]
    ]


# ============================================================
# TASK EXECUTION
# ============================================================

def execute_task(
    task_id,
    objective,
    research_enabled=True,
    verification_enabled=True,
    remember=True,
    max_sources=8
):
    started = time.time()

    intent = classify_intent(
        objective
    )

    related_memory = find_related_memory(
        objective
    )

    # --------------------------------------------------------
    # STEP 1 - RESEARCH
    # --------------------------------------------------------

    if research_enabled:
        research_result = research(
            objective,
            max_results=max_sources,
            verify=verification_enabled
        )
    else:
        research_result = {
            "query": objective,
            "search_results": [],
            "verified_sources": [],
            "evidence": [],
            "sources_found": 0,
            "sources_verified": 0,
        }

    evidence_pack = build_evidence_pack(
        research_result
    )

    # --------------------------------------------------------
    # STEP 2 - SPECIALIST MINDS
    # --------------------------------------------------------

    specialists = []

    with ThreadPoolExecutor(
        max_workers=len(MINDS)
    ) as executor:

        futures = [
            executor.submit(
                run_mind,
                mind,
                objective,
                intent,
                evidence_pack
            )
            for mind in MINDS
        ]

        for future in as_completed(futures):
            try:
                specialists.append(
                    future.result()
                )
            except Exception as exc:
                specialists.append({
                    "mind": "Unknown Mind",
                    "provider": "error",
                    "success": False,
                    "analysis": str(exc),
                })

    specialists.sort(
        key=lambda item: item.get("mind", "")
    )

    # --------------------------------------------------------
    # STEP 3 - SYNTHESIS
    # --------------------------------------------------------

    synthesis = synthesize(
        objective,
        intent,
        research_result,
        specialists
    )

    # --------------------------------------------------------
    # STEP 4 - FINAL VERIFICATION
    # --------------------------------------------------------

    if verification_enabled:
        verification = verify_synthesis(
            objective,
            synthesis.get("text", ""),
            research_result
        )
    else:
        verification = {
            "success": False,
            "provider": None,
            "text": "Verification disabled.",
        }

    # --------------------------------------------------------
    # STEP 5 - CONFIDENCE
    # --------------------------------------------------------

    confidence = calculate_confidence(
        research_result,
        specialists,
        synthesis,
        verification
    )

    result = {
        "objective": objective,
        "intent": intent,

        "research": research_result,

        "memory_context": {
            "related_items": related_memory,
            "count": len(related_memory),
        },

        "specialist_minds": specialists,

        "synthesis": synthesis,

        "verification": verification,

        "confidence": confidence,

        "execution_plan": [
            {
                "step": 1,
                "action": "Classify objective",
                "status": "completed",
            },
            {
                "step": 2,
                "action": "Research external evidence",
                "status": (
                    "completed"
                    if research_enabled
                    else "disabled"
                ),
            },
            {
                "step": 3,
                "action": "Verify external sources",
                "status": (
                    "completed"
                    if research_result.get(
                        "sources_verified",
                        0
                    ) > 0
                    else "no_verified_sources"
                ),
            },
            {
                "step": 4,
                "action": "Run six specialist minds",
                "status": "completed",
            },
            {
                "step": 5,
                "action": "Synthesize answer",
                "status": (
                    "completed"
                    if synthesis.get("success")
                    else "failed"
                ),
            },
            {
                "step": 6,
                "action": "Verify synthesis",
                "status": (
                    "completed"
                    if verification.get("success")
                    else "failed_or_disabled"
                ),
            },
            {
                "step": 7,
                "action": "Calculate confidence",
                "status": "completed",
            },
            {
                "step": 8,
                "action": "Store reusable memory",
                "status": (
                    "completed"
                    if remember
                    else "disabled"
                ),
            },
        ],

        "providers": get_provider_stats(),

        "duration_seconds": round(
            time.time() - started,
            2
        ),

        "generated_at": time.time(),
    }

    # --------------------------------------------------------
    # STEP 6 - MEMORY
    # --------------------------------------------------------

    if remember:
        try:
            memory_item = save_memory(
                objective,
                result,
                task_id
            )

            result["memory_saved"] = memory_item

        except Exception as exc:
            result["memory_saved"] = {
                "success": False,
                "error": str(exc),
            }

    return result


def save_task(task_id, data):
    path = TASK_DIR / f"{safe_filename(task_id)}.json"
    atomic_write(path, data)


def load_task(task_id):
    path = TASK_DIR / f"{safe_filename(task_id)}.json"

    return read_json(
        path,
        default=None
    )


def create_task_record(
    task_id,
    objective
):
    return {
        "task_id": task_id,
        "status": "running",
        "objective": objective,
        "created_at": now_iso(),
    }


# ============================================================
# PROVIDER STATS
# ============================================================

def get_provider_stats():
    with telemetry_lock:
        return {
            provider: {
                "attempts": telemetry[
                    "provider_attempts"
                ][provider],
                "successes": telemetry[
                    "provider_successes"
                ][provider],
                "failures": telemetry[
                    "provider_failures"
                ][provider],
            }
            for provider in [
                "huggingface",
                "pollinations",
                "local_fallback",
            ]
        }


# ============================================================
# ENDPOINTS
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
def home():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta
 name="viewport"
 content="width=device-width,
 initial-scale=1.0,
 maximum-scale=1.0"
>
<title>AI Infinity</title>

<style>
* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #070b12;
    color: #f4f7fb;
    font-family:
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
}

.container {
    width: 100%;
    max-width: 850px;
    margin: auto;
    padding: 22px 16px 50px;
}

.logo {
    font-size: 32px;
    font-weight: 800;
    margin-bottom: 5px;
}

.subtitle {
    opacity: .65;
    margin-bottom: 25px;
}

.card {
    background: #101722;
    border: 1px solid #202c3b;
    border-radius: 18px;
    padding: 18px;
    margin-bottom: 15px;
}

textarea {
    width: 100%;
    min-height: 150px;
    resize: vertical;
    background: #080d15;
    color: white;
    border: 1px solid #263548;
    border-radius: 14px;
    padding: 15px;
    font-size: 16px;
    outline: none;
}

button {
    width: 100%;
    border: 0;
    border-radius: 14px;
    padding: 15px;
    margin-top: 12px;
    font-size: 16px;
    font-weight: 700;
    cursor: pointer;
    background: #ffffff;
    color: #070b12;
}

button:disabled {
    opacity: .5;
}

pre {
    white-space: pre-wrap;
    word-wrap: break-word;
    line-height: 1.55;
    color: #dce5ef;
}

.status {
    margin-top: 12px;
    opacity: .7;
}

.badge {
    display: inline-block;
    padding: 5px 9px;
    border-radius: 999px;
    background: #182536;
    margin: 3px;
    font-size: 12px;
}
</style>
</head>

<body>

<div class="container">

<div class="logo">∞ AI Infinity</div>

<div class="subtitle">
Evidence-first multi-mind intelligence
</div>

<div class="card">

<textarea
 id="objective"
 placeholder="Tell AI Infinity what you want..."
></textarea>

<button
 id="run"
 onclick="runTask()"
>
Run AI Infinity
</button>

<div
 id="status"
 class="status"
></div>

</div>

<div
 id="result"
 class="card"
 style="display:none"
>
<h3>Result</h3>

<div id="badges"></div>

<pre id="output"></pre>

</div>

</div>

<script>

async function runTask() {

    const button =
        document.getElementById("run");

    const status =
        document.getElementById("status");

    const result =
        document.getElementById("result");

    const output =
        document.getElementById("output");

    const badges =
        document.getElementById("badges");

    const objective =
        document
        .getElementById("objective")
        .value
        .trim();

    if (!objective) {
        status.textContent =
            "Enter an objective first.";
        return;
    }

    button.disabled = true;

    status.textContent =
        "AI Infinity is researching, thinking, synthesizing and verifying...";

    result.style.display = "none";

    try {

        const response =
            await fetch("/task", {
                method: "POST",
                headers: {
                    "Content-Type":
                        "application/json"
                },
                body: JSON.stringify({
                    objective: objective,
                    research: true,
                    verify: true,
                    remember: true,
                    max_sources: 8
                })
            });

        const data =
            await response.json();

        if (!response.ok) {
            throw new Error(
                data.detail ||
                "Task failed"
            );
        }

        result.style.display =
            "block";

        const confidence =
            data.confidence?.percentage
            || "N/A";

        const verified =
            data.research?.sources_verified
            || 0;

        badges.innerHTML =
            `<span class="badge">
                Confidence: ${confidence}
             </span>
             <span class="badge">
                Verified sources: ${verified}
             </span>
             <span class="badge">
                Minds: ${
                    data.specialist_minds?.length || 0
                }
             </span>
             <span class="badge">
                ${data.duration_seconds || 0}s
             </span>`;

        output.textContent =
            data.synthesis?.text
            || "No synthesis returned.";

        status.textContent =
            "Completed.";

    } catch (error) {

        status.textContent =
            "Error: " + error.message;

    } finally {

        button.disabled = false;
    }
}

</script>

</body>
</html>
"""


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": APP_NAME,
        "version": VERSION,
        "engine": "evidence-first",
        "time": now_iso(),
    }


@app.get("/config")
def config():
    return {
        "service": APP_NAME,
        "version": VERSION,
        "research_engine": True,
        "source_verification": True,
        "six_specialist_minds": True,
        "persistent_memory": True,
        "provider_failover": True,
        "huggingface_configured": bool(HF_TOKEN),
        "pollinations_configured": bool(
            POLLINATIONS_API_KEY
        ),
        "models": {
            "primary": HF_MODEL,
            "backup": HF_BACKUP_MODEL,
            "pollinations": POLLINATIONS_MODEL,
        },
    }


@app.get("/stats")
def stats():
    return {
        "service": APP_NAME,
        "version": VERSION,
        "uptime_seconds": round(
            time.time()
            - telemetry["started_at"],
            2
        ),
        "tasks": telemetry["tasks"],
        "completed": telemetry["completed"],
        "failed": telemetry["failed"],
        "research_requests": telemetry[
            "research_requests"
        ],
        "sources_found": telemetry[
            "sources_found"
        ],
        "sources_verified": telemetry[
            "sources_verified"
        ],
        "memory_writes": telemetry[
            "memory_writes"
        ],
        "providers": get_provider_stats(),
    }


@app.post("/research")
def research_endpoint(request: ResearchRequest):
    result = research(
        request.query,
        max_results=request.max_results,
        verify=True
    )

    return {
        "success": True,
        "version": VERSION,
        **result,
    }


@app.get("/memory")
def memory_endpoint(
    limit: int = Query(
        default=20,
        ge=1,
        le=100
    )
):
    memory = load_memory()

    return {
        "count": len(memory),
        "items": memory[:limit],
    }


@app.post("/task")
def task_endpoint(request: TaskRequest):

    objective = (
        request.command
        or request.objective
        or ""
    ).strip()

    if not objective:
        raise HTTPException(
            status_code=422,
            detail=(
                "Provide either "
                "'command' or 'objective'."
            )
        )

    task_id = (
        "task-"
        + uuid.uuid4().hex[:12]
    )

    metric("tasks", "tasks")

    task_record = create_task_record(
        task_id,
        objective
    )

    save_task(
        task_id,
        task_record
    )

    try:

        result = execute_task(
            task_id=task_id,
            objective=objective,
            research_enabled=request.research,
            verification_enabled=request.verify,
            remember=request.remember,
            max_sources=request.max_sources,
        )

        final_record = {
            **task_record,
            "status": "completed",
            "result": result,
            "completed_at": now_iso(),
        }

        save_task(
            task_id,
            final_record
        )

        metric(
            "completed",
            "completed"
        )

        return {
            "task_id": task_id,
            "status": "completed",
            **result,
        }

    except Exception as exc:

        metric(
            "failed",
            "failed"
        )

        failed_record = {
            **task_record,
            "status": "failed",
            "error": str(exc),
            "failed_at": now_iso(),
        }

        save_task(
            task_id,
            failed_record
        )

        raise HTTPException(
            status_code=500,
            detail=str(exc)
        )


@app.post("/run")
def run_alias(request: TaskRequest):
    return task_endpoint(request)


@app.get("/task/{task_id}")
def task_status(task_id: str):

    data = load_task(task_id)

    if data is None:
        raise HTTPException(
            status_code=404,
            detail="Task not found."
        )

    return data


@app.get("/tasks")
def list_tasks(
    limit: int = Query(
        default=20,
        ge=1,
        le=100
    )
):
    files = sorted(
        TASK_DIR.glob("task-*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True
    )

    results = []

    for path in files[:limit]:
        data = read_json(
            path,
            default={}
        )

        results.append({
            "task_id": data.get("task_id"),
            "status": data.get("status"),
            "objective": data.get("objective"),
            "created_at": data.get(
                "created_at"
            ),
            "completed_at": data.get(
                "completed_at"
            ),
        })

    return {
        "count": len(results),
        "items": results,
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup_event():

    BASE.mkdir(
        parents=True,
        exist_ok=True
    )

    TASK_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    MEMORY_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    KNOWLEDGE_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    print(
        f"{APP_NAME} v{VERSION} online."
    )

    print(
        "Research engine: ENABLED"
    )

    print(
        "Six specialist minds: ENABLED"
    )

    print(
        "Evidence verification: ENABLED"
    )

    print(
        "Persistent memory: ENABLED"
    )

    print(
        "Hugging Face: "
        + ("CONFIGURED" if HF_TOKEN else "NOT CONFIGURED")
    )

    print(
        "Pollinations: "
        + (
            "CONFIGURED"
            if POLLINATIONS_API_KEY
            else "NOT CONFIGURED"
        )
    )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    import uvicorn

    port = int(
        os.getenv("PORT", "8000")
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port
    )
