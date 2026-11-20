from __future__ import annotations
import time
from datetime import datetime
from .errors import NotFoundError
from .loader import load_batches, load_orders, load_policies, load_shipments
from .schemas import *

REQUIRED_EVIDENCE = {
    "broken_leak": ["开箱视频", "面单照片", "破损部位照片"],
    "missing_item": ["开箱视频", "面单照片", "整箱称重照片"],
    "wrong_item": ["开箱视频", "面单照片", "商品照片"],
}

# 酒知识咨询关键词：命中即优先命中 P-KNOW 系列（权重高于理赔条款）
KNOWLEDGE_KEYWORDS = ("保存", "存放", "储存", "饮用", "开瓶", "怎么喝", "醒酒", "口感")
KNOWLEDGE_PREFIX = "P-KNOW"

def _meta(tool: str, t0: float, ok: bool = True) -> ToolMeta:
    return ToolMeta(tool=tool, ok=ok, elapsed_ms=int((time.perf_counter() - t0) * 1000))

def get_order(order_id: str) -> GetOrderOutput:
    t0 = time.perf_counter()
    for o in load_orders():
        if o["order_id"] == order_id:
            return GetOrderOutput(meta=_meta("get_order", t0), order=OrderInfo(**o))
    raise NotFoundError(f"订单不存在: {order_id}")

def get_logistics(order_id: str) -> GetLogisticsOutput:
    t0 = time.perf_counter()
    for s in load_shipments():
        if s["order_id"] == order_id:
            return GetLogisticsOutput(meta=_meta("get_logistics", t0), shipment=ShipmentInfo(**s))
    raise NotFoundError(f"物流单不存在: {order_id}")

def get_batch(batch_no: str) -> GetBatchOutput:
    t0 = time.perf_counter()
    for b in load_batches():
        if b["batch_no"] == batch_no:
            return GetBatchOutput(meta=_meta("get_batch", t0), batch=BatchInfo(**b))
    raise NotFoundError(f"批次不存在: {batch_no}")

def search_policy(query: str, top_k: int = 3) -> SearchPolicyOutput:
    """字符重合度检索；query 命中酒知识关键词时，P-KNOW 系列整体上浮到理赔条款之前。

    排序键：(知识优先, 字符得分降序, 政策编号升序)。
    非知识咨询的查询（如“破损漏液”）priority 恒为 0，排序与旧实现完全一致。
    """
    t0 = time.perf_counter()
    q = set(query.strip().lower())
    prefer_knowledge = any(k in query for k in KNOWLEDGE_KEYWORDS)
    scored: list[tuple[int, int, dict]] = []
    for p in load_policies():
        text = f"{p['title']} {p['content']}".lower()
        score = sum(1 for ch in q if ch in text)
        if score == 0:
            continue
        priority = 1 if (prefer_knowledge and p["id"].startswith(KNOWLEDGE_PREFIX)) else 0
        scored.append((priority, score, p))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]["id"]))
    hits = [PolicyHit(score=float(s), **p) for _, s, p in scored[:top_k]]
    return SearchPolicyOutput(meta=_meta("search_policy", t0), hits=hits)

def check_evidence(order_id: str, issue_type: str, evidence: list[str]) -> CheckEvidenceOutput:
    t0 = time.perf_counter()
    get_order(order_id)
    required = REQUIRED_EVIDENCE.get(issue_type, [])
    have = [e for e in evidence if e in required]
    missing = [r for r in required if r not in evidence]
    status = EvidenceStatus(complete=not missing, missing=missing, have=have)
    return CheckEvidenceOutput(meta=_meta("check_evidence", t0), status=status)

def calc_compensation(order_id: str, issue_type: str, evidence_complete: bool) -> CalcCompensationOutput:
    t0 = time.perf_counter()
    order = get_order(order_id).order
    tz = order.signed_at.tzinfo
    now = datetime.now(tz) if tz else datetime.now()
    days = (now - order.signed_at).days

    if not evidence_complete:
        suggestion = CompensationSuggestion(recommendation="supplement_evidence", amount=0.0, reason="证据不齐，先补证", policy_ids=["P-EVID-001"])
    elif days > 7:
        suggestion = CompensationSuggestion(recommendation="handoff_human", amount=0.0, reason=f"签收已 {days} 天，超 7 天时效，转人工复核", policy_ids=["P-TIME-001"])
    elif issue_type == "broken_leak":
        amount = round(order.order_amount * 0.3, 2)
        if order.insured:
            amount = min(amount, order.insured_amount)
        suggestion = CompensationSuggestion(recommendation="partial_refund", amount=amount, reason="破损漏液部分赔付（30%，保价封顶）", policy_ids=["P-BREAK-001"])
    elif issue_type == "missing_item":
        suggestion = CompensationSuggestion(recommendation="reship", amount=0.0, reason="少件错发，建议补发", policy_ids=["P-EVID-001"])
    else:
        suggestion = CompensationSuggestion(recommendation="handoff_human", amount=0.0, reason="需人工判定", policy_ids=[])

    return CalcCompensationOutput(meta=_meta("calc_compensation", t0), suggestion=suggestion)