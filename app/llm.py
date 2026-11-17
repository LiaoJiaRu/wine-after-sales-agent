from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Literal

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import settings

INTENTS = (
    "破损漏液",
    "少件错发",
    "真伪争议",
    "标签保质期",
    "物流延迟",
    "储存饮用咨询",
    "多意图",
    "其他",
)
Intent = Literal[
    "破损漏液",
    "少件错发",
    "真伪争议",
    "标签保质期",
    "物流延迟",
    "储存饮用咨询",
    "多意图",
    "其他",
]

MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
TIMEOUT = float(os.getenv("LLM_TIMEOUT", "20"))
MAX_REPAIR = 1
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "router_v1.txt"

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_OBJECT_RE = re.compile(r"\{.*\}", re.S)
_LAST_TRACE_ID: str | None = None


class LLMError(Exception):
    def __init__(self, message: str, *, code: str = "LLM_ERROR", retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


class IntentDecision(BaseModel):
    """路由结论。error/needs_human 仅在降级时出现。"""

    model_config = ConfigDict(extra="ignore")

    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    need_clarify: bool = False
    sub_tasks: list[str] = Field(default_factory=list)
    error: dict | None = None
    needs_human: bool = False


def _degraded(code: str, message: str, retryable: bool = False) -> IntentDecision:
    return IntentDecision(
        intent="其他",
        confidence=0.0,
        need_clarify=True,
        sub_tasks=[],
        error={"code": code, "message": message, "retryable": retryable},
        needs_human=True,
    )


@lru_cache(maxsize=1)
def load_router_prompt() -> str:
    if not PROMPT_PATH.exists():
        raise LLMError(f"Prompt 文件缺失: {PROMPT_PATH}", code="PROMPT_MISSING")
    return PROMPT_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    key = (settings.deepseek_api_key or "").strip()
    base = (settings.deepseek_base_url or "https://api.deepseek.com").strip()
    if not key or key.startswith("PASTE"):
        # 本地 Ollama 不校验 key，BASE_URL 切过去时给个占位值即可
        if "localhost" in base or "127.0.0.1" in base:
            key = "ollama"
        else:
            raise LLMError("DEEPSEEK_API_KEY 未配置或仍是占位符", code="MISSING_API_KEY")
    return OpenAI(api_key=key, base_url=base, timeout=TIMEOUT, max_retries=0)


def complete(messages: list[dict], *, model: str = MODEL) -> str:
    try:
        resp = _client().chat.completions.create(
            model=model, messages=messages, temperature=0.0, timeout=TIMEOUT
        )
    except APITimeoutError as exc:
        raise LLMError(f"LLM 超时: {exc}", code="TIMEOUT", retryable=True) from exc
    except APIStatusError as exc:
        code = "AUTH_ERROR" if exc.status_code in (401, 403) else "API_ERROR"
        raise LLMError(
            f"LLM 返回 {exc.status_code}: {exc.message}",
            code=code,
            retryable=exc.status_code == 429 or exc.status_code >= 500,
        ) from exc
    except APIConnectionError as exc:
        raise LLMError(f"LLM 连接失败: {exc}", code="CONNECTION_ERROR", retryable=True) from exc
    except LLMError:
        raise
    except Exception as exc:  # 兜底：任何 SDK 异常都转结构化错误
        raise LLMError(f"LLM 调用异常: {exc}", code="API_ERROR") from exc

    content = (resp.choices[0].message.content or "").strip()
    if not content:
        raise LLMError("LLM 返回空内容", code="EMPTY_RESPONSE", retryable=True)
    return content


def parse_decision(raw: str) -> IntentDecision:
    text = (raw or "").strip()
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        obj = _OBJECT_RE.search(text)
        if not obj:
            raise ValueError("输出中未找到 JSON 对象")
        text = obj.group(0)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层必须是对象")
    return IntentDecision.model_validate(data)


@lru_cache(maxsize=1)
def get_langfuse():
    if not settings.langfuse_enabled:
        return None
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        return None
    try:
        from langfuse import Langfuse

        return Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception:
        return None


@contextmanager
def trace_span(name: str, *, as_type: str = "span", **fields):
    """Langfuse 包装：不可用或上报异常时静默降级为 None。"""
    global _LAST_TRACE_ID
    client = get_langfuse()
    cm = span = None
    if client is not None:
        try:
            cm = client.start_as_current_observation(as_type=as_type, name=name, **fields)
            span = cm.__enter__()
            trace_id = getattr(span, "trace_id", None)
            if trace_id:
                _LAST_TRACE_ID = trace_id
        except Exception:
            cm = span = None
    try:
        yield span
    finally:
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                pass


def last_trace_id() -> str | None:
    return _LAST_TRACE_ID


def trace_url() -> str | None:
    if not _LAST_TRACE_ID:
        return None
    return f"{settings.langfuse_host.rstrip('/')}/trace/{_LAST_TRACE_ID}"


def flush() -> None:
    client = get_langfuse()
    if client is not None:
        try:
            client.flush()
        except Exception:
            pass


REPAIR_PROMPT = (
    "你的上一条输出无法通过 JSON Schema 校验：{error}\n"
    "请只输出一个合法 JSON 对象，字段为 intent/confidence/need_clarify/sub_tasks，不要任何解释或代码块。"
)


def classify_intent(text: str) -> IntentDecision:
    """意图识别：调用 LLM → Schema 校验 → 一次修复重试 → 失败降级。"""
    messages = [
        {"role": "system", "content": load_router_prompt()},
        {"role": "user", "content": text},
    ]
    last_error = ""
    for attempt in range(MAX_REPAIR + 1):
        span_name = "router_llm" if attempt == 0 else "router_llm_repair"
        try:
            with trace_span(
                span_name,
                as_type="generation",
                model=MODEL,
                input=messages,
                metadata={"attempt": attempt + 1},
            ) as span:
                raw = complete(messages)
                if span is not None:
                    try:
                        span.update(output=raw)
                    except Exception:
                        pass
        except LLMError as exc:
            return _degraded(exc.code, exc.message, exc.retryable)

        try:
            return parse_decision(raw)
        except (ValueError, ValidationError) as exc:
            last_error = str(exc)[:500]
            messages = messages + [
                {"role": "assistant", "content": raw[:2000]},
                {"role": "user", "content": REPAIR_PROMPT.format(error=last_error)},
            ]

    return _degraded("INVALID_JSON", f"输出未通过 Schema 校验（已重试一次）: {last_error}")
