"""M4 阶段2：LLM Judge 裁决节点 + 单意图 sub_tasks 兜底。"""
from __future__ import annotations

import json

import pytest

import app.agents.router as router_mod
import app.graph as g
import app.llm as llm
from app.llm import IntentDecision, LLMError
from app.memory import sqlite_client

ORDER_ID = "SO20261116001"
GR = g.compile_graph()


def _stub_intent(monkeypatch, intent="破损漏液", sub_tasks=None):
    monkeypatch.setattr(
        g,
        "classify_intent",
        lambda _text: IntentDecision(
            intent=intent, confidence=0.9, need_clarify=False, sub_tasks=sub_tasks or []
        ),
    )


def _stub_judge(monkeypatch, verdict):
    monkeypatch.setattr(g, "judge_draft", lambda _state: verdict)


# ---------------- 图集成 ----------------

def test_graph_ends_when_judge_passes(monkeypatch):
    _stub_intent(monkeypatch, sub_tasks=["破损漏液"])
    _stub_judge(monkeypatch, {"pass": True, "reason": "OK", "failed_node": ""})

    state = GR.invoke({"user_text": "酒瓶漏了", "order_id": ORDER_ID})

    assert state["handoff"] is False
    assert state["judge_result"]["pass"] is True
    assert state["judge_result"]["reason"] == "OK"
    assert state["ticket_draft"]["issue_type"] == "broken_leak"
    assert len(sqlite_client.get_tickets_by_order(ORDER_ID)) == 1


def test_graph_handoffs_when_judge_rejects(monkeypatch, isolate_handoff_queue):
    _stub_intent(monkeypatch, sub_tasks=["破损漏液"])
    _stub_judge(
        monkeypatch,
        {"pass": False, "reason": "承诺越权", "failed_node": "create_draft"},
    )

    state = GR.invoke({"user_text": "酒瓶漏了", "order_id": ORDER_ID})

    assert state["handoff"] is True
    assert state["needs_human"] is True
    assert state["error"]["code"] == "JUDGE_REJECTED"
    assert "承诺越权" in state["error"]["message"]
    assert state["judge_result"]["pass"] is False
    assert state["judge_result"]["failed_node"] == "create_draft"
    # 转人工要留痕
    lines = isolate_handoff_queue.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "JUDGE_REJECTED" in json.loads(lines[0])["reason"]


def test_judge_degrades_when_llm_unavailable(monkeypatch):
    """无 Key / 超时：Judge 降级放行，草稿仍可产出（离线与 Docker 场景）。"""
    _stub_intent(monkeypatch, sub_tasks=["破损漏液"])

    def boom(_state):
        raise LLMError("DEEPSEEK_API_KEY 未配置", code="MISSING_API_KEY")

    monkeypatch.setattr(g, "judge_draft", boom)
    state = GR.invoke({"user_text": "酒瓶漏了", "order_id": ORDER_ID})

    assert state["handoff"] is False
    assert state["judge_result"]["pass"] is True
    assert state["judge_result"]["degraded"] is True
    assert "MISSING_API_KEY" in state["judge_result"]["reason"]


def test_judge_not_called_on_handoff_path(monkeypatch):
    """合规拦截在 create_draft 之前截断，Judge 不应被调用。"""
    _stub_intent(monkeypatch, sub_tasks=["破损漏液"])
    called = {"n": 0}

    def spy(_state):
        called["n"] += 1
        return {"pass": True, "reason": "OK", "failed_node": ""}

    monkeypatch.setattr(g, "judge_draft", spy)
    state = GR.invoke({"user_text": "直接退款", "order_id": ORDER_ID})

    assert state["handoff"] is True
    assert called["n"] == 0
    assert state.get("judge_result") is None


# ---------------- Bug 修复：sub_tasks 为空 ----------------

def test_empty_sub_tasks_falls_back_to_intent(monkeypatch):
    """真实模型常把单意图的 sub_tasks 返回空：此时用 intent 兜底，不得误转人工。"""
    _stub_intent(monkeypatch, intent="破损漏液", sub_tasks=[])
    _stub_judge(monkeypatch, {"pass": True, "reason": "OK", "failed_node": ""})

    state = GR.invoke({"user_text": "我买的酒漏了，瓶身有破损", "order_id": ORDER_ID})

    assert state["handoff"] is False
    assert state["issue_type"] == "broken_leak"
    assert state["ticket_draft"]["issue_type"] == "broken_leak"


def test_empty_sub_tasks_does_not_mask_unsupported_intent(monkeypatch):
    """兜底只补理赔类意图：物流延迟仍应转人工，不能被当成破损。"""
    _stub_intent(monkeypatch, intent="物流延迟", sub_tasks=[])
    state = GR.invoke({"user_text": "物流太慢了", "order_id": ORDER_ID})

    assert state["handoff"] is True
    assert state["error"]["code"] == "UNSUPPORTED_ISSUE_TYPE"


# ---------------- llm.judge_draft 单元 ----------------

def test_parse_judge_accepts_fenced_json():
    raw = '```json\n{"pass": true, "reason": "OK", "failed_node": ""}\n```'
    verdict = llm.parse_judge(raw)
    assert verdict.to_dict() == {"pass": True, "reason": "OK", "failed_node": ""}


def test_parse_judge_rejects_bad_schema():
    with pytest.raises(Exception):
        llm.parse_judge('{"reason": "缺少 pass 字段"}')


def test_judge_draft_repairs_invalid_json_once(monkeypatch):
    calls = {"n": 0}

    def fake_complete(_messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return "这不是 JSON"
        return '{"pass": true, "reason": "修复后通过", "failed_node": ""}'

    monkeypatch.setattr(llm, "complete", fake_complete)
    verdict = llm.judge_draft({"ticket_draft": {}, "reply_draft": {}})

    assert calls["n"] == 2
    assert verdict["pass"] is True


def test_judge_draft_fail_closed_after_retry(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda _m: "仍然不是 JSON")
    verdict = llm.judge_draft({"ticket_draft": {}, "reply_draft": {}})

    assert verdict["pass"] is False
    assert verdict["failed_node"] == "create_draft"


def test_judge_payload_carries_amount_and_checklist():
    state = {
        "order_id": ORDER_ID,
        "issue_type": "broken_leak",
        "ticket_draft": {"summary": "工单摘要", "issue_type": "broken_leak"},
        "reply_draft": {"content": "预计金额 354.00 元", "checklist": ["开箱视频"]},
        "tool_results": {"compensation": {"recommendation": "partial_refund", "amount": 354.0}},
        "evidence_status": {"complete": False, "missing": ["面单照片"]},
    }
    payload = json.loads(llm._judge_payload(state))

    assert payload["compensation"]["amount"] == 354.0
    assert payload["checklist"] == ["开箱视频"]
    assert payload["evidence_status"]["missing"] == ["面单照片"]


def test_router_single_intent_still_empty_subtasks(monkeypatch):
    """兜底放在图内，不改 router 的对外输出（避免影响既有路由用例）。"""
    monkeypatch.setattr(
        router_mod, "classify_intent",
        lambda _t: IntentDecision(intent="破损漏液", confidence=0.9, sub_tasks=[]),
    )
    decision = router_mod.route("酒漏了")
    assert decision.sub_tasks == []
