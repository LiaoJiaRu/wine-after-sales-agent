"""M2 破损漏液单场景闭环 E2E 测试。

直接调用 compile_graph().invoke({...})，断言 graph 最终 State 中：
  1. tool_results 有 order / shipment / batch 数据（dict 非空）
  2. evidence_status 反映证据是否齐全（complete=True 或 missing 有列表）
  3. handoff / needs_human 被正确标记（正常路径 False，异常路径 True）
  4. ticket_draft / reply_draft 正常路径非 None
  5. CLI 层 run_graph 最外层兜底：任何 LangGraph 运行时异常也不崩溃
"""
from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

from app.graph import compile_graph

ORDER_ID = "SO20261116001"
USER_TEXT_BROKEN = "我买的酒漏了，瓶身有破损"

# broken_leak 完整证据项
EVIDENCE_COMPLETE = ["开箱视频", "面单照片", "破损部位照片"]
# broken_leak 缺失开箱视频 → 走补证路径
EVIDENCE_MISSING_UNBOX = ["面单照片", "破损部位照片"]


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


GR = compile_graph()


# ---------------------------------------------------------------------------
# 正常路径：证据齐全 → draft 输出完整，handoff=False
# ---------------------------------------------------------------------------


def test_e2e_normal_flow_evidence_complete():
    """用户反馈破损漏液，证据齐全 → 走完整链路，draft 生成，handoff=False。"""
    state = GR.invoke(
        {
            "user_text": USER_TEXT_BROKEN,
            "order_id": ORDER_ID,
            "issue_type": "broken_leak",
            "evidence": EVIDENCE_COMPLETE,
        }
    )

    # 1. tool_results 有订单 + 物流 + 批次
    tr = state["tool_results"]
    assert isinstance(tr, dict) and tr, "tool_results 应为非空 dict"
    assert tr.get("order") and isinstance(tr["order"], dict), "tool_results.order 缺失或非 dict"
    assert tr.get("shipment") and isinstance(tr["shipment"], dict), "tool_results.shipment 缺失或非 dict"
    assert tr.get("batch") and isinstance(tr["batch"], dict), "tool_results.batch 缺失或非 dict"
    # 额外断言：订单主键 & 物流单号
    assert tr["order"].get("order_id") == ORDER_ID
    assert tr["shipment"].get("order_id") == ORDER_ID

    # 2. evidence_status 反映齐全
    es = state["evidence_status"]
    assert es.get("complete") is True, f"证据齐全时 complete 应为 True，实际 {es}"
    assert es.get("missing") == [], f"齐全证据 missing 应为空，实际 {es.get('missing')}"

    # 3. handoff / needs_human 正常路径为 False
    assert state["handoff"] is False
    assert state["needs_human"] is False

    # 4. draft 输出完整
    assert state["ticket_draft"] is not None
    assert state["reply_draft"] is not None
    assert isinstance(state["ticket_draft"], dict)
    assert isinstance(state["reply_draft"], dict)


def test_e2e_normal_flow_without_explicit_issue_type():
    """不显式传 issue_type，由 router.sub_tasks 推导 broken_leak。"""
    state = GR.invoke(
        {
            "user_text": USER_TEXT_BROKEN,
            "order_id": ORDER_ID,
            "evidence": EVIDENCE_COMPLETE,
        }
    )
    assert state["handoff"] is False
    assert state["tool_results"].get("issue_type") == "broken_leak"
    assert state["issue_type"] == "broken_leak"
    assert state["ticket_draft"] is not None


# ---------------------------------------------------------------------------
# 补证路径：证据缺失 → evidence_status.missing 有内容，draft 仍生成（含补证提示）
# ---------------------------------------------------------------------------


def test_e2e_missing_evidence_still_drafts_no_handoff():
    """缺一项证据时，evidence_status 标 missing 列表，流程不中断 → draft 仍生成补证提示。"""
    state = GR.invoke(
        {
            "user_text": USER_TEXT_BROKEN,
            "order_id": ORDER_ID,
            "issue_type": "broken_leak",
            "evidence": EVIDENCE_MISSING_UNBOX,
        }
    )

    # 证据状态：complete=False，missing 有列表
    es = state["evidence_status"]
    assert es.get("complete") is False
    missing = es.get("missing") or []
    assert isinstance(missing, list) and "开箱视频" in missing, f"missing 应包含 '开箱视频'，实际 {missing}"

    # 关键：缺证不转人工，handoff/needs_human 仍为 False，draft 正常生成
    assert state["handoff"] is False
    assert state["needs_human"] is False
    assert state["ticket_draft"] is not None, "缺证时工单草稿仍应生成（含补证标记）"
    assert state["reply_draft"] is not None

    # ticket_draft / summary 里应能看到补证标记（draft 工具的补证信号：need_human_review=True + summary 有"补证"/"证据不齐"）
    draft_dict = state["ticket_draft"]
    assert draft_dict.get("need_human_review") is True, \
        f"缺证工单 need_human_review 应为 True，实际 {draft_dict.get('need_human_review')}"
    summary = draft_dict.get("summary", "")
    assert ("补证" in summary) or ("证据不齐" in summary), \
        f"ticket_draft.summary 应包含补证提示，实际 summary={summary!r}"


# ---------------------------------------------------------------------------
# 异常路径：order 不存在 → fetch_evidence 抛 NotFoundError → handoff_human
# ---------------------------------------------------------------------------


def test_e2e_nonexistent_order_handoffs():
    state = GR.invoke(
        {
            "user_text": USER_TEXT_BROKEN,
            "order_id": "O1001",
            "issue_type": "broken_leak",
            "evidence": EVIDENCE_COMPLETE,
        }
    )
    assert state["handoff"] is True
    assert state["needs_human"] is True
    assert state.get("error", {}).get("code") == "NOT_FOUND"
    # 异常路径 tool_results 不完整（只有 issue_type 兜底），draft 不生成
    assert state.get("ticket_draft") is None
    assert state.get("reply_draft") is None


def test_e2e_empty_order_id_handoffs_before_tool():
    state = GR.invoke({"user_text": USER_TEXT_BROKEN, "order_id": ""})
    assert state["handoff"] is True
    assert state["needs_human"] is True
    assert state.get("error", {}).get("code") == "EMPTY_ORDER_ID"


# ---------------------------------------------------------------------------
# CLI 层 run_graph 最外层兜底
# ---------------------------------------------------------------------------


def test_run_graph_outer_try_except_on_uncaught_exception():
    """compile_graph().invoke() 抛 LangGraph 运行时异常 → run_graph 捕获，返回 error + needs_human。"""
    from app.cli import run_graph

    with patch("app.graph.compile_graph") as mock_compile:
        mock_compile.return_value.invoke.side_effect = RuntimeError("boom inside invoke")
        result = run_graph(USER_TEXT_BROKEN, ORDER_ID)

    assert isinstance(result, dict)
    assert result.get("needs_human") is True
    assert result.get("handoff") is True
    assert "error" in result
    assert result["error"].get("code") == "GRAPH_RUNTIME"
    assert "boom inside invoke" in result["error"].get("message", "")


# ---------------------------------------------------------------------------
# CLI --graph 端到端（参数解析 + JSON 输出）
# ---------------------------------------------------------------------------


def test_cli_graph_mode_normal_flow_json_output(capsys):
    from app.cli import main

    rc = main(["--graph", "--order", ORDER_ID, USER_TEXT_BROKEN])
    assert rc == 0

    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, dict)
    # 关键字段
    assert data.get("handoff") is False
    assert data.get("needs_human") is False
    assert data.get("ticket_draft") is not None
    assert data.get("reply_draft") is not None
    assert data.get("tool_results", {}).get("order")


def test_cli_graph_mode_not_found_order_prints_error(capsys):
    from app.cli import main

    rc = main(["--graph", "--order", "O1001", USER_TEXT_BROKEN])
    assert rc == 0

    data = json.loads(capsys.readouterr().out)
    assert data.get("handoff") is True
    assert data.get("needs_human") is True
    assert data.get("error", {}).get("code") == "NOT_FOUND"


def test_cli_non_graph_mode_still_works(capsys):
    """不传 --graph → 走原 route() 逻辑，返回 RouterDecision JSON。"""
    from app.cli import main

    rc = main(["我买的酒漏了"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    # route() 输出不含 tool_results
    assert "intent" in data or "needs_human" in data
    assert "tool_results" not in data


# ---------------------------------------------------------------------------
# 第二幕：证据齐全 → partial_refund 354.0 元 + 回复告知赔付金额、提及人工审核
# ---------------------------------------------------------------------------


def test_e2e_scene2_full_evidence_partial_refund_354():
    """第二幕核心断言：完整证据 → evidence complete + partial_refund 354.0 + 人工审核话术。"""
    state = GR.invoke(
        {
            "user_text": USER_TEXT_BROKEN,
            "order_id": ORDER_ID,
            "issue_type": "broken_leak",
            "evidence": EVIDENCE_COMPLETE,
        }
    )

    # 1. evidence_status.complete 为 True
    es = state["evidence_status"]
    assert es.get("complete") is True, f"第二幕证据应齐全，实际 {es}"
    assert es.get("missing") == []

    # 2. compensation.recommendation == "partial_refund"，amount == 1180 * 0.3 == 354.0
    comp = state["tool_results"]["compensation"]
    assert comp.get("recommendation") == "partial_refund", \
        f"证据齐全 + 破损漏液应部分赔付，实际 {comp.get('recommendation')}"
    assert comp.get("amount") == pytest.approx(354.0), \
        f"赔付金额应为订单金额 1180 的 30% = 354.0（保价 1200 不截断），实际 {comp.get('amount')}"

    # 3. reply_draft 不再有【需要您补充的材料】，告知赔付金额并提及"人工审核"
    reply = state["reply_draft"]
    assert reply is not None
    content = reply.get("content", "")
    assert "【需要您补充的材料】" not in content, "第二幕不应再要求补证"
    assert reply.get("checklist") == [], "证据齐全时 checklist 应为空"
    assert "354.00" in content, f"回复应告知赔付金额 354.00 元，实际 content={content!r}"
    assert "人工审核" in content, f"回复应提及人工审核，实际 content={content!r}"

    # 4. 工单草稿同步携带赔付建议，且仍需人工审核
    ticket = state["ticket_draft"]
    assert ticket["compensation"]["recommendation"] == "partial_refund"
    assert ticket["compensation"]["amount"] == pytest.approx(354.0)
    assert ticket["need_human_review"] is True
    assert state["handoff"] is False
    assert state["needs_human"] is False


def test_e2e_scene2_run_m2_script_with_evidence(capsys):
    """run_m2.py --with-evidence 第二幕脚本：stdout 含 partial_refund / 354.00 / 人工审核。"""
    from app.run_m2 import main as run_m2_main

    rc = run_m2_main(["--order", ORDER_ID, "--with-evidence"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "第二幕" in out
    assert '"complete": true' in out
    assert '"recommendation": "partial_refund"' in out
    assert '"amount": 354.0' in out
    assert "【需要您补充的材料】" not in out
    assert "354.00" in out
    assert "人工审核" in out


def test_e2e_scene1_run_m2_script_without_evidence(capsys):
    """run_m2.py 第一幕回归：不带 --with-evidence → 仍输出补证清单。"""
    from app.run_m2 import main as run_m2_main

    rc = run_m2_main(["--order", ORDER_ID])
    assert rc == 0

    out = capsys.readouterr().out
    assert "第一幕" in out
    assert '"complete": false' in out
    assert "【需要您补充的材料】" in out


# ---------------------------------------------------------------------------
# JSON 序列化 smoke：完整 State dump 不会因为循环引用崩
# ---------------------------------------------------------------------------


def test_state_json_serializable():
    state = GR.invoke(
        {
            "user_text": USER_TEXT_BROKEN,
            "order_id": ORDER_ID,
            "issue_type": "broken_leak",
            "evidence": EVIDENCE_COMPLETE,
        }
    )
    # 不应抛 TypeError: Object of type ... is not JSON serializable
    dumped = json.dumps(state, ensure_ascii=False, default=str)
    assert len(dumped) > 100
    parsed = json.loads(dumped)
    assert parsed["handoff"] is False
