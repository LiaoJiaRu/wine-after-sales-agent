"""M3 补齐阶段2：多理赔子任务取证结果合并进同一张工单与同一段回复。"""
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
            intent="多意图", confidence=0.92, need_clarify=False, sub_tasks=MIXED_SUBTASKS
        ),
    )


def _invoke_mixed(monkeypatch, evidence=None):
    _stub_multi_intent(monkeypatch)
    payload = {"user_text": MIXED_TEXT, "order_id": ORDER_ID}
    if evidence:
        payload["evidence"] = evidence
    return GR.invoke(payload)


def test_three_tasks_two_claims_success(monkeypatch):
    """3 个任务对象，两个理赔任务各自成功，结论互不覆盖。"""
    state = _invoke_mixed(monkeypatch)
    tasks = {t["task_type"]: t for t in state["task_outputs"]}

    assert len(state["task_outputs"]) == 3
    assert tasks["破损漏液"]["status"] == "success"
    assert tasks["少件错发"]["status"] == "success"
    assert tasks["破损漏液"]["result"]["issue_type"] == "broken_leak"
    assert tasks["少件错发"]["result"]["issue_type"] == "missing_item"
    # 各自独立的结果对象，不是同一份引用
    assert tasks["破损漏液"]["result"] is not tasks["少件错发"]["result"]


def test_ticket_summary_mentions_secondary_claim(monkeypatch):
    """工单 summary：主诉为破损，且追加少件提示。"""
    state = _invoke_mixed(monkeypatch)
    summary = state["ticket_draft"]["summary"]

    assert "破损漏液" in summary
    assert "少件" in summary
    assert "建议一并处理" in summary
    # 工单 schema 与只产草稿边界不变
    assert state["ticket_draft"]["issue_type"] == "broken_leak"
    assert state["ticket_draft"]["need_human_review"] is True


def test_reply_checklist_merges_two_claims(monkeypatch):
    """回复补证清单合并：破损 3 项 + 少件专属的整箱称重照片。"""
    state = _invoke_mixed(monkeypatch)
    reply = state["reply_draft"]
    content = reply["content"]

    assert "【需要您补充的材料】" in content
    assert "破损部位照片" in content
    assert "整箱称重照片" in content
    assert len(reply["checklist"]) >= 3
    assert reply["checklist"] == ["开箱视频", "面单照片", "破损部位照片", "整箱称重照片"]


def test_knowledge_block_still_appended_last(monkeypatch):
    """知识合并依旧生效，且位于回复末尾。"""
    state = _invoke_mixed(monkeypatch)
    content = state["reply_draft"]["content"]

    assert "【酒知识解答】" in content
    assert "避光平放" in content
    assert content.rstrip().endswith(content.rstrip().split("\n")[-1])
    assert "【酒知识解答】" in content[content.index("【酒知识解答】"):]
    assert "感谢您的理解与配合" in content[: content.index("【酒知识解答】")]


def test_all_evidence_complete_keeps_single_claim_text(monkeypatch):
    """两个理赔任务证据都齐全时，不再出现补证清单，赔付金额仍按主诉算。"""
    state = _invoke_mixed(monkeypatch, evidence=["开箱视频", "面单照片", "破损部位照片", "整箱称重照片"])
    content = state["reply_draft"]["content"]

    assert "【需要您补充的材料】" not in content
    assert "【证据核验】" in content
    assert state["reply_draft"]["checklist"] == []
    assert state["tool_results"]["compensation"]["recommendation"] == "partial_refund"


def test_secondary_reship_appended_to_reply(monkeypatch):
    """证据齐全时，次要诉求（少件 reship）的处置动作必须进入回复正文。"""
    state = _invoke_mixed(monkeypatch, evidence=["开箱视频", "面单照片", "破损部位照片", "整箱称重照片"])
    content = state["reply_draft"]["content"]

    # 主诉结论仍在
    assert "354.00 元" in content
    assert "人工审核" in content
    # 次要诉求的补发动作已合并
    assert "少件" in content
    assert "补发" in content
    # 位置合理：处理进度之后、保存建议之前，且不与主诉话术重复
    assert content.index("【处理进度】") < content.index("补发") < content.index("【保存建议】")
    assert content.count("预计金额") == 1


def test_secondary_reship_absent_for_single_claim(monkeypatch):
    """单理赔任务不追加少件话术（防串话）。"""
    state = GR.invoke({"user_text": "我买的酒漏了", "order_id": ORDER_ID,
                       "evidence": ["开箱视频", "面单照片", "破损部位照片"]})
    content = state["reply_draft"]["content"]

    assert "354.00 元" in content
    assert "少件" not in content
    assert "补发" not in content
