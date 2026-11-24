"""展示层数据源 & datetime 序列化验证。

本文件只测试 ui/streamlit_app.py 中**纯函数**，不启动 Streamlit：
  - _build_ticket_view(state)：合并 SQLite 真实工单与 Agent 快照
  - _jsonable(obj)：datetime / date 递归 ISO 序列化
  - sqlite_client.get_ticket(draft_id)：单条查询不存在返回 None
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.memory import sqlite_client


# 把 Streamlit 的两个纯函数抽到可直接 import 的位置
# （ui 模块本身也会被其他工具间接引用，但此文件不调用 st.* 组件）
import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

_APP_PATH = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "streamlit_app_under_test",
    str(_APP_PATH / "ui" / "streamlit_app.py"),
)
_streamlit_app = importlib.util.module_from_spec(_SPEC)
# 允许 ui 模块在无 streamlit 运行时也能被 import
try:
    import streamlit  # noqa: F401
except ImportError:
    pytest.skip("需要 streamlit 才能 import ui/streamlit_app.py", allow_module_level=True)
_SPEC.loader.exec_module(_streamlit_app)

_build_ticket_view = _streamlit_app._build_ticket_view
_jsonable = _streamlit_app._jsonable


def _upsert_test_ticket(draft_id: str, status: str = "approved", amount: float = 354.0) -> None:
    """写一条最小化 SQLIte 工单用于测试。"""
    sqlite_client.init_db()
    sqlite_client.save_ticket({
        "draft_id": draft_id,
        "order_id": "SO20261116001",
        "issue_type": "broken_leak",
        "summary": "测试摘要",
        "status": "draft",
        "compensation": {"amount": amount},
    })
    sqlite_client.update_ticket_status(draft_id, status)


# ---------------------------------------------------------------------------
# 1) SQLite approved + State draft 合并视图
# ---------------------------------------------------------------------------


def test_build_ticket_view_uses_sqlite_status_over_snapshot():
    _upsert_test_ticket("TD-TEST", status="approved", amount=354.0)

    state = {
        "ticket_draft": {
            "draft_id": "TD-TEST",
            "status": "draft",
            "amount": 0.0,
            "summary": "Agent 草稿摘要",
            "compensation": {"recommendation": "partial_refund", "amount": 354.0},
        },
        "evidence_status": {"complete": True, "missing": [], "have": ["开箱视频"]},
    }

    view = _build_ticket_view(state)

    assert view is not None
    assert view["source"] == "sqlite", "应优先命中 SQLite 真实状态"
    assert view["top_status"] == "approved", "顶层状态必须取 SQLite 中的 approved"
    assert view["top_amount"] == 354.0
    assert view["top_summary"] == "测试摘要"
    assert view["full_view"]["status"] == "approved"
    assert view["full_view"]["agent_snapshot"]["status"] == "draft", (
        "agent_snapshot 必须保留 State 原始草稿的 draft 状态"
    )
    assert view["evidence_status"]["complete"] is True


# ---------------------------------------------------------------------------
# 2) get_ticket 不存在返回 None；_build_ticket_view 降级不抛异常
# ---------------------------------------------------------------------------


def test_get_ticket_not_exist_returns_none():
    assert sqlite_client.get_ticket("TD-NOTEXIST") is None


def test_build_ticket_view_fallback_when_sqlite_row_missing():
    state = {
        "ticket_draft": {
            "draft_id": "TD-NOTEXIST",
            "status": "draft",
            "summary": "Agent 自己生成的草稿",
            "compensation": {"amount": 354.0},
        }
    }

    view = _build_ticket_view(state)

    assert view is not None
    assert view["source"] == "fallback"
    assert view["top_status"] == "draft"
    assert view["top_summary"] == "Agent 自己生成的草稿"
    assert view["warning"] is not None
    assert "TD-NOTEXIST" in view["warning"]
    # 关键：没有 sqlite_row
    assert view["sqlite_row"] is None
    # 关键：不抛异常——这个测试函数没有 try/except，若抛异常 pytest 会直接 fail


def test_build_ticket_view_returns_none_when_no_ticket():
    assert _build_ticket_view({}) is None
    assert _build_ticket_view({"ticket_draft": None}) is None
    assert _build_ticket_view({"ticket_draft": {}}) is None


# ---------------------------------------------------------------------------
# 3) _jsonable 递归处理 datetime，且结果可 fromisoformat 解析
# ---------------------------------------------------------------------------


def test_jsonable_datetime_roundtrip():
    now = datetime.now(timezone.utc)
    nested = {
        "order": {
            "signed_at": now,
            "nested_list": [now, {"traces": [{"time": now}]}],
        },
        "strings": "保留",
        "numbers": 123,
        "none": None,
    }

    out = _jsonable(nested)

    # 顶层没有 datetime.datetime(...) 字符串
    flat = str(out)
    assert "datetime.datetime(" not in flat
    assert "datetime.date(" not in flat

    # 所有 datetime 都被转成 ISO 字符串，且可可逆解析
    dt_strings = [
        out["order"]["signed_at"],
        out["order"]["nested_list"][0],
        out["order"]["nested_list"][1]["traces"][0]["time"],
    ]
    for iso in dt_strings:
        assert isinstance(iso, str)
        datetime.fromisoformat(iso), f"_jsonable 产出的 '{iso}' 应可被 fromisoformat 解析"


def test_jsonable_passthrough_for_non_datetime():
    """确认 _jsonable 不会误把非 datetime 对象改掉。"""
    original = {
        "a": 1,
        "b": "hello",
        "c": None,
        "d": [1, "x", None],
        "e": {"nested": True},
    }
    assert _jsonable(original) == original
