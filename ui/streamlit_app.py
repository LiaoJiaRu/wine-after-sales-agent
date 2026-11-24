"""M5 Streamlit 四视图：对话、工单、证据与回复、审核与追踪。"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

from app.graph import compile_graph
from app.llm import trace_url
from app.memory import sqlite_client
from app.tools.errors import NotFoundError
from app.tools.readonly import get_order, get_logistics

EVIDENCE_NAMES = ["开箱视频", "面单照片", "破损部位照片", "整箱称重照片", "商品照片"]
GRAPH_RUNTIME_MESSAGE = "系统繁忙，已转人工处理"
ROLE_CUSTOMER = "顾客端"
ROLE_MERCHANT = "商家审核端"


def _jsonable(obj: Any) -> Any:
    """递归把 datetime / date 对象转成 ISO 字符串，避免 st.json 把它们 repr 成
    原始构造形式或显示不全，影响最终 State JSON 的可读性与可再解析性。

    只处理 Python 原生 datetime / date；字符串/数字/容器原样保留，便于下游再解析。
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


@st.cache_resource
def get_graph():
    """整个 Streamlit 服务复用同一张已编译图。"""
    return compile_graph()


def init_session() -> None:
    defaults: dict[str, Any] = {
        "graph_state": {},
        "messages": [],
        "evidence": [],
        "last_user_text": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def invoke_graph(user_text: str, order_id: str, session_id: str) -> dict[str, Any]:
    """携带当前证据调用真实 LangGraph，并把 Trace 链接写回最终 State。"""
    evidence = list(st.session_state.evidence)
    payload = {
        "user_text": user_text,
        "order_id": order_id.strip(),
        "session_id": session_id.strip(),
        "evidence": evidence,
    }
    try:
        state = get_graph().invoke(
            payload,
            config={"recursion_limit": 32},
        )
    except Exception:
        state = {
            **payload,
            "handoff": True,
            "needs_human": True,
            "error": {
                "code": "UI_GRAPH_RUNTIME",
                "message": GRAPH_RUNTIME_MESSAGE,
                "retryable": False,
            },
        }

    url = trace_url()
    if url:
        state["langfuse_trace_url"] = url
    st.session_state.graph_state = state
    st.session_state.evidence = list(state.get("evidence") or evidence)
    st.session_state.last_user_text = user_text
    return state


def assistant_text(state: dict[str, Any]) -> str:
    reply = state.get("reply_draft") or {}
    if reply.get("content"):
        return reply["content"]
    if state.get("knowledge_result"):
        return str(state["knowledge_result"])
    error = state.get("error") or {}
    if error.get("code") == "UI_GRAPH_RUNTIME":
        return GRAPH_RUNTIME_MESSAGE
    if error:
        return f"{error.get('code', 'ERROR')}：{error.get('message', '执行失败')}"
    return "本次执行已完成。"


def record_turn(user_text: str, state: dict[str, Any]) -> None:
    st.session_state.messages.append({"role": "user", "content": user_text})
    st.session_state.messages.append({"role": "assistant", "content": assistant_text(state)})


def render_execution_status(state: dict[str, Any]) -> None:
    if not state:
        st.info("提交一条用户原话后，这里会展示执行状态与最终 JSON。")
        return
    error = state.get("error") or {}
    judge_result = state.get("judge_result") or {}
    if error.get("code") == "UI_GRAPH_RUNTIME":
        st.warning(GRAPH_RUNTIME_MESSAGE)
    elif error:
        st.error(f"{error.get('code', 'ERROR')}：{error.get('message', '执行失败')}")
    elif state.get("handoff"):
        st.warning("执行完成：已转人工处理。")
    elif judge_result.get("pass") is False:
        st.error("执行完成：Judge 未通过。")
    elif judge_result.get("pass") is True:
        st.success("执行完成：Judge 审核通过。")
    else:
        st.success("执行完成。")


def render_conversation(order_id: str, session_id: str) -> None:
    state = st.session_state.graph_state
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input("请输入售后问题或酒知识咨询")
    if prompt:
        result = invoke_graph(prompt, order_id, session_id)
        record_turn(prompt, result)
        st.rerun()

    st.subheader("执行状态")
    render_execution_status(state)

    st.divider()
    st.subheader("证据上传模拟")
    st.caption("文件仅用于模拟，不会读取或上传内容；追加证据后将复用上一轮原话重新执行。")
    uploaded_file = st.file_uploader(
        "选择证据文件",
        type=["mp4", "mov", "jpg", "jpeg", "png"],
        key="evidence_file",
    )
    evidence_name = st.selectbox("对应证据名称", EVIDENCE_NAMES, key="evidence_name")
    current_evidence = st.session_state.evidence
    st.write("当前证据：" + ("、".join(current_evidence) if current_evidence else "暂无"))

    can_append = uploaded_file is not None and bool(st.session_state.last_user_text)
    if st.button("追加证据并重新执行", disabled=not can_append):
        if evidence_name not in st.session_state.evidence:
            st.session_state.evidence.append(evidence_name)
        result = invoke_graph(st.session_state.last_user_text, order_id, session_id)
        upload_text = f"上传证据（模拟）：{evidence_name}（{uploaded_file.name}）"
        record_turn(upload_text, result)
        st.rerun()
    if uploaded_file is not None and not st.session_state.last_user_text:
        st.info("请先提交一次对话，再使用上传模拟补充证据。")

    st.subheader("最终 State JSON")
    st.json(_jsonable(state) if state else {})


def _build_ticket_view(state: dict[str, Any]) -> dict[str, Any] | None:
    """构建工单展示用的合并视图：SQLite 真实状态 + Agent 原始快照。

    纯函数，方便单元测试，不依赖 Streamlit UI。
    返回 None 表示 state 里没有 ticket_draft；返回 dict 包含以下键：
      - source: "sqlite" 或 "fallback"（降级展示 Agent 快照）
      - top_status / top_created_at / top_amount / top_summary  顶层显示用字段
      - evidence_status / compensation
      - full_view: 渲染"查看完整工单"的完整结构
      - sqlite_row  若命中 SQLite，含该行字典（供上层断言）
      - warning     降级时的提示文本
    """
    ticket = (state or {}).get("ticket_draft") or {}
    if not ticket:
        return None

    draft_id = str(ticket.get("draft_id") or "")
    sqlite_row = None
    _db_error: str | None = None

    if draft_id:
        try:
            sqlite_row = sqlite_client.get_ticket(draft_id)
        except Exception as exc:
            _msg = str(exc).lower()
            try:
                _current_db = str(sqlite_client._db_path())
            except Exception:
                _current_db = "<unknown>"
            if "unable to open database file" in _msg or "no such table" in _msg:
                _db_error = (
                    f"数据库访问异常：{exc}。"
                    f"可能原因：SQLite 路径漂移（TICKETS_DB_PATH 未设置、CWD 不对）。"
                    f"当前 _db_path() = {_current_db}"
                )
            elif "database is locked" in _msg:
                _db_error = f"数据库被锁定：{exc}，请稍后再试。"
            else:
                _db_error = f"数据库读取异常：{exc}"
            sqlite_row = None

    evidence_status = state.get("evidence_status") or ticket.get("evidence_status") or {}
    compensation = ticket.get("compensation") or (state.get("tool_results") or {}).get("compensation") or {}

    if sqlite_row is not None:
        summary = sqlite_row.get("summary") or ticket.get("summary") or ""
        status = sqlite_row.get("status", "")
        amount = sqlite_row.get("amount", 0.0)
        created_at = sqlite_row.get("created_at", "")
        full_view = {**sqlite_row, "agent_snapshot": ticket}
        source = "sqlite"
        warning = None
    else:
        summary = ticket.get("summary") or ""
        status = ticket.get("status") or "draft"
        amount = (ticket.get("compensation") or {}).get("amount", 0.0)
        created_at = ticket.get("created_at", "")
        full_view = ticket
        source = "fallback"
        if _db_error is not None:
            warning = f"{_db_error}。降级展示 Agent 快照。"
        elif draft_id:
            warning = f"工单 {draft_id} 在库中不存在，展示 Agent 快照。"
        else:
            warning = None

    return {
        "source": source,
        "top_status": status,
        "top_created_at": created_at,
        "top_amount": float(amount or 0.0),
        "top_summary": summary,
        "evidence_status": evidence_status,
        "compensation": compensation,
        "full_view": full_view,
        "sqlite_row": sqlite_row,
        "warning": warning,
    }


def render_ticket(state: dict[str, Any]) -> None:
    """工单摘要：优先从 SQLite 取"当前真实状态"（含审核结果），找不到时降级展示 Agent 快照。"""
    view = _build_ticket_view(state)
    if view is None:
        st.info("当前没有工单草稿。")
        return

    st.subheader("工单摘要")
    st.write(view["top_summary"] or "暂无摘要")
    st.caption(
        f"状态：{view['top_status']}｜"
        f"金额：{view['top_amount']:.2f}｜"
        f"创建：{view['top_created_at']}"
    )
    if view.get("warning"):
        st.warning(view["warning"])

    col_evidence, col_compensation = st.columns(2)
    with col_evidence:
        st.subheader("证据状态")
        st.json(_jsonable(view["evidence_status"]))
    with col_compensation:
        st.subheader("赔付建议")
        st.json(_jsonable(view["compensation"]))

    with st.expander("查看完整工单"):
        st.json(_jsonable(view["full_view"]))


def render_reply_and_knowledge(state: dict[str, Any]) -> None:
    reply = state.get("reply_draft") or {}
    content = reply.get("content") or ""
    st.subheader("回复草稿")
    if content:
        st.text(content)
    else:
        st.info("当前没有回复草稿。")

    knowledge = state.get("knowledge_result")
    if knowledge:
        st.subheader("酒知识检索结果")
        st.write(knowledge)


def render_review_and_trace(state: dict[str, Any]) -> None:
    judge_result = state.get("judge_result") or {}
    st.subheader("Judge 审核")
    if judge_result:
        if judge_result.get("pass") is True:
            st.success("审核通过")
        else:
            st.error("审核未通过")
        st.json(judge_result)
    else:
        st.info("本次执行没有 Judge 审核结果。")

    st.subheader("Langfuse Trace")
    url = state.get("langfuse_trace_url") or state.get("trace_url")
    if isinstance(url, str) and url.startswith(("http://", "https://")):
        st.markdown(f"[查看 Trace]({url})")
    else:
        st.info("本次执行未生成可用的 Trace 链接。")
    st.write(f"Judge 重跑次数：{state.get('retry_count', 0)}")


def render_ticket_card(ticket: dict[str, Any]) -> None:
    """单张待审核工单卡片：工单号 / 订单号 / 金额 / 摘要 + 审核动作 + 原始订单联查。"""
    with st.container(border=True):
        col_id, col_order, col_amount = st.columns(3)
        col_id.markdown(f"**工单号**\n\n`{ticket.get('draft_id', '')}`")
        col_order.markdown(f"**订单号**\n\n{ticket.get('order_id', '')}")
        col_amount.markdown(f"**金额**\n\n{float(ticket.get('amount') or 0.0):.2f}")
        st.markdown(f"**摘要**：{ticket.get('summary', '')}")
        st.caption(f"状态：{ticket.get('status', '')}｜创建时间：{ticket.get('created_at', '')}")

        order_id = str(ticket.get("order_id", ""))
        with st.expander("🔍 关联订单原始信息"):
            _render_original_order(order_id)

        draft_id = str(ticket.get("draft_id", ""))
        col_approve, col_reject = st.columns(2)
        if col_approve.button("审核通过", key=f"approve-{draft_id}", type="primary"):
            review_ticket(draft_id, "approved")
        if col_reject.button("打回重审", key=f"reject-{draft_id}"):
            review_ticket(draft_id, "rejected")


def _render_original_order(order_id: str) -> None:
    """从 Mock 数据联查原始订单与物流信息，辅助商家区分“订单”与“售后工单”。"""
    if not order_id:
        st.info("工单打不到 order_id，无法联查原始订单信息。")
        return

    col_order, col_logistics = st.columns(2)
    with col_order:
        st.markdown("**📦 原始订单**")
        try:
            result = get_order(order_id)
            order = result.order
            st.json({
                "订单号": order.order_id,
                "商品名": order.product_name,
                "SKU": order.sku,
                "批次号": order.batch_no,
                "数量": order.quantity,
                "原始金额": f"{order.order_amount:.2f} 元",
                "保价": f"{order.insured_amount:.2f} 元" if order.insured else "未投保价",
                "签收时间": order.signed_at.strftime("%Y-%m-%d %H:%M:%S") if order.signed_at else "—",
                "订单状态": order.status,
            })
        except NotFoundError:
            st.warning(f"原始订单 {order_id} 在 Mock 数据中不存在。")
        except Exception as exc:
            st.error(f"获取原始订单失败：{exc}")

    with col_logistics:
        st.markdown("**🚚 物流信息**")
        try:
            result = get_logistics(order_id)
            shipment = result.shipment
            st.json({
                "物流单号": shipment.waybill_no,
                "承运方": shipment.carrier,
                "物流状态": shipment.status,
                "签收时间": shipment.signed_at.strftime("%Y-%m-%d %H:%M:%S") if shipment.signed_at else "未签收",
                "异常记录": shipment.abnormal or "无",
                "轨迹条数": len(shipment.traces),
            })
        except NotFoundError:
            st.warning(f"物流记录 {order_id} 在 Mock 数据中不存在。")
        except Exception as exc:
            st.error(f"获取物流信息失败：{exc}")


def review_ticket(draft_id: str, status: str) -> None:
    """更新工单状态并强制刷新商家端列表。"""
    try:
        updated = sqlite_client.update_ticket_status(draft_id, status)
    except Exception:
        st.warning("工单状态更新失败，请稍后重试。")
        return
    if not updated:
        st.warning(f"未找到工单 {draft_id}，可能已被处理。")
        return
    action = "审核通过" if status == "approved" else "打回重审"
    st.success(f"工单 {draft_id} 已{action}。")
    st.rerun()


def render_merchant_review() -> None:
    """商家审核端：顶部联查原始订单，待审核卡片逐张审核（每张卡也可单独联查）。"""
    with st.expander("🔍 联查任意订单与物流信息（顶部快捷入口）", expanded=False):
        lookup_order_id = st.text_input(
            "order_id",
            value=st.session_state.get("merchant_lookup_order_id", "SO20261116001"),
            key="merchant_lookup_order_id",
            help="输入任意订单号，查询原始订单与物流。默认 SO20261116001 为演示订单。",
        )
        if st.button("查询并联查", key="merchant_lookup_submit", type="secondary"):
            _render_original_order(lookup_order_id.strip())

    st.subheader("待审核工单")
    try:
        tickets = sqlite_client.list_tickets(status="draft")
    except Exception:
        st.warning("工单库暂时不可用，请稍后重试。")
        return

    if not tickets:
        st.info("当前没有待审核的工单。")
        return

    st.caption(f"共 {len(tickets)} 条待审核工单")
    for ticket in tickets:
        render_ticket_card(ticket)


def main() -> None:
    st.set_page_config(page_title="葡萄酒售后 Agent", layout="wide")
    init_session()
    st.title("葡萄酒售后 Agent 工作台")

    with st.sidebar:
        role = st.radio("选择身份", [ROLE_CUSTOMER, ROLE_MERCHANT], key="role")
        st.divider()

    if role == ROLE_MERCHANT:
        st.caption("商家审核端：审核顾客工单或打回重审")
        render_merchant_review()
        return

    st.caption("对话 → 工单 → 证据与回复 → 审核与追踪")
    with st.sidebar:
        st.header("会话参数")
        order_id = st.text_input("order_id", value="SO20261116001")
        session_id = st.text_input("session_id", value="ui-session-001")

    tab_chat, tab_ticket, tab_reply, tab_review = st.tabs(
        ["对话", "工单", "证据与回复", "审核与追踪"]
    )
    state = st.session_state.graph_state
    with tab_chat:
        render_conversation(order_id, session_id)
    with tab_ticket:
        render_ticket(state)
    with tab_reply:
        render_reply_and_knowledge(state)
    with tab_review:
        render_review_and_trace(state)


if __name__ == "__main__":
    main()
