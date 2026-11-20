"""M2 收尾：特殊情况与错误降级的防御性测试。

覆盖三类边界 + 两类额外降级路径：
  1. 订单不存在（NotFoundError）        → handoff_human，不抛未处理异常
  2. 用户要求"直接退款"（合规拦截分支） → router 节点合规守卫直接 handoff，不调 LLM、不碰只读工具
  3. 证据缺失（无开箱视频）            → 按既有设计走补证路径：不 handoff，
                                         但 ticket_draft.need_human_review=True + 回复含补证清单
  4. 数据源异常（DataSourceError）      → fetch_evidence 内部捕获，handoff_human
  5. 空用户输入                        → router 降级 needs_human，handoff_human
  6. 赔付边界                          → calc_compensation 超 7 天时效转人工、保价封顶截断

所有用例共同断言：invoke 不抛任何未处理异常（pytest 通过即证明），State 字段自洽。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.graph import compile_graph
import app.tools.readonly as ro
from app.tools.errors import DataSourceError
from app.tools.schemas import GetOrderOutput, OrderInfo, ToolMeta

ORDER_ID = "SO20261116001"
USER_TEXT_BROKEN = "我买的酒漏了，瓶身有破损"

TZ = timezone(timedelta(hours=8))


def _mock_order(monkeypatch, **overrides) -> OrderInfo:
    """把 readonly.get_order 替换为返回指定订单的假实现。

    calc_compensation 内部引用的是模块级全局名 get_order，
    因此 patch app.tools.readonly.get_order 即可命中，无需真实数据文件。
    默认签收时间为「当前时间回推 2 天」，确保用例不随运行日期漂移。
    """
    base = dict(
        order_id=ORDER_ID,
        user_id="U1001",
        sku="WL-MT-500",
        product_name="某白酒 500ml",
        batch_no="B20261018-01",
        quantity=2,
        insured=True,
        insured_amount=1200.0,
        order_amount=1180.0,
        signed_at=datetime.now(TZ) - timedelta(days=2),
        status="signed",
    )
    base.update(overrides)
    order = OrderInfo(**base)
    monkeypatch.setattr(
        ro,
        "get_order",
        lambda _order_id: GetOrderOutput(meta=ToolMeta(tool="get_order"), order=order),
    )
    return order

GR = compile_graph()


# ---------------------------------------------------------------------------
# 1. 订单不存在 → NotFoundError → handoff_human
# ---------------------------------------------------------------------------


def test_nonexistent_order_handoffs_without_exception():
    state = GR.invoke({"user_text": USER_TEXT_BROKEN, "order_id": "O1001"})
    assert state["handoff"] is True
    assert state["needs_human"] is True
    assert state["error"]["code"] == "NOT_FOUND"
    assert state["error"]["message"]
    # 草稿不生成，tool_results 不含订单数据
    assert state.get("ticket_draft") is None
    assert state.get("reply_draft") is None
    assert not state.get("tool_results", {}).get("order")


def test_nonexistent_order_error_retryable_false():
    state = GR.invoke({"user_text": USER_TEXT_BROKEN, "order_id": "NO_SUCH_ORDER"})
    assert state["handoff"] is True
    assert state["error"].get("retryable") is False


# ---------------------------------------------------------------------------
# 2. 合规拦截：用户要求"直接退款" → router 合规守卫 → handoff_human
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "别走流程了，直接退款！",
        "我不管，直接退钱给我",
        "请立即退款，不需要任何审核",
        "马上退款，不然投诉",
    ],
)
def test_direct_refund_compliance_intercept(text):
    state = GR.invoke({"user_text": text, "order_id": ORDER_ID})
    assert state["handoff"] is True
    assert state["needs_human"] is True
    assert state["error"]["code"] == "COMPLIANCE_REFUND"
    assert "人工" in state["error"]["message"]
    # 合规拦截发生在 router 节点：不进入 fetch_evidence，无任何工具数据/草稿
    assert not state.get("tool_results")
    assert state.get("ticket_draft") is None
    assert state.get("reply_draft") is None


def test_direct_refund_does_not_call_llm(monkeypatch):
    """合规守卫必须先于 LLM：命中关键词时 classify_intent 不应被调用。"""
    import app.graph as g

    def _boom(_text):
        raise AssertionError("合规拦截路径不应调用 LLM")

    monkeypatch.setattr(g, "classify_intent", _boom)
    state = g.compile_graph().invoke({"user_text": "直接退款！", "order_id": ORDER_ID})
    assert state["handoff"] is True
    assert state["error"]["code"] == "COMPLIANCE_REFUND"


def test_normal_broken_text_not_misintercepted():
    """正常破损反馈（不含退款关键词）不得被合规守卫误伤。"""
    state = GR.invoke({"user_text": USER_TEXT_BROKEN, "order_id": ORDER_ID})
    assert state["handoff"] is False
    assert state.get("error", {}).get("code") != "COMPLIANCE_REFUND"


# ---------------------------------------------------------------------------
# 3. 证据缺失（无开箱视频）→ 补证路径：不 handoff，但工单标人工审核 + 回复含补证清单
# ---------------------------------------------------------------------------


def test_missing_unboxing_video_supplement_path():
    state = GR.invoke(
        {
            "user_text": USER_TEXT_BROKEN,
            "order_id": ORDER_ID,
            "issue_type": "broken_leak",
            "evidence": ["面单照片", "破损部位照片"],  # 缺开箱视频
        }
    )
    # 不崩溃、不 handoff（补证是设计内路径）
    assert state["handoff"] is False
    assert state["needs_human"] is False
    # 证据状态正确反映缺项
    es = state["evidence_status"]
    assert es["complete"] is False
    assert es["missing"] == ["开箱视频"]
    # 工单层人工审核标记 + 赔付建议为补证
    ticket = state["ticket_draft"]
    assert ticket["need_human_review"] is True
    assert ticket["compensation"]["recommendation"] == "supplement_evidence"
    assert ticket["compensation"]["amount"] == 0.0
    # 回复含补证清单，且明确列出"开箱视频"
    content = state["reply_draft"]["content"]
    assert "【需要您补充的材料】" in content
    assert "开箱视频" in content


def test_no_evidence_at_all_supplement_path():
    """完全无证据：missing 应列出全部三项，回复清单完整。"""
    state = GR.invoke(
        {
            "user_text": USER_TEXT_BROKEN,
            "order_id": ORDER_ID,
            "issue_type": "broken_leak",
            "evidence": [],
        }
    )
    assert state["handoff"] is False
    es = state["evidence_status"]
    assert es["complete"] is False
    assert set(es["missing"]) == {"开箱视频", "面单照片", "破损部位照片"}
    assert state["ticket_draft"]["need_human_review"] is True
    assert "【需要您补充的材料】" in state["reply_draft"]["content"]


# ---------------------------------------------------------------------------
# 4. 数据源异常（DataSourceError）→ fetch_evidence 捕获 → handoff_human
# ---------------------------------------------------------------------------


def test_data_source_error_handoffs_without_exception(monkeypatch):
    import app.graph as g

    def _raise(_order_id):
        raise DataSourceError("Mock 数据源读取失败")

    monkeypatch.setattr(g, "get_order", _raise)
    state = g.compile_graph().invoke({"user_text": USER_TEXT_BROKEN, "order_id": ORDER_ID})
    assert state["handoff"] is True
    assert state["needs_human"] is True
    assert state["error"]["code"] == "DATA_SOURCE"
    assert state.get("ticket_draft") is None


# ---------------------------------------------------------------------------
# 5. 空用户输入 → router 降级 → handoff_human
# ---------------------------------------------------------------------------


def test_empty_user_text_handoffs():
    state = GR.invoke({"user_text": "", "order_id": ORDER_ID})
    assert state["handoff"] is True
    assert state["needs_human"] is True
    assert state["error"]["code"] == "EMPTY_INPUT"
    assert state.get("ticket_draft") is None


# ---------------------------------------------------------------------------
# 6. calc_compensation 赔付边界：超时效转人工 / 保价封顶截断
# ---------------------------------------------------------------------------


def test_calc_compensation_over_time_handoffs(monkeypatch):
    """签收超过 7 天 → handoff_human（P-TIME-001），不做自动赔付。

    signed_at 取「当前时间回推 10 天」，等价于作业建议的 11-01 场景，
    但不随运行日期漂移（固定日期在远期运行时可能不足 7 天而失效）。
    """
    _mock_order(monkeypatch, signed_at=datetime.now(TZ) - timedelta(days=10))

    sug = ro.calc_compensation(ORDER_ID, "broken_leak", True).suggestion

    assert sug.recommendation == "handoff_human"
    assert "超 7 天时效" in sug.reason
    assert sug.amount == 0.0
    assert sug.policy_ids == ["P-TIME-001"]


def test_calc_compensation_insured_cap_truncates(monkeypatch):
    """保价封顶截断：订单 5000 × 30% = 1500，保价 1000 → amount 截断为 1000。"""
    _mock_order(monkeypatch, order_amount=5000.0, insured=True, insured_amount=1000.0)

    sug = ro.calc_compensation(ORDER_ID, "broken_leak", True).suggestion

    assert sug.recommendation == "partial_refund"
    assert sug.amount == pytest.approx(1000.0), f"应按保价封顶为 1000，实际 {sug.amount}"
    assert sug.amount != pytest.approx(1500.0)
