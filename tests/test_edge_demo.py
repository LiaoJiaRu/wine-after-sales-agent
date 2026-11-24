"""演示防线：无关文本、超长输入、SQL 注入与非法订单号。

隔离说明：`tests/conftest.py` 的 autouse fixture 已为本文件提供
fakeredis、tmp_path SQLite 与临时转人工队列，测试不会写入真实 `data/`，
也不会污染本机 Redis。
"""
from __future__ import annotations

import app.graph as g
from app.llm import IntentDecision

ORDER_ID = "SO20261116001"
FULL_BROKEN_EVIDENCE = ["开箱视频", "面单照片", "破损部位照片"]
GRAPH = g.compile_graph()


def _invoke(text: str, order_id: str = ORDER_ID, **extra: object) -> dict:
    """走完整主图；任何节点异常都会被图内兜底为 handoff，而不是向外抛出。"""
    return GRAPH.invoke(
        {"user_text": text, "order_id": order_id, **extra},
        config={"recursion_limit": g.RECURSION_LIMIT},
    )


def test_unrelated_text_handoffs_without_ticket(monkeypatch):
    """无关文本：不识别理赔类型，转人工且不生成工单草稿。"""
    monkeypatch.setattr(
        g,
        "classify_intent",
        lambda _text: IntentDecision(
            intent="其他",
            confidence=0.2,
            need_clarify=False,
            sub_tasks=[],
        ),
    )

    state = _invoke("今天天气很好，适合出门散步")

    assert state["handoff"] is True
    assert state["needs_human"] is True
    assert state["error"]["code"] == "UNSUPPORTED_ISSUE_TYPE"
    assert state.get("ticket_draft") is None
    assert state.get("reply_draft") is None


def test_long_text_does_not_crash():
    """超长输入：3000 字以上仍返回完整 State，不抛异常。"""
    text = "酒瓶漏了，我已上传开箱视频、面单照片和破损部位照片。" * 120
    assert len(text) > 3000

    state = _invoke(text, evidence=FULL_BROKEN_EVIDENCE)

    assert isinstance(state, dict)
    assert state["order_id"] == ORDER_ID
    assert state["handoff"] is False
    assert state["evidence_status"]["complete"] is True
    assert state["tool_results"]["compensation"]["amount"] == 354.0
    assert state.get("ticket_draft") is not None


def test_sql_injection_text_is_handled_safely():
    """注入字符：作为普通文本处理，不进入 SQL 语句，不产生 INTERNAL 错误。"""
    payload = "我的酒漏了' OR 1=1 -- ；开箱视频、面单照片、破损部位照片都已上传"

    state = _invoke(payload, evidence=FULL_BROKEN_EVIDENCE)

    assert (state.get("error") or {}).get("code") != "INTERNAL"
    assert state["order_id"] == ORDER_ID
    assert state["handoff"] is False
    assert state.get("ticket_draft") is not None
    assert "354.00" in state["reply_draft"]["content"]


def test_illegal_order_id_degrades_to_handoff():
    """非法 order_id：查不到订单即降级转人工，不抛 500、不生成草稿。"""
    bad_order_id = "SO<>2026' OR 1=1 --"

    state = _invoke("酒瓶漏了，请帮我处理", order_id=bad_order_id)

    assert state["handoff"] is True
    assert state["needs_human"] is True
    assert state["error"]["code"] == "NOT_FOUND"
    assert state.get("ticket_draft") is None
    assert state.get("reply_draft") is None
