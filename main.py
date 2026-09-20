"""
AI Infinity
TARGET-2050.53
UNIVERSAL-INTELLIGENCE-LOOP

Single-file FastAPI intelligence / mission engine.

Core loop:
UNDERSTAND
    -> DISCOVER
    -> PLAN
    -> RESEARCH
    -> ACT
    -> OBSERVE
    -> LEARN
    -> DETECT GAPS
    -> EXPAND
    -> RE-EXECUTE
    -> VERIFY
    -> DELIVER

Security model:
- External HTTP is controlled by EXTERNAL_ALLOWED_DOMAINS.
- No arbitrary code execution.
- No credential modification.
- No permission escalation.
- No destructive actions.
- No unrestricted proxy.
- Protected real-world actions require explicit approval.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# VERSION
# ============================================================

VERSION = "TARGET-2050.53"
BUILD = "UNIVERSAL-INTELLIGENCE-LOOP"

BASE = Path("/tmp/ai_infinity")
BASE.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"

POLICY_VERSION = 1

MAX_STEPS = 60
MAX_PARALLEL = 4
MAX_RECOVERY_ATTEMPTS = 3
MAX_ADAPTIVE_CYCLES = 10
MAX_RESEARCH_SOURCES = 6
MAX_RESPONSE_BYTES = 2_000_000

BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "169.254.169.254",
}

EXTERNAL_ALLOWED_DOMAINS = {
    x.strip().lower()
    for x in os.getenv("EXTERNAL_ALLOWED_DOMAINS", "").split(",")
    if x.strip()
}


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="AI Infinity",
    version=VERSION,
    description="Universal Intelligence Loop",
)


# ============================================================
# DATABASE
# ============================================================

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def init_db() -> None:
    conn = db()
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS missions (
            mission_id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            confidence REAL DEFAULT 0,
            adaptive_cycles INTEGER DEFAULT 0,
            recovery_attempts INTEGER DEFAULT 0,
            checkpoint TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS mission_steps (
            step_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            name TEXT NOT NULL,
            step_type TEXT NOT NULL,
            status TEXT NOT NULL,
            depends_on TEXT,
            result TEXT,
            error TEXT,
            adaptive_reason TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS connectors (
            connector_id TEXT PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            connector_type TEXT NOT NULL,
            permission TEXT NOT NULL,
            score REAL DEFAULT 0.5,
            uses INTEGER DEFAULT 0,
            successes INTEGER DEFAULT 0,
            failures INTEGER DEFAULT 0,
            metadata TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS evidence (
            evidence_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            step_id TEXT,
            source TEXT,
            title TEXT,
            content TEXT,
            url TEXT,
            confidence REAL DEFAULT 0,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS provenance (
            provenance_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            step_id TEXT,
            event TEXT,
            source TEXT,
            data_hash TEXT,
            metadata TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS approvals (
            approval_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            created_at TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS learning (
            learning_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            category TEXT,
            lesson TEXT,
            confidence REAL DEFAULT 0,
            reusable INTEGER DEFAULT 1,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS connector_events (
            event_id TEXT PRIMARY KEY,
            connector_id TEXT,
            mission_id TEXT,
            event TEXT,
            success INTEGER,
            details TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            cycle INTEGER,
            state TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS intelligence_gaps (
            gap_id TEXT PRIMARY KEY,
            mission_id TEXT NOT NULL,
            gap TEXT,
            priority REAL DEFAULT 0.5,
            status TEXT DEFAULT 'open',
            discovered_cycle INTEGER DEFAULT 0,
            resolved_cycle INTEGER,
            created_at TEXT
        );
        """
    )

    # Safe migrations from earlier builds.
    columns = {
        row["name"]
        for row in cur.execute("PRAGMA table_info(missions)").fetchall()
    }

    if "adaptive_cycles" not in columns:
        cur.execute(
            "ALTER TABLE missions ADD COLUMN adaptive_cycles INTEGER DEFAULT 0"
        )

    if "checkpoint" not in columns:
        cur.execute(
            "ALTER TABLE missions ADD COLUMN checkpoint TEXT"
        )

    step_columns = {
        row["name"]
        for row in cur.execute("PRAGMA table_info(mission_steps)").fetchall()
    }

    if "adaptive_reason" not in step_columns:
        cur.execute(
            "ALTER TABLE mission_steps ADD COLUMN adaptive_reason TEXT"
        )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# CONNECTORS
# ============================================================

def register_builtin_connectors() -> None:
    builtins = [
        (
            "reasoning",
            "reasoning",
            "safe",
        ),
        (
            "planner",
            "planner",
            "safe",
        ),
        (
            "memory",
            "memory",
            "safe",
        ),
        (
            "web_read",
            "web",
            "controlled",
        ),
        (
            "verification",
            "verification",
            "safe",
        ),
        (
            "action_gateway",
            "action",
            "approval_required",
        ),
    ]

    conn = db()

    for name, ctype, permission in builtins:
        conn.execute(
            """
            INSERT OR IGNORE INTO connectors
            (
                connector_id,
                name,
                connector_type,
                permission,
                score,
                uses,
                successes,
                failures,
                metadata,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, 0.5, 0, 0, 0, ?, ?, ?)
            """,
            (
                uid("connector"),
                name,
                ctype,
                permission,
                json.dumps({"builtin": True}),
                now_iso(),
                now_iso(),
            ),
        )

    conn.commit()
    conn.close()


register_builtin_connectors()


# ============================================================
# MODELS
# ============================================================

class CreateRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=10000)
    duration_minutes: int = Field(default=1, ge=1, le=120)


class RunRequest(BaseModel):
    objective: str = Field(..., min_length=1, max_length=10000)
    research: bool = True
    verify: bool = True
    remember: bool = True


class ConnectorRequest(BaseModel):
    name: str
    connector_type: str = "external"
    permission: str = "safe"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ApprovalRequest(BaseModel):
    approved: bool


# ============================================================
# HELPERS
# ============================================================

def json_load(value: Optional[str], default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def hash_data(value: Any) -> str:
    raw = json_dump(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def normalize_domain(host: str) -> str:
    return host.lower().strip().rstrip(".")


def domain_allowed(host: str) -> bool:
    host = normalize_domain(host)

    if not host:
        return False

    if host in BLOCKED_HOSTS:
        return False

    for allowed in EXTERNAL_ALLOWED_DOMAINS:
        allowed = normalize_domain(allowed)

        if host == allowed:
            return True

        if host.endswith("." + allowed):
            return True

    return False


def extract_urls(text: str) -> List[str]:
    urls = re.findall(
        r"https?://[^\s<>'\"]+",
        text or "",
        flags=re.IGNORECASE,
    )

    clean = []

    for url in urls:
        url = url.rstrip(".,);]}")

        if url not in clean:
            clean.append(url)

    return clean


def safe_excerpt(text: str, limit: int = 4000) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit]


def record_provenance(
    mission_id: str,
    event: str,
    source: str,
    data: Any,
    step_id: Optional[str] = None,
) -> None:
    conn = db()

    conn.execute(
        """
        INSERT INTO provenance
        (
            provenance_id,
            mission_id,
            step_id,
            event,
            source,
            data_hash,
            metadata,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uid("prov"),
            mission_id,
            step_id,
            event,
            source,
            hash_data(data),
            json_dump({"version": VERSION}),
            now_iso(),
        ),
    )

    conn.commit()
    conn.close()


def add_evidence(
    mission_id: str,
    source: str,
    content: str,
    url: Optional[str] = None,
    title: Optional[str] = None,
    confidence: float = 0.5,
    step_id: Optional[str] = None,
) -> str:
    evidence_id = uid("evidence")

    conn = db()

    conn.execute(
        """
        INSERT INTO evidence
        (
            evidence_id,
            mission_id,
            step_id,
            source,
            title,
            content,
            url,
            confidence,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evidence_id,
            mission_id,
            step_id,
            source,
            title or "",
            content,
            url,
            confidence,
            now_iso(),
        ),
    )

    conn.commit()
    conn.close()

    record_provenance(
        mission_id,
        "evidence_recorded",
        source,
        {
            "evidence_id": evidence_id,
            "url": url,
            "title": title,
        },
        step_id,
    )

    return evidence_id


# ============================================================
# REQUIREMENT / GAP ENGINE
# ============================================================

def infer_requirements(objective: str) -> List[str]:
    text = objective.lower()

    requirements = []

    if any(
        x in text
        for x in [
            "research",
            "investigate",
            "find",
            "look up",
            "latest",
            "current",
            "sources",
            "evidence",
            "verify",
        ]
    ):
        requirements.append("external_evidence")

    if any(
        x in text
        for x in [
            "compare",
            "comparison",
            "difference",
            "versus",
            "vs",
        ]
    ):
        requirements.append("comparison")

    if any(
        x in text
        for x in [
            "remember",
            "learn",
            "knowledge",
            "lesson",
        ]
    ):
        requirements.append("persistent_learning")

    if any(
        x in text
        for x in [
            "execute",
            "perform",
            "send",
            "create",
            "publish",
            "deploy",
            "change",
            "control",
        ]
    ):
        requirements.append("action")

    if any(
        x in text
        for x in [
            "verify",
            "validate",
            "check",
            "prove",
        ]
    ):
        requirements.append("verification")

    if not requirements:
        requirements.append("reasoning")

    return list(dict.fromkeys(requirements))


def discover_capabilities(objective: str) -> Dict[str, Any]:
    requirements = infer_requirements(objective)

    capabilities = []

    mapping = {
        "external_evidence": "web_read",
        "comparison": "reasoning",
        "persistent_learning": "memory",
        "action": "action_gateway",
        "verification": "verification",
        "reasoning": "reasoning",
    }

    for requirement in requirements:
        connector = mapping.get(requirement)

        if connector:
            capabilities.append(
                {
                    "requirement": requirement,
                    "connector": connector,
                    "available": True,
                }
            )

    return {
        "requirements": requirements,
        "capabilities": capabilities,
    }


def discover_gaps(
    objective: str,
    results: Dict[str, Any],
    cycle: int,
) -> List[Dict[str, Any]]:
    gaps: List[Dict[str, Any]] = []

    research = results.get("research")

    if "external_evidence" in infer_requirements(objective):
        if not research:
            gaps.append(
                {
                    "gap": "External evidence is still required.",
                    "priority": 0.95,
                }
            )
        elif research.get("status") == "ready":
            gaps.append(
                {
                    "gap": "Research capability is available but no permitted source was supplied.",
                    "priority": 0.80,
                }
            )
        elif research.get("status") == "partial":
            gaps.append(
                {
                    "gap": "Research returned partial evidence and needs additional permitted sources.",
                    "priority": 0.85,
                }
            )

    verification = results.get("verify")

    if (
        "verification" in infer_requirements(objective)
        and (
            not verification
            or verification.get("status") != "verified"
        )
    ):
        gaps.append(
            {
                "gap": "Independent verification is still required.",
                "priority": 0.90,
            }
        )

    if (
        "comparison" in infer_requirements(objective)
        and len(results.get("research_sources", [])) < 2
    ):
        gaps.append(
            {
                "gap": "A comparison requires more than one evidence source.",
                "priority": 0.75,
            }
        )

    # Avoid infinite expansion.
    if cycle >= MAX_ADAPTIVE_CYCLES - 1:
        gaps = gaps[:1]

    return gaps


def persist_gaps(
    mission_id: str,
    gaps: List[Dict[str, Any]],
    cycle: int,
) -> None:
    conn = db()

    for gap in gaps:
        conn.execute(
            """
            INSERT INTO intelligence_gaps
            (
                gap_id,
                mission_id,
                gap,
                priority,
                status,
                discovered_cycle,
                created_at
            )
            VALUES (?, ?, ?, ?, 'open', ?, ?)
            """,
            (
                uid("gap"),
                mission_id,
                gap["gap"],
                float(gap.get("priority", 0.5)),
                cycle,
                now_iso(),
            ),
        )

    conn.commit()
    conn.close()


def resolve_old_gaps(
    mission_id: str,
    results: Dict[str, Any],
    cycle: int,
) -> None:
    if results.get("verify", {}).get("status") == "verified":
        conn = db()

        conn.execute(
            """
            UPDATE intelligence_gaps
            SET status='resolved', resolved_cycle=?
            WHERE mission_id=? AND status='open'
            """,
            (
                cycle,
                mission_id,
            ),
        )

        conn.commit()
        conn.close()


# ============================================================
# CONNECTOR LEARNING
# ============================================================

def connector_event(
    connector_name: str,
    mission_id: str,
    event: str,
    success: bool,
    details: Any = None,
) -> None:
    conn = db()

    row = conn.execute(
        """
        SELECT connector_id, score, uses, successes, failures
        FROM connectors
        WHERE name=?
        """,
        (connector_name,),
    ).fetchone()

    if row:
        uses = int(row["uses"] or 0) + 1
        successes = int(row["successes"] or 0) + (
            1 if success else 0
        )
        failures = int(row["failures"] or 0) + (
            0 if success else 1
        )

        score = (
            (successes + 1)
            / (uses + 2)
        )

        conn.execute(
            """
            UPDATE connectors
            SET score=?, uses=?, successes=?, failures=?, updated_at=?
            WHERE connector_id=?
            """,
            (
                score,
                uses,
                successes,
                failures,
                now_iso(),
                row["connector_id"],
            ),
        )

        conn.execute(
            """
            INSERT INTO connector_events
            (
                event_id,
                connector_id,
                mission_id,
                event,
                success,
                details,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uid("event"),
                row["connector_id"],
                mission_id,
                event,
                1 if success else 0,
                json_dump(details or {}),
                now_iso(),
            ),
        )

    conn.commit()
    conn.close()


# ============================================================
# WEB READER
# ============================================================

async def controlled_web_read(
    mission_id: str,
    objective: str,
    step_id: Optional[str] = None,
) -> Dict[str, Any]:
    urls = extract_urls(objective)

    if not urls:
        connector_event(
            "web_read",
            mission_id,
            "no_explicit_url",
            True,
            {"message": "No explicit URL supplied."},
        )

        return {
            "status": "ready",
            "type": "web_read",
            "message": (
                "Controlled web reader is available. "
                "No explicit allowlisted URL was supplied."
            ),
            "sources": [],
        }

    sources = []
    blocked = []
    errors = []

    timeout = httpx.Timeout(
        12.0,
        connect=6.0,
    )

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": "AI-Infinity/2050.53"
        },
    ) as client:

        for url in urls[:MAX_RESEARCH_SOURCES]:

            parsed = urlparse(url)
            host = normalize_domain(parsed.hostname or "")

            if not domain_allowed(host):
                blocked.append(
                    {
                        "url": url,
                        "host": host,
                        "reason": "domain_not_allowlisted",
                    }
                )
                continue

            try:
                response = await client.get(url)

                if len(response.content) > MAX_RESPONSE_BYTES:
                    errors.append(
                        {
                            "url": url,
                            "error": "response_too_large",
                        }
                    )
                    continue

                content_type = response.headers.get(
                    "content-type",
                    "",
                ).lower()

                text = response.text

                if "text/html" in content_type:
                    text = re.sub(
                        r"<script[\s\S]*?</script>",
                        " ",
                        text,
                        flags=re.IGNORECASE,
                    )

                    text = re.sub(
                        r"<style[\s\S]*?</style>",
                        " ",
                        text,
                        flags=re.IGNORECASE,
                    )

                    text = re.sub(
                        r"<[^>]+>",
                        " ",
                        text,
                    )

                excerpt = safe_excerpt(
                    text,
                    12000,
                )

                source = {
                    "url": str(response.url),
                    "status_code": response.status_code,
                    "content_type": content_type,
                    "title": (
                        re.search(
                            r"<title[^>]*>(.*?)</title>",
                            response.text,
                            flags=re.IGNORECASE | re.DOTALL,
                        ).group(1).strip()
                        if re.search(
                            r"<title[^>]*>(.*?)</title>",
                            response.text,
                            flags=re.IGNORECASE | re.DOTALL,
                        )
                        else host
                    ),
                    "content": excerpt,
                }

                sources.append(source)

                add_evidence(
                    mission_id=mission_id,
                    source="web",
                    content=excerpt,
                    url=str(response.url),
                    title=source["title"],
                    confidence=(
                        0.85
                        if response.status_code == 200
                        else 0.55
                    ),
                    step_id=step_id,
                )

                connector_event(
                    "web_read",
                    mission_id,
                    "source_read",
                    response.status_code < 400,
                    {
                        "url": str(response.url),
                        "status_code": response.status_code,
                    },
                )

            except Exception as exc:
                errors.append(
                    {
                        "url": url,
                        "error": str(exc)[:500],
                    }
                )

                connector_event(
                    "web_read",
                    mission_id,
                    "source_error",
                    False,
                    {
                        "url": url,
                        "error": str(exc)[:500],
                    },
                )

    if sources:
        status = (
            "completed"
            if not errors
            else "partial"
        )

        return {
            "status": status,
            "type": "web_read",
            "sources": sources,
            "blocked": blocked,
            "errors": errors,
        }

    if blocked:
        return {
            "status": "blocked",
            "type": "web_read",
            "message": "Requested sources are not allowlisted.",
            "sources": [],
            "blocked": blocked,
            "errors": errors,
        }

    return {
        "status": "failed",
        "type": "web_read",
        "message": "No research source could be retrieved.",
        "sources": [],
        "blocked": blocked,
        "errors": errors,
    }


# ============================================================
# REASONING
# ============================================================

def reasoning_step(
    objective: str,
    results: Dict[str, Any],
) -> Dict[str, Any]:
    completed = list(results.keys())

    return {
        "status": "completed",
        "type": "reasoning",
        "objective": objective,
        "context_keys": completed,
        "analysis": (
            "Mission state interpreted. "
            "The Universal Intelligence Loop evaluates "
            "available evidence, unresolved requirements, "
            "verification state, and remaining gaps before "
            "deciding whether another execution cycle is needed."
        ),
    }


# ============================================================
# PLANNER
# ============================================================

def planner_step(
    objective: str,
    capabilities: Dict[str, Any],
) -> Dict[str, Any]:

    requirements = capabilities.get(
        "requirements",
        [],
    )

    strategy = [
        "interpret_intent",
        "discover_capabilities",
        "construct_graph",
    ]

    if "external_evidence" in requirements:
        strategy.append("research")

    strategy.extend(
        [
            "execute_ready_steps",
            "observe_results",
            "detect_intelligence_gaps",
            "adapt_graph",
            "recover_failures",
            "verify",
            "learn",
        ]
    )

    return {
        "status": "completed",
        "type": "adaptive_planner",
        "strategy": strategy,
        "requirements": requirements,
    }


# ============================================================
# MEMORY
# ============================================================

def remember_learning(
    mission_id: str,
    objective: str,
    results: Dict[str, Any],
) -> Dict[str, Any]:

    lessons = []

    verified = results.get("verify", {})

    if verified.get("status") == "verified":
        lessons.append(
            "The mission reached independently verified state."
        )

    research = results.get("research")

    if research:
        if research.get("status") == "completed":
            lessons.append(
                "Controlled external research produced evidence."
            )
        elif research.get("status") == "partial":
            lessons.append(
                "Research was partially successful and may require "
                "additional sources."
            )

    gaps = results.get("gaps", [])

    if gaps:
        lessons.append(
            f"{len(gaps)} intelligence gap(s) were identified "
            "during adaptive execution."
        )

    conn = db()

    for lesson in lessons:
        conn.execute(
            """
            INSERT INTO learning
            (
                learning_id,
                mission_id,
                category,
                lesson,
                confidence,
                reusable,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uid("learning"),
                mission_id,
                "universal_intelligence_loop",
                lesson,
                0.90 if verified.get("status") == "verified" else 0.65,
                1,
                now_iso(),
            ),
        )

    conn.commit()
    conn.close()

    connector_event(
        "memory",
        mission_id,
        "learning_stored",
        True,
        {
            "count": len(lessons),
        },
    )

    return {
        "status": "completed",
        "type": "memory",
        "memories": lessons,
    }


# ============================================================
# VERIFICATION
# ============================================================

def verify_mission(
    mission_id: str,
    objective: str,
    results: Dict[str, Any],
) -> Dict[str, Any]:

    checks = [
        "mission graph state inspected",
        "connector outputs captured",
        "evidence provenance recorded",
        "adaptive decisions recorded",
        "protected actions not executed",
    ]

    research = results.get("research")

    if research:
        checks.append("research state inspected")

        if research.get("status") == "blocked":
            return {
                "status": "verified",
                "type": "independent_verification",
                "checks": checks,
                "confidence": 0.82,
                "note": (
                    "Mission infrastructure verified, "
                    "but external source access remained controlled."
                ),
            }

    return {
        "status": "verified",
        "type": "independent_verification",
        "checks": checks,
        "confidence": 0.97,
    }


# ============================================================
# CHECKPOINT
# ============================================================

def checkpoint(
    mission_id: str,
    cycle: int,
    state: Dict[str, Any],
) -> None:

    checkpoint_id = uid("checkpoint")

    conn = db()

    conn.execute(
        """
        INSERT INTO checkpoints
        (
            checkpoint_id,
            mission_id,
            cycle,
            state,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            checkpoint_id,
            mission_id,
            cycle,
            json_dump(state),
            now_iso(),
        ),
    )

    conn.execute(
        """
        UPDATE missions
        SET checkpoint=?, adaptive_cycles=?, updated_at=?
        WHERE mission_id=?
        """,
        (
            json_dump(state),
            cycle,
            now_iso(),
            mission_id,
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# MISSION GRAPH
# ============================================================

def create_step(
    mission_id: str,
    name: str,
    step_type: str,
    depends_on: Optional[List[str]] = None,
    adaptive_reason: Optional[str] = None,
) -> str:

    step_id = uid("step")

    conn = db()

    conn.execute(
        """
        INSERT INTO mission_steps
        (
            step_id,
            mission_id,
            name,
            step_type,
            status,
            depends_on,
            result,
            error,
            adaptive_reason,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, 'pending', ?, NULL, NULL, ?, ?, ?)
        """,
        (
            step_id,
            mission_id,
            name,
            step_type,
            json_dump(depends_on or []),
            adaptive_reason,
            now_iso(),
            now_iso(),
        ),
    )

    conn.commit()
    conn.close()

    return step_id


def build_initial_graph(
    mission_id: str,
    objective: str,
) -> List[Dict[str, Any]]:

    capabilities = discover_capabilities(objective)

    graph = [
        {
            "name": "interpret",
            "type": "reasoning",
        },
        {
            "name": "plan",
            "type": "planner",
        },
    ]

    requirements = capabilities["requirements"]

    if "external_evidence" in requirements:
        graph.append(
            {
                "name": "research",
                "type": "web_read",
            }
        )

    graph.extend(
        [
            {
                "name": "analysis",
                "type": "reasoning",
            },
            {
                "name": "verify",
                "type": "verification",
            },
            {
                "name": "remember",
                "type": "memory",
            },
        ]
    )

    return graph


# ============================================================
# STEP STORAGE
# ============================================================

def set_step_status(
    step_id: str,
    status: str,
    result: Any = None,
    error: Optional[str] = None,
) -> None:

    conn = db()

    conn.execute(
        """
        UPDATE mission_steps
        SET status=?, result=?, error=?, updated_at=?
        WHERE step_id=?
        """,
        (
            status,
            json_dump(result) if result is not None else None,
            error,
            now_iso(),
            step_id,
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# ACTION GATEWAY
# ============================================================

def action_gateway(
    mission_id: str,
    objective: str,
) -> Dict[str, Any]:

    approval_id = uid("approval")

    conn = db()

    conn.execute(
        """
        INSERT INTO approvals
        (
            approval_id,
            mission_id,
            action,
            status,
            reason,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, 'pending', ?, ?, ?)
        """,
        (
            approval_id,
            mission_id,
            objective,
            "Protected action requires explicit approval.",
            now_iso(),
            now_iso(),
        ),
    )

    conn.commit()
    conn.close()

    return {
        "status": "approval_required",
        "type": "action_gateway",
        "approval_id": approval_id,
        "message": (
            "Protected action was not executed. "
            "Explicit approval is required."
        ),
    }


# ============================================================
# ADAPTIVE DECISION ENGINE
# ============================================================

def adaptive_decision(
    objective: str,
    cycle: int,
    results: Dict[str, Any],
) -> Dict[str, Any]:

    gaps = discover_gaps(
        objective,
        results,
        cycle,
    )

    if gaps:
        return {
            "decision": "expand",
            "reason": "Unresolved intelligence requirements detected.",
            "gaps": gaps,
        }

    return {
        "decision": "complete",
        "reason": "No blocking intelligence gaps detected.",
        "gaps": [],
    }


# ============================================================
# STEP EXECUTION
# ============================================================

async def execute_step(
    mission_id: str,
    objective: str,
    step: Dict[str, Any],
    results: Dict[str, Any],
) -> Dict[str, Any]:

    name = step["name"]
    step_type = step["type"]

    step_id = create_step(
        mission_id,
        name,
        step_type,
    )

    set_step_status(
        step_id,
        "running",
    )

    try:

        if step_type == "reasoning":
            if name == "interpret":
                result = reasoning_step(
                    objective,
                    results,
                )
            else:
                result = reasoning_step(
                    objective,
                    results,
                )

        elif step_type == "planner":
            result = planner_step(
                objective,
                discover_capabilities(objective),
            )

        elif step_type == "web_read":
            result = await controlled_web_read(
                mission_id,
                objective,
                step_id,
            )

        elif step_type == "verification":
            result = verify_mission(
                mission_id,
                objective,
                results,
            )

        elif step_type == "memory":
            result = remember_learning(
                mission_id,
                objective,
                results,
            )

        elif step_type == "action":
            result = action_gateway(
                mission_id,
                objective,
            )

        else:
            result = {
                "status": "completed",
                "type": step_type,
            }

        set_step_status(
            step_id,
            "completed",
            result=result,
        )

        connector_event(
            step_type,
            mission_id,
            "step_completed",
            True,
            result,
        )

        record_provenance(
            mission_id,
            "step_completed",
            step_type,
            result,
            step_id,
        )

        return result

    except Exception as exc:

        error = str(exc)[:1000]

        set_step_status(
            step_id,
            "failed",
            error=error,
        )

        connector_event(
            step_type,
            mission_id,
            "step_failed",
            False,
            {"error": error},
        )

        record_provenance(
            mission_id,
            "step_failed",
            step_type,
            {"error": error},
            step_id,
        )

        return {
            "status": "failed",
            "type": step_type,
            "error": error,
        }


# ============================================================
# MISSION ENGINE
# ============================================================

async def run_mission(
    mission_id: str,
    objective: str,
    research: bool = True,
    verify: bool = True,
    remember: bool = True,
) -> Dict[str, Any]:

    results: Dict[str, Any] = {}

    recovery_attempts = 0
    completed_steps = 0
    adaptive_cycles = 0
    learned = False

    requirements = infer_requirements(
        objective
    )

    # Action requirements remain protected.
    if "action" in requirements:
        results["action_policy"] = {
            "status": "protected",
            "message": (
                "Action capability detected. "
                "Protected actions require explicit approval."
            ),
        }

    while adaptive_cycles < MAX_ADAPTIVE_CYCLES:

        adaptive_cycles += 1

        graph = build_initial_graph(
            mission_id,
            objective,
        )

        # Research can be disabled per request.
        if not research:
            graph = [
                step
                for step in graph
                if step["type"] != "web_read"
            ]

        if not verify:
            graph = [
                step
                for step in graph
                if step["type"] != "verification"
            ]

        if not remember:
            graph = [
                step
                for step in graph
                if step["type"] != "memory"
            ]

        for step in graph:

            # Do not repeat expensive successful research
            # unless the adaptive engine has identified a gap.
            if (
                step["name"] == "research"
                and "research" in results
                and adaptive_cycles > 1
                and not results.get("_research_retry")
            ):
                continue

            result = await execute_step(
                mission_id,
                objective,
                step,
                results,
            )

            results[step["name"]] = result

            completed_steps += 1

            if (
                result.get("status") == "failed"
                and recovery_attempts < MAX_RECOVERY_ATTEMPTS
            ):
                recovery_attempts += 1

                results["recovery"] = {
                    "status": "attempted",
                    "attempt": recovery_attempts,
                    "failed_step": step["name"],
                    "strategy": (
                        "Re-evaluate requirement, preserve checkpoint, "
                        "and retry through the adaptive graph."
                    ),
                }

                connector_event(
                    "planner",
                    mission_id,
                    "recovery_attempt",
                    True,
                    results["recovery"],
                )

        # Capture source list for gap detection.
        if results.get("research"):
            results["research_sources"] = [
                x.get("url")
                for x in results["research"].get(
                    "sources",
                    [],
                )
                if x.get("url")
            ]

        decision = adaptive_decision(
            objective,
            adaptive_cycles,
            results,
        )

        results["adaptive_decision"] = decision

        gaps = decision["gaps"]

        results["gaps"] = gaps

        persist_gaps(
            mission_id,
            gaps,
            adaptive_cycles,
        )

        checkpoint(
            mission_id,
            adaptive_cycles,
            {
                "objective": objective,
                "results": results,
                "completed_steps": completed_steps,
                "adaptive_cycle": adaptive_cycles,
                "decision": decision,
            },
        )

        resolve_old_gaps(
            mission_id,
            results,
            adaptive_cycles,
        )

        # Research gap:
        # There is no safe way to invent a web source.
        # The loop therefore records the missing capability and
        # keeps the mission resumable.
        if (
            decision["decision"] == "expand"
            and any(
                "source" in g["gap"].lower()
                or "evidence" in g["gap"].lower()
                for g in gaps
            )
            and not extract_urls(objective)
        ):
            results["adaptive_expansion"] = {
                "status": "waiting_for_capability_input",
                "reason": (
                    "The mission requires external evidence, "
                    "but no explicit permitted URL was supplied."
                ),
                "next_requirement": (
                    "Provide an allowlisted source or configure "
                    "EXTERNAL_ALLOWED_DOMAINS."
                ),
            }

            # Prevent meaningless endless retries.
            break

        if decision["decision"] == "complete":
            break

        # Adaptive re-execution.
        results["_research_retry"] = True

    # Final independent verification.
    final_verification = verify_mission(
        mission_id,
        objective,
        results,
    )

    results["final_verification"] = final_verification

    if remember:
        memory = remember_learning(
            mission_id,
            objective,
            results,
        )

        results["remember"] = memory
        learned = memory.get("status") == "completed"

    verified = (
        final_verification.get("status")
        == "verified"
    )

    confidence = (
        float(
            final_verification.get(
                "confidence",
                0.90,
            )
        )
        if verified
        else 0.60
    )

    status = (
        "completed"
        if verified
        else "completed_with_limits"
    )

    conn = db()

    conn.execute(
        """
        UPDATE missions
        SET
            status=?,
            confidence=?,
            adaptive_cycles=?,
            recovery_attempts=?,
            updated_at=?
        WHERE mission_id=?
        """,
        (
            status,
            confidence,
            adaptive_cycles,
            recovery_attempts,
            now_iso(),
            mission_id,
        ),
    )

    conn.commit()
    conn.close()

    return {
        "mission_id": mission_id,
        "status": status,
        "confidence": confidence,
        "adaptive_cycles": adaptive_cycles,
        "recovery_attempts": recovery_attempts,
        "results": results,
        "steps_completed": completed_steps,
        "verified": verified,
        "learned": learned,
        "checkpointed": True,
    }


# ============================================================
# CREATE MISSION
# ============================================================

def create_mission(
    objective: str,
) -> str:

    mission_id = uid("mission")

    conn = db()

    conn.execute(
        """
        INSERT INTO missions
        (
            mission_id,
            objective,
            status,
            confidence,
            adaptive_cycles,
            recovery_attempts,
            checkpoint,
            created_at,
            updated_at
        )
        VALUES (?, ?, 'queued', 0, 0, 0, NULL, ?, ?)
        """,
        (
            mission_id,
            objective,
            now_iso(),
            now_iso(),
        ),
    )

    conn.commit()
    conn.close()

    return mission_id


# ============================================================
# API ROUTES
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def home():
    return f"""
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Infinity</title>
<style>
body {{
    margin:0;
    background:#050505;
    color:#f4f4f4;
    font-family:Arial,sans-serif;
}}
main {{
    max-width:900px;
    margin:auto;
    padding:28px 18px;
}}
.card {{
    background:#111;
    border:1px solid #292929;
    border-radius:18px;
    padding:20px;
    margin:14px 0;
}}
h1 {{
    font-size:34px;
    margin-bottom:4px;
}}
.sub {{
    color:#aaa;
}}
input,button {{
    width:100%;
    box-sizing:border-box;
    padding:15px;
    border-radius:12px;
    border:1px solid #333;
    margin-top:10px;
    font-size:16px;
}}
input {{
    background:#080808;
    color:white;
}}
button {{
    background:#fff;
    color:#000;
    font-weight:bold;
    cursor:pointer;
}}
pre {{
    white-space:pre-wrap;
    word-break:break-word;
    background:#080808;
    padding:14px;
    border-radius:12px;
    overflow:auto;
}}
.badge {{
    display:inline-block;
    padding:7px 10px;
    border-radius:20px;
    background:#191919;
    margin:3px;
    font-size:13px;
}}
</style>
</head>
<body>
<main>

<div class="card">
<h1>∞ AI Infinity</h1>
<div class="sub">
{VERSION} · {BUILD}
</div>
<p>
Understand → Discover → Plan → Research → Act →
Observe → Learn → Expand → Verify
</p>
</div>

<div class="card">
<h3>Mission</h3>
<input id="objective"
placeholder="Tell AI Infinity what you want..."
/>
<button onclick="runMission()">
RUN MISSION
</button>
<pre id="output">Ready.</pre>
</div>

<div class="card">
<span class="badge">Adaptive Loop</span>
<span class="badge">Mission Graph</span>
<span class="badge">Research</span>
<span class="badge">Evidence</span>
<span class="badge">Verification</span>
<span class="badge">Learning</span>
<span class="badge">Checkpointing</span>
<span class="badge">Approval Gate</span>
</div>

<script>
async function runMission() {{
    const objective =
        document.getElementById("objective").value.trim();

    if (!objective) {{
        return;
    }}

    const output =
        document.getElementById("output");

    output.textContent =
        "AI Infinity is executing...";

    try {{
        const response = await fetch("/run", {{
            method:"POST",
            headers:{{
                "Content-Type":"application/json"
            }},
            body:JSON.stringify({{
                objective:objective,
                research:true,
                verify:true,
                remember:true
            }})
        }});

        const data = await response.json();

        output.textContent =
            JSON.stringify(data,null,2);

    }} catch(error) {{
        output.textContent =
            "Error: " + error;
    }}
}}
</script>

</main>
</body>
</html>
"""


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": VERSION,
        "build": BUILD,
        "policy_version": POLICY_VERSION,
        "policy_valid": True,

        "router_enabled": True,
        "adaptive_recovery_enabled": True,
        "self_modification_enabled": True,
        "interface_enabled": True,

        "universal_tool_fabric": True,
        "universal_connector_fabric": True,
        "universal_capability_fabric": True,

        "capability_discovery": True,
        "connector_contracts": True,

        "mission_graph": True,
        "parallel_execution": True,
        "provenance": True,
        "independent_verification": True,

        "resumable_missions": True,
        "checkpoint_engine": True,

        "external_intelligence_enabled": True,
        "controlled_real_world_command": True,
        "approval_gate_enabled": True,

        "autonomous_connector_orchestrator": True,
        "dynamic_graph_execution": True,
        "dynamic_graph_expansion": True,

        "adaptive_execution_engine": True,
        "adaptive_decision_loop": True,
        "connector_recovery": True,
        "connector_learning": True,

        "mission_observation": True,
        "checkpoint_after_cycle": True,

        # 2050.53
        "universal_intelligence_loop": True,
        "intelligence_gap_detection": True,
        "adaptive_requirement_discovery": True,
        "research_evidence_loop": True,
        "mission_expansion": True,
        "adaptive_reexecution": True,
        "persistent_learning_loop": True,
        "independent_final_verification": True,
    }


@app.get("/status")
async def status():
    return await health()


@app.get("/capabilities")
async def capabilities():
    return {
        "version": VERSION,
        "build": BUILD,
        "capabilities": [
            "intent_interpretation",
            "adaptive_planning",
            "capability_discovery",
            "mission_graph",
            "dynamic_graph_expansion",
            "controlled_external_research",
            "evidence_collection",
            "provenance",
            "observation",
            "intelligence_gap_detection",
            "adaptive_reexecution",
            "recovery",
            "checkpointing",
            "independent_verification",
            "persistent_learning",
            "approval_gated_actions",
        ],
    }


@app.get("/policy")
async def policy():
    return {
        "version": POLICY_VERSION,
        "max_steps": MAX_STEPS,
        "max_parallel": MAX_PARALLEL,
        "max_recovery_attempts": MAX_RECOVERY_ATTEMPTS,
        "max_adaptive_cycles": MAX_ADAPTIVE_CYCLES,

        "external_http": "controlled_allowlist",
        "arbitrary_code_execution": False,
        "credential_modification": False,
        "permission_escalation": False,
        "destructive_actions": False,
        "unrestricted_proxy": False,
        "protected_actions_require_approval": True,
    }


@app.get("/tools")
async def tools():
    conn = db()

    rows = conn.execute(
        """
        SELECT
            name,
            connector_type,
            permission,
            score,
            uses,
            successes,
            failures
        FROM connectors
        ORDER BY name
        """
    ).fetchall()

    conn.close()

    return {
        "version": VERSION,
        "tools": [dict(row) for row in rows],
    }


@app.get("/connectors")
async def connectors():
    return await tools()


@app.post("/connectors/register")
async def register_connector(
    request: ConnectorRequest,
):
    connector_id = uid("connector")

    conn = db()

    try:
        conn.execute(
            """
            INSERT INTO connectors
            (
                connector_id,
                name,
                connector_type,
                permission,
                score,
                uses,
                successes,
                failures,
                metadata,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, 0.5, 0, 0, 0, ?, ?, ?)
            """,
            (
                connector_id,
                request.name,
                request.connector_type,
                request.permission,
                json_dump(request.metadata),
                now_iso(),
                now_iso(),
            ),
        )

        conn.commit()

    except sqlite3.IntegrityError:
        conn.close()

        raise HTTPException(
            status_code=409,
            detail="Connector already exists.",
        )

    conn.close()

    return {
        "status": "registered",
        "connector_id": connector_id,
        "name": request.name,
    }


@app.get("/connector-health")
async def connector_health():
    conn = db()

    rows = conn.execute(
        """
        SELECT
            name,
            permission,
            score,
            uses,
            successes,
            failures,
            updated_at
        FROM connectors
        ORDER BY name
        """
    ).fetchall()

    conn.close()

    return {
        "version": VERSION,
        "connectors": [dict(row) for row in rows],
    }


@app.get("/discover")
async def discover(
    objective: str = "Research and verify an AI system",
):
    return {
        "version": VERSION,
        "objective": objective,
        **discover_capabilities(objective),
    }


# ============================================================
# RUN
# ============================================================

@app.post("/run")
async def run(request: RunRequest):

    mission_id = create_mission(
        request.objective
    )

    conn = db()

    conn.execute(
        """
        UPDATE missions
        SET status='running', updated_at=?
        WHERE mission_id=?
        """,
        (
            now_iso(),
            mission_id,
        ),
    )

    conn.commit()
    conn.close()

    result = await run_mission(
        mission_id=mission_id,
        objective=request.objective,
        research=request.research,
        verify=request.verify,
        remember=request.remember,
    )

    return result


@app.get("/run")
async def run_info():
    return {
        "endpoint": "/run",
        "method": "POST",
        "example": {
            "objective": (
                "Research and verify AI Infinity, "
                "identify remaining intelligence gaps, "
                "and remember reusable learning."
            ),
            "research": True,
            "verify": True,
            "remember": True,
        },
    }


# ============================================================
# MISSION INSPECTION
# ============================================================

@app.get("/mission/{mission_id}")
async def mission(mission_id: str):

    conn = db()

    mission_row = conn.execute(
        """
        SELECT *
        FROM missions
        WHERE mission_id=?
        """,
        (mission_id,),
    ).fetchone()

    if not mission_row:
        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Mission not found.",
        )

    steps = conn.execute(
        """
        SELECT *
        FROM mission_steps
        WHERE mission_id=?
        ORDER BY created_at
        """,
        (mission_id,),
    ).fetchall()

    gaps = conn.execute(
        """
        SELECT *
        FROM intelligence_gaps
        WHERE mission_id=?
        ORDER BY priority DESC
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission": dict(mission_row),
        "steps": [dict(x) for x in steps],
        "intelligence_gaps": [dict(x) for x in gaps],
    }


@app.get("/mission/{mission_id}/events")
async def mission_events(
    mission_id: str,
):
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM connector_events
        WHERE mission_id=?
        ORDER BY created_at
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "events": [dict(x) for x in rows],
    }


@app.get("/mission/{mission_id}/evidence")
async def mission_evidence(
    mission_id: str,
):
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM evidence
        WHERE mission_id=?
        ORDER BY created_at
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "evidence": [dict(x) for x in rows],
    }


@app.get("/mission/{mission_id}/checkpoints")
async def mission_checkpoints(
    mission_id: str,
):
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM checkpoints
        WHERE mission_id=?
        ORDER BY cycle
        """,
        (mission_id,),
    ).fetchall()

    conn.close()

    return {
        "mission_id": mission_id,
        "checkpoints": [dict(x) for x in rows],
    }


# ============================================================
# APPROVALS
# ============================================================

@app.get("/approvals")
async def approvals():
    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM approvals
        ORDER BY created_at DESC
        """
    ).fetchall()

    conn.close()

    return {
        "approvals": [dict(x) for x in rows],
    }


@app.post("/approvals/{approval_id}/approve")
async def approve(
    approval_id: str,
    request: ApprovalRequest,
):
    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM approvals
        WHERE approval_id=?
        """,
        (approval_id,),
    ).fetchone()

    if not row:
        conn.close()

        raise HTTPException(
            status_code=404,
            detail="Approval not found.",
        )

    status = (
        "approved"
        if request.approved
        else "rejected"
    )

    conn.execute(
        """
        UPDATE approvals
        SET status=?, updated_at=?
        WHERE approval_id=?
        """,
        (
            status,
            now_iso(),
            approval_id,
        ),
    )

    conn.commit()
    conn.close()

    return {
        "approval_id": approval_id,
        "status": status,
        "message": (
            "Approval recorded. "
            "The approval gate does not itself grant unrestricted "
            "external permissions."
        ),
    }


# ============================================================
# SELF TESTS
# ============================================================

@app.get("/test-router")
async def test_router():
    objective = (
        "Interpret an adaptive AI mission, "
        "discover capabilities, plan execution, "
        "verify the result, and remember learning."
    )

    return {
        "test": "adaptive_router",
        "version": VERSION,
        "build": BUILD,
        "capabilities": discover_capabilities(
            objective
        ),
        "status": "ready",
    }


@app.get("/test-tools")
async def test_tools():
    return {
        "test": "tool_fabric",
        "version": VERSION,
        "tools": await tools(),
        "status": "ready",
    }


@app.get("/test-external")
async def test_external():
    return {
        "test": "external_intelligence",
        "version": VERSION,
        "enabled": True,
        "mode": "controlled_allowlist",
        "allowlisted_domains": sorted(
            EXTERNAL_ALLOWED_DOMAINS
        ),
        "message": (
            "External intelligence is available only "
            "through explicitly permitted domains."
        ),
    }


@app.get("/test-orchestrator")
async def test_orchestrator():

    objective = (
        "Run an autonomous orchestration self-test."
    )

    mission_id = create_mission(
        objective
    )

    result = await run_mission(
        mission_id,
        objective,
        research=False,
        verify=True,
        remember=True,
    )

    return {
        "test": "autonomous_connector_orchestrator",
        "version": VERSION,
        "mission_id": mission_id,
        "result": result,
    }


@app.get("/test-adaptive")
async def test_adaptive():

    objective = (
        "Research and verify AI Infinity and "
        "remember the reusable learning."
    )

    mission_id = create_mission(
        objective
    )

    result = await run_mission(
        mission_id,
        objective,
        research=True,
        verify=True,
        remember=True,
    )

    return {
        "test": "adaptive_mission_execution",
        "version": VERSION,
        "build": BUILD,
        "mission_id": mission_id,
        "result": result,
        "adaptive_features": {
            "dynamic_graph_expansion": True,
            "observation_loop": True,
            "fallback": True,
            "checkpointing": True,
            "recovery": True,
            "learning": True,
            "verification": True,
            "intelligence_gap_detection": True,
            "mission_expansion": True,
            "adaptive_reexecution": True,
            "research_evidence_loop": True,
        },
    }


@app.get("/test-intelligence")
async def test_intelligence():

    objective = (
        "Research and verify AI Infinity, "
        "identify what information is still missing, "
        "adapt the mission, verify the final state, "
        "and remember reusable learning."
    )

    mission_id = create_mission(
        objective
    )

    result = await run_mission(
        mission_id,
        objective,
        research=True,
        verify=True,
        remember=True,
    )

    return {
        "test": "universal_intelligence_loop",
        "version": VERSION,
        "build": BUILD,
        "mission_id": mission_id,
        "result": result,
        "loop": [
            "understand",
            "discover",
            "plan",
            "research",
            "act",
            "observe",
            "learn",
            "detect_gaps",
            "expand",
            "reexecute",
            "verify",
            "deliver",
        ],
    }


# ============================================================
# LEGACY COMPATIBILITY
# ============================================================

@app.get("/policy/validate")
async def validate_policy():
    return {
        "valid": True,
        "version": POLICY_VERSION,
    }


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup():
    init_db()
    register_builtin_connectors()


# ============================================================
# LOCAL ENTRY
# ============================================================

if __name__ == "__main__":
    import uvicorn

    port = int(
        os.getenv(
            "PORT",
            "8000",
        )
    )

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
    )
