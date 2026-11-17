import pytest

from app.agents import router
from app.llm import LLMError, IntentDecision, classify_intent, parse_decision

VALID = '{"intent":"破损漏液","confidence":0.92,"need_clarify":false,"sub_tasks":[]}'


@pytest.fixture(autouse=True)
def disable_langfuse(monkeypatch):
    """单测不打真实 Langfuse，也不产生网络请求。"""
    monkeypatch.setattr("app.llm.get_langfuse", lambda: None)


def test_valid_json_no_repair(monkeypatch):
    calls = []

    def fake_complete(messages, **kwargs):
        calls.append(messages)
        return VALID

    monkeypatch.setattr("app.llm.complete", fake_complete)
    decision = classify_intent("收到酒瓶破了")
    assert decision.intent == "破损漏液"
    assert decision.confidence == 0.92
    assert len(calls) == 1


def test_invalid_json_triggers_one_repair(monkeypatch):
    outputs = iter(["这不是JSON", VALID])
    calls = []

    def fake_complete(messages, **kwargs):
        calls.append(messages)
        return next(outputs)

    monkeypatch.setattr("app.llm.complete", fake_complete)
    decision = classify_intent("收到酒瓶破了")
    assert decision.intent == "破损漏液"
    assert len(calls) == 2
    assert "JSON Schema" in calls[1][-1]["content"]


def test_api_failure_degrades_without_crash(monkeypatch):
    def boom(messages, **kwargs):
        raise LLMError("401 Unauthorized", code="AUTH_ERROR")

    monkeypatch.setattr("app.llm.complete", boom)
    decision = classify_intent("收到酒瓶破了")
    assert decision.intent == "其他"
    assert decision.needs_human is True
    assert decision.error["code"] == "AUTH_ERROR"


def test_bad_intent_enum_degrades(monkeypatch):
    monkeypatch.setattr(
        "app.llm.complete",
        lambda messages, **kwargs: '{"intent":"退款","confidence":0.9,"need_clarify":false,"sub_tasks":[]}',
    )
    decision = classify_intent("随便问问")
    assert decision.intent == "其他"
    assert decision.error["code"] == "INVALID_JSON"


def test_empty_input_returns_structured_error():
    decision = router.route("   ")
    assert isinstance(decision, IntentDecision)
    assert decision.needs_human is True
    assert decision.error["code"] == "EMPTY_INPUT"


def test_parse_decision_accepts_code_fence():
    decision = parse_decision('```json\n{"intent":"物流延迟","confidence":0.8}\n```')
    assert decision.intent == "物流延迟"
