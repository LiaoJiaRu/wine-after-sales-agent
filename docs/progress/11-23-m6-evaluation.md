# M6 阶段1：15 条端到端评测报告

## 1. 结论

- 评测日期：2026-11-23
- 评测文件：`tests/test_m6_evaluation.py`
- 执行命令：`.venv\Scripts\python.exe -m pytest -q tests/test_m6_evaluation.py`
- 实测结果：**12 passed，3 failed**
- 成功率：**12 / 15 × 100% = 80.0%**
- 验收线：≥ 80%，**达到下限**
- 失败用例：**U05、U07、U08**
- 限制遵守：未修改 `app/` 下任何业务代码，未修改既有测试或 Mock 数据文件；失败断言未放宽，也未使用 `xfail`。

pytest 因 3 条业务断言失败返回退出码 1，输出摘要为：

```text
....F.FF.......                                                          [100%]
3 failed, 12 passed, 1 warning in 17.93s
```

## 2. 评测口径

1. 15 条用例由同一个 `pytest.mark.parametrize` 入口执行，参数 ID 严格为 U01-U15。
2. 每条用例均调用 `compile_graph().invoke()` 跑真实 LangGraph 主链，并断言最终 State 的 `handoff`、`needs_human`，同时按场景断言金额、错误码、证据状态或回复关键内容。
3. 意图路由使用确定性的 `IntentDecision` mock，避免外部 LLM 波动；订单、批次、政策、证据、赔付、草稿、SQLite、Redis 会话等仍走现有实现。
4. U13/U14 通过 `monkeypatch` 替换 `app.graph.judge_draft`：U13 首次拒绝、第二次通过；U14 持续拒绝，验证首次加两次重试后转人工。
5. 评测时钟固定为 **2026-11-16 12:00:00+08:00**。`SO20261116001` 签收于 2026-11-15，因此正常证据齐全场景处于 7 天时效内，赔付期望为 1180 × 30% = 354.00 元。此处理只固定测试时钟，不修改业务时间逻辑。
6. `O-TIMEOUT` 已存在于 `mock_orders.json`，但 `mock_logistics.json` 没有对应物流记录。若直接执行，主链会以物流 `NOT_FOUND` 提前结束，无法验证超时效分支。U07 因此在测试内通过 `monkeypatch` 为该订单补齐物流返回，实测已进入 `handoff_human` 赔付建议及“超 7 天时效”原因分支；未修改 Mock 数据文件。
7. U08 路由按现有 Router Prompt 判为“储存饮用咨询”，未伪造 `needs_human=True` 来掩盖主链缺失的未成年人拦截。

## 3. 逐条结果

| 用例 | 场景 | 关键验收点 | 实测结果 |
|---|---|---|---|
| U01 | 破损漏液，有视频及完整证据 | 证据齐全；赔付 354.00 元；回复包含金额与人工审核；不转人工 | 通过 |
| U02 | 破损漏液，无开箱视频 | 缺失项为开箱视频；金额 0；回复要求补证；不转人工 | 通过 |
| U03 | 破损漏液，跨轮补证 | 第一轮补证；第二轮仅凭 session_id 恢复订单并补齐证据；最终 354.00 元 | 通过 |
| U04 | 少件 | 证据齐全；建议 `reship`；金额 0；回复包含补发 | 通过 |
| U05 | 错发 | 工具建议 `handoff_human`、金额 0，最终 State 应转人工 | **失败：最终 `handoff=False`** |
| U06 | 真伪争议 | `UNSUPPORTED_ISSUE_TYPE`；不生成自动工单；转人工 | 通过 |
| U07 | O-TIMEOUT 超 7 天 | 已进入超时效分支；建议 `handoff_human`、金额 0，最终 State 应转人工 | **失败：最终 `handoff=False`** |
| U08 | 未成年人购酒及饮用咨询 | 应立即拦截并转人工 | **失败：最终 `handoff=False`，未进入人工处理** |
| U09 | 破损 + 少件 + 保存咨询 | 三个子任务均成功；回复同时包含整箱称重照片与酒知识解答 | 通过 |
| U10 | 历史会话恢复 | 第二轮仅带 session_id；恢复订单和三项证据；赔付 354.00 元 | 通过 |
| U11 | 订单不存在 | `NOT_FOUND`；不生成草稿；转人工 | 通过 |
| U12 | 数据源异常 | `DATA_SOURCE`；不生成工单；转人工 | 通过 |
| U13 | Judge 首次拒绝后重跑 | Judge 调用 2 次；`retry_count=1`；工单含复核意见；最终通过且不转人工 | 通过 |
| U14 | Judge 连续拒绝 | Judge 调用 3 次；`retry_count=2`；`JUDGE_REJECTED`；转人工 | 通过 |
| U15 | 酒知识咨询 | 命中保存知识，回复知识包含“避光平放”；不生成理赔草稿；不转人工 | 通过 |

## 4. 失败归因表

| 用例 | 现象 | 已确认事实 | 根因定位 | 归因类别 |
|---|---|---|---|---|
| U05 | 错发证据齐全，工具已建议人工处理，但最终 State 未转人工 | `tool_results.compensation.recommendation == "handoff_human"`，金额为 0，回复也包含“售后专员”；最终 `handoff=False`、`needs_human=False` | `app/graph.py::create_draft` 成功生成草稿后固定返回 `handoff=False`、`needs_human=False`；图没有根据 compensation 的 `handoff_human` 建议转入 `handoff_human` 节点 | 冻结业务实现与异常理赔转人工要求不一致 |
| U07 | O-TIMEOUT 已判定超 7 天，但最终 State 未转人工 | 已进入时效计算，建议为 `handoff_human`，原因含“超 7 天时效”，金额为 0；最终 `handoff=False` | 与 U05 相同：`create_draft` 覆盖人工处理语义，缺少按赔付建议转人工的条件边 | 冻结业务实现与超时效转人工要求不一致 |
| U08 | 未成年人咨询未拦截 | 按现有路由契约进入“储存饮用咨询”；最终 `handoff=False`，没有人工错误信息 | 主路由合规守卫仅覆盖退款关键词；知识路由直接执行 `knowledge_agent`。虽然 `P-KNOW-003` 规定未成年人咨询立即拦截并转人工，但主链没有对应的确定性合规判断和转人工路由 | 冻结业务实现缺少未成年人合规拦截 |

## 5. 边界与后续建议

本次 M6 只负责暴露问题，不修改冻结业务代码。后续若进入缺陷修复阶段，应分别处理：

1. 将 `compensation.recommendation == "handoff_human"` 映射为真实图级转人工，而不是仅生成“转交专员”的回复文本。
2. 在意图路由前增加确定性的未成年人合规守卫，命中购买或饮用诉求时直接转人工，不能依赖知识检索是否恰好返回 P-KNOW-003。
3. 若希望 U07 脱离测试内补丁独立运行，应在后续获准修改 Mock 数据时补充 O-TIMEOUT 对应物流记录。

以上建议不构成对本阶段冻结代码的改动。
