"""M4: Redis 会话上下文读写 + TTL。用 fakeredis 隔离，不依赖 Docker。"""
import pytest
import fakeredis

from app.memory import redis_client


@pytest.fixture()
def fake_redis(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_client, "_client", client)
    yield client
    client.flushall()


def test_save_then_load_roundtrip(fake_redis):
    redis_client.save_session(
        "s1",
        summary="用户反馈漏液",
        slots={"order_id": "SO20261116001"},
        retry_count=0,
    )
    ctx = redis_client.load_session("s1")
    assert ctx["summary"] == "用户反馈漏液"
    assert ctx["slots"] == {"order_id": "SO20261116001"}
    assert ctx["retry_count"] == 0


def test_partial_update_keeps_other_fields(fake_redis):
    redis_client.save_session("s1", summary="初版", slots={"a": 1})
    redis_client.save_session("s1", slots={"a": 2})
    ctx = redis_client.load_session("s1")
    assert ctx["summary"] == "初版"   # 未被覆盖
    assert ctx["slots"] == {"a": 2}


def test_ttl_set_after_write(fake_redis, monkeypatch):
    monkeypatch.setenv("REDIS_TTL_SECONDS", "60")
    redis_client.save_session("s1", summary="x")
    ttl = fake_redis.ttl("agent:ctx:s1")
    assert 0 < ttl <= 60


def test_ttl_refresh_on_load(fake_redis, monkeypatch):
    monkeypatch.setenv("REDIS_TTL_SECONDS", "60")
    redis_client.save_session("s1", summary="x")
    fake_redis.expire("agent:ctx:s1", 5)  # 手动压短
    redis_client.load_session("s1")       # 读一次应续期
    assert fake_redis.ttl("agent:ctx:s1") > 5


def test_load_missing_returns_empty(fake_redis):
    assert redis_client.load_session("nope") == {}


def test_empty_session_id_is_noop(fake_redis):
    redis_client.save_session("", summary="x")
    assert redis_client.load_session("") == {}