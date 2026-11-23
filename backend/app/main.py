from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import traceback
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Path as ApiPath, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    import litellm
    from litellm import Cache, completion
except ImportError:  # Keep health and validation available in a misconfigured deployment.
    litellm = None
    Cache = None
    completion = None

DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant. Be clear, accurate, and concise."
MODEL = os.getenv("LLM_MODEL", "gemini/gemini-3.5-flash-lite")
KEYS = [value.strip() for value in os.getenv("LLM_API_KEYS", "").split(",") if value.strip()]
KEY_IDS = [f"key_{index + 1}" for index in range(len(KEYS))]
ENABLE_FAILURE_SIMULATION = os.getenv("ENABLE_FAILURE_SIMULATION", "false").lower() == "true"

if litellm is not None:
    # LiteLLM's exact-match in-memory response cache; one worker keeps request cache hits local.
    litellm.cache = Cache(type="local")
    litellm.set_verbose = False

app = FastAPI(title="Lyftr AI Chat", docs_url=None, redoc_url=None, openapi_url=None)
logger = logging.getLogger("lyftr.providers")
origins = [value.strip() for value in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if value.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Simulate-Failure"],
)


def database_path() -> Path:
    url = os.getenv("DATABASE_URL", "sqlite:////data/app.db")
    if not url.startswith("sqlite:///"):
        raise RuntimeError("DATABASE_URL must use SQLite")
    raw = url[len("sqlite:///"):]
    # sqlite:////absolute/path leaves one leading slash after removing sqlite:///
    path = Path("/" + raw.lstrip("/")) if url.startswith("sqlite:////") else Path(raw)
    return path


def connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def initialize_database() -> None:
    with closing(connect()) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                system_prompt TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session_id_id ON messages(session_id, id);
            """
        )
        db.commit()


@app.on_event("startup")
def startup() -> None:
    initialize_database()


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    system_prompt: str | None = Field(default=None, max_length=4000)

    @field_validator("session_id", mode="before")
    @classmethod
    def trim_session(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("message", mode="before")
    @classmethod
    def trim_message(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("system_prompt", mode="before")
    @classmethod
    def trim_prompt(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        value = value.strip()
        return value or None


def parse_failures(value: str | None) -> dict[str, int]:
    result: dict[str, int] = {}
    if not value:
        return result
    for pair in value.split(","):
        match = re.fullmatch(r"\s*(key_[1-9][0-9]*|\*)\s*=\s*(429|500|502|503)\s*", pair)
        if not match:
            raise HTTPException(status_code=422, detail="invalid X-Simulate-Failure format")
        result[match.group(1)] = int(match.group(2))
    return result


def failure_for_key(failures: dict[str, int], key_id: str) -> int | None:
    return failures.get(key_id, failures.get("*"))


def token_estimate(messages: list[dict[str, str]], text: str) -> int:
    if litellm is not None:
        try:
            return int(litellm.token_counter(model=MODEL, messages=messages))
        except Exception:
            pass
    return max(1, (len(text) + 3) // 4)


def response_value(response: Any, name: str, default: Any = None) -> Any:
    if isinstance(response, dict):
        return response.get(name, default)
    return getattr(response, name, default)


def response_is_cached(response: Any) -> bool:
    hidden = response_value(response, "_hidden_params", {}) or {}
    return bool(hidden.get("cache_hit", False)) if isinstance(hidden, dict) else False


def provider_reply(messages: list[dict[str, str]], failures: dict[str, int]) -> tuple[str, dict[str, Any]]:
    if not KEYS or completion is None:
        raise HTTPException(status_code=503, detail="all providers unavailable")
    failed_attempts = 0
    # Failure simulation disables lookup/write for this request so the hook always exercises routing.
    use_cache = not bool(failures)
    for index, (key_id, api_key) in enumerate(zip(KEY_IDS, KEYS)):
        simulated_status = failure_for_key(failures, key_id)
        if simulated_status is not None:
            failed_attempts += 1
            continue
        try:
            started = time.perf_counter()
            response = completion(
                model=MODEL,
                messages=messages,
                api_key=api_key,
                caching=use_cache,
                temperature=0.7,
                num_retries=0,
            )
            elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))
            choices = response_value(response, "choices", []) or []
            choice = choices[0] if choices else None
            message_obj = response_value(choice, "message", {}) if choice is not None else {}
            reply = response_value(message_obj, "content", "")
            if not isinstance(reply, str) or not reply.strip():
                failed_attempts += 1
                continue
            cached = response_is_cached(response)
            usage = response_value(response, "usage", {}) or {}
            input_tokens = response_value(usage, "prompt_tokens", None)
            output_tokens = response_value(usage, "completion_tokens", None)
            return reply.strip(), {
                "resolution_source": "LLM_CACHE" if cached else "LIVE_INFERENCE",
                "model": MODEL,
                "latency_ms": 0 if cached else elapsed_ms,
                "input_tokens": int(input_tokens) if input_tokens is not None else token_estimate(messages, json.dumps(messages)),
                "output_tokens": int(output_tokens) if output_tokens is not None else max(1, (len(reply) + 3) // 4),
                "key_id": None if cached else key_id,
                "fallback_attempts": 0 if cached else failed_attempts,
            }
        except HTTPException:
            raise
        except Exception as exc:
            # Keep provider diagnostics useful without logging credentials or full prompts.
            error_detail = getattr(exc, "message", None) or str(exc)
            error_detail = " ".join(str(error_detail).split())
            error_detail = re.sub(r"AIza[0-9A-Za-z_-]{20,}", "[REDACTED]", error_detail)
            error_detail = re.sub(
                r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*[^,\s]+",
                r"\1=[REDACTED]",
                error_detail,
            )[:500]
            frames = traceback.extract_tb(exc.__traceback__)
            location = frames[-1] if frames else None
            logger.warning(
                "provider call failed key_id=%s model=%s provider=%s error_type=%s status=%s message=%s at=%s:%s function=%s",
                key_id,
                MODEL,
                getattr(exc, "llm_provider", "unknown"),
                type(exc).__name__,
                getattr(exc, "status_code", None),
                error_detail,
                location.filename.rsplit("/", 1)[-1] if location else "unknown",
                location.lineno if location else "unknown",
                location.name if location else "unknown",
            )
            failed_attempts += 1
    raise HTTPException(status_code=503, detail="all providers unavailable")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        initialize_database()
        with closing(connect()) as db:
            db.execute("SELECT 1").fetchone()
    except Exception:
        raise HTTPException(status_code=503, detail="database unavailable")
    if len(KEYS) < 2 or litellm is None:
        raise HTTPException(status_code=503, detail="provider key pool unavailable")
    return {"status": "ok", "keys_configured": len(KEYS), "cache_enabled": litellm is not None}


@app.post("/chat")
def chat(request: ChatRequest, x_simulate_failure: str | None = Header(default=None, alias="X-Simulate-Failure")) -> dict[str, Any]:
    failures: dict[str, int] = {}
    if x_simulate_failure and ENABLE_FAILURE_SIMULATION:
        failures = parse_failures(x_simulate_failure)
    with closing(connect()) as db:
        session = db.execute("SELECT system_prompt FROM sessions WHERE session_id = ?", (request.session_id,)).fetchone()
        system_prompt = request.system_prompt or (session["system_prompt"] if session else DEFAULT_SYSTEM_PROMPT)
        history_rows = db.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC", (request.session_id,)
        ).fetchall()
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend({"role": row["role"], "content": row["content"]} for row in history_rows)
        messages.append({"role": "user", "content": request.message})
        reply, metadata = provider_reply(messages, failures)
        timestamp = now_iso()
        try:
            db.execute("BEGIN")
            db.execute(
                "INSERT INTO sessions(session_id, system_prompt, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET system_prompt = excluded.system_prompt",
                (request.session_id, system_prompt, timestamp),
            )
            db.execute(
                "INSERT INTO messages(session_id, role, content, metadata, created_at) VALUES (?, 'user', ?, NULL, ?)",
                (request.session_id, request.message, timestamp),
            )
            db.execute(
                "INSERT INTO messages(session_id, role, content, metadata, created_at) VALUES (?, 'assistant', ?, ?, ?)",
                (request.session_id, reply, json.dumps(metadata), timestamp),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise HTTPException(status_code=503, detail="unable to store conversation")
    return {"session_id": request.session_id, "reply": reply, "metadata": metadata}


@app.get("/sessions/{session_id}/messages")
def get_messages(session_id: str = ApiPath(min_length=1, max_length=128)) -> dict[str, Any]:
    with closing(connect()) as db:
        exists = db.execute("SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="session not found")
        rows = db.execute(
            "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,)
        ).fetchall()
    messages: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {"role": row["role"], "content": row["content"]}
        if row["role"] == "assistant" and row["metadata"]:
            item["metadata"] = json.loads(row["metadata"])
        messages.append(item)
    return {"session_id": session_id, "messages": messages}


@app.exception_handler(Exception)
async def unexpected_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": "internal server error"})
