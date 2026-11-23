"""M5 阶段1：polish 节点、事实锁定与降级测试。"""
from __future__ import annotations

import pytest

import app.graph as g
import app.llm as llm

ORDER_ID = "SO20261116001"
ORIGINAL_REPLY = """尊敬的顾客，您好：
关于订单 SO20261116001，我们已收到您的反馈。
【需要您补充的材料】
1）开箱视频
2）面单照片
【处理进度】
预计金额 354.00 元；最终结果以人工审核为准。"""


def _state():
    return {
        "reply_draft": {
            "content": ORIGINAL_REPLY,
            "checklist": ["开箱视频", "面单照片"],
        }
    }


def test_polish_node_updates_reply_content(monkeypatch):
    polished = ORIGINAL_REPLY.replace(
        "尊敬的顾客，您好：", "尊敬的顾客，您好，我们很理解您此刻的担忧："
    )
    seen = {}

    def fake_polish(content):
        seen["original"] = content
        return polished

    monkeypatch.setattr(g, "polish_reply", fake_polish)
    result = g.polish(_state())

    assert seen["original"] == ORIGINAL_REPLY
    assert result["reply_draft"]["content"] == polished
    assert result["reply_draft"]["checklist"] == ["开箱视频", "面单照片"]


def test_polish_node_skips_empty_content(monkeypatch):
    monkeypatch.setattr(g, "polish_reply", lambda _content: pytest.fail("空草稿不应调用 LLM"))
    assert g.polish({"reply_draft": None}) == {}
    assert g.polish({"reply_draft": {"content": "  "}}) == {}


def test_polish_node_failure_keeps_original(monkeypatch):
    state = _state()

    def raise_timeout(_content):
        raise llm.LLMError("润色超时", code="TIMEOUT", retryable=True)

    monkeypatch.setattr(g, "polish_reply", raise_timeout)
    assert g.polish(state) == {}
    assert state["reply_draft"]["content"] == ORIGINAL_REPLY


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("354.00", "999.00"),
        ("1）开箱视频", "1）商品照片"),
        ("人工审核", "自动审核"),
    ],
    ids=["amount_changed", "evidence_changed", "human_review_changed"],
)
def test_polish_node_rejects_locked_fact_changes(monkeypatch, old, new):
    monkeypatch.setattr(g, "polish_reply", lambda content: content.replace(old, new))
    assert g.polish(_state()) == {}


def test_polish_reply_calls_deepseek_with_system_prompt(monkeypatch):
    captured = {}

    def fake_complete(messages):
        captured["messages"] = messages
        return "润色后的完整回复"

    monkeypatch.setattr(llm, "complete", fake_complete)
    monkeypatch.setattr(llm, "get_langfuse", lambda: None)

    assert llm.polish_reply("原始草稿") == "润色后的完整回复"
    messages = captured["messages"]
    assert messages[0]["role"] == "system"
    assert "高级客服质检员" in messages[0]["content"]
    assert "严格禁止修改任何金额" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "原始草稿"}


def test_graph_runs_polish_between_create_draft_and_judge(monkeypatch):
    events = []

    def fake_polish(content):
        events.append("polish")
        return content.replace("尊敬的顾客，您好：", "尊敬的顾客，您好，我们会耐心跟进：")

    def fake_judge(state):
        events.append("judge")
        assert "我们会耐心跟进" in state["reply_draft"]["content"]
        return {"pass": True, "reason": "OK", "failed_node": ""}

    monkeypatch.setattr(g, "polish_reply", fake_polish)
    monkeypatch.setattr(g, "judge_draft", fake_judge)
    state = g.compile_graph().invoke(
        {"user_text": "酒瓶漏了", "order_id": ORDER_ID},
        config={"recursion_limit": g.RECURSION_LIMIT},
    )

    assert events == ["polish", "judge"]
    assert "我们会耐心跟进" in state["reply_draft"]["content"]
    assert state["handoff"] is False


def test_graph_polish_failure_degrades_without_blocking(monkeypatch):
    seen = {}

    def raise_timeout(content):
        seen["original"] = content
        raise TimeoutError("M5 模拟润色超时")

    monkeypatch.setattr(g, "polish_reply", raise_timeout)
    state = g.compile_graph().invoke(
        {"user_text": "酒瓶漏了", "order_id": ORDER_ID},
        config={"recursion_limit": g.RECURSION_LIMIT},
    )

    assert state["reply_draft"]["content"] == seen["original"]
    assert state["judge_result"]["pass"] is True
    assert state["handoff"] is False
    assert state["needs_human"] is False
