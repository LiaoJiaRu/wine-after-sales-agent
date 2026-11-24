# 10 分钟答辩演示脚本

- 版本：V1.1（2026-11-24，新增上台时间线与清场三连，演示环境改为 Docker Compose）
- 适用场景：M8 现场演示与答辩
- 演示环境：Docker Compose 双容器（app + redis）+ Langfuse 云端 Trace；本地 Streamlit 仅作应急兜底
- 总时长：10 分钟（含开场与收尾）

## 1. 演示目标

用一条完整业务链路证明四件事：

1. 顾客一句话包含多个诉求时，系统能拆解、并发取证并合并结果。
2. 缺证据时先补证、不误赔；证据齐全时按政策给出 354.00 元赔付建议。
3. 工单只产草稿，必须经商家端人工审核，且重复提交不会产生多条待审单。
4. 全链路可在 Langfuse 回放，异常输入不会让系统崩溃。

## 2. 上台时间线与演示前检查

### 2.1 上台时间线（彩排 → 现场）

原则：镜像只在彩排时构建一次；上台当天只启动、只确认、最后清场，全程不关机。命令均在 PyCharm 终端（PowerShell）执行，先进入项目目录 `cd d:\projects\Wine-After-Sales-Agent\wine-after-sales-agent`。

| 阶段 | 动作 | 预期 |
|---|---|---|
| 彩排 ①（演示前 1~2 天） | `docker compose up -d --build` | 唯一一次构建并启动，镜像留存本地 |
| 彩排 ② 验证 | `docker compose ps`；`docker compose exec app python -m pytest -q` | 两服务 healthy；容器内 195 passed |
| 彩排 ③ 全链路 | 按 §4~§9 完整跑一遍两幕 + 审核 + Trace 回放，并录制备用录屏 | Langfuse 生成当天 Trace |
| 上台当天早上 | Docker Desktop 自启并恢复容器（勾选 Start when you log in，笔记本不休眠） | `docker compose ps` 两服务 healthy，全程不关 |
| 上台前 10 分钟 | 执行 §2.2 清场三连（所有验证之后的最后一步） | 工单库、会话、队列恢复干净 |
| 上台时 | 不跑任何命令；展示工程规范时敲 `docker compose ps` | 零启动等待，直接开讲 |

注意：清场之后不再跑 `pytest` 与试演，避免重新写入数据；`--build` 依赖网络，只出现在彩排阶段，上台当天一律 `docker compose up -d`（不带 `--build`）。

### 2.2 清场三连（上台前 10 分钟）

```powershell
# PyCharm 终端（PowerShell），先进入项目目录
cd d:\projects\Wine-After-Sales-Agent\wine-after-sales-agent

# 1) 清空 SQLite 工单表（data/ 为 bind mount，宿主机清理对容器实时生效）
python -c "import sqlite3; conn=sqlite3.connect('data/wine_after_sales.db'); conn.execute('DELETE FROM tickets'); conn.commit(); print('DB cleared')"

# 2) 清空 Redis 会话（交互式终端保留 -it；写成一键脚本时去掉 -it）
docker exec -it wine-after-sales-agent-redis-1 redis-cli FLUSHALL

# 3) 转人工队列二选一：删除求干净；推荐保留，作为转人工机制真实证据
Remove-Item data\handoff_queue.jsonl -ErrorAction SilentlyContinue
```

### 2.3 演示前检查（T-10 分钟）

| 检查项 | 操作 | 预期 |
|---|---|---|
| 容器状态 | `docker compose ps` | app 与 redis 均 healthy |
| 工作台 | 浏览器打开 `http://localhost:8501` | 页面可访问（容器映射 8501:8000） |
| 环境变量 | 确认 `.env` 已填 DeepSeek 与 Langfuse 三个 Key | 无 `MISSING_API_KEY` |
| Trace 页面 | 提前登录 Langfuse，打开 U01 Trace 备用标签页 | 页面已加载，避免现场登录 |
| 备用录屏 | 打开彩排时录制的播放器 | 可随时切换 |
| 数据状态 | `data/mock_*.json` 原样保留，运行库已按 §2.2 清场 | 演示从干净工单库开始 |

演示前把浏览器标签固定为：工作台、Langfuse Trace、备用录屏。

## 3. 时间分配

| 时间 | 环节 | 时长 |
|---|---|---|
| 0:00 - 0:40 | 开场与架构一句话 | 40 秒 |
| 0:40 - 3:10 | 第一幕：缺证据与补证清单 | 2 分 30 秒 |
| 3:10 - 6:40 | 第二幕：补齐证据与 354.00 元赔付 | 3 分 30 秒 |
| 6:40 - 8:10 | 商家端审核与防重复审批 | 1 分 30 秒 |
| 8:10 - 9:10 | Trace 回放与评测结论 | 1 分钟 |
| 9:10 - 10:00 | 收尾与过渡到提问 | 50 秒 |

## 4. 开场（0:00 - 0:40）

讲解词：

> 这是一个葡萄酒售后理赔客服 Agent。顾客只需要说一句话，系统会完成意图拆解、订单与物流取证、证据校验、赔付建议、回复草稿和工单草稿；所有资金动作都交人工审核，系统只出草稿。
>
> 主链是 LangGraph 状态机：会话加载、路由、并发取证、证据校验、草稿生成、润色、Judge 质检、转人工。下面用两幕演示完整闭环。

动作：展示工作台首页，左侧身份选择“顾客端”。

## 5. 第一幕：缺证据提示补证（0:40 - 3:10）

### 5.1 设置会话（0:40 - 1:00）

- `order_id`：`SO20261116001`
- `session_id`：`demo-001`
- 说明：订单为 11-21 签收，处于 7 天时效内；保价 1200 元，订单金额 1180 元。

### 5.2 顾客输入（1:00 - 1:30）

输入原文：

```text
我买的酒漏了，还少一瓶，另外这酒怎么保存？
```

预期后台行为：

- `intent=多意图`，拆出 `破损漏液`、`少件错发`、`储存饮用咨询` 三个子任务。
- 三个子任务并发取证，结果按原顺序回写。
- 证据未上传，`evidence_status.complete=false`。
- 缺失项合并为：开箱视频、面单照片、破损部位照片、整箱称重照片。
- 赔付建议 `supplement_evidence`，金额 0，不转人工。

### 5.3 后台展示（1:30 - 2:20）

切到“审核与追踪”页，指出三个字段：

| 展示点 | 预期值 | 讲解重点 |
|---|---|---|
| 子任务 | 3 个，均为 success | 多意图不是串行硬编码，而是任务对象 + 线程池 |
| `evidence_status` | `complete=false`，`missing` 四项 | 缺证先补证，不进入赔付 |
| `compensation` | `supplement_evidence`，`amount=0` | 缺证不误赔 |

讲解词：

> 这里能看出系统没有直接把“漏了”当成可赔付，而是先要求补证；同时少件诉求也保留了，不会只处理主诉。

### 5.4 顾客端回复（2:20 - 2:50）

查看“证据与回复”页，指出：

- 【需要您补充的材料】逐项列出四项缺失证据。
- 【处理进度】说明材料补充中。
- 末尾如有酒知识诉求，追加【酒知识解答】。

### 5.5 商家端草稿（2:50 - 3:10）

切到“商家审核端”，指出：

- 已生成一条 `draft` 工单，`need_human_review=true`。
- 工单摘要包含破损、少件与证据缺失信息。
- 此时还没有赔付金额。

## 6. 第二幕：补齐证据与 354.00 元赔付（3:10 - 6:40）

### 6.1 补齐证据（3:10 - 3:40）

保持同一 `session_id=demo-001`，在证据上传模拟中勾选：

```text
开箱视频、面单照片、破损部位照片、整箱称重照片
```

### 6.2 第二轮输入（3:40 - 4:10）

输入原文：

```text
我已经上传了开箱视频、面单照片、破损部位照片和整箱称重照片
```

预期后台行为：

- `load_session` 从 Redis 恢复订单与第一轮证据。
- 槽位抽取识别四类证据，与历史去重合并。
- `evidence_status.complete=true`。
- 破损漏液：`partial_refund`，金额 1180 × 30% = **354.00 元**；保价 1200 元未触发封顶。
- 少件：建议补发，金额 0。
- Judge 质检通过，polish 只改表达，不改金额与承诺。

### 6.3 后台展示（4:10 - 5:20）

切到“审核与追踪”页，逐项指出：

| 展示点 | 预期值 | 讲解重点 |
|---|---|---|
| `order_id` | `SO20261116001` | 第二轮只带 session_id 也能恢复 |
| `evidence_status.complete` | `true` | 补证后进入赔付评估 |
| `compensation.recommendation` | `partial_refund` | 政策计算，非模型编造 |
| `compensation.amount` | `354.0` | 1180 × 30%，保价未截断 |
| `judge_result.pass` | `true` | 金额、清单、承诺均通过质检 |

### 6.4 顾客端回复（5:20 - 6:10）

指出回复中的四个要点：

1. 明确赔付金额 354.00 元。
2. 写明“最终结果以人工审核为准”。
3. 少件部分说明“审核确认后安排补发”。
4. 末尾保留【酒知识解答】与【保存建议】。

### 6.5 商家端新草稿（6:10 - 6:40）

回到商家审核端，指出：

- 同一订单出现新的待审草稿，旧草稿状态变为 `superseded`。
- 商家端只展示最新一条待审单，避免重复审核和重复赔付。

## 7. 商家端审核（6:40 - 8:10）

### 7.1 打开工单卡片（6:40 - 7:00）

展示工单字段：订单号、问题类型、证据状态、赔付建议、政策条款、创建时间。

### 7.2 执行审核（7:00 - 7:50）

点击“通过”：

- 工单状态从 `draft` 更新为已通过，写入 SQLite。
- 说明系统不会自动退款，审核结果只代表人工结论。

补充说明“打回”路径：

- 打回后工单保留在库中，顾客可补充材料再次提交。
- 新提交会生成新草稿，旧草稿自动 `superseded`。

### 7.3 防重复审批（7:50 - 8:10）

讲解词：

> 同一订单反复提交不会产生多条待审单。写新草稿前会把旧 draft 置为 superseded，商家端只看到最新一条，历史仍可追溯。

## 8. Trace 回放与评测结论（8:10 - 9:10）

### 8.1 Trace 回放（8:10 - 8:45）

切换到提前打开的 Langfuse 页面，使用已归档的 U01 真实 Trace：

```text
https://jp.cloud.langfuse.com/trace/bafdc0f1f96511b84cf6b55d5f48d35f
```

指出三层结构：路由意图、工具取证与赔付计算、Judge 质检。说明这是真实 DeepSeek + Langfuse 链路，不是截图拼接。

### 8.2 评测结论（8:45 - 9:10）

| 指标 | 数值 | 口径 |
|---|---|---|
| 端到端用例 | 15/15，100% | 离线评测集，U01~U15 |
| 全量测试 | 195 passed | 171 条 M1~M6 功能 + 24 条 M7 边界与展示层 |
| 真实 Trace | 4 条 | U01/bafdc0f1、U06/37cc8384、U09/b292d155、U14/5f444fbf（U01 URL 已更新为正确值） |
| 其余用例 | 11 条 | 离线 Mock，不产生真实 Trace |

如实说明：评测使用 Mock 订单数据与确定性路由/Judge，不代表生产流量；离线未采集的指标不虚标。

## 9. 收尾（9:10 - 10:00）

用三句话收束：

1. 多意图：任务对象 + 线程池并发，结果顺序稳定、异常隔离。
2. 可管控：Redis 记忆、SQLite 落库、Judge 质检、局部重跑上限 2 次。
3. 有边界：退款、未成年人、超时效、非法输入全部转人工，系统只出草稿。

最后一页展示边界测试结果：

| 输入 | 结果 |
|---|---|
| 无关文本“今天天气很好” | `UNSUPPORTED_ISSUE_TYPE`，转人工，不生成工单 |
| 3000 字以上超长文本 | 正常返回 State，不崩溃 |
| SQL 注入字符 | 作为普通文本处理，不产生 `INTERNAL` 错误 |
| 非法 `order_id`（含 `<>`） | `NOT_FOUND` 降级转人工，无 500 |

## 10. 演示边界与应急预案

注意事项：若网络波动，请立即切换备用录屏。

| 异常 | 现场动作 | 说明 |
|---|---|---|
| LLM 超时或限流 | 立即切备用录屏 | 录屏包含完整两幕与审核流程 |
| Redis 未启动 | 直接继续 | 会话自动降级为进程内存储，功能可用 |
| 容器未启动 | `docker compose up -d`（不带 `--build`） | 镜像已彩排预构建，秒级启动 |
| Docker 异常 | 改用本地 Streamlit | 两套启动方式等价 |
| Streamlit 崩溃 | 切 CLI 演示 | `python -m app.cli --graph --order SO20261116001 --session demo-001 "..."` |
| Langfuse 打不开 | 展示归档 URL 与截图 | 已提前登录并缓存页面 |
| 现场只能离线 | 明确说明评测为 Mock 口径 | 不把离线结果说成真实线上流量 |

演示期间不做的事：

- 不现场修改代码、不现场安装依赖、不现场清理数据库。
- 不承诺自动退款或线上赔付时效。
- 不打开未验证的第三方页面。

## 11. 常见提问速答

| 问题 | 回答要点 |
|---|---|
| 为什么不用向量 RAG？ | Windows 缺 C++ 编译环境，Chroma 暂缓；当前用知识关键词加权 + 字符重合度降级，接口保留 |
| 354.00 元怎么来的？ | 订单 1180 × 30% = 354，保价 1200 未触发封顶；金额由工具计算，模型不得改写 |
| 怎么防止模型乱承诺？ | Judge 四条标准 + polish 事实锁定 + 代码侧金额/订单号/清单二次校验，越界回退原稿 |
| 怎么防止死循环？ | Judge 重跑上限 2 次，图递归上限 32，超限转人工 |
| 重复提交怎么办？ | 旧草稿置为 `superseded`，商家端只保留最新待审单 |
| 为什么还有 11 条 Mock Trace？ | 离线评测不产生真实调用；4 条核心场景已走真实链路并归档，其余如实标注 |

## 12. 一页口令卡（PyCharm 终端，先 cd 到项目目录）

```powershell
# ===== 上台当天：只启动，不构建（镜像已在彩排时 --build）=====
docker compose up -d
docker compose ps          # app 与 redis 均 healthy；工作台 http://localhost:8501

# ===== 上台前 10 分钟：清场三连（之后不再跑测试与试演）=====
python -c "import sqlite3; conn=sqlite3.connect('data/wine_after_sales.db'); conn.execute('DELETE FROM tickets'); conn.commit(); print('DB cleared')"
docker exec -it wine-after-sales-agent-redis-1 redis-cli FLUSHALL
Remove-Item data\handoff_queue.jsonl -ErrorAction SilentlyContinue   # 或保留作转人工证据

# ===== 彩排：唯一一次构建 + 容器内全量测试 =====
docker compose up -d --build
docker compose exec app python -m pytest -q    # 195 passed

# ===== 兜底：Docker 异常时本地启动 / CLI 跑完整图 =====
streamlit run ui/streamlit_app.py --server.port=8000
python -m app.cli --graph --order SO20261116001 --session demo-001 "我买的酒漏了，还少一瓶，另外这酒怎么保存？"
python -m app.cli --graph --order SO20261116001 --session demo-001 "我已经上传了开箱视频、面单照片、破损部位照片和整箱称重照片"
pytest -q   # 仅本地兜底环境使用（清场之前）
```

演示参数：`order_id=SO20261116001`，`session_id=demo-001`，期望赔付 `354.00` 元。

## 13. 评分维度实时锚点（答辩时自然融入，不生硬）

| 演示节点 | 对应评分维度 | 可以说什么（自然融入） |
|---|---|---|
| 输入后出现 3 个 `task_outputs` | ① Agent 真实性 | "它不是简单问答——它把一句话拆成了破损漏液、少件错发、储存饮用咨询三个子任务，各自并发取证" |
| 商家端旧草稿自动 `superseded` | ③ 工具质量 | "写入前自动 superseded 旧草稿，SQLite 事务保证，不会重复审批" |
| Langfuse Trace 三层结构 | ⑤ 评测 | "这是真实 DeepSeek + Langfuse 链路，路由意图层、工具取证层、Judge 质检层都能独立回放" |
| `pytest 195 passed` | ⑤ + ⑥ | "171 功能 + 24 M7 边界与展示层，23 个文件，全局 fakeredis + tmp_path 隔离，不写真实数据" |
| 换目录跑 UI 还能读同一个库 | ⑥ + ⑦ | "M7 修了路径漂移，_db_path() 锚定项目根，这是工程规范的核心证据" |
| 被问"为什么选确定性编排不选 ReAct" | ② + ⑦ | "场景路径固定、规则明确，我选了确定性编排——可测可复现，对比表在 architecture §3" |
