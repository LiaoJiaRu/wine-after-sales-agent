# 11-18 当日总结（草稿）：M2 破损漏液单场景闭环收尾

## 一、今日目标回顾

打通"破损漏液"单场景端到端闭环：router 子任务 → 只读取证 → 证据核验 → 赔付建议 → 工单/回复草稿，
并保证任何异常路径都不崩溃、可转人工。

## 二、LangGraph 成功接入

- 新增 `app/graph.py`：`GraphState`（TypedDict，覆盖作业1-1 表4 全部字段：intent / sub_tasks /
  tool_results / evidence_status / judge_result / retry_count / handoff，另含 order_id / issue_type /
  order / shipment / batch / evidence / ticket_draft / reply_draft / error / needs_human）。
- 图结构：`START → load_session → router → fetch_evidence → check_evidence → create_draft → END`，
  每个分叉节点均可经条件边进入 `handoff_human → END`。
- 防崩溃两层机制：
  1. 节点内 try/except（NotFoundError / DataSourceError / ToolError / Exception）→ 写 error +
     handoff=True，条件边自动转人工；
  2. CLI 外层 `run_graph()` 大 try/except 兜底 → 任何 LangGraph 运行时异常返回
     `{"error": ..., "needs_human": true}`，主程序永不崩溃。
- 踩坑记录（LangGraph 0.2）：同一 step 同 key 不能多次写（全部改用条件边）；TypedDict 未声明的
  invoke 输入 key 会被静默丢弃（user_text / evidence 必须显式声明）；节点未写过的字段不出现在
  invoke 结果里（load_session 显式补 retry_count / judge_result）。

## 三、新增 2 个草稿写工具（`app/tools/write.py`）

- `create_ticket_draft`：装配订单/证据/赔付事实为工单草稿，`need_human_review` 恒为 True；
  只生成草稿，不落库、不退款、不改订单状态。
- `draft_reply`：面向消费者的温和话术，按事实输出补证清单 / 赔付金额与人工审核提示 / 储存建议。
- 配套 Schema 追加至 `app/tools/schemas.py`，8 条工具单测全绿。

## 四、CLI 与演示脚本

- `app/cli.py`：新增 `--graph` / `--order` 参数走完整图流程；不带 `--graph` 保持原 router 行为。
- `app/run_m2.py`：两幕演示脚本。
  - 第一幕（无证据）：输出补证清单（开箱视频/面单照片/破损部位照片）。
  - 第二幕（`--with-evidence`）：evidence complete=True，赔付建议 partial_refund 354.00 元
    （订单 1180 × 30%，保价 1200 未截断），回复告知金额并提示"最终结果以人工审核为准"。

## 五、合规与边界防御（今日收尾新增）

- `graph.router` 节点新增合规守卫：命中"直接退款/直接退钱/立即退款/立刻退款/马上退款/马上退钱"
  关键词时先于 LLM 拦截，`error.code=COMPLIANCE_REFUND` 直接转人工，不进入自动流程。
- 新增 `tests/test_edge_cases.py`（12 条）：
  - 订单不存在 ×2：handoff=True + NOT_FOUND + retryable=False，无草稿；
  - 直接退款合规拦截 ×4（参数化）：COMPLIANCE_REFUND，且不调用 LLM（monkeypatch 验证）、不碰只读工具；
    正常破损文本不误伤；
  - 证据缺失 ×2：缺开箱视频/完全无证据 → 按设计走补证路径（不 handoff），
    工单 need_human_review=True + 回复含补证清单；
  - DataSourceError：monkeypatch get_order 抛错 → handoff=True + error.code=DATA_SOURCE；
  - 空用户输入：router 降级 EMPTY_INPUT → handoff。

## 六、E2E 测试结果

| 测试文件 | 条数 | 覆盖 |
| --- | --- | --- |
| tests/test_readonly_tools.py | 9 | 6 只读工具 |
| tests/test_router.py | 6 | 路由 + 降级 |
| tests/test_draft_tools.py | 8 | 2 草稿写工具 |
| tests/test_graph_basics.py | 5 | 图骨架 + handoff 条件边 |
| tests/test_e2e_m2.py | 13 | 两幕 E2E + CLI --graph + run_graph 兜底 |
| tests/test_edge_cases.py | 12 | 边界与合规防御 |
| **合计** | **53** | **pytest -q 全绿** |

## 七、明日待办

- Docker 最终构建复跑（`docker build -t wine-agent-m2 . && docker run --rm wine-agent-m2`）并 Commit。
- M3 规划：多意图并行、Judge 节点、ReAct 证据补齐回路。

补充更新：D12 偏差已消除，app/graph.py 已创建并完成单场景闭环；新增的 app/tools/write.py 中的草稿写工具无越权操作，符合边界约束①。