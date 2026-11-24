"""SQLite 工单落库（M4 阶段1）。

表 `tickets` 只记录工单草稿的索引信息（不含赔付明细，避免与 Mock 数据重复存储）：
    draft_id / order_id / issue_type / summary / status / amount / created_at

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
    amount     REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickets_order ON tickets(order_id);
"""


def _db_path() -> Path:
    """SQLite 路径解析，消除 CWD 漂移。

    优先级：TICKETS_DB_PATH > SQLITE_PATH > 默认值（锚定项目根）。

    - 绝对路径：原样放行（Docker / 测试 tmp_path 覆盖场景）。
    - 相对路径：强制拼接 PROJECT_ROOT（本文件向上两级：
      ``wine-after-sales-agent/app/memory/sqlite_client.py → wine-after-sales-agent/``），
      彻底消除跟随 CWD 漂移的隐患。
    """
    PROJECT_ROOT = Path(__file__).resolve().parents[2]

    raw = (
        os.getenv("TICKETS_DB_PATH")
        or os.getenv("SQLITE_PATH")
        or "data/wine_after_sales.db"
    ).strip()

    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()

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
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tickets)")}
        if "amount" not in columns:
            # 兼容 M4 旧库：补列后历史工单金额按 0 处理
            conn.execute("ALTER TABLE tickets ADD COLUMN amount REAL NOT NULL DEFAULT 0")
    return _db_path()


def _created_at(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")
    text = str(value or "").strip()
    return text or datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_ticket(draft: dict[str, Any]) -> bool:
    """插入/覆盖一条工单草稿记录。draft 为 TicketDraft.model_dump() 的结果。

    同一 order_id 只保留最新一条待审工单：写入前先把该订单旧的 draft 置为 superseded，
    避免商家重复审核。返回 True 表示写入成功；字段缺失时抛 KeyError，由调用方决定是否降级。
    """
    init_db()
    compensation = draft.get("compensation") or {}
    row = (
        str(draft["draft_id"]),
        str(draft["order_id"]),
        str(draft["issue_type"]),
        str(draft.get("summary") or ""),
        str(draft.get("status") or "draft"),
        float(compensation.get("amount") or 0.0),
        _created_at(draft.get("created_at")),
    )
    with _connect() as conn:
        conn.execute(
            "UPDATE tickets SET status = 'superseded' WHERE order_id = ? AND status = 'draft'",
            (row[1],),
        )
        conn.execute(
            "INSERT OR REPLACE INTO tickets "
            "(draft_id, order_id, issue_type, summary, status, amount, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            row,
        )
    return True


def get_tickets_by_order(order_id: str) -> list[dict[str, Any]]:
    """按订单号查工单草稿，按创建时间倒序。"""
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT draft_id, order_id, issue_type, summary, status, amount, created_at "
            "FROM tickets WHERE order_id = ? ORDER BY created_at DESC, draft_id DESC",
            (str(order_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_ticket(draft_id: str) -> dict[str, Any] | None:
    """按 draft_id 查单条工单；查不到返回 None（不抛异常）。"""
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT draft_id, order_id, issue_type, summary, status, amount, created_at "
            "FROM tickets WHERE draft_id = ?",
            (str(draft_id),),
        ).fetchone()
    return dict(row) if row is not None else None


def list_tickets(status: str | None = None) -> list[dict[str, Any]]:
    """按状态查工单（不传 status 返回全部），按创建时间倒序。"""
    init_db()
    sql = (
        "SELECT draft_id, order_id, issue_type, summary, status, amount, created_at "
        "FROM tickets"
    )
    params: tuple[Any, ...] = ()
    if status:
        sql += " WHERE status = ?"
        params = (str(status),)
    sql += " ORDER BY created_at DESC, draft_id DESC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def update_ticket_status(draft_id: str, status: str) -> bool:
    """更新工单状态（如 draft → approved / rejected），返回是否有记录命中。"""
    init_db()
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE tickets SET status = ? WHERE draft_id = ?",
            (str(status), str(draft_id)),
        )
        return cursor.rowcount > 0


def count_tickets() -> int:
    init_db()
    with _connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])
