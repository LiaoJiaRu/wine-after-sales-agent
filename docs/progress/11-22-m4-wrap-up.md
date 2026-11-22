# 11-22 M4 收尾：落库 / 记忆 / Judge / 重跑 / 容器验证

## 1 今日结论

M4 四个能力全部落地并进入主链：**SQLite 工单落库**、**Redis 多轮会话记忆**、**LLM Judge 结构化裁决**、
**failed_node 局部重跑（上限 2 次）**。全量 **135 passed**（本地 15.33s；容器内另见 §6），
测试全程不写真实 `data/`，也不依赖本地 Redis 与真实 API Key。

## 2 交付清单

| # | 交付 | 落点 | 状态 |
|---|---|---|---|
| 1 | SQLite 客户端 | `app/memory/sqlite_client.py` | ✅ |
| 2 | Redis 客户端（含降级实现） | `app/memory/redis_client.py` | ✅ |
| 3 | 会话加载/写回 | `graph.load_session` / `graph.create_draft` | ✅ |
| 4 | Judge prompt | `app/prompts/judge_v1.txt` | ✅ |
| 5 | Judge 调用与解析/修复重试 | `app/llm.py::judge_draft` | ✅ |
| 6 | Judge 节点与条件边 | `graph.judge` / `graph._route_after_judge` | ✅ |
| 7 | 多意图缺证合并修复 | `graph.fetch_evidence` / `graph.create_draft` | ✅ |
| 8 | 测试隔离 | `tests/conftest.py`（tmp SQLite + fakeredis） | ✅ |
| 9 | 容器验证 | `Dockerfile` / `docker-compose.yml` | ✅ |

## 3 机制说明

### 3.1 SQLite 工单落库

表结构（`init_db()` 幂等建表）：

```sql
CREATE TABLE IF NOT EXISTS tickets (
    draft_id   TEXT PRIMARY KEY,
    order_id   TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    summary    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'draft',   -- 只产草稿，恒 draft
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickets_order ON tickets(order_id);
```

- `save_ticket()` 用 `INSERT OR REPLACE`，按 `draft_id` 幂等；`get_tickets_by_order()` 按时间倒序
- 路径：`TICKETS_DB_PATH > SQLITE_PATH > data/wine_after_sales.db`
- 落库时机：`create_draft` 出草稿后；**转人工路径不落库**，避免半成品工单进库
- 写入失败只降级（不吞草稿）

### 3.2 Redis 多轮记忆

- Key：`agent:ctx:{session_id}`，Hash 存 `summary / slots / retry_count / last_active`，TTL 30 分钟滑动续期
- `slots` 存 `order_id / issue_type / evidence / ticket_draft_id`
- `load_session` 合并规则：**本轮显式输入优先，历史只补空缺**；因此第二轮只给 `session_id` 也能续上证据并算出 354.00
- 可用性：真实 Redis 连不上（本地未启动 / 容器无 Redis）时自动降级为进程内实现，接口语义一致，不阻断主链

### 3.3 LLM Judge

- Prompt 三条硬标准：不得承诺直接退款、金额须与计算一致、补证清单须完整
- 输出严格 JSON：`{"pass": bool, "reason": str, "failed_node": "create_draft"|"fetch_evidence"}`
- 解析失败触发一次修复重试；两次非法则 **fail-closed**（转人工）
- LLM 不可用（无 Key/超时）则 **Fail-Open 降级放行**，`judge_result.degraded=true`；
  确定性守卫（合规拦截、Schema 校验、证据核验）仍在前置生效
- 通过后清空 `judge_feedback`；未通过写入原因，供下一轮 `create_draft` 追加到工单摘要「（质检复核：…）」

### 3.4 failed_node 局部重跑

| 条件 | 动作 |
|---|---|
| `pass=true` | END |
| `pass=false` 且 `retry_count < 2` | `retry_count += 1`，按 `failed_node` 回跳 `create_draft` 或 `fetch_evidence` |
| `retry_count >= 2` | `handoff=True`，`error.code=JUDGE_REJECTED`，写队列后 END |

- 最多 3 次 Judge（首次 + 2 次重试）
- 步数上限：`RECURSION_LIMIT = 16`，并在 `cli.run_graph` / `run_m2` 显式传给 `invoke(config=...)`
- `retry_count` 只在 judge 节点递增，避免同 step 重复写 State

### 3.5 多意图缺证合并（Bug 修复）

- 根因：`fetch_evidence` 只把主诉 evidence 写进 `evidence_status`，少件的「整箱称重照片」丢失
- 修复：多理赔任务时 `missing` 按任务顺序去重合并、`complete` 取交集；
  `create_draft` 用 `EvidenceStatus` 重建工单证据状态
- 效果：State / 工单 / 回复 checklist / Judge payload 四处同源

## 4 防污染与离线可用

`tests/conftest.py` 三个 autouse fixture：

| fixture | 作用 |
|---|---|
| `isolate_handoff_queue` | 队列写入 `tmp_path`，不碰 `data/handoff_queue.jsonl` |
| `isolate_memory` | `TICKETS_DB_PATH` → `tmp_path`；Redis 替换为 fakeredis（不可用时退回进程内实现） |
| `mock_judge` | Judge 默认放行，禁止单测发真实 LLM 请求 |

验证方式：清空 `data/` 下遗留产物后跑全量，**跑完不生成 `wine_after_sales.db` / `handoff_queue.jsonl`**。

## 5 测试分布（135 条 / 15 个文件）

| 文件 | 条数 | 覆盖 |
|---|---|---|
| test_readonly_tools.py | 9 | 6 只读工具 |
| test_router.py | 6 | 路由与降级 |
| test_draft_tools.py | 8 | 草稿写工具 |
| test_graph_basics.py | 5 | 图骨架与 State 字段 |
| test_e2e_m2.py | 13 | 两幕 E2E |
| test_edge_cases.py | 14 | 边界/合规 |
| test_issue_type_and_handoff.py | 7 | 意图兜底与写队列 |
| test_m3_knowledge.py | 8 | 知识检索与节点 |
| test_m3_multi_intent.py | 7 | 多意图合并 |
| test_m3_parallel.py | 6 | 并发取证 |
| test_m3_subtasks.py | 6 | 任务对象化 |
| test_m3_edge.py | 4 | 多任务边界 |
| test_m3_merge.py | 7 | 结果合并 |
| test_m4_memory.py | 10 | SQLite / 记忆 |
| test_m4_judge.py | 12 | Judge / 单意图兜底 |
| test_m4_retry.py | 7 | 局部重跑 / 缺证合并 |
| test_m4_redis.py | 6 | Redis 读写与 TTL |

## 6 Docker 验证

- `Dockerfile`：`pip install` 改为可配置源（`ARG PIP_INDEX_URL`，默认官方 PyPI），
  构建时用 `--build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/` 规避清华源对部分新版本 wheel 的 403
- `docker-compose.yml`：app 服务去掉 `env_file: .env` 依赖、`restart: "no"`，测试容器无 Key 也能跑；
  redis 服务保持 `127.0.0.1:6379` + healthcheck
- `docker compose up -d redis`：`wine-after-sales-agent-redis-1` 状态 healthy
- `docker build -t wine-agent-m4 .`：构建成功（sha256 `0f806afd…`）
- `docker run --rm wine-agent-m4`：容器内（无 .env、无外部 Redis）**135 passed in 5.83s**
- 本地对照：`pytest -q` **135 passed in 15.33s**，跑完 `data/` 不产生 db 与队列文件

## 7 遗留与下一步

- `persist` 独立节点：当前仍在 create_draft 内落库，后续如需"Judge 通过后才落库"再拆分
- Judge 重跑期间每次 create_draft 会生成新 `draft_id`，库里可能留多条草稿版本；如需只保留终稿，可在 persist 节点收敛
- M5：Streamlit 四视图 + Trace 深链；M6：15 条评测脚本与失败归因；M7：docker-compose 一键起服务与交付文档
