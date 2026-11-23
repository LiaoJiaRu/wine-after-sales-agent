"""M4 阶段1：SQLite 工单落库 + Redis 多轮会话记忆。"""
from __future__ import annotations

from datetime import datetime

import pytest

import app.graph as g
from app.llm import IntentDecision
from app.memory import sqlite_client
from app.memory import redis_client

ORDER_ID = "SO20261116001"
FULL_EVIDENCE = ["开箱视频", "面单照片", "破损部位照片", "整箱称重照片"]
GR = g.compile_graph()


def _stub(monkeypatch, intent="破损漏液", sub_tasks=None):
    monkeypatch.setattr(
        g,
        "classify_intent",
        lambda _text: IntentDecision(
            intent=intent,
            confidence=0.9,
            need_clarify=False,
            sub_tasks=sub_tasks or [intent],
        ),
    )


def test_graph_invocation_persists_ticket(monkeypatch):
    _stub(monkeypatch)
    before = sqlite_client.count_tickets()

    state = GR.invoke({"user_text": "酒瓶漏了", "order_id": ORDER_ID})
    assert state["handoff"] is False

    rows = sqlite_client.get_tickets_by_order(ORDER_ID)
    assert sqlite_client.count_tickets() == before + 1
    assert len(rows) == 1
    row = rows[0]
    assert row["draft_id"] == state["ticket_draft"]["draft_id"]
    assert row["issue_type"] == "broken_leak"
    assert row["status"] == "draft"
    assert row["summary"] == state["ticket_draft"]["summary"]
    # created_at 以 UTC ISO 文本落库，可被解析
    assert datetime.fromisoformat(row["created_at"])


def test_get_tickets_by_order_returns_empty_for_unknown():
    assert sqlite_client.get_tickets_by_order("SO-NOT-EXIST") == []


def test_init_db_is_idempotent(monkeypatch):
    _stub(monkeypatch)
    sqlite_client.init_db()
    sqlite_client.init_db()
    GR.invoke({"user_text": "酒瓶漏了", "order_id": ORDER_ID})
    assert sqlite_client.count_tickets() == 1


def test_session_round_trip_restores_evidence(monkeypatch):
    """第一轮存证据，第二轮只给 session_id，应自动补回 order_id/evidence 并算出 354。"""
    _stub(monkeypatch)
    first = GR.invoke(
        {
            "user_text": "酒瓶漏了",
            "order_id": ORDER_ID,
            "evidence": FULL_EVIDENCE,
            "session_id": "sess-001",
        }
    )
    assert first["tool_results"]["compensation"]["amount"] == 354.0

    slots = redis_client.load_session("sess-001")["slots"]
    assert slots["order_id"] == ORDER_ID
    assert slots["evidence"] == FULL_EVIDENCE
    assert slots["ticket_draft_id"] == first["ticket_draft"]["draft_id"]

    # 第二轮：不传 order_id / evidence，仅靠会话补全
    second = GR.invoke({"user_text": "还是漏，我该怎么处理", "session_id": "sess-001"})
    assert second["order_id"] == ORDER_ID
    assert second["evidence"] == FULL_EVIDENCE
    assert second["handoff"] is False
    assert second["tool_results"]["compensation"]["recommendation"] == "partial_refund"
    assert second["tool_results"]["compensation"]["amount"] == 354.0


def test_current_round_input_wins_over_history(monkeypatch):
    """本轮显式传入的 order_id / 证据优先，历史只补空缺。"""
    _stub(monkeypatch)
    redis_client.save_session(
        "sess-002",
        slots={"order_id": ORDER_ID, "evidence": FULL_EVIDENCE, "issue_type": "broken_leak"},
    )
    state = GR.invoke(
        {"user_text": "换一单问问", "order_id": "SO20261116002", "session_id": "sess-002"}
    )
    assert state["order_id"] == "SO20261116002"
    # 证据仍从会话补全（本轮未传）
    assert state["evidence"] == FULL_EVIDENCE


def test_no_session_id_skips_redis(monkeypatch):
    _stub(monkeypatch)
    GR.invoke({"user_text": "酒瓶漏了", "order_id": ORDER_ID})
    client = redis_client.get_client()
    assert client.hgetall("agent:ctx:") == {}
    assert redis_client.load_session("") == {}


def test_sqlite_failure_degrades_to_draft(monkeypatch):
    _stub(monkeypatch)

    def boom(_draft):
        raise RuntimeError("disk full")

    monkeypatch.setattr(sqlite_client, "save_ticket", boom)

    state = GR.invoke({"user_text": "酒瓶漏了", "order_id": ORDER_ID})
    assert state["handoff"] is False
    assert state["ticket_draft"]["draft_id"]


def test_redis_failure_degrades_to_draft(monkeypatch):
    _stub(monkeypatch)

    def boom(*_a, **_kw):
        raise RuntimeError("redis down")

    monkeypatch.setattr(redis_client, "load_session", boom)
    monkeypatch.setattr(redis_client, "save_session", boom)

    state = GR.invoke(
        {"user_text": "酒瓶漏了", "order_id": ORDER_ID, "session_id": "sess-err"}
    )
    assert state["handoff"] is False
    assert state["ticket_draft"]["issue_type"] == "broken_leak"


def test_handoff_does_not_write_ticket(monkeypatch):
    """转人工（退款拦截）不得落库，避免半成品工单进入 DB。"""
    _stub(monkeypatch)
    state = GR.invoke({"user_text": "直接退款", "order_id": ORDER_ID})
    assert state["handoff"] is True
    assert sqlite_client.get_tickets_by_order(ORDER_ID) == []


def test_multi_intent_ticket_persisted_once(monkeypatch):
    _stub(monkeypatch, intent="多意图", sub_tasks=["破损漏液", "少件错发"])
    GR.invoke({"user_text": "漏了还少一瓶", "order_id": ORDER_ID})
    rows = sqlite_client.get_tickets_by_order(ORDER_ID)
    assert len(rows) == 1
    assert "少件" in rows[0]["summary"]


def test_merchant_review_lists_and_updates_draft(monkeypatch):
    """商家审核端依赖：按 status 查询工单并更新状态。"""
    _stub(monkeypatch)
    sqlite_client.init_db()
    state = GR.invoke(
        {"user_text": "酒瓶漏了", "order_id": ORDER_ID, "evidence": FULL_EVIDENCE}
    )
    draft_id = state["ticket_draft"]["draft_id"]

    drafts = sqlite_client.list_tickets(status="draft")
    assert [row["draft_id"] for row in drafts] == [draft_id]
    assert drafts[0]["amount"] == 354.0

    assert sqlite_client.update_ticket_status(draft_id, "approved") is True
    assert sqlite_client.list_tickets(status="draft") == []
    assert sqlite_client.list_tickets(status="approved")[0]["draft_id"] == draft_id
    assert sqlite_client.update_ticket_status("TD-NOT-EXIST", "approved") is False


def test_repeat_apply_for_same_order_supersedes_old_draft(monkeypatch):
    """同一订单重复申请：旧待审工单置为 superseded，draft 只保留最新一条。"""
    _stub(monkeypatch)
    first = GR.invoke({"user_text": "酒瓶漏了", "order_id": ORDER_ID})
    old_id = first["ticket_draft"]["draft_id"]

    second = GR.invoke({"user_text": "酒瓶又漏了", "order_id": ORDER_ID})
    new_id = second["ticket_draft"]["draft_id"]
    assert new_id != old_id

    drafts = sqlite_client.list_tickets(status="draft")
    assert [row["draft_id"] for row in drafts] == [new_id]

    all_rows = {row["draft_id"]: row for row in sqlite_client.list_tickets()}
    assert all_rows[old_id]["status"] == "superseded"
    assert all_rows[new_id]["status"] == "draft"
