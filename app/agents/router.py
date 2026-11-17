from __future__ import annotations

from app.llm import IntentDecision, classify_intent, trace_span


def route(text: str) -> IntentDecision:
    """对外入口：空输入返回结构化错误，其余交给 LLM 路由，整体挂一条 Langfuse trace。"""
    text = (text or "").strip()
    if not text:
        return IntentDecision(
            intent="其他",
            confidence=0.0,
            need_clarify=True,
            sub_tasks=[],
            error={"code": "EMPTY_INPUT", "message": "输入为空，请提供用户原话", "retryable": False},
            needs_human=True,
        )

    try:
        with trace_span("router_intent", as_type="span", input=text, metadata={"source": "cli"}) as span:
            decision = classify_intent(text)
            if span is not None:
                try:
                    span.update(output=decision.model_dump(exclude_none=True))
                except Exception:
                    pass
        return decision
    except Exception as exc:  # 任何未预期异常都不允许让 CLI 崩掉
        return IntentDecision(
            intent="其他",
            confidence=0.0,
            need_clarify=True,
            sub_tasks=[],
            error={"code": "INTERNAL_ERROR", "message": f"路由内部异常: {exc}", "retryable": False},
            needs_human=True,
        )
