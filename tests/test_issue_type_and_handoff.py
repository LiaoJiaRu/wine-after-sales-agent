"""M3 遗留缺陷修复：意图误判（UNSUPPORTED_ISSUE_TYPE）与第 9 个工具（转人工写队列）。"""
from __future__ import annotations

import json

import app.graph as g
from app.llm import IntentDecision
from app.tools.write import write_handoff_queue

ORDER_ID = "SO20261116001"
GR = g.compile_graph()


def _stub(monkeypatch, intent, sub_tasks):
    monkeypatch.setattr(
        g,
        "classify_intent",
        lambda _text: IntentDecision(
            intent=intent, confidence=0.9, need_clarify=False, sub_tasks=sub_tasks
        ),
    )


# ---------------------------------------------------------------------------
# 一、意图误判：非理赔意图不再被当成破损漏液算 354 元
# ---------------------------------------------------------------------------
def test_derive_issue_type_returns_empty_for_non_claim():
    assert g._derive_issue_type(["物流延迟"]) == ""
    assert g._derive_issue_type(["真伪争议"]) == ""
    assert g._derive_issue_type([]) == ""
    # 理赔类仍正常映射
    assert g._derive_issue_type(["破损漏液"]) == "broken_leak"
    assert g._derive_issue_type(["少件错发"]) == "missing_item"


def test_logistics_delay_handoffs_without_compensation(monkeypatch):
    _stub(monkeypatch, "物流延迟", ["物流延迟"])
    state = GR.invoke({"user_text": "物流太慢了，怎么还没到？", "order_id": ORDER_ID})

    assert state["handoff"] is True
    assert state["needs_human"] is True
    assert state["error"]["code"] == "UNSUPPORTED_ISSUE_TYPE"

    # 不出工单、不出回复、不产赔付建议（尤其不能出现 354）
    assert state.get("ticket_draft") is None
    assert state.get("reply_draft") is None
    tool_results = state.get("tool_results") or {}
    assert "compensation" not in tool_results
    assert "354" not in json.dumps(state, ensure_ascii=False, default=str)


def test_fake_wine_handoffs(monkeypatch):
    _stub(monkeypatch, "真伪争议", ["真伪争议"])
    state = GR.invoke({"user_text": "这酒是假的吧？", "order_id": ORDER_ID})

    assert state["handoff"] is True
    assert state["error"]["code"] == "UNSUPPORTED_ISSUE_TYPE"
    assert state.get("ticket_draft") is None


def test_broken_leak_still_compensates(monkeypatch):
    """回归：真正的破损漏液不受本次修复影响。"""
    _stub(monkeypatch, "破损漏液", ["破损漏液"])
    state = GR.invoke(
        {
            "user_text": "我买的酒漏了",
            "order_id": ORDER_ID,
            "evidence": ["开箱视频", "面单照片", "破损部位照片"],
        }
    )

    assert state["handoff"] is False
    assert state["tool_results"]["compensation"]["amount"] == 354.0
    assert state["ticket_draft"]["issue_type"] == "broken_leak"


# ---------------------------------------------------------------------------
# 二、第 9 个工具：转人工写队列
# ---------------------------------------------------------------------------
def test_write_handoff_queue_appends_records(tmp_path, monkeypatch):
    queue = tmp_path / "queue.jsonl"
    monkeypatch.setenv("HANDOFF_QUEUE_PATH", str(queue))

    first = write_handoff_queue(ORDER_ID, "测试原因一")
    write_handoff_queue(ORDER_ID, "测试原因二")

    assert first["queued"] is True
    lines = queue.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert records[0]["order_id"] == ORDER_ID
    assert records[0]["reason"] == "测试原因一"
    assert records[0]["status"] == "pending_approval"
    assert "T" in records[0]["ts"]


def test_handoff_node_writes_queue(monkeypatch, tmp_path):
    """图触发转人工后，队列文件确实多了一条记录。"""
    queue = tmp_path / "graph_queue.jsonl"
    monkeypatch.setenv("HANDOFF_QUEUE_PATH", str(queue))
    _stub(monkeypatch, "物流延迟", ["物流延迟"])

    GR.invoke({"user_text": "物流太慢了，怎么还没到？", "order_id": ORDER_ID})

    lines = queue.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["order_id"] == ORDER_ID
    assert "UNSUPPORTED_ISSUE_TYPE" in record["reason"]
    assert record["status"] == "pending_approval"


def test_refund_compliance_also_queued(monkeypatch, tmp_path):
    """合规拦截同样落队列，原因码可追溯。"""
    queue = tmp_path / "refund_queue.jsonl"
    monkeypatch.setenv("HANDOFF_QUEUE_PATH", str(queue))
    _stub(monkeypatch, "破损漏液", ["破损漏液"])

    state = GR.invoke({"user_text": "直接给我退款", "order_id": ORDER_ID})

    assert state["error"]["code"] == "COMPLIANCE_REFUND"
    record = json.loads(queue.read_text(encoding="utf-8").strip().splitlines()[0])
    assert "COMPLIANCE_REFUND" in record["reason"]
