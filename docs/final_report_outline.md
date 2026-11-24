# 实训报告大纲（8 节）

> 项目：线上酒类售后理赔客服工单 Agent
> 版本：M7 交付版（2026-11-24）
> 用途：按本大纲逐节扩写即可形成完整实训报告，建议全文 8000~10000 字。
> 写作原则：每一节都对应到具体代码、常量、State 字段或测试证据，不写无法验证的空泛结论。

## 0 使用说明

| 项目 | 约定 |
|---|---|
| 章节 | 严格按 8 节：背景、需求、设计、实现、测试、难点、不足、总结 |
| 每节结构 | 写作目标 + 必写事实 + 建议图表 + 证据位置 + 建议字数 |
| 数据口径 | 功能测试 171 条（M1~M6）+ M7 边界与展示层 24 条 = 195 条；评测 15/15；冻结基线 M7 交付（2026-11-24） |
| 代码引用 | 使用相对路径与函数名，如 `app/graph.py::fetch_evidence` |
| 图表 | 架构图、状态图、时序图建议直接引用 `docs/architecture.md` 的 Mermaid 源 |

## 1 项目背景（建议 700~900 字）

### 1.1 写作目标

说明为什么需要“售后理赔 Agent”，而不是一个普通客服问答机器人。

### 1.2 必写事实

- 业务场景：葡萄酒电商售后，典型问题包括破损漏液、少件错发、真伪争议、物流延迟、标签保质期、储存饮用咨询。
- 人工痛点：一句话常含多个诉求，人工需要分别查订单、物流、批次、政策；赔付口径依赖经验，容易出现金额不一致或越权承诺。
- 合规约束：退款属于资金操作，必须人工审核；未成年人购酒/饮酒必须拦截；超过 7 天时效不能自动赔付。
- 项目定位：系统只产出“回复草稿 + 工单草稿”，不自动退款、不修改订单状态，所有结论可由人工复核。

### 1.3 建议图表

| 图表 | 内容 |
|---|---|
| 表 1-1 | 六类售后场景、顾客表达示例、期望处理方式 |
| 图 1-1 | 人工处理流程与 Agent 辅助流程对比（泳道图） |

### 1.4 证据位置

- 场景与验收口径：`docs/architecture.md` 第 1 节
- 评测用例：`tests/test_m6_evaluation.py` 的 `CASES`
- Mock 数据：`data/mock_orders.json`、`mock_logistics.json`、`mock_batches.json`、`mock_policies.json`

## 2 需求分析（建议 1000~1300 字）

### 2.1 写作目标

把“想做一个 Agent”拆成可验收的功能与非功能需求，并说明裁剪依据。

### 2.2 功能需求

按主链路写，不要按技术堆栈写：

1. 意图识别与多意图拆解：一句话拆出 2~3 个子任务。
2. 取证：订单、物流、批次、政策、证据完整性、赔付计算共 6 个只读能力。
3. 草稿：工单草稿、回复草稿、转人工队列 3 个写能力，均无资金副作用。
4. 记忆：同一 `session_id` 支持跨轮补证。
5. 审核：Judge 质检、失败节点局部重跑、超限转人工。
6. 双端：顾客端对话与证据上传，商家端审核通过/打回。
7. 可观测：Langfuse Trace 与评测指标。

### 2.3 非功能需求

| 维度 | 要求 | 落地方式 |
|---|---|---|
| 可测试 | 单测不依赖真实 API、Redis、SQLite | `tests/conftest.py` 全局 Mock + fakeredis + tmp_path |
| 可复现 | 干净环境一条命令启动 | Docker Compose + healthcheck + 数据卷 |
| 可观测 | 每次链路可回放 | Langfuse v2 + `trace_url()` |
| 安全 | 不自动退款、不越权承诺 | router 合规守卫 + Judge + polish 事实锁定 |
| 稳定 | 异常不崩溃 | 节点内 try/except + 条件边转人工 + 递归上限 |

### 2.4 范围与裁剪

- P0：单场景闭环、9 个工具、Redis/SQLite、Judge、15 条评测、Docker 与文档。
- P1：多意图、润色、Streamlit 全功能、证据上传模拟，最终全部完成。
- P2：MCP Server、协作可视化、小程序壳未启动；最小审核后台由 Streamlit 商家端覆盖。
- 裁剪顺序：小程序壳 → 审核后台 → MCP → 协作可视化 → 润色 → 多意图，实际未触发降级。

### 2.5 建议图表

| 图表 | 内容 |
|---|---|
| 表 2-1 | 需求 → 实现模块 → 验收证据 三列映射 |
| 表 2-2 | P0/P1/P2 范围与完成状态 |

### 2.6 证据位置

- 优先级与裁剪：`README.md`、`docs/architecture.md`
- 工具清单：`app/tools/readonly.py`、`app/tools/write.py`
- 状态字段：`app/graph.py::GraphState`

## 3 系统设计（建议 1500~1800 字）

> 本章覆盖评分维度 ② 架构设计合理性（15 分）

### 3.1 分层架构

按六层写，每层给出职责与代码落点：

| 层 | 代码落点 | 职责 |
|---|---|---|
| 接入层 | `ui/streamlit_app.py`、`app/main.py`、`app/cli.py` | 顾客对话、商家审核、HTTP/CLI 调用 |
| 编排层 | `app/graph.py` | 9 节点 LangGraph、条件边、递归上限 |
| 能力层 | `app/tools/readonly.py`、`app/tools/write.py` | 6 只读 + 3 写工具 |
| 记忆层 | `app/memory/redis_client.py`、`sqlite_client.py` | 会话槽位、工单落库 |
| 质检层 | `app/llm.py`、`app/prompts/` | 结构化输出、Judge、polish |
| 观测层 | Langfuse v2 | Trace 与深链 |

### 3.2 LangGraph 状态机

必须写清节点与边，而不是只放一张图：

- 节点：`load_session`、`router`、`fetch_evidence`、`check_evidence`、`create_draft`、`polish`、`judge`、`knowledge_agent`、`handoff_human`。
- 条件边：
  - `load_session`：空 `order_id` → 转人工；否则 → `router`。
  - `router`：合规/未成年人/降级 → 转人工；知识意图 → `knowledge_agent`；其余 → `fetch_evidence`。
  - `fetch_evidence`：异常 → 转人工；成功 → `check_evidence`。
  - `judge`：通过 → 结束；失败且 `retry_count<2` → 回跳 `create_draft` 或 `fetch_evidence`；超限 → 转人工。
- 常量：`RECURSION_LIMIT = 32`，`MAX_JUDGE_RETRY = 2`。
- 设计取舍：主链采用确定性编排而非自由 ReAct，保证可测、可复现；ReAct 仅保留为扩展设计。

### 3.3 State 字段设计

用表格说明字段为什么存在，避免流水账：

| 字段 | 设计原因 |
|---|---|
| `task_outputs` | 多意图下每个子任务独立保存结果，避免互相覆盖 |
| `evidence_status` | 汇总所有理赔任务的缺失项，供补证清单与 Judge 使用 |
| `judge_feedback` | 把复核意见注入下一轮草稿，形成可追溯重跑 |
| `handoff` / `needs_human` | 双写，兼容图路由与 CLI/UI 读取 |
| `session_id` | 跨轮恢复订单与证据槽位 |

### 3.4 存储设计

- Redis：Key `agent:ctx:{session_id}`，Hash 保存 `summary / slots / retry_count / last_active`，TTL 默认 1800 秒并滑动续期；连接失败降级为进程内字典。
- SQLite：`tickets` 表字段 `draft_id / order_id / issue_type / summary / status / amount / created_at`；`draft_id` 主键保证幂等。
- 防重复审批：写入新草稿前执行 `UPDATE tickets SET status='superseded' WHERE order_id=? AND status='draft'`，只保留最新待审单。
- 转人工队列：`data/handoff_queue.jsonl`，记录 `ts / order_id / reason / status=pending_approval`。

### 3.5 安全设计

- 退款关键词守卫在 LLM 之前执行，命中即 `COMPLIANCE_REFUND`。
- 未成年人关键词 + 酒类关键词同时命中 → `MINOR_PROTECTION`。
- 无法映射理赔类型 → `UNSUPPORTED_ISSUE_TYPE`，不生成草稿。
- 超 7 天 → `handoff_human`，金额 0。

### 3.6 建议图表

| 图表 | 内容 |
|---|---|
| 图 3-1 | 六层架构图（引用 `docs/architecture.md` 第 1 节） |
| 图 3-2 | LangGraph 状态流转图（引用第 2 节） |
| 表 3-1 | 节点职责与出口条件 |
| 表 3-2 | State 字段与设计原因 |
| 表 3-3 | Redis / SQLite / 队列三类存储对比 |

## 4 核心实现（建议 2200~2600 字）

> 本章覆盖评分维度 ③ 工具开发质量（15 分）+ ④ Prompt 工程质量（10 分）

### 4.1 路由与多意图拆解

写作要点：

- `app/prompts/router_v1.txt` 定义 8 类意图；`intent=多意图` 时必须输出 `sub_tasks`。
- `app/llm.py::classify_intent` 调用 LLM 后做 Pydantic 校验，`MAX_REPAIR=1` 只修复一次。
- `app/graph.py::router` 把 `sub_tasks` 转成 `task_outputs`，初始 `status=pending`。
- `_ISSUE_MAP` 把中文子任务映射为工具层 `issue_type`；未知类型返回空串，交给后续转人工。
- 知识分流按 `intent` 判定，避免“漏了……怎么保存”被误分流到知识节点。

### 4.2 并发取证

必须写代码级细节：

- `fetch_evidence` 对理赔任务使用 `ThreadPoolExecutor`，`max_workers = max(1, min(len(claim_indexes), 4))`。
- 线程池只负责执行，结果先写入 `outcomes[idx]`，再按 `task_outputs` 原始顺序回写，保证完成顺序不影响输出顺序。
- 异常分两层：`NotFoundError / DataSourceError / ToolError` 保留原错误码；其他异常归为 `INTERNAL`。
- 单任务失败只标记该任务并整体转人工，不丢弃其他任务结果。

### 4.3 参数与槽位抽取

这一节要解释“为什么不用 LLM 抽取”：

- 关键词表：开箱视频、面单照片、破损部位照片、整箱称重照片。
- 否定防护：关键词前 6 个字符出现“没/未/无/没有/还没”则跳过。
- 疑问防护：关键词后 4 个字符出现“吗/？/?/怎么”则跳过。
- 去重保序：按标准顺序返回，避免用户叙述顺序影响补证清单。
- 示例：输入“还没有开箱视频，面单照片已上传”只能抽到“面单照片”。
- 取舍：固定补证话术下规则更稳定、零时延、可测试；复杂自由表述再评估 LLM 抽取。

### 4.4 长上下文与 Redis 槽位去重

- 第一轮只保存订单与已有证据；第二轮仅带 `session_id`，`load_session` 从 Redis 拉回。
- 合并规则：本轮显式输入优先，历史只补空缺，不覆盖用户新输入。
- 证据合并去重：新抽取证据逐项加入，已存在则跳过，保持标准顺序。
- TTL：默认 1800 秒，读写后滑动续期；Redis 故障时降级为进程内字典，主链不中断。
- 风险与边界：Redis 降级后不跨进程持久，演示环境建议启动 Redis。

### 4.5 赔付计算

- 破损漏液：`round(order_amount * 0.3, 2)`；若保价，则 `min(金额, insured_amount)`。
- 示例订单 `SO20261116001`：订单 1180 元，保价 1200 元，赔付 354.00 元，保价未触发封顶。
- 少件：`reship`，金额 0；证据不齐：`supplement_evidence`，金额 0；超 7 天或不支持类型：`handoff_human`，金额 0。
- 强调金额由工具计算，LLM 不参与算术。

### 4.6 Judge 质检与重试上限

- Judge Prompt 四条标准：不得承诺退款、金额一致、补证清单完整、事实不矛盾。
- 缺证例外：`complete=false` 且 `supplement_evidence` 时不核对金额，避免“金额为 0 被误驳回”。
- 输出契约：`pass / reason / failed_node`，`failed_node` 只允许 `create_draft` 或 `fetch_evidence`。
- 重试：`retry_count < MAX_JUDGE_RETRY(2)` 时按失败节点回跳；达到上限置 `JUDGE_REJECTED` 并转人工。
- 双保险：图递归上限 `RECURSION_LIMIT=32`，容纳首次审核 + 两次取证重跑 + polish，防止死循环。
- 失败策略：输出始终非法时 fail-closed 交人工；LLM 不可用时图节点降级放行并标记 `degraded=True`，避免离线环境整链瘫痪。

### 4.7 润色与事实锁定

- `polish_v1.txt` 只允许改语气，不允许改事实。
- 代码侧二次校验：金额集合、订单号集合、编号清单行、违禁承诺词。
- 任一不一致或 LLM 异常，直接回退原稿，不阻断主链。

### 4.8 双端界面与防重复审批

- 顾客端四个视图：对话、工单、证据与回复、审核与追踪。
- 商家端读取 SQLite 待审工单，支持通过/打回并写回状态。
- 防连点：同一订单新草稿写入前，旧 `draft` 置为 `superseded`，商家端只展示最新一条。

### 4.9 工程化

- Dockerfile 基于 `python:3.11-slim`，启动 Streamlit 8000 端口。
- Compose 中 Redis `healthcheck` 通过后 App 才启动；映射 `8501:8000`，挂载 `./data:/app/data`。
- 测试隔离：全局 Mock LLM、fakeredis、`TICKETS_DB_PATH` 与 `HANDOFF_QUEUE_PATH` 指向临时目录。

### 4.10 建议图表与代码片段

| 图表 | 内容 |
|---|---|
| 图 4-1 | 多意图并发取证时序图（router → 线程池 → 回写 → create_draft） |
| 图 4-2 | Judge 重跑流程图（pass / 回跳 / 超限） |
| 表 4-1 | 工具输入输出与错误码 |
| 表 4-2 | 证据要求与赔付规则 |
| 代码 4-1 | `extract_evidence_from_text` 否定/疑问防护片段 |
| 代码 4-2 | `ThreadPoolExecutor` 并发与顺序回写片段 |

## 5 测试与评测（建议 1400~1700 字）

> 本章覆盖评分维度 ⑤ 评测与可观测性（15 分）

### 5.1 测试体系

| 模块 | 条数 | 代表文件 |
|---|---|---|
| M1 基础 | 15 | `test_readonly_tools.py`、`test_router.py` |
| M2 单场景 | 40 | `test_e2e_m2.py`、`test_edge_cases.py` |
| M3 多意图 | 45 | `test_m3_parallel.py`、`test_m3_merge.py` |
| M4 记忆与审核 | 47 | `test_m4_judge.py`、`test_m4_retry.py` |
| M5 润色 | 9 | `test_m5_polish.py` |
| M6 评测 | 15 | `test_m6_evaluation.py` |
| M7 演示边界 | 4 | `test_edge_demo.py`（无关/超长/SQL注入/非法order_id） |
| M7 UI 展示层 | 20 | `test_edge_display.py`（14：SQLite 路径错/锁/不存在分流）+ `test_display_source.py`（6：工单合并视图） |
| 合计 | 195 | 23 个测试文件 |

说明：171 条为 M1~M6 功能测试，M7 新增 24 条边界与展示层测试（4+14+6），全量 195 条。

### 5.2 端到端评测

- 15 条用例覆盖破损、少件、错发、真伪、时效、合规、多意图、历史会话、异常、Judge 重试、酒知识。
- 结果 15/15，成功率 100%，无 P0 缺陷。
- 评测时钟固定为 2026-11-16 12:00，确保 7 天时效分支稳定；只固定测试时钟，不修改业务时间逻辑。

### 5.3 指标口径

| 指标 | 结果 | 说明 |
|---|---|---|
| 任务成功率 | 15/15 = 100% | 评测集判定 |
| 转人工率 | 7/15 = 46.7% | 评测集故意放大故障/合规场景，非生产采样 |
| 工具调用成功率 | 未采集 | 离线 Mock 无真实调用数据 |
| 平均重试次数 | 未采集 | 同上 |
| P95 延迟 | 未采集 | 同上 |
| Trace 覆盖率 | 未采集 | 同上 |

诚实说明：4 项未采集指标不虚标；U01/U06/U09/U14 为真实 Trace，其余 11 条为离线 Mock。

### 5.4 失败案例复盘（为什么 15/15 不是"碰巧跑通"）

> 这一节直接命中评分维度 ⑤"不是跑通就行"和 ⑦"如实记录踩坑"。

评测与开发过程中实际遇到并修复的失败案例：

| 阶段 | 失败表现 | 根因 | 修复 | 验证 |
|---|---|---|---|---|
| M2 早期 | 缺证场景金额=0 被 Judge 驳回 | Judge 规则没考虑缺证例外——`complete=false` 且 `supplement_evidence` 时不应核对金额 | 增加缺证例外：`complete=false && supplement_evidence` 时跳过金额校验 | `test_m4_judge.py` 缺证用例 + U02 评测通过 |
| M3 中期 | "漏了…怎么保存"被误分流到知识节点 | 路由按子任务是否含知识词判断，理赔文本带保存词时误判为纯知识 | 改为 `intent` 为主判定，仅非多意图且子任务含知识才走知识节点 | U09 多意图用例"三个子任务都保留" |
| M4 后期 | U14 Judge 连续 2 次失败后重试无上限 | `retry_count` 没在 Judge 节点递增，`MAX_JUDGE_RETRY=2` 形同虚设 | Judge 节点每次失败递增 `retry_count`，达到上限置 `JUDGE_REJECTED` 转人工 | U14 "重试 2 次后超限转人工"通过 |
| M7 评审 | 换目录跑 UI 提示"工单 TD-xxx 在库中不存在" | `_db_path()` 直接用默认相对路径，跟随 CWD 漂移 | `_db_path()` 加 `PROJECT_ROOT = Path(__file__).resolve().parents[2]` 锚定 | 23 个测试文件全局隔离不污染真实库 |

### 5.5 演示边界测试

- 无关文本：`UNSUPPORTED_ISSUE_TYPE`，不生成工单。
- 3000 字以上：正常返回 State，不崩溃。
- SQL 注入字符：作为普通文本处理，无 `INTERNAL`。
- 非法 `order_id`：`NOT_FOUND` 降级转人工，无 500。

### 5.5 建议图表

| 图表 | 内容 |
|---|---|
| 表 5-1 | 测试分布与代表用例 |
| 表 5-2 | 15 条评测逐条结果 |
| 表 5-3 | 6 项指标与采集口径 |
| 图 5-1 | pytest 195 passed 截图 |
| 图 5-2 | Langfuse 真实 Trace 截图 |

## 6 设计权衡与踩坑记录（建议 1700~2000 字）

> 本章覆盖评分维度 ⑦ 报告深度（10 分）

### 6.1 难点一：重试与死循环控制

- 问题：Judge 失败回跳、工具异常、LLM 修复都可能形成循环。
- 方案：`MAX_JUDGE_RETRY=2` 控制业务重试；`RECURSION_LIMIT=32` 控制图步数；节点内异常转 `handoff`；超限写 `JUDGE_REJECTED`。
- 效果：U13 首次拒绝后重试 1 次通过；U14 重试 2 次后转人工；重跑测试无 `RecursionError`。

### 6.2 难点二：参数抽取的稳定性

- 问题：顾客会说“还没有开箱视频”“开箱视频要拍吗”，直接关键词匹配会误判为已提供。
- 方案：前置 6 字符否定窗口、后置 4 字符疑问窗口、去重保序。
- 效果：四类证据可稳定抽取；否定/疑问不误判；跨轮合并不重复。

### 6.3 难点三：长上下文与跨轮状态

- 问题：多轮补证只带 `session_id`，不能要求用户重复订单号；历史证据不能覆盖本轮新输入。
- 方案：Redis Hash + 滑动 TTL；`load_session` 本轮优先、历史补缺；证据逐项去重。
- 效果：第二轮仅凭 `session_id` 恢复订单与三项证据并计算 354.00 元；Redis 故障仍可降级运行。

### 6.4 难点四：多意图结果互相覆盖

- 问题：串行实现中后一个子任务会覆盖前一个的 `tool_results`。
- 方案：`task_outputs` 每个子任务独立 `result`；线程池结果按索引回写；证据缺失项做并集。
- 效果：破损、少件、知识三类诉求同时保留；工单摘要与回复都能体现次要诉求。

### 6.5 难点五：防幻觉与越权承诺

- 问题：LLM 可能自行加价、承诺退款、删减补证清单。
- 方案：Judge 四条标准 + 缺证例外；polish 事实锁定；代码侧正则二次校验；合规守卫前移。
- 效果：金额与清单保持一致；越界润色回退原稿；退款诉求不进入 LLM。

### 6.6 难点六：重复审批

- 问题：同一订单反复提交会产生多条 `draft`，商家重复审核甚至重复赔付。
- 方案：写入前把旧 `draft` 置为 `superseded`，只保留最新待审单。
- 效果：商家端始终只有一条待审；历史草稿可追溯。

### 6.7 方案对比一：架构选型（确定性编排 vs 自由 ReAct vs Plan-and-Execute）

必须写成对比表，而不是一句"选了确定性编排"（对应 ⑦ 报告深度的"有对比"考点）：

| 维度 | 本方案：LangGraph 确定性编排 | 自由 ReAct 循环 | Plan-and-Execute |
|---|---|---|---|
| 可测性 | 9 节点边界清晰，pytest 逐节点验证，23 文件 195 条稳定通过 | 步数顺序不确定，难写稳定用例 | 计划可测但执行顺序有不确定性 |
| 可复现性 | 相同输入固定路径输出相同，Trace 完整回放 | 同一输入多次可能走不同路径 | 部分可复现 |
| 复杂度 | ~300 行核心代码 + 条件边 + 重试守卫 | 需设计行动选择 Prompt + 循环控制 + 上下限 | 需计划层 + 执行层双层 LLM 调用 |
| 适用场景 | 售后理赔（路径固定、规则明确） | 开放式探索（研究助理、代码审查） | 复杂多步骤任务（数据流水线） |
| 成本 | 单次 LLM 调用（router/judge/polish） | 多轮调用，成本高 | 双重调用，成本翻倍 |
| 缺点 | 不支持 Agent 自主发现新工具 | 循环上限控制难 + 成本高 | 延迟 + 计划层幻觉风险 |

**决策依据**：售后理赔路径固定、规则明确，实训交付优先保证可测与可复现。ReAct 循环保留为扩展设计（见 architecture §3），若后续接入开放式工具选择再启用。

### 6.8 方案对比二：Chroma 向量检索与关键词加权降级

必须写成对比表，而不是一句“环境不支持”：

| 维度 | Chroma 向量检索 | 当前关键词加权降级 |
|---|---|---|
| 语义泛化 | 强，支持同义表达 | 弱，依赖关键词覆盖 |
| 环境依赖 | 需要 C++ 编译环境与向量库 | 纯 Python，零额外依赖 |
| 可解释性 | 相似度分数，较难解释 | 知识优先级 + 字符得分，可逐条解释 |
| 稳定性 | 受模型与索引影响 | 固定规则，结果可复现 |
| 当前效果 | 未启用 | P-KNOW-001~003 命中稳定，U09/U15 通过 |
| 迁移成本 | Windows 环境阻塞 | 接口保留，后续可平滑切换 |

结论：在交付周期与 Windows 环境约束下，选择可运行、可测试、可解释的降级方案，并在报告中明确“未做语义泛化”，不把降级说成完整 RAG。

### 6.9 难点七：SQLite 路径漂移与锚定修复（M7 评审暴露）

> 这一节把一个"修了的 bug"提升为"工程规范的核心证据"，同时覆盖 ⑥ 工程规范和 ⑦ 报告深度两个评分维度。

- **问题**：在工作区根目录跑验证脚本和在项目目录跑 Streamlit UI，会产生两个不同的 `wine_after_sales.db` 文件——UI 说"工单 TD-xxx 在库中不存在"，其实工单在另一个库里。
- **排查过程**：先怀疑是 `CREATE TABLE IF NOT EXISTS` 重复初始化 → 排查后不是；然后 `print(os.getcwd())` 发现两个运行方式的 CWD 不同 → 根因找到了。
- **根因**：`_db_path()` 直接用 `os.getenv("SQLITE_PATH") or "data/wine_after_sales.db"`，相对路径拼 CWD。
- **修复**：三处同源锚定策略
  ```python
  # app/memory/sqlite_client.py::_db_path()
  PROJECT_ROOT = Path(__file__).resolve().parents[2]  # sqlite_client.py 在 app/memory/
  raw = os.getenv("TICKETS_DB_PATH") or os.getenv("SQLITE_PATH") or "data/wine_after_sales.db"
  path = Path(raw).expanduser()
  if not path.is_absolute():
      path = (PROJECT_ROOT / path).resolve()
  ```
- **规则**：env 提供绝对路径时原样放行（Docker Compose `TICKETS_DB_PATH=/app/data/...`）；相对路径强制拼接 PROJECT_ROOT，CWD 不影响。
- **验证**：23 个测试文件全部通过 env 注入绝对路径（`tmp_path`）实现隔离，不写入真实 `data/`，跑完即清理。
- **额外收益**：`ui/streamlit_app.py::_build_ticket_view` 增加三类异常提示（路径错 / 锁 / 工单不存在）+ 降级展示 Agent 快照，通过 `test_edge_display.py`（14 条）和 `test_display_source.py`（6 条）验证。

### 6.10 建议图表

| 图表 | 内容 |
|---|---|
| 表 6-1 | 六类难点、根因、方案、验证证据 |
| 表 6-2 | Chroma 与关键词加权对比 |
| 图 6-1 | Judge 重试与递归上限双层防护图 |

## 7 不足与展望（建议 700~900 字）

### 7.1 不足

1. 政策检索为关键词加权降级，未实现向量语义检索。
2. 四项运行指标未采集：工具调用成功率、平均重试次数、P95 延迟、Trace 覆盖率。
3. 15 条评测中 11 条为离线 Mock Trace，只有 4 条真实链路。
4. 物流、假酒专业子 Agent 仍为统一 `run()` 占位，未拆成独立图节点。
5. ReAct 开放式工具选择未启用，主链为确定性编排。
6. 工单审核为单机 SQLite，未接入真实工单系统与权限体系。
7. **持久化层静默吞错**：`save_ticket()` / `save_session()` / `write_queue()` 全部 `except Exception: pass`，不阻断主链但无日志，生产环境需补充监控。
8. **Redis 退化后不恢复**：一旦 `_call()` 切为 `_MemoryClient()` 就永久降级，不会尝试重连。

### 7.2 展望

1. Chroma 环境就绪后切换向量检索，并重新跑 15 条评测对比命中率。
2. 增加真实运行时埋点，补齐 4 项指标，建立线上回归基线。
3. 将 11 条 Mock 用例逐步迁移到真实链路，保留离线回归作为快速门禁。
4. 拆分专业子 Agent 节点，引入工具白名单与更细的权限边界。
5. 接入企业工单系统，补充登录、角色权限、审计日志与限流。

### 7.3 建议图表

| 图表 | 内容 |
|---|---|
| 表 7-1 | 不足 → 影响 → 改进计划 → 优先级 |

## 8 总结（建议 600~800 字）

### 8.1 必须写到的结果

- 完成 9 个工具、9 个图节点、双端工作台与 Compose 部署。
- 15/15 端到端评测通过；171 条 M1~M6 功能测试 + 24 条 M7 边界与展示层 = 195 条全绿。
- 4 条真实 Trace 归档；4 项未采集指标如实标注。
- 关键安全边界：退款、未成年人、超时效、非法输入全部转人工。

### 8.2 工程体会

1. 先闭环再增强：先保证一句话能出草稿，再补多意图、记忆、Judge。
2. 把不确定性关进笼子：规则守卫前置、Judge 后置、重试有上限、失败可降级。
3. 证据驱动交付：每个结论都对应测试、Trace 或指标，不用“应该可以”代替验证。

### 8.3 建议图表

| 图表 | 内容 |
|---|---|
| 图 8-1 | 项目最终能力全景图（工具、节点、双端、观测） |

## 附录 A：关键常量速查

| 常量 | 值 | 位置 |
|---|---|---|
| `RECURSION_LIMIT` | 32 | `app/graph.py` |
| `MAX_JUDGE_RETRY` | 2 | `app/graph.py` |
| `MAX_REPAIR` | 1 | `app/llm.py` |
| Redis TTL | 1800 秒 | `app/memory/redis_client.py` |
| 并发上限 | `min(任务数, 4)` | `app/graph.py::fetch_evidence` |
| 破损赔付比例 | 30% | `app/tools/readonly.py::calc_compensation` |
| 时效阈值 | 7 天 | 同上 |
| 示例赔付 | 354.00 元 | 1180 × 30%，保价 1200 |
| 全量测试 | 195 条 | 171 M1~M6 功能 + 24 M7 边界与展示层 |

## 附录 B：证据索引

| 内容 | 位置 |
|---|---|
| 架构与状态图 | `docs/architecture.md` |
| Prompt 设计与迭代 | `docs/prompt_design.md` |
| 10 分钟演示脚本 | `docs/demo_script.md` |
| 15 条评测与真实 Trace | `docs/traces/m6-evaluation-traces.md` |
| 指标采集报告 | `docs/progress/11-23-m6-metrics.md` |
| 冻结与 Compose 验证 | `docs/progress/11-23-m6-wrap-up.md` |
| 演示边界测试 | `tests/test_edge_demo.py` |

## 附录 C：建议字数分配

| 章节 | 建议字数 | 占比 |
|---|---|---|
| 1 项目背景 | 800 | 9% |
| 2 需求分析 | 1200 | 13% |
| 3 系统设计 | 1700 | 19% |
| 4 核心实现 | 2400 | 27% |
| 5 测试与评测 | 1500 | 17% |
| 6 难点与方案 | 1800 | 20% |
| 7 不足与展望 | 800 | 9% |
| 8 总结 | 700 | 8% |
| 合计 | 10900 | 100% |

写完后建议通读一遍：每个“实现了/提升了/解决了”后面，必须能指向一个文件、一条测试或一个数值。
