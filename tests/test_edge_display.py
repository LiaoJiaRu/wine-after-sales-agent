"""展示层合并视图 + datetime 序列化边界测试。

仅新增文件，不改 ui/streamlit_app.py、app/memory/sqlite_client.py 业务代码，
不改 tickets 表结构，不向 NOT NULL 列插 NULL。
依赖 tests/conftest.py 的 isolate_memory autouse fixture（fakeredis + tmp_path SQLite）。
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
from datetime import datetime, timezone

import pytest

from pathlib import Path

_APP = Path(__file__).resolve().parents[1]

# 动态 import ui 模块 —— 它也会被其他工具间接引用，但本文件只取两个纯函数
_SPEC = importlib.util.spec_from_file_location(
    "streamlit_app_edge",
    str(_APP / "ui" / "streamlit_app.py"),
)
_streamlit_app = importlib.util.module_from_spec(_SPEC)
try:
    import streamlit  # noqa: F401
except ImportError:
    pytest.skip("需要 streamlit 才能 import ui/streamlit_app.py", allow_module_level=True)
_SPEC.loader.exec_module(_streamlit_app)

_build_ticket_view = _streamlit_app._build_ticket_view
_jsonable = _streamlit_app._jsonable

from app.memory import sqlite_client  # noqa: E402


def _min_draft(draft_id: str, order_id: str = "SO20261116001", amount: float = 354.0) -> dict:
    return {
        "draft_id": draft_id,
        "order_id": order_id,
        "issue_type": "broken_leak",
        "summary": f"草稿 {draft_id}",
        "status": "draft",
        "compensation": {"amount": amount},
    }


# ---------------------------------------------------------------------------
# 1) 已审重查：SQLite approved 覆盖 State draft
# ---------------------------------------------------------------------------


def test_approved_ticket_sqlite_over_snapshot():
    sqlite_client.save_ticket(_min_draft("TD-A"))
    sqlite_client.update_ticket_status("TD-A", "approved")

    state = {
        "ticket_draft": {
            "draft_id": "TD-A",
            "status": "draft",
            "summary": "Agent 快照摘要",
            "compensation": {"amount": 354.0},
        },
    }

    view = _build_ticket_view(state)

    assert view["source"] == "sqlite"
    assert view["top_status"] == "approved"
    assert view["sqlite_row"] is not None
    assert view["full_view"]["agent_snapshot"]["status"] == "draft"
    assert view["full_view"]["status"] == "approved"


# ---------------------------------------------------------------------------
# 2) 不存在 draft → fallback + 提示，不抛异常
# ---------------------------------------------------------------------------


def test_get_ticket_not_exist_returns_none():
    assert sqlite_client.get_ticket("TD-NOTEXIST") is None


def test_fallback_when_sqlite_row_missing_no_exception():
    state = {"ticket_draft": {"draft_id": "TD-NOTEXIST", "status": "draft"}}

    view = _build_ticket_view(state)

    assert view["source"] == "fallback"
    assert view["warning"] is not None
    assert "TD-NOTEXIST" in view["warning"]
    assert view["sqlite_row"] is None
    # 本测试无 try/except —— 若抛异常 pytest 会直接失败


# ---------------------------------------------------------------------------
# 3) 非法 status → 原样展示，不 KeyError
# ---------------------------------------------------------------------------


def test_illegal_status_passthrough():
    sqlite_client.save_ticket(_min_draft("TD-B"))
    sqlite_client.update_ticket_status("TD-B", "weird_value")

    state = {"ticket_draft": {"draft_id": "TD-B"}}
    view = _build_ticket_view(state)

    assert view["top_status"] == "weird_value"
    # 不抛 KeyError 即通过；显式断言 sqlite_row 能取到该值
    assert view["sqlite_row"]["status"] == "weird_value"


# ---------------------------------------------------------------------------
# 4) _jsonable 递归 datetime + json.dumps 不抛 + 不含 dotted repr
# ---------------------------------------------------------------------------


def test_jsonable_datetime_recursion_and_json_dumps():
    out = _jsonable({
        "a": datetime(2026, 11, 24, 11, 11, 44),
        "b": [datetime(2026, 1, 1)],
        "c": None,
    })

    # ISO 字符串可被 fromisoformat 解析
    assert datetime.fromisoformat(out["a"]) == datetime(2026, 11, 24, 11, 11, 44)
    assert datetime.fromisoformat(out["b"][0]) == datetime(2026, 1, 1)
    assert out["c"] is None

    # 不抛 json.dumps
    json_text = json.dumps(out, ensure_ascii=False)

    # 不含 datetime.datetime( 字符串（UI 页面里这是要消除的坏味道）
    assert "datetime.datetime(" not in json_text


# ---------------------------------------------------------------------------
# 5) agent_snapshot 缺失时不 KeyError；state 无 ticket_draft 时返回 None
# ---------------------------------------------------------------------------


def test_agent_snapshot_missing_state_no_keyerror():
    sqlite_client.save_ticket(_min_draft("TD-C"))
    state = {
        "ticket_draft": {"draft_id": "TD-C"},  # 故意不写 summary/compensation/status
        "evidence_status": {"complete": True, "have": ["开箱视频"]},
    }

    view = _build_ticket_view(state)

    assert "agent_snapshot" in view["full_view"]
    assert view["full_view"]["agent_snapshot"] == state["ticket_draft"]


def test_no_ticket_draft_returns_none():
    assert _build_ticket_view({}) is None
    assert _build_ticket_view({"ticket_draft": None}) is None
    assert _build_ticket_view({"ticket_draft": {}}) is None


# ---------------------------------------------------------------------------
# 6) 重复审批 → 旧 draft superseded，新 draft 留为 draft
# ---------------------------------------------------------------------------


def test_duplicate_order_id_second_ticket_supersedes_first():
    sqlite_client.save_ticket(_min_draft("TD-OLD", order_id="SO-DUP"))
    sqlite_client.save_ticket(_min_draft("TD-NEW", order_id="SO-DUP", amount=300.0))

    assert [t["draft_id"] for t in sqlite_client.list_tickets("draft")] == ["TD-NEW"]
    assert sqlite_client.get_ticket("TD-OLD")["status"] == "superseded"
    assert sqlite_client.get_ticket("TD-NEW")["status"] == "draft"


# ---------------------------------------------------------------------------
# 7) save_ticket 传入 datetime created_at，get_ticket 返回可 fromisoformat
# ---------------------------------------------------------------------------


def test_created_at_roundtrip_via_save_and_get():
    when = datetime(2026, 11, 24, 11, 11, 44, tzinfo=timezone.utc)
    draft = _min_draft("TD-TS")
    draft["created_at"] = when
    sqlite_client.save_ticket(draft)

    row = sqlite_client.get_ticket("TD-TS")
    assert row is not None
    parsed = datetime.fromisoformat(row["created_at"])
    assert parsed.tzinfo is not None, "created_at 应带 UTC 时区"
    assert parsed == when


# ---------------------------------------------------------------------------
# 8) 防御性：_created_at(None) 返回非空 ISO；_jsonable(None) 返回 None
# ---------------------------------------------------------------------------


def test_created_at_none_returns_iso_string():
    s = sqlite_client._created_at(None)
    assert isinstance(s, str) and s, "不应返回空串或 None"
    # 必须能 fromisoformat（_created_at 内部把 datetime 转 UTC+ISO）
    datetime.fromisoformat(s)


def test_jsonable_none_roundtrip():
    assert _jsonable(None) is None


# ---------------------------------------------------------------------------
# 9) evidence_status 取值链：顶层 have 优先于 ticket 内 have —— 自证场景
# ---------------------------------------------------------------------------


def test_evidence_status_prefers_top_level_have_over_ticket_have():
    """顶层 state.evidence_status 与 ticket 内 evidence_status **同时存在、内容不同**，
    断言 view 用的是顶层的 have —— 这条测试自证场景，不是空转。"""

    # 先让 TD-E 进 SQLite，走完整路径；不进 SQLite 时 full_view.agent_snapshot 仍有 ticket
    sqlite_client.save_ticket(_min_draft("TD-E"))

    # 构造：顶层 have 有值、ticket 内 have 为空
    state = {
        "ticket_draft": {
            "draft_id": "TD-E",
            "evidence_status": {"complete": False, "have": [], "missing": ["开箱视频"]},
        },
        "evidence_status": {"complete": True, "have": ["开箱视频"], "missing": []},
    }

    # 自证 1：两条确实同时存在且 have 不同 —— 否则这个测试是空转
    assert state["evidence_status"]["have"] != state["ticket_draft"]["evidence_status"]["have"]

    view = _build_ticket_view(state)

    # 自证 2：view 走到 SQLite 分支（full_view 应含 agent_snapshot）
    assert view["source"] == "sqlite"
    assert "agent_snapshot" in view["full_view"]

    # 核心断言：view 的 have 取的是顶层的，不是 ticket 的空数组
    assert view["evidence_status"]["have"] == ["开箱视频"], (
        "顶层 state.evidence_status.have 应优先于 ticket_draft.evidence_status.have"
    )
    # 进一步确认：agent_snapshot 里 ticket 内的 have 确实是空数组（自证场景有效）
    assert view["full_view"]["agent_snapshot"]["evidence_status"]["have"] == []


def test_evidence_status_fallback_to_ticket_when_top_level_empty():
    """顶层 evidence_status 为空 dict（falsy）或 None 时，应 fallback 到 ticket 内的 evidence_status。"""

    sqlite_client.save_ticket(_min_draft("TD-F"))

    state_empty = {
        "ticket_draft": {
            "draft_id": "TD-F",
            "evidence_status": {"complete": False, "have": ["开箱视频"]},
        },
        "evidence_status": {},
    }
    view_empty = _build_ticket_view(state_empty)
    assert view_empty["evidence_status"]["have"] == ["开箱视频"], "空 dict 应 fallback 到 ticket 内"

    state_none = {
        "ticket_draft": {
            "draft_id": "TD-F",
            "evidence_status": {"complete": False, "have": ["面单照片"]},
        },
        "evidence_status": None,
    }
    view_none = _build_ticket_view(state_none)
    assert view_none["evidence_status"]["have"] == ["面单照片"], "None 应 fallback 到 ticket 内"


def test_evidence_status_known_bug_truthy_top_level_overwrites_missing_have():
    """⚠️ 已知 Bug：_build_ticket_view 第 202 行用 `or` 整字典替换，
    当顶层 evidence_status truthy 但缺少 have 键时，会把 ticket 里完整的 have 一起盖掉。

    这条测试锁定当前行为（have 确实丢失），后续修复业务代码为"键级合并"时，
    需要翻转断言：应改为 assert view["evidence_status"]["have"] == ...
    修复方案：把整字典 `or` 改成键级合并 {**ticket_es, **state_es}。
    """

    sqlite_client.save_ticket(_min_draft("TD-BUG"))

    state = {
        "ticket_draft": {
            "draft_id": "TD-BUG",
            "evidence_status": {
                "complete": True,
                "have": ["开箱视频", "面单照片"],
                "missing": [],
            },
        },
        # 顶层 truthy，但故意缺 have 键 —— 这是 Agent 跑完后 State 可能出现的真实形态
        "evidence_status": {"complete": True},
    }

    view = _build_ticket_view(state)

    # 自证：ticket 内本来有完整 have
    assert "have" in state["ticket_draft"]["evidence_status"]
    assert state["ticket_draft"]["evidence_status"]["have"] == ["开箱视频", "面单照片"]

    # 已知 Bug：顶层 truthy 整字典覆盖后，have 键丢失
    assert "have" not in view["evidence_status"], (
        "BUG：顶层 truthy dict 覆盖掉了 ticket 内完整的 have —— "
        "当前 view.evidence_status 只有 {'complete': True}，证据面板不显示 have 列表"
    )
    assert view["evidence_status"] == {"complete": True}, "整字典覆盖确实发生了"
    # 修复后的期望（业务变更后翻转）：
    # expected = {**state["ticket_draft"]["evidence_status"], **state["evidence_status"]}
    # assert view["evidence_status"] == expected
