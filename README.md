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
- [ ] LangGraph 状态图骨架
- [ ] 3 个草稿写工具（create_ticket_draft, draft_reply, handoff_human）
- [ ] 破损漏液端到端测试