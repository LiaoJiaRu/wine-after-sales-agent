# Router/Judge/Polish 三份 Prompt 设计思路与迭代：三次失败、三处调整

本文档整理 `app/prompts/` 下三份 Prompt 的设计目标、输出契约、与代码的联动方式，以及两轮迭代记录。对应 M7 交付基线。

## 1. Prompt 资产总览

| 文件 | 版本 | 角色 | 调用点 | 状态 |
|---|---|---|---|---|
| `app/prompts/router_v1.txt` | v1 | 意图路由与多意图拆解 | `app/llm.py::classify_intent` | 已上线 |
| `app/prompts/judge_v1.txt` | v1 | 工单与回复草稿质检 | `app/llm.py::judge_draft` | 已上线 |
| `app/prompts/polish_v1.txt` | v1 | 回复语气润色与事实锁定 | `app/llm.py::polish_reply` | 已上线 |
| 专业 Agent Prompt | 未建 | 物流/假酒/订单子 Agent | `app/agents/*_agent.py` 占位 | 部分完成 |
| ReAct 兜底模板 | 未建 | 开放式工具选择 | 无 | 未启用 |

说明：仓库只保留 v1 文件，两轮评审结论已合并进 v1 正文；迭代过程记录见第 5 节。

## 2. Router Prompt

### 2.1 设计目标

把顾客口语化描述稳定映射为 8 类意图之一，并在多诉求时拆分可执行的子任务，供后续并发取证使用。

### 2.2 输出契约

```json
{
  "intent": "多意图",
  "confidence": 0.95,
  "need_clarify": false,
  "sub_tasks": ["破损漏液", "少件错发", "储存饮用咨询"]
}
```

- `intent` 只能取 8 类：破损漏液、少件错发、真伪争议、标签保质期、物流延迟、储存饮用咨询、多意图、其他。
- `sub_tasks` 只在 `intent=多意图` 时给出 2 条以上；其余情况为空数组。
- 只输出 JSON，不解释、不加代码块、不编造订单号与金额。

### 2.3 设计要点

- **多意图优先**：只要一句话里出现两个及以上不同问题，必须判为“多意图”，避免只处理主诉而漏掉次要诉求。
- **子任务命名与工具映射对齐**：子任务使用中文名（如“破损漏液”“少件错发”），由 `graph._ISSUE_MAP` 映射为工具层 `issue_type`。
- **不承担事实抽取**：订单号、金额、证据等事实由工具与槽位规则负责，Prompt 只做归类，降低幻觉风险。
- **知识分流不靠 Prompt 猜测**：是否走 `knowledge_agent` 由图按 `intent` 判定，避免“漏了……怎么保存”这类理赔文本被误分流。

### 2.4 代码联动

| 环节 | 实现 |
|---|---|
| 调用 | `classify_intent(text)` 拼接系统 Prompt 与用户原话 |
| 校验 | `parse_decision` 做 Pydantic Schema 校验，兼容代码块围栏与前后缀 |
| 修复 | `MAX_REPAIR = 1`，失败时追加错误信息与原输出，要求重新输出合法 JSON |
| 降级 | 仍不合法返回 `INVALID_JSON`；API 失败返回对应错误码；空输入直接转人工 |
| 合规前置 | 退款关键词与未成年人守卫在 LLM 之前执行，Prompt 不参与资金决策 |

## 3. Judge Prompt

### 3.1 设计目标

在人工审批前自动拦截四类高风险草稿：违规退款承诺、金额不一致、补证清单缺项、事实自相矛盾。

### 3.2 四条审核标准

1. 不得承诺直接退款；涉及金额必须写明“最终结果以人工审核为准”。
2. 金额必须与 `compensation.amount` 一致（两位小数），不得加价、减免或编造。
   **缺证例外**：当 `evidence_status.complete=false` 且 `recommendation=supplement_evidence` 时，不核对金额，也不要求回复出现金额。
3. 缺证时回复必须包含【需要您补充的材料】，且与 `missing` 一一对应。
4. `summary`、回复正文、`compensation` 三者事实一致。

### 3.3 输出契约

```json
{"pass": true, "reason": "金额与补证清单一致，无退款承诺", "failed_node": ""}
```

- `failed_node` 只允许 `create_draft`、`fetch_evidence` 或空串。
- 代码侧再做白名单校验，非法值统一回落为 `create_draft`。

### 3.4 代码联动

| 环节 | 实现 |
|---|---|
| 调用 | `judge_draft(state)` 注入工单摘要、证据状态、赔付建议与回复正文 |
| 校验 | `parse_judge` 严格解析 `pass/reason/failed_node` |
| 修复 | 非法 JSON 自动重试一次（`MAX_REPAIR=1`） |
| 失败策略 | 输出始终非法时 **fail-closed**（`pass=False`），交人工；LLM 不可用时图节点降级放行并标记 `degraded=True`，避免离线环境整链路瘫痪 |
| 重跑 | `retry_count < 2` 时按 `failed_node` 回跳；达到上限置 `JUDGE_REJECTED` 并转人工 |

## 4. Polish Prompt

### 4.1 设计目标

只改善语气、衔接与同理心，不改变任何事实、金额、清单与承诺。

### 4.2 硬性约束

1. 只优化表达，不增删或改写订单事实、商品事实、处理结论与政策依据。
2. 订单号、编号、金额及单位原样保留，不得四舍五入、换算或补充新数字。
3. 证据清单编号项逐项保留，不得漏项、换项或调整语义。
4. “人工审核”承诺必须保留，不得改为自动审核或直接赔付。
5. 不得新增“已退款”“立即退款”“保证退款”“直接退给您”“已补发”“保证补发”等承诺。
6. 只输出润色后的完整正文，不加解释与代码块。

### 4.3 代码侧二次校验

Prompt 约束之外，`graph.polish` 还会做确定性校验，任一不满足即回退原稿：

| 校验项 | 规则 |
|---|---|
| 金额 | 原文与润色稿的 `数字 + 元` 集合必须一致 |
| 订单号 | 原文与润色稿的订单号集合必须一致 |
| 编号清单 | 以 `数字）` 开头的行数与内容必须一致 |
| 违禁承诺 | 不得出现“已退款/立即退款/保证退款/直接退给您/已补发/保证补发” |
| 空正文 | 原文为空时跳过润色 |

LLM 异常、Prompt 文件缺失或校验不通过时，均保留原回复，不阻断主链。

## 5. 两轮迭代记录

| Prompt | 轮次 | 初始问题 | 调整 | 验证 |
|---|---|---|---|---|
| Router | 第 1 轮 | 初版只识别单一意图，混合诉求只处理主诉 | 新增“多意图”枚举、`sub_tasks` 拆分约束与混合诉求示例 | `test_m3_multi_intent.py`、`test_m3_subtasks.py`；M6 U09 |
| Router | 第 2 轮 | 知识分支曾按“子任务是否含知识词”判断，理赔文本带“怎么保存”时易误分流 | 改为以 `intent` 为主判定，仅非多意图且子任务含知识时才走知识分支 | `test_m3_knowledge.py` 中“理赔文本含保存词仍走理赔”用例 |
| Judge | 第 1 轮 | 规则为“金额必须一致”，缺证补证场景金额为 0，回复不写金额会被误驳回 | 增加缺证例外：`complete=false` 且 `supplement_evidence` 时不核对金额、不强制出现金额 | M6 U02；`test_m4_judge.py` |
| Judge | 第 2 轮 | `failed_node` 可能返回未定义节点，非法 JSON 会直接中断质检 | 增加 `failed_node` 白名单与改写；非法输出修复一次，仍非法则 fail-closed | `test_m4_judge.py` 的解析、修复与 fail-closed 用例；M6 U13/U14 |
| Polish | 第 1 轮 | 初版只要求“更温和专业”，模型可能顺手改动金额或承诺 | 增加金额、编号、订单号、“人工审核”四类硬性锁定 | `test_m5_polish.py` 的锁定与越界用例 |
| Polish | 第 2 轮 | 仅靠 Prompt 约束仍可能越界 | 增加代码侧正则二次校验与回退原稿机制 | `test_m5_polish.py` 的 amount/evidence/human_review 参数化用例 |

## 6. 未建 Prompt 与现状

| 项目 | 现状 | 原因 |
|---|---|---|
| 专业子 Agent Prompt | 物流、假酒子 Agent 仅有统一 `run()` 占位，未形成独立 Prompt | 当前由 `fetch_evidence` 统一取证，拆分节点不影响已验收能力 |
| ReAct 兜底模板 | 未启用 | 主链采用确定性编排，优先保证可测与可复现 |
| Few-shot 库 | 未单独维护 | Router 示例写在 `router_v1.txt` 内；Judge/Polish 以规则条款为主 |

## 7. 测试映射

| Prompt | 主要测试 |
|---|---|
| Router | `tests/test_router.py`、`tests/test_m3_knowledge.py`、`tests/test_m4_slot.py` |
| Judge | `tests/test_m4_judge.py`、`tests/test_m4_retry.py`、`tests/test_m6_evaluation.py` |
| Polish | `tests/test_m5_polish.py` |

全量回归命令：

```powershell
pytest -q
```

预期 **195 passed**（171 条 M1~M6 功能测试 + 24 条 M7 边界与展示层测试）。
