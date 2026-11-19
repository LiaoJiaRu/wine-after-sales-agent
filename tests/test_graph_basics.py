"""M2 LangGraph 骨架单测。

只验证骨架能 invoke、State 字段齐全、handoff 条件边能按预期分流。
不验证业务结果（draft 内容质量留给 draft 工具单测覆盖）。
"""
from __future__ import annotations

import pytest

from app.graph import GraphState, compile_graph

ORDER_ID = "SO20261116001"
USER_TEXT = "我买的酒漏了，还少一瓶，这酒怎么保存？"

GR = compile_graph()


def _required_fields(s: GraphState) -> set[str]:
    """作业1-1 表4 要求的字段全部出现在 State 字典里（值可为空，但 key 必须存在）。"""
    required = {
        "intent", "sub_tasks", "tool_results", "evidence_status",
        "judge_result", "retry_count", "handoff",
    }
    return required - set(s.keys())


def test_compile_returns_runnable():
    # compile 不抛异常 + 返回对象有 .invoke
    assert callable(getattr(GR, "invoke", None))


def test_normal_flow_reaches_draft_and_handoff_default_false():
    out = GR.invoke({"user_text": USER_TEXT, "order_id": ORDER_ID})
    # 骨架正常路径：load_session → router → fetch_evidence → check_evidence → create_draft → END
    assert out["handoff"] is False
    assert out["intent"]  # 非空字符串
    assert out["ticket_draft"] is not None
    assert out["reply_draft"] is not None
    assert "error" in out  # 字段存在（可为 {}）


def test_state_contains_all_required_fields():
    out = GR.invoke({"user_text": USER_TEXT, "order_id": ORDER_ID})
    missing = _required_fields(out)
    assert not missing, f"作业1-1 表4 要求的字段缺失: {missing}"


def test_empty_order_id_handoffs():
    # load_session 内部守卫：order_id 为空 → 直接 handoff，不打任何工具
    out = GR.invoke({"user_text": USER_TEXT, "order_id": ""})
    assert out["handoff"] is True
    assert out.get("error", {}).get("code") == "EMPTY_ORDER_ID"


def test_mock_order_id_handoffs_via_tool_not_found():
    # 用不存在的 order_id → fetch_evidence 抛 NotFoundError → handoff_human 节点
    out = GR.invoke({"user_text": USER_TEXT, "order_id": "O1001"})
    assert out["handoff"] is True
    # 错误码应是 NOT_FOUND（NotFoundError.code）
    assert out.get("error", {}).get("code") == "NOT_FOUND"
    # 草稿节点根本不会被执行
    assert out.get("ticket_draft") is None
