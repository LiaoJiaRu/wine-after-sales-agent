# M6 端到端评测 Trace 证据汇总

## 1. 证据口径

M6 使用确定性路由/Judge mock 和隔离的 SQLite、Redis、转人工队列完成离线评测，最终状态断言均可复现。本轮未产生可核验的 Langfuse 云端 Trace URL，因此下表链接栏保留为预留位，不将 pytest State 证据冒充 Langfuse 链接。

- 本地复现命令：`.venv\Scripts\python.exe -m pytest -q tests/test_m6_evaluation.py`
- 复现结果：`15 passed`，成功率 100%。
- 全量回归：`.venv\Scripts\python.exe -m pytest -q`，结果为 `159 passed`。
- 云端链接补录方式：在配置有效 Langfuse 环境变量后运行对应 CLI/图调用，将输出的真实 Trace URL 填入下表预留位。

## 2. 关键场景证据

| 用例 | 关键链路 | 最终 State / 行为证据 | Langfuse Trace 链接 | 本地复现 |
|---|---|---|---|---|
| U01 | 破损漏液，证据齐全 | 证据完整；`partial_refund`；354.00 元；回复含人工审核；不转人工 | 预留：待真实运行后补录 | `pytest -q tests/test_m6_evaluation.py -k U01` |
| U03 | 跨轮补证 | 第一轮 `supplement_evidence`；第二轮凭 session_id 恢复订单和证据；最终 354.00 元 | 预留：待真实运行后补录 | `pytest -q tests/test_m6_evaluation.py -k U03` |
| U05 | 错发异常理赔 | 工具建议 `handoff_human`；最终转人工；`AUTO_REFUND_REJECTED`；保留人工参考草稿 | 预留：待真实运行后补录 | `pytest -q tests/test_m6_evaluation.py -k U05` |
| U06 | 真伪争议 | `UNSUPPORTED_ISSUE_TYPE`；不自动生成工单；转人工 | 预留：待真实运行后补录 | `pytest -q tests/test_m6_evaluation.py -k U06` |
| U07 | O-TIMEOUT 超时效 | 物流记录正常加载；命中“超 7 天时效”；金额 0；转人工并保留草稿 | 预留：待真实运行后补录 | `pytest -q tests/test_m6_evaluation.py -k U07` |
| U08 | 未成年人合规 | 真实 Router 命中 `MINOR_PROTECTION`；不生成草稿；转人工 | 预留：合规前置拦截无 LLM Span，可补录图运行记录 | `pytest -q tests/test_m6_evaluation.py -k U08` |
| U09 | 破损 + 少件 + 保存咨询 | 三个子任务均成功；合并补证清单；回复含酒知识解答 | 预留：待真实运行后补录 | `pytest -q tests/test_m6_evaluation.py -k U09` |
| U13 | Judge 失败后局部重跑 | Judge 调用 2 次；`retry_count=1`；复核意见进入工单；第二次通过 | 预留：待真实运行后补录 | `pytest -q tests/test_m6_evaluation.py -k U13` |
| U14 | Judge 连续失败 | Judge 调用 3 次；`retry_count=2`；`JUDGE_REJECTED`；转人工 | 预留：待真实运行后补录 | `pytest -q tests/test_m6_evaluation.py -k U14` |
| U15 | 酒知识咨询 | 命中“避光平放”等保存知识；不生成理赔草稿；不转人工 | 预留：待真实运行后补录 | `pytest -q tests/test_m6_evaluation.py -k U15` |

## 3. Docker Compose 运行证据

Compose 实际构建启动时，Redis 健康检查通过后 App 启动，容器内输出：

```text
Container wine-after-sales-agent-redis-1 Healthy
Container wine-after-sales-agent-app-1 Started
app-1 | 159 passed, 1 warning in 8.50s
app-1 exited with code 0
```

该记录证明冻结后的代码及依赖可在 Compose 环境复现；Langfuse 云端链接仍需在具备真实凭据的环境补录。
