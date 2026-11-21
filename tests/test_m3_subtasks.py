"""M3 补齐阶段1：sub_tasks → task_outputs 任务对象化（向后兼容验证）。"""
from __future__ import annotations

import app.graph as g
from app.llm import IntentDecision

ORDER_ID = "SO20261116001"
MIXED_TEXT = "我买的酒漏了，还少一瓶，这酒怎么保存？"
MIXED_SUBTASKS = ["破损漏液", "少件错发", "储存饮用咨询"]

GR = g.compile_graph()


def _stub_multi_intent(monkeypatch):
    monkeypatch.setattr(
        g,
        "classify_intent",
        lambda _text: IntentDecision(
            intent="多意图",
            confidence=0.92,
            need_clarify=False,
            sub_tasks=MIXED_SUBTASKS,
        ),
    )


def _by_type(state):
    return {t["task_type"]: t for t in state["task_outputs"]}


def test_multi_intent_builds_three_task_objects(monkeypatch):
    """混装诉求 → task_outputs 长度为 3，且三类子任务各自成对象。"""
    _stub_multi_intent(monkeypatch)
    state = GR.invoke({"user_text": MIXED_TEXT, "order_id": ORDER_ID})

    outputs = state["task_outputs"]
    assert len(outputs) == 3
    assert [t["task_type"] for t in outputs] == MIXED_SUBTASKS
    assert {t["status"] for t in outputs} == {"success"}


def test_claim_tasks_have_non_empty_results(monkeypatch):
    """破损、少件两个理赔子任务都必须真正取到证（result 非空）。"""
    _stub_multi_intent(monkeypatch)
    state = GR.invoke({"user_text": MIXED_TEXT, "order_id": ORDER_ID})
    tasks = _by_type(state)

    broken = tasks["破损漏液"]
    assert broken["status"] == "success"
    assert broken["result"] and broken["result"]["compensation"]
    assert broken["result"]["order"]["order_id"] == ORDER_ID

    missing = tasks["少件错发"]
    assert missing["status"] == "success"
    assert missing["result"] and missing["result"]["order"]
    assert missing["result"]["issue_type"] == "missing_item"


def test_knowledge_task_result_filled(monkeypatch):
    """知识类子任务由 knowledge_agent 填充，内容含保存要点。"""
    _stub_multi_intent(monkeypatch)
    state = GR.invoke({"user_text": MIXED_TEXT, "order_id": ORDER_ID})
    task = _by_type(state)["储存饮用咨询"]

    assert task["status"] == "success"
    assert "避光平放" in task["result"]["knowledge_result"]


def test_tool_results_still_from_first_claim_task(monkeypatch):
    """兼容：tool_results 仍取第一个理赔子任务（破损）的结果，字段结构不变。"""
    _stub_multi_intent(monkeypatch)
    state = GR.invoke({"user_text": MIXED_TEXT, "order_id": ORDER_ID})

    assert state["issue_type"] == "broken_leak"
    for key in ("order", "shipment", "batch", "evidence", "compensation", "policy_hits", "sub_tasks"):
        assert key in state["tool_results"]
    assert state["ticket_draft"] and state["reply_draft"]


def test_single_intent_keeps_legacy_behavior():
    """单意图不生成任务对象，老口径（空 task_outputs + 原 tool_results）逐项不变。"""
    state = GR.invoke({"user_text": "我买的酒漏了", "order_id": ORDER_ID})

    assert state["task_outputs"] == []
    assert state["issue_type"] == "broken_leak"
    assert state["tool_results"]["compensation"]["recommendation"] == "supplement_evidence"
    assert state["ticket_draft"]["need_human_review"] is True


def test_claim_task_failure_marks_failed_and_handoffs(monkeypatch):
    """任一理赔子任务取证失败 → 该任务 failed，整条链路转人工（保持 M2 降级语义）。"""
    _stub_multi_intent(monkeypatch)
    state = GR.invoke({"user_text": MIXED_TEXT, "order_id": "SO_NOT_EXIST"})

    assert state["handoff"] is True
    assert state["error"]["code"] == "NOT_FOUND"
    failed = [t for t in state["task_outputs"] if t["status"] == "failed"]
    assert len(failed) == 2
    assert all(t["result"]["error"]["code"] == "NOT_FOUND" for t in failed)
