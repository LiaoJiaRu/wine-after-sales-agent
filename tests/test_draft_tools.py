import pytest

from app.tools.errors import InvalidInputError
from app.tools.readonly import calc_compensation, check_evidence, get_logistics, get_order
from app.tools.write import create_ticket_draft, draft_reply

ORDER_ID = "SO20261116001"


ALL_EVIDENCE = ["开箱视频", "面单照片", "破损部位照片"]


def _tool_results(evidence: list[str] | None = None) -> dict:
    """按同一份证据事实同时生成证据状态与赔付建议，避免两处输入自相矛盾。"""
    ev = ALL_EVIDENCE if evidence is None else evidence
    status = check_evidence(ORDER_ID, "broken_leak", ev).status
    return {
        "order": get_order(ORDER_ID).order.model_dump(),
        "shipment": get_logistics(ORDER_ID).shipment.model_dump(),
        "evidence": status.model_dump(),
        "compensation": calc_compensation(ORDER_ID, "broken_leak", status.complete).suggestion.model_dump(),
    }


def test_create_ticket_draft_ok():
    d = create_ticket_draft(ORDER_ID, "broken_leak", _tool_results(["开箱视频"])).draft
    assert d.order_id == ORDER_ID
    assert d.need_human_review is True
    assert d.status == "draft"
    assert d.draft_id.startswith("TD-")
    assert d.evidence_status.complete is False
    assert d.compensation.recommendation == "supplement_evidence"
    assert d.policy_clauses and all(isinstance(c, str) for c in d.policy_clauses)
    assert "破损漏液" in d.summary


def test_create_ticket_draft_empty_input_raises():
    with pytest.raises(InvalidInputError):
        create_ticket_draft(ORDER_ID, "broken_leak", {})


def test_create_ticket_draft_missing_order_raises():
    with pytest.raises(InvalidInputError):
        create_ticket_draft(ORDER_ID, "broken_leak", {"shipment": None})


def test_create_ticket_draft_blank_order_id_raises():
    with pytest.raises(InvalidInputError):
        create_ticket_draft("  ", "broken_leak", _tool_results())


def test_draft_reply_ok():
    r = draft_reply(
        ORDER_ID,
        "broken_leak",
        {
            "product_name": "赤霞珠干红葡萄酒",
            "evidence_complete": False,
            "evidence_missing": ["面单照片", "破损部位照片"],
            "recommendation": "supplement_evidence",
        },
    ).reply
    assert r.status == "draft"
    assert r.tone == "gentle"
    assert r.draft_id.startswith("RP-")
    assert r.checklist == ["面单照片", "破损部位照片"]
    assert "面单照片" in r.content
    assert "保存建议" in r.content
    assert r.storage_advice


def test_draft_reply_partial_refund_mentions_review():
    r = draft_reply(
        ORDER_ID,
        "broken_leak",
        {"product_name": "赤霞珠干红葡萄酒", "recommendation": "partial_refund", "amount": 198.0},
    ).reply
    assert "198.00" in r.content
    assert "人工审核" in r.content
    assert r.checklist == []


def test_draft_reply_empty_facts_degrades_gracefully():
    r = draft_reply(ORDER_ID, "broken_leak", {}).reply
    assert r.status == "draft"
    assert r.content.strip()
    assert r.checklist == []
    assert "保存建议" in r.content


def test_draft_reply_blank_order_id_raises():
    with pytest.raises(InvalidInputError):
        draft_reply("", "broken_leak", {})
