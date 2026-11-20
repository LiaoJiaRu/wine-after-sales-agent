# 11-19 当日总结（M3）：酒知识子 Agent、多意图草稿合并与合规优先级锁定

## 一、今日目标回顾

在 M2 单场景闭环之上补齐 M3 核心能力：

1. 强化政策检索，让酒知识类咨询优先命中 `P-KNOW` 系列；
2. 新增酒知识专业子 Agent 节点 `knowledge_agent`；
3. 多意图（理赔 + 知识）时把知识结果合并进回复草稿；
4. 收尾时锁定“合规拦截优先于知识检索”的底线。

约束：不改动工具层计算逻辑（`calc_compensation` 等一行未动），不破坏已有测试；不引入任何外部并发库。

## 二、检索强化（`app/tools/readonly.py`）

- 新增 `KNOWLEDGE_KEYWORDS = ("保存", "存放", "储存", "饮用", "开瓶", "怎么喝", "醒酒", "口感")`。
- `search_policy` 排序键改为 `(知识优先, 字符得分降序, 政策编号升序)`：
  query 命中知识词时，`P-KNOW` 系列整体上浮到理赔条款之前；否则 `priority` 恒为 0，
  **排序与旧实现完全一致**（回归由 `test_search_policy_claim_query_order_unchanged` 锁定）。
- `PolicyHit.score` 语义不变（仍是字符重合度），加权只影响排序，不污染得分。

## 三、`knowledge_agent` 节点（`app/graph.py`）

- State 新增字段 `knowledge_result: str`（TypedDict 与 `DEFAULT_EMPTY` 同步）。
- 节点逻辑：读取 `user_text`（为空回退 `sub_tasks`）→ `search_policy(top_k=3)` →
  过滤 `P-KNOW` 取前 2 条 → 以“标题：内容”换行拼接写入 `knowledge_result`。
- 检索不到或检索异常 → 兜底“关于该问题我暂时没有找到相关说明”，不 handoff、不阻断主链。
- 路由：`router` 后新增条件边，仅当 `intent == 储存饮用咨询`（或 sub_tasks 含该项）时走
  `knowledge_agent → END`。
- 路由只按 intent 判定、不按关键词判定：因为“我买的酒漏了，还少一瓶，这酒怎么保存？”
  同时含理赔与知识词，按关键词分流会把理赔单误送知识节点。

## 四、多意图草稿合并（阶段3）

- `create_draft` 生成工单与回复后，调用 `_merge_knowledge_into_reply`：
  - 触发条件：`user_text + sub_tasks` 命中知识诉求词；
  - 知识来源：复用 `knowledge_agent(state)`，取前 2 条；
  - 追加格式：`\n\n【酒知识解答】\n1. 标题：内容\n2. …`；
  - 同步回写 State 的 `knowledge_result`。
- 工单逻辑零改动：`need_human_review=True`、补证清单、赔付建议全部保持原样。
- 纯理赔文本不命中知识词 → 回复内容与阶段2 完全一致，避免过度合并。

示例（订单 SO20261116001，无证据，混装诉求）：

```
【需要您补充的材料】1）开箱视频 2）面单照片 3）破损部位照片
【处理进度】您的材料尚在补充中，补齐后即可进入赔付评估。
【保存建议】…
感谢您的理解与配合…

【酒知识解答】
1. 葡萄酒保存条件：葡萄酒应避光平放保存，最佳温度 12~16℃…
2. 白酒保存条件：白酒需避光、防潮、常温保存，直立存放…
```

证据齐全时赔付仍为 `partial_refund 354.00 元`（1180×30%），话术含“最终结果以人工审核为准”，
且不再出现补证清单。

## 五、合规优先级锁定（阶段4）

- 需求：退款诉求的优先级必须高于知识检索，一旦触发即截断整条链路。
- 问题：原守卫只匹配“直接退款”等连续串，`直接给我退款` 这类插入式表达会漏检。
- 处理：`_REFUND_KEYWORDS` 扩充为包含 `退款` / `退钱`，即**只要出现退款语义即转人工**
  （资金操作不做自动处理），命中后 `error.code=COMPLIANCE_REFUND`，条件边直接转 `handoff_human`。
- 顺序保证：合规判定位于 `router` 开头，早于 `classify_intent`、早于知识分流、早于
  `create_draft` 的知识合并，因此退款用户既不会拿到工单，也不会收到知识解答。
- 影响面：无正常流程用例包含“退款/退钱”字样，扩充后原 67 条用例全部通过。

## 六、测试

| 测试文件 | 条数 | 覆盖 |
| --- | --- | --- |
| tests/test_readonly_tools.py | 9 | 6 只读工具（含赔付边界） |
| tests/test_router.py | 6 | 路由 + 降级 |
| tests/test_draft_tools.py | 8 | 2 草稿写工具 |
| tests/test_graph_basics.py | 5 | 图骨架 + handoff |
| tests/test_e2e_m2.py | 13 | 两幕 E2E + CLI --graph |
| tests/test_edge_cases.py | 14 | 边界、合规、赔付边界 |
| tests/test_m3_knowledge.py | 8 | 知识检索 + knowledge_agent |
| tests/test_m3_multi_intent.py | 7 | 多意图合并 + 合规优先 |
| **合计** | **70** | **pytest -q 全绿** |

阶段4 新增 3 条：

1. `test_refund_with_knowledge_handoffs_and_skips_knowledge_agent`：
   把 `knowledge_agent` 替换为必抛异常的桩，退款 + 知识混装仍能正常返回 handoff，证明该节点未执行；
   同时断言 `knowledge_result` 为空、无工单、无回复草稿。
2. `test_refund_with_knowledge_does_not_call_llm`：证明合规判定先于 LLM。
3. `test_knowledge_only_still_not_intercepted`：纯知识咨询不被合规守卫误伤。

说明：第 1 条必须在 `compile_graph()` **之前**替换节点函数——图在编译时绑定函数对象，
编译后再 monkeypatch 模块属性不会生效。

## 七、可复现命令

| 用途 | 命令 | 结果 |
| --- | --- | --- |
| 全量测试 | `.venv\Scripts\python.exe -m pytest -q` | 70 passed, 1 warning in 9.96s |
| 知识节点直调 | `python -c "from app.graph import knowledge_agent; print(knowledge_agent({'user_text':'酒怎么保存'})['knowledge_result'])"` | 输出葡萄酒/白酒保存条件两行 |
| 合规拦截 | 见 `tests/test_m3_multi_intent.py` | COMPLIANCE_REFUND |

## 八、明日待办（11-20）

- Docker 最终复验：`docker build -t wine-agent-m3 . && docker run --rm wine-agent-m3`，确认容器内 70 passed。
- M4：规则校验 `rule_validate` + LLM Judge，把 11-19 的关键词合规判断并入统一规则层。
- `handoff_human` 审批写队列按基线 11-20 落地（当前节点只置标，不写队列）。
- 图内 Langfuse span 覆盖到 `knowledge_agent` / `create_draft`（M5）。
