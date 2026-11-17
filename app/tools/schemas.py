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