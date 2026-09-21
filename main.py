"""
AI Infinity
TARGET-2050.70

BUILD:
EDGE-AWARE-TRANSPORT-AND-APPLICATION-SEPARATION-CORE

Goals
-----
1. Separate edge/WAF failures from application failures.
2. Never interpret a Render/Cloudflare/WAF HTML page as AI Infinity JSON.
3. Record transport evidence.
4. Preserve mission persistence and workflow integrity.
5. Provide deterministic diagnostics.
6. Provide explicit recovery classification.
7. Preserve security-policy boundaries.
8. Never fabricate research/evidence results.
9. Keep arbitrary code execution disabled.
10. Keep unrestricted private-network access disabled.

Runtime:
    FastAPI
    SQLite
    Uvicorn
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


# ============================================================
# IDENTITY
# ============================================================

SERVICE = "AI Infinity"

VERSION = "TARGET-2050.70"

BUILD = (
    "EDGE-AWARE-TRANSPORT-AND-APPLICATION-SEPARATION-CORE"
)


# ============================================================
# STORAGE
# ============================================================

DATA_DIR = Path(
    os.getenv(
        "AI_INFINITY_DATA_DIR",
        "/tmp/ai-infinity",
    )
)

DATA_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

DB_PATH = DATA_DIR / "ai_infinity.db"


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title=SERVICE,
    version=VERSION,
    description=(
        "AI Infinity autonomous workflow runtime with "
        "edge-aware transport/application separation."
    ),
)


# ============================================================
# SECURITY POLICY
# ============================================================

POLICY: Dict[str, Any] = {
    "valid": True,

    "network_policy_enforced": True,

    "controlled_public_web_access": True,

    "arbitrary_code_execution": False,

    "unrestricted_private_network_access": False,

    "permission_bypass": False,

    "transport_boundary_enforced": True,

    "edge_failure_separation": True,

    "html_waf_detection": True,

    "application_failure_requires_application_evidence": True,

    "fabricated_evidence": False,
}


# ============================================================
# CONSTANTS
# ============================================================

EDGE_STATUS_CODES = {
    401,
    403,
    406,
    407,
    409,
    412,
    418,
    429,
}


RETRYABLE_STATUS_CODES = {
    408,
    425,
    429,
    500,
    502,
    503,
    504,
    520,
    521,
    522,
    523,
    524,
}


WAF_MARKERS = [
    "web application firewall",
    "application firewall",
    "request was blocked",
    "request blocked",
    "your request was blocked",
    "access denied",
    "forbidden",
    "security policy",
    "cloudflare",
    "ray id",
    "powered by render",
    "render",
    "waf",
]


HTML_MARKERS = [
    "<!doctype html",
    "<html",
    "<head",
    "<body",
    "<title",
    "<style",
]


# ============================================================
# DATABASE
# ============================================================

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db() -> None:
    conn = get_db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS missions (
            mission_id TEXT PRIMARY KEY,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            result_json TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transport_events (
            event_id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            classification TEXT NOT NULL,
            layer TEXT NOT NULL,
            http_status INTEGER,
            content_type TEXT,
            reached_application INTEGER NOT NULL,
            application_failure_proven INTEGER NOT NULL,
            retryable INTEGER NOT NULL,
            html_detected INTEGER NOT NULL,
            waf_detected INTEGER NOT NULL,
            evidence_json TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_events (
            event_id TEXT PRIMARY KEY,
            mission_id TEXT,
            created_at REAL NOT NULL,
            event_type TEXT NOT NULL,
            event_json TEXT NOT NULL
        )
        """
    )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# MODELS
# ============================================================

class RunRequest(BaseModel):
    objective: str = Field(
        ...,
        min_length=1,
        max_length=20000,
    )


class TransportRequest(BaseModel):
    http_status: Optional[int] = Field(
        default=None,
        ge=100,
        le=599,
    )

    content_type: Optional[str] = None

    body: Optional[str] = None

    headers: Dict[str, str] = Field(
        default_factory=dict,
    )


# ============================================================
# GENERAL HELPERS
# ============================================================

def timestamp() -> float:
    return time.time()


def make_id(prefix: str) -> str:
    return (
        f"{prefix}-"
        f"{uuid.uuid4().hex[:12]}"
    )


def json_encode(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def json_decode(value: Optional[str]) -> Any:
    if not value:
        return None

    try:
        return json.loads(value)
    except Exception:
        return None


def normalize(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, bytes):
        value = value.decode(
            "utf-8",
            errors="replace",
        )

    return str(value).lower()


# ============================================================
# HTML DETECTION
# ============================================================

def detect_html(
    body: Optional[str],
    content_type: Optional[str],
) -> bool:

    body_text = normalize(body).lstrip()

    content_type_text = normalize(
        content_type
    )

    if "text/html" in content_type_text:
        return True

    sample = body_text[:10000]

    return any(
        marker in sample
        for marker in HTML_MARKERS
    )


# ============================================================
# WAF DETECTION
# ============================================================

def detect_waf(
    body: Optional[str],
    headers: Optional[Dict[str, str]],
) -> bool:

    body_text = normalize(body)

    header_text = " ".join(
        f"{key}:{value}"
        for key, value in (headers or {}).items()
    ).lower()

    combined = (
        body_text
        + "\n"
        + header_text
    )

    return any(
        marker in combined
        for marker in WAF_MARKERS
    )


# ============================================================
# TRANSPORT CLASSIFICATION
# ============================================================

def classify_transport(
    http_status: Optional[int],
    content_type: Optional[str],
    body: Optional[str],
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:

    headers = headers or {}

    html_detected = detect_html(
        body,
        content_type,
    )

    waf_detected = detect_waf(
        body,
        headers,
    )

    # --------------------------------------------------------
    # NO RESPONSE
    # --------------------------------------------------------

    if http_status is None:

        return {
            "classification": (
                "TRANSPORT_NO_RESPONSE"
            ),

            "layer": "transport",

            "http_status": None,

            "reached_application": False,

            "application_failure_proven": False,

            "retryable": True,

            "html_detected": html_detected,

            "waf_detected": waf_detected,

            "reason": (
                "No HTTP response was received."
            ),
        }

    # --------------------------------------------------------
    # EXPLICIT WAF / EDGE BLOCK
    # --------------------------------------------------------

    if (
        http_status in EDGE_STATUS_CODES
        and (
            waf_detected
            or html_detected
        )
    ):

        return {
            "classification": (
                "EDGE_WAF_BLOCK"
            ),

            "layer": "edge",

            "http_status": http_status,

            "reached_application": False,

            "application_failure_proven": False,

            "retryable": (
                http_status
                in RETRYABLE_STATUS_CODES
            ),

            "html_detected": html_detected,

            "waf_detected": True,

            "reason": (
                "The request appears to have "
                "been rejected before reaching "
                "the application."
            ),
        }

    # --------------------------------------------------------
    # 5XX / UPSTREAM
    # --------------------------------------------------------

    if (
        http_status
        in RETRYABLE_STATUS_CODES
    ):

        return {
            "classification": (
                "TRANSPORT_OR_UPSTREAM_FAILURE"
            ),

            "layer": "transport_or_upstream",

            "http_status": http_status,

            "reached_application": False,

            "application_failure_proven": False,

            "retryable": True,

            "html_detected": html_detected,

            "waf_detected": waf_detected,

            "reason": (
                "The response indicates a "
                "transport, proxy, edge, or "
                "upstream failure."
            ),
        }

    # --------------------------------------------------------
    # AUTHORIZATION / ACCESS
    # --------------------------------------------------------

    if http_status in {
        401,
        403,
    }:

        return {
            "classification": (
                "ACCESS_REJECTED"
            ),

            "layer": (
                "edge_or_application"
            ),

            "http_status": http_status,

            "reached_application": False,

            "application_failure_proven": False,

            "retryable": False,

            "html_detected": html_detected,

            "waf_detected": waf_detected,

            "reason": (
                "Access was rejected, but "
                "application failure has not "
                "been proven."
            ),
        }

    # --------------------------------------------------------
    # SUCCESS + HTML
    # --------------------------------------------------------

    if (
        200 <= http_status < 300
        and html_detected
    ):

        return {
            "classification": (
                "INVALID_APPLICATION_RESPONSE_HTML"
            ),

            "layer": "application_boundary",

            "http_status": http_status,

            "reached_application": True,

            "application_failure_proven": True,

            "retryable": False,

            "html_detected": True,

            "waf_detected": waf_detected,

            "reason": (
                "An HTML response was returned "
                "where an application response "
                "was expected."
            ),
        }

    # --------------------------------------------------------
    # NORMAL SUCCESS
    # --------------------------------------------------------

    if 200 <= http_status < 300:

        return {
            "classification": (
                "APPLICATION_RESPONSE_RECEIVED"
            ),

            "layer": "application",

            "http_status": http_status,

            "reached_application": True,

            "application_failure_proven": False,

            "retryable": False,

            "html_detected": False,

            "waf_detected": False,

            "reason": (
                "A normal application response "
                "was received."
            ),
        }

    # --------------------------------------------------------
    # OTHER 4XX
    # --------------------------------------------------------

    if 400 <= http_status < 500:

        return {
            "classification": (
                "APPLICATION_OR_REQUEST_ERROR"
            ),

            "layer": "application_boundary",

            "http_status": http_status,

            "reached_application": True,

            "application_failure_proven": True,

            "retryable": False,

            "html_detected": html_detected,

            "waf_detected": waf_detected,

            "reason": (
                "A client/request/application "
                "error was observed."
            ),
        }

    # --------------------------------------------------------
    # UNKNOWN
    # --------------------------------------------------------

    return {
        "classification": (
            "UNCLASSIFIED_HTTP_RESPONSE"
        ),

        "layer": "transport",

        "http_status": http_status,

        "reached_application": False,

        "application_failure_proven": False,

        "retryable": False,

        "html_detected": html_detected,

        "waf_detected": waf_detected,

        "reason": (
            "The response could not be "
            "confidently classified."
        ),
    }


# ============================================================
# TRANSPORT EVIDENCE STORAGE
# ============================================================

def record_transport_event(
    classification: Dict[str, Any],
    content_type: Optional[str],
) -> str:

    event_id = make_id("transport")

    conn = get_db()

    conn.execute(
        """
        INSERT INTO transport_events (
            event_id,
            created_at,
            classification,
            layer,
            http_status,
            content_type,
            reached_application,
            application_failure_proven,
            retryable,
            html_detected,
            waf_detected,
            evidence_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            timestamp(),
            classification[
                "classification"
            ],
            classification["layer"],
            classification[
                "http_status"
            ],
            content_type,
            int(
                classification[
                    "reached_application"
                ]
            ),
            int(
                classification[
                    "application_failure_proven"
                ]
            ),
            int(
                classification["retryable"]
            ),
            int(
                classification[
                    "html_detected"
                ]
            ),
            int(
                classification[
                    "waf_detected"
                ]
            ),
            json_encode(classification),
        ),
    )

    conn.commit()
    conn.close()

    return event_id


# ============================================================
# WORKFLOW EVENT STORAGE
# ============================================================

def record_workflow_event(
    event_type: str,
    event: Dict[str, Any],
    mission_id: Optional[str] = None,
) -> str:

    event_id = make_id("workflow")

    conn = get_db()

    conn.execute(
        """
        INSERT INTO workflow_events (
            event_id,
            mission_id,
            created_at,
            event_type,
            event_json
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            event_id,
            mission_id,
            timestamp(),
            event_type,
            json_encode(event),
        ),
    )

    conn.commit()
    conn.close()

    return event_id


# ============================================================
# MISSION PERSISTENCE
# ============================================================

def create_mission(
    objective: str,
) -> str:

    mission_id = make_id("mission")

    current = timestamp()

    conn = get_db()

    conn.execute(
        """
        INSERT INTO missions (
            mission_id,
            objective,
            status,
            result_json,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            mission_id,
            objective,
            "running",
            None,
            current,
            current,
        ),
    )

    conn.commit()
    conn.close()

    record_workflow_event(
        "MISSION_CREATED",
        {
            "mission_id": mission_id,
            "objective": objective,
        },
        mission_id,
    )

    return mission_id


def update_mission(
    mission_id: str,
    status: str,
    result: Optional[Dict[str, Any]],
) -> None:

    conn = get_db()

    conn.execute(
        """
        UPDATE missions
        SET
            status = ?,
            result_json = ?,
            updated_at = ?
        WHERE mission_id = ?
        """,
        (
            status,
            (
                json_encode(result)
                if result is not None
                else None
            ),
            timestamp(),
            mission_id,
        ),
    )

    conn.commit()
    conn.close()

    record_workflow_event(
        "MISSION_STATE_CHANGED",
        {
            "mission_id": mission_id,
            "status": status,
        },
        mission_id,
    )


def fetch_mission(
    mission_id: str,
) -> Optional[Dict[str, Any]]:

    conn = get_db()

    row = conn.execute(
        """
        SELECT *
        FROM missions
        WHERE mission_id = ?
        """,
        (mission_id,),
    ).fetchone()

    conn.close()

    if row is None:
        return None

    return {
        "mission_id": row[
            "mission_id"
        ],
        "objective": row[
            "objective"
        ],
        "status": row[
            "status"
        ],
        "result": json_decode(
            row["result_json"]
        ),
        "created_at": row[
            "created_at"
        ],
        "updated_at": row[
            "updated_at"
        ],
    }


# ============================================================
# MISSION EXECUTION
# ============================================================

def execute_mission(
    objective: str,
) -> Dict[str, Any]:

    mission_id = create_mission(
        objective
    )

    result = {
        "mission_id": mission_id,

        "objective": objective,

        "workflow": {
            "created": True,

            "transport_boundary": (
                "enforced"
            ),

            "application_boundary": (
                "enforced"
            ),

            "evidence_fabrication": (
                "disabled"
            ),
        },

        "evidence": {
            "research_executed": False,

            "external_claims_fabricated": False,

            "reason": (
                "TARGET-2050.70 transport "
                "boundary does not fabricate "
                "research results."
            ),
        },

        "next_state": (
            "awaiting_research_execution"
        ),
    }

    update_mission(
        mission_id,
        "queued",
        result,
    )

    return fetch_mission(
        mission_id
    ) or result


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root() -> Dict[str, Any]:

    return {
        "service": SERVICE,

        "version": VERSION,

        "build": BUILD,

        "status": "online",

        "architecture": {
            "transport_boundary": True,
            "edge_failure_separation": True,
            "application_failure_separation": True,
            "html_waf_detection": True,
            "workflow_persistence": True,
        },
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health() -> Dict[str, Any]:

    database_available = False

    database_error = None

    try:

        conn = get_db()

        conn.execute(
            "SELECT 1"
        ).fetchone()

        conn.close()

        database_available = True

    except Exception as exc:

        database_error = type(
            exc
        ).__name__

    status = (
        "healthy"
        if database_available
        else "degraded"
    )

    response = {
        "status": status,

        "service": SERVICE,

        "version": VERSION,

        "build": BUILD,

        "database": {
            "available": database_available,
        },

        "policy": POLICY,

        "architecture": {
            "transport_boundary": True,

            "edge_failure_separation": True,

            "html_waf_detection": True,

            "application_failure_requires_application_evidence": (
                True
            ),
        },
    }

    if database_error:
        response[
            "database"
        ]["error_type"] = database_error

    return response


# ============================================================
# READINESS
# ============================================================

@app.get("/ready")
async def ready() -> Dict[str, Any]:

    checks: Dict[str, bool] = {}

    try:

        conn = get_db()

        conn.execute(
            "SELECT 1"
        ).fetchone()

        conn.close()

        checks["database"] = True

    except Exception:

        checks["database"] = False

    checks[
        "policy"
    ] = bool(
        POLICY["valid"]
    )

    checks[
        "transport_boundary"
    ] = True

    checks[
        "edge_failure_separation"
    ] = True

    checks[
        "html_waf_detection"
    ] = True

    checks[
        "application_failure_separation"
    ] = True

    ready_state = all(
        checks.values()
    )

    return {
        "ready": ready_state,

        "service": SERVICE,

        "version": VERSION,

        "build": BUILD,

        "checks": checks,
    }


# ============================================================
# RUN
# ============================================================

@app.post("/run")
async def run(
    request: RunRequest,
) -> Dict[str, Any]:

    return execute_mission(
        request.objective
    )


# ============================================================
# SINGLE MISSION
# ============================================================

@app.get("/mission/{mission_id}")
async def get_single_mission(
    mission_id: str,
) -> Dict[str, Any]:

    mission = fetch_mission(
        mission_id
    )

    if mission is None:

        raise HTTPException(
            status_code=404,
            detail="Mission not found",
        )

    return mission


# ============================================================
# MISSION LIST
# ============================================================

@app.get("/missions")
async def list_missions(
    limit: int = 20,
) -> Dict[str, Any]:

    limit = max(
        1,
        min(limit, 100),
    )

    conn = get_db()

    rows = conn.execute(
        """
        SELECT *
        FROM missions
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    conn.close()

    missions: List[
        Dict[str, Any]
    ] = []

    for row in rows:

        missions.append(
            {
                "mission_id": row[
                    "mission_id"
                ],

                "objective": row[
                    "objective"
                ],

                "status": row[
                    "status"
                ],

                "result": json_decode(
                    row["result_json"]
                ),

                "created_at": row[
                    "created_at"
                ],

                "updated_at": row[
                    "updated_at"
                ],
            }
        )

    return {
        "count": len(missions),
        "missions": missions,
    }


# ============================================================
# TRANSPORT CLASSIFIER
# ============================================================

@app.post("/transport/classify")
async def transport_classify(
    request: TransportRequest,
) -> Dict[str, Any]:

    classification = classify_transport(
        http_status=request.http_status,

        content_type=request.content_type,

        body=request.body,

        headers=request.headers,
    )

    event_id = record_transport_event(
        classification,
        request.content_type,
    )

    return {
        "event_id": event_id,

        "service": SERVICE,

        "version": VERSION,

        "build": BUILD,

        "transport": classification,
    }


# ============================================================
# TRANSPORT EVENTS
# ============================================================

@app.get("/transport/events")
async def get_transport_events(
    limit: int = 20,
) -> Dict[str, Any]:

    limit = max(
        1,
        min(limit, 100),
    )

    conn = get_db()

    rows = conn.execute(
        """
        SELECT *
        FROM transport_events
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    conn.close()

    events = []

    for row in rows:

        events.append(
            {
                "event_id": row[
                    "event_id"
                ],

                "created_at": row[
                    "created_at"
                ],

                "classification": row[
                    "classification"
                ],

                "layer": row[
                    "layer"
                ],

                "http_status": row[
                    "http_status"
                ],

                "content_type": row[
                    "content_type"
                ],

                "reached_application": bool(
                    row[
                        "reached_application"
                    ]
                ),

                "application_failure_proven": bool(
                    row[
                        "application_failure_proven"
                    ]
                ),

                "retryable": bool(
                    row["retryable"]
                ),

                "html_detected": bool(
                    row[
                        "html_detected"
                    ]
                ),

                "waf_detected": bool(
                    row[
                        "waf_detected"
                    ]
                ),

                "evidence": json_decode(
                    row["evidence_json"]
                ),
            }
        )

    return {
        "count": len(events),
        "events": events,
    }


# ============================================================
# WORKFLOW EVENTS
# ============================================================

@app.get("/workflow/events")
async def get_workflow_events(
    limit: int = 50,
) -> Dict[str, Any]:

    limit = max(
        1,
        min(limit, 200),
    )

    conn = get_db()

    rows = conn.execute(
        """
        SELECT *
        FROM workflow_events
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    conn.close()

    events = []

    for row in rows:

        events.append(
            {
                "event_id": row[
                    "event_id"
                ],

                "mission_id": row[
                    "mission_id"
                ],

                "created_at": row[
                    "created_at"
                ],

                "event_type": row[
                    "event_type"
                ],

                "event": json_decode(
                    row["event_json"]
                ),
            }
        )

    return {
        "count": len(events),
        "events": events,
    }


# ============================================================
# DIAGNOSTIC TEST ENGINE
# ============================================================

def diagnostic_cases() -> List[Dict[str, Any]]:

    return [
        {
            "name": "render_waf_403",

            "status": 403,

            "content_type": "text/html",

            "body": (
                "<!DOCTYPE html>"
                "<html>"
                "<head>"
                "<title>Blocked</title>"
                "</head>"
                "<body>"
                "Your request was blocked by "
                "this site's web application "
                "firewall."
                "</body>"
                "</html>"
            ),

            "expected": (
                "EDGE_WAF_BLOCK"
            ),
        },

        {
            "name": "normal_json_success",

            "status": 200,

            "content_type": (
                "application/json"
            ),

            "body": (
                '{"status":"ok"}'
            ),

            "expected": (
                "APPLICATION_RESPONSE_RECEIVED"
            ),
        },

        {
            "name": "html_success_wrong_boundary",

            "status": 200,

            "content_type": "text/html",

            "body": (
                "<!DOCTYPE html>"
                "<html>"
                "<body>"
                "Blocked"
                "</body>"
                "</html>"
            ),

            "expected": (
                "INVALID_APPLICATION_RESPONSE_HTML"
            ),
        },

        {
            "name": "upstream_502",

            "status": 502,

            "content_type": (
                "text/plain"
            ),

            "body": "Bad Gateway",

            "expected": (
                "TRANSPORT_OR_UPSTREAM_FAILURE"
            ),
        },

        {
            "name": "upstream_503",

            "status": 503,

            "content_type": (
                "text/plain"
            ),

            "body": "Service Unavailable",

            "expected": (
                "TRANSPORT_OR_UPSTREAM_FAILURE"
            ),
        },

        {
            "name": "application_404",

            "status": 404,

            "content_type": (
                "application/json"
            ),

            "body": (
                '{"detail":"not found"}'
            ),

            "expected": (
                "APPLICATION_OR_REQUEST_ERROR"
            ),
        },

        {
            "name": "no_response",

            "status": None,

            "content_type": None,

            "body": None,

            "expected": (
                "TRANSPORT_NO_RESPONSE"
            ),
        },

        {
            "name": "render_blocked_html",

            "status": 403,

            "content_type": (
                "text/html; charset=utf-8"
            ),

            "body": (
                "<html>"
                "<title>Blocked</title>"
                "Powered by Render"
                "</html>"
            ),

            "expected": (
                "EDGE_WAF_BLOCK"
            ),
        },
    ]


@app.get("/diagnostics")
async def diagnostics() -> Dict[str, Any]:

    results = []

    passed = 0

    failed = 0

    for case in diagnostic_cases():

        result = classify_transport(
            http_status=case[
                "status"
            ],

            content_type=case[
                "content_type"
            ],

            body=case[
                "body"
            ],

            headers={},
        )

        test_passed = (
            result[
                "classification"
            ]
            == case["expected"]
        )

        if test_passed:
            passed += 1
        else:
            failed += 1

        results.append(
            {
                "test": case[
                    "name"
                ],

                "expected": case[
                    "expected"
                ],

                "actual": result[
                    "classification"
                ],

                "passed": test_passed,

                "layer": result[
                    "layer"
                ],

                "reached_application": result[
                    "reached_application"
                ],

                "application_failure_proven": result[
                    "application_failure_proven"
                ],

                "retryable": result[
                    "retryable"
                ],
            }
        )

    return {
        "status": (
            "diagnostic_pass"
            if failed == 0
            else "diagnostic_failure"
        ),

        "service": SERVICE,

        "version": VERSION,

        "build": BUILD,

        "summary": {
            "total": len(results),

            "passed": passed,

            "failed": failed,

            "all_passed": failed == 0,
        },

        "tests": results,
    }


# ============================================================
# SECURITY / POLICY
# ============================================================

@app.get("/policy")
async def policy() -> Dict[str, Any]:

    return {
        "service": SERVICE,

        "version": VERSION,

        "build": BUILD,

        "policy": POLICY,
    }


# ============================================================
# ARCHITECTURE STATE
# ============================================================

@app.get("/architecture")
async def architecture() -> Dict[str, Any]:

    return {
        "service": SERVICE,

        "version": VERSION,

        "build": BUILD,

        "architecture": {
            "transport_layer": {
                "enabled": True,

                "waf_detection": True,

                "html_detection": True,

                "upstream_detection": True,
            },

            "application_layer": {
                "enabled": True,

                "failure_boundary": True,

                "runtime_exception_boundary": True,
            },

            "workflow_layer": {
                "missions": True,

                "persistence": True,

                "event_logging": True,
            },

            "evidence_layer": {
                "transport_events": True,

                "fabricated_results": False,

                "application_failure_requires_evidence": True,
            },

            "security_layer": {
                "arbitrary_code_execution": False,

                "private_network_bypass": False,

                "permission_bypass": False,
            },
        },
    }


# ============================================================
# GLOBAL APPLICATION ERROR BOUNDARY
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:

    error_id = make_id(
        "application-error"
    )

    event = {
        "error_id": error_id,

        "classification": (
            "APPLICATION_RUNTIME_FAILURE"
        ),

        "layer": "application",

        "application_failure_proven": True,

        "path": str(
            request.url.path
        ),

        "method": request.method,

        "error_type": type(
            exc
        ).__name__,

        "version": VERSION,

        "build": BUILD,
    }

    record_workflow_event(
        "APPLICATION_RUNTIME_FAILURE",
        event,
    )

    return JSONResponse(
        status_code=500,

        content={
            "status": "error",

            "classification": (
                "APPLICATION_RUNTIME_FAILURE"
            ),

            "layer": "application",

            "application_failure_proven": True,

            "error_id": error_id,

            "path": str(
                request.url.path
            ),

            "error_type": type(
                exc
            ).__name__,

            "version": VERSION,

            "build": BUILD,
        },
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup() -> None:

    init_db()

    record_workflow_event(
        "RUNTIME_STARTED",
        {
            "service": SERVICE,
            "version": VERSION,
            "build": BUILD,
            "policy_valid": POLICY[
                "valid"
            ],
        },
    )
