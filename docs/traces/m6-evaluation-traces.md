# M6 端到端评测 Trace 证据汇总

> 评测集离线执行，其中 U01/U06/U09/U14 已补充真实链路 Trace 演示链接。

## 1. 证据口径

M6 的 15 条端到端用例使用确定性路由/Judge mock 和隔离的 SQLite、Redis、转人工队列完成离线评测，最终状态断言均可复现。其中 U01/U06/U09/U14 四个核心场景已在具备真实 DeepSeek 与 Langfuse 凭据的环境下重新走真实 LLM 链路，捕获到可核验的 Langfuse 云端 Trace URL，填入下表；其余 11 条仍为离线 Mock 执行，不产生真实 Trace。

- 本地复现命令：`.venv\Scripts\python.exe -m pytest -q tests/test_m6_evaluation.py`
- 复现结果：`15 passed`，成功率 100%。
- 真实 Trace 采集脚本：`scripts/run_m6_real_traces.py`（无任何 mock，走真实 LLM + Langfuse）。
- Trace 主机：`https://jp.cloud.langfuse.com`（LANGFUSE_HOST）。

## 2. 关键场景证据

| 用例 | 关键链路 | 最终 State / 行为证据 | Langfuse Trace 链接 | 本地复现 |
|---|---|---|---|---|
| U01 | 破损漏液，证据齐全 | 证据完整；partial_refund；354.00 元；回复含人工审核；不转人工 | https://jp.cloud.langfuse.com/trace/d89f075a7cd8b15146e545d1d1cad1f1 | `pytest -q tests/test_m6_evaluation.py -k U01` |
| U02 | 破损漏液，无视频补证 | 证据不完整，missing=[开箱视频]；金额 0；回复要求补开箱视频；不转人工 | 离线 Mock 执行，不产生真实 Trace | `pytest -q tests/test_m6_evaluation.py -k U02` |
| U03 | 跨轮补证 | 第一轮 supplement_evidence；第二轮凭 session_id 恢复订单和证据；最终 354.00 元 | 离线 Mock 执行，不产生真实 Trace | `pytest -q tests/test_m6_evaluation.py -k U03` |
| U04 | 少件证据齐全补发 | issue_type=missing_item；recommendation=reship；金额 0；回复含补发；不转人工 | 离线 Mock 执行，不产生真实 Trace | `pytest -q tests/test_m6_evaluation.py -k U04` |
| U05 | 错发异常理赔 | 工具建议 handoff_human；最终转人工；AUTO_REFUND_REJECTED；保留人工参考草稿 | 离线 Mock 执行，不产生真实 Trace | `pytest -q tests/test_m6_evaluation.py -k U05` |
| U06 | 真伪争议 | UNSUPPORTED_ISSUE_TYPE；不自动生成工单；转人工 | https://jp.cloud.langfuse.com/trace/37cc8384b39138b5f24f503f4d2df90b | `pytest -q tests/test_m6_evaluation.py -k U06` |
| U07 | O-TIMEOUT 超时效 | 命中超 7 天时效；金额 0；转人工并保留草稿 | 离线 Mock 执行，不产生真实 Trace | `pytest -q tests/test_m6_evaluation.py -k U07` |
| U08 | 未成年人合规 | 真实 Router 命中 MINOR_PROTECTION；不生成草稿；转人工 | 离线 Mock 执行，不产生真实 Trace | `pytest -q tests/test_m6_evaluation.py -k U08` |
| U09 | 破损 + 少件 + 保存咨询 | 三个子任务均成功；合并补证清单；回复含酒知识解答 | https://jp.cloud.langfuse.com/trace/b292d15595fbd4033af9430c95feb771 | `pytest -q tests/test_m6_evaluation.py -k U09` |
| U10 | 历史会话恢复 | 第二轮仅带 session_id 恢复订单与三项证据；赔付 354.00 元；不转人工 | 离线 Mock 执行，不产生真实 Trace | `pytest -q tests/test_m6_evaluation.py -k U10` |
| U11 | 订单不存在 | error.code=NOT_FOUND；不生成草稿；转人工 | 离线 Mock 执行，不产生真实 Trace | `pytest -q tests/test_m6_evaluation.py -k U11` |
| U12 | 数据源异常 | error.code=DATA_SOURCE；不生成工单；转人工 | 离线 Mock 执行，不产生真实 Trace | `pytest -q tests/test_m6_evaluation.py -k U12` |
| U13 | Judge 失败后局部重跑 | Judge 调用 2 次；retry_count=1；复核意见进入工单；第二次通过 | 离线 Mock 执行，不产生真实 Trace | `pytest -q tests/test_m6_evaluation.py -k U13` |
| U14 | Judge 连续失败 | Judge 调用 3 次；retry_count=2；JUDGE_REJECTED；转人工 | https://jp.cloud.langfuse.com/trace/5f444fbfef9ee5250b61ade8c5e80440 | `pytest -q tests/test_m6_evaluation.py -k U14` |
| U15 | 酒知识咨询 | 命中避光平放等保存知识；不生成理赔草稿；不转人工 | 离线 Mock 执行，不产生真实 Trace | `pytest -q tests/test_m6_evaluation.py -k U15` |

## 3. Docker Compose 运行证据

Compose 实际构建启动时，Redis 健康检查通过后 App 启动，容器内输出：

```text
Container wine-after-sales-agent-redis-1 Healthy
Container wine-after-sales-agent-app-1 Started
app-1 | 159 passed, 1 warning in 8.50s
app-1 exited with code 0
```

该记录证明冻结后的代码及依赖可在 Compose 环境复现；U01/U06/U09/U14 的真实 Langfuse 云端链接见上表。
