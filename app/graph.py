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

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from app.agents.router import route as classify_intent
from app.llm import LLMError, judge_draft
from app.memory import redis_client, sqlite_client
from app.tools.errors import DataSourceError, NotFoundError, ToolError
from app.tools.schemas import EvidenceStatus
from app.tools.readonly import (
    KNOWLEDGE_KEYWORDS,
    calc_compensation,
    check_evidence as _check_evidence,
    get_batch,
    get_logistics,
    get_order,
    search_policy,
)
from app.tools.write import create_ticket_draft, draft_reply, write_handoff_queue


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
    session_id: str              # 多轮会话标识（M4 新增）：有值时 load_session 读 Redis、create_draft 写 Redis
    judge_feedback: str          # Judge 复核意见（M4 阶段3）：失败重跑时注入下一轮 create_draft/fetch_evidence
    error: dict[str, Any]
    needs_human: bool            # CLI 层的 needs_human 标志（graph.handoff 与 needs_human 含义相同，双写便于 CLI 直接取）


DEFAULT_EMPTY: GraphState = {
    "order_id": "",
    "issue_type": "",
    "intent": "",
    "sub_tasks": [],
    "session_id": "",
    "judge_feedback": "",
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
    """从 router.sub_tasks 推导工具层 issue_type；识别不到时返回空串。

    不再兜底 broken_leak：物流延迟/真伪争议/标签保质期等非理赔意图若被当成破损，
    会误算 30% 赔付（354 元）。空串由 fetch_evidence / create_draft 统一转 handoff_human。
    """
    for t in sub_tasks:
        mapped = _ISSUE_MAP.get((t or "").strip())
        if mapped:
            return mapped
    return ""


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
    """会话加载（M4）：有 session_id 时从 Redis 拉回上一轮的 order_id / evidence。

    合并规则：本轮 State 显式传入的字段优先，历史只补空缺（不覆盖用户新输入）。
    Redis 不可用时 redis_client 自动降级为进程内字典，这里不感知、不抛错。

    同时把作业1-1 表4 要求的两个非必填字段（retry_count / judge_result）显式写回，
    否则 LangGraph 0.2 TypedDict State 不会把它们收集到 invoke 结果里。
    """
    session_id = (state.get("session_id") or "").strip()
    history: dict[str, Any] = {}
    slots: dict[str, Any] = {}
    if session_id:
        try:
            history = redis_client.load_session(session_id) or {}
            slots = history.get("slots") or {}
        except Exception:  # 缓存故障不允许阻断主链路
            history, slots = {}, {}

    order_id = (state.get("order_id") or "").strip() or str(slots.get("order_id") or "").strip()
    evidence = list(state.get("evidence") or [])
    if not evidence:
        history_evidence = slots.get("evidence") or []
        if isinstance(history_evidence, str):
            history_evidence = [history_evidence]
        evidence = [str(item) for item in history_evidence]

    common_fills: dict[str, Any] = {"retry_count": 0, "judge_result": None}
    if session_id:
        common_fills["session_id"] = session_id
    if not order_id:
        return {
            "handoff": True,
            "needs_human": True,
            "error": {"code": "EMPTY_ORDER_ID", "message": "order_id 为空，无法加载会话", "retryable": False},
            **common_fills,
        }

    merged: dict[str, Any] = {"order_id": order_id, **common_fills}
    if evidence:
        merged["evidence"] = evidence
    return merged


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


# 证据槽位关键词：顺序与 REQUIRED_EVIDENCE 对齐，便于合并后保持稳定顺序
_EVIDENCE_KEYWORDS = ("开箱视频", "面单照片", "破损部位照片", "整箱称重照片")
_NEGATIONS = ("还没", "没", "未", "无", "没有")


def extract_evidence_from_text(text: str) -> list[str]:
    """从用户原话里抽取"已提供"的证据（M4 槽位填充）。

    纯关键词映射，但做两点防护，避免把"还没拍开箱视频""开箱视频要拍吗"误判为已提供：
    - 关键词前 6 个字内出现否定词（没/未/无/没有/还没）→ 跳过；
    - 关键词后 4 个字内出现疑问词（吗/？/?/怎么）→ 跳过。

    只做提取，不校验真伪；证据是否齐全仍由 check_evidence 判定。
    """
    text = text or ""
    found: list[str] = []
    for keyword in _EVIDENCE_KEYWORDS:
        start = text.find(keyword)
        while start >= 0:
            head = text[max(0, start - 6):start]
            tail = text[start + len(keyword):start + len(keyword) + 6]
            negated = any(word in head for word in _NEGATIONS) or any(
                word in tail for word in ("没有", "还没", "未", "无")
            )
            questioned = any(word in tail[:4] for word in ("吗", "？", "?", "怎么"))
            if not negated and not questioned:
                found.append(keyword)
                break
            start = text.find(keyword, start + len(keyword))
    # 按标准顺序返回，避免用户叙述顺序影响下游展示
    return [item for item in _EVIDENCE_KEYWORDS if item in found]


def router(state: GraphState) -> dict[str, Any]:
    """复用现有 route()。同时把 needs_human 同步到 State.needs_human，便于 CLI 层直接取。

    合规守卫（先于 LLM、先于任何分流）：用户要求"直接退款"类强资金诉求时，不进入自动流程，
    直接置 handoff/needs_human=True（error.code=COMPLIANCE_REFUND），由条件边转 handoff_human。
    该判定排在知识分流之前，因此"退款 + 酒知识"混装诉求只会转人工，不会解答知识。
    草稿工具本身只生成草稿、不退款，这里是第一道拦截。

    M4 槽位填充：从原话抽取"已上传/已提供"的证据，与历史证据合并去重后写回 State，
    使多轮补证（仅给 session_id + "我已经上传了…"）也能重新算出赔付。
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

    extracted = extract_evidence_from_text(text)
    merged_evidence = list(state.get("evidence") or [])
    for item in extracted:
        if item not in merged_evidence:
            merged_evidence.append(item)

    return {
        "intent": decision.intent,
        "sub_tasks": sub_tasks,
        "task_outputs": _build_task_outputs(decision.intent, sub_tasks),
        "handoff": handoff,
        "needs_human": handoff,
        "error": decision.error or {},
        "user_text": text,
        "evidence": merged_evidence,
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
        issue_type = state.get("issue_type") or _derive_issue_type(
            sub_tasks or [state.get("intent", "")]
        )
        if not issue_type:
            return {
                "handoff": True,
                "needs_human": True,
                "issue_type": "",
                "error": {
                    "code": "UNSUPPORTED_ISSUE_TYPE",
                    "message": f"无法识别理赔类型（子任务：{sub_tasks or '空'}），转人工核实",
                    "retryable": False,
                },
            }
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

    # —— 多意图路径：理赔任务并发取证（ThreadPoolExecutor），知识任务就地执行 ——
    updated: list[SubTaskState] = [
        {
            "task_type": task.get("task_type", ""),
            "status": task.get("status", "pending"),
            "result": task.get("result"),
        }
        for task in tasks
    ]

    claim_indexes = [
        idx for idx, item in enumerate(updated) if _ISSUE_MAP.get((item["task_type"] or "").strip())
    ]
    outcomes: dict[int, tuple[str, dict[str, Any]]] = {}

    if claim_indexes:
        # 每个理赔子任务互不共享状态，可安全并发；结果先落 outcomes，再按原顺序回写
        max_workers = max(1, min(len(claim_indexes), 4))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="claim") as pool:
            futures = {
                pool.submit(
                    _fetch_claim,
                    order_id,
                    _ISSUE_MAP[(updated[idx]["task_type"] or "").strip()],
                    provided_evidence,
                    query,
                ): idx
                for idx in claim_indexes
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    outcomes[idx] = ("success", future.result())
                except (NotFoundError, DataSourceError, ToolError) as exc:
                    outcomes[idx] = (
                        "failed",
                        {"error": {"code": getattr(exc, "code", "TOOL_ERROR"), "message": getattr(exc, "message", str(exc))}},
                    )
                except Exception as exc:  # 线程内未预期异常同样降级，不让整图崩
                    outcomes[idx] = ("failed", {"error": {"code": "INTERNAL", "message": str(exc)}})

    primary: dict[str, Any] | None = None
    primary_issue = ""
    first_error: dict[str, Any] | None = None

    for idx, item in enumerate(updated):
        name = (item["task_type"] or "").strip()
        if idx in outcomes:
            status, payload = outcomes[idx]
            item["status"] = status
            item["result"] = payload
            if status == "failed":
                first_error = first_error or payload["error"]
            elif primary is None:
                primary, primary_issue = payload, _ISSUE_MAP.get(name, "")
        elif _is_knowledge_task(name):
            knowledge = knowledge_agent(state).get("knowledge_result", "")
            item["result"] = {"knowledge_result": knowledge}
            item["status"] = "success" if knowledge and knowledge != KNOWLEDGE_QUERY_FALLBACK else "failed"

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

    # M4 修复：多理赔任务时 evidence_status 必须合并所有任务的缺证项，
    # 否则 Judge/回复只看到主诉的缺证，少件的"整箱称重照片"会被漏掉。
    payloads = [
        task.get("result") or {}
        for task in updated
        if (task.get("result") or {}).get("evidence")
    ]
    merged_evidence = dict(primary["evidence"])
    if len(payloads) > 1:
        missing: list[str] = []
        complete = True
        for payload in payloads:
            ev = payload.get("evidence") or {}
            complete = complete and bool(ev.get("complete"))
            for item in ev.get("missing") or []:
                if item not in missing:
                    missing.append(item)
        merged_evidence.update({"complete": complete, "missing": missing})

    return {
        "task_outputs": updated,
        "order": primary["order"],
        "shipment": primary["shipment"],
        "batch": primary["batch"],
        "evidence_status": merged_evidence,
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


def _secondary_disposition_text(payloads: list[tuple[str, dict[str, Any]]]) -> str:
    """次要理赔任务的处置动作（补发/赔付/转人工）转成一句客户话术。

    只做字符串拼接，不动 draft_reply 的 Schema；主诉的处置仍在【处理进度】里。
    """
    lines: list[str] = []
    for issue_type, payload in payloads[1:]:
        comp = payload.get("compensation") or {}
        rec = comp.get("recommendation")
        label = _ISSUE_LABELS.get(issue_type, issue_type or "其他")
        if rec == "reship":
            lines.append(f"另发现{label}问题，我们将在审核确认后尽快为您安排补发。")
        elif rec == "partial_refund":
            amount = float(comp.get("amount") or 0.0)
            lines.append(
                f"另发现{label}问题，预计可申请部分赔付 {amount:.2f} 元，最终结果以人工审核为准。"
            )
        elif rec == "handoff_human":
            lines.append(f"另发现{label}问题，需售后专员进一步核实，我们会尽快联系您。")
    return "\n".join(lines)


def _insert_before_section(content: str, section: str, block: str) -> str:
    """把 block 插到指定板块标题之前；找不到板块就追加到正文末尾。"""
    idx = content.find(section)
    if idx < 0:
        return content.rstrip() + "\n\n" + block
    return content[:idx] + block + "\n\n" + content[idx:]


def _persist_ticket(state: GraphState, ticket_draft: Any, reply_content: str) -> None:
    """M4：工单落 SQLite + 会话槽位写 Redis。

    两者都是"尽力而为"：数据库/缓存故障只降级，不允许影响草稿产出（草稿本身不落库）。
    """
    try:
        sqlite_client.save_ticket(ticket_draft.model_dump())
    except Exception:
        pass

    session_id = (state.get("session_id") or "").strip()
    if not session_id:
        return
    try:
        redis_client.save_session(
            session_id,
            summary=f"{ticket_draft.issue_type} | {(reply_content or '')[:80]}",
            slots={
                "order_id": ticket_draft.order_id,
                "issue_type": ticket_draft.issue_type,
                "evidence": [str(item) for item in (state.get("evidence") or [])],
                "ticket_draft_id": ticket_draft.draft_id,
            },
        )
    except Exception:
        pass


def create_draft(state: GraphState) -> dict[str, Any]:
    """生成工单草稿 + 回复草稿。任何异常 → handoff/needs_human=True。

    - 工单：入参仍按"主诉"（tool_results / issue_type），多理赔任务只在 summary 末尾追加
      "另发现少件问题，建议一并处理"，不触碰 TicketDraft schema。
    - 回复：多个理赔任务的补证清单合并去重后统一列出。
    - 多意图（理赔 + 酒知识）时再把知识条目追加到回复末尾。
    """
    order_id = state.get("order_id", "")
    issue_type = state.get("issue_type") or _derive_issue_type(
        state.get("sub_tasks") or [state.get("intent", "")]
    )
    if not issue_type:
        # 兜底防线：即使上游漏判，也不能凭空 issue_type 生成带赔付的工单
        return {
            "handoff": True,
            "needs_human": True,
            "error": {
                "code": "UNSUPPORTED_ISSUE_TYPE",
                "message": "issue_type 无法识别，转人工核实后再出草稿",
                "retryable": False,
            },
        }
    try:
        payloads = _claim_payloads(state)
        ticket = create_ticket_draft(order_id, issue_type, tool_results=state.get("tool_results", {}))
        ev_status = _merged_evidence(state, payloads)

        update: dict[str, Any] = {}
        suffix = _secondary_claim_suffix(payloads)
        if suffix:
            update["summary"] = ticket.draft.summary + suffix
        # 多意图：工单证据状态与 State 对齐（union missing），避免与回复清单不一致
        update["evidence_status"] = EvidenceStatus(
            complete=bool(ev_status.get("complete", True)),
            missing=list(ev_status.get("missing") or []),
        )
        # 重跑时把 Judge 的复核意见注入工单上下文，便于追溯本轮修复点
        feedback = (state.get("judge_feedback") or "").strip()
        if feedback:
            base = update.get("summary", ticket.draft.summary)
            update["summary"] = f"{base}（质检复核：{feedback[:60]}）"
        ticket_draft = ticket.draft.model_copy(update=update)

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
        reply_dict = reply.reply.model_dump()
        disposition = _secondary_disposition_text(payloads)
        if disposition:
            # 放在【处理进度】之后、【保存建议】之前，读起来是同一段处理结论
            reply_dict["content"] = _insert_before_section(
                reply_dict["content"], "【保存建议】", disposition
            )
        reply_dict, knowledge = _merge_knowledge_into_reply(state, reply_dict)
        _persist_ticket(state, ticket_draft, reply_dict.get("content", ""))
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
    """转人工节点：写审批队列（第 9 个工具）+ 标记 handoff/needs_human=True。

    队列只落一条待审批记录，不退款、不改订单状态；写队列失败也不能反过来把图弄崩，
    因此这里 try/except 后静默降级，状态标记照常返回。
    """
    err = state.get("error") or {}
    code = err.get("code", "HANDOFF")
    message = err.get("message", "需要人工介入")
    order_id = (state.get("order_id") or "").strip()
    if order_id:
        try:
            write_handoff_queue(order_id, f"[{code}] {message}")
        except Exception:  # 队列不可用不阻断转人工语义
            pass
    return {
        "handoff": True,
        "needs_human": True,
        "error": {
            "code": code,
            "message": message,
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
    # 酒知识咨询走专业子 Agent：只按 intent 判定，避免"漏了…怎么保存"这类理赔文本被误分流
    intent = state.get("intent")
    if intent in _KNOWLEDGE_INTENTS:
        return "knowledge_agent"
    # 兼容：intent 判成"其他"但子任务只有知识咨询；多意图必须走取证主链（task_outputs 逐任务执行）
    if intent != "多意图" and "储存饮用咨询" in (state.get("sub_tasks") or []):
        return "knowledge_agent"
    # —— 专业子 Agent 扩展位（M4 启用，当前仅作架构占位）——
    # app/agents/logistics_agent.py  → intent=物流延迟
    # app/agents/fake_wine_agent.py  → intent=真伪争议
    # 现由 fetch_evidence 统一调用只读工具；M4 拆分为独立节点后在此按 intent 分流。
    return "fetch_evidence"


def _route_after_load(state: GraphState) -> str:
    # load_session 空 order_id 写 handoff=True → 直接 handoff_human，不走 router
    return "handoff_human" if state.get("handoff") else "router"


MAX_JUDGE_RETRY = 2  # 局部重跑上限：Judge 连续失败 3 次（首次 + 2 次重试）转人工
RECURSION_LIMIT = 16  # 图执行步数上限，双保险防死循环（节点内已有 retry_count 闸门）


def judge(state: GraphState) -> dict[str, Any]:
    """M4：LLM Judge 二次审核 create_draft 的产物。

    - pass=True  → 正常结束（persist 节点留到 M4 后续阶段）
    - pass=False → 按 failed_node 局部重跑（retry_count + 1，最多 2 次），
                  复核意见写入 judge_feedback 供下一轮 create_draft 读取
    - retry_count 达上限 → handoff，error.code=JUDGE_REJECTED
    - LLM 不可用（无 Key / 超时）→ 降级放行并标记 degraded=True，
      避免离线/Docker 环境因质检不可用而整链路瘫痪（确定性守卫仍在前面兜底）
    """
    if state.get("handoff") or not state.get("ticket_draft"):
        return {}
    try:
        verdict = judge_draft(state)
    except LLMError as exc:
        return {
            "judge_result": {
                "pass": True,
                "reason": f"Judge 不可用（{exc.code}），降级放行",
                "failed_node": "",
                "degraded": True,
            },
            "judge_feedback": "",
        }
    except Exception as exc:  # 任何未预期异常都不允许让草稿流程崩掉
        return {
            "judge_result": {
                "pass": True,
                "reason": f"Judge 异常，降级放行：{exc}",
                "failed_node": "",
                "degraded": True,
            },
            "judge_feedback": "",
        }

    if verdict.get("pass"):
        return {"judge_result": verdict, "judge_feedback": ""}

    reason = verdict.get("reason") or "Judge 判定草稿不通过"
    failed_node = verdict.get("failed_node") or "create_draft"
    if failed_node not in ("create_draft", "fetch_evidence"):
        failed_node = "create_draft"
    verdict["failed_node"] = failed_node

    retry_count = int(state.get("retry_count") or 0)
    if retry_count >= MAX_JUDGE_RETRY:
        return {
            "judge_result": verdict,
            "judge_feedback": reason,
            "retry_count": retry_count,
            "handoff": True,
            "needs_human": True,
            "error": {
                "code": "JUDGE_REJECTED",
                "message": f"{reason}（已重试 {retry_count} 次，转人工）",
                "retryable": False,
            },
        }

    return {
        "judge_result": verdict,
        "judge_feedback": reason,
        "retry_count": retry_count + 1,
    }


def _route_after_judge(state: GraphState) -> str:
    if state.get("handoff"):
        return "handoff_human"
    verdict = state.get("judge_result") or {}
    if verdict.get("pass"):
        return END
    # 局部重跑：按 Judge 指认的失败节点回跳，最多 MAX_JUDGE_RETRY 次
    return "fetch_evidence" if verdict.get("failed_node") == "fetch_evidence" else "create_draft"


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
    sg.add_node("judge", judge)
    sg.add_node("knowledge_agent", knowledge_agent)
    sg.add_node("handoff_human", handoff_human)

    # load_session 后加条件边：空 order_id 直接 handoff_human（不进 router，避免同 step 两次写 handoff）
    sg.add_edge(START, "load_session")
    sg.add_conditional_edges("load_session", _route_after_load)
    sg.add_conditional_edges("router", _route_after_router)
    sg.add_conditional_edges("fetch_evidence", _route_after_fetch)
    sg.add_edge("check_evidence", "create_draft")
    sg.add_edge("create_draft", "judge")
    sg.add_conditional_edges("judge", _route_after_judge)
    sg.add_edge("knowledge_agent", END)
    sg.add_edge("handoff_human", END)

    return sg.compile()


__all__ = ["compile_graph", "GraphState", "DEFAULT_EMPTY"]
