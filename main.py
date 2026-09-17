import os
import json
import time
import uuid
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


# ============================================================
# AI INFINITY v9.0
# Research → Reason → Verify → Remember
# Free-first resilient AI orchestration engine
# ============================================================

VERSION = "9.0"
APP_NAME = "AI Infinity"

BASE = Path("/tmp/ai-infinity")
BASE.mkdir(parents=True, exist_ok=True)

TASKS_FILE = BASE / "tasks.json"
MEMORY_FILE = BASE / "memory.json"

HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
POLLINATIONS_API_KEY = os.getenv("POLLINATIONS_API_KEY", "").strip()

POLLINATIONS_BASE_URL = os.getenv(
    "POLLINATIONS_BASE_URL",
    "https://gen.pollinations.ai"
).rstrip("/")

TIMEOUT = 25
MAX_RETRIES = 2


app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description=(
        "Free-first resilient AI orchestration platform: "
        "research, specialist reasoning, synthesis, verification and memory."
    ),
)


# ============================================================
# PROVIDER STATS
# ============================================================

PROVIDER_STATS = {
    "pollinations": {
        "attempts": 0,
        "successes": 0,
    },
    "huggingface": {
        "attempts": 0,
        "successes": 0,
    },
    "local_fallback": {
        "attempts": 0,
        "successes": 0,
    },
}


# ============================================================
# SPECIALIST MINDS
# ============================================================

MINDS = [
    "Research Mind",
    "Builder Mind",
    "Critical Mind",
    "Optimizer Mind",
    "Verification Mind",
    "Future Mind",
]


# ============================================================
# REQUEST MODELS
# ============================================================

class TaskRequest(BaseModel):
    # Both are accepted.
    # This fixes the previous "command field required" problem.
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
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass

    return default


def save_json(path: Path, data: Any) -> None:
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )
    except Exception:
        pass


def load_tasks() -> Dict[str, Any]:
    return load_json(TASKS_FILE, {})


def save_tasks(data: Dict[str, Any]) -> None:
    save_json(TASKS_FILE, data)


def load_memory() -> List[Dict[str, Any]]:
    return load_json(MEMORY_FILE, [])


def save_memory(data: List[Dict[str, Any]]) -> None:
    save_json(MEMORY_FILE, data[-200:])


# ============================================================
# HELPERS
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


def safe_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            indent=2
        )
    except Exception:
        return str(value)


# ============================================================
# FREE WEB RESEARCH
# ============================================================

def web_research(
    query: str,
    max_results: int = 6
) -> List[Dict[str, str]]:

    results = []

    try:

        response = requests.get(
            "https://html.duckduckgo.com/html/",
            params={
                "q": query
            },
            headers={
                "User-Agent":
                    "Mozilla/5.0 AI-Infinity/9.0"
            },
            timeout=15
        )

        if response.status_code != 200:
            return results

        blocks = re.findall(
            r'<a[^>]+class="result__a"[^>]*'
            r'href="([^"]+)"[^>]*>(.*?)</a>',
            response.text,
            flags=re.I | re.S
        )

        for href, title in blocks[:max_results]:

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
                    "title": title,
                    "url": href
                })

    except Exception:
        pass

    return results


# ============================================================
# SOURCE VERIFICATION
# ============================================================

def verify_sources(
    results: List[Dict[str, str]]
) -> List[Dict[str, Any]]:

    checked = []

    for item in results[:6]:

        url = item.get(
            "url",
            ""
        )

        status = None
        reachable = False

        try:

            response = requests.head(
                url,
                allow_redirects=True,
                headers={
                    "User-Agent":
                        "Mozilla/5.0 AI-Infinity/9.0"
                },
                timeout=8
            )

            status = response.status_code

            reachable = (
                200 <= response.status_code < 400
            )

        except Exception:

            try:

                response = requests.get(
                    url,
                    allow_redirects=True,
                    headers={
                        "User-Agent":
                            "Mozilla/5.0 AI-Infinity/9.0"
                    },
                    timeout=8,
                    stream=True
                )

                status = response.status_code

                reachable = (
                    200 <= response.status_code < 400
                )

            except Exception:
                pass

        checked.append({
            **item,
            "reachable": reachable,
            "status_code": status
        })

    return checked


# ============================================================
# PROVIDER 1
# POLLINATIONS
# ============================================================

def provider_pollinations(
    prompt: str
) -> Optional[str]:

    if not POLLINATIONS_API_KEY:
        return None

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
            "AI-Infinity/9.0"
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
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    "temperature": 0.2,
                    "max_tokens": 900
                },
                timeout=TIMEOUT
            )

            if response.status_code != 200:
                continue

            data = response.json()

            text = (
                data
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )

            if text:
                return clean_text(text)

        except Exception:
            continue

    return None


# ============================================================
# PROVIDER 2
# HUGGING FACE
# ============================================================

def provider_huggingface(
    prompt: str
) -> Optional[str]:

    if not HF_TOKEN:
        return None

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
            "AI-Infinity/9.0"
    }

    models = [
        os.getenv(
            "HF_MODEL",
            "openai/gpt-oss-120b:fastest"
        ),
        "deepseek-ai/DeepSeek-R1:fastest"
    ]

    for model in models:

        try:

            response = requests.post(
                url,
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    "temperature": 0.2,
                    "max_tokens": 900,
                    "stream": False
                },
                timeout=TIMEOUT
            )

            if response.status_code != 200:
                continue

            data = response.json()

            text = (
                data
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )

            if text:
                return clean_text(text)

        except Exception:
            continue

    return None


# ============================================================
# LOCAL FALLBACK
# ============================================================

def provider_free_fallback(
    prompt: str
) -> str:

    return (
        "External AI providers were unavailable. "
        "AI Infinity completed the safe local "
        "orchestration path without pretending that "
        "unavailable model output was real. "
        "Retry with an enabled provider for full reasoning."
    )


# ============================================================
# UNIVERSAL AI ROUTER
# ============================================================

def call_provider(
    name: str,
    prompt: str
) -> Optional[str]:

    PROVIDER_STATS[name]["attempts"] += 1

    result = None

    if name == "pollinations":

        result = provider_pollinations(
            prompt
        )

    elif name == "huggingface":

        result = provider_huggingface(
            prompt
        )

    elif name == "local_fallback":

        result = provider_free_fallback(
            prompt
        )

    if result:

        PROVIDER_STATS[name]["successes"] += 1

    return result


def ai_generate(
    prompt: str
) -> Dict[str, Any]:

    providers = [
        "pollinations",
        "huggingface",
        "local_fallback"
    ]

    for provider in providers:

        for attempt in range(
            1,
            MAX_RETRIES + 1
        ):

            result = call_provider(
                provider,
                prompt
            )

            if result:

                return {
                    "success": True,
                    "provider": provider,
                    "attempt": attempt,
                    "text": result
                }

            if provider != "local_fallback":

                time.sleep(0.4)

    return {
        "success": False,
        "provider": None,
        "attempt": MAX_RETRIES,
        "text": "No provider returned a usable response."
    }


# ============================================================
# INTENT ENGINE
# ============================================================

def classify_intent(
    command: str
) -> str:

    text = command.lower()

    groups = {

        "build": [
            "build",
            "create",
            "make",
            "develop",
            "code",
            "app",
            "website"
        ],

        "research": [
            "research",
            "investigate",
            "find",
            "analyze",
            "study"
        ],

        "business": [
            "business",
            "money",
            "profit",
            "market",
            "startup"
        ],

        "technical": [
            "technical",
            "bug",
            "error",
            "api",
            "server",
            "deployment"
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
            "design"
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
# SPECIALIST MIND
# ============================================================

def run_mind(
    mind: str,
    objective: str,
    research: List[Dict[str, Any]]
) -> Dict[str, Any]:

    prompt = f"""
You are the {mind} inside AI Infinity.

OBJECTIVE:
{objective}

AVAILABLE EVIDENCE:
{safe_json(research)}

Return exactly:

1. Key finding
2. Risk or uncertainty
3. Concrete recommendation
4. One actionable next step

Be factual, concise and practical.
Do not invent evidence.
"""

    result = ai_generate(prompt)

    return {
        "mind": mind,
        "provider": result["provider"],
        "success": result["success"],
        "analysis": result["text"]
    }


# ============================================================
# CORE PIPELINE
# ============================================================

def execute_pipeline(
    objective: str,
    do_research: bool = True,
    do_verify: bool = True
) -> Dict[str, Any]:

    intent = classify_intent(
        objective
    )

    # --------------------------------------------------------
    # RESEARCH
    # --------------------------------------------------------

    research = (
        web_research(objective)
        if do_research
        else []
    )

    # --------------------------------------------------------
    # VERIFY SOURCES
    # --------------------------------------------------------

    verified_sources = (
        verify_sources(research)
        if do_verify and research
        else []
    )

    evidence = (
        verified_sources
        or research
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
                    "mind": "unknown",
                    "provider": None,
                    "success": False,
                    "analysis": str(exc)
                })

    # --------------------------------------------------------
    # SYNTHESIS
    # --------------------------------------------------------

    synthesis_prompt = f"""
You are the central AI Infinity synthesis engine.

OBJECTIVE:
{objective}

INTENT:
{intent}

RESEARCH:
{safe_json(evidence)}

SPECIALIST MINDS:
{safe_json(mind_results)}

Create the best practical answer.

RULES:

- Answer the objective directly.
- Separate facts from recommendations.
- Never invent missing evidence.
- State uncertainty when evidence is missing.
- If the user requests 3 items, provide exactly 3.
- Make actions concrete.
- End with ONE immediate next action.
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
Verify this AI Infinity result.

OBJECTIVE:
{objective}

ANSWER:
{synthesis["text"]}

RESEARCH:
{safe_json(evidence)}

Check:

1. Does it answer the objective?
2. Are unsupported claims present?
3. Are there contradictions?
4. Are recommendations concrete?
5. Is uncertainty clearly stated?

Return:

PASS or NEEDS_REVIEW

Then give the 3 most important checks.
"""

        verification = ai_generate(
            verification_prompt
        )

    # --------------------------------------------------------
    # FINAL PIPELINE RESULT
    # --------------------------------------------------------

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

        "execution_plan": [

            {
                "step": 1,
                "action":
                    "Understand and classify objective",
                "status":
                    "completed"
            },

            {
                "step": 2,
                "action":
                    "Research and verify evidence",
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
                    "Run specialist minds in parallel",
                "status":
                    "completed"
            },

            {
                "step": 4,
                "action":
                    "Synthesize final answer",
                "status":
                    "completed"
            },

            {
                "step": 5,
                "action":
                    "Verify final result",
                "status":
                    (
                        "completed"
                        if do_verify
                        else "skipped"
                    )
            },

            {
                "step": 6,
                "action":
                    "Store reusable memory",
                "status":
                    "available"
            }
        ],

        "providers":
            PROVIDER_STATS
    }


# ============================================================
# MEMORY
# ============================================================

def remember(
    objective: str,
    result: Dict[str, Any]
) -> None:

    memory = load_memory()

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
            result
            .get("synthesis", {})
            .get("provider"),

        "success":
            result
            .get("synthesis", {})
            .get("success"),

        "summary":
            clean_text(
                result
                .get("synthesis", {})
                .get("text", ""),
                1200
            )
    })

    save_memory(
        memory
    )


# ============================================================
# TASK EXECUTION
# ============================================================

def run_task(
    payload: TaskRequest
) -> Dict[str, Any]:

    objective = clean_text(
        payload.command
        or payload.objective,
        10000
    )

    if not objective:

        raise HTTPException(
            status_code=422,
            detail=(
                "Provide either "
                "'command' or 'objective'."
            )
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

    save_tasks(
        tasks
    )

    try:

        result = execute_pipeline(
            objective,
            payload.research,
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

        save_tasks(
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

        save_tasks(
            tasks
        )

        raise HTTPException(
            status_code=500,
            detail=str(exc)
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
<!doctype html>

<html>

<head>

<meta name="viewport"
      content="width=device-width,initial-scale=1">

<title>AI Infinity</title>

<style>

body {
    font-family: system-ui, sans-serif;
    max-width: 760px;
    margin: 30px auto;
    padding: 20px;
    background: #0b1020;
    color: white;
}

h1 {
    font-size: 34px;
}

textarea {
    width: 100%;
    box-sizing: border-box;
    padding: 15px;
    border-radius: 12px;
    border: 1px solid #334;
    background: #11182c;
    color: white;
    font-size: 16px;
}

button {
    width: 100%;
    padding: 15px;
    margin-top: 12px;
    border-radius: 12px;
    border: 0;
    background: #2457ff;
    color: white;
    font-weight: 700;
    font-size: 16px;
}

pre {
    white-space: pre-wrap;
    background: #11182c;
    padding: 15px;
    border-radius: 12px;
    overflow-x: auto;
}

</style>

</head>

<body>

<h1>∞ AI Infinity</h1>

<p>
Research → Reason → Verify → Remember
</p>

<textarea
    id="q"
    rows="8"
    placeholder="Enter your objective..."
></textarea>

<button onclick="runAI()">
RUN AI INFINITY
</button>

<pre id="out">Ready.</pre>

<script>

async function runAI() {

    const q =
        document.getElementById("q").value;

    const out =
        document.getElementById("out");

    if (!q.trim()) {

        out.textContent =
            "Enter an objective first.";

        return;
    }

    out.textContent =
        "AI Infinity is working...";

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
                        objective: q,
                        research: true,
                        verify: true,
                        remember: true
                    })
                }
            );

        const data =
            await response.json();

        out.textContent =
            JSON.stringify(
                data,
                null,
                2
            );

    } catch (error) {

        out.textContent =
            String(error);
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

        "pipeline":
            [
                "intent",
                "research",
                "specialist_minds",
                "synthesis",
                "verification",
                "memory"
            ],

        "providers": {

            "pollinations_configured":
                bool(
                    POLLINATIONS_API_KEY
                ),

            "huggingface_configured":
                bool(
                    HF_TOKEN
                ),

            "local_fallback":
                True
        }
    }


# ============================================================
# MAIN TASK ENDPOINT
# ============================================================

@app.post("/task")
def create_task(
    payload: TaskRequest
):

    return run_task(
        payload
    )


# ============================================================
# COMPATIBILITY ALIAS
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

@app.get(
    "/task/{task_id}"
)
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
# RESEARCH ENDPOINT
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
# MEMORY ENDPOINT
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

    tasks = load_tasks()

    return {

        "app":
            APP_NAME,

        "version":
            VERSION,

        "tasks":
            len(tasks),

        "memory_items":
            len(
                load_memory()
            ),

        "providers":
            PROVIDER_STATS,

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
                "specialist_minds",
                "synthesis",
                "verification",
                "memory"
            ],

        "provider_order":
            [
                "pollinations",
                "huggingface",
                "local_fallback"
            ],

        "api_keys_present":
            {
                "POLLINATIONS_API_KEY":
                    bool(
                        POLLINATIONS_API_KEY
                    ),

                "HF_TOKEN":
                    bool(
                        HF_TOKEN
                    )
            }
    }
