"""M3 阶段3：多意图（理赔 + 酒知识）草稿合并。

主链路仍走理赔取证 → 工单草稿；知识检索结果只追加到 reply_draft.content 末尾，
不改动 ticket_draft 的补证清单与赔付建议。
"""
from __future__ import annotations

from app.graph import compile_graph

ORDER_ID = "SO20261116001"
MIXED_TEXT = "我买的酒漏了，还少一瓶，这酒怎么保存？"
EVIDENCE_COMPLETE = ["开箱视频", "面单照片", "破损部位照片"]

GR = compile_graph()


def test_multi_intent_merges_knowledge_into_claim_reply():
    """混装诉求：工单非空（走理赔），回复同时含补证清单与酒知识解答。"""
    state = GR.invoke({"user_text": MIXED_TEXT, "order_id": ORDER_ID})

    # 断言1：确实走了理赔链路
    ticket = state.get("ticket_draft")
    assert ticket is not None, "混装诉求仍应产出工单草稿"
    assert ticket.get("need_human_review") is True
    assert "证据" in ticket.get("summary", "")

    # 断言2：回复里既有补证清单，也有酒知识解答
    content = state["reply_draft"]["content"]
    assert "【需要您补充的材料】" in content
    assert "【酒知识解答】" in content
    assert "避光" in content

    # 知识块追加在末尾，且不影响原有收尾话术
    assert content.rstrip().endswith(content.rstrip().split("\n")[-1])
    assert content.index("【酒知识解答】") > content.index("【需要您补充的材料】")
    # State 同步写回 knowledge_result，便于 CLI/后续节点直接取
    assert state.get("knowledge_result")


def test_multi_intent_evidence_complete_keeps_compensation():
    """证据齐全的混装诉求：赔付建议不变（354.00），同时追加知识解答。"""
    state = GR.invoke({"user_text": MIXED_TEXT, "order_id": ORDER_ID, "evidence": EVIDENCE_COMPLETE})

    ticket = state["ticket_draft"]
    assert ticket["compensation"]["recommendation"] == "partial_refund"
    assert ticket["compensation"]["amount"] == 354.0

    content = state["reply_draft"]["content"]
    assert "354.00" in content
    assert "人工审核" in content
    assert "【酒知识解答】" in content
    assert "【需要您补充的材料】" not in content


def test_pure_claim_text_has_no_knowledge_block():
    """纯理赔文本（无知识诉求词）：回复内容与合并前一致，不追加知识块。"""
    state = GR.invoke({"user_text": "我买的酒漏了，瓶身有破损", "order_id": ORDER_ID})

    content = state["reply_draft"]["content"]
    assert "【酒知识解答】" not in content
    assert "【需要您补充的材料】" in content
    assert not state.get("knowledge_result")


def test_knowledge_block_is_numbered_and_capped():
    """知识块为编号列表，最多 2 条，不出现兜底话术。"""
    state = GR.invoke({"user_text": MIXED_TEXT, "order_id": ORDER_ID})
    content = state["reply_draft"]["content"]

    block = content.split("【酒知识解答】", 1)[1].strip()
    lines = [ln for ln in block.split("\n") if ln.strip()]
    assert 1 <= len(lines) <= 2, f"知识块应为 1~2 条编号列表，实际 {lines}"
    assert lines[0].startswith("1. ")
    assert "关于该问题我暂时没有找到相关说明" not in block


# ---------------------------------------------------------------------------
# 阶段4：合规拦截优先级高于知识检索（退款诉求 → 整条链路截断）
# ---------------------------------------------------------------------------

REFUND_WITH_KNOWLEDGE = "直接给我退款，另外这酒怎么保存？"


def test_refund_with_knowledge_handoffs_and_skips_knowledge_agent(monkeypatch):
    """退款 + 知识混装：立即 handoff，COMPLIANCE_REFUND，且 knowledge_agent 绝不执行。"""
    import app.graph as g

    def _boom(_state):
        raise AssertionError("退款诉求不得执行 knowledge_agent（不允许给退款用户解答知识）")

    # 必须在 compile 之前替换：节点绑定的是编译时的函数对象
    monkeypatch.setattr(g, "knowledge_agent", _boom)
    state = g.compile_graph().invoke({"user_text": REFUND_WITH_KNOWLEDGE, "order_id": ORDER_ID})

    assert state["handoff"] is True
    assert state["needs_human"] is True
    assert state["error"]["code"] == "COMPLIANCE_REFUND"
    # 链路截断：不产知识、不产工单/回复草稿
    assert not state.get("knowledge_result")
    assert state.get("ticket_draft") is None
    assert state.get("reply_draft") is None


def test_refund_with_knowledge_does_not_call_llm(monkeypatch):
    """合规判定在 LLM 之前完成：退款 + 知识诉求不得触发意图识别。"""
    import app.graph as g

    def _boom(_text):
        raise AssertionError("合规拦截路径不应调用 LLM")

    monkeypatch.setattr(g, "classify_intent", _boom)
    state = g.compile_graph().invoke({"user_text": REFUND_WITH_KNOWLEDGE, "order_id": ORDER_ID})

    assert state["handoff"] is True
    assert state["error"]["code"] == "COMPLIANCE_REFUND"


def test_knowledge_only_still_not_intercepted(monkeypatch):
    """纯知识咨询（无退款）不受合规模块影响，仍走 knowledge_agent。"""
    import app.graph as g
    from app.llm import IntentDecision

    monkeypatch.setattr(
        g,
        "classify_intent",
        lambda _text: IntentDecision(intent="储存饮用咨询", confidence=0.9, need_clarify=False, sub_tasks=["储存饮用咨询"]),
    )
    state = g.compile_graph().invoke({"user_text": "这酒怎么保存", "order_id": ORDER_ID})

    assert state["handoff"] is False
    assert "避光平放" in state["knowledge_result"]
