from __future__ import annotations
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field

class ToolMeta(BaseModel):
    tool: str
    ok: bool = True
    elapsed_ms: int = 0

class OrderInfo(BaseModel):
    order_id: str
    user_id: str
    sku: str
    product_name: str
    batch_no: str
    quantity: int
    insured: bool
    insured_amount: float
    order_amount: float
    signed_at: datetime
    status: str

class GetOrderInput(BaseModel):
    order_id: str = Field(..., description="订单号，如 SO20261116001")

class GetOrderOutput(BaseModel):
    meta: ToolMeta
    order: OrderInfo

class LogisticsTrace(BaseModel):
    time: datetime
    event: str

class ShipmentInfo(BaseModel):
    order_id: str
    waybill_no: str
    carrier: str
    status: str
    signed_at: Optional[datetime] = None
    traces: list[LogisticsTrace] = Field(default_factory=list)
    abnormal: Optional[str] = None

class GetLogisticsInput(BaseModel):
    order_id: str

class GetLogisticsOutput(BaseModel):
    meta: ToolMeta
    shipment: ShipmentInfo

class BatchInfo(BaseModel):
    batch_no: str
    sku: str
    outbound_at: datetime
    warehouse: str
    abnormal_count: int = 0

class GetBatchInput(BaseModel):
    batch_no: str

class GetBatchOutput(BaseModel):
    meta: ToolMeta
    batch: BatchInfo

class PolicyHit(BaseModel):
    id: str
    title: str
    content: str
    score: float = 0.0

class SearchPolicyInput(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(3, ge=1, le=10)

class SearchPolicyOutput(BaseModel):
    meta: ToolMeta
    hits: list[PolicyHit]

IssueType = Literal["broken_leak", "missing_item", "wrong_item"]

class CheckEvidenceInput(BaseModel):
    order_id: str
    issue_type: IssueType
    evidence: list[str] = Field(default_factory=list)

class EvidenceStatus(BaseModel):
    complete: bool
    missing: list[str] = Field(default_factory=list)
    have: list[str] = Field(default_factory=list)

class CheckEvidenceOutput(BaseModel):
    meta: ToolMeta
    status: EvidenceStatus

class CalcCompensationInput(BaseModel):
    order_id: str
    issue_type: IssueType
    evidence_complete: bool

class CompensationSuggestion(BaseModel):
    recommendation: Literal["partial_refund", "reship", "supplement_evidence", "handoff_human"]
    amount: float = 0.0
    reason: str
    policy_ids: list[str] = Field(default_factory=list)

class CalcCompensationOutput(BaseModel):
    meta: ToolMeta
    suggestion: CompensationSuggestion

# ---------- 草稿写工具（只产出草稿，不落库、不退款、不改订单） ----------

class TicketToolResults(BaseModel):
    """工单草稿的输入事实：订单必填，物流/证据/赔付建议可选。"""
    order: OrderInfo
    shipment: Optional[ShipmentInfo] = None
    evidence: Optional[EvidenceStatus] = None
    compensation: Optional[CompensationSuggestion] = None

class CreateTicketDraftInput(BaseModel):
    order_id: str
    issue_type: IssueType
    tool_results: TicketToolResults

class TicketDraft(BaseModel):
    draft_id: str
    order_id: str
    issue_type: str
    summary: str
    evidence_status: EvidenceStatus
    compensation: CompensationSuggestion
    policy_clauses: list[str] = Field(default_factory=list)
    need_human_review: bool = True
    status: Literal["draft"] = "draft"
    created_at: datetime

class CreateTicketDraftOutput(BaseModel):
    meta: ToolMeta
    draft: TicketDraft

class ReplyFacts(BaseModel):
    """回复草稿的输入事实；允许为空，空事实时输出通用话术。"""
    product_name: str = ""
    evidence_complete: bool = True
    evidence_missing: list[str] = Field(default_factory=list)
    recommendation: Optional[Literal["partial_refund", "reship", "supplement_evidence", "handoff_human"]] = None
    amount: float = 0.0
    reason: str = ""

class DraftReplyInput(BaseModel):
    order_id: str
    issue_type: IssueType
    facts: ReplyFacts = Field(default_factory=ReplyFacts)

class ReplyDraft(BaseModel):
    draft_id: str
    order_id: str
    issue_type: str
    tone: Literal["gentle"] = "gentle"
    content: str
    checklist: list[str] = Field(default_factory=list)
    storage_advice: list[str] = Field(default_factory=list)
    status: Literal["draft"] = "draft"
    created_at: datetime

class DraftReplyOutput(BaseModel):
    meta: ToolMeta
    reply: ReplyDraft