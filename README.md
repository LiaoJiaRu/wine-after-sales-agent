# 线上酒类售后理赔客服工单 Agent

## 进度记录

### 基础配置与基建（✅ 已完成，11-17）
- [x] 工程根目录确认（D:\projects\Wine-After-Sales-Agent\wine-after-sales-agent）
- [x] 环境隔离（基于 Python 3.12 创建独立虚拟环境 `.venv`）
- [x] 依赖安装与排障（解决 Windows 下 chromadb 缺少 C++ 编译环境的问题，暂缓安装）
- [x] `.env` 配置与连通性验证（DeepSeek API 与 Langfuse 云端 JP 节点均鉴权通过）
- [x] Langfuse T6 链路打通（成功发送测试 Trace 并记录）

### M1 骨架与工具层（✅ 已完成，11-17）
- [x] 目录骨架搭建（`app/`, `data/`, `tests/`, `ui/`, `docs/`）
- [x] Mock 数据就位（订单、物流、批次、政策 4 个 JSON 文件）
- [x] 6 个只读工具开发完毕并附带 Pydantic Schema
- [x] 错误类型降级（NotFoundError, DataSourceError 等）
- [x] 9 条工具单测 100% 通过（pytest 9 passed）

### M2 单场景闭环（🚧 进行中）
- [x] DeepSeek LLM 客户端接入与连通性测试
- [x] 意图路由（11-17）：`app/llm.py`（OpenAI 兼容、BASE_URL 可切 Ollama、JSON Schema 校验 + 一次修复重试 + 降级）
- [x] `app/prompts/router_v1.txt` + `app/agents/router.py` + `app/cli.py`
- [x] 路由单测 6 条（合法 JSON / 非法 JSON 修复 / API 失败降级 / 空输入），pytest 15 passed
- [x] T6：CLI 真实调用上报 Langfuse（`router_intent` → `router_llm` GENERATION），记录见 `docs/traces/11-17.md`
- [ ] LangGraph 状态图骨架
- [ ] 3 个草稿写工具（create_ticket_draft, draft_reply, handoff_human）
- [ ] 破损漏液端到端测试

### 明日待办（11-18）
- [ ] LangGraph 状态定义（节点/边/路由分支）
- [ ] 破损漏液单场景闭环
- [ ] 3 个草稿写工具