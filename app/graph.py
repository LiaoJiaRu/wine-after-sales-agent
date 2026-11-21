"""LangGraph 编排主链路的骨架实现（M2 阶段）。

覆盖作业1-1 表4 要求的 State 字段：
  intent / sub_tasks / tool_results / evidence_status / judge_result / retry_count / handoff

当前只搭骨架：节点能串起来、State 能流转、Data 层异常能进 handoff_human；
不做 Judge、ReAct、并行等复杂逻辑——那是 M3/M4 的事。

异常兜底：LangGraph 0.2 不会自动把节点里的异常当 State 更新，
所有可能抛错的节点（fetch_evidence / check_evidence）都在内部 try/except，
把错误落到 State.error + 置 handoff=True，再由条件边转 handoff_human。
"""
from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from app.agents.router import route as classify_intent
from app.tools.errors import DataSourceError, NotFoundError, ToolError
from app.tools.readonly import (
    KNOWLEDGE_KEYWORDS,
    calc_compensation,
    check_evidence as _check_evidence,
    get_batch,
    get_logistics,
    get_order,
    search_policy,
)
from app.tools.write import create_ticket_draft, draft_reply


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class SubTaskState(TypedDict, total=False):
    """多意图子任务对象（M3 补齐阶段1）。

    task_type: 子任务类型，沿用 router.sub_tasks 的中文串（如"破损漏液"/"少件错发"/"储存饮用咨询"）
    status:    pending / success / failed
    result:    该子任务的取证结果（理赔类为订单/物流/批次/证据/赔付/政策；知识类为 knowledge_result）
    """

    task_type: str
    status: str
    result: dict[str, Any] | None


class GraphState(TypedDict, total=False):
    """LangGraph 全局状态（Typeddict 风格，所有字段可选）。

    作业1-1 表4 必填字段：
      intent, sub_tasks, tool_results, evidence_status, judge_result, retry_count, handoff
    其他字段：
      order_id: str                  —— 贯穿全链路的工单主键（工具都要它）
      issue_type: str                —— broken_leak / missing_item / wrong_item
      order / shipment               —— fetch_evidence 写回的只读快照
      ticket_draft / reply_draft     —— create_draft 写回的草稿（输出）
      error: dict                    —— 任何节点置 handoff=True 时同时落在这里
    """

    # —— 作业1-1 表4 要求的字段 ——
    intent: str
    sub_tasks: list[str]
    tool_results: dict[str, Any]
    evidence_status: dict[str, Any]
    judge_result: dict[str, Any] | None
    retry_count: int
    handoff: bool

    # —— 本骨架需要的其他字段 ——
    order_id: str
    issue_type: str
    user_text: str               # invoke({"user_text": ...}) 入口；需在 TypedDict 里声明否则 LangGraph 0.2 会丢掉
    order: dict[str, Any]
    shipment: dict[str, Any]
    batch: dict[str, Any]
    evidence: list[str]        # 调用方可在 invoke 输入里直接塞证据列表（E2E/单元测试路径）
    ticket_draft: dict[str, Any] | None
    reply_draft: dict[str, Any] | None
    knowledge_result: str        # knowledge_agent 写回的酒知识说明（M3 新增）
    task_outputs: list[SubTaskState]  # 多意图子任务对象列表（M3 补齐阶段1，intent=多意图 时由 router 生成）
    error: dict[str, Any]
    needs_human: bool            # CLI 层的 needs_human 标志（graph.handoff 与 needs_human 含义相同，双写便于 CLI 直接取）


DEFAULT_EMPTY: GraphState = {
    "order_id": "",
    "issue_type": "",
    "intent": "",
    "sub_tasks": [],
    "tool_results": {},
    "evidence_status": {},
    "judge_result": None,
    "retry_count": 0,
    "handoff": False,
    "order": {},
    "shipment": {},
    "batch": {},
    "ticket_draft": None,
    "reply_draft": None,
    "knowledge_result": "",
    "task_outputs": [],
    "error": {},
    "needs_human": False,
}


# ---------------------------------------------------------------------------
# 节点函数
# ---------------------------------------------------------------------------


# 最小 ReAct：把 router.sub_tasks 的中文子任务映射成工具层 issue_type。
# 真正的多意图并行（分别跑破损/少件/错发三条链路）是 M3 的事，
# 当前骨架只吃第一个子任务，其余 sub_tasks 原样带进 tool_results 供后续节点参考。
_ISSUE_MAP = {
    "破损漏液": "broken_leak",
    "破损": "broken_leak",
    "漏液": "broken_leak",
    "少件错发": "missing_item",
    "少件": "missing_item",
    "错发": "wrong_item",
}


def _derive_issue_type(sub_tasks: list[str]) -> str:
    """从 router.sub_tasks 推导工具层 issue_type；推不出来默认 broken_leak。"""
    for t in sub_tasks:
        mapped = _ISSUE_MAP.get(t.strip())
        if mapped:
            return mapped
    return "broken_leak"


# 合规守卫关键词：只要出现退款/退钱诉求（资金操作）一律转人工，
# 优先级高于意图路由与知识检索——命中即截断整条链路，不做赔付、不出草稿、不解答知识。
_REFUND_KEYWORDS = (
    "退款",
    "退钱",
    "直接退款",
    "直接退钱",
    "立即退款",
    "立刻退款",
    "马上退款",
    "马上退钱",
)


def load_session(state: GraphState) -> dict[str, Any]:
    """会话加载（当前先 Mock 透传：只保证 order_id 存在）。

    M4 接 Redis 后在这里把会话槽位/历史拉回来；当前骨架只做空输入守卫：
    order_id 为空时直接进 handoff，避免后续节点炸。

    同时把作业1-1 表4 要求的两个非必填字段（retry_count / judge_result）显式写回，
    否则 LangGraph 0.2 TypedDict State 不会把它们收集到 invoke 结果里。
    """
    order_id = (state.get("order_id") or "").strip()
    common_fills = {"retry_count": 0, "judge_result": None}
    if not order_id:
        return {
            "handoff": True,
            "needs_human": True,
            "error": {"code": "EMPTY_ORDER_ID", "message": "order_id 为空，无法加载会话", "retryable": False},
            **common_fills,
        }
    return {"order_id": order_id, **common_fills}


def _build_task_outputs(intent: str, sub_tasks: list[str]) -> list[SubTaskState]:
    """intent=多意图 时把 sub_tasks 字符串升级为任务对象列表（初始全部 pending）。

    非多意图返回空列表 —— 老的单意图链路完全不受影响（向后兼容）。
    """
    if intent != "多意图":
        return []
    outputs: list[SubTaskState] = []
    for raw in sub_tasks:
        name = (raw or "").strip()
        if not name:
            continue
        outputs.append({"task_type": name, "status": "pending", "result": None})
    return outputs


def router(state: GraphState) -> dict[str, Any]:
    """复用现有 route()。同时把 needs_human 同步到 State.needs_human，便于 CLI 层直接取。

    合规守卫（先于 LLM、先于任何分流）：用户要求"直接退款"类强资金诉求时，不进入自动流程，
    直接置 handoff/needs_human=True（error.code=COMPLIANCE_REFUND），由条件边转 handoff_human。
    该判定排在知识分流之前，因此"退款 + 酒知识"混装诉求只会转人工，不会解答知识。
    草稿工具本身只生成草稿、不退款，这里是第一道拦截。
    """
    text = state.get("user_text", "") or state.get("text", "") or ""
    if any(k in text for k in _REFUND_KEYWORDS):
        return {
            "intent": "其他",
            "sub_tasks": [],
            "handoff": True,
            "needs_human": True,
            "error": {
                "code": "COMPLIANCE_REFUND",
                "message": "用户要求直接退款，涉及资金操作，转人工审核，不自动执行",
                "retryable": False,
            },
            "user_text": text,
        }
    decision = classify_intent(text)
    handoff = bool(decision.needs_human)
    sub_tasks = decision.sub_tasks or []
    return {
        "intent": decision.intent,
        "sub_tasks": sub_tasks,
        "task_outputs": _build_task_outputs(decision.intent, sub_tasks),
        "handoff": handoff,
        "needs_human": handoff,
        "error": decision.error or {},
        "user_text": text,
    }


def _is_knowledge_task(task_type: str) -> bool:
    """判断子任务是否属于酒知识类（走 knowledge_agent，而不是理赔取证）。"""
    name = (task_type or "").strip()
    return name in {"储存饮用咨询", "酒知识", "知识咨询"} or any(k in name for k in KNOWLEDGE_KEYWORDS)


def _fetch_claim(
    order_id: str, issue_type: str, provided_evidence: list[str], query: str
) -> dict[str, Any]:
    """单个理赔子任务的取证：订单 + 物流 + 批次 + 证据核验 + 赔付建议 + 政策检索。

    异常直接向上抛，由调用方统一降级为 handoff（与 M2 行为一致）。
    """
    order = get_order(order_id).order
    shipment = get_logistics(order_id).shipment
    batch = get_batch(order.batch_no).batch
    evidence = _check_evidence(order_id, issue_type, provided_evidence)
    compensation = calc_compensation(order_id, issue_type, evidence.status.complete)
    search = search_policy(query or issue_type)
    return {
        "order": order.model_dump(),
        "shipment": shipment.model_dump(),
        "batch": batch.model_dump(),
        "evidence": evidence.status.model_dump(),
        "compensation": compensation.suggestion.model_dump(),
        "policy_hits": [h.model_dump() for h in search.hits],
        "issue_type": issue_type,
    }


def fetch_evidence(state: GraphState) -> dict[str, Any]:
    """按任务取证：多意图时遍历 task_outputs 逐个执行，单意图保持原串行行为。

    - 理赔类子任务（破损/少件/错发）：各自调用 _fetch_claim，结果写入该任务的 result，status=success；
      任一理赔任务抛 NotFound/DataSource/ToolError → 该任务 status=failed 并整体转 handoff（同 M2 语义）。
    - 知识类子任务：调用 knowledge_agent 写 result={"knowledge_result": ...}。
    - 兼容：tool_results 仍取「第一个理赔任务」的结果，字段结构不变，下游 create_draft 无需改动；
      单意图（task_outputs 为空）走原逻辑，search_policy 的查询串与取值口径完全一致。
    """
    order_id = state["order_id"]  # load_session 已保证存在
    sub_tasks = state.get("sub_tasks", []) or []
    provided_evidence = state.get("evidence", []) or []
    query = " ".join(sub_tasks)
    tasks = state.get("task_outputs") or []
    claim_tasks = [t for t in tasks if _ISSUE_MAP.get((t.get("task_type") or "").strip())]

    # —— 兼容路径：单意图，或 task_outputs 为空/无理赔任务 ——
    if not claim_tasks:
        issue_type = state.get("issue_type") or _derive_issue_type(sub_tasks)
        try:
            results = _fetch_claim(order_id, issue_type, provided_evidence, query)
        except (NotFoundError, DataSourceError, ToolError) as exc:
            err = {"code": getattr(exc, "code", "TOOL_ERROR"), "message": getattr(exc, "message", str(exc))}
            return {"handoff": True, "needs_human": True, "error": err, "issue_type": issue_type}

        results["sub_tasks"] = sub_tasks
        return {
            "order": results["order"],
            "shipment": results["shipment"],
            "batch": results["batch"],
            "evidence_status": results["evidence"],
            "tool_results": results,
            "issue_type": issue_type,
        }

    # —— 多意图路径：逐个子任务执行 ——
    updated: list[SubTaskState] = []
    primary: dict[str, Any] | None = None
    primary_issue = ""
    first_error: dict[str, Any] | None = None

    for task in tasks:
        item: SubTaskState = {
            "task_type": task.get("task_type", ""),
            "status": task.get("status", "pending"),
            "result": task.get("result"),
        }
        name = (item["task_type"] or "").strip()
        mapped = _ISSUE_MAP.get(name)

        if mapped:
            try:
                payload = _fetch_claim(order_id, mapped, provided_evidence, query)
            except (NotFoundError, DataSourceError, ToolError) as exc:
                err = {"code": getattr(exc, "code", "TOOL_ERROR"), "message": getattr(exc, "message", str(exc))}
                item["status"] = "failed"
                item["result"] = {"error": err}
                first_error = first_error or err
                updated.append(item)
                continue
            item["status"] = "success"
            item["result"] = payload
            if primary is None:
                primary, primary_issue = payload, mapped
        elif _is_knowledge_task(name):
            knowledge = knowledge_agent(state).get("knowledge_result", "")
            item["result"] = {"knowledge_result": knowledge}
            item["status"] = "success" if knowledge and knowledge != KNOWLEDGE_QUERY_FALLBACK else "failed"
        updated.append(item)

    if first_error is not None:
        return {
            "task_outputs": updated,
            "handoff": True,
            "needs_human": True,
            "error": first_error,
            "issue_type": primary_issue or state.get("issue_type", ""),
        }

    assert primary is not None  # claim_tasks 非空，循环内至少成功一次
    primary["sub_tasks"] = sub_tasks
    return {
        "task_outputs": updated,
        "order": primary["order"],
        "shipment": primary["shipment"],
        "batch": primary["batch"],
        "evidence_status": primary["evidence"],
        "tool_results": primary,
        "issue_type": primary_issue,
    }


def check_evidence(state: GraphState) -> dict[str, Any]:
    """证据完整性闸门：

    真正的“是否缺证”已经在 fetch_evidence 里跑完了，这里只做两层语义层处理：
    1. 把 evidence_status 里的 missing 列表原样保留给下游 create_draft 生成补证清单；
    2. 证据缺失但有进展时**不转人工**（留着 create_draft 生成"请补证"回复），
       只有 complete=True 才会继续走赔付评估。两种情况都走同一后续路径。
    """
    status = state.get("evidence_status") or {}
    return {
        "handoff": False,
        "needs_human": False,
    }


def _merge_knowledge_into_reply(state: GraphState, reply_dict: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """多意图合并：理赔诉求 + 知识诉求时，把 P-KNOW 前 2 条追加到回复草稿末尾。

    只读 State.user_text / sub_tasks 判定是否含知识诉求词；不含则原样返回，
    保证纯理赔文本的回复内容与合并前完全一致。返回 (reply_dict, knowledge_text)。
    """
    text = f"{state.get('user_text') or ''} {' '.join(state.get('sub_tasks') or [])}"
    if not any(k in text for k in KNOWLEDGE_KEYWORDS):
        return reply_dict, ""

    knowledge = knowledge_agent(state).get("knowledge_result", "")
    if not knowledge or knowledge == KNOWLEDGE_QUERY_FALLBACK:
        return reply_dict, ""

    items = [line for line in knowledge.split("\n") if line.strip()]
    block = "【酒知识解答】\n" + "\n".join(f"{i}. {line}" for i, line in enumerate(items, 1))
    reply_dict["content"] = reply_dict["content"].rstrip() + "\n\n" + block
    return reply_dict, knowledge


_ISSUE_LABELS = {
    "broken_leak": "破损漏液",
    "missing_item": "少件",
    "wrong_item": "错发",
}


def _claim_payloads(state: GraphState) -> list[tuple[str, dict[str, Any]]]:
    """取出所有成功的理赔子任务结论（知识类任务没有 evidence/compensation，自动排除）。"""
    out: list[tuple[str, dict[str, Any]]] = []
    for task in state.get("task_outputs") or []:
        if (task.get("status") or "") != "success":
            continue
        payload = task.get("result") or {}
        if not payload.get("evidence") or not payload.get("compensation"):
            continue
        out.append(((payload.get("issue_type") or "").strip(), payload))
    return out


def _secondary_claim_suffix(payloads: list[tuple[str, dict[str, Any]]]) -> str:
    """把次要理赔任务写进工单 summary 文本（不改 schema，只拼 summary 串）。"""
    extras = [it for it, _ in payloads[1:] if it]
    if not extras:
        return ""
    parts = [f"另发现{_ISSUE_LABELS.get(it, it)}问题，建议一并处理" for it in extras]
    return "；" + "；".join(parts)


def _merged_evidence(state: GraphState, payloads: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """多理赔任务时合并证据状态：missing 去重保序，全部齐全才算齐全。"""
    if len(payloads) <= 1:
        return state.get("evidence_status", {}) or {}
    missing: list[str] = []
    complete = True
    for _, payload in payloads:
        ev = payload.get("evidence") or {}
        complete = complete and bool(ev.get("complete"))
        for item in ev.get("missing") or []:
            if item not in missing:
                missing.append(item)
    return {"complete": complete, "missing": missing}


def create_draft(state: GraphState) -> dict[str, Any]:
    """生成工单草稿 + 回复草稿。任何异常 → handoff/needs_human=True。

    - 工单：入参仍按"主诉"（tool_results / issue_type），多理赔任务只在 summary 末尾追加
      "另发现少件问题，建议一并处理"，不触碰 TicketDraft schema。
    - 回复：多个理赔任务的补证清单合并去重后统一列出。
    - 多意图（理赔 + 酒知识）时再把知识条目追加到回复末尾。
    """
    order_id = state.get("order_id", "")
    issue_type = state.get("issue_type") or _derive_issue_type(state.get("sub_tasks", []))
    try:
        payloads = _claim_payloads(state)
        ticket = create_ticket_draft(order_id, issue_type, tool_results=state.get("tool_results", {}))
        suffix = _secondary_claim_suffix(payloads)
        ticket_draft = (
            ticket.draft.model_copy(update={"summary": ticket.draft.summary + suffix})
            if suffix
            else ticket.draft
        )

        ev_status = _merged_evidence(state, payloads)
        comp = state.get("tool_results", {}).get("compensation") or {}
        order_dict = state.get("order") or {}
        reply = draft_reply(
            order_id,
            issue_type,
            facts={
                "product_name": order_dict.get("product_name", ""),
                "evidence_complete": ev_status.get("complete", True),
                "evidence_missing": ev_status.get("missing", []),
                "recommendation": comp.get("recommendation"),
                "amount": comp.get("amount", 0.0),
            },
        )
        reply_dict, knowledge = _merge_knowledge_into_reply(state, reply.reply.model_dump())
        return {
            "ticket_draft": ticket_draft.model_dump(),
            "reply_draft": reply_dict,
            "knowledge_result": knowledge,
            "handoff": False,
            "needs_human": False,
        }
    except ToolError as exc:
        return {
            "handoff": True, "needs_human": True,
            "error": {"code": getattr(exc, "code", "TOOL_ERROR"), "message": getattr(exc, "message", str(exc))},
        }
    except Exception as exc:  # pragma: no cover - 兜底
        return {"handoff": True, "needs_human": True, "error": {"code": "INTERNAL", "message": str(exc)}}


KNOWLEDGE_QUERY_FALLBACK = "关于该问题我暂时没有找到相关说明"
_KNOWLEDGE_INTENTS = {"储存饮用咨询"}


def knowledge_agent(state: GraphState) -> dict[str, Any]:
    """酒知识专业子 Agent 节点（M3）。

    读取 user_text（回退 sub_tasks）→ search_policy → 取 P-KNOW 前 2 条拼成一段说明，
    写入 State.knowledge_result；检索不到或异常时写兜底话术，不 handoff、不影响主链。
    """
    query = (state.get("user_text") or "").strip()
    if not query:
        query = " ".join(state.get("sub_tasks") or [])

    try:
        hits = [h for h in search_policy(query, top_k=3).hits if h.id.startswith("P-KNOW")][:2]
    except Exception:  # 知识检索失败不阻断对话，直接兜底
        hits = []

    if not hits:
        return {"knowledge_result": KNOWLEDGE_QUERY_FALLBACK}

    text = "\n".join(f"{h.title}：{h.content}" for h in hits)
    return {"knowledge_result": text}


def handoff_human(state: GraphState) -> dict[str, Any]:
    """转人工节点：标记 handoff/needs_human=True，携带 error 供人工座席参考。"""
    err = state.get("error") or {}
    return {
        "handoff": True,
        "needs_human": True,
        "error": {
            "code": err.get("code", "HANDOFF"),
            "message": err.get("message", "需要人工介入"),
            "retryable": False,
        },
    }


def dummy_end(state: GraphState) -> dict[str, Any]:
    """正常流转终点（LangGraph 0.2 仍要求返回 dict）。"""
    return state


# ---------------------------------------------------------------------------
# 条件边辅助
# ---------------------------------------------------------------------------


def _route_after_fetch(state: GraphState) -> str:
    # fetch_evidence 内部抛错会写 handoff=True；正常走完则 False → check_evidence
    return "handoff_human" if state.get("handoff") else "check_evidence"


def _route_after_router(state: GraphState) -> str:
    # router 里 needs_human=True 也走 handoff（空输入、路由降级、非法 intent）
    if state.get("handoff"):
        return "handoff_human"
    # 酒知识咨询走专业子 Agent：只按 intent 判定，避免“漏了…怎么保存”这类理赔文本被误分流
    intent = state.get("intent")
    if intent in _KNOWLEDGE_INTENTS:
        return "knowledge_agent"
    # 兼容：intent 判成"其他"但子任务只有知识咨询；多意图必须走取证主链（task_outputs 逐任务执行）
    if intent != "多意图" and "储存饮用咨询" in (state.get("sub_tasks") or []):
        return "knowledge_agent"
    return "fetch_evidence"


def _route_after_load(state: GraphState) -> str:
    # load_session 空 order_id 写 handoff=True → 直接 handoff_human，不走 router
    return "handoff_human" if state.get("handoff") else "router"


# ---------------------------------------------------------------------------
# compile_graph
# ---------------------------------------------------------------------------


def compile_graph():
    """返回已编译的 StateGraph，支持 .invoke({...})。"""
    sg = StateGraph(GraphState)

    sg.add_node("load_session", load_session)
    sg.add_node("router", router)
    sg.add_node("fetch_evidence", fetch_evidence)
    sg.add_node("check_evidence", check_evidence)
    sg.add_node("create_draft", create_draft)
    sg.add_node("knowledge_agent", knowledge_agent)
    sg.add_node("handoff_human", handoff_human)

    # load_session 后加条件边：空 order_id 直接 handoff_human（不进 router，避免同 step 两次写 handoff）
    sg.add_edge(START, "load_session")
    sg.add_conditional_edges("load_session", _route_after_load)
    sg.add_conditional_edges("router", _route_after_router)
    sg.add_conditional_edges("fetch_evidence", _route_after_fetch)
    sg.add_edge("check_evidence", "create_draft")
    sg.add_edge("create_draft", END)
    sg.add_edge("knowledge_agent", END)
    sg.add_edge("handoff_human", END)

    return sg.compile()


__all__ = ["compile_graph", "GraphState", "DEFAULT_EMPTY"]
