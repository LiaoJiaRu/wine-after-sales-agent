"""M3 阶段2：政策检索强化 + 酒知识专业子 Agent（knowledge_agent）单测。

覆盖：
  1. search_policy 对酒知识类 query 优先命中 P-KNOW-001（权重高于理赔条款）；
  2. knowledge_agent 节点直接调用 → knowledge_result 含“避光平放”；
  3. 图路由：intent=储存饮用咨询 → 走 knowledge_agent → END，不生成工单草稿；
  4. 检索不到时写兜底话术。
"""
from __future__ import annotations

import pytest

from app.graph import KNOWLEDGE_QUERY_FALLBACK, compile_graph, knowledge_agent
from app.llm import IntentDecision
from app.tools.readonly import search_policy

ORDER_ID = "SO20261116001"
GR = compile_graph()


# ---------------------------------------------------------------------------
# 1. 政策检索：知识类 query 优先命中 P-KNOW 系列
# ---------------------------------------------------------------------------


def test_search_policy_knowledge_query_hits_p_know_001():
    hits = search_policy("怎么保存葡萄酒").hits
    assert hits, "知识类 query 应至少命中一条政策"
    assert hits[0].id == "P-KNOW-001", f"应优先命中 P-KNOW-001，实际 {[h.id for h in hits]}"


def test_search_policy_knowledge_beats_claim_clause():
    """知识类 query 下，P-KNOW 必须整体排在理赔条款之前（P-KNOW 共 2 条）。"""
    hits = search_policy("开瓶后怎么存放").hits
    assert [h.id for h in hits[:2]] == ["P-KNOW-001", "P-KNOW-002"], f"知识条款应占前两位，实际 {[h.id for h in hits]}"


def test_search_policy_claim_query_order_unchanged():
    """非知识类 query 不触发加权，仍按字符得分排序（回归保护）。"""
    hits = search_policy("破损漏液").hits
    assert hits and hits[0].id == "P-BREAK-001"


# ---------------------------------------------------------------------------
# 2. knowledge_agent 节点：拼装前 2 条知识
# ---------------------------------------------------------------------------


def test_knowledge_agent_node_returns_storage_advice():
    out = knowledge_agent({"user_text": "酒怎么保存", "sub_tasks": []})
    text = out["knowledge_result"]
    assert text, "knowledge_result 不应为空"
    assert "避光平放" in text
    assert text.count("\n") <= 1, "最多拼接 2 条知识"


def test_knowledge_agent_falls_back_to_sub_tasks():
    """user_text 为空时回退 sub_tasks 作为检索词。"""
    out = knowledge_agent({"user_text": "", "sub_tasks": ["储存饮用咨询"]})
    assert out["knowledge_result"]


def test_knowledge_agent_fallback_when_no_hit():
    out = knowledge_agent({"user_text": "量子力学怎么入门", "sub_tasks": []})
    assert out["knowledge_result"] == KNOWLEDGE_QUERY_FALLBACK


# ---------------------------------------------------------------------------
# 3. 图路由：储存饮用咨询 → knowledge_agent → END
# ---------------------------------------------------------------------------


def test_graph_routes_knowledge_intent_to_agent(monkeypatch):
    import app.graph as g

    monkeypatch.setattr(
        g,
        "classify_intent",
        lambda _text: IntentDecision(intent="储存饮用咨询", confidence=0.92, need_clarify=False, sub_tasks=["储存饮用咨询"]),
    )
    state = g.compile_graph().invoke({"user_text": "酒怎么保存", "order_id": ORDER_ID})

    assert state["intent"] == "储存饮用咨询"
    assert state["knowledge_result"]
    assert "避光平放" in state["knowledge_result"]
    # 知识路径不产出工单/回复草稿，也不转人工
    assert state["handoff"] is False
    assert state.get("ticket_draft") is None
    assert state.get("reply_draft") is None


def test_graph_claim_text_with_storage_words_still_claims(monkeypatch):
    """“漏了…还少一瓶，这酒怎么保存？”仍走理赔链路，知识只并入回复、不抢主链。"""
    import app.graph as g

    monkeypatch.setattr(
        g,
        "classify_intent",
        lambda _text: IntentDecision(intent="破损漏液", confidence=0.9, need_clarify=False, sub_tasks=["破损漏液"]),
    )
    state = g.compile_graph().invoke(
        {"user_text": "我买的酒漏了，还少一瓶，这酒怎么保存？", "order_id": ORDER_ID}
    )

    # 理赔主链不受影响：工单与回复草稿都要产出
    assert state["ticket_draft"] is not None
    assert state["reply_draft"] is not None
    # 阶段3：知识诉求以“并入回复”的方式满足，而不是把整条链路改成知识节点
    assert "【酒知识解答】" in state["reply_draft"]["content"]
