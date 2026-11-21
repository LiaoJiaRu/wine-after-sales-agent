"""M3 工程化：多理赔任务并发取证（ThreadPoolExecutor）与子 Agent 占位。"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import app.graph as g
from app.agents.fake_wine_agent import FakeWineAgent, fake_wine_agent
from app.agents.logistics_agent import LogisticsAgent, logistics_agent
from app.llm import IntentDecision

ORDER_ID = "SO20261116001"
TEXT = "我买的酒漏了，还少一瓶"
GR = g.compile_graph()


def _stub(monkeypatch, sub_tasks, intent="多意图"):
    monkeypatch.setattr(
        g,
        "classify_intent",
        lambda _text: IntentDecision(
            intent=intent, confidence=0.9, need_clarify=False, sub_tasks=sub_tasks
        ),
    )


def test_two_claim_tasks_run_and_stay_isolated(monkeypatch):
    _stub(monkeypatch, ["破损漏液", "少件错发"])
    state = GR.invoke({"user_text": TEXT, "order_id": ORDER_ID})
    tasks = {t["task_type"]: t for t in state["task_outputs"]}

    assert tasks["破损漏液"]["status"] == "success"
    assert tasks["少件错发"]["status"] == "success"
    assert tasks["破损漏液"]["result"]["issue_type"] == "broken_leak"
    assert tasks["少件错发"]["result"]["issue_type"] == "missing_item"
    # 结果对象互不共享（无跨线程覆盖）
    assert tasks["破损漏液"]["result"] is not tasks["少件错发"]["result"]
    assert tasks["破损漏液"]["result"]["order"] is not tasks["少件错发"]["result"]["order"]


def test_task_order_preserved_when_completion_order_differs(monkeypatch):
    """让破损线程睡一会儿，完成顺序反转，但 task_outputs 仍按原顺序回写。"""
    _stub(monkeypatch, ["破损漏液", "少件错发"])
    real_check = g._check_evidence

    def slow_check(order_id, issue_type, evidence):
        if issue_type == "broken_leak":
            time.sleep(0.12)
        return real_check(order_id, issue_type, evidence)

    monkeypatch.setattr(g, "_check_evidence", slow_check)
    state = GR.invoke({"user_text": TEXT, "order_id": ORDER_ID})

    assert [t["task_type"] for t in state["task_outputs"]] == ["破损漏液", "少件错发"]
    assert [t["result"]["issue_type"] for t in state["task_outputs"]] == ["broken_leak", "missing_item"]
    assert state["issue_type"] == "broken_leak"


def test_concurrent_error_isolated(monkeypatch):
    """线程内异常只标记该任务失败，其余任务结果保留，整图转人工。"""
    _stub(monkeypatch, ["破损漏液", "少件错发"])
    real_check = g._check_evidence

    def flaky_check(order_id, issue_type, evidence):
        if issue_type == "missing_item":
            raise g.DataSourceError("少件数据源超时")
        return real_check(order_id, issue_type, evidence)

    monkeypatch.setattr(g, "_check_evidence", flaky_check)
    state = GR.invoke({"user_text": TEXT, "order_id": ORDER_ID})
    tasks = {t["task_type"]: t for t in state["task_outputs"]}

    assert tasks["破损漏液"]["status"] == "success"
    assert tasks["少件错发"]["status"] == "failed"
    assert tasks["少件错发"]["result"]["error"]["code"] == "DATA_SOURCE"
    assert state["handoff"] is True


def test_results_identical_to_serial_implementation(monkeypatch):
    """并发与串行的取证结果逐字段一致（数据结构不变）。"""
    _stub(monkeypatch, ["破损漏液", "少件错发"])
    claim_issue = {"破损漏液": "broken_leak", "少件错发": "missing_item"}
    state = GR.invoke({"user_text": TEXT, "order_id": ORDER_ID})

    for task in state["task_outputs"]:
        # 复刻图内的查询串与证据入参；sub_tasks 是主诉附加字段，不参与比对
        serial = g._fetch_claim(
            ORDER_ID, claim_issue[task["task_type"]], [], "破损漏液 少件错发"
        )
        actual = {k: v for k, v in task["result"].items() if k != "sub_tasks"}
        assert actual == serial


def test_thread_pool_is_used(monkeypatch):
    """确认确实走线程池：记录 worker 线程名。"""
    _stub(monkeypatch, ["破损漏液", "少件错发"])
    seen: list[str] = []
    real_check = g._check_evidence

    def record_thread(order_id, issue_type, evidence):
        import threading

        seen.append(threading.current_thread().name)
        return real_check(order_id, issue_type, evidence)

    monkeypatch.setattr(g, "_check_evidence", record_thread)
    GR.invoke({"user_text": TEXT, "order_id": ORDER_ID})

    assert seen and all(name.startswith("claim") for name in seen)
    assert isinstance(g.ThreadPoolExecutor, type)


def test_sub_agent_placeholders_are_importable():
    """专业子 Agent 占位类可导入且有统一 run 签名。"""
    assert isinstance(logistics_agent, LogisticsAgent)
    assert isinstance(fake_wine_agent, FakeWineAgent)

    logistics = logistics_agent.run(ORDER_ID)
    assert logistics["agent"] == "logistics_agent"
    assert logistics["shipment"]["order_id"] == ORDER_ID
    assert LogisticsAgent.issue_types == ("物流延迟",)
    assert FakeWineAgent.issue_types == ("真伪争议",)
