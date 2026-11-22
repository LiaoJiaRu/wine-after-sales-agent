"""SQLite 工单落库（M4 阶段1）。

表 `tickets` 只记录工单草稿的索引信息（不含赔付明细，避免与 Mock 数据重复存储）：
    draft_id / order_id / issue_type / summary / status / created_at

路径优先级：TICKETS_DB_PATH > SQLITE_PATH > data/wine_after_sales.db。
测试通过 TICKETS_DB_PATH 指向 tmp_path，避免污染仓库。
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    draft_id   TEXT PRIMARY KEY,
    order_id   TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    summary    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickets_order ON tickets(order_id);
"""


def _db_path() -> Path:
    raw = os.getenv("TICKETS_DB_PATH") or os.getenv("SQLITE_PATH") or "data/wine_after_sales.db"
    path = Path(raw).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(_db_path(), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> Path:
    """建表（幂等）。返回实际使用的数据库路径。"""
    with _connect() as conn:
        conn.executescript(_SCHEMA)
    return _db_path()


def _created_at(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")
    text = str(value or "").strip()
    return text or datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_ticket(draft: dict[str, Any]) -> bool:
    """插入/覆盖一条工单草稿记录。draft 为 TicketDraft.model_dump() 的结果。

    返回 True 表示写入成功；字段缺失时抛 KeyError，由调用方决定是否降级。
    """
    init_db()
    row = (
        str(draft["draft_id"]),
        str(draft["order_id"]),
        str(draft["issue_type"]),
        str(draft.get("summary") or ""),
        str(draft.get("status") or "draft"),
        _created_at(draft.get("created_at")),
    )
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO tickets "
            "(draft_id, order_id, issue_type, summary, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            row,
        )
    return True


def get_tickets_by_order(order_id: str) -> list[dict[str, Any]]:
    """按订单号查工单草稿，按创建时间倒序。"""
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT draft_id, order_id, issue_type, summary, status, created_at "
            "FROM tickets WHERE order_id = ? ORDER BY created_at DESC, draft_id DESC",
            (str(order_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def count_tickets() -> int:
    init_db()
    with _connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])
