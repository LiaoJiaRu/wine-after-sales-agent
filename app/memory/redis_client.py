"""Redis 会话上下文存储（M4）。

Key 设计: agent:ctx:{session_id}
数据结构: Hash (summary / slots / retry_count / last_active)
TTL: 每次读写后滑动续期 REDIS_TTL_SECONDS（默认 1800s）

可用性策略：Redis 连不上（本地未启动 / Docker 内无 Redis）时自动降级为进程内字典，
接口与 Hash 语义一致，保证图流程不因缓存不可用而崩溃；测试通过 `reset_client()`
+ 进程内实现隔离，不依赖外部 Redis。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import redis

_DEFAULT_TTL = 1800  # 30 分钟
_client: redis.Redis | None = None
_STORE: dict[str, dict[str, str]] = {}


class _MemoryPipeline:
    def __init__(self, store: dict[str, dict[str, str]]):
        self._store = store
        self._ops: list[tuple[str, str, dict[str, Any]]] = []

    def hset(self, key: str, mapping: dict[str, Any] | None = None, **_kw):
        self._ops.append(("hset", key, dict(mapping or {})))
        return self

    def expire(self, key: str, _ttl: int):
        self._ops.append(("expire", key, {}))
        return self

    def execute(self) -> list[Any]:
        for op, key, mapping in self._ops:
            if op == "hset":
                bucket = self._store.setdefault(key, {})
                bucket.update({str(k): str(v) for k, v in mapping.items()})
        self._ops.clear()
        return []


class _MemoryClient:
    """Redis 不可用时的进程内实现（只覆盖本项目用到的 Hash 命令）。"""

    def __init__(self, store: dict[str, dict[str, str]] | None = None):
        self._store = _STORE if store is None else store

    def ping(self) -> bool:
        return True

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._store.get(key, {}))

    def hset(self, key: str, mapping: dict[str, Any] | None = None, **_kw) -> int:
        bucket = self._store.setdefault(key, {})
        bucket.update({str(k): str(v) for k, v in (mapping or {}).items()})
        return len(mapping or {})

    def expire(self, key: str, _ttl: int) -> bool:
        return key in self._store

    def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    def pipeline(self) -> _MemoryPipeline:
        return _MemoryPipeline(self._store)


def _build_client() -> redis.Redis:
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return redis.from_url(
        url, decode_responses=True, socket_connect_timeout=0.5, socket_timeout=0.5
    )


def get_client():
    global _client
    if _client is None:
        try:
            candidate = _build_client()
            candidate.ping()
            _client = candidate
        except Exception:
            _client = _MemoryClient()
    return _client


def reset_client() -> None:
    """测试用：清空连接与进程内数据。"""
    global _client
    _client = None
    _STORE.clear()


def _ttl() -> int:
    try:
        return int(os.getenv("REDIS_TTL_SECONDS", _DEFAULT_TTL))
    except ValueError:
        return _DEFAULT_TTL


def _key(session_id: str) -> str:
    return f"agent:ctx:{session_id}"


def _call(fn, *args, **kwargs):
    """Redis 操作失败时切到进程内实现，保证调用方无感。"""
    global _client
    try:
        return fn(get_client(), *args, **kwargs)
    except Exception:
        _client = _MemoryClient()
        return fn(_client, *args, **kwargs)


def load_session(session_id: str) -> dict[str, Any]:
    """读取会话上下文。不存在或 session_id 为空时返回空 dict。"""
    if not session_id:
        return {}
    key = _key(session_id)
    raw = _call(lambda c, k: c.hgetall(k), key)
    if not raw:
        return {}
    _call(lambda c, k: c.expire(k, _ttl()), key)  # 滑动续期
    try:
        slots = json.loads(raw.get("slots") or "{}")
    except json.JSONDecodeError:
        slots = {}
    try:
        retry_count = int(raw.get("retry_count", 0))
    except ValueError:
        retry_count = 0
    return {
        "summary": raw.get("summary", ""),
        "slots": slots,
        "retry_count": retry_count,
        "last_active": raw.get("last_active", ""),
    }


def save_session(
    session_id: str,
    *,
    summary: str | None = None,
    slots: dict[str, Any] | None = None,
    retry_count: int | None = None,
) -> None:
    """增量写入（只写传入的字段），并刷新 TTL。"""
    if not session_id:
        return
    key = _key(session_id)
    payload: dict[str, Any] = {
        "last_active": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if summary is not None:
        payload["summary"] = summary
    if slots is not None:
        payload["slots"] = json.dumps(slots, ensure_ascii=False)
    if retry_count is not None:
        payload["retry_count"] = str(retry_count)

    def _write(client, k: str, data: dict[str, Any]):
        pipe = client.pipeline()
        pipe.hset(k, mapping=data)
        pipe.expire(k, _ttl())
        pipe.execute()

    _call(_write, key, payload)


def clear_session(session_id: str) -> None:
    if session_id:
        _call(lambda c, k: c.delete(k), _key(session_id))
