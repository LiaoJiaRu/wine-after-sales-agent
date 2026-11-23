"""M4 阶段3：Judge 失败局部重跑、重试上限与多意图缺证合并。"""
from __future__ import annotations

import json

import app.graph as g
import app.llm as llm
from app.llm import IntentDecision
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


def _scripted_judge(monkeypatch, script):
    """按脚本依次返回裁决，超出脚本后沿用最后一条。"""
    calls = {"n": 0}

    def fake(_state):
        idx = min(calls["n"], len(script) - 1)
        calls["n"] += 1
        return dict(script[idx])

    monkeypatch.setattr(g, "judge_draft", fake)
    return calls


def test_retry_once_then_pass(monkeypatch):
    _stub_intent(monkeypatch, sub_tasks=["破损漏液"])
    calls = _scripted_judge(
        monkeypatch,
        [
            {"pass": False, "reason": "补证清单不完整", "failed_node": "create_draft"},
            {"pass": True, "reason": "OK", "failed_node": ""},
        ],
    )

    state = GR.invoke({"user_text": "酒瓶漏了", "order_id": ORDER_ID})

    assert calls["n"] == 2
    assert state["retry_count"] == 1
    assert state["handoff"] is False
    assert state["judge_result"]["pass"] is True
    assert state["ticket_draft"]["draft_id"]
    # 复核意见已注入下一轮工单上下文
    assert "质检复核" in state["ticket_draft"]["summary"]
    assert "补证清单不完整" in state["ticket_draft"]["summary"]


def test_three_failures_handoff(monkeypatch, isolate_handoff_queue):
    _stub_intent(monkeypatch, sub_tasks=["破损漏液"])
    calls = _scripted_judge(
        monkeypatch,
        [{"pass": False, "reason": "承诺越权", "failed_node": "create_draft"}],
    )

    state = GR.invoke({"user_text": "酒瓶漏了", "order_id": ORDER_ID})

    assert calls["n"] == 3  # 首次 + 2 次重试
    assert state["retry_count"] == 2
    assert state["handoff"] is True
    assert state["needs_human"] is True
    assert state["error"]["code"] == "JUDGE_REJECTED"
    assert "已重试 2 次" in state["error"]["message"]

    lines = isolate_handoff_queue.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "JUDGE_REJECTED" in json.loads(lines[0])["reason"]


def test_failed_node_fetch_evidence_reruns_fetch(monkeypatch):
    _stub_intent(monkeypatch, sub_tasks=["破损漏液"])
    real_fetch = g._fetch_claim
    fetch_calls = {"n": 0}

    def counting_fetch(*args, **kwargs):
        fetch_calls["n"] += 1
        return real_fetch(*args, **kwargs)

    monkeypatch.setattr(g, "_fetch_claim", counting_fetch)
    _scripted_judge(
        monkeypatch,
        [
            {"pass": False, "reason": "金额与计算结果不一致", "failed_node": "fetch_evidence"},
            {"pass": True, "reason": "OK", "failed_node": ""},
        ],
    )

    state = GR.invoke(
        {"user_text": "酒瓶漏了", "order_id": ORDER_ID,
         "evidence": ["开箱视频", "面单照片", "破损部位照片"]}
    )

    assert fetch_calls["n"] == 2  # 初次取证 + 一次局部重跑
    assert state["retry_count"] == 1
    assert state["handoff"] is False
    assert state["tool_results"]["compensation"]["amount"] == 354.0


def test_pass_first_time_keeps_retry_count_zero(monkeypatch):
    _stub_intent(monkeypatch, sub_tasks=["破损漏液"])
    calls = _scripted_judge(monkeypatch, [{"pass": True, "reason": "OK", "failed_node": ""}])

    state = GR.invoke({"user_text": "酒瓶漏了", "order_id": ORDER_ID})

    assert calls["n"] == 1
    assert state["retry_count"] == 0
    assert state["judge_feedback"] == ""


def test_retry_is_bounded_no_recursion_error(monkeypatch):
    """始终失败也只能跑 3 次 Judge，不得触发 GraphRecursionError。"""
    _stub_intent(monkeypatch, sub_tasks=["破损漏液"])
    calls = _scripted_judge(
        monkeypatch, [{"pass": False, "reason": "一直不通过", "failed_node": "create_draft"}]
    )

    state = GR.invoke({"user_text": "酒瓶漏了", "order_id": ORDER_ID})

    assert calls["n"] == g.MAX_JUDGE_RETRY + 1
    assert state["handoff"] is True
    assert g.RECURSION_LIMIT >= 2 * (g.MAX_JUDGE_RETRY + 1)


def test_failed_node_fetch_evidence_bounded_no_recursion_error(monkeypatch, isolate_handoff_queue):
    """多意图连续驳回取证也必须完成两次重跑并转人工，不得先耗尽图步数。"""
    _stub_intent(
        monkeypatch,
        intent="多意图",
        sub_tasks=["破损漏液", "少件错发", "储存饮用咨询"],
    )
    calls = _scripted_judge(
        monkeypatch,
        [{"pass": False, "reason": "持续驳回取证", "failed_node": "fetch_evidence"}],
    )

    state = GR.invoke(
        {"user_text": "我买的酒漏了，还少一瓶，这酒怎么保存", "order_id": ORDER_ID},
        config={"recursion_limit": g.RECURSION_LIMIT},
    )

    assert calls["n"] == g.MAX_JUDGE_RETRY + 1
    assert state["retry_count"] == g.MAX_JUDGE_RETRY
    assert state["handoff"] is True
    assert state["error"]["code"] == "JUDGE_REJECTED"
    assert g._route_after_judge(
        {"retry_count": g.MAX_JUDGE_RETRY, "judge_result": {"pass": False}}
    ) == "handoff_human"


def test_multi_intent_evidence_merged_everywhere(monkeypatch):
    """漏了 + 少件 + 保存：State / 工单 / 回复清单必须是同一份合并后的缺证列表。"""
    _stub_intent(
        monkeypatch,
        intent="多意图",
        sub_tasks=["破损漏液", "少件错发", "储存饮用咨询"],
    )
    calls = _scripted_judge(monkeypatch, [{"pass": True, "reason": "OK", "failed_node": ""}])

    state = GR.invoke(
        {"user_text": "我买的酒漏了，还少一瓶，这酒怎么保存？", "order_id": ORDER_ID}
    )

    expected = ["开箱视频", "面单照片", "破损部位照片", "整箱称重照片"]
    assert calls["n"] == 1
    assert state["evidence_status"]["complete"] is False
    assert state["evidence_status"]["missing"] == expected
    assert state["ticket_draft"]["evidence_status"]["missing"] == expected
    assert state["reply_draft"]["checklist"] == expected
    assert "整箱称重照片" in state["reply_draft"]["content"]

    payload = json.loads(llm._judge_payload(state))
    assert payload["evidence_status"]["missing"] == expected
    assert payload["checklist"] == expected

    # 无证据时少件走补证（不承诺补发），但工单 summary 必须体现少件，知识块仍在末尾
    assert "补发" not in state["reply_draft"]["content"]
    assert "少件" in state["ticket_draft"]["summary"]
    assert "【酒知识解答】" in state["reply_draft"]["content"]


def test_single_intent_ticket_evidence_unchanged(monkeypatch):
    """单理赔任务：工单证据状态与 tool_results 一致，不被合并逻辑改写。"""
    _stub_intent(monkeypatch, sub_tasks=["破损漏液"])
    _scripted_judge(monkeypatch, [{"pass": True, "reason": "OK", "failed_node": ""}])

    state = GR.invoke({"user_text": "酒瓶漏了", "order_id": ORDER_ID})

    assert state["ticket_draft"]["evidence_status"] == state["tool_results"]["evidence"]
    assert len(sqlite_client.get_tickets_by_order(ORDER_ID)) == 1
