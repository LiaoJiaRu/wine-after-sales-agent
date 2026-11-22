"""M4 收尾：多轮补证的证据槽位抽取（Slot Filling）。"""
from __future__ import annotations

import app.graph as g
from app.llm import IntentDecision
from app.memory import redis_client

ORDER_ID = "SO20261116001"
UPLOAD_TEXT = "我已经上传了开箱视频和面单照片和破损部位照片和整箱称重照片"
GR = g.compile_graph()


def _stub(monkeypatch, intent="破损漏液"):
    monkeypatch.setattr(
        g,
        "classify_intent",
        lambda _t: IntentDecision(intent=intent, confidence=0.95, need_clarify=False, sub_tasks=[]),
    )


# ---------------- 抽取函数 ----------------

def test_extract_all_four_in_standard_order():
    assert g.extract_evidence_from_text(UPLOAD_TEXT) == [
        "开箱视频",
        "面单照片",
        "破损部位照片",
        "整箱称重照片",
    ]


def test_extract_ignores_negation():
    assert g.extract_evidence_from_text("我还没拍开箱视频，面单照片也没有") == []


def test_extract_ignores_question():
    assert g.extract_evidence_from_text("开箱视频要拍吗？面单照片怎么提供") == []


def test_extract_dedupes_and_keeps_order():
    text = "面单照片、开箱视频、面单照片都已上传"
    assert g.extract_evidence_from_text(text) == ["开箱视频", "面单照片"]


# ---------------- 图集成 ----------------

def test_router_fills_evidence_and_completes(monkeypatch):
    _stub(monkeypatch)
    state = GR.invoke({"user_text": UPLOAD_TEXT, "order_id": ORDER_ID})

    assert set(state["evidence"]) == {"开箱视频", "面单照片", "破损部位照片", "整箱称重照片"}
    assert state["evidence_status"]["complete"] is True
    assert state["handoff"] is False


def test_evidence_from_text_reaches_354(monkeypatch):
    """破损类只需三项：原话提供三项即应算出 354.00。"""
    _stub(monkeypatch)
    state = GR.invoke(
        {
            "user_text": "我已经上传了开箱视频和面单照片和破损部位照片",
            "order_id": ORDER_ID,
        }
    )

    assert state["tool_results"]["compensation"]["recommendation"] == "partial_refund"
    assert state["tool_results"]["compensation"]["amount"] == 354.0
    assert "354.00" in state["reply_draft"]["content"]


def test_negated_evidence_keeps_missing(monkeypatch):
    _stub(monkeypatch)
    state = GR.invoke({"user_text": "我还没拍开箱视频", "order_id": ORDER_ID})

    assert state["evidence"] == []
    assert state["evidence_status"]["complete"] is False
    assert state["tool_results"]["compensation"]["recommendation"] == "supplement_evidence"


def test_multi_turn_session_only_upload_message(monkeypatch):
    """第一轮只留订单，第二轮只给 session_id + 上传凭证话术，仍应补出 354。"""
    _stub(monkeypatch)
    first = GR.invoke({"user_text": "酒瓶漏了", "order_id": ORDER_ID, "session_id": "slot-1"})
    assert first["handoff"] is False
    assert first["tool_results"]["compensation"]["recommendation"] == "supplement_evidence"

    second = GR.invoke(
        {"user_text": "我已经上传了开箱视频和面单照片和破损部位照片", "session_id": "slot-1"}
    )
    assert second["order_id"] == ORDER_ID
    assert second["tool_results"]["compensation"]["amount"] == 354.0
    assert second["tool_results"]["compensation"]["recommendation"] == "partial_refund"


def test_extracted_evidence_merges_with_history_without_duplicates(monkeypatch):
    _stub(monkeypatch)
    redis_client.save_session(
        "slot-2",
        slots={"order_id": ORDER_ID, "evidence": ["开箱视频", "面单照片"], "issue_type": "broken_leak"},
    )
    state = GR.invoke(
        {"user_text": "我已经上传了面单照片和破损部位照片", "session_id": "slot-2"}
    )

    assert state["evidence"] == ["开箱视频", "面单照片", "破损部位照片"]
    assert len(state["evidence"]) == len(set(state["evidence"]))
    assert state["evidence_status"]["complete"] is True
