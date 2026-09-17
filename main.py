import os
import json
import time
import uuid
import re
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY v10.0
# Research → Reason → Verify → Remember → Improve
# ============================================================

VERSION = "10.0"
APP_NAME = "AI Infinity"

BASE = Path("/tmp/ai-infinity")
BASE.mkdir(parents=True, exist_ok=True)

TASKS_FILE = BASE / "tasks.json"
MEMORY_FILE = BASE / "memory.json"

HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
POLLINATIONS_API_KEY = os.getenv(
    "POLLINATIONS_API_KEY", ""
).strip()

POLLINATIONS_BASE_URL = os.getenv(
    "POLLINATIONS_BASE_URL",
    "https://gen.pollinations.ai"
).rstrip("/")

HF_MODEL = os.getenv(
    "HF_MODEL",
    "openai/gpt-oss-120b:fastest"
)

TIMEOUT = 35
MAX_RETRIES = 2
MAX_RESEARCH = 6
MAX_MEMORY = 300

app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description=(
        "Free-first resilient AI orchestration platform."
    )
)


# ============================================================
# THREAD-SAFE PROVIDER STATS
# ============================================================

STATS_LOCK = threading.Lock()

PROVIDER_STATS = {
    "huggingface": {
        "attempts": 0,
        "successes": 0,
        "failures": 0
    },
    "pollinations": {
        "attempts": 0,
        "successes": 0,
        "failures": 0
    },
    "local_fallback": {
        "attempts": 0,
        "successes": 0,
        "failures": 0
    }
}


def stat_attempt(provider: str):
    with STATS_LOCK:
        PROVIDER_STATS[provider]["attempts"] += 1


def stat_success(provider: str):
    with STATS_LOCK:
        PROVIDER_STATS[provider]["successes"] += 1


def stat_failure(provider: str):
    with STATS_LOCK:
        PROVIDER_STATS[provider]["failures"] += 1


# ============================================================
# SPECIALIST MINDS
# ============================================================

MINDS = [
    "Research Mind",
    "Builder Mind",
    "Critical Mind",
    "Optimizer Mind",
    "Verification Mind",
    "Future Mind"
]


# ============================================================
# REQUEST MODELS
# ============================================================

class TaskRequest(BaseModel):

    command: Optional[str] = Field(
        default=None,
        max_length=10000
    )

    objective: Optional[str] = Field(
        default=None,
        max_length=10000
    )

    research: bool = True
    verify: bool = True
    remember: bool = True

    duration_minutes: int = Field(
        default=1,
        ge=1,
        le=120
    )


class ResearchRequest(BaseModel):

    query: str = Field(
        ...,
        min_length=1,
        max_length=2000
    )


# ============================================================
# STORAGE
# ============================================================

def load_json(path: Path, default: Any) -> Any:

    try:

        if path.exists():

            with path.open(
                "r",
                encoding="utf-8"
            ) as f:

                return json.load(f)

    except Exception:
        pass

    return default


def save_json(path: Path, data: Any):

    try:

        temp = path.with_suffix(
            path.suffix + ".tmp"
        )

        with temp.open(
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

        temp.replace(path)

    except Exception:
        pass


def load_tasks():
    return load_json(
        TASKS_FILE,
        {}
    )


def load_memory():
    return load_json(
        MEMORY_FILE,
        []
    )


# ============================================================
# TEXT HELPERS
# ============================================================

def clean_text(
    value: Any,
    limit: int = 15000
) -> str:

    if value is None:
        return ""

    text = str(value)

    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()

    return text[:limit]


def safe_json(
    value: Any
) -> str:

    try:

        return json.dumps(
            value,
            ensure_ascii=False,
            indent=2
        )

    except Exception:

        return str(value)


def extract_content(
    data: Dict[str, Any]
) -> str:

    try:

        choices = data.get(
            "choices",
            []
        )

        if not choices:
            return ""

        message = choices[0].get(
            "message",
            {}
        )

        content = message.get(
            "content",
            ""
        )

        if isinstance(
            content,
            list
        ):

            parts = []

            for item in content:

                if isinstance(
                    item,
                    dict
                ):

                    text = item.get(
                        "text",
                        ""
                    )

                    if text:
                        parts.append(
                            str(text)
                        )

            content = "\n".join(parts)

        return clean_text(
            content,
            20000
        )

    except Exception:

        return ""


# ============================================================
# INTENT
# ============================================================

def classify_intent(
    objective: str
) -> str:

    text = objective.lower()

    groups = {

        "build": [
            "build",
            "create",
            "make",
            "develop",
            "code",
            "app",
            "website",
            "software"
        ],

        "research": [
            "research",
            "investigate",
            "find",
            "analyze",
            "analyse",
            "study",
            "compare"
        ],

        "technical": [
            "technical",
            "bug",
            "error",
            "api",
            "server",
            "deployment",
            "deploy",
            "github",
            "render"
        ],

        "business": [
            "business",
            "money",
            "profit",
            "market",
            "startup",
            "revenue"
        ],

        "automation": [
            "automate",
            "automation",
            "workflow",
            "agent"
        ],

        "creative": [
            "write",
            "story",
            "creative",
            "idea",
            "design",
            "video"
        ]
    }

    for intent, words in groups.items():

        if any(
            word in text
            for word in words
        ):

            return intent

    return "general"


# ============================================================
# WEB RESEARCH
# ============================================================

def web_research(
    query: str,
    max_results: int = MAX_RESEARCH
) -> List[Dict[str, Any]]:

    results = []

    try:

        response = requests.get(

            "https://html.duckduckgo.com/html/",

            params={
                "q": query
            },

            headers={
                "User-Agent":
                    "Mozilla/5.0 AI-Infinity/10.0"
            },

            timeout=15
        )

        if response.status_code != 200:
            return results

        blocks = re.findall(

            r'<a[^>]+class="result__a"'
            r'[^>]*href="([^"]+)"'
            r'[^>]*>(.*?)</a>',

            response.text,

            flags=re.I | re.S
        )

        for href, title in blocks:

            if len(results) >= max_results:
                break

            title = re.sub(
                r"<.*?>",
                "",
                title
            )

            title = clean_text(
                title,
                300
            )

            if title:

                results.append({

                    "id":
                        "src-" +
                        uuid.uuid4().hex[:8],

                    "title":
                        title,

                    "url":
                        href,

                    "source_type":
                        "web_search"
                })

    except Exception:
        pass

    return results


# ============================================================
# SOURCE VERIFICATION
# ============================================================

def verify_sources(
    results: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:

    checked = []

    for item in results[:MAX_RESEARCH]:

        url = item.get(
            "url",
            ""
        )

        reachable = False
        status_code = None

        try:

            response = requests.head(

                url,

                allow_redirects=True,

                headers={
                    "User-Agent":
                        "Mozilla/5.0 AI-Infinity/10.0"
                },

                timeout=8
            )

            status_code = (
                response.status_code
            )

            reachable = (
                200 <=
                status_code <
                400
            )

        except Exception:

            try:

                response = requests.get(

                    url,

                    allow_redirects=True,

                    headers={
                        "User-Agent":
                            "Mozilla/5.0 AI-Infinity/10.0"
                    },

                    timeout=8,

                    stream=True
                )

                status_code = (
                    response.status_code
                )

                reachable = (
                    200 <=
                    status_code <
                    400
                )

            except Exception:
                pass

        checked.append({

            **item,

            "reachable":
                reachable,

            "status_code":
                status_code
        })

    return checked


# ============================================================
# HUGGING FACE
# PRIMARY REASONING PROVIDER
# ============================================================

def provider_huggingface(
    prompt: str
) -> Optional[str]:

    if not HF_TOKEN:
        return None

    stat_attempt(
        "huggingface"
    )

    url = (
        "https://router.huggingface.co"
        "/v1/chat/completions"
    )

    headers = {

        "Authorization":
            f"Bearer {HF_TOKEN}",

        "Content-Type":
            "application/json",

        "User-Agent":
            "AI-Infinity/10.0"
    }

    models = [

        HF_MODEL,

        "openai/gpt-oss-120b:fastest",

        "deepseek-ai/DeepSeek-R1:fastest"
    ]

    # Remove duplicates
    models = list(
        dict.fromkeys(models)
    )

    for model in models:

        try:

            response = requests.post(

                url,

                headers=headers,

                json={

                    "model":
                        model,

                    "messages": [

                        {
                            "role":
                                "system",

                            "content":
                                (
                                    "You are a reliable "
                                    "reasoning engine inside "
                                    "AI Infinity. Be factual, "
                                    "practical and explicit "
                                    "about uncertainty."
                                )
                        },

                        {
                            "role":
                                "user",

                            "content":
                                prompt
                        }
                    ],

                    "temperature":
                        0.2,

                    "max_tokens":
                        1400,

                    "stream":
                        False
                },

                timeout=TIMEOUT
            )

            if response.status_code != 200:
                continue

            text = extract_content(
                response.json()
            )

            if text:

                stat_success(
                    "huggingface"
                )

                return text

        except Exception:
            continue

    stat_failure(
        "huggingface"
    )

    return None


# ============================================================
# POLLINATIONS
# SECONDARY PROVIDER
# ============================================================

def provider_pollinations(
    prompt: str
) -> Optional[str]:

    if not POLLINATIONS_API_KEY:
        return None

    stat_attempt(
        "pollinations"
    )

    url = (
        f"{POLLINATIONS_BASE_URL}"
        "/v1/chat/completions"
    )

    headers = {

        "Authorization":
            f"Bearer {POLLINATIONS_API_KEY}",

        "Content-Type":
            "application/json",

        "User-Agent":
            "AI-Infinity/10.0"
    }

    models = [

        "openai",

        "openai-fast",

        "openai-large"
    ]

    for model in models:

        try:

            response = requests.post(

                url,

                headers=headers,

                json={

                    "model":
                        model,

                    "messages": [

                        {
                            "role":
                                "system",

                            "content":
                                (
                                    "You are a reliable "
                                    "reasoning engine inside "
                                    "AI Infinity."
                                )
                        },

                        {
                            "role":
                                "user",

                            "content":
                                prompt
                        }
                    ],

                    "temperature":
                        0.2,

                    "max_tokens":
                        1400,

                    "stream":
                        False
                },

                timeout=TIMEOUT
            )

            if response.status_code != 200:
                continue

            text = extract_content(
                response.json()
            )

            if text:

                stat_success(
                    "pollinations"
                )

                return text

        except Exception:
            continue

    stat_failure(
        "pollinations"
    )

    return None


# ============================================================
# LOCAL FALLBACK
# ============================================================

def provider_local_fallback(
    prompt: str
) -> str:

    stat_attempt(
        "local_fallback"
    )

    stat_success(
        "local_fallback"
    )

    return (
        "AI provider output was unavailable. "
        "AI Infinity preserved the evidence, "
        "execution state and provenance instead "
        "of inventing an answer. Enable HF_TOKEN "
        "or POLLINATIONS_API_KEY for model reasoning."
    )


# ============================================================
# UNIVERSAL ROUTER
# ============================================================

def ai_generate(
    prompt: str
) -> Dict[str, Any]:

    # PRIMARY → SECONDARY → FALLBACK
    providers = [

        (
            "huggingface",
            provider_huggingface
        ),

        (
            "pollinations",
            provider_pollinations
        )
    ]

    for provider_name, provider_fn in providers:

        for attempt in range(
            1,
            MAX_RETRIES + 1
        ):

            result = provider_fn(
                prompt
            )

            if result:

                return {

                    "success":
                        True,

                    "provider":
                        provider_name,

                    "attempt":
                        attempt,

                    "text":
                        result
                }

            time.sleep(
                0.25
            )

    return {

        "success":
            True,

        "provider":
            "local_fallback",

        "attempt":
            1,

        "text":
            provider_local_fallback(
                prompt
            )
    }


# ============================================================
# SPECIALIST MIND
# ============================================================

def run_mind(
    mind: str,
    objective: str,
    evidence: List[Dict[str, Any]]
) -> Dict[str, Any]:

    prompt = f"""
You are the {mind} inside AI Infinity.

OBJECTIVE:
{objective}

EVIDENCE:
{safe_json(evidence)}

Analyze the objective from your specialist role.

Return:

1. Key finding
2. Evidence used
3. Risk or uncertainty
4. Concrete recommendation
5. One actionable next step

Rules:
- Never invent evidence.
- Distinguish evidence from recommendation.
- Be concise.
- Be practical.
"""

    result = ai_generate(
        prompt
    )

    return {

        "mind":
            mind,

        "provider":
            result["provider"],

        "success":
            result["success"],

        "analysis":
            result["text"]
    }


# ============================================================
# CONFIDENCE
# ============================================================

def calculate_confidence(
    evidence: List[Dict[str, Any]],
    mind_results: List[Dict[str, Any]],
    synthesis: Dict[str, Any],
    verification: Optional[Dict[str, Any]]
) -> Dict[str, Any]:

    score = 0.35

    reachable = sum(
        1
        for item in evidence
        if item.get(
            "reachable",
            False
        )
    )

    if reachable:
        score += min(
            0.25,
            reachable * 0.04
        )

    successful_minds = sum(
        1
        for mind in mind_results
        if mind.get(
            "success",
            False
        )
    )

    score += min(
        0.25,
        successful_minds * 0.04
    )

    if synthesis.get(
        "provider"
    ) != "local_fallback":

        score += 0.10

    if verification and verification.get(
        "provider"
    ) != "local_fallback":

        score += 0.05

    score = max(
        0.0,
        min(
            score,
            0.95
        )
    )

    return {

        "score":
            round(score, 2),

        "percentage":
            f"{round(score * 100)}%",

        "basis": {

            "verified_evidence":
                reachable,

            "successful_specialist_minds":
                successful_minds,

            "synthesis_provider":
                synthesis.get(
                    "provider"
                ),

            "verification_provider":
                (
                    verification.get(
                        "provider"
                    )
                    if verification
                    else None
                )
        },

        "note":
            "Heuristic confidence, not a probability."
    }


# ============================================================
# MAIN PIPELINE
# ============================================================

def execute_pipeline(
    objective: str,
    do_research: bool = True,
    do_verify: bool = True
) -> Dict[str, Any]:

    started = time.time()

    intent = classify_intent(
        objective
    )

    # --------------------------------------------------------
    # RESEARCH
    # --------------------------------------------------------

    research = []

    if do_research:

        research = web_research(
            objective
        )

    # --------------------------------------------------------
    # VERIFY SOURCES
    # --------------------------------------------------------

    verified_sources = []

    if research:

        verified_sources = verify_sources(
            research
        )

    evidence = (
        verified_sources
        if verified_sources
        else research
    )

    # --------------------------------------------------------
    # SPECIALIST MINDS
    # --------------------------------------------------------

    mind_results = []

    with ThreadPoolExecutor(
        max_workers=len(MINDS)
    ) as executor:

        futures = [

            executor.submit(
                run_mind,
                mind,
                objective,
                evidence
            )

            for mind in MINDS
        ]

        for future in as_completed(
            futures
        ):

            try:

                mind_results.append(
                    future.result()
                )

            except Exception as exc:

                mind_results.append({

                    "mind":
                        "unknown",

                    "provider":
                        "local",

                    "success":
                        False,

                    "analysis":
                        str(exc)
                })

    # --------------------------------------------------------
    # SYNTHESIS
    # --------------------------------------------------------

    synthesis_prompt = f"""
You are the CENTRAL SYNTHESIS ENGINE of AI Infinity.

OBJECTIVE:
{objective}

INTENT:
{intent}

VERIFIED EVIDENCE:
{safe_json(evidence)}

SPECIALIST ANALYSIS:
{safe_json(mind_results)}

Produce the best practical answer.

IMPORTANT:

- Answer the objective directly.
- Use the evidence.
- Do not invent facts.
- Separate FACTS, UNCERTAINTIES and RECOMMENDATIONS.
- If evidence conflicts, explicitly say so.
- Make recommendations actionable.
- End with exactly ONE immediate next action.

Output structure:

ANSWER

KEY FACTS

UNCERTAINTIES

RECOMMENDATIONS

ONE IMMEDIATE NEXT ACTION
"""

    synthesis = ai_generate(
        synthesis_prompt
    )

    # --------------------------------------------------------
    # VERIFICATION
    # --------------------------------------------------------

    verification = None

    if do_verify:

        verification_prompt = f"""
You are the final verification engine of AI Infinity.

OBJECTIVE:
{objective}

SYNTHESIS:
{synthesis["text"]}

EVIDENCE:
{safe_json(evidence)}

SPECIALIST ANALYSIS:
{safe_json(mind_results)}

Check:

1. Does the answer answer the objective?
2. Are claims supported by evidence?
3. Are unsupported claims clearly marked?
4. Are facts separated from recommendations?
5. Are contradictions identified?
6. Is the next action concrete?

Return:

VERDICT: PASS or NEEDS_REVIEW

CRITICAL_CHECKS:
1.
2.
3.

CORRECTIONS:
Only list corrections if necessary.
"""

        verification = ai_generate(
            verification_prompt
        )

    # --------------------------------------------------------
    # CONFIDENCE
    # --------------------------------------------------------

    confidence = calculate_confidence(

        evidence,

        mind_results,

        synthesis,

        verification
    )

    # --------------------------------------------------------
    # PROVENANCE
    # --------------------------------------------------------

    provenance = {

        "objective":
            objective,

        "intent":
            intent,

        "research_enabled":
            do_research,

        "verification_enabled":
            do_verify,

        "sources_considered":
            len(evidence),

        "specialist_minds":
            len(mind_results),

        "synthesis_provider":
            synthesis.get(
                "provider"
            ),

        "verification_provider":
            (
                verification.get(
                    "provider"
                )
                if verification
                else None
            ),

        "generated_at":
            time.time()
    }

    # --------------------------------------------------------
    # PLAN
    # --------------------------------------------------------

    execution_plan = [

        {
            "step": 1,
            "action":
                "Classify objective",
            "status":
                "completed"
        },

        {
            "step": 2,
            "action":
                "Research external evidence",
            "status":
                (
                    "completed"
                    if do_research
                    else "skipped"
                )
        },

        {
            "step": 3,
            "action":
                "Verify sources",
            "status":
                (
                    "completed"
                    if verified_sources
                    else "no_verified_sources"
                )
        },

        {
            "step": 4,
            "action":
                "Run six specialist minds",
            "status":
                "completed"
        },

        {
            "step": 5,
            "action":
                "Synthesize answer",
            "status":
                "completed"
        },

        {
            "step": 6,
            "action":
                "Verify synthesis",
            "status":
                (
                    "completed"
                    if do_verify
                    else "skipped"
                )
        },

        {
            "step": 7,
            "action":
                "Calculate confidence",
            "status":
                "completed"
        },

        {
            "step": 8,
            "action":
                "Store reusable memory",
            "status":
                "available"
        }
    ]

    return {

        "objective":
            objective,

        "intent":
            intent,

        "research_results":
            research,

        "verified_sources":
            verified_sources,

        "specialist_minds":
            mind_results,

        "synthesis":
            synthesis,

        "verification":
            verification,

        "confidence":
            confidence,

        "provenance":
            provenance,

        "execution_plan":
            execution_plan,

        "providers":
            PROVIDER_STATS,

        "duration_seconds":
            round(
                time.time() - started,
                2
            )
    }


# ============================================================
# MEMORY
# ============================================================

def remember(
    objective: str,
    result: Dict[str, Any]
):

    memory = load_memory()

    synthesis = result.get(
        "synthesis",
        {}
    )

    memory.append({

        "id":
            "mem-" +
            uuid.uuid4().hex[:12],

        "timestamp":
            time.time(),

        "objective":
            objective,

        "intent":
            result.get(
                "intent"
            ),

        "provider":
            synthesis.get(
                "provider"
            ),

        "confidence":
            result.get(
                "confidence",
                {}
            ),

        "summary":
            clean_text(
                synthesis.get(
                    "text",
                    ""
                ),
                2000
            )
    })

    save_json(
        MEMORY_FILE,
        memory[-MAX_MEMORY:]
    )


# ============================================================
# TASK RUNNER
# ============================================================

def run_task(
    payload: TaskRequest
):

    objective = clean_text(

        payload.command
        or payload.objective,

        10000
    )

    if not objective:

        raise HTTPException(

            status_code=422,

            detail:
                "Provide either "
                "'command' or 'objective'."
        )

    task_id = (
        "task-" +
        uuid.uuid4().hex[:12]
    )

    tasks = load_tasks()

    tasks[task_id] = {

        "task_id":
            task_id,

        "status":
            "running",

        "objective":
            objective,

        "created_at":
            time.time()
    }

    save_json(
        TASKS_FILE,
        tasks
    )

    try:

        result = execute_pipeline(

            objective,

            do_research=
                payload.research,

            do_verify=
                payload.verify
        )

        if payload.remember:

            remember(
                objective,
                result
            )

        tasks = load_tasks()

        tasks[task_id] = {

            **tasks.get(
                task_id,
                {}
            ),

            "status":
                "completed",

            "result":
                result,

            "completed_at":
                time.time()
        }

        save_json(
            TASKS_FILE,
            tasks
        )

        return {

            "task_id":
                task_id,

            "status":
                "completed",

            "objective":
                objective,

            "result":
                result
        }

    except Exception as exc:

        tasks = load_tasks()

        tasks[task_id] = {

            **tasks.get(
                task_id,
                {}
            ),

            "status":
                "failed",

            "error":
                str(exc)
        }

        save_json(
            TASKS_FILE,
            tasks
        )

        raise HTTPException(

            status_code=500,

            detail:
                str(exc)
        )


# ============================================================
# WEB UI
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
def home():

    return """
<!DOCTYPE html>

<html>

<head>

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>AI Infinity v10</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    padding: 20px;
    font-family: system-ui, sans-serif;
    background: #070b14;
    color: white;
}

.container {
    max-width: 850px;
    margin: auto;
}

h1 {
    font-size: 38px;
    margin-bottom: 5px;
}

.subtitle {
    opacity: .7;
    margin-bottom: 25px;
}

.card {
    background: #111827;
    border: 1px solid #263044;
    border-radius: 16px;
    padding: 18px;
    margin-bottom: 15px;
}

textarea {
    width: 100%;
    min-height: 170px;
    resize: vertical;
    border-radius: 12px;
    border: 1px solid #344057;
    background: #080d18;
    color: white;
    padding: 15px;
    font-size: 16px;
}

button {
    width: 100%;
    border: 0;
    border-radius: 12px;
    padding: 16px;
    margin-top: 12px;
    background: #315efb;
    color: white;
    font-size: 16px;
    font-weight: 700;
}

button:disabled {
    opacity: .5;
}

pre {
    white-space: pre-wrap;
    word-break: break-word;
    overflow-x: auto;
    font-size: 13px;
}

.badge {
    display: inline-block;
    padding: 6px 10px;
    border-radius: 20px;
    background: #1d293d;
    margin-right: 5px;
    margin-bottom: 5px;
}

</style>

</head>

<body>

<div class="container">

<div class="card">

<h1>∞ AI Infinity</h1>

<div class="subtitle">
Research → Reason → Verify → Remember → Improve
</div>

<div class="badge">v10.0</div>
<div class="badge">Multi-Mind</div>
<div class="badge">Web Research</div>
<div class="badge">Verification</div>
<div class="badge">Memory</div>

</div>

<div class="card">

<textarea
id="objective"
placeholder="What should AI Infinity accomplish?"
></textarea>

<button
id="run"
onclick="runAI()">

RUN AI INFINITY

</button>

</div>

<div class="card">

<pre id="output">Ready.</pre>

</div>

</div>

<script>

async function runAI() {

    const objective =
        document
        .getElementById("objective")
        .value
        .trim();

    const output =
        document
        .getElementById("output");

    const button =
        document
        .getElementById("run");

    if (!objective) {

        output.textContent =
            "Enter an objective first.";

        return;
    }

    button.disabled = true;

    output.textContent =
        "AI Infinity is working...\\n\\n" +
        "Research → 6 Minds → Synthesis → Verification";

    try {

        const response =
            await fetch(
                "/task",
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body: JSON.stringify({

                        objective:
                            objective,

                        research:
                            true,

                        verify:
                            true,

                        remember:
                            true
                    })
                }
            );

        const data =
            await response.json();

        output.textContent =
            JSON.stringify(
                data,
                null,
                2
            );

    } catch (error) {

        output.textContent =
            "ERROR: " + error;

    } finally {

        button.disabled = false;
    }
}

</script>

</body>

</html>
"""


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {

        "status":
            "ok",

        "app":
            APP_NAME,

        "version":
            VERSION,

        "architecture":
            "Research → Reason → Verify → Remember → Improve",

        "minds":
            MINDS,

        "providers":
            {

                "huggingface":
                    bool(HF_TOKEN),

                "pollinations":
                    bool(POLLINATIONS_API_KEY),

                "local_fallback":
                    True
            }
    }


# ============================================================
# TASK
# ============================================================

@app.post("/task")
def create_task(
    payload: TaskRequest
):

    return run_task(
        payload
    )


# ============================================================
# RUN ALIAS
# ============================================================

@app.post("/run")
def run_alias(
    payload: TaskRequest
):

    return run_task(
        payload
    )


# ============================================================
# GET TASK
# ============================================================

@app.get("/task/{task_id}")
def get_task(
    task_id: str
):

    tasks = load_tasks()

    task = tasks.get(
        task_id
    )

    if not task:

        raise HTTPException(
            status_code=404,
            detail="Task not found"
        )

    return task


# ============================================================
# RESEARCH
# ============================================================

@app.post("/research")
def research(
    payload: ResearchRequest
):

    results = web_research(
        payload.query,
        max_results=10
    )

    return {

        "query":
            payload.query,

        "results":
            results,

        "verified":
            verify_sources(
                results
            )
    }


# ============================================================
# MEMORY
# ============================================================

@app.get("/memory")
def memory():

    items = load_memory()

    return {

        "count":
            len(items),

        "items":
            items
    }


# ============================================================
# STATS
# ============================================================

@app.get("/stats")
def stats():

    return {

        "app":
            APP_NAME,

        "version":
            VERSION,

        "tasks":
            len(load_tasks()),

        "memory_items":
            len(load_memory()),

        "providers":
            PROVIDER_STATS,

        "provider_priority":
            [
                "huggingface",
                "pollinations",
                "local_fallback"
            ],

        "minds":
            MINDS,

        "free_first":
            True
    }


# ============================================================
# CONFIG
# ============================================================

@app.get("/config")
def config():

    return {

        "app":
            APP_NAME,

        "version":
            VERSION,

        "pipeline":
            [
                "intent",
                "research",
                "source_verification",
                "six_specialist_minds",
                "synthesis",
                "verification",
                "confidence",
                "provenance",
                "memory"
            ],

        "provider_order":
            [
                "huggingface",
                "pollinations",
                "local_fallback"
            ],

        "models":
            {
                "huggingface":
                    HF_MODEL,

                "pollinations":
                    [
                        "openai",
                        "openai-fast",
                        "openai-large"
                    ]
            },

        "api_keys_present":
            {

                "HF_TOKEN":
                    bool(HF_TOKEN),

                "POLLINATIONS_API_KEY":
                    bool(
                        POLLINATIONS_API_KEY
                    )
            }
    }
