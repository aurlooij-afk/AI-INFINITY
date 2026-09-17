import os
import re
import json
import uuid
import time
import hashlib
import ast
import sqlite3
import traceback
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY
# EXECUTION + VERIFICATION + MEMORY + GENOME + SELF-HEALING
# ============================================================

VERSION = "21.0.0"
APP_NAME = "AI Infinity"

# ------------------------------------------------------------
# Storage
# ------------------------------------------------------------

BASE = Path(
    os.getenv(
        "AI_INFINITY_DATA",
        "/tmp/ai-infinity"
    )
)

TASK_DIR = BASE / "tasks"
ARTIFACT_DIR = BASE / "artifacts"
MEMORY_DIR = BASE / "memory"
GENOME_DIR = BASE / "genomes"
AUDIT_DIR = BASE / "audit"
SNAPSHOT_DIR = BASE / "snapshots"

for directory in [
    BASE,
    TASK_DIR,
    ARTIFACT_DIR,
    MEMORY_DIR,
    GENOME_DIR,
    AUDIT_DIR,
    SNAPSHOT_DIR,
]:
    directory.mkdir(
        parents=True,
        exist_ok=True
    )


# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

HF_TOKEN = os.getenv(
    "HF_TOKEN",
    ""
).strip()

HF_MODEL = os.getenv(
    "HF_MODEL",
    "openai/gpt-oss-120b:cheapest"
).strip()

HF_URL = (
    "https://router.huggingface.co/"
    "v1/chat/completions"
)

REQUEST_TIMEOUT = int(
    os.getenv(
        "REQUEST_TIMEOUT",
        "60"
    )
)

MAX_ARTIFACT_BYTES = int(
    os.getenv(
        "MAX_ARTIFACT_BYTES",
        "200000"
    )
)

MAX_MEMORY_ITEMS = int(
    os.getenv(
        "MAX_MEMORY_ITEMS",
        "100"
    )
)

# IMPORTANT:
# Arbitrary shell/code execution is intentionally disabled.
ALLOW_ARBITRARY_EXECUTION = (
    os.getenv(
        "AI_INFINITY_ALLOW_ARBITRARY_EXECUTION",
        "false"
    ).lower()
    == "true"
)

ZERO_DOLLAR_MODE = (
    os.getenv(
        "AI_INFINITY_ZERO_DOLLAR",
        "true"
    ).lower()
    == "true"
)


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description=(
        "AI Infinity execution, verification, "
        "memory, intelligence genome and "
        "self-healing engine."
    )
)


# ============================================================
# UTILITIES
# ============================================================

def now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def make_id(prefix):
    return (
        prefix
        + "-"
        + uuid.uuid4().hex[:12]
    )


def sha256_text(text):
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(
        data
    ).hexdigest()


def safe_name(value):
    value = str(value or "file")

    value = re.sub(
        r"[^a-zA-Z0-9_.-]",
        "_",
        value
    )

    value = value.strip("._")

    if not value:
        value = "artifact"

    return value[:120]


def write_json(path, data):

    tmp = Path(
        str(path) + ".tmp"
    )

    tmp.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    tmp.replace(path)


def read_json(path):

    path = Path(path)

    if not path.exists():
        return None

    try:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return None


# ============================================================
# AUDIT ENGINE
# ============================================================

def audit(event, payload=None):

    record = {

        "event_id":
            make_id("event"),

        "timestamp":
            now(),

        "event":
            event,

        "payload":
            payload or {},
    }

    write_json(

        AUDIT_DIR
        /
        (
            record["event_id"]
            +
            ".json"
        ),

        record
    )

    return record


# ============================================================
# SQLITE MEMORY INDEX
# ============================================================

DB_PATH = BASE / "infinity.db"


def db():

    connection = sqlite3.connect(
        DB_PATH
    )

    connection.row_factory = (
        sqlite3.Row
    )

    return connection


def initialize_db():

    connection = db()

    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            created_at TEXT,
            updated_at TEXT,
            status TEXT,
            objective TEXT,
            result TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            memory_id TEXT PRIMARY KEY,
            created_at TEXT,
            task_id TEXT,
            objective TEXT,
            summary TEXT,
            confidence REAL,
            memory_hash TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS genomes (
            genome_id TEXT PRIMARY KEY,
            created_at TEXT,
            task_id TEXT,
            objective TEXT,
            genome_hash TEXT,
            performance TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS executions (
            execution_id TEXT PRIMARY KEY,
            task_id TEXT,
            created_at TEXT,
            action TEXT,
            status TEXT,
            artifact_path TEXT,
            verification TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS failures (
            failure_id TEXT PRIMARY KEY,
            task_id TEXT,
            created_at TEXT,
            stage TEXT,
            error TEXT,
            recovery TEXT
        )
    """)

    connection.commit()
    connection.close()


initialize_db()


# ============================================================
# TASK STORAGE
# ============================================================

def save_task(task):

    connection = db()

    connection.execute(
        """
        INSERT OR REPLACE INTO tasks
        (
            task_id,
            created_at,
            updated_at,
            status,
            objective,
            result
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            task["task_id"],
            task["created_at"],
            now(),
            task["status"],
            task["objective"],
            json.dumps(
                task,
                ensure_ascii=False
            ),
        )
    )

    connection.commit()
    connection.close()

    write_json(
        TASK_DIR
        /
        (
            task["task_id"]
            +
            ".json"
        ),
        task
    )


def get_task_data(task_id):

    connection = db()

    row = connection.execute(
        """
        SELECT result
        FROM tasks
        WHERE task_id = ?
        """,
        (task_id,)
    ).fetchone()

    connection.close()

    if not row:
        return None

    try:
        return json.loads(
            row["result"]
        )
    except Exception:
        return None


# ============================================================
# INTENT + EXECUTION PLAN
# ============================================================

def classify_objective(objective):

    text = objective.lower()

    if any(
        word in text
        for word in [
            "build",
            "create",
            "develop",
            "implement",
            "code",
            "upgrade",
            "fix",
        ]
    ):
        return "build"

    if any(
        word in text
        for word in [
            "research",
            "analyze",
            "investigate",
            "study",
            "compare",
        ]
    ):
        return "research"

    if any(
        word in text
        for word in [
            "test",
            "verify",
            "validate",
            "audit",
        ]
    ):
        return "verification"

    return "general"


def build_execution_plan(objective):

    intent = classify_objective(
        objective
    )

    return {

        "plan_id":
            make_id("plan"),

        "objective":
            objective,

        "intent":
            intent,

        "steps": [

            {
                "id": "understand",
                "stage": "intent",
                "status": "planned",
            },

            {
                "id": "reason",
                "stage": "intelligence",
                "status": "planned",
            },

            {
                "id": "execute",
                "stage": "execution",
                "status": "planned",
            },

            {
                "id": "verify",
                "stage": "verification",
                "status": "planned",
            },

            {
                "id": "learn",
                "stage": "memory",
                "status": "planned",
            },

            {
                "id": "genome",
                "stage": "genome",
                "status": "planned",
            },
        ],
    }


# ============================================================
# AI ENGINE
# ============================================================

def call_huggingface(
    objective,
    context=""
):

    if not HF_TOKEN:

        return {

            "ok": False,

            "provider":
                "huggingface",

            "model":
                HF_MODEL,

            "error":
                "HF_TOKEN is not configured.",
        }

    system = """
You are the intelligence engine inside AI Infinity.

Your job is to produce concrete, useful work.

Always:
- separate facts from assumptions;
- avoid invented evidence;
- identify uncertainty;
- propose executable steps;
- consider failure modes;
- provide verification criteria;
- never claim a simulation proves reality;
- never claim AGI or ASI merely from architecture.

When the user asks to build software, provide an
implementation-oriented result.
"""

    user = f"""
OBJECTIVE:

{objective}

SYSTEM CONTEXT:

{context}

Return:

1. Understanding
2. Concrete solution
3. Implementation steps
4. Expected output
5. Verification criteria
6. Failure modes
7. Reusable knowledge
"""

    payload = {

        "model":
            HF_MODEL,

        "messages": [

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
        ],

        "temperature":
            0.2,

        "max_tokens":
            2200,
    }

    try:

        response = requests.post(

            HF_URL,

            headers={
                "Authorization":
                    "Bearer "
                    +
                    HF_TOKEN,

                "Content-Type":
                    "application/json",
            },

            json=payload,

            timeout=
                REQUEST_TIMEOUT
        )

        if response.status_code >= 400:

            return {

                "ok":
                    False,

                "provider":
                    "huggingface",

                "model":
                    HF_MODEL,

                "error":
                    (
                        f"HTTP "
                        f"{response.status_code}: "
                        f"{response.text[:3000]}"
                    ),
            }

        data = response.json()

        choices = data.get(
            "choices",
            []
        )

        if not choices:

            return {

                "ok":
                    False,

                "provider":
                    "huggingface",

                "model":
                    HF_MODEL,

                "error":
                    "No choices returned.",
            }

        content = (
            choices[0]
            .get(
                "message",
                {}
            )
            .get(
                "content",
                ""
            )
        )

        if not content:

            return {

                "ok":
                    False,

                "provider":
                    "huggingface",

                "model":
                    HF_MODEL,

                "error":
                    "Empty AI response.",
            }

        return {

            "ok":
                True,

            "provider":
                "huggingface",

            "model":
                HF_MODEL,

            "text":
                content,

            "generated_at":
                now(),
        }

    except Exception as error:

        return {

            "ok":
                False,

            "provider":
                "huggingface",

            "model":
                HF_MODEL,

            "error":
                str(error),
        }


# ============================================================
# EXECUTION ENGINE
# ============================================================

class ExecuteRequest(BaseModel):

    task_id: str

    action: str = "create_artifact"

    filename: str = "result.md"

    content: str = ""

    approved: bool = False


def create_artifact(
    task_id,
    filename,
    content
):

    filename = safe_name(
        filename
    )

    encoded = content.encode(
        "utf-8"
    )

    if len(encoded) > MAX_ARTIFACT_BYTES:

        raise HTTPException(
            413,
            "Artifact exceeds maximum size."
        )

    task_folder = (
        ARTIFACT_DIR
        /
        safe_name(task_id)
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

    resolved_folder = (
        task_folder
        .resolve()
    )

    resolved_path = (
        path.resolve()
    )

    if (
        resolved_folder
        not in
        resolved_path.parents
    ):

        raise HTTPException(
            400,
            "Unsafe artifact path."
        )

    path.write_bytes(
        encoded
    )

    data = path.read_bytes()

    result = {

        "execution_id":
            make_id("exec"),

        "task_id":
            task_id,

        "action":
            "create_artifact",

        "status":
            "completed",

        "filename":
            filename,

        "path":
            str(path),

        "bytes":
            len(data),

        "sha256":
            sha256_bytes(data),

        "reversible":
            True,

        "external_side_effect":
            False,

        "created_at":
            now(),
    }

    connection = db()

    connection.execute(
        """
        INSERT INTO executions
        (
            execution_id,
            task_id,
            created_at,
            action,
            status,
            artifact_path,
            verification
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result["execution_id"],
            task_id,
            result["created_at"],
            result["action"],
            result["status"],
            result["path"],
            "",
        )
    )

    connection.commit()
    connection.close()

    audit(
        "execution_completed",
        result
    )

    return result


# ============================================================
# VERIFICATION ENGINE
# ============================================================

def verify_artifact(
    artifact
):

    path = Path(
        artifact["path"]
    )

    if not path.exists():

        return {

            "verified":
                False,

            "status":
                "failed",

            "reason":
                "Artifact does not exist.",
        }

    data = path.read_bytes()

    actual_hash = sha256_bytes(
        data
    )

    hash_match = (
        actual_hash
        ==
        artifact["sha256"]
    )

    syntax_valid = None

    if path.suffix == ".py":

        try:

            ast.parse(
                data.decode(
                    "utf-8"
                )
            )

            syntax_valid = True

        except Exception:

            syntax_valid = False

    verified = (
        hash_match
        and
        syntax_valid is not False
    )

    result = {

        "verified":
            verified,

        "status":
            (
                "passed"
                if verified
                else
                "failed"
            ),

        "checks": {

            "exists":
                path.exists(),

            "hash_match":
                hash_match,

            "size_valid":
                len(data)
                <=
                MAX_ARTIFACT_BYTES,

            "python_syntax":
                syntax_valid,
        },

        "sha256":
            actual_hash,

        "verified_at":
            now(),
    }

    audit(
        "verification_completed",
        result
    )

    return result


# ============================================================
# MEMORY ENGINE
# ============================================================

def save_memory(
    task_id,
    objective,
    result,
    verification
):

    memory_id = make_id(
        "memory"
    )

    summary = (
        result[:10000]
        if result
        else ""
    )

    memory_hash = sha256_text(
        objective
        +
        summary
    )

    confidence = 0.5

    if verification.get(
        "verified"
    ):
        confidence = 0.9

    memory = {

        "memory_id":
            memory_id,

        "task_id":
            task_id,

        "created_at":
            now(),

        "objective":
            objective,

        "summary":
            summary,

        "confidence":
            confidence,

        "verification":
            verification,

        "memory_hash":
            memory_hash,

        "type":
            "execution_experience",

        "reusable":
            True,
    }

    write_json(

        MEMORY_DIR
        /
        (
            memory_id
            +
            ".json"
        ),

        memory
    )

    connection = db()

    connection.execute(
        """
        INSERT INTO memories
        (
            memory_id,
            created_at,
            task_id,
            objective,
            summary,
            confidence,
            memory_hash
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            memory_id,
            memory["created_at"],
            task_id,
            objective,
            summary,
            confidence,
            memory_hash,
        )
    )

    connection.commit()
    connection.close()

    audit(
        "memory_saved",
        memory
    )

    return memory


def get_memories(limit=20):

    limit = max(
        1,
        min(
            int(limit),
            MAX_MEMORY_ITEMS
        )
    )

    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM memories
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,)
    ).fetchall()

    connection.close()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# INTELLIGENCE GENOME
# ============================================================

def create_genome(
    task,
    intelligence,
    execution,
    verification,
    memory
):

    genome_id = make_id(
        "genome"
    )

    genome = {

        "genome_id":
            genome_id,

        "created_at":
            now(),

        "task_id":
            task["task_id"],

        "objective":
            task["objective"],

        "intent":
            task.get(
                "plan",
                {}
            ).get(
                "intent"
            ),

        "capabilities": [

            "intent_analysis",

            "ai_reasoning",

            "execution",

            "artifact_generation",

            "verification",

            "memory",

            "failure_recovery",

        ],

        "reasoning": {

            "provider":
                intelligence.get(
                    "provider"
                ),

            "model":
                intelligence.get(
                    "model"
                ),

            "output_hash":
                sha256_text(
                    intelligence.get(
                        "text",
                        ""
                    )
                ),
        },

        "execution": execution,

        "verification": verification,

        "memory": {

            "memory_id":
                memory.get(
                    "memory_id"
                )
                if memory
                else None,

            "confidence":
                memory.get(
                    "confidence"
                )
                if memory
                else None,
        },

        "failure_modes": [

            "provider_failure",

            "execution_failure",

            "verification_failure",

            "storage_failure",

            "authorization_failure",

        ],

        "recovery_policy": {

            "provider_failure":
                "retry or fallback",

            "execution_failure":
                "diagnose then retry safely",

            "verification_failure":
                "do not mark success",

            "storage_failure":
                "preserve task state",

            "authorization_failure":
                "stop and request approval",

        },

        "fitness": {

            "execution":
                (
                    1.0
                    if execution
                    and
                    execution.get(
                        "status"
                    )
                    == "completed"
                    else
                    0.0
                ),

            "verification":
                (
                    1.0
                    if verification.get(
                        "verified"
                    )
                    else
                    0.0
                ),
        },

        "reusable":
            bool(
                verification.get(
                    "verified"
                )
            ),
    }

    canonical = json.dumps(
        genome,
        sort_keys=True,
        ensure_ascii=False
    )

    genome[
        "genome_hash"
    ] = sha256_text(
        canonical
    )

    write_json(

        GENOME_DIR
        /
        (
            genome_id
            +
            ".json"
        ),

        genome
    )

    connection = db()

    connection.execute(
        """
        INSERT INTO genomes
        (
            genome_id,
            created_at,
            task_id,
            objective,
            genome_hash,
            performance
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            genome_id,
            genome["created_at"],
            task["task_id"],
            task["objective"],
            genome["genome_hash"],
            json.dumps(
                genome["fitness"]
            ),
        )
    )

    connection.commit()
    connection.close()

    audit(
        "genome_created",
        genome
    )

    return genome


def get_genomes(limit=20):

    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM genomes
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (
            max(
                1,
                min(
                    int(limit),
                    100
                )
            ),
        )
    ).fetchall()

    connection.close()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# SELF-HEALING ENGINE
# ============================================================

def diagnose_failure(
    stage,
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

        recovery = [
            "retry",
            "use backup provider",
            "use local fallback",
        ]

    elif (
        "401" in text
        or
        "403" in text
    ):

        recovery = [
            "stop",
            "check credentials",
            "check authorization",
        ]

    elif "syntax" in text:

        recovery = [
            "capture invalid artifact",
            "repair source",
            "verify syntax again",
        ]

    elif "memory" in text:

        recovery = [
            "preserve task state",
            "reduce context",
            "retry with smaller payload",
        ]

    else:

        recovery = [
            "capture failure",
            "isolate failed stage",
            "retry safe operation",
            "rollback if required",
        ]

    return {

        "failure_stage":
            stage,

        "error":
            str(error)[:4000],

        "recovery_plan":
            recovery,

        "automatic_external_change":
            False,

        "diagnosed_at":
            now(),
    }


def record_failure(
    task_id,
    stage,
    error
):

    recovery = diagnose_failure(
        stage,
        error
    )

    failure_id = make_id(
        "failure"
    )

    record = {

        "failure_id":
            failure_id,

        "task_id":
            task_id,

        "created_at":
            now(),

        "stage":
            stage,

        "error":
            str(error),

        "recovery":
            recovery,
    }

    write_json(

        AUDIT_DIR
        /
        (
            failure_id
            +
            ".json"
        ),

        record
    )

    connection = db()

    connection.execute(
        """
        INSERT INTO failures
        (
            failure_id,
            task_id,
            created_at,
            stage,
            error,
            recovery
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            failure_id,
            task_id,
            record["created_at"],
            stage,
            str(error)[:10000],
            json.dumps(
                recovery
            ),
        )
    )

    connection.commit()
    connection.close()

    audit(
        "self_healing_failure",
        record
    )

    return record


# ============================================================
# MASTER INFINITY EXECUTOR
# ============================================================

class RunRequest(BaseModel):

    objective: str = Field(
        min_length=1,
        max_length=12000
    )

    execute: bool = True

    remember: bool = True


def run_infinity(
    request
):

    task_id = make_id(
        "task"
    )

    task = {

        "task_id":
            task_id,

        "created_at":
            now(),

        "objective":
            request.objective,

        "status":
            "running",

        "version":
            VERSION,
    }

    save_task(
        task
    )

    audit(
        "task_started",
        {
            "task_id":
                task_id,

            "objective":
                request.objective,
        }
    )

    try:

        # ----------------------------------------------------
        # PHASE 1: PLAN
        # ----------------------------------------------------

        plan = build_execution_plan(
            request.objective
        )

        task[
            "plan"
        ] = plan

        save_task(
            task
        )

        # ----------------------------------------------------
        # PHASE 2: MEMORY CONTEXT
        # ----------------------------------------------------

        memories = get_memories(
            10
        )

        memory_context = "\n".join(

            (
                "- "
                +
                item["objective"]
                +
                ": "
                +
                item["summary"][:1000]
            )

            for item in memories
        )

        # ----------------------------------------------------
        # PHASE 3: AI REASONING
        # ----------------------------------------------------

        intelligence = call_huggingface(

            request.objective,

            context=memory_context
        )

        if not intelligence.get(
            "ok"
        ):

            raise RuntimeError(
                intelligence.get(
                    "error",
                    "AI generation failed."
                )
            )

        task[
            "intelligence"
        ] = intelligence

        save_task(
            task
        )

        # ----------------------------------------------------
        # PHASE 4: EXECUTION
        # ----------------------------------------------------

        execution = None

        if request.execute:

            execution = create_artifact(

                task_id,

                "intelligence-result.md",

                intelligence.get(
                    "text",
                    ""
                )
            )

        task[
            "execution"
        ] = execution

        save_task(
            task
        )

        # ----------------------------------------------------
        # PHASE 5: VERIFICATION
        # ----------------------------------------------------

        verification = {

            "verified":
                False,

            "status":
                "skipped",

        }

        if execution:

            verification = verify_artifact(
                execution
            )

        task[
            "verification"
        ] = verification

        save_task(
            task
        )

        # ----------------------------------------------------
        # PHASE 6: MEMORY
        # ----------------------------------------------------

        memory = None

        if request.remember:

            memory = save_memory(

                task_id,

                request.objective,

                intelligence.get(
                    "text",
                    ""
                ),

                verification
            )

        task[
            "memory"
        ] = memory

        save_task(
            task
        )

        # ----------------------------------------------------
        # PHASE 7: GENOME
        # ----------------------------------------------------

        genome = create_genome(

            task,

            intelligence,

            execution,

            verification,

            memory
        )

        task[
            "genome"
        ] = genome

        # ----------------------------------------------------
        # FINAL
        # ----------------------------------------------------

        task[
            "status"
        ] = "completed"

        task[
            "completed_at"
        ] = now()

        task[
            "governance"
        ] = {

            "zero_dollar":
                ZERO_DOLLAR_MODE,

            "arbitrary_execution":
                ALLOW_ARBITRARY_EXECUTION,

            "external_side_effects":
                False,

            "human_authorization":
                "required for consequential external actions",

        }

        save_task(
            task
        )

        audit(
            "task_completed",
            {
                "task_id":
                    task_id,

                "verified":
                    verification.get(
                        "verified"
                    ),

                "genome":
                    genome.get(
                        "genome_id"
                    ),
            }
        )

        return task

    except Exception as error:

        failure = record_failure(

            task_id,

            "pipeline",

            error
        )

        task[
            "status"
        ] = "error"

        task[
            "error"
        ] = str(error)

        task[
            "self_healing"
        ] = failure

        task[
            "failed_at"
        ] = now()

        save_task(
            task
        )

        return task


# ============================================================
# API ENDPOINTS
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

        "execution_engine":
            True,

        "verification":
            True,

        "memory":
            True,

        "genome":
            True,

        "self_healing":
            True,

        "zero_dollar":
            ZERO_DOLLAR_MODE,
    }


@app.get(
    "/v1/status"
)
def status():

    connection = db()

    task_count = connection.execute(
        "SELECT COUNT(*) AS n FROM tasks"
    ).fetchone()["n"]

    memory_count = connection.execute(
        "SELECT COUNT(*) AS n FROM memories"
    ).fetchone()["n"]

    genome_count = connection.execute(
        "SELECT COUNT(*) AS n FROM genomes"
    ).fetchone()["n"]

    execution_count = connection.execute(
        "SELECT COUNT(*) AS n FROM executions"
    ).fetchone()["n"]

    failure_count = connection.execute(
        "SELECT COUNT(*) AS n FROM failures"
    ).fetchone()["n"]

    connection.close()

    return {

        "service":
            APP_NAME,

        "version":
            VERSION,

        "architecture":
            [

                "intent",

                "planning",

                "ai_reasoning",

                "execution_engine",

                "verification_engine",

                "memory_engine",

                "intelligence_genome",

                "self_healing",

            ],

        "counts": {

            "tasks":
                task_count,

            "memories":
                memory_count,

            "genomes":
                genome_count,

            "executions":
                execution_count,

            "failures":
                failure_count,
        },

        "governance": {

            "zero_dollar":
                ZERO_DOLLAR_MODE,

            "arbitrary_execution":
                ALLOW_ARBITRARY_EXECUTION,

            "external_writes":
                False,

            "automatic_spending":
                False,
        },
    }


@app.post(
    "/v1/run"
)
def run_endpoint(
    request: RunRequest
):

    return run_infinity(
        request
    )


@app.post(
    "/v1/execute"
)
def execute_endpoint(
    request: ExecuteRequest
):

    if request.action != "create_artifact":

        if not ALLOW_ARBITRARY_EXECUTION:

            raise HTTPException(

                403,

                (
                    "Arbitrary execution is disabled. "
                    "Only safe artifact creation is enabled."
                )
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


@app.get(
    "/v1/tasks/{task_id}"
)
def task_endpoint(
    task_id: str
):

    task = get_task_data(
        task_id
    )

    if not task:

        raise HTTPException(
            404,
            "Task not found."
        )

    return task


@app.get(
    "/v1/memory"
)
def memory_endpoint(
    limit: int = 20
):

    return {

        "memories":
            get_memories(
                limit
            )
    }


@app.get(
    "/v1/genomes"
)
def genome_endpoint(
    limit: int = 20
):

    return {

        "genomes":
            get_genomes(
                limit
            )
    }


@app.get(
    "/v1/failures"
)
def failures_endpoint(
    limit: int = 20
):

    connection = db()

    rows = connection.execute(
        """
        SELECT *
        FROM failures
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (
            max(
                1,
                min(
                    int(limit),
                    100
                )
            ),
        )
    ).fetchall()

    connection.close()

    return {

        "failures":
            [
                dict(row)
                for row in rows
            ]
    }


@app.get(
    "/v1/audit"
)
def audit_endpoint():

    files = sorted(

        AUDIT_DIR.glob(
            "*.json"
        ),

        key=lambda p:
            p.stat().st_mtime,

        reverse=True
    )

    events = []

    for path in files[:100]:

        item = read_json(
            path
        )

        if item:
            events.append(
                item
            )

    return {

        "events":
            events
    }


# ============================================================
# WEB UI
# ============================================================

HTML = """
<!DOCTYPE html>

<html>

<head>

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>AI Infinity</title>

<style>

body{
    margin:0;
    background:#08090d;
    color:#f5f5f5;
    font-family:Arial,sans-serif;
}

.wrap{
    max-width:950px;
    margin:auto;
    padding:25px 16px 50px;
}

h1{
    font-size:42px;
    margin:0 0 8px;
}

.sub{
    color:#9da4b3;
    margin-bottom:22px;
}

.card{
    background:#11141b;
    border:1px solid #282e39;
    border-radius:18px;
    padding:20px;
    margin-bottom:18px;
}

textarea{
    width:100%;
    min-height:160px;
    box-sizing:border-box;
    background:#080a0f;
    color:#fff;
    border:1px solid #303744;
    border-radius:12px;
    padding:14px;
    font-size:16px;
}

button{
    border:0;
    border-radius:10px;
    padding:13px 18px;
    margin-top:10px;
    margin-right:7px;
    font-weight:bold;
    cursor:pointer;
}

pre{
    white-space:pre-wrap;
    background:#080a0f;
    border:1px solid #282e39;
    border-radius:12px;
    padding:15px;
    overflow:auto;
}

.badge{
    display:inline-block;
    padding:7px 10px;
    border-radius:20px;
    background:#202631;
    margin:4px;
    font-size:13px;
}

</style>

</head>

<body>

<div class="wrap">

<h1>∞ AI Infinity</h1>

<div class="sub">
Execution → Verification → Memory →
Intelligence Genome → Self-Healing
</div>

<div class="card">

<textarea
id="objective"
placeholder="Give AI Infinity a real objective..."></textarea>

<br>

<button onclick="runAI()">
Run AI Infinity
</button>

<button onclick="checkStatus()">
System Status
</button>

</div>

<div class="card">

<pre id="output">
Ready.
</pre>

</div>

<div class="card">

<b>Integrated Layers</b>

<br><br>

<span class="badge">Intent</span>
<span class="badge">Planning</span>
<span class="badge">AI Reasoning</span>
<span class="badge">Execution</span>
<span class="badge">Verification</span>
<span class="badge">Memory</span>
<span class="badge">Genome</span>
<span class="badge">Self-Healing</span>
<span class="badge">$0 Governor</span>

</div>

</div>

<script>

async function runAI(){

    const objective =
        document
        .getElementById("objective")
        .value;

    if(!objective.trim()){
        alert("Enter an objective.");
        return;
    }

    document
    .getElementById("output")
    .innerText =
        "AI Infinity is executing...";

    try{

        const response =
            await fetch(
                "/v1/run",
                {
                    method:"POST",

                    headers:{
                        "Content-Type":
                            "application/json"
                    },

                    body:
                        JSON.stringify({
                            objective:
                                objective,

                            execute:
                                true,

                            remember:
                                true
                        })
                }
            );

        const data =
            await response.json();

        document
        .getElementById("output")
        .innerText =
            JSON.stringify(
                data,
                null,
                2
            );

    }catch(error){

        document
        .getElementById("output")
        .innerText =
            String(error);
    }
}


async function checkStatus(){

    const response =
        await fetch(
            "/v1/status"
        );

    const data =
        await response.json();

    document
    .getElementById("output")
    .innerText =
        JSON.stringify(
            data,
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

    initialize_db()

    audit(
        "system_started",
        {
            "version":
                VERSION,

            "zero_dollar":
                ZERO_DOLLAR_MODE,

            "arbitrary_execution":
                ALLOW_ARBITRARY_EXECUTION,

            "hf_configured":
                bool(HF_TOKEN),

            "hf_model":
                HF_MODEL,
        }
    )
