import os
import re
import json
import time
import uuid
import hashlib
import ast
import base64
import threading
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY
# INTEGRATED INTELLIGENCE FABRIC
# ============================================================
#
# Intent
#   ↓
# World/Evidence
#   ↓
# Temporary Minds
#   ↓
# Strategy / Simulation
#   ↓
# Execution Graph
#   ↓
# Permission Gate
#   ↓
# Tools / Artifacts
#   ↓
# Verification
#   ↓
# Memory
#   ↓
# Intelligence Genome
#   ↓
# Learning / Recovery
#
# This is an engineering system, not a claim of AGI/ASI.
# Consequential external actions require explicit authorization.
# ============================================================

VERSION = "20.0"

APP_NAME = "AI Infinity"

BASE = Path(
    os.getenv(
        "AI_INFINITY_DATA",
        "/tmp/ai-infinity"
    )
)

TASK_DIR = BASE / "tasks"
MEMORY_DIR = BASE / "memory"
ARTIFACT_DIR = BASE / "artifacts"
AUDIT_DIR = BASE / "audit"
KNOWLEDGE_DIR = BASE / "knowledge"
GENOME_DIR = BASE / "genomes"
SNAPSHOT_DIR = BASE / "snapshots"

for directory in (
    BASE,
    TASK_DIR,
    MEMORY_DIR,
    ARTIFACT_DIR,
    AUDIT_DIR,
    KNOWLEDGE_DIR,
    GENOME_DIR,
    SNAPSHOT_DIR,
):
    directory.mkdir(
        parents=True,
        exist_ok=True
    )


# ============================================================
# CONFIGURATION
# ============================================================

HF_TOKEN = os.getenv(
    "HF_TOKEN",
    ""
).strip()

POLLINATIONS_API_KEY = os.getenv(
    "POLLINATIONS_API_KEY",
    ""
).strip()

HF_BASE_URL = (
    "https://router.huggingface.co/"
    "v1/chat/completions"
)

POLLINATIONS_BASE_URL = (
    "https://gen.pollinations.ai/"
    "v1/chat/completions"
)

HF_MODEL = os.getenv(
    "HF_MODEL",
    "openai/gpt-oss-120b:cheapest"
).strip()

HF_BACKUP_MODEL = os.getenv(
    "HF_BACKUP_MODEL",
    "openai/gpt-oss-120b:cheapest"
).strip()

POLLINATIONS_MODEL = os.getenv(
    "POLLINATIONS_MODEL",
    "openai"
).strip()

REQUEST_TIMEOUT = int(
    os.getenv(
        "REQUEST_TIMEOUT",
        "45"
    )
)

RESEARCH_TIMEOUT = int(
    os.getenv(
        "RESEARCH_TIMEOUT",
        "15"
    )
)

MAX_RESEARCH_RESULTS = int(
    os.getenv(
        "MAX_RESEARCH_RESULTS",
        "8"
    )
)

MAX_SOURCE_CHARS = int(
    os.getenv(
        "MAX_SOURCE_CHARS",
        "7000"
    )
)

MAX_COMMAND_LENGTH = 12000

MAX_ARTIFACT_SIZE = int(
    os.getenv(
        "AI_INFINITY_MAX_ARTIFACT",
        "200000"
    )
)

MAX_MEMORY_ITEMS = int(
    os.getenv(
        "MAX_MEMORY_ITEMS",
        "200"
    )
)

GITHUB_TOKEN = os.getenv(
    "GITHUB_TOKEN",
    ""
).strip()

GITHUB_REPO = os.getenv(
    "GITHUB_REPO",
    "aurlooij-afk/AI-INFINITY"
).strip()

# IMPORTANT:
# False means no external writes.
ALLOW_EXTERNAL_WRITES = (
    os.getenv(
        "AI_INFINITY_ALLOW_EXTERNAL_WRITES",
        "false"
    ).lower()
    == "true"
)

# $0 Governor.
# The application never performs paid purchases.
ZERO_DOLLAR_MODE = (
    os.getenv(
        "AI_INFINITY_ZERO_DOLLAR",
        "true"
    ).lower()
    == "true"
)


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description=(
        "AI Infinity Integrated Intelligence Fabric. "
        "Evidence-first planning, reasoning, simulation, "
        "safe execution, verification and learning."
    )
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
    "artifacts_created": 0,
    "verification_passes": 0,
    "verification_failures": 0,
    "provider_attempts": {
        "huggingface": 0,
        "pollinations": 0,
        "local": 0,
    },
    "provider_successes": {
        "huggingface": 0,
        "pollinations": 0,
        "local": 0,
    },
    "provider_failures": {
        "huggingface": 0,
        "pollinations": 0,
        "local": 0,
    },
}


def inc(group, key, amount=1):

    with telemetry_lock:

        telemetry[group][key] += amount


# ============================================================
# REQUEST MODELS
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

    simulate: bool = True

    dream: bool = True

    execute: bool = True

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


class ExecuteRequest(BaseModel):

    task_id: str

    approved: bool = False

    action: str = "create_artifact"

    content: str = ""

    filename: str = "result.md"


class ToolRequest(BaseModel):

    tool: str = Field(
        min_length=1,
        max_length=100
    )

    approved: bool = False

    args: dict = Field(
        default_factory=dict
    )


# ============================================================
# BASIC UTILITIES
# ============================================================

def now_iso():

    return datetime.now(
        timezone.utc
    ).isoformat()


def uid(prefix):

    return (
        prefix
        + "-"
        + uuid.uuid4().hex[:12]
    )


def safe_filename(value):

    value = re.sub(
        r"[^a-zA-Z0-9_.-]+",
        "-",
        value or "artifact"
    )

    return value[:120]


def atomic_json_write(
    path,
    data
):

    temp = path.with_suffix(
        path.suffix + ".tmp"
    )

    temp.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    temp.replace(path)


def read_json(
    path,
    default=None
):

    if not path.exists():

        return default

    try:

        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    except Exception:

        return default


def hash_text(text):

    return hashlib.sha256(
        text.encode(
            "utf-8"
        )
    ).hexdigest()


def hash_bytes(data):

    return hashlib.sha256(
        data
    ).hexdigest()


def clean_text(text):

    return re.sub(
        r"\s+",
        " ",
        text or ""
    ).strip()


def normalize_url(url):

    try:

        parsed = urlparse(
            url
        )

        if parsed.scheme not in (
            "http",
            "https"
        ):

            return None

        query = parse_qs(
            parsed.query
        )

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

            if key.lower() in blocked:
                continue

            for value in values:

                clean_query.append(
                    f"{quote_plus(key)}="
                    f"{quote_plus(value)}"
                )

        result = (
            f"{parsed.scheme}://"
            f"{parsed.netloc}"
            f"{parsed.path or '/'}"
        )

        if clean_query:

            result += (
                "?"
                +
                "&".join(
                    clean_query
                )
            )

        return result

    except Exception:

        return None


def extract_domain(url):

    try:

        return urlparse(
            url
        ).netloc.lower()

    except Exception:

        return ""


# ============================================================
# AUDIT
# ============================================================

def audit(
    event,
    payload
):

    record = {

        "id":
            uid("evt"),

        "time":
            now_iso(),

        "event":
            event,

        "payload":
            payload,
    }

    atomic_json_write(

        AUDIT_DIR
        /
        (
            record["id"]
            +
            ".json"
        ),

        record
    )

    return record


# ============================================================
# INTENT ENGINE
# ============================================================

def classify_intent(
    objective
):

    text = (
        objective
        or ""
    ).lower()

    if any(
        x in text
        for x in (
            "build",
            "create",
            "develop",
            "implement",
            "code",
            "upgrade",
            "fix",
            "deploy",
        )
    ):

        return "build"

    if any(
        x in text
        for x in (
            "research",
            "investigate",
            "analyze",
            "analysis",
            "compare",
            "study",
            "find out",
        )
    ):

        return "research"

    if any(
        x in text
        for x in (
            "plan",
            "roadmap",
            "strategy",
            "future",
            "next",
        )
    ):

        return "strategy"

    if any(
        x in text
        for x in (
            "test",
            "verify",
            "audit",
            "validate",
            "check",
        )
    ):

        return "verification"

    return "general"


def detect_domains(
    objective
):

    text = (
        objective
        or ""
    ).lower()

    domains = []

    mapping = {

        "software": (
            "software",
            "code",
            "app",
            "api",
            "github",
            "program",
        ),

        "ai": (
            "ai",
            "artificial intelligence",
            "model",
            "agent",
            "machine learning",
        ),

        "science": (
            "science",
            "scientific",
            "experiment",
            "hypothesis",
        ),

        "business": (
            "business",
            "market",
            "money",
            "revenue",
            "customer",
        ),

        "research": (
            "research",
            "study",
            "evidence",
            "paper",
        ),

        "robotics": (
            "robot",
            "robotics",
            "sensor",
            "physical",
        ),

        "security": (
            "security",
            "privacy",
            "permission",
            "attack",
        ),
    }

    for domain, words in mapping.items():

        if any(
            word in text
            for word in words
        ):

            domains.append(
                domain
            )

    if not domains:

        domains.append(
            "general"
        )

    return domains


# ============================================================
# PLANNING ENGINE
# ============================================================

def create_plan(
    objective
):

    intent = classify_intent(
        objective
    )

    domains = detect_domains(
        objective
    )

    steps = [

        {
            "id": "understand",
            "type": "intent",
            "status": "ready",
        },

        {
            "id": "evidence",
            "type": "research",
            "status": "ready",
        },

        {
            "id": "minds",
            "type": "specialists",
            "status": "ready",
        },

        {
            "id": "simulate",
            "type": "counterfactual",
            "status": "ready",
        },

        {
            "id": "synthesize",
            "type": "intelligence",
            "status": "ready",
        },

        {
            "id": "execute",
            "type": "execution",
            "status": "ready",
        },

        {
            "id": "verify",
            "type": "verification",
            "status": "ready",
        },

        {
            "id": "learn",
            "type": "memory",
            "status": "ready",
        },
    ]

    return {

        "plan_id":
            uid("plan"),

        "objective":
            objective,

        "intent":
            intent,

        "domains":
            domains,

        "strategy":
            (
                "Understand → gather evidence → "
                "assemble specialist perspectives → "
                "simulate alternatives → synthesize → "
                "execute safely → verify → learn."
            ),

        "steps":
            steps,
    }


# ============================================================
# WEB RESEARCH
# ============================================================

def search_duckduckgo(
    query,
    max_results=8
):

    inc(
        "research_requests",
        "value"
    ) if False else None

    url = (
        "https://html.duckduckgo.com/"
        "html/?q="
        +
        quote_plus(query)
    )

    headers = {

        "User-Agent":
            (
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

            timeout=
                RESEARCH_TIMEOUT
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        results = []

        seen = set()

        for item in soup.select(
            ".result"
        ):

            link = item.select_one(
                ".result__a"
            )

            if not link:
                continue

            href = (
                link.get(
                    "href",
                    ""
                )
                .strip()
            )

            if not href:
                continue

            if "uddg=" in href:

                try:

                    href = parse_qs(
                        urlparse(
                            href
                        ).query
                    ).get(
                        "uddg",
                        [href]
                    )[0]

                except Exception:
                    pass

            clean_url = normalize_url(
                href
            )

            if not clean_url:
                continue

            if clean_url in seen:
                continue

            seen.add(
                clean_url
            )

            title = clean_text(
                link.get_text(
                    " ",
                    strip=True
                )
            )

            snippet_node = item.select_one(
                ".result__snippet"
            )

            snippet = clean_text(

                snippet_node.get_text(
                    " ",
                    strip=True
                )

                if snippet_node
                else ""
            )

            results.append({

                "id":
                    hash_text(
                        clean_url
                    )[:16],

                "title":
                    title,

                "url":
                    clean_url,

                "domain":
                    extract_domain(
                        clean_url
                    ),

                "snippet":
                    snippet,

                "retrieved_at":
                    now_iso(),
            })

            if len(results) >= max_results:
                break

        inc(
            "sources_found",
            "value",
            len(results)
        )

        return results

    except Exception as error:

        return [

            {

                "error":
                    "research_search_failed",

                "message":
                    str(error),
            }
        ]


def fetch_source(
    url
):

    headers = {

        "User-Agent":
            (
                "Mozilla/5.0 "
                "(compatible; "
                "AI-Infinity/20.0)"
            )
    }

    try:

        response = requests.get(

            url,

            headers=headers,

            timeout=
                RESEARCH_TIMEOUT,

            allow_redirects=True
        )

        response.raise_for_status()

        content_type = (
            response.headers
            .get(
                "content-type",
                ""
            )
            .lower()
        )

        if "text/html" not in content_type:

            return {

                "success":
                    False,

                "reason":
                    "not_html",
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
            or
            soup.find("article")
            or
            soup.body
        )

        text = clean_text(

            main.get_text(
                " ",
                strip=True
            )

            if main
            else ""
        )

        text = text[
            :MAX_SOURCE_CHARS
        ]

        if not text:

            return {

                "success":
                    False,

                "reason":
                    "empty",
            }

        return {

            "success":
                True,

            "url":
                url,

            "final_url":
                response.url,

            "title":
                title,

            "domain":
                extract_domain(
                    response.url
                ),

            "text":
                text,

            "status_code":
                response.status_code,

            "retrieved_at":
                now_iso(),
        }

    except Exception as error:

        return {

            "success":
                False,

            "reason":
                str(error),
        }


def verify_source(
    source
):

    fetched = fetch_source(
        source.get(
            "url",
            ""
        )
    )

    if fetched.get(
        "success"
    ):

        inc(
            "sources_verified",
            "value"
        )

        return {

            **source,
            **fetched,

            "verified":
                True,

            "verification_reason":
                "reachable_and_readable",
        }

    return {

        **source,

        "verified":
            False,

        "verification_reason":
            fetched.get(
                "reason",
                "unavailable"
            ),
    }


def research(
    objective,
    max_results=8,
    verify=True
):

    inc(
        "research_requests",
        "research_requests"
    )

    results = search_duckduckgo(

        objective,

        max_results
            =
            max_results
    )

    valid = [

        x for x in results
        if x.get("url")
    ]

    verified = []

    if verify and valid:

        workers = min(
            6,
            len(valid)
        )

        with ThreadPoolExecutor(
            max_workers=workers
        ) as executor:

            futures = [

                executor.submit(
                    verify_source,
                    item
                )

                for item in valid
            ]

            for future in as_completed(
                futures
            ):

                try:

                    verified.append(
                        future.result()
                    )

                except Exception:
                    pass

    else:

        verified = valid

    evidence = []

    for source in verified:

        if not source.get(
            "verified"
        ):

            continue

        evidence.append({

            "source_id":
                source.get("id"),

            "title":
                source.get("title"),

            "url":
                source.get("url"),

            "domain":
                source.get("domain"),

            "evidence":
                source.get(
                    "text",
                    ""
                )[:5000],

            "retrieved_at":
                source.get(
                    "retrieved_at"
                ),
        })

    return {

        "query":
            objective,

        "search_results":
            results,

        "verified_sources":
            verified,

        "evidence":
            evidence,

        "sources_found":
            len(valid),

        "sources_verified":
            len(evidence),
    }


# ============================================================
# TEMPORARY MIND FACTORY
# ============================================================

def create_temporary_minds(
    objective,
    domains
):

    minds = [

        {

            "id":
                uid("mind"),

            "role":
                "architect",

            "mission":
                "decompose the problem "
                "and design a practical solution.",
        },

        {

            "id":
                uid("mind"),

            "role":
                "researcher",

            "mission":
                "identify evidence, "
                "unknowns and constraints.",
        },

        {

            "id":
                uid("mind"),

            "role":
                "critic",

            "mission":
                "attack assumptions and "
                "identify failure modes.",
        },

        {

            "id":
                uid("mind"),

            "role":
                "builder",

            "mission":
                "turn the solution into "
                "concrete executable steps.",
        },

        {

            "id":
                uid("mind"),

            "role":
                "verifier",

            "mission":
                "define tests and "
                "independent verification.",
        },
    ]

    if "science" in domains:

        minds.append({

            "id":
                uid("mind"),

            "role":
                "scientist",

            "mission":
                "form hypotheses and "
                "propose controlled tests.",
        })

    if "security" in domains:

        minds.append({

            "id":
                uid("mind"),

            "role":
                "security_reviewer",

            "mission":
                "identify authorization, "
                "privacy and security risks.",
        })

    return {

        "factory":
            "temporary_mind_factory",

        "objective":
            objective,

        "minds":
            minds,

        "lifecycle":
            "assemble → solve → critique → "
            "verify → extract skill → discard runtime",
    }


# ============================================================
# COUNTERFACTUAL / SIMULATION LAB
# ============================================================

def simulate(
    objective
):

    return {

        "simulation_id":
            uid("sim"),

        "objective":
            objective,

        "status":
            "simulated",

        "scenarios": [

            {

                "name":
                    "baseline",

                "assumption":
                    "Current resources and "
                    "known constraints remain unchanged.",

                "expected_behavior":
                    "Follow the direct execution plan.",

                "risk":
                    "unknown",
            },

            {

                "name":
                    "resource_failure",

                "assumption":
                    "Primary AI provider or web "
                    "research temporarily fails.",

                "expected_behavior":
                    "Retry, use backup provider "
                    "or local fallback.",

                "risk":
                    "medium",
            },

            {

                "name":
                    "evidence_conflict",

                "assumption":
                    "Sources disagree.",

                "expected_behavior":
                    "Expose disagreement and "
                    "lower confidence.",

                "risk":
                    "medium",
            },

            {

                "name":
                    "execution_failure",

                "assumption":
                    "A planned action fails.",

                "expected_behavior":
                    "Stop dependent steps, diagnose, "
                    "retry safely or rollback.",

                "risk":
                    "high",
            },

            {

                "name":
                    "unexpected_opportunity",

                "assumption":
                    "New useful information appears.",

                "expected_behavior":
                    "Create a new branch for evaluation "
                    "rather than silently changing the goal.",

                "risk":
                    "unknown",
            },
        ],

        "warning":
            "Simulation is not evidence of real-world outcome.",
    }


# ============================================================
# DREAM ENGINE
# ============================================================

def dream(
    objective
):

    return {

        "dream_id":
            uid("dream"),

        "objective":
            objective,

        "mode":
            "sandbox_only",

        "questions": [

            "What assumption could be wrong?",

            "What alternative representation "
            "could solve this differently?",

            "What independent evidence is missing?",

            "What would make the current plan fail?",

            "Can two domains be combined to produce "
            "a better strategy?",

            "What is the smallest experiment that "
            "could falsify the current approach?",
        ],

        "candidate_directions": [

            "change representation",

            "split the problem",

            "use multiple independent specialists",

            "search for contradictory evidence",

            "simulate failure before execution",

            "extract reusable capability",
        ],

        "safety":
            "Dream outputs are hypotheses until verified.",
    }


# ============================================================
# LOCAL INTELLIGENCE FALLBACK
# ============================================================

def local_intelligence(
    objective,
    evidence=None
):

    evidence = evidence or []

    evidence_summary = "\n".join(

        "- "
        + str(
            item.get(
                "title",
                ""
            )
        )
        + ": "
        + str(
            item.get(
                "url",
                ""
            )
        )

        for item in evidence[:5]
    )

    text = f"""
AI Infinity local fallback response.

Objective:
{objective}

Evidence candidates:
{evidence_summary or "No external evidence available."}

Execution principle:
1. Understand the objective.
2. Separate facts from assumptions.
3. Use available evidence.
4. Produce a concrete plan.
5. Execute only safe/reversible actions automatically.
6. Verify outputs.
7. Store successful patterns for future use.

The external AI providers were unavailable, so this response
was generated by the deterministic local fallback.
"""

    return text.strip()


# ============================================================
# AI PROVIDERS
# ============================================================

def provider_request(
    base_url,
    token,
    model,
    messages
):

    headers = {

        "Authorization":
            "Bearer "
            +
            token,

        "Content-Type":
            "application/json",

    }

    payload = {

        "model":
            model,

        "messages":
            messages,

        "temperature":
            0.2,

        "max_tokens":
            1800,
    }

    response = requests.post(

        base_url,

        headers=headers,

        json=payload,

        timeout=
            REQUEST_TIMEOUT
    )

    if response.status_code >= 400:

        raise RuntimeError(

            f"HTTP {response.status_code}: "
            f"{response.text[:3000]}"
        )

    data = response.json()

    choices = data.get(
        "choices",
        []
    )

    if not choices:

        raise RuntimeError(
            "Provider returned no choices."
        )

    message = choices[0].get(
        "message",
        {}
    )

    content = (
        message.get(
            "content"
        )
        or ""
    )

    if not content:

        raise RuntimeError(
            "Provider returned empty content."
        )

    return content


def ai_generate(
    objective,
    evidence=None,
    minds=None,
    simulation=None,
    dream_data=None
):

    evidence = evidence or []

    minds = minds or []

    system = """
You are the reasoning engine inside AI Infinity.

Produce useful, concrete, evidence-aware work.

Rules:
- distinguish facts from assumptions;
- do not invent evidence;
- identify uncertainty;
- use the supplied evidence;
- use specialist perspectives;
- consider failure modes;
- prefer practical implementation;
- do not claim that simulations prove reality;
- never claim AGI/ASI merely because a system is modular;
- when an action would change an external system, mark it as
  requiring explicit authorization.
"""

    evidence_text = "\n".join(

        f"[{i+1}] "
        f"{item.get('title','')} "
        f"({item.get('url','')})\n"
        f"{item.get('evidence','')[:1800]}"

        for i, item
        in enumerate(
            evidence[:8]
        )
    )

    minds_text = "\n".join(

        f"- {mind.get('role')}: "
        f"{mind.get('mission')}"

        for mind in minds
    )

    user = f"""
OBJECTIVE:
{objective}

SPECIALIST MINDS:
{minds_text}

EVIDENCE:
{evidence_text or "No verified external evidence."}

SIMULATION:
{json.dumps(
    simulation or {},
    ensure_ascii=False
)[:8000]}

DREAM/HYPOTHESIS LAYER:
{json.dumps(
    dream_data or {},
    ensure_ascii=False
)[:5000]}

Return:

1. Understanding
2. Key findings
3. Recommended implementation
4. Concrete next actions
5. Risks/failure modes
6. Verification plan
7. Reusable capability that should be remembered
"""

    messages = [

        {
            "role":
                "system",

            "content":
                system,
        },

        {
            "role":
                "user",

            "content":
                user,
        },
    ]

    # --------------------------------------------------------
    # Provider 1: Hugging Face
    # --------------------------------------------------------

    if HF_TOKEN:

        inc(
            "provider_attempts",
            "huggingface"
        )

        try:

            result = provider_request(

                HF_BASE_URL,

                HF_TOKEN,

                HF_MODEL,

                messages
            )

            inc(
                "provider_successes",
                "huggingface"
            )

            return {

                "ok":
                    True,

                "provider":
                    "huggingface",

                "model":
                    HF_MODEL,

                "text":
                    result,

                "message":
                    "AI response generated.",
            }

        except Exception as error:

            inc(
                "provider_failures",
                "huggingface"
            )

            hf_error = str(
                error
            )

    else:

        hf_error = (
            "HF_TOKEN not configured."
        )


    # --------------------------------------------------------
    # Provider 2: Pollinations
    # --------------------------------------------------------

    if POLLINATIONS_API_KEY:

        inc(
            "provider_attempts",
            "pollinations"
        )

        try:

            result = provider_request(

                POLLINATIONS_BASE_URL,

                POLLINATIONS_API_KEY,

                POLLINATIONS_MODEL,

                messages
            )

            inc(
                "provider_successes",
                "pollinations"
            )

            return {

                "ok":
                    True,

                "provider":
                    "pollinations",

                "model":
                    POLLINATIONS_MODEL,

                "text":
                    result,

                "message":
                    "Backup AI response generated.",
            }

        except Exception as error:

            inc(
                "provider_failures",
                "pollinations"
            )

            pollinations_error = str(
                error
            )

    else:

        pollinations_error = (
            "POLLINATIONS_API_KEY not configured."
        )


    # --------------------------------------------------------
    # Provider 3: deterministic local fallback
    # --------------------------------------------------------

    inc(
        "provider_attempts",
        "local"
    )

    inc(
        "provider_successes",
        "local"
    )

    return {

        "ok":
            True,

        "provider":
            "local",

        "model":
            "deterministic-fallback",

        "text":
            local_intelligence(
                objective,
                evidence
            ),

        "message":
            "External providers unavailable; "
            "local fallback used.",

        "provider_errors":
            {

                "huggingface":
                    hf_error,

                "pollinations":
                    pollinations_error,
            },
    }


# ============================================================
# ARTIFACT EXECUTION ENGINE
# ============================================================

def create_artifact(
    task_id,
    filename,
    content
):

    filename = safe_filename(
        filename
    )

    if not filename:

        filename = "result.md"

    if len(
        content.encode(
            "utf-8"
        )
    ) > MAX_ARTIFACT_SIZE:

        raise HTTPException(
            413,
            "Artifact is too large."
        )

    task_folder = (
        ARTIFACT_DIR
        /
        safe_filename(task_id)
    )

    task_folder.mkdir(
        parents=True,
        exist_ok=True
    )

    path = (
        task_folder
        /
        filename
    )

    # Final path safety check.
    if (
        task_folder.resolve()
        not in
        path.resolve().parents
    ):

        raise HTTPException(
            400,
            "Unsafe artifact path."
        )

    path.write_text(
        content,
        encoding="utf-8"
    )

    data = path.read_bytes()

    result = {

        "success":
            True,

        "task_id":
            task_id,

        "filename":
            filename,

        "path":
            str(path),

        "bytes":
            len(data),

        "sha256":
            hash_bytes(data),

        "reversible":
            True,

        "external":
            False,

        "created_at":
            now_iso(),
    }

    inc(
        "artifacts_created",
        "value"
    )

    audit(
        "artifact_created",
        result
    )

    return result


# ============================================================
# ARTIFACT VERIFICATION
# ============================================================

def verify_artifact(
    artifact
):

    path = Path(
        artifact.get(
            "path",
            ""
        )
    )

    if not path.exists():

        inc(
            "verification_failures",
            "value"
        )

        return {

            "verified":
                False,

            "reason":
                "artifact_missing",
        }

    data = path.read_bytes()

    actual_hash = hash_bytes(
        data
    )

    hash_ok = (
        actual_hash
        ==
        artifact.get(
            "sha256"
        )
    )

    syntax_check = None

    if path.suffix == ".py":

        try:

            ast.parse(
                data.decode(
                    "utf-8"
                )
            )

            syntax_check = True

        except SyntaxError:

            syntax_check = False

    verified = (
        hash_ok
        and
        (
            syntax_check
            is not False
        )
    )

    if verified:

        inc(
            "verification_passes",
            "value"
        )

    else:

        inc(
            "verification_failures",
            "value"
        )

    result = {

        "verified":
            verified,

        "hash_match":
            hash_ok,

        "python_syntax_valid":
            syntax_check,

        "actual_sha256":
            actual_hash,

        "checked_at":
            now_iso(),
    }

    audit(
        "artifact_verified",
        result
    )

    return result


# ============================================================
# MEMORY ENGINE
# ============================================================

def save_memory(
    objective,
    result,
    evidence=None
):

    memory_id = uid(
        "mem"
    )

    memory = {

        "id":
            memory_id,

        "created_at":
            now_iso(),

        "objective":
            objective,

        "summary":
            result[:6000],

        "evidence":
            evidence or [],

        "hash":
            hash_text(
                objective
                +
                result
            ),

        "confidence":
            "unrated",

        "provenance":
            "AI Infinity execution pipeline",
    }

    path = (
        MEMORY_DIR
        /
        (
            memory_id
            +
            ".json"
        )
    )

    atomic_json_write(
        path,
        memory
    )

    inc(
        "memory_writes",
        "value"
    )

    return memory


def recent_memory(
    limit=10
):

    files = sorted(

        MEMORY_DIR.glob(
            "mem-*.json"
        ),

        key=lambda p:
            p.stat().st_mtime,

        reverse=True
    )

    return [

        read_json(
            path
        )

        for path in files[:limit]

    ]


# ============================================================
# INTELLIGENCE GENOME
# ============================================================

def create_genome(
    task,
    intelligence,
    verification
):

    genome_id = uid(
        "genome"
    )

    text = intelligence.get(
        "text",
        ""
    )

    genome = {

        "id":
            genome_id,

        "created_at":
            now_iso(),

        "objective":
            task.get(
                "objective"
            ),

        "intent":
            task.get(
                "plan",
                {}
            ).get(
                "intent"
            ),

        "domains":
            task.get(
                "plan",
                {}
            ).get(
                "domains",
                []
            ),

        "strategy":
            task.get(
                "plan",
                {}
            ).get(
                "strategy"
            ),

        "provider":
            intelligence.get(
                "provider"
            ),

        "reasoning_output_hash":
            hash_text(
                text
            ),

        "verification":
            verification,

        "capabilities":
            [

                "intent_analysis",

                "web_research",

                "temporary_specialists",

                "counterfactual_simulation",

                "dream_generation",

                "safe_artifact_execution",

                "verification",

                "memory",

            ],

        "failure_modes":
            [

                "provider_failure",

                "source_failure",

                "evidence_conflict",

                "artifact_failure",

                "authorization_failure",

            ],

        "reusable":
            True,
    }

    atomic_json_write(

        GENOME_DIR
        /
        (
            genome_id
            +
            ".json"
        ),

        genome
    )

    return genome


# ============================================================
# SELF-HEALING
# ============================================================

def recovery_plan(
    error
):

    text = str(
        error
    ).lower()

    if (
        "timeout" in text
        or
        "connection" in text
    ):

        action = (
            "retry → backup provider → "
            "local fallback"
        )

    elif (
        "401" in text
        or
        "403" in text
    ):

        action = (
            "stop external operation → "
            "check credentials/permissions"
        )

    elif "400" in text:

        action = (
            "inspect request → "
            "correct configuration → retry"
        )

    else:

        action = (
            "capture failure → "
            "isolate step → retry safely → "
            "rollback if necessary"
        )

    return {

        "recovery":
            action,

        "automatic_external_change":
            False,

        "error":
            str(error)[:2000],
    }


# ============================================================
# REAL-WORLD TOOL LAYER
# ============================================================

def safe_repo(
    repo
):

    repo = (
        repo
        or GITHUB_REPO
    ).strip()

    if (
        repo.count("/")
        != 1
        or ".." in repo
        or "\\" in repo
        or " " in repo
    ):

        raise HTTPException(
            400,
            "Invalid GitHub repository."
        )

    return repo


def safe_repo_path(
    path
):

    path = (
        path
        or ""
    ).strip("/")

    if (
        not path
        or
        ".." in Path(path).parts
    ):

        raise HTTPException(
            400,
            "Invalid repository path."
        )

    return path


def github_headers():

    if not GITHUB_TOKEN:

        raise HTTPException(
            503,
            "GITHUB_TOKEN is not configured."
        )

    return {

        "Authorization":
            "Bearer "
            +
            GITHUB_TOKEN,

        "Accept":
            "application/vnd.github+json",

        "X-GitHub-Api-Version":
            "2022-11-28",

        "User-Agent":
            "AI-Infinity",
    }


def github_request(
    method,
    url,
    **kwargs
):

    try:

        response = requests.request(

            method,

            url,

            headers=
                github_headers(),

            timeout=
                REQUEST_TIMEOUT,

            **kwargs
        )

    except requests.RequestException as error:

        raise HTTPException(
            502,
            f"GitHub connection failed: {error}"
        )

    if response.status_code >= 400:

        try:

            detail = response.json()

        except Exception:

            detail = response.text[
                :3000
            ]

        raise HTTPException(
            response.status_code,
            detail
        )

    try:

        return response.json()

    except Exception:

        return {

            "status_code":
                response.status_code,

            "text":
                response.text,
        }


def github_get_file(
    repo,
    path,
    ref=None
):

    repo = safe_repo(
        repo
    )

    path = safe_repo_path(
        path
    )

    url = (
        "https://api.github.com/repos/"
        +
        repo
        +
        "/contents/"
        +
        path
    )

    params = (

        {"ref": ref}

        if ref

        else None
    )

    data = github_request(

        "GET",

        url,

        params=params
    )

    if isinstance(
        data,
        list
    ):

        return {

            "success":
                True,

            "type":
                "directory",

            "items":
                data,
        }

    encoded = data.get(
        "content",
        ""
    )

    try:

        decoded = base64.b64decode(
            encoded
        ).decode(
            "utf-8"
        )

    except Exception:

        decoded = ""

    return {

        "success":
            True,

        "tool":
            "github_get_file",

        "name":
            data.get("name"),

        "path":
            data.get("path"),

        "sha":
            data.get("sha"),

        "url":
            data.get("html_url"),

        "content":
            decoded,
    }


def require_external_write(
    tool,
    approved
):

    if not approved:

        raise HTTPException(

            403,

            {

                "error":
                    "explicit_approval_required",

                "tool":
                    tool,

                "message":
                    "This action changes external state. "
                    "Explicit approval is required.",
            }
        )

    if not ALLOW_EXTERNAL_WRITES:

        raise HTTPException(

            403,

            {

                "error":
                    "external_writes_disabled",

                "tool":
                    tool,

                "message":
                    "AI Infinity external writes are disabled.",
            }
        )


def github_create_issue(
    repo,
    title,
    body,
    labels,
    approved
):

    require_external_write(

        "github_create_issue",

        approved
    )

    repo = safe_repo(
        repo
    )

    if not title.strip():

        raise HTTPException(
            400,
            "Issue title required."
        )

    payload = {

        "title":
            title[:256],

        "body":
            (body or "")[
                :MAX_ARTIFACT_SIZE
            ],
    }

    if labels:

        payload[
            "labels"
        ] = [
            str(x)
            for x in labels[:10]
        ]

    result = github_request(

        "POST",

        (
            "https://api.github.com/"
            "repos/"
            +
            repo
            +
            "/issues"
        ),

        json=payload
    )

    output = {

        "success":
            True,

        "tool":
            "github_create_issue",

        "number":
            result.get("number"),

        "url":
            result.get("html_url"),

        "title":
            result.get("title"),
    }

    audit(
        "github_issue_created",
        output
    )

    return output


def github_upsert_file(
    repo,
    path,
    content,
    message,
    branch,
    approved
):

    require_external_write(

        "github_upsert_file",

        approved
    )

    repo = safe_repo(
        repo
    )

    path = safe_repo_path(
        path
    )

    if len(
        content.encode(
            "utf-8"
        )
    ) > MAX_ARTIFACT_SIZE:

        raise HTTPException(
            413,
            "File too large."
        )

    url = (
        "https://api.github.com/repos/"
        +
        repo
        +
        "/contents/"
        +
        path
    )

    current_sha = None

    try:

        current = github_request(

            "GET",

            url,

            params=(
                {"ref": branch}
                if branch
                else None
            )
        )

        if isinstance(
            current,
            dict
        ):

            current_sha = current.get(
                "sha"
            )

    except HTTPException as error:

        if error.status_code != 404:
            raise

    payload = {

        "message":
            (
                message
                or
                "AI Infinity update"
            )[:256],

        "content":
            base64.b64encode(
                content.encode(
                    "utf-8"
                )
            ).decode(
                "ascii"
            ),
    }

    if branch:

        payload[
            "branch"
        ] = branch

    if current_sha:

        payload[
            "sha"
        ] = current_sha

    result = github_request(

        "PUT",

        url,

        json=payload
    )

    output = {

        "success":
            True,

        "tool":
            "github_upsert_file",

        "repo":
            repo,

        "path":
            path,

        "commit_url":
            result.get(
                "commit",
                {}
            ).get(
                "html_url"
            ),

        "content_sha":
            result.get(
                "content",
                {}
            ).get(
                "sha"
            ),
    }

    audit(
        "github_file_written",
        output
    )

    return output


def readonly_http_get(
    url
):

    parsed = urlparse(
        url
    )

    if (
        parsed.scheme
        not in (
            "http",
            "https"
        )
        or
        not parsed.netloc
    ):

        raise HTTPException(
            400,
            "Only HTTP/HTTPS URLs are allowed."
        )

    try:

        response = requests.get(

            url,

            headers={
                "User-Agent":
                    "AI-Infinity"
            },

            timeout=
                REQUEST_TIMEOUT,

            allow_redirects=True
        )

    except requests.RequestException as error:

        raise HTTPException(
            502,
            str(error)
        )

    raw = response.content

    return {

        "success":
            True,

        "tool":
            "http_get",

        "status_code":
            response.status_code,

        "url":
            response.url,

        "content_type":
            response.headers.get(
                "content-type",
                ""
            ),

        "bytes":
            len(raw),

        "sha256":
            hash_bytes(raw),

        "body":
            response.text[
                :MAX_ARTIFACT_SIZE
            ],

        "read_only":
            True,
    }


TOOLS = [

    {

        "name":
            "http_get",

        "type":
            "read",

        "approval":
            "not_required",

    },

    {

        "name":
            "github_get_file",

        "type":
            "read",

        "approval":
            "not_required",

    },

    {

        "name":
            "create_artifact",

        "type":
            "safe_local_write",

        "approval":
            "not_required",

    },

    {

        "name":
            "github_create_issue",

        "type":
            "external_write",

        "approval":
            "required",

    },

    {

        "name":
            "github_upsert_file",

        "type":
            "external_write",

        "approval":
            "required",

    },
]


def execute_tool(
    request
):

    audit(

        "tool_requested",

        {

            "tool":
                request.tool,

            "approved":
                request.approved,

            "argument_names":
                sorted(
                    request.args.keys()
                ),
        }
    )

    args = request.args

    if request.tool == "http_get":

        return readonly_http_get(
            args.get(
                "url",
                ""
            )
        )

    if request.tool == "github_get_file":

        return github_get_file(

            args.get(
                "repo"
            ),

            args.get(
                "path",
                ""
            ),

            args.get(
                "ref"
            )
        )

    if request.tool == "create_artifact":

        task_id = args.get(
            "task_id",
            uid("task")
        )

        return create_artifact(

            task_id,

            args.get(
                "filename",
                "result.md"
            ),

            args.get(
                "content",
                ""
            )
        )

    if request.tool == "github_create_issue":

        return github_create_issue(

            args.get(
                "repo"
            ),

            args.get(
                "title",
                ""
            ),

            args.get(
                "body",
                ""
            ),

            args.get(
                "labels",
                []
            ),

            request.approved
        )

    if request.tool == "github_upsert_file":

        return github_upsert_file(

            args.get(
                "repo"
            ),

            args.get(
                "path",
                ""
            ),

            args.get(
                "content",
                ""
            ),

            args.get(
                "message",
                "AI Infinity update"
            ),

            args.get(
                "branch"
            ),

            request.approved
        )

    raise HTTPException(
        404,
        "Unknown tool."
    )


# ============================================================
# MASTER EXECUTION PIPELINE
# ============================================================

def run_infinity(
    request
):

    objective = (
        request.objective
        or
        request.command
        or
        ""
    ).strip()

    if not objective:

        raise HTTPException(
            400,
            "command or objective is required."
        )

    task_id = uid(
        "task"
    )

    inc(
        "tasks",
        "value"
    )

    task = {

        "task_id":
            task_id,

        "created_at":
            now_iso(),

        "objective":
            objective,

        "status":
            "running",
    }

    atomic_json_write(

        TASK_DIR
        /
        (
            task_id
            +
            ".json"
        ),

        task
    )

    audit(

        "task_started",

        {

            "task_id":
                task_id,

            "objective":
                objective,
        }
    )

    try:

        # ----------------------------------------------------
        # 1. INTENT
        # ----------------------------------------------------

        plan = create_plan(
            objective
        )

        task[
            "plan"
        ] = plan

        # ----------------------------------------------------
        # 2. RESEARCH
        # ----------------------------------------------------

        evidence_package = {

            "evidence":
                [],

            "verified_sources":
                [],

            "sources_found":
                0,

            "sources_verified":
                0,
        }

        if request.research:

            evidence_package = research(

                objective,

                max_results=
                    request.max_sources,

                verify=
                    request.verify
            )

        task[
            "research"
        ] = evidence_package

        # ----------------------------------------------------
        # 3. TEMPORARY MINDS
        # ----------------------------------------------------

        mind_factory = (
            create_temporary_minds(

                objective,

                plan[
                    "domains"
                ]
            )
        )

        task[
            "temporary_minds"
        ] = mind_factory

        # ----------------------------------------------------
        # 4. SIMULATION
        # ----------------------------------------------------

        simulation = (

            simulate(
                objective
            )

            if request.simulate

            else None
        )

        task[
            "simulation"
        ] = simulation

        # ----------------------------------------------------
        # 5. DREAM
        # ----------------------------------------------------

        dream_data = (

            dream(
                objective
            )

            if request.dream

            else None
        )

        task[
            "dream"
        ] = dream_data

        # ----------------------------------------------------
        # 6. INTELLIGENCE
        # ----------------------------------------------------

        intelligence = ai_generate(

            objective,

            evidence=
                evidence_package.get(
                    "evidence",
                    []
                ),

            minds=
                mind_factory.get(
                    "minds",
                    []
                ),

            simulation=
                simulation,

            dream_data=
                dream_data
        )

        task[
            "intelligence"
        ] = intelligence

        # ----------------------------------------------------
        # 7. SAFE EXECUTION
        # ----------------------------------------------------

        artifact = None

        if (
            request.execute
            and
            intelligence.get(
                "text"
            )
        ):

            artifact = create_artifact(

                task_id,

                "intelligence-result.md",

                intelligence[
                    "text"
                ]
            )

        task[
            "execution"
        ] = {

            "status":
                (
                    "completed"
                    if artifact
                    else
                    "skipped"
                ),

            "artifact":
                artifact,

            "external_writes":
                False,

            "zero_dollar_mode":
                ZERO_DOLLAR_MODE,
        }

        # ----------------------------------------------------
        # 8. VERIFICATION
        # ----------------------------------------------------

        verification = {

            "status":
                "pending",

            "evidence_verified":
                evidence_package.get(
                    "sources_verified",
                    0
                ),

            "simulation_is_not_evidence":
                True,
        }

        if artifact:

            artifact_verification = (
                verify_artifact(
                    artifact
                )
            )

            verification[
                "artifact"
            ] = artifact_verification

            verification[
                "status"
            ] = (

                "verified"

                if artifact_verification.get(
                    "verified"
                )

                else
                "failed"
            )

        task[
            "verification"
        ] = verification

        # ----------------------------------------------------
        # 9. MEMORY
        # ----------------------------------------------------

        memory = None

        if request.remember:

            memory = save_memory(

                objective,

                intelligence.get(
                    "text",
                    ""
                ),

                evidence_package.get(
                    "evidence",
                    []
                )
            )

        task[
            "memory"
        ] = memory

        # ----------------------------------------------------
        # 10. INTELLIGENCE GENOME
        # ----------------------------------------------------

        genome = create_genome(

            task,

            intelligence,

            verification
        )

        task[
            "genome"
        ] = genome

        # ----------------------------------------------------
        # COMPLETE
        # ----------------------------------------------------

        task[
            "status"
        ] = "completed"

        task[
            "completed_at"
        ] = now_iso()

        inc(
            "completed",
            "value"
        )

        atomic_json_write(

            TASK_DIR
            /
            (
                task_id
                +
                ".json"
            ),

            task
        )

        audit(

            "task_completed",

            {

                "task_id":
                    task_id,

                "verification":
                    verification.get(
                        "status"
                    ),

                "artifact":
                    bool(artifact),

            }
        )

        return task

    except Exception as error:

        recovery = recovery_plan(
            error
        )

        task[
            "status"
        ] = "error"

        task[
            "error"
        ] = str(error)

        task[
            "recovery"
        ] = recovery

        task[
            "failed_at"
        ] = now_iso()

        inc(
            "failed",
            "value"
        )

        atomic_json_write(

            TASK_DIR
            /
            (
                task_id
                +
                ".json"
            ),

            task
        )

        audit(

            "task_failed",

            {

                "task_id":
                    task_id,

                "error":
                    str(error)[:2000],

                "recovery":
                    recovery,
            }
        )

        return task


# ============================================================
# API
# ============================================================

@app.get(
    "/health"
)
def health():

    return {

        "status":
            "ok",

        "service":
            APP_NAME,

        "version":
            VERSION,

        "mode":
            "integrated-intelligence",

        "zero_dollar":
            ZERO_DOLLAR_MODE,

        "external_writes":
            ALLOW_EXTERNAL_WRITES,

        "ai_provider":
            (
                "huggingface"
                if HF_TOKEN
                else
                (
                    "pollinations"
                    if POLLINATIONS_API_KEY
                    else
                    "local"
                )
            ),
    }


@app.get(
    "/v1/status"
)
def status():

    uptime = (
        time.time()
        -
        telemetry[
            "started_at"
        ]
    )

    return {

        "service":
            APP_NAME,

        "version":
            VERSION,

        "uptime_seconds":
            round(
                uptime,
                2
            ),

        "architecture":
            [

                "intent",

                "research",

                "temporary_minds",

                "simulation",

                "dream",

                "ai_routing",

                "execution",

                "verification",

                "memory",

                "genome",

                "self_healing",

                "tool_registry",

                "permission_gate",

            ],

        "governance":
            {

                "zero_dollar":
                    ZERO_DOLLAR_MODE,

                "external_writes":
                    ALLOW_EXTERNAL_WRITES,

                "automatic_spending":
                    False,

                "automatic_external_writes":
                    False,
            },

        "telemetry":
            telemetry,
    }


@app.post(
    "/v1/ask"
)
def ask(
    request: TaskRequest
):

    objective = (
        request.objective
        or
        request.command
        or
        ""
    ).strip()

    if not objective:

        raise HTTPException(
            400,
            "command or objective is required."
        )

    result = ai_generate(
        objective
    )

    return result


@app.post(
    "/v1/research"
)
def research_endpoint(
    request: ResearchRequest
):

    return research(

        request.query,

        request.max_results,

        True
    )


@app.post(
    "/v1/orchestrate"
)
def orchestrate(
    request: TaskRequest
):

    return run_infinity(
        request
    )


@app.post(
    "/v1/run"
)
def run(
    request: TaskRequest
):

    return run_infinity(
        request
    )


@app.post(
    "/v1/execute"
)
def execute(
    request: ExecuteRequest
):

    if request.action != "create_artifact":

        raise HTTPException(
            400,
            "Only safe local artifact execution "
            "is automatic in this version."
        )

    artifact = create_artifact(

        request.task_id,

        request.filename,

        request.content
    )

    verification = verify_artifact(
        artifact
    )

    return {

        "execution":
            artifact,

        "verification":
            verification,
    }


@app.post(
    "/v1/tools/execute"
)
def execute_tool_endpoint(
    request: ToolRequest
):

    return execute_tool(
        request
    )


@app.get(
    "/v1/tools"
)
def tools():

    return {

        "service":
            APP_NAME,

        "tools":
            TOOLS,

        "github_configured":
            bool(GITHUB_TOKEN),

        "external_writes_enabled":
            ALLOW_EXTERNAL_WRITES,

        "automatic_external_writes":
            False,
    }


@app.get(
    "/v1/tools/audit"
)
def audit_log():

    events = []

    files = sorted(

        AUDIT_DIR.glob(
            "evt-*.json"
        ),

        key=lambda p:
            p.stat().st_mtime,

        reverse=True
    )

    for path in files[:100]:

        item = read_json(
            path
        )

        if item:

            events.append(
                item
            )

    return {

        "count":
            len(events),

        "events":
            events,
    }


@app.get(
    "/v1/memory"
)
def memory():

    return {

        "count":
            len(
                recent_memory(
                    MAX_MEMORY_ITEMS
                )
            ),

        "items":
            recent_memory(
                MAX_MEMORY_ITEMS
            ),
    }


@app.get(
    "/v1/tasks/{task_id}"
)
def get_task(
    task_id: str
):

    path = (
        TASK_DIR
        /
        (
            safe_filename(
                task_id
            )
            +
            ".json"
        )
    )

    data = read_json(
        path
    )

    if not data:

        raise HTTPException(
            404,
            "Task not found."
        )

    return data


@app.get(
    "/v1/events"
)
def events():

    return audit_log()


@app.get(
    "/v1/genomes"
)
def genomes():

    files = sorted(

        GENOME_DIR.glob(
            "genome-*.json"
        ),

        key=lambda p:
            p.stat().st_mtime,

        reverse=True
    )

    items = [

        read_json(
            path
        )

        for path in files[:100]
    ]

    return {

        "count":
            len(items),

        "genomes":
            items,
    }


@app.get(
    "/v1/intent"
)
def intent(
    objective: str
):

    return {

        "objective":
            objective,

        "intent":
            classify_intent(
                objective
            ),

        "domains":
            detect_domains(
                objective
            ),

        "plan":
            create_plan(
                objective
            ),
    }


@app.get(
    "/v1/simulate"
)
def simulation(
    objective: str
):

    return simulate(
        objective
    )


@app.get(
    "/v1/dream"
)
def dream_endpoint(
    objective: str
):

    return dream(
        objective
    )


# ============================================================
# UI
# ============================================================

HTML = r"""
<!doctype html>

<html>

<head>

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>AI Infinity</title>

<style>

body{
    margin:0;
    background:#08090d;
    color:#f4f5f7;
    font-family:Arial,sans-serif;
}

.container{
    max-width:1000px;
    margin:auto;
    padding:28px 18px 60px;
}

h1{
    font-size:42px;
    margin-bottom:6px;
}

.subtitle{
    color:#9da4b3;
    margin-bottom:24px;
}

.card{
    background:#11141b;
    border:1px solid #252b36;
    border-radius:18px;
    padding:20px;
    margin-bottom:18px;
}

textarea{
    width:100%;
    min-height:150px;
    box-sizing:border-box;
    background:#090b10;
    color:white;
    border:1px solid #303744;
    border-radius:12px;
    padding:14px;
    font-size:16px;
    resize:vertical;
}

button{
    background:#f4f5f7;
    color:#08090d;
    border:0;
    border-radius:10px;
    padding:13px 17px;
    margin:8px 6px 0 0;
    font-weight:bold;
    cursor:pointer;
}

button.secondary{
    background:#202632;
    color:white;
}

pre{
    white-space:pre-wrap;
    overflow:auto;
    background:#080a0f;
    border-radius:12px;
    padding:15px;
    border:1px solid #252b36;
}

.badge{
    display:inline-block;
    padding:7px 10px;
    border-radius:20px;
    background:#202632;
    margin:4px;
    font-size:13px;
}

.small{
    color:#8d95a5;
    font-size:13px;
}

</style>

</head>

<body>

<div class="container">

<h1>∞ AI Infinity</h1>

<div class="subtitle">
Integrated Intelligence Fabric ·
Plan → Research → Minds → Simulate →
Execute → Verify → Learn
</div>

<div class="card">

<div class="small">
Give AI Infinity a real objective.
</div>

<br>

<textarea id="objective"
placeholder="Example:
Analyze AI Infinity and design the next major capability, research current evidence, simulate failure modes, create an implementation plan, verify it, and remember the result."></textarea>

<br>

<button onclick="runInfinity()">
Run AI Infinity
</button>

<button class="secondary"
onclick="dream()">
Dream
</button>

<button class="secondary"
onclick="simulate()">
Simulate
</button>

<button class="secondary"
onclick="research()">
Research
</button>

</div>

<div class="card">

<div id="status">

Ready.

</div>

<pre id="output"></pre>

</div>

<div class="card">

<b>Core Systems</b>

<br><br>

<span class="badge">Intent</span>
<span class="badge">Research</span>
<span class="badge">Temporary Minds</span>
<span class="badge">Simulation</span>
<span class="badge">Dream Engine</span>
<span class="badge">AI Router</span>
<span class="badge">Execution</span>
<span class="badge">Verification</span>
<span class="badge">Memory</span>
<span class="badge">Genome</span>
<span class="badge">Self-Healing</span>
<span class="badge">Tool Registry</span>
<span class="badge">Permission Gate</span>
<span class="badge">$0 Governor</span>

</div>

</div>

<script>

async function post(url, body){

    const response =
        await fetch(
            url,
            {
                method:"POST",
                headers:{
                    "Content-Type":
                        "application/json"
                },
                body:
                    JSON.stringify(body)
            }
        );

    return await response.json();
}

async function runInfinity(){

    const objective =
        document
        .getElementById("objective")
        .value;

    if(!objective.trim()){
        return;
    }

    document
    .getElementById("status")
    .innerText =
        "AI Infinity is running...";

    const result =
        await post(
            "/v1/run",
            {
                objective:
                    objective,

                command:
                    objective,

                research:true,

                verify:true,

                remember:true,

                simulate:true,

                dream:true,

                execute:true
            }
        );

    document
    .getElementById("status")
    .innerText =
        "Completed.";

    document
    .getElementById("output")
    .innerText =
        JSON.stringify(
            result,
            null,
            2
        );
}

async function dream(){

    const objective =
        document
        .getElementById("objective")
        .value;

    const result =
        await fetch(
            "/v1/dream?objective="
            +
            encodeURIComponent(
                objective
            )
        );

    document
    .getElementById("output")
    .innerText =
        JSON.stringify(
            await result.json(),
            null,
            2
        );
}

async function simulate(){

    const objective =
        document
        .getElementById("objective")
        .value;

    const result =
        await fetch(
            "/v1/simulate?objective="
            +
            encodeURIComponent(
                objective
            )
        );

    document
    .getElementById("output")
    .innerText =
        JSON.stringify(
            await result.json(),
            null,
            2
        );
}

async function research(){

    const objective =
        document
        .getElementById("objective")
        .value;

    const result =
        await post(
            "/v1/research",
            {
                query:
                    objective,

                max_results:8
            }
        );

    document
    .getElementById("output")
    .innerText =
        JSON.stringify(
            result,
            null,
            2
        );
}

</script>

</body>

</html>
"""


@app.get(
    "/",
    response_class=HTMLResponse
)
def homepage():

    return HTML


# ============================================================
# STARTUP
# ============================================================

@app.on_event(
    "startup"
)
def startup():

    audit(

        "system_started",

        {

            "version":
                VERSION,

            "zero_dollar":
                ZERO_DOLLAR_MODE,

            "external_writes":
                ALLOW_EXTERNAL_WRITES,

            "github_configured":
                bool(
                    GITHUB_TOKEN
                ),

            "hf_configured":
                bool(
                    HF_TOKEN
                ),

            "pollinations_configured":
                bool(
                    POLLINATIONS_API_KEY
                ),
        }
    )
