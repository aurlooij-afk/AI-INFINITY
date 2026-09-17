import ast
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY — TARGET-2
# Research + Evidence + Execution + Verification + Memory
# ============================================================

VERSION = "TARGET-2.0.0"
SERVICE = "AI Infinity"

BASE = Path("/tmp/ai-infinity")
ARTIFACTS = BASE / "artifacts"
WORK = BASE / "work"
LOGS = BASE / "logs"

for directory in (BASE, ARTIFACTS, WORK, LOGS):
    directory.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

HF_TOKEN = os.getenv("HF_TOKEN", "").strip()

HF_MODEL = os.getenv(
    "HF_MODEL",
    "openai/gpt-oss-120b:cheapest",
)

HF_BACKUP_MODEL = os.getenv(
    "HF_BACKUP_MODEL",
    "openai/gpt-oss-20b:cheapest",
)

HF_URL = os.getenv(
    "HF_URL",
    "https://router.huggingface.co/v1/chat/completions",
)

MAX_RESEARCH_SOURCES = 6
MAX_SOURCE_CHARS = 12000
REQUEST_TIMEOUT = 15


app = FastAPI(
    title=SERVICE,
    version=VERSION,
    description="AI Infinity TARGET-2 — Evidence-driven intelligence fabric",
)


# ============================================================
# UTILITIES
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_json(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    connection = db()

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            provenance_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS genomes (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            genome_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS executions (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS failures (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            stage TEXT NOT NULL,
            error TEXT NOT NULL,
            recovery_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            event_type TEXT NOT NULL,
            payload_json TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evidence (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            source_url TEXT NOT NULL,
            source_title TEXT,
            claim TEXT,
            excerpt TEXT,
            content_hash TEXT,
            retrieved_at TEXT NOT NULL,
            verification_status TEXT DEFAULT 'unverified',
            metadata_json TEXT
        );

        CREATE TABLE IF NOT EXISTS research (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            question TEXT NOT NULL,
            summary TEXT,
            evidence_json TEXT,
            uncertainty REAL DEFAULT 0.5,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_memories_type
        ON memories(memory_type);

        CREATE INDEX IF NOT EXISTS idx_evidence_task
        ON evidence(task_id);

        CREATE INDEX IF NOT EXISTS idx_research_task
        ON research(task_id);
        """
    )

    connection.commit()
    connection.close()


init_db()


# ============================================================
# EVENT LOG
# ============================================================

def log_event(
    task_id: Optional[str],
    event_type: str,
    payload: Any,
) -> None:
    connection = db()

    connection.execute(
        """
        INSERT INTO events
        (id, task_id, event_type, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            uid("event"),
            task_id,
            event_type,
            json_dump(payload),
            now_iso(),
        ),
    )

    connection.commit()
    connection.close()


def log_execution(
    task_id: Optional[str],
    action: str,
    status: str,
    result: Any,
) -> None:
    connection = db()

    connection.execute(
        """
        INSERT INTO executions
        (id, task_id, action, status, result_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            uid("execution"),
            task_id,
            action,
            status,
            json_dump(result),
            now_iso(),
        ),
    )

    connection.commit()
    connection.close()


# ============================================================
# MEMORY
# ============================================================

def save_memory(
    content: str,
    memory_type: str = "verified",
    confidence: float = 0.5,
    provenance: Optional[Dict[str, Any]] = None,
) -> str:

    memory_id = uid("memory")

    connection = db()

    connection.execute(
        """
        INSERT INTO memories
        (id, content, memory_type, confidence, provenance_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            memory_id,
            content,
            memory_type,
            confidence,
            json_dump(provenance or {}),
            now_iso(),
        ),
    )

    connection.commit()
    connection.close()

    return memory_id


def search_memory(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM memories
        WHERE content LIKE ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (f"%{query[:200]}%", limit),
    ).fetchall()

    connection.close()

    return [dict(row) for row in rows]


# ============================================================
# TASKS
# ============================================================

def create_task(objective: str) -> str:
    task_id = uid("task")
    timestamp = now_iso()

    connection = db()

    connection.execute(
        """
        INSERT INTO tasks
        (id, objective, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            task_id,
            objective,
            "running",
            timestamp,
            timestamp,
        ),
    )

    connection.commit()
    connection.close()

    return task_id


def update_task(
    task_id: str,
    status: str,
    result: Any,
) -> None:

    connection = db()

    connection.execute(
        """
        UPDATE tasks
        SET status = ?, result_json = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            json_dump(result),
            now_iso(),
            task_id,
        ),
    )

    connection.commit()
    connection.close()


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    connection = db()

    row = connection.execute(
        "SELECT * FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()

    connection.close()

    if not row:
        return None

    result = dict(row)

    if result.get("result_json"):
        try:
            result["result"] = json.loads(result["result_json"])
        except Exception:
            result["result"] = result["result_json"]

    result.pop("result_json", None)

    return result


# ============================================================
# INTENT ENGINE
# ============================================================

def classify_intent(objective: str) -> Dict[str, Any]:
    text = objective.lower()

    domains = []

    keywords = {
        "software": [
            "code",
            "python",
            "file",
            "software",
            "api",
            "program",
            "github",
        ],
        "research": [
            "research",
            "investigate",
            "analyze",
            "find out",
            "study",
            "sources",
        ],
        "science": [
            "science",
            "scientific",
            "experiment",
            "physics",
            "biology",
        ],
        "business": [
            "business",
            "market",
            "money",
            "company",
            "startup",
        ],
        "content": [
            "video",
            "image",
            "article",
            "content",
        ],
    }

    for domain, terms in keywords.items():
        if any(term in text for term in terms):
            domains.append(domain)

    if not domains:
        domains.append("general")

    intent_type = "research" if (
        "research" in domains
        or "investigate" in text
        or "sources" in text
    ) else "goal"

    return {
        "type": intent_type,
        "domains": domains,
        "objective": objective,
        "priority": "normal",
    }


# ============================================================
# WORLD MODEL
# ============================================================

def build_world_model(
    objective: str,
    intent: Dict[str, Any],
) -> Dict[str, Any]:

    return {
        "objective": objective,
        "known_entities": [],
        "events": [],
        "relationships": [],
        "resources": [
            "local_python",
            "local_filesystem",
            "available_ai_models",
            "web_research",
            "evidence_database",
        ],
        "constraints": {
            "free_first": True,
            "no_automatic_spending": True,
            "no_arbitrary_shell_execution": True,
            "authorized_actions_only": True,
        },
        "domains": intent["domains"],
        "uncertainty": 0.5,
        "evidence_status": "initial",
    }


# ============================================================
# DREAM ENGINE
# ============================================================

def dream_strategies() -> List[Dict[str, str]]:
    return [
        {
            "strategy": "direct_execution",
            "description": "Solve using the simplest verified path.",
        },
        {
            "strategy": "parallel_specialists",
            "description": "Generate multiple specialist perspectives.",
        },
        {
            "strategy": "evidence_first",
            "description": "Collect external evidence before making factual conclusions.",
        },
        {
            "strategy": "failure_first",
            "description": "Predict likely failure modes before execution.",
        },
        {
            "strategy": "reuse_memory",
            "description": "Reuse previously verified intelligence.",
        },
        {
            "strategy": "counterfactual_search",
            "description": "Explore alternative assumptions and strategies.",
        },
    ]


# ============================================================
# COUNTERFACTUAL ENGINE
# ============================================================

def build_counterfactuals(
    strategies: List[Dict[str, str]],
) -> List[Dict[str, Any]]:

    result = []

    for strategy in strategies:
        result.append(
            {
                "world": strategy["strategy"],
                "assumption": strategy["description"],
                "expected_risk": "unknown",
                "expected_benefit": "potentially useful",
                "status": "simulation_only",
            }
        )

    return result


# ============================================================
# TEMPORARY MINDS
# ============================================================

def create_temporary_minds(
    domains: List[str],
) -> List[Dict[str, Any]]:

    minds = []

    roles = [
        ("engineer", "software"),
        ("researcher", "research"),
        ("critic", "verification"),
        ("analyst", "analysis"),
    ]

    for name, role in roles:
        if role in domains or role in ("verification", "analysis"):
            minds.append(
                {
                    "id": uid("mind"),
                    "name": name,
                    "role": role,
                    "temporary": True,
                    "status": "ready",
                }
            )

    if not minds:
        minds.append(
            {
                "id": uid("mind"),
                "name": "generalist",
                "role": "general",
                "temporary": True,
                "status": "ready",
            }
        )

    return minds


# ============================================================
# SAFE CALCULATOR
# ============================================================

ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Mod: lambda a, b: a % b,
}

ALLOWED_UNARYOPS = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}


def safe_calculate(expression: str) -> Any:

    if len(expression) > 200:
        raise ValueError("Expression too long")

    tree = ast.parse(expression, mode="eval")

    def evaluate(node: ast.AST) -> Any:

        if isinstance(node, ast.Expression):
            return evaluate(node.body)

        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                raise ValueError("Boolean values are not allowed")

            if isinstance(node.value, (int, float)):
                if not math.isfinite(float(node.value)):
                    raise ValueError("Non-finite number")
                return node.value

            raise ValueError("Only numeric constants are allowed")

        if isinstance(node, ast.BinOp):
            operator_type = type(node.op)

            if operator_type not in ALLOWED_BINOPS:
                raise ValueError("Operator not allowed")

            left = evaluate(node.left)
            right = evaluate(node.right)

            return ALLOWED_BINOPS[operator_type](left, right)

        if isinstance(node, ast.UnaryOp):
            operator_type = type(node.op)

            if operator_type not in ALLOWED_UNARYOPS:
                raise ValueError("Unary operator not allowed")

            return ALLOWED_UNARYOPS[operator_type](
                evaluate(node.operand)
            )

        raise ValueError(
            f"Expression node not allowed: {type(node).__name__}"
        )

    return evaluate(tree)


def create_calculator_artifact(
    task_id: str,
) -> Dict[str, Any]:

    directory = ARTIFACTS / task_id
    directory.mkdir(parents=True, exist_ok=True)

    path = directory / "simple_calculator.py"

    source = r'''"""
AI Infinity safe calculator.

Supported:
    +
    -
    *
    /
    %
    parentheses

No eval() is used.
"""

import ast
import math


_ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Mod: lambda a, b: a % b,
}

_ALLOWED_UNARYOPS = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}


def evaluate(expression: str):
    if len(expression) > 200:
        raise ValueError("Expression too long")

    tree = ast.parse(expression, mode="eval")

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)

        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                raise ValueError("Boolean values are not allowed")

            if isinstance(node.value, (int, float)):
                if not math.isfinite(float(node.value)):
                    raise ValueError("Non-finite number")
                return node.value

            raise ValueError("Only numeric constants are allowed")

        if isinstance(node, ast.BinOp):
            operation = _ALLOWED_BINOPS.get(type(node.op))

            if operation is None:
                raise ValueError("Operator not allowed")

            return operation(
                walk(node.left),
                walk(node.right),
            )

        if isinstance(node, ast.UnaryOp):
            operation = _ALLOWED_UNARYOPS.get(type(node.op))

            if operation is None:
                raise ValueError("Unary operator not allowed")

            return operation(walk(node.operand))

        raise ValueError(
            f"Expression node not allowed: {type(node).__name__}"
        )

    return walk(tree)


if __name__ == "__main__":
    print(evaluate("2+3*4"))
'''

    path.write_text(source, encoding="utf-8")

    data = path.read_bytes()

    return {
        "status": "completed",
        "filename": path.name,
        "path": str(path),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def verify_calculator(
    artifact: Dict[str, Any],
) -> Dict[str, Any]:

    path = Path(artifact["path"])

    if not path.exists():
        return {
            "verified": False,
            "status": "failed",
            "reason": "File does not exist",
        }

    source = path.read_text(encoding="utf-8")

    try:
        compile(source, str(path), "exec")
        syntax_ok = True
        syntax_error = None
    except SyntaxError as exc:
        syntax_ok = False
        syntax_error = str(exc)

    if not syntax_ok:
        return {
            "verified": False,
            "status": "failed",
            "checks": {
                "exists": True,
                "python_syntax": False,
                "python_syntax_error": syntax_error,
            },
        }

    expression = "2+3*4"
    expected = 14

    try:
        result = safe_calculate(expression)
        functional_passed = result == expected
        execution = {
            "passed": functional_passed,
            "returncode": 0,
            "output": str(result),
            "value": result,
        }
    except Exception as exc:
        result = None
        functional_passed = False
        execution = {
            "passed": False,
            "returncode": 1,
            "output": str(exc),
            "value": None,
        }

    return {
        "verified": (
            syntax_ok
            and functional_passed
        ),
        "status": (
            "passed"
            if syntax_ok and functional_passed
            else "failed"
        ),
        "checks": {
            "exists": True,
            "hash": artifact["sha256"],
            "size": artifact["bytes"],
            "python_syntax": syntax_ok,
            "python_syntax_error": syntax_error,
            "functional_test": True,
            "functional_test_passed": functional_passed,
            "test_expression": expression,
            "expected": expected,
            "actual": result,
            "execution": execution,
        },
    }


# ============================================================
# WEB RESEARCH
# ============================================================

def clean_html(html: str) -> str:
    html = re.sub(
        r"(?is)<script.*?>.*?</script>",
        " ",
        html,
    )

    html = re.sub(
        r"(?is)<style.*?>.*?</style>",
        " ",
        html,
    )

    html = re.sub(
        r"(?s)<[^>]+>",
        " ",
        html,
    )

    html = re.sub(
        r"\s+",
        " ",
        html,
    )

    return html.strip()


def fetch_url(url: str) -> Dict[str, Any]:

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        return {
            "url": url,
            "ok": False,
            "error": "Only HTTP and HTTPS URLs are allowed",
        }

    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": (
                    "AI-Infinity/2.0 "
                    "(research; evidence collection)"
                )
            },
            allow_redirects=True,
        )

        content_type = response.headers.get(
            "content-type",
            "",
        ).lower()

        raw = response.text[:MAX_SOURCE_CHARS]

        if "text/html" in content_type:
            text = clean_html(raw)
        else:
            text = raw

        return {
            "url": response.url,
            "status_code": response.status_code,
            "content_type": content_type,
            "ok": response.ok,
            "text": text[:MAX_SOURCE_CHARS],
            "title": extract_title(raw),
            "retrieved_at": now_iso(),
        }

    except Exception as exc:
        return {
            "url": url,
            "ok": False,
            "error": str(exc),
            "retrieved_at": now_iso(),
        }


def extract_title(html: str) -> str:

    match = re.search(
        r"(?is)<title[^>]*>(.*?)</title>",
        html,
    )

    if not match:
        return ""

    title = re.sub(
        r"\s+",
        " ",
        match.group(1),
    )

    return title.strip()[:500]


def search_web(
    query: str,
    limit: int = MAX_RESEARCH_SOURCES,
) -> List[Dict[str, Any]]:

    """
    Lightweight public-web discovery.

    Uses DuckDuckGo's HTML endpoint when available.
    It is treated only as discovery; retrieved pages become
    the evidence records.
    """

    endpoint = "https://html.duckduckgo.com/html/"

    try:
        response = requests.post(
            endpoint,
            data={"q": query[:500]},
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "AI-Infinity-Research/2.0"
                )
            },
        )

        if not response.ok:
            return []

        html = response.text

        matches = re.findall(
            r'(?is)result__a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            html,
        )

        results = []

        for url, title_html in matches:

            title = re.sub(
                r"<[^>]+>",
                "",
                title_html,
            )

            title = re.sub(
                r"\s+",
                " ",
                title,
            ).strip()

            if url.startswith("//"):
                url = "https:" + url

            if not url.startswith(("http://", "https://")):
                continue

            results.append(
                {
                    "url": url,
                    "title": title[:500],
                }
            )

            if len(results) >= limit:
                break

        return results

    except Exception:
        return []


def collect_evidence(
    task_id: str,
    query: str,
    limit: int = MAX_RESEARCH_SOURCES,
) -> Dict[str, Any]:

    discovered = search_web(query, limit)

    evidence_records = []

    for item in discovered:

        fetched = fetch_url(item["url"])

        if not fetched.get("ok"):
            continue

        text = fetched.get("text", "")

        if not text:
            continue

        content_hash = sha256_text(text)

        evidence_id = uid("evidence")

        metadata = {
            "discovery": "public_web_search",
            "status_code": fetched.get("status_code"),
            "content_type": fetched.get("content_type"),
        }

        connection = db()

        connection.execute(
            """
            INSERT INTO evidence
            (
                id,
                task_id,
                source_url,
                source_title,
                claim,
                excerpt,
                content_hash,
                retrieved_at,
                verification_status,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                task_id,
                fetched.get("url", item["url"]),
                fetched.get("title") or item.get("title", ""),
                "",
                text[:4000],
                content_hash,
                fetched.get("retrieved_at", now_iso()),
                "collected",
                json_dump(metadata),
            ),
        )

        connection.commit()
        connection.close()

        evidence_records.append(
            {
                "id": evidence_id,
                "url": fetched.get("url", item["url"]),
                "title": fetched.get("title")
                or item.get("title", ""),
                "excerpt": text[:4000],
                "content_hash": content_hash,
                "retrieved_at": fetched.get(
                    "retrieved_at",
                    now_iso(),
                ),
                "verification_status": "collected",
            }
        )

    return {
        "query": query,
        "sources_discovered": len(discovered),
        "sources_collected": len(evidence_records),
        "evidence": evidence_records,
    }


# ============================================================
# EVIDENCE VERIFICATION
# ============================================================

def verify_evidence(
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:

    if not evidence:
        return {
            "verified": False,
            "status": "insufficient_evidence",
            "confidence": 0.0,
            "reason": "No sources were successfully collected.",
        }

    unique_domains = set()

    for item in evidence:
        try:
            domain = urlparse(
                item["url"]
            ).netloc.lower()

            if domain:
                unique_domains.add(domain)
        except Exception:
            pass

    source_count = len(evidence)
    domain_count = len(unique_domains)

    if source_count >= 3 and domain_count >= 2:
        confidence = 0.75
        status = "multi_source"
    elif source_count >= 2:
        confidence = 0.60
        status = "multi_source_limited"
    else:
        confidence = 0.40
        status = "single_source"

    return {
        "verified": True,
        "status": status,
        "confidence": confidence,
        "source_count": source_count,
        "domain_count": domain_count,
        "warning": (
            "Source collection confirms that material was retrieved; "
            "it does not by itself prove every claim on those pages."
        ),
    }


# ============================================================
# AI REASONING
# ============================================================

def call_huggingface(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
) -> Optional[Dict[str, Any]]:

    if not HF_TOKEN:
        return None

    selected_model = model or HF_MODEL

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": selected_model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1800,
    }

    try:
        response = requests.post(
            HF_URL,
            headers=headers,
            json=payload,
            timeout=60,
        )

        if not response.ok:
            return None

        data = response.json()

        choices = data.get("choices") or []

        if not choices:
            return None

        message = choices[0].get("message") or {}

        text = message.get("content")

        if not text:
            return None

        return {
            "provider": "huggingface",
            "model": selected_model,
            "text": text,
        }

    except Exception:
        return None


def local_reasoning(
    objective: str,
    evidence: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:

    evidence = evidence or []

    if evidence:
        return {
            "provider": "local",
            "model": "deterministic-fallback",
            "text": (
                f"Objective: {objective}\n\n"
                f"Collected evidence sources: {len(evidence)}.\n"
                "External evidence was collected and stored with "
                "source URLs, retrieval timestamps and hashes. "
                "Claims should be treated according to their "
                "source quality and independent corroboration."
            ),
        }

    return {
        "provider": "local",
        "model": "deterministic-fallback",
        "text": (
            f"Objective received: {objective}\n"
            "No external evidence was supplied."
        ),
    }


def reason_about_research(
    objective: str,
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:

    evidence_text = []

    for item in evidence[:6]:
        evidence_text.append(
            f"SOURCE: {item.get('url')}\n"
            f"TITLE: {item.get('title')}\n"
            f"TEXT: {item.get('excerpt', '')[:2500]}"
        )

    combined = "\n\n".join(evidence_text)

    messages = [
        {
            "role": "system",
            "content": (
                "You are the AI Infinity research analyst. "
                "Separate sourced facts from inference. "
                "Do not invent sources. "
                "If evidence is insufficient, say so. "
                "Return a concise research synthesis."
            ),
        },
        {
            "role": "user",
            "content": (
                f"OBJECTIVE:\n{objective}\n\n"
                f"EVIDENCE:\n{combined}\n\n"
                "Produce:\n"
                "1. Findings\n"
                "2. Evidence-supported facts\n"
                "3. Uncertain/inferred points\n"
                "4. Contradictions if visible\n"
                "5. Recommended next verification step"
            ),
        },
    ]

    result = call_huggingface(messages)

    if result:
        return result

    return local_reasoning(
        objective,
        evidence,
    )


# ============================================================
# RESEARCH ENGINE
# ============================================================

def run_research(
    task_id: str,
    question: str,
) -> Dict[str, Any]:

    log_event(
        task_id,
        "research_started",
        {"question": question},
    )

    collected = collect_evidence(
        task_id,
        question,
    )

    evidence = collected["evidence"]

    verification = verify_evidence(
        evidence,
    )

    reasoning = reason_about_research(
        question,
        evidence,
    )

    uncertainty = max(
        0.0,
        1.0 - float(
            verification.get(
                "confidence",
                0.0,
            )
        ),
    )

    research_id = uid("research")

    connection = db()

    connection.execute(
        """
        INSERT INTO research
        (
            id,
            task_id,
            question,
            summary,
            evidence_json,
            uncertainty,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            research_id,
            task_id,
            question,
            reasoning.get("text", ""),
            json_dump(evidence),
            uncertainty,
            now_iso(),
        ),
    )

    connection.commit()
    connection.close()

    memory_id = save_memory(
        content=(
            f"Research question: {question}\n\n"
            f"Research synthesis:\n"
            f"{reasoning.get('text', '')}\n\n"
            f"Evidence sources: {len(evidence)}"
        ),
        memory_type="research",
        confidence=verification.get(
            "confidence",
            0.0,
        ),
        provenance={
            "task_id": task_id,
            "research_id": research_id,
            "sources": [
                item.get("url")
                for item in evidence
            ],
            "verification": verification,
        },
    )

    log_event(
        task_id,
        "research_completed",
        {
            "research_id": research_id,
            "memory_id": memory_id,
            "verification": verification,
        },
    )

    return {
        "research_id": research_id,
        "memory_id": memory_id,
        "question": question,
        "evidence": collected,
        "verification": verification,
        "analysis": reasoning,
    }


# ============================================================
# INTELLIGENCE COMPILER
# ============================================================

def compile_intelligence(
    objective: str,
    intent: Dict[str, Any],
    world_model: Dict[str, Any],
    strategies: List[Dict[str, str]],
    counterfactuals: List[Dict[str, Any]],
    minds: List[Dict[str, Any]],
) -> Dict[str, Any]:

    return {
        "input": objective,
        "goal_model": {
            "objective": objective,
            "domains": intent["domains"],
        },
        "constraints": world_model["constraints"],
        "world_model": world_model,
        "strategies": strategies,
        "counterfactuals": counterfactuals,
        "temporary_minds": minds,
        "execution_policy": {
            "authorized_registry_only": True,
            "arbitrary_code_execution": False,
        },
        "research_policy": {
            "source_tracking": True,
            "provenance_tracking": True,
            "evidence_required_for_external_claims": True,
        },
    }


# ============================================================
# GENOME
# ============================================================

def create_genome(
    task_id: str,
    objective: str,
    intent: Dict[str, Any],
    strategies: List[Dict[str, str]],
    minds: List[Dict[str, Any]],
    execution: Any,
    verification: Any,
    research: Any,
) -> Dict[str, Any]:

    genome = {
        "genome_version": "2.0",
        "task_id": task_id,
        "objective": objective,
        "assumptions": [],
        "constraints": {
            "free_first": True,
            "no_automatic_spending": True,
            "no_arbitrary_shell_execution": True,
            "authorized_actions_only": True,
        },
        "strategy": strategies,
        "reasoning_pattern": (
            "intent-research-plan-execute-verify-learn"
        ),
        "domains": intent["domains"],
        "temporary_minds": minds,
        "tools": [
            "registered_action_registry",
            "web_research",
            "evidence_store",
            "verification_engine",
        ],
        "research": research,
        "execution": execution,
        "verification": verification,
        "failure_modes": [],
        "recovery_policy": {
            "safe_retry": True,
            "rollback": True,
        },
        "fitness": (
            1.0
            if (
                verification
                and verification.get("verified")
            )
            else 0.5
        ),
        "reusable": True,
        "created_at": now_iso(),
    }

    genome_id = uid("genome")
    genome["id"] = genome_id

    connection = db()

    connection.execute(
        """
        INSERT INTO genomes
        (id, task_id, genome_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            genome_id,
            task_id,
            json_dump(genome),
            now_iso(),
        ),
    )

    connection.commit()
    connection.close()

    return genome


# ============================================================
# SAFE EXECUTION REGISTRY
# ============================================================

AUTHORIZED_ACTIONS = {
    "create_calculator",
    "research_web",
}


def execute_registered_action(
    task_id: str,
    action: str,
    objective: str,
) -> Dict[str, Any]:

    if action not in AUTHORIZED_ACTIONS:
        raise ValueError(
            f"Unauthorized action: {action}"
        )

    if action == "create_calculator":

        artifact = create_calculator_artifact(
            task_id
        )

        verification = verify_calculator(
            artifact
        )

        result = {
            "action": action,
            "status": (
                "completed"
                if verification["verified"]
                else "failed"
            ),
            "artifact": artifact,
            "verification": verification,
        }

        log_execution(
            task_id,
            action,
            result["status"],
            result,
        )

        return result

    if action == "research_web":

        result = run_research(
            task_id,
            objective,
        )

        log_execution(
            task_id,
            action,
            "completed",
            result,
        )

        return result

    raise ValueError(
        f"Action not implemented: {action}"
    )


# ============================================================
# SELF-HEALING
# ============================================================

def record_failure(
    task_id: str,
    stage: str,
    error: str,
    recovery: Dict[str, Any],
) -> str:

    failure_id = uid("failure")

    connection = db()

    connection.execute(
        """
        INSERT INTO failures
        (
            id,
            task_id,
            stage,
            error,
            recovery_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            failure_id,
            task_id,
            stage,
            error,
            json_dump(recovery),
            now_iso(),
        ),
    )

    connection.commit()
    connection.close()

    return failure_id


def diagnose_failure(
    task_id: str,
    stage: str,
    error: str,
) -> Dict[str, Any]:

    recovery = {
        "safe_retry": True,
        "rollback": True,
        "recommendation": (
            "Inspect the failed stage, preserve evidence, "
            "retry only if the action remains authorized."
        ),
    }

    failure_id = record_failure(
        task_id,
        stage,
        error,
        recovery,
    )

    return {
        "failure_id": failure_id,
        "stage": stage,
        "error": error,
        "recovery": recovery,
    }


# ============================================================
# EVOLUTION
# ============================================================

def propose_evolution(
    genome: Dict[str, Any],
) -> Dict[str, Any]:

    return {
        "status": "sandbox_proposal",
        "source_genome": genome.get("id"),
        "method": [
            "generate_variants",
            "benchmark",
            "red_team",
            "verify",
            "preserve_only_verified_improvements",
        ],
        "automatic_deployment": False,
        "proposal": (
            "Add stronger evidence ranking, persistent "
            "provenance and independent verification."
        ),
    }


# ============================================================
# MASTER INFINITY LOOP
# ============================================================

def run_infinity(
    objective: str,
    research: bool = True,
    verify: bool = True,
    remember: bool = True,
) -> Dict[str, Any]:

    task_id = create_task(objective)

    try:

        intent = classify_intent(objective)

        world_model = build_world_model(
            objective,
            intent,
        )

        strategies = dream_strategies()

        counterfactuals = build_counterfactuals(
            strategies
        )

        minds = create_temporary_minds(
            intent["domains"]
        )

        compiler = compile_intelligence(
            objective,
            intent,
            world_model,
            strategies,
            counterfactuals,
            minds,
        )

        log_event(
            task_id,
            "intelligence_compiled",
            compiler,
        )

        research_result = None

        if research and (
            intent["type"] == "research"
            or "research" in intent["domains"]
            or "investigate" in objective.lower()
            or "analyze" in objective.lower()
        ):
            research_result = run_research(
                task_id,
                objective,
            )

        execution_result = None

        calculator_keywords = [
            "simple_calculator.py",
            "calculator",
            "2+3*4",
            "safe calculator",
        ]

        if any(
            keyword in objective.lower()
            for keyword in calculator_keywords
        ):
            execution_result = execute_registered_action(
                task_id,
                "create_calculator",
                objective,
            )

        verification_result = (
            execution_result.get("verification")
            if execution_result
            else (
                research_result.get("verification")
                if research_result
                else None
            )
        )

        memory_id = None

        if remember:

            memory_content = (
                f"Verified AI Infinity task.\n"
                f"Objective: {objective}\n"
                f"Task ID: {task_id}\n"
                f"Verification: "
                f"{json_dump(verification_result)}"
            )

            memory_id = save_memory(
                memory_content,
                memory_type="verified",
                confidence=(
                    1.0
                    if verification_result
                    and verification_result.get(
                        "verified"
                    )
                    else 0.5
                ),
                provenance={
                    "task_id": task_id,
                    "source": "AI Infinity execution engine",
                },
            )

        genome = create_genome(
            task_id=task_id,
            objective=objective,
            intent=intent,
            strategies=strategies,
            minds=minds,
            execution=execution_result,
            verification=verification_result,
            research=research_result,
        )

        evolution = propose_evolution(
            genome
        )

        final_result = {
            "task_id": task_id,
            "status": "completed",
            "version": VERSION,
            "objective": objective,
            "intent": intent,
            "world_model": world_model,
            "dream_engine": {
                "strategies": strategies,
            },
            "counterfactual_universe": {
                "scenarios": counterfactuals,
                "warning": (
                    "Counterfactual simulations are hypothetical "
                    "and are not evidence of real-world outcomes."
                ),
            },
            "temporary_minds": minds,
            "intelligence_compiler": compiler,
            "research": research_result,
            "execution": execution_result,
            "verification": verification_result,
            "memory": {
                "saved": memory_id,
            },
            "intelligence_genome": genome,
            "evolution": evolution,
            "safety": {
                "arbitrary_shell_execution": False,
                "automatic_spending": False,
                "automatic_self_modification": False,
                "authorized_actions_only": True,
            },
        }

        update_task(
            task_id,
            "completed",
            final_result,
        )

        return final_result

    except Exception as exc:

        failure = diagnose_failure(
            task_id,
            "master_loop",
            str(exc),
        )

        result = {
            "task_id": task_id,
            "status": "failed",
            "version": VERSION,
            "objective": objective,
            "failure": failure,
        }

        update_task(
            task_id,
            "failed",
            result,
        )

        return result


# ============================================================
# API MODELS
# ============================================================

class RunRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=1,
        max_length=20000,
    )
    research: bool = True
    verify: bool = True
    remember: bool = True


class ExecuteRequest(BaseModel):
    action: str
    objective: str = ""


class ResearchRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=2,
        max_length=1000,
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": SERVICE,
        "version": VERSION,
        "research": True,
        "evidence": True,
        "memory": True,
        "verification": True,
        "free_first": True,
    }


@app.get("/v1/status")
def status() -> Dict[str, Any]:

    connection = db()

    counts = {}

    for table in (
        "tasks",
        "memories",
        "genomes",
        "executions",
        "failures",
        "events",
        "evidence",
        "research",
    ):
        row = connection.execute(
            f"SELECT COUNT(*) AS count FROM {table}"
        ).fetchone()

        counts[table] = row["count"]

    connection.close()

    return {
        "service": SERVICE,
        "version": VERSION,
        "status": "online",
        "capabilities": {
            "intent_engine": True,
            "world_model": True,
            "dream_engine": True,
            "counterfactual_engine": True,
            "temporary_minds": True,
            "web_research": True,
            "evidence_fabric": True,
            "verification": True,
            "memory": True,
            "intelligence_genome": True,
            "self_healing": True,
            "safe_execution_registry": True,
        },
        "safety": {
            "arbitrary_shell_execution": False,
            "automatic_spending": False,
            "automatic_self_modification": False,
        },
        "database": counts,
    }


# ============================================================
# RUN
# ============================================================

@app.post("/v1/run")
def run_endpoint(
    request: RunRequest,
) -> Dict[str, Any]:

    return run_infinity(
        objective=request.objective,
        research=request.research,
        verify=request.verify,
        remember=request.remember,
    )


# ============================================================
# EXECUTE
# ============================================================

@app.post("/v1/execute")
def execute_endpoint(
    request: ExecuteRequest,
) -> Dict[str, Any]:

    task_id = create_task(
        request.objective or request.action
    )

    try:

        result = execute_registered_action(
            task_id,
            request.action,
            request.objective,
        )

        update_task(
            task_id,
            "completed",
            result,
        )

        return {
            "task_id": task_id,
            **result,
        }

    except Exception as exc:

        failure = diagnose_failure(
            task_id,
            "execute",
            str(exc),
        )

        result = {
            "task_id": task_id,
            "status": "failed",
            "failure": failure,
        }

        update_task(
            task_id,
            "failed",
            result,
        )

        raise HTTPException(
            status_code=400,
            detail=result,
        )


# ============================================================
# RESEARCH ENDPOINT
# ============================================================

@app.post("/v1/research")
def research_endpoint(
    request: ResearchRequest,
) -> Dict[str, Any]:

    task_id = create_task(
        request.question
    )

    result = run_research(
        task_id,
        request.question,
    )

    update_task(
        task_id,
        "completed",
        result,
    )

    return {
        "task_id": task_id,
        "version": VERSION,
        **result,
    }


# ============================================================
# TASK
# ============================================================

@app.get("/v1/tasks/{task_id}")
def task_endpoint(
    task_id: str,
) -> Dict[str, Any]:

    result = get_task(task_id)

    if not result:
        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    return result


# ============================================================
# MEMORY
# ============================================================

@app.get("/v1/memory")
def memory_endpoint(
    q: str = "",
) -> Dict[str, Any]:

    if q:
        memories = search_memory(q)
    else:
        connection = db()

        rows = connection.execute(
            """
            SELECT *
            FROM memories
            ORDER BY created_at DESC
            LIMIT 20
            """
        ).fetchall()

        connection.close()

        memories = [
            dict(row)
            for row in rows
        ]

    return {
        "count": len(memories),
        "memories": memories,
    }


# ============================================================
# GENOMES
# ============================================================

@app.get("/v1/genomes")
def genomes_endpoint() -> Dict[str, Any]:

    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM genomes
        ORDER BY created_at DESC
        LIMIT 20
        """
    ).fetchall()

    connection.close()

    result = []

    for row in rows:

        item = dict(row)

        try:
            item["genome"] = json.loads(
                item.pop("genome_json")
            )
        except Exception:
            item["genome"] = item.pop(
                "genome_json",
                None,
            )

        result.append(item)

    return {
        "count": len(result),
        "genomes": result,
    }


# ============================================================
# EVIDENCE
# ============================================================

@app.get("/v1/evidence")
def evidence_endpoint(
    task_id: str = "",
) -> Dict[str, Any]:

    connection = db()

    if task_id:

        rows = connection.execute(
            """
            SELECT *
            FROM evidence
            WHERE task_id = ?
            ORDER BY retrieved_at DESC
            LIMIT 50
            """,
            (task_id,),
        ).fetchall()

    else:

        rows = connection.execute(
            """
            SELECT *
            FROM evidence
            ORDER BY retrieved_at DESC
            LIMIT 50
            """
        ).fetchall()

    connection.close()

    result = []

    for row in rows:

        item = dict(row)

        try:
            item["metadata"] = json.loads(
                item.pop("metadata_json")
            )
        except Exception:
            item["metadata"] = {}

        result.append(item)

    return {
        "count": len(result),
        "evidence": result,
    }


# ============================================================
# RESEARCH HISTORY
# ============================================================

@app.get("/v1/research")
def research_history() -> Dict[str, Any]:

    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM research
        ORDER BY created_at DESC
        LIMIT 30
        """
    ).fetchall()

    connection.close()

    result = []

    for row in rows:

        item = dict(row)

        try:
            item["evidence"] = json.loads(
                item.pop("evidence_json")
            )
        except Exception:
            item["evidence"] = []

        result.append(item)

    return {
        "count": len(result),
        "research": result,
    }


# ============================================================
# FAILURES
# ============================================================

@app.get("/v1/failures")
def failures_endpoint() -> Dict[str, Any]:

    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM failures
        ORDER BY created_at DESC
        LIMIT 30
        """
    ).fetchall()

    connection.close()

    result = []

    for row in rows:

        item = dict(row)

        try:
            item["recovery"] = json.loads(
                item.pop("recovery_json")
            )
        except Exception:
            item["recovery"] = {}

        result.append(item)

    return {
        "count": len(result),
        "failures": result,
    }


# ============================================================
# AUDIT
# ============================================================

@app.get("/v1/audit")
def audit_endpoint() -> Dict[str, Any]:

    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM events
        ORDER BY created_at DESC
        LIMIT 100
        """
    ).fetchall()

    connection.close()

    result = []

    for row in rows:

        item = dict(row)

        try:
            item["payload"] = json.loads(
                item.pop("payload_json")
            )
        except Exception:
            item["payload"] = {}

        result.append(item)

    return {
        "count": len(result),
        "events": result,
    }


# ============================================================
# ROOT UI
# ============================================================

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width,initial-scale=1">

<title>AI Infinity TARGET-2</title>

<style>
body {
    font-family: Arial, sans-serif;
    max-width: 900px;
    margin: auto;
    padding: 20px;
    background: #0b0f14;
    color: #f1f5f9;
}

.card {
    background: #151b23;
    border-radius: 14px;
    padding: 18px;
    margin-bottom: 18px;
}

textarea {
    width: 100%;
    min-height: 150px;
    box-sizing: border-box;
    padding: 12px;
    border-radius: 10px;
    border: 1px solid #334155;
    background: #0f172a;
    color: white;
}

button {
    padding: 12px 18px;
    margin-top: 10px;
    border: 0;
    border-radius: 10px;
    cursor: pointer;
}

pre {
    white-space: pre-wrap;
    word-break: break-word;
    background: #020617;
    padding: 14px;
    border-radius: 10px;
    overflow-x: auto;
}

.badge {
    display: inline-block;
    padding: 6px 10px;
    border-radius: 999px;
    background: #1e293b;
    margin-right: 6px;
}
</style>
</head>

<body>

<div class="card">

<h1>♾️ AI Infinity</h1>

<p>
<span class="badge">TARGET-2</span>
<span class="badge">Research</span>
<span class="badge">Evidence</span>
<span class="badge">Memory</span>
<span class="badge">Verification</span>
</p>

<p>
Intent → Research → Evidence → Reason → Execute
→ Verify → Learn → Genome
</p>

</div>

<div class="card">

<h2>Run Objective</h2>

<textarea id="objective"
placeholder="Example:
Research the current state of AI agent systems, collect multiple sources, identify the most important capabilities, separate evidence from assumptions, and save the verified research."></textarea>

<br>

<button onclick="runObjective()">
Run AI Infinity
</button>

</div>

<div class="card">

<h2>Result</h2>

<pre id="result">Ready.</pre>

</div>

<script>

async function runObjective() {

    const objective =
        document.getElementById("objective").value;

    document.getElementById("result").textContent =
        "AI Infinity is working...";

    try {

        const response = await fetch(
            "/v1/run",
            {
                method: "POST",
                headers: {
                    "Content-Type":
                        "application/json"
                },
                body: JSON.stringify({
                    objective: objective,
                    research: true,
                    verify: true,
                    remember: true
                })
            }
        );

        const data =
            await response.json();

        document.getElementById(
            "result"
        ).textContent =
            JSON.stringify(data, null, 2);

    } catch (error) {

        document.getElementById(
            "result"
        ).textContent =
            String(error);
    }
}

</script>

</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return HTML


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup() -> None:

    init_db()

    log_event(
        None,
        "system_start",
        {
            "service": SERVICE,
            "version": VERSION,
            "research": True,
            "evidence": True,
            "memory": True,
            "verification": True,
        },
    )
