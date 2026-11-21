"""M3 补齐阶段3：多任务并行链路的隔离性与异常边界。"""
from __future__ import annotations

import json

import app.graph as g
from app.llm import IntentDecision

ORDER_ID = "SO20261116001"
GR = g.compile_graph()


def _stub_intents(monkeypatch, sub_tasks, intent="多意图"):
    monkeypatch.setattr(
        g,
        "classify_intent",
        lambda _text: IntentDecision(
            intent=intent, confidence=0.92, need_clarify=False, sub_tasks=sub_tasks
        ),
    )


def _by_type(state):
    return {t["task_type"]: t for t in state["task_outputs"]}


# ---------------------------------------------------------------------------
# 场景A：理赔取证失败，但知识子任务照常产出，整体仍转人工
# ---------------------------------------------------------------------------
def test_claim_failure_does_not_kill_knowledge_task(monkeypatch):
    _stub_intents(monkeypatch, ["少件错发", "储存饮用咨询"])
    state = GR.invoke(
        {"user_text": "少了一瓶，另外这酒怎么保存？", "order_id": "SO_NOT_EXIST"}
    )
    tasks = _by_type(state)

    # 理赔失败 → 整体 handoff，不产工单
    assert state["handoff"] is True
    assert state["needs_human"] is True
    assert state["error"]["code"] == "NOT_FOUND"
    assert state.get("ticket_draft") is None

    # 失败被限制在理赔任务内，知识任务仍执行成功
    assert tasks["少件错发"]["status"] == "failed"
    assert tasks["少件错发"]["result"]["error"]["code"] == "NOT_FOUND"
    assert tasks["储存饮用咨询"]["status"] == "success"
    assert "避光平放" in tasks["储存饮用咨询"]["result"]["knowledge_result"]


def test_task_outputs_are_json_serializable(monkeypatch):
    """任务对象要能直接序列化（CLI/Trace 落盘用），失败态也不含不可序列化对象。"""
    _stub_intents(monkeypatch, ["少件错发", "储存饮用咨询"])
    state = GR.invoke({"user_text": "少了一瓶，另外这酒怎么保存？", "order_id": "SO_NOT_EXIST"})

    dumped = json.dumps(state["task_outputs"], ensure_ascii=False)
    assert "少件错发" in dumped
    assert '"failed"' in dumped


# ---------------------------------------------------------------------------
# 场景B：破损 + 少件，仅提供开箱视频 → 两个任务都缺证，清单合并
# ---------------------------------------------------------------------------
def test_partial_evidence_marks_both_claims_incomplete(monkeypatch):
    _stub_intents(monkeypatch, ["破损漏液", "少件错发"])
    state = GR.invoke(
        {
            "user_text": "漏了一瓶，还少一瓶",
            "order_id": ORDER_ID,
            "evidence": ["开箱视频"],
        }
    )
    tasks = _by_type(state)

    assert tasks["破损漏液"]["status"] == "success"
    assert tasks["少件错发"]["status"] == "success"
    assert tasks["破损漏液"]["result"]["evidence"]["complete"] is False
    assert tasks["少件错发"]["result"]["evidence"]["complete"] is False
    assert tasks["破损漏液"]["result"]["evidence"]["missing"] == ["面单照片", "破损部位照片"]
    assert tasks["少件错发"]["result"]["evidence"]["missing"] == ["面单照片", "整箱称重照片"]

    # 工单：仍按主诉出草稿、需人工审核、summary 提示第二诉求
    ticket = state["ticket_draft"]
    assert ticket["need_human_review"] is True
    assert "破损漏液" in ticket["summary"] and "少件" in ticket["summary"]

    # 回复：清单合并去重，破损与少件各自的缺证都在
    reply = state["reply_draft"]
    assert "【需要您补充的材料】" in reply["content"]
    assert reply["checklist"] == ["面单照片", "破损部位照片", "整箱称重照片"]
    assert "面单照片" in reply["content"]
    assert "破损部位照片" in reply["content"]
    assert "整箱称重照片" in reply["content"]


# ---------------------------------------------------------------------------
# 隔离性：只有失败的那个任务被标记 failed，其余任务结果保留
# ---------------------------------------------------------------------------
def test_only_failed_task_is_marked_failed(monkeypatch):
    _stub_intents(monkeypatch, ["破损漏液", "少件错发"])
    real_check = g._check_evidence

    def fake_check(order_id, issue_type, evidence):
        if issue_type == "missing_item":
            raise g.DataSourceError("少件数据源不可用")
        return real_check(order_id, issue_type, evidence)

    monkeypatch.setattr(g, "_check_evidence", fake_check)
    state = GR.invoke({"user_text": "漏了一瓶，还少一瓶", "order_id": ORDER_ID})
    tasks = _by_type(state)

    assert tasks["破损漏液"]["status"] == "success"
    assert tasks["破损漏液"]["result"]["compensation"]
    assert tasks["少件错发"]["status"] == "failed"
    assert tasks["少件错发"]["result"]["error"]["code"] == "DATA_SOURCE"
    # 任一理赔任务失败仍整体转人工，避免半成品工单流出
    assert state["handoff"] is True
    assert state["error"]["code"] == "DATA_SOURCE"
