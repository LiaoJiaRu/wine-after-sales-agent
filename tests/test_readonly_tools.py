import pytest
from app.tools.errors import NotFoundError
from app.tools.readonly import get_order, get_logistics, get_batch, search_policy, check_evidence, calc_compensation

ORDER_ID = "SO20261116001"
BATCH_NO = "B20261018-01"

def test_get_order_ok():
    assert get_order(ORDER_ID).order.quantity == 2

def test_get_order_not_found():
    with pytest.raises(NotFoundError): get_order("NOPE")

def test_get_logistics_ok():
    assert get_logistics(ORDER_ID).shipment.waybill_no == "SF1234567890"

def test_get_batch_ok():
    assert get_batch(BATCH_NO).batch.batch_no == BATCH_NO

def test_search_policy_hits():
    r = search_policy("破损漏液")
    assert len(r.hits) >= 1

def test_check_evidence_missing():
    r = check_evidence(ORDER_ID, "broken_leak", ["开箱视频"])
    assert r.status.complete is False and "面单照片" in r.status.missing

def test_check_evidence_complete():
    r = check_evidence(ORDER_ID, "broken_leak", ["开箱视频", "面单照片", "破损部位照片"])
    assert r.status.complete is True

def test_calc_compensation_supplement():
    assert calc_compensation(ORDER_ID, "broken_leak", False).suggestion.recommendation == "supplement_evidence"

def test_calc_compensation_partial():
    assert calc_compensation(ORDER_ID, "broken_leak", True).suggestion.recommendation == "partial_refund"