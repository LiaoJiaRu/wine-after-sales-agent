"""草稿写工具：只生成草稿文本，不落库、不退款、不修改订单状态。

- create_ticket_draft：把订单/物流/证据/赔付事实装配成工单草稿，始终要求人工审核。
- draft_reply：把事实数据转成面向消费者的温和话术（补证清单、处理进度、保存建议）。

调用方（LangGraph 节点）负责把草稿写库/发消息；本模块无任何副作用。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from .errors import InvalidInputError
from .loader import load_policies
from .schemas import (
    CompensationSuggestion,
    CreateTicketDraftOutput,
    DraftReplyOutput,
    EvidenceStatus,
    ReplyDraft,
    ReplyFacts,
    TicketDraft,
    TicketToolResults,
    ToolMeta,
)

ISSUE_LABELS = {
    "broken_leak": "破损漏液",
    "missing_item": "少件",
    "wrong_item": "错发",
}

STORAGE_ADVICE = [
    "避光平放存放，避免频繁震动与倒置。",
    "长期存放建议 12~16℃、湿度 60%~70%，远离异味与热源。",
    "开瓶后请密封冷藏，建议 3 天内饮完。",
]

EVIDENCE_POLICY_ID = "P-EVID-001"

# 转人工队列：默认写到项目 data/ 下；测试用 HANDOFF_QUEUE_PATH 覆盖到 tmp_path 隔离
DEFAULT_HANDOFF_QUEUE = Path(__file__).resolve().parents[2] / "data" / "handoff_queue.jsonl"


def _meta(tool: str, t0: float) -> ToolMeta:
    return ToolMeta(tool=tool, ok=True, elapsed_ms=int((time.perf_counter() - t0) * 1000))


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _policy_clauses(policy_ids: list[str]) -> list[str]:
    """把政策编号解析为“编号 标题”，未知编号原样保留；去重保序。"""
    titles = {p["id"]: p["title"] for p in load_policies()}
    out: list[str] = []
    for pid in policy_ids:
        clause = f"{pid} {titles[pid]}" if pid in titles else pid
        if clause not in out:
            out.append(clause)
    return out


def create_ticket_draft(
    order_id: str,
    issue_type: str,
    tool_results: TicketToolResults | dict,
) -> CreateTicketDraftOutput:
    """生成工单草稿。need_human_review 恒为 True，草稿不触发任何写库或退款。"""
    t0 = time.perf_counter()

    if not order_id or not order_id.strip():
        raise InvalidInputError("order_id 不能为空")
    if not tool_results:
        raise InvalidInputError("tool_results 为空：至少需要订单信息才能生成工单草稿")
    try:
        results = TicketToolResults.model_validate(tool_results)
    except ValidationError as exc:
        field = ".".join(str(x) for x in exc.errors()[0]["loc"])
        raise InvalidInputError(f"tool_results 缺少必要字段: {field}") from exc

    order = results.order
    evidence = results.evidence or EvidenceStatus(complete=False, missing=[], have=[])
    compensation = results.compensation or CompensationSuggestion(
        recommendation="supplement_evidence",
        amount=0.0,
        reason="暂无赔付建议，等待证据补齐",
        policy_ids=[EVIDENCE_POLICY_ID],
    )

    policy_ids = list(compensation.policy_ids)
    if not evidence.complete and EVIDENCE_POLICY_ID not in policy_ids:
        policy_ids.append(EVIDENCE_POLICY_ID)

    evidence_text = "齐全" if evidence.complete else f"缺 {len(evidence.missing)} 项"
    summary = (
        f"{order.product_name}（{order.sku}）×{order.quantity}，"
        f"{ISSUE_LABELS.get(issue_type, issue_type)}；"
        f"证据{evidence_text}，赔付建议：{compensation.recommendation}"
        + (f"（{compensation.amount:.2f} 元）" if compensation.amount else "")
        + f"；{compensation.reason}"
    )

    draft = TicketDraft(
        draft_id=_new_id("TD"),
        order_id=order_id,
        issue_type=issue_type,
        summary=summary,
        evidence_status=evidence,
        compensation=compensation,
        policy_clauses=_policy_clauses(policy_ids),
        need_human_review=True,
        created_at=datetime.now(),
    )
    return CreateTicketDraftOutput(meta=_meta("create_ticket_draft", t0), draft=draft)


def draft_reply(
    order_id: str,
    issue_type: str,
    facts: ReplyFacts | dict | None = None,
) -> DraftReplyOutput:
    """生成面向消费者的温和话术草稿。facts 为空时输出通用话术，不报错。"""
    t0 = time.perf_counter()

    if not order_id or not order_id.strip():
        raise InvalidInputError("order_id 不能为空")
    try:
        f = ReplyFacts.model_validate(facts or {})
    except ValidationError as exc:
        field = ".".join(str(x) for x in exc.errors()[0]["loc"])
        raise InvalidInputError(f"facts 字段非法: {field}") from exc

    product = f.product_name or "您购买的商品"
    checklist = list(f.evidence_missing)
    lines = [
        "尊敬的顾客，您好：",
        "",
        f"非常抱歉本次购物给您带来了不便。关于订单 {order_id}（{product}），"
        "我们已收到您的反馈，正在为您跟进处理。",
        "",
    ]

    if checklist:
        lines.append("【需要您补充的材料】")
        lines.extend(f"{i}）{item}" for i, item in enumerate(checklist, 1))
        lines.append("补齐后我们会第一时间继续为您处理，请您不必担心。")
    else:
        lines.append("【证据核验】")
        lines.append("您提交的材料已齐全，无需再次补充。")

    lines.append("")
    lines.append("【处理进度】")
    if f.recommendation == "partial_refund":
        lines.append(
            f"根据售后政策，本次可申请部分赔付，预计金额 {f.amount:.2f} 元；"
            "最终结果以人工审核为准。"
        )
    elif f.recommendation == "reship":
        lines.append("建议为您安排补发，审核确认后执行，具体进度会另行通知。")
    elif f.recommendation == "supplement_evidence":
        lines.append("您的材料尚在补充中，补齐后即可进入赔付评估。")
    elif f.recommendation == "handoff_human":
        lines.append("该情况需由售后专员进一步核实，我们已为您转交专人跟进。")
    else:
        lines.append("我们正在核实您的情况，有结论会第一时间告知您。")

    lines.append("")
    lines.append("【保存建议】")
    lines.extend(f"{i}）{tip}" for i, tip in enumerate(STORAGE_ADVICE, 1))
    lines.append("")
    lines.append("感谢您的理解与配合，如有疑问可随时联系我们。")

    reply = ReplyDraft(
        draft_id=_new_id("RP"),
        order_id=order_id,
        issue_type=issue_type,
        content="\n".join(lines),
        checklist=checklist,
        storage_advice=list(STORAGE_ADVICE),
        created_at=datetime.now(),
    )
    return DraftReplyOutput(meta=_meta("draft_reply", t0), reply=reply)


def _handoff_queue_path() -> Path:
    """转人工队列路径，与 _db_path 同样的锚定策略。"""
    PROJECT_ROOT = Path(__file__).resolve().parents[2]  # write.py 在 app/tools/

    raw = (os.getenv("HANDOFF_QUEUE_PATH") or "").strip() or "data/handoff_queue.jsonl"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_handoff_queue(order_id: str, reason: str) -> dict:
    """转人工写队列（第 9 个工具）：追加一条 JSONL 记录，等待人工审批。

    只写队列，不退款、不改订单状态、不做任何审批动作；approval 由人工在队列侧完成。
    路径默认 data/handoff_queue.jsonl，可用环境变量 HANDOFF_QUEUE_PATH 覆盖（测试隔离）。
    """
    t0 = time.perf_counter()

    if not order_id or not order_id.strip():
        raise InvalidInputError("order_id 不能为空")

    path = _handoff_queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "order_id": order_id.strip(),
        "reason": (reason or "").strip() or "需要人工介入",
        "status": "pending_approval",
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {
        "meta": _meta("write_handoff_queue", t0).model_dump(),
        "queued": True,
        "path": str(path),
        "record": record,
    }
