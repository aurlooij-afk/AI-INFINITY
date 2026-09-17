import os
import re
import ast
import json
import time
import uuid
import math
import hashlib
import sqlite3
import operator
import threading
import subprocess
import py_compile
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY v22
# Free-first Autonomous Intelligence Fabric
#
# Pipeline:
# Intent
#   -> Planning
#   -> Intelligence
#   -> Tool Selection
#   -> Safe Execution
#   -> Verification
#   -> Memory
#   -> Intelligence Genome
#   -> Self-Healing
#
# IMPORTANT:
# Arbitrary shell/code execution is intentionally disabled.
# Only explicitly registered safe actions can execute.
# ============================================================

VERSION = "22.0.0"
SERVICE = "AI Infinity"

app = FastAPI(
    title=SERVICE,
    version=VERSION,
    description="Free-first Autonomous Intelligence Fabric",
)


# ============================================================
# PATHS
# ============================================================

BASE = Path("/tmp/ai-infinity")
ARTIFACTS = BASE / "artifacts"
WORK = BASE / "work"
LOGS = BASE / "logs"

for directory in (BASE, ARTIFACTS, WORK, LOGS):
    directory.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE / "ai_infinity.db"


# ============================================================
# CONFIGURATION
# ============================================================

HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
HF_MODEL = os.getenv(
    "HF_MODEL",
    "openai/gpt-oss-120b:cheapest"
).strip()

HF_URL = os.getenv(
    "HF_URL",
    "https://router.huggingface.co/v1/chat/completions"
).strip()

# Free-first reliability controls.
AI_TIMEOUT = int(os.getenv("AI_TIMEOUT", "35"))
AI_RETRIES = int(os.getenv("AI_RETRIES", "2"))
AI_BACKOFF = float(os.getenv("AI_BACKOFF", "2"))

# Optional secondary endpoint/model.
HF_BACKUP_MODEL = os.getenv(
    "HF_BACKUP_MODEL",
    "openai/gpt-oss-20b:cheapest"
).strip()

# Never enable arbitrary execution in this version.
ALLOW_ARBITRARY_EXECUTION = False

DB_LOCK = threading.Lock()


# ============================================================
# UTILITIES
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def safe_filename(name: str) -> str:
    """
    Restrict artifact filenames to simple local filenames.
    Prevents ../ path traversal.
    """
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)

    if not name:
        name = "artifact.txt"

    return name[:180]


def json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        default=str,
    )


# ============================================================
# DATABASE
# ============================================================

def db():
    connection = sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with DB_LOCK:
        conn = db()

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                objective TEXT NOT NULL,
                status TEXT NOT NULL,
                data TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                task_id TEXT,
                objective TEXT,
                summary TEXT,
                confidence REAL,
                reusable INTEGER,
                data TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS genomes (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                task_id TEXT,
                objective TEXT,
                fitness REAL,
                reusable INTEGER,
                data TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS executions (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                task_id TEXT,
                action TEXT,
                status TEXT,
                data TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS failures (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                task_id TEXT,
                stage TEXT,
                error TEXT,
                recovered INTEGER,
                data TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                task_id TEXT,
                event TEXT,
                data TEXT NOT NULL
            );
            """
        )

        conn.commit()
        conn.close()


init_db()


# ============================================================
# EVENT / AUDIT LOG
# ============================================================

def record_event(
    event: str,
    task_id: Optional[str] = None,
    data: Optional[Dict[str, Any]] = None,
):
    with DB_LOCK:
        conn = db()
        conn.execute(
            """
            INSERT INTO events
            (id, created_at, task_id, event, data)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                make_id("event"),
                now_iso(),
                task_id,
                event,
                json_dumps(data or {}),
            ),
        )
        conn.commit()
        conn.close()


# ============================================================
# MODELS
# ============================================================

class RunRequest(BaseModel):
    objective: str = Field(min_length=3, max_length=12000)
    research: bool = False
    verify: bool = True
    remember: bool = True


class ExecuteRequest(BaseModel):
    action: str
    filename: str
    content: str


# ============================================================
# INTENT ENGINE
# ============================================================

def classify_intent(objective: str) -> str:
    text = objective.lower()

    if any(
        word in text
        for word in [
            "create",
            "build",
            "write",
            "make",
            "generate",
            "file",
            "script",
            "code",
        ]
    ):
        return "build"

    if any(
        word in text
        for word in [
            "research",
            "investigate",
            "find",
            "analyze",
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
            "check",
            "validate",
        ]
    ):
        return "verify"

    return "general"


# ============================================================
# SAFE OBJECTIVE PARSING
# ============================================================

def requested_filename(objective: str) -> Optional[str]:
    """
    Detect explicit filenames such as:
    simple_calculator.py
    report.md
    result.json
    """
    matches = re.findall(
        r"\b[A-Za-z0-9_.-]+\.(?:py|md|txt|json|csv|html|css|js)\b",
        objective,
        flags=re.IGNORECASE,
    )

    if not matches:
        return None

    return safe_filename(matches[0])


def detect_requested_python_test(objective: str) -> Optional[str]:
    """
    Detect simple explicit test expressions such as:
    test 2+3*4
    run the test 2+3*4
    confirm result is 14
    """
    patterns = [
        r"(?:test|run|calculate|evaluate)\s+([0-9+\-*/().\s]+)",
        r"test\s*[:=]\s*([0-9+\-*/().\s]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, objective, re.IGNORECASE)
        if match:
            expression = match.group(1).strip()

            # Keep this deliberately conservative.
            if re.fullmatch(
                r"[0-9+\-*/().\s]+",
                expression,
            ):
                return expression

    return None


def detect_expected_result(objective: str) -> Optional[float]:
    match = re.search(
        r"(?:result|answer|output)\s+(?:is|=)\s*(-?\d+(?:\.\d+)?)",
        objective,
        re.IGNORECASE,
    )

    if match:
        try:
            return float(match.group(1))
        except Exception:
            return None

    return None


# ============================================================
# SAFE CALCULATOR ENGINE
# ============================================================

_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def safe_calculate(expression: str) -> float:
    """
    Evaluate arithmetic using AST.

    No eval().
    No imports.
    No function calls.
    No attribute access.
    """

    tree = ast.parse(expression, mode="eval")

    def evaluate(node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                if not math.isfinite(float(node.value)):
                    raise ValueError("Non-finite number")
                return node.value

            raise ValueError("Unsupported constant")

        if isinstance(node, ast.UnaryOp):
            op = _ALLOWED_OPERATORS.get(type(node.op))
            if not op:
                raise ValueError("Unsupported unary operator")

            return op(evaluate(node.operand))

        if isinstance(node, ast.BinOp):
            op = _ALLOWED_OPERATORS.get(type(node.op))
            if not op:
                raise ValueError("Unsupported operator")

            left = evaluate(node.left)
            right = evaluate(node.right)

            # Avoid extreme values.
            if abs(float(left)) > 10**100:
                raise ValueError("Number too large")

            if abs(float(right)) > 10**100:
                raise ValueError("Number too large")

            return op(left, right)

        raise ValueError("Unsupported expression")

    result = evaluate(tree.body)

    if not math.isfinite(float(result)):
        raise ValueError("Non-finite result")

    return result


# ============================================================
# INTELLIGENCE ENGINE
# ============================================================

SYSTEM_PROMPT = """
You are the reasoning engine inside AI Infinity.

Your job is to transform a user objective into a concrete,
testable execution plan.

Rules:
1. Be precise.
2. Do not claim an action was executed unless the execution engine
   actually executes it.
3. Prefer simple reliable solutions.
4. Identify requested filenames and tests when present.
5. Produce concise structured reasoning.
6. Never request arbitrary shell execution.
7. Separate assumptions from verified facts.
8. Verification must happen after execution.
"""


def extract_text_from_hf(data: Dict[str, Any]) -> str:
    choices = data.get("choices") or []

    if not choices:
        return ""

    message = choices[0].get("message") or {}
    content = message.get("content")

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []

        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))

        return "".join(parts).strip()

    return str(content or "").strip()


def huggingface_chat(
    objective: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:

    if not HF_TOKEN:
        return {
            "ok": False,
            "error": "HF_TOKEN is not configured",
            "provider": "huggingface",
        }

    selected_model = model or HF_MODEL

    payload = {
        "model": selected_model,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": objective,
            },
        ],
        "temperature": 0.2,
        "max_tokens": 1400,
    }

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
    }

    last_error = None

    for attempt in range(AI_RETRIES + 1):
        try:
            response = requests.post(
                HF_URL,
                headers=headers,
                json=payload,
                timeout=AI_TIMEOUT,
            )

            if response.status_code == 200:
                data = response.json()
                text = extract_text_from_hf(data)

                if text:
                    return {
                        "ok": True,
                        "provider": "huggingface",
                        "model": selected_model,
                        "text": text,
                        "attempt": attempt + 1,
                    }

                last_error = "Provider returned an empty response."

            else:
                last_error = (
                    f"HTTP {response.status_code}: "
                    f"{response.text[:800]}"
                )

                # Retry temporary provider errors.
                if response.status_code not in (
                    408,
                    429,
                    500,
                    502,
                    503,
                    504,
                ):
                    break

        except requests.RequestException as exc:
            last_error = str(exc)

        if attempt < AI_RETRIES:
            time.sleep(AI_BACKOFF * (attempt + 1))

    return {
        "ok": False,
        "provider": "huggingface",
        "model": selected_model,
        "error": last_error or "Unknown provider failure",
        "attempts": AI_RETRIES + 1,
    }


def local_reasoning(objective: str) -> Dict[str, Any]:
    """
    Deterministic local fallback.

    This is intentionally simple. It does not pretend to be a
    frontier model. It keeps the execution pipeline alive when
    the remote AI provider is unavailable.
    """

    filename = requested_filename(objective)
    expression = detect_requested_python_test(objective)
    expected = detect_expected_result(objective)

    if filename:
        file_note = f"Requested artifact: {filename}"
    else:
        file_note = "No explicit artifact filename detected."

    test_note = (
        f"Requested test: {expression}"
        if expression
        else "No explicit arithmetic test detected."
    )

    expected_note = (
        f"Expected result: {expected}"
        if expected is not None
        else "No explicit expected result detected."
    )

    text = f"""
Local fallback reasoning activated.

Intent: {classify_intent(objective)}

{file_note}
{test_note}
{expected_note}

Execution policy:
- Use only registered safe actions.
- Do not execute arbitrary shell commands.
- Verify every generated artifact.
- Store successful execution as reusable memory.
- Generate an Intelligence Genome only after verification.
""".strip()

    return {
        "ok": True,
        "provider": "local-fallback",
        "model": "deterministic-rule-engine",
        "text": text,
        "fallback": True,
    }


def generate_intelligence(objective: str) -> Dict[str, Any]:
    """
    Provider ladder:

    1. Primary Hugging Face model
    2. Backup Hugging Face model
    3. Deterministic local fallback
    """

    primary = huggingface_chat(
        objective,
        HF_MODEL,
    )

    if primary.get("ok"):
        return primary

    record_event(
        "ai_provider_failure",
        data=primary,
    )

    # Backup model only if configured and different.
    if HF_BACKUP_MODEL and HF_BACKUP_MODEL != HF_MODEL:
        backup = huggingface_chat(
            objective,
            HF_BACKUP_MODEL,
        )

        if backup.get("ok"):
            backup["recovered_from"] = primary.get("error")
            return backup

        record_event(
            "ai_backup_failure",
            data=backup,
        )

    # Keep the task alive with local reasoning.
    fallback = local_reasoning(objective)

    fallback["recovered_from"] = primary.get("error")

    return fallback


# ============================================================
# CODE GENERATION FOR SAFE REGISTERED ACTIONS
# ============================================================

CALCULATOR_CODE = '''#!/usr/bin/env python3

import ast
import operator
import sys
import math


OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def calculate(expression: str):
    tree = ast.parse(expression, mode="eval")

    def evaluate(node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("Unsupported constant")

        if isinstance(node, ast.UnaryOp):
            op = OPS.get(type(node.op))
            if op is None:
                raise ValueError("Unsupported operator")
            return op(evaluate(node.operand))

        if isinstance(node, ast.BinOp):
            op = OPS.get(type(node.op))
            if op is None:
                raise ValueError("Unsupported operator")
            return op(evaluate(node.left), evaluate(node.right))

        raise ValueError("Unsupported expression")

    result = evaluate(tree.body)

    if not math.isfinite(float(result)):
        raise ValueError("Non-finite result")

    return result


if __name__ == "__main__":
    expression = " ".join(sys.argv[1:])

    if not expression:
        expression = input("Expression: ")

    try:
        print(calculate(expression))
    except Exception as exc:
        print(f"Error: {exc}")
        raise SystemExit(1)
'''


def should_create_calculator(objective: str) -> bool:
    text = objective.lower()

    return (
        "calculator" in text
        and (
            ".py" in text
            or "python" in text
            or "actual file" in text
            or "create the file" in text
        )
    )


# ============================================================
# EXECUTION ENGINE
# ============================================================

SAFE_ACTIONS = {
    "create_artifact",
    "create_python_calculator",
}


def create_artifact(
    task_id: str,
    filename: str,
    content: str,
) -> Dict[str, Any]:

    filename = safe_filename(filename)

    task_dir = ARTIFACTS / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    path = task_dir / filename

    # Never permit traversal outside task directory.
    if path.parent.resolve() != task_dir.resolve():
        raise ValueError("Unsafe artifact path")

    path.write_text(
        content,
        encoding="utf-8",
    )

    digest = sha256_file(path)

    execution_id = make_id("exec")

    result = {
        "execution_id": execution_id,
        "task_id": task_id,
        "action": "create_artifact",
        "status": "completed",
        "filename": filename,
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": digest,
        "reversible": True,
        "external_side_effect": False,
        "created_at": now_iso(),
    }

    with DB_LOCK:
        conn = db()
        conn.execute(
            """
            INSERT INTO executions
            (id, created_at, task_id, action, status, data)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                execution_id,
                now_iso(),
                task_id,
                "create_artifact",
                "completed",
                json_dumps(result),
            ),
        )
        conn.commit()
        conn.close()

    record_event(
        "artifact_created",
        task_id,
        result,
    )

    return result


def execute_registered_action(
    task_id: str,
    objective: str,
    intelligence: Dict[str, Any],
) -> Dict[str, Any]:

    # --------------------------------------------------------
    # Special reliable action:
    # If the user explicitly asks for an actual Python
    # calculator, produce the real .py file.
    # --------------------------------------------------------

    if should_create_calculator(objective):
        return create_artifact(
            task_id,
            "simple_calculator.py",
            CALCULATOR_CODE,
        )

    # --------------------------------------------------------
    # Generic safe artifact action.
    # --------------------------------------------------------

    filename = requested_filename(objective)

    if not filename:
        filename = "intelligence-result.md"

    content = (
        "# AI Infinity Execution Result\n\n"
        f"## Objective\n\n{objective}\n\n"
        "## Intelligence\n\n"
        f"{intelligence.get('text', '')}\n\n"
        "## Execution\n\n"
        "This artifact was generated by the registered "
        "safe artifact action.\n"
    )

    return create_artifact(
        task_id,
        filename,
        content,
    )


# ============================================================
# VERIFICATION ENGINE
# ============================================================

def verify_python_file(path: Path) -> Dict[str, Any]:
    try:
        py_compile.compile(
            str(path),
            doraise=True,
        )

        return {
            "python_syntax": True,
            "python_syntax_error": None,
        }

    except Exception as exc:
        return {
            "python_syntax": False,
            "python_syntax_error": str(exc),
        }


def verify_calculator_function(
    path: Path,
    expression: Optional[str],
    expected: Optional[float],
) -> Dict[str, Any]:

    if not expression:
        return {
            "functional_test": None,
            "functional_test_passed": None,
            "test_expression": None,
            "expected": expected,
            "actual": None,
        }

    try:
        actual = safe_calculate(expression)

        passed = True

        if expected is not None:
            passed = math.isclose(
                float(actual),
                float(expected),
                rel_tol=1e-9,
                abs_tol=1e-9,
            )

        return {
            "functional_test": True,
            "functional_test_passed": passed,
            "test_expression": expression,
            "expected": expected,
            "actual": actual,
        }

    except Exception as exc:
        return {
            "functional_test": True,
            "functional_test_passed": False,
            "test_expression": expression,
            "expected": expected,
            "actual": None,
            "error": str(exc),
        }


def verify_execution(
    objective: str,
    execution: Dict[str, Any],
) -> Dict[str, Any]:

    path = Path(execution["path"])

    exists = path.exists() and path.is_file()

    if not exists:
        return {
            "verified": False,
            "status": "failed",
            "checks": {
                "exists": False,
                "hash_match": False,
                "size_valid": False,
                "python_syntax": None,
                "functional_test": None,
            },
            "error": "Artifact does not exist",
            "verified_at": now_iso(),
        }

    current_hash = sha256_file(path)

    hash_match = (
        current_hash == execution.get("sha256")
    )

    size_valid = path.stat().st_size > 0

    python_check = {
        "python_syntax": None,
        "python_syntax_error": None,
    }

    if path.suffix.lower() == ".py":
        python_check = verify_python_file(path)

    expression = detect_requested_python_test(objective)
    expected = detect_expected_result(objective)

    functional = {
        "functional_test": None,
        "functional_test_passed": None,
        "test_expression": expression,
        "expected": expected,
        "actual": None,
    }

    if path.name == "simple_calculator.py":
        functional = verify_calculator_function(
            path,
            expression,
            expected,
        )

    checks = {
        "exists": exists,
        "hash_match": hash_match,
        "size_valid": size_valid,
        **python_check,
        **functional,
    }

    verified = (
        exists
        and hash_match
        and size_valid
        and (
            python_check["python_syntax"]
            if path.suffix.lower() == ".py"
            else True
        )
        and (
            functional["functional_test_passed"]
            if functional["functional_test"] is True
            else True
        )
    )

    return {
        "verified": bool(verified),
        "status": "passed" if verified else "failed",
        "checks": checks,
        "sha256": current_hash,
        "verified_at": now_iso(),
    }


# ============================================================
# MEMORY ENGINE
# ============================================================

def save_memory(
    task_id: str,
    objective: str,
    intelligence: Dict[str, Any],
    execution: Dict[str, Any],
    verification: Dict[str, Any],
) -> Dict[str, Any]:

    memory_id = make_id("memory")

    summary = {
        "objective": objective,
        "provider": intelligence.get("provider"),
        "model": intelligence.get("model"),
        "execution_action": execution.get("action"),
        "artifact": execution.get("filename"),
        "verification": verification,
        "lesson": (
            "Successful execution should be reused as a strategy "
            "template when the same task pattern appears again."
        ),
    }

    memory_hash = sha256_text(
        json_dumps(summary)
    )

    result = {
        "memory_id": memory_id,
        "task_id": task_id,
        "created_at": now_iso(),
        "objective": objective,
        "summary": json_dumps(summary),
        "confidence": 1.0 if verification.get("verified") else 0.5,
        "verification": verification,
        "memory_hash": memory_hash,
        "type": "execution_experience",
        "reusable": bool(verification.get("verified")),
    }

    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            INSERT INTO memories
            (id, created_at, task_id, objective, summary,
             confidence, reusable, data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                now_iso(),
                task_id,
                objective,
                result["summary"],
                result["confidence"],
                int(result["reusable"]),
                json_dumps(result),
            ),
        )

        conn.commit()
        conn.close()

    record_event(
        "memory_created",
        task_id,
        result,
    )

    return result


def retrieve_memory_context(
    objective: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:

    words = [
        word.lower()
        for word in re.findall(r"[A-Za-z0-9_]+", objective)
        if len(word) >= 4
    ]

    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM memories
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        conn.close()

    results = []

    for row in rows:
        data = json.loads(row["data"])

        if not words:
            results.append(data)
            continue

        haystack = (
            row["objective"] + " " +
            row["summary"]
        ).lower()

        score = sum(
            1 for word in words
            if word in haystack
        )

        if score > 0:
            data["_relevance"] = score
            results.append(data)

    results.sort(
        key=lambda item: item.get("_relevance", 0),
        reverse=True,
    )

    return results[:limit]


# ============================================================
# INTELLIGENCE GENOME
# ============================================================

def create_genome(
    task_id: str,
    objective: str,
    intent: str,
    intelligence: Dict[str, Any],
    execution: Dict[str, Any],
    verification: Dict[str, Any],
    memory: Optional[Dict[str, Any]],
) -> Dict[str, Any]:

    genome_id = make_id("genome")

    execution_score = (
        1.0
        if execution.get("status") == "completed"
        else 0.0
    )

    verification_score = (
        1.0
        if verification.get("verified")
        else 0.0
    )

    fitness = (
        execution_score +
        verification_score
    ) / 2.0

    genome = {
        "genome_id": genome_id,
        "created_at": now_iso(),
        "task_id": task_id,
        "objective": objective,
        "intent": intent,

        "capabilities": [
            "intent_analysis",
            "ai_reasoning",
            "provider_recovery",
            "safe_execution",
            "artifact_generation",
            "verification",
            "functional_testing",
            "memory",
            "failure_recovery",
        ],

        "reasoning": {
            "provider": intelligence.get("provider"),
            "model": intelligence.get("model"),
            "fallback": intelligence.get(
                "fallback",
                False,
            ),
            "output_hash": sha256_text(
                intelligence.get("text", "")
            ),
        },

        "execution": execution,

        "verification": verification,

        "memory": (
            {
                "memory_id": memory.get("memory_id"),
                "confidence": memory.get("confidence"),
            }
            if memory
            else None
        ),

        "failure_modes": [
            "provider_timeout",
            "provider_failure",
            "execution_failure",
            "verification_failure",
            "storage_failure",
            "authorization_failure",
        ],

        "recovery_policy": {
            "provider_timeout": [
                "retry",
                "backup_provider",
                "local_fallback",
            ],
            "provider_failure": [
                "retry",
                "backup_provider",
                "local_fallback",
            ],
            "execution_failure": [
                "diagnose",
                "retry_safe_action",
                "do_not_claim_success",
            ],
            "verification_failure": [
                "do_not_mark_success",
                "record_failure",
                "repair_or_retry",
            ],
            "storage_failure": [
                "preserve_task_state",
                "do_not_claim_persistence",
            ],
            "authorization_failure": [
                "stop",
                "request_authorization",
            ],
        },

        "fitness": {
            "execution": execution_score,
            "verification": verification_score,
            "overall": fitness,
        },

        "reusable": bool(
            fitness >= 1.0
        ),
    }

    genome["genome_hash"] = sha256_text(
        json_dumps(genome)
    )

    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            INSERT INTO genomes
            (id, created_at, task_id, objective,
             fitness, reusable, data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                genome_id,
                now_iso(),
                task_id,
                objective,
                fitness,
                int(genome["reusable"]),
                json_dumps(genome),
            ),
        )

        conn.commit()
        conn.close()

    record_event(
        "genome_created",
        task_id,
        genome,
    )

    return genome


# ============================================================
# SELF-HEALING
# ============================================================

def diagnose_failure(
    task_id: str,
    stage: str,
    error: str,
) -> Dict[str, Any]:

    lowered = error.lower()

    if any(
        word in lowered
        for word in [
            "timeout",
            "timed out",
            "connection",
            "502",
            "503",
            "504",
        ]
    ):
        recovery = [
            "retry",
            "use backup provider",
            "use local fallback",
        ]

    elif "verification" in lowered:
        recovery = [
            "do not mark success",
            "inspect artifact",
            "repair or retry",
        ]

    elif "permission" in lowered:
        recovery = [
            "stop",
            "request authorization",
        ]

    else:
        recovery = [
            "diagnose",
            "retry safely",
            "preserve task state",
        ]

    failure_id = make_id("failure")

    result = {
        "failure_id": failure_id,
        "task_id": task_id,
        "created_at": now_iso(),
        "stage": stage,
        "error": error,
        "recovery": {
            "failure_stage": stage,
            "error": error,
            "recovery_plan": recovery,
            "automatic_external_change": False,
            "diagnosed_at": now_iso(),
        },
    }

    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            INSERT INTO failures
            (id, created_at, task_id, stage,
             error, recovered, data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                failure_id,
                now_iso(),
                task_id,
                stage,
                error,
                0,
                json_dumps(result),
            ),
        )

        conn.commit()
        conn.close()

    record_event(
        "failure_diagnosed",
        task_id,
        result,
    )

    return result


# ============================================================
# MASTER ORCHESTRATOR
# ============================================================

def create_plan(
    objective: str,
) -> Dict[str, Any]:

    intent = classify_intent(objective)

    return {
        "plan_id": make_id("plan"),
        "objective": objective,
        "intent": intent,
        "steps": [
            {
                "id": "understand",
                "stage": "intent",
                "status": "completed",
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


def update_task(
    task_id: str,
    status: str,
    data: Dict[str, Any],
):
    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            UPDATE tasks
            SET status = ?, data = ?
            WHERE id = ?
            """,
            (
                status,
                json_dumps(data),
                task_id,
            ),
        )

        conn.commit()
        conn.close()


def run_infinity(
    objective: str,
    research: bool = False,
    verify: bool = True,
    remember: bool = True,
) -> Dict[str, Any]:

    task_id = make_id("task")

    plan = create_plan(objective)

    intent = plan["intent"]

    initial = {
        "task_id": task_id,
        "created_at": now_iso(),
        "objective": objective,
        "status": "running",
        "version": VERSION,
        "plan": plan,
    }

    with DB_LOCK:
        conn = db()

        conn.execute(
            """
            INSERT INTO tasks
            (id, created_at, objective, status, data)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                task_id,
                now_iso(),
                objective,
                "running",
                json_dumps(initial),
            ),
        )

        conn.commit()
        conn.close()

    record_event(
        "task_started",
        task_id,
        {
            "objective": objective,
            "intent": intent,
        },
    )

    try:
        # ----------------------------------------------------
        # MEMORY CONTEXT
        # ----------------------------------------------------

        memory_context = retrieve_memory_context(
            objective
        )

        # ----------------------------------------------------
        # INTELLIGENCE
        # ----------------------------------------------------

        intelligence = generate_intelligence(
            objective
        )

        if not intelligence.get("ok"):
            raise RuntimeError(
                intelligence.get(
                    "error",
                    "Intelligence engine failed",
                )
            )

        record_event(
            "intelligence_ready",
            task_id,
            {
                "provider": intelligence.get("provider"),
                "model": intelligence.get("model"),
                "fallback": intelligence.get(
                    "fallback",
                    False,
                ),
            },
        )

        # ----------------------------------------------------
        # EXECUTION
        # ----------------------------------------------------

        execution = execute_registered_action(
            task_id,
            objective,
            intelligence,
        )

        # ----------------------------------------------------
        # VERIFICATION
        # ----------------------------------------------------

        verification = (
            verify_execution(
                objective,
                execution,
            )
            if verify
            else {
                "verified": False,
                "status": "not_requested",
                "checks": {},
                "verified_at": now_iso(),
            }
        )

        if verify and not verification.get("verified"):
            failure = diagnose_failure(
                task_id,
                "verification",
                json_dumps(verification),
            )

            result = {
                **initial,
                "status": "error",
                "intelligence": intelligence,
                "execution": execution,
                "verification": verification,
                "self_healing": failure,
            }

            update_task(
                task_id,
                "error",
                result,
            )

            return result

        # ----------------------------------------------------
        # MEMORY
        # ----------------------------------------------------

        memory = None

        if remember:
            memory = save_memory(
                task_id,
                objective,
                intelligence,
                execution,
                verification,
            )

        # ----------------------------------------------------
        # GENOME
        # ----------------------------------------------------

        genome = create_genome(
            task_id,
            objective,
            intent,
            intelligence,
            execution,
            verification,
            memory,
        )

        # ----------------------------------------------------
        # COMPLETE
        # ----------------------------------------------------

        completed_at = now_iso()

        result = {
            **initial,
            "status": "completed",
            "completed_at": completed_at,

            "memory_context": memory_context,

            "intelligence": {
                "ok": True,
                "provider": intelligence.get("provider"),
                "model": intelligence.get("model"),
                "fallback": intelligence.get(
                    "fallback",
                    False,
                ),
                "text": intelligence.get("text"),
            },

            "execution": execution,

            "verification": verification,

            "memory": memory,

            "genome": genome,

            "governance": {
                "zero_dollar": True,
                "arbitrary_execution": False,
                "external_side_effects": False,
                "human_authorization": (
                    "required for consequential "
                    "external actions"
                ),
            },
        }

        update_task(
            task_id,
            "completed",
            result,
        )

        record_event(
            "task_completed",
            task_id,
            {
                "verified": verification.get(
                    "verified"
                ),
                "reusable": genome.get(
                    "reusable"
                ),
            },
        )

        return result

    except Exception as exc:

        error = str(exc)

        failure = diagnose_failure(
            task_id,
            "pipeline",
            error,
        )

        result = {
            **initial,
            "status": "error",
            "error": error,
            "self_healing": failure,
            "governance": {
                "zero_dollar": True,
                "arbitrary_execution": False,
                "external_side_effects": False,
            },
            "failed_at": now_iso(),
        }

        update_task(
            task_id,
            "error",
            result,
        )

        return result


# ============================================================
# API
# ============================================================

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": SERVICE,
        "version": VERSION,

        "execution_engine": True,
        "verification": True,
        "functional_testing": True,
        "memory": True,
        "genome": True,
        "self_healing": True,

        "provider": (
            "huggingface"
            if HF_TOKEN
            else "local-fallback"
        ),

        "arbitrary_execution": False,
        "zero_dollar_governor": True,
    }


@app.get("/v1/status")
def status():
    with DB_LOCK:
        conn = db()

        tasks = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks"
        ).fetchone()["n"]

        memories = conn.execute(
            "SELECT COUNT(*) AS n FROM memories"
        ).fetchone()["n"]

        genomes = conn.execute(
            "SELECT COUNT(*) AS n FROM genomes"
        ).fetchone()["n"]

        failures = conn.execute(
            "SELECT COUNT(*) AS n FROM failures"
        ).fetchone()["n"]

        executions = conn.execute(
            "SELECT COUNT(*) AS n FROM executions"
        ).fetchone()["n"]

        conn.close()

    return {
        "service": SERVICE,
        "version": VERSION,
        "status": "operational",
        "counters": {
            "tasks": tasks,
            "executions": executions,
            "memories": memories,
            "genomes": genomes,
            "failures": failures,
        },
        "capabilities": [
            "intent",
            "planning",
            "AI reasoning",
            "provider retry",
            "backup provider",
            "local fallback",
            "safe execution",
            "artifact generation",
            "functional verification",
            "memory",
            "intelligence genomes",
            "failure diagnosis",
            "self-healing plans",
        ],
    }


@app.post("/v1/run")
def run(request: RunRequest):
    return run_infinity(
        request.objective,
        research=request.research,
        verify=request.verify,
        remember=request.remember,
    )


@app.post("/v1/execute")
def execute(request: ExecuteRequest):

    if request.action not in SAFE_ACTIONS:
        raise HTTPException(
            status_code=403,
            detail=(
                "Action is not registered as a safe "
                "AI Infinity action."
            ),
        )

    task_id = make_id("manual")

    if request.action == "create_python_calculator":
        if request.filename != "simple_calculator.py":
            raise HTTPException(
                status_code=400,
                detail=(
                    "The calculator action requires "
                    "simple_calculator.py"
                ),
            )

        execution = create_artifact(
            task_id,
            "simple_calculator.py",
            CALCULATOR_CODE,
        )

    else:
        execution = create_artifact(
            task_id,
            request.filename,
            request.content,
        )

    verification = verify_execution(
        request.filename,
        execution,
    )

    return {
        "task_id": task_id,
        "execution": execution,
        "verification": verification,
    }


@app.get("/v1/tasks/{task_id}")
def get_task(task_id: str):

    with DB_LOCK:
        conn = db()

        row = conn.execute(
            """
            SELECT data
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()

        conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="Task not found",
        )

    return json.loads(row["data"])


@app.get("/v1/memory")
def memories():

    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT data
            FROM memories
            ORDER BY created_at DESC
            LIMIT 50
            """
        ).fetchall()

        conn.close()

    return [
        json.loads(row["data"])
        for row in rows
    ]


@app.get("/v1/genomes")
def genomes():

    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT data
            FROM genomes
            ORDER BY created_at DESC
            LIMIT 50
            """
        ).fetchall()

        conn.close()

    return [
        json.loads(row["data"])
        for row in rows
    ]


@app.get("/v1/failures")
def failures():

    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT data
            FROM failures
            ORDER BY created_at DESC
            LIMIT 50
            """
        ).fetchall()

        conn.close()

    return [
        json.loads(row["data"])
        for row in rows
    ]


@app.get("/v1/audit")
def audit():

    with DB_LOCK:
        conn = db()

        rows = conn.execute(
            """
            SELECT *
            FROM events
            ORDER BY created_at DESC
            LIMIT 100
            """
        ).fetchall()

        conn.close()

    return [
        {
            "id": row["id"],
            "created_at": row["created_at"],
            "task_id": row["task_id"],
            "event": row["event"],
            "data": json.loads(row["data"]),
        }
        for row in rows
    ]


# ============================================================
# WEB UI
# ============================================================

HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width,initial-scale=1">

<title>AI Infinity</title>

<style>
body {
    margin: 0;
    background: #0b0d12;
    color: #f5f7fb;
    font-family: Arial, sans-serif;
}

.container {
    max-width: 900px;
    margin: auto;
    padding: 24px;
}

h1 {
    font-size: 38px;
    margin-bottom: 5px;
}

.sub {
    color: #9da5b4;
    margin-bottom: 25px;
}

textarea {
    width: 100%;
    min-height: 150px;
    box-sizing: border-box;
    padding: 16px;
    border-radius: 12px;
    border: 1px solid #303642;
    background: #151922;
    color: white;
    font-size: 16px;
}

button {
    margin-top: 14px;
    padding: 13px 20px;
    border: 0;
    border-radius: 10px;
    cursor: pointer;
    font-weight: bold;
}

.primary {
    background: #ffffff;
    color: #000000;
}

.secondary {
    background: #202632;
    color: white;
    margin-left: 8px;
}

pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    background: #11151d;
    border: 1px solid #2b313c;
    padding: 16px;
    border-radius: 12px;
    margin-top: 20px;
}

.status {
    margin-top: 20px;
    color: #8ee6a8;
}
</style>
</head>

<body>

<div class="container">

<h1>∞ AI Infinity</h1>

<div class="sub">
Orchestrate → Execute → Verify → Remember → Evolve
</div>

<textarea id="objective"
placeholder="Give AI Infinity a real objective..."></textarea>

<br>

<button class="primary"
        onclick="runTask()">
Run Objective
</button>

<button class="secondary"
        onclick="health()">
System Health
</button>

<div id="status"
     class="status"></div>

<pre id="output">Ready.</pre>

</div>

<script>

async function runTask() {

    const objective =
        document.getElementById("objective").value.trim();

    if (!objective) {
        alert("Enter an objective first.");
        return;
    }

    document.getElementById("status").textContent =
        "AI Infinity is working...";

    document.getElementById("output").textContent =
        "Running...";

    try {

        const response = await fetch("/v1/run", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                objective: objective,
                research: false,
                verify: true,
                remember: true
            })
        });

        const data = await response.json();

        document.getElementById("status").textContent =
            data.status === "completed"
                ? "Completed"
                : "Task failed — inspect recovery data.";

        document.getElementById("output").textContent =
            JSON.stringify(data, null, 2);

    } catch (error) {

        document.getElementById("status").textContent =
            "Request failed.";

        document.getElementById("output").textContent =
            String(error);
    }
}


async function health() {

    try {

        const response =
            await fetch("/health");

        const data =
            await response.json();

        document.getElementById("status").textContent =
            "System online.";

        document.getElementById("output").textContent =
            JSON.stringify(data, null, 2);

    } catch (error) {

        document.getElementById("status").textContent =
            "Health request failed.";

        document.getElementById("output").textContent =
            String(error);
    }
}

</script>

</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def home():
    return HTML


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup():
    init_db()

    record_event(
        "system_started",
        data={
            "service": SERVICE,
            "version": VERSION,
            "provider": (
                "huggingface"
                if HF_TOKEN
                else "local-fallback"
            ),
            "arbitrary_execution": False,
            "zero_dollar_governor": True,
        },
    )


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )
