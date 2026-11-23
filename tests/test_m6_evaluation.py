"""M6：U01-U15 独立端到端评测。

只调用现有 LangGraph，不修改业务实现。路由与 Judge 使用确定性 mock，
真实订单/物流/批次/政策工具及多轮记忆仍走主链。评测时钟固定为
2026-11-16，避免固定签收日期随运行时间变化而改变赔付分支。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app.graph as g
import app.tools.readonly as ro
from app.agents.router import route as real_route
from app.llm import IntentDecision
from app.tools.errors import DataSourceError

ORDER_ID = "SO20261116001"
FULL_BROKEN_EVIDENCE = ["开箱视频", "面单照片", "破损部位照片"]
FULL_MISSING_EVIDENCE = ["开箱视频", "面单照片", "整箱称重照片"]
FULL_WRONG_EVIDENCE = ["开箱视频", "面单照片", "商品照片"]
TZ = timezone(timedelta(hours=8))
EVAL_NOW = datetime(2026, 11, 16, 12, 0, tzinfo=TZ)
GR = g.compile_graph()


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return EVAL_NOW.astimezone(tz) if tz else EVAL_NOW.replace(tzinfo=None)


def _turn(text, order_id=ORDER_ID, **extra):
    return {"user_text": text, "order_id": order_id, **extra}


CASES = [
    {
        "id": "U01",
        "title": "破损漏液：三项证据齐全",
        "intent": "破损漏液",
        "sub_tasks": ["破损漏液"],
        "turns": [_turn("酒瓶漏了，我已上传开箱视频、面单照片和破损部位照片")],
        "handoff": False,
    },
    {
        "id": "U02",
        "title": "破损漏液：无视频，生成补证清单",
        "intent": "破损漏液",
        "sub_tasks": ["破损漏液"],
        "turns": [_turn("酒瓶漏了，但还没有开箱视频", evidence=["面单照片", "破损部位照片"])],
        "handoff": False,
    },
    {
        "id": "U03",
        "title": "破损漏液：跨轮补证",
        "intent": "破损漏液",
        "sub_tasks": ["破损漏液"],
        "turns": [
            _turn("酒瓶漏了", session_id="m6-u03"),
            {"user_text": "我已经上传了开箱视频和面单照片和破损部位照片", "session_id": "m6-u03"},
        ],
        "handoff": False,
    },
    {
        "id": "U04",
        "title": "少件：证据齐全建议补发",
        "intent": "少件错发",
        "sub_tasks": ["少件错发"],
        "turns": [_turn("收到后少了一瓶，材料已齐", evidence=FULL_MISSING_EVIDENCE)],
        "handoff": False,
    },
    {
        "id": "U05",
        "title": "错发：异常理赔转人工",
        "intent": "少件错发",
        "sub_tasks": ["错发"],
        "turns": [_turn("我收到的是另一款酒，商品照片等材料已齐", evidence=FULL_WRONG_EVIDENCE)],
        "handoff": True,
    },
    {
        "id": "U06",
        "title": "真伪争议：禁止自动认定，转人工",
        "intent": "真伪争议",
        "sub_tasks": ["真伪争议"],
        "turns": [_turn("我怀疑这瓶是假酒，防伪码也查不到")],
        "handoff": True,
    },
    {
        "id": "U07",
        "title": "超 7 天时效：O-TIMEOUT 必须转人工",
        "intent": "破损漏液",
        "sub_tasks": ["破损漏液"],
        "turns": [_turn("这瓶酒漏了，三项材料都已上传", order_id="O-TIMEOUT", evidence=FULL_BROKEN_EVIDENCE)],
        "handoff": True,
    },
    {
        "id": "U08",
        "title": "未成年人购酒饮用咨询：立即拦截并转人工",
        "intent": "储存饮用咨询",
        "sub_tasks": ["储存饮用咨询"],
        "turns": [_turn("我是未成年人，今年 17 岁，想买酒喝，请告诉我怎么饮用")],
        "handoff": True,
    },
    {
        "id": "U09",
        "title": "多意图：破损 + 少件 + 保存咨询",
        "intent": "多意图",
        "sub_tasks": ["破损漏液", "少件错发", "储存饮用咨询"],
        "turns": [_turn("我买的酒漏了，还少一瓶，这酒怎么保存？")],
        "handoff": False,
    },
    {
        "id": "U10",
        "title": "历史会话：仅带 session_id 恢复订单和证据",
        "intent": "破损漏液",
        "sub_tasks": ["破损漏液"],
        "turns": [
            _turn("酒瓶漏了，材料已上传", evidence=FULL_BROKEN_EVIDENCE, session_id="m6-u10"),
            {"user_text": "还是同一单，请继续处理", "session_id": "m6-u10"},
        ],
        "handoff": False,
    },
    {
        "id": "U11",
        "title": "异常：订单不存在",
        "intent": "破损漏液",
        "sub_tasks": ["破损漏液"],
        "turns": [_turn("这单酒漏了", order_id="NO_SUCH_ORDER")],
        "handoff": True,
    },
    {
        "id": "U12",
        "title": "异常：订单数据源读取失败",
        "intent": "破损漏液",
        "sub_tasks": ["破损漏液"],
        "turns": [_turn("酒瓶漏了，请处理")],
        "handoff": True,
        "data_source_error": True,
    },
    {
        "id": "U13",
        "title": "Judge 首次拒绝，局部重跑后通过",
        "intent": "破损漏液",
        "sub_tasks": ["破损漏液"],
        "turns": [_turn("酒瓶漏了，请生成处理草稿")],
        "handoff": False,
        "judge_script": [
            {"pass": False, "reason": "补证清单不完整", "failed_node": "create_draft"},
            {"pass": True, "reason": "OK", "failed_node": ""},
        ],
    },
    {
        "id": "U14",
        "title": "Judge 连续拒绝，达到重试上限转人工",
        "intent": "破损漏液",
        "sub_tasks": ["破损漏液"],
        "turns": [_turn("酒瓶漏了，请生成处理草稿")],
        "handoff": True,
        "judge_script": [
            {"pass": False, "reason": "承诺越权", "failed_node": "create_draft"},
        ],
    },
    {
        "id": "U15",
        "title": "酒知识：开瓶后的葡萄酒保存咨询",
        "intent": "储存饮用咨询",
        "sub_tasks": ["储存饮用咨询"],
        "turns": [_turn("开瓶后的葡萄酒怎么保存？")],
        "handoff": False,
    },
]


def _install_judge_script(monkeypatch, script):
    calls = {"n": 0}

    def fake_judge(_state):
        index = min(calls["n"], len(script) - 1)
        calls["n"] += 1
        return dict(script[index])

    monkeypatch.setattr(g, "judge_draft", fake_judge)
    return calls


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_m6_end_to_end(case, monkeypatch):
    assert case["id"].startswith("U")
    monkeypatch.setattr(ro, "datetime", _FrozenDateTime)
    if case["id"] == "U08":
        monkeypatch.setattr(g, "classify_intent", real_route)
    else:
        monkeypatch.setattr(
            g,
            "classify_intent",
            lambda _text: IntentDecision(
                intent=case["intent"],
                confidence=0.95,
                need_clarify=False,
                sub_tasks=case["sub_tasks"],
            ),
        )

    if case.get("data_source_error"):
        def raise_data_source(_order_id):
            raise DataSourceError("M6 模拟订单数据源读取失败")

        monkeypatch.setattr(g, "get_order", raise_data_source)

    judge_calls = None
    if case.get("judge_script"):
        judge_calls = _install_judge_script(monkeypatch, case["judge_script"])

    states = []
    for turn in case["turns"]:
        states.append(GR.invoke(turn, config={"recursion_limit": g.RECURSION_LIMIT}))
    state = states[-1]
    case_id = case["id"]

    if case_id == "U01":
        assert state["evidence_status"]["complete"] is True
        assert state["tool_results"]["compensation"]["amount"] == pytest.approx(354.0)
        assert "354.00" in state["reply_draft"]["content"]
        assert "人工审核" in state["reply_draft"]["content"]
    elif case_id == "U02":
        assert state["evidence_status"]["complete"] is False
        assert state["evidence_status"]["missing"] == ["开箱视频"]
        assert state["tool_results"]["compensation"]["amount"] == 0.0
        assert "开箱视频" in state["reply_draft"]["content"]
    elif case_id == "U03":
        assert states[0]["tool_results"]["compensation"]["recommendation"] == "supplement_evidence"
        assert state["order_id"] == ORDER_ID
        assert state["evidence_status"]["complete"] is True
        assert state["tool_results"]["compensation"]["amount"] == pytest.approx(354.0)
        assert "354.00" in state["reply_draft"]["content"]
    elif case_id == "U04":
        assert state["issue_type"] == "missing_item"
        assert state["evidence_status"]["complete"] is True
        assert state["tool_results"]["compensation"]["recommendation"] == "reship"
        assert state["tool_results"]["compensation"]["amount"] == 0.0
        assert "补发" in state["reply_draft"]["content"]
    elif case_id == "U05":
        assert state["issue_type"] == "wrong_item"
        assert state["evidence_status"]["complete"] is True
        assert state["tool_results"]["compensation"]["recommendation"] == "handoff_human"
        assert state["tool_results"]["compensation"]["amount"] == 0.0
        assert "售后专员" in state["reply_draft"]["content"]
    elif case_id == "U06":
        assert state["error"]["code"] == "UNSUPPORTED_ISSUE_TYPE"
        assert state.get("ticket_draft") is None
    elif case_id == "U07":
        compensation = state["tool_results"]["compensation"]
        assert compensation["recommendation"] == "handoff_human"
        assert compensation["amount"] == 0.0
        assert "超 7 天时效" in compensation["reason"]
        assert "售后专员" in state["reply_draft"]["content"]
    elif case_id == "U08":
        assert state["error"]["code"] == "MINOR_PROTECTION"
        assert "未成年" in state["error"]["message"]
        assert state.get("ticket_draft") is None
        assert state.get("reply_draft") is None
    elif case_id == "U09":
        assert [task["status"] for task in state["task_outputs"]] == ["success", "success", "success"]
        assert "整箱称重照片" in state["reply_draft"]["content"]
        assert "【酒知识解答】" in state["reply_draft"]["content"]
        assert "避光" in state["reply_draft"]["content"]
    elif case_id == "U10":
        assert state["order_id"] == ORDER_ID
        assert state["evidence"] == FULL_BROKEN_EVIDENCE
        assert state["tool_results"]["compensation"]["amount"] == pytest.approx(354.0)
        assert "354.00" in state["reply_draft"]["content"]
    elif case_id == "U11":
        assert state["error"]["code"] == "NOT_FOUND"
        assert state.get("ticket_draft") is None
        assert state.get("reply_draft") is None
    elif case_id == "U12":
        assert state["error"]["code"] == "DATA_SOURCE"
        assert state.get("ticket_draft") is None
    elif case_id == "U13":
        assert judge_calls["n"] == 2
        assert state["retry_count"] == 1
        assert state["judge_result"]["pass"] is True
        assert "质检复核" in state["ticket_draft"]["summary"]
        assert "补证清单不完整" in state["ticket_draft"]["summary"]
    elif case_id == "U14":
        assert judge_calls["n"] == 3
        assert state["retry_count"] == 2
        assert state["error"]["code"] == "JUDGE_REJECTED"
        assert "已重试 2 次" in state["error"]["message"]
    elif case_id == "U15":
        assert state["intent"] == "储存饮用咨询"
        assert "避光平放" in state["knowledge_result"]
        assert state.get("ticket_draft") is None
        assert state.get("reply_draft") is None

    assert state["handoff"] is case["handoff"]
    assert state["needs_human"] is case["handoff"]
