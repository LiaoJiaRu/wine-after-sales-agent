import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from app.llm import IntentDecision


@pytest.fixture(autouse=True)
def mock_llm_intent(monkeypatch):
    """全局 mock 意图路由，禁止测试发真实 LLM 请求。

    Docker 容器内没有 .env / DEEPSEEK_API_KEY，凡走 graph / cli 的测试
    （test_graph_basics / test_e2e_m2 / test_edge_cases）都会经
    app.graph.classify_intent（即 agents.router.route）或 app.cli.route
    触发真实 API 调用 → MISSING_API_KEY。这里统一替换为固定的
    "破损漏液"意图；test_router.py 自行 mock app.llm.complete，不受影响。
    个别测试（如合规拦截）可用 monkeypatch 再次覆盖同目标，后写先生效。
    """
    decision = IntentDecision(
        intent="破损漏液",
        confidence=0.95,
        need_clarify=False,
        sub_tasks=["破损漏液"],
    )

    def fake_route(text):
        # 保留 route() 的空输入守卫语义：空文本仍走 EMPTY_INPUT 降级，
        # 非空文本一律返回固定"破损漏液"意图，不发真实 API 请求。
        if not (text or "").strip():
            return IntentDecision(
                intent="其他",
                confidence=0.0,
                need_clarify=True,
                sub_tasks=[],
                error={"code": "EMPTY_INPUT", "message": "输入为空，请提供用户原话", "retryable": False},
                needs_human=True,
            )
        return decision

    monkeypatch.setattr("app.graph.classify_intent", fake_route)
    monkeypatch.setattr("app.cli.route", fake_route)


@pytest.fixture(autouse=True)
def isolate_handoff_queue(tmp_path_factory, monkeypatch):
    """把转人工队列重定向到临时目录，避免单测往 data/handoff_queue.jsonl 里写脏数据。"""
    path = tmp_path_factory.mktemp("handoff") / "handoff_queue.jsonl"
    monkeypatch.setenv("HANDOFF_QUEUE_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def isolate_memory(tmp_path_factory, monkeypatch):
    """M4：每个测试独立 SQLite 文件 + fakeredis 替身。

    - SQLite 指向 tmp_path，绝不写真实 data/wine_after_sales.db；
    - Redis 用 fakeredis（TTL/增量写语义与真实 Redis 一致），fakeredis 不可用时退回进程内实现；
    - 队列路径由 isolate_handoff_queue 单独重定向。
    """
    from app.memory import redis_client

    db_path = tmp_path_factory.mktemp("memory") / "tickets.db"
    monkeypatch.setenv("TICKETS_DB_PATH", str(db_path))

    try:
        import fakeredis

        fake = fakeredis.FakeRedis(decode_responses=True)
    except Exception:  # pragma: no cover - fakeredis 未安装时兜底
        fake = redis_client._MemoryClient()

    redis_client.reset_client()
    monkeypatch.setattr(redis_client, "_client", fake)
    yield db_path
    redis_client.reset_client()


@pytest.fixture(autouse=True)
def mock_judge(monkeypatch):
    """Judge 默认放行：单测禁止发真实 LLM 请求（Docker 内无 Key）。

    test_m4_judge.py 会在单个用例里再次 monkeypatch 覆盖本 fixture（后写生效）。
    """
    monkeypatch.setattr(
        "app.graph.judge_draft",
        lambda _state: {"pass": True, "reason": "OK(mock)", "failed_node": ""},
    )


