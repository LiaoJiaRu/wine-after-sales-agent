# 线上酒类售后理赔客服工单 Agent

面向葡萄酒电商售后场景的智能客服 Agent：读懂顾客一句话里的多个诉求，完成取证、赔付建议、回复草稿与工单草稿，再由人工审核闭环。系统只产出草稿，不自动退款、不修改订单状态。

当前交付基线（M7，2026-11-24），全量测试 **195 passed**（171 条 M1~M6 功能测试 + 24 条 M7 边界与展示层测试）。

## 1. 项目简介

### 1.1 业务范围

| 场景 | 示例 | 处理方式 |
|---|---|---|
| 破损漏液 | 酒瓶破裂、渗漏 | 取证 → 部分赔付建议（订单金额 30%，保价封顶） |
| 少件 / 错发 | 少发一瓶、发错规格 | 核对订单 → 补发建议或转人工 |
| 真伪争议 | 怀疑假货、防伪码异常 | 记录批号 → 转人工送检流程 |
| 物流延迟 | 未发货、运输停滞 | 转人工，不误算赔付 |
| 储存饮用咨询 | 怎么保存、开瓶后能放多久 | 知识库检索回答，不生成理赔草稿 |
| 多意图 | 破损 + 少件 + 保存咨询 | 拆子任务、并发取证、合并工单与回复 |

### 1.2 核心能力

- **多意图拆解与并发取证**：`task_outputs` 保存子任务，`ThreadPoolExecutor` 并发执行，结果按原顺序回写，单任务失败隔离。
- **多轮会话记忆**：Redis 保存订单与证据槽位（30 分钟滑动 TTL），第二轮仅凭 `session_id` 即可续接。
- **工单持久化**：SQLite `tickets` 表落库，同一订单新草稿写入前将旧 `draft` 置为 `superseded`，避免重复审批。
- **LLM Judge 质检**：结构化裁决 `pass / reason / failed_node`，失败只重跑失败节点，最多 2 次，超限转人工。
- **润色与事实锁定**：`polish` 只改表达，金额、订单号、编号清单与“人工审核”承诺不得变更，越界回退原稿。
- **双端工作台**：Streamlit 顾客端（对话 / 工单 / 证据与回复 / 审核与追踪）与商家审核端。
- **可观测与评测**：Langfuse v2 链路追踪；15 条端到端评测用例，其中 4 条具备真实 Trace。

### 1.3 安全边界

- 退款类诉求在 `router` 入口直接拦截（`COMPLIANCE_REFUND`），先于 LLM 与知识检索。
- 未成年人购酒/饮酒咨询命中 `MINOR_PROTECTION`，直接转人工。
- 超 7 天时效、数据源异常、订单不存在、无法识别的意图统一转人工。
- 所有工具只读或只写草稿/队列，不执行资金操作。

## 2. 核心架构

| 分层 | 实现 | 职责 |
|---|---|---|
| 接入层 | Streamlit `ui/streamlit_app.py`、FastAPI `app/main.py`、CLI `app/cli.py` | 顾客对话、商家审核、HTTP/命令行调用 |
| 编排层 | LangGraph `app/graph.py` | 9 个节点、条件边、递归上限 32 |
| 能力层 | `app/tools/readonly.py`、`app/tools/write.py` | 6 个只读工具 + 3 个草稿/队列写工具 |
| 记忆层 | `app/memory/redis_client.py`、`app/memory/sqlite_client.py` | 会话槽位、工单落库、故障降级 |
| 质检层 | `app/llm.py` + `app/prompts/` | LLM 结构化输出、Judge、polish、规则守卫 |
| 可观测 | Langfuse v2 | 路由与全链路 Trace |

主链路：

```text
START → load_session → router → fetch_evidence → check_evidence
      → create_draft → polish → judge → END
```

- 知识咨询：`router → knowledge_agent → END`。
- 转人工：`handoff_human → END`，同时写入 `data/handoff_queue.jsonl`（队列路径已锚定项目根，Docker 绝对路径放行）。
- Judge 不通过：按 `failed_node` 回跳 `create_draft` 或 `fetch_evidence`，最多 2 次。

完整节点、条件边、状态字段与 9 个工具接口见 [docs/architecture.md](docs/architecture.md)。

## 3. 目录结构

```text
wine-after-sales-agent/
├─ app/
│  ├─ graph.py              # LangGraph 主链路与条件边
│  ├─ llm.py                # LLM 客户端、Judge、润色、Langfuse
│  ├─ cli.py                # 命令行入口
│  ├─ main.py               # FastAPI 入口
│  ├─ agents/               # 意图路由与专业子 Agent
│  ├─ memory/               # Redis 会话、SQLite 工单
│  ├─ prompts/              # router / judge / polish Prompt
│  ├─ rules/                # 规则扩展位
│  └─ tools/                # 只读工具、草稿工具、Schema、错误码
├─ ui/streamlit_app.py      # 双端工作台
├─ data/                    # 4 份 Mock 数据与运行时产物
├─ tests/                   # 195 条测试（23 个文件）
├─ scripts/                 # 指标采集与真实 Trace 采集
├─ docs/                    # 架构、Prompt、进度、Trace、Docker 记录
├─ Dockerfile
├─ docker-compose.yml
├─ requirements.txt
└─ .env.example
```

## 4. 快速启动

### 4.1 前置条件

- Python 3.11+（本项目在 3.12 上开发验证）
- Redis 可选：未启动时自动降级为进程内会话存储
- Docker Desktop 可选：仅 Compose 方式需要

### 4.2 方式一：本地运行

> ⚠️ **首次启动必须先配置环境变量**：复制 `.env.example` 为 `.env` 并填入 `DEEPSEEK_API_KEY`（必填），`LANGFUSE_*`（选填）。否则 Agent 的 LLM 环节无法运行。

```powershell
cd wine-after-sales-agent
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

# 复制环境变量模板（必填）
Copy-Item .env.example .env
# 然后用记事本打开 .env，填入 DEEPSEEK_API_KEY=你的真实Key（留占位符会报错）

# 全量测试（无需真实 Key，测试已全局 Mock）
pytest -q

# 启动双端工作台
streamlit run ui/streamlit_app.py --server.port=8000
```

浏览器打开 `http://localhost:8000`，左侧可切换“顾客端 / 商家审核端”。

命令行示例：

```powershell
# 仅意图路由
python -m app.cli "我买的酒漏了"

# 完整图流程（多轮会话）
python -m app.cli --graph --order SO20261116001 --session demo-001 "我买的酒漏了，还少一瓶，这酒怎么保存？"

# 两幕演示脚本：缺证补证 / 证据齐全 354.00 元赔付建议
python app/run_m2.py
python app/run_m2.py --with-evidence
```

可选 HTTP 入口：

```powershell
uvicorn app.main:app --port 8001
```

- `GET /health`：健康检查
- `POST /graph/invoke`：入参 `user_text`、`order_id`、`session_id`、`evidence`

### 4.3 方式二：Docker Compose 一键启动

> ⚠️ **Docker 同样需要 .env**：`docker-compose.yml` 里 `env_file: .env` 是硬依赖，缺少 `.env` 会直接启动失败。按上面的方式先复制并填写 `.env`。

```powershell
cd wine-after-sales-agent
Copy-Item .env.example .env   # 先填好 DEEPSEEK_API_KEY（必填），否则容器里 LLM 环节报错
docker compose up --build
```

- 工作台：`http://localhost:8501`（容器内 8000，映射 8501:8000）
- 健康检查：`http://localhost:8501/_stcore/health`
- 服务依赖：`redis` 健康后 `app` 才启动；`redis` 仅绑定 `127.0.0.1:6379`
- 数据卷：`./data:/app/data`，工单库与 Mock 数据持久化在宿主机
- 停止：`docker compose down`

仅构建镜像：

```powershell
docker build -t wine-agent:latest .
```

说明：Compose 通过 `env_file: .env` 注入配置，缺少 `.env` 会启动失败；若只想离线验证测试，直接执行本地 `pytest -q` 即可，测试不依赖 `.env`、Redis 与真实 API。

## 5. 环境变量清单

从 `.env.example` 复制为 `.env`。模板共 15 个键，其中 3 个必须填写真实值：

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | 是 | 占位符 | DeepSeek API Key |
| `DEEPSEEK_BASE_URL` | 否 | `https://api.deepseek.com` | OpenAI 兼容地址，可切换其他兼容服务 |
| `LANGFUSE_PUBLIC_KEY` | 是 | 占位符 | Langfuse 公钥 |
| `LANGFUSE_SECRET_KEY` | 是 | 占位符 | Langfuse 私钥 |
| `LANGFUSE_HOST` | 否 | `http://localhost:3000` | 模板默认本地；实际使用云端 `https://jp.cloud.langfuse.com`，不修改则 Trace 静默不落库 |
| `LANGFUSE_ENABLED` | 否 | `true` | 是否上报 Trace |
| `USE_MOCK_DATA` | 否 | `true` | 使用 `data/` 下的 Mock 数据 |
| `REDIS_URL` | 否 | `redis://localhost:6379/0` | 会话存储；不可用时降级为进程内字典 |
| `REDIS_TTL_SECONDS` | 否 | `1800` | 会话 TTL（秒），读写后滑动续期 |
| `SQLITE_PATH` | 否 | `data/wine_after_sales.db` | 工单库路径；相对路径已锚定 PROJECT_ROOT（项目根目录），不跟随 CWD 漂移；Docker Compose 提供绝对路径时原样放行 |
| `CHROMA_PATH` | 否 | `data/chroma` | 向量库预留路径（当前未启用） |
| `APP_ENV` | 否 | `dev` | 运行环境标识 |
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 |
| `MAX_STEPS` | 否 | `20` | Agent 步数上限配置 |
| `MAX_RETRY` | 否 | `2` | 重试上限配置 |

代码中还会读取以下可选覆盖项（未写入模板，按需设置）：

| 变量 | 作用 |
|---|---|
| `TICKETS_DB_PATH` | 优先于 `SQLITE_PATH` 的工单库路径；测试与 Compose 用它隔离；相对路径锚定 PROJECT_ROOT |
| `HANDOFF_QUEUE_PATH` | 转人工队列 JSONL 路径；测试用临时目录隔离；锚定策略同上 |

`.env` 已被 `.gitignore` 排除，不会入库；`.env.example` 只含占位符与非敏感默认值。

## 6. 测试命令

### 6.1 全量测试

```powershell
pytest -q
```

预期结果：**195 passed**（171 条 M1~M6 功能测试 + 24 条 M7 边界与展示层测试）。用例按模块分布：

| 模块 | 覆盖内容 | 文件 | 条数 |
|---|---|---|---|
| M1 基础 | 只读工具、意图路由 | `test_readonly_tools.py`、`test_router.py` | 15 |
| M2 单场景 | 草稿工具、端到端、边界与合规、图骨架 | `test_draft_tools.py`、`test_e2e_m2.py`、`test_edge_cases.py`、`test_graph_basics.py` | 40 |
| M3 多意图 | 并发取证、知识检索、话术合并、意图兜底、写队列 | `test_m3_*.py`、`test_issue_type_and_handoff.py` | 45 |
| M4 记忆与审核 | Redis、SQLite、槽位抽取、Judge、局部重跑 | `test_m4_*.py` | 47 |
| M5 润色 | 事实锁定、越界回退、图内位置 | `test_m5_polish.py` | 9 |
| M6 评测 | 15 条端到端用例 U01~U15 | `test_m6_evaluation.py` | 15 |
| M7 演示边界 | 无关文本、超长输入、SQL 注入、非法订单号 | `test_edge_demo.py` | 4 |
| M7 UI 展示层 | 工单合并视图、SQLite 路径错/锁/不存在、_jsonable 序列化 | `test_edge_display.py`、`test_display_source.py` | 14 + 6 |
| 合计 | — | 23 个测试文件 | **195** |

### 6.2 分层执行

```powershell
# 15 条端到端评测
pytest -q tests/test_m6_evaluation.py

# Judge 与局部重跑
pytest -q tests/test_m4_judge.py tests/test_m4_retry.py

# 多轮记忆与槽位
pytest -q tests/test_m4_redis.py tests/test_m4_slot.py tests/test_m4_memory.py
```

### 6.3 测试隔离约定

- `tests/conftest.py` 全局 Mock 意图路由与 Judge，禁止单测发起真实 LLM 请求。
- Redis 使用 `fakeredis`，SQLite 与转人工队列使用 `tmp_path`，不写入真实 `data/`。
- 真实 LLM 仅用于手工演示与 `scripts/run_m6_real_traces.py` 的 4 条 Trace 补录。

## 7. 数据与评测证据

| 内容 | 位置 |
|---|---|
| 15 条评测与 4 条真实 Trace | [docs/traces/m6-evaluation-traces.md](docs/traces/m6-evaluation-traces.md) |
| 6 项指标采集报告 | [docs/progress/11-23-m6-metrics.md](docs/progress/11-23-m6-metrics.md) |
| M6 评测明细 | [docs/progress/11-23-m6-evaluation.md](docs/progress/11-23-m6-evaluation.md) |
| M6 冻结与 Compose 验证 | [docs/progress/11-23-m6-wrap-up.md](docs/progress/11-23-m6-wrap-up.md) |
| 各阶段进度记录 | `docs/progress/` |
| Docker 首次构建记录 | `docs/docker/11-17-first-build.md` |
| 指标与 Trace 采集脚本 | `scripts/collect_m6_metrics.py`、`scripts/run_m6_real_traces.py` |

Mock 数据位于 `data/`：订单、物流、批次、政策各一份；政策库含 `P-KNOW-001~003` 酒知识。运行时生成的 `data/wine_after_sales.db` 与 `data/handoff_queue.jsonl` 均已被 `.gitignore` 覆盖；路径锚定同样适用——换目录运行不会产生"野库"。

## 8. 常见问题

**Q6：为什么换目录跑 UI 还能读同一个 SQLite 库？**
M7 修复了路径漂移问题——`_db_path()` 用 `Path(__file__).resolve().parents[2]` 锚定项目根目录，`_handoff_queue_path()` 同理。env 提供绝对路径时原样放行（Docker Compose 场景），相对路径强制拼接 PROJECT_ROOT，CWD 不影响。

**Q7：为什么我之前换目录跑会产生"野库"？**
M7 之前 `_db_path()` 直接用 env 值或默认相对路径，在工作区根目录跑脚本和在项目目录跑 UI 会产生两个不同的 `wine_after_sales.db`。修复后已不存在该问题。

**Q1：为什么没有 Chroma 向量检索？**
Windows 缺少 C++ 编译环境，`chromadb` 暂未启用。`search_policy` 采用“知识关键词加权 + 字符重合度”降级方案，接口保留，后续环境就绪可切换。

**Q2：Trace 页面没有数据？**
检查 `.env` 的 `LANGFUSE_HOST` 是否改为云端地址，`LANGFUSE_ENABLED` 是否为 `true`，以及 Key 是否有效。

**Q3：本地没有 Redis 会怎样？**
`redis_client` 自动降级为进程内字典，功能可用但不跨进程持久；生产/演示建议启动 Redis。

**Q4：重复提交同一订单会生成多条待审工单吗？**
不会。写入新草稿前会把该订单旧的 `draft` 置为 `superseded`，商家端只展示最新待审单。

**Q5：端口被占用怎么办？**
本地启动可改 `--server.port`；Compose 方式修改 `docker-compose.yml` 的 `8501:8000` 左侧端口。

## 9. 文档索引

- [docs/architecture.md](docs/architecture.md)：9 节点 LangGraph 状态图、ReAct 循环设计取舍、9 个工具接口、错误码、路径锚定与异常降级策略
- [docs/prompt_design.md](docs/prompt_design.md)：Router / Judge / Polish 设计思路与迭代记录
- [docs/progress/](docs/progress/)：M2~M6 阶段收尾与指标报告
- [docs/traces/](docs/traces/)：Langfuse Trace 证据
- [docs/docker/](docs/docker/)：容器构建与 Compose 验证

## 评分维度索引（对照实训单五、评分标准）

| 评分维度 | 分值 | 本仓库证据位置 |
|---|---|---|
| ① Agent 能力真实性 | 25 | docs/architecture.md §2 LangGraph 状态流转图 + demo_script §5~6 两幕完整业务链（多意图拆解为三个并发子任务） |
| ② 架构设计合理性 | 15 | docs/architecture.md §1 分层解耦 + §3 设计取舍（确定性编排 vs 自由 ReAct vs Plan-and-Execute 三方对比） |
| ③ 工具开发质量 | 15 | docs/architecture.md §4 工具接口 + §5 错误码分层 + app/tools/schemas.py Pydantic Schema 校验 |
| ④ Prompt 工程质量 | 10 | docs/prompt_design.md Router/Judge/Polish 三份 Prompt 的设计思路与迭代记录 |
| ⑤ 评测与可观测性 | 15 | 195 条 pytest 全绿 + 4 条真实 Langfuse Trace + 15/15 端到端评测 + final_report §5 失败复盘 |
| ⑥ 工程规范与部署 | 10 | Docker Compose + fakeredis + tmp_path 全局隔离 + 路径锚定（本文件 §5） |
| ⑦ 报告深度 | 10 | final_report §6 设计权衡与踩坑记录 + architecture §6.4~6.5 异常降级与路径锚定 |
