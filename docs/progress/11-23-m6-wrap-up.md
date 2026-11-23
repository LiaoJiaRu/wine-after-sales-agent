# M6 阶段3：功能冻结与 Docker Compose 验证

**11-23 功能已冻结。M6 评测 15 条全部通过（成功率 100%），全量 159 条测试全绿；Docker Compose 一键拉起 Redis 与 App 验证通过，核心代码正式锁定。**

## 1. 冻结基线

| 项目 | 结果 |
|---|---|
| M6 端到端评测 | 15 passed，成功率 100% |
| 本地全量回归 | 159 passed（原有 144 + M6 15） |
| M6 阶段3语法检查 | `docker compose config` 成功 |
| 容器内全量回归 | 159 passed，App 退出码 0 |
| 功能状态 | 核心代码正式锁定，准备 M7 交付冻结 |

本阶段未修改业务代码或测试代码，仅进行 Compose 验证并补全收尾文档。

## 2. Docker Compose 配置核验

- `redis` 使用 `redis:7-alpine`，healthcheck 为 `redis-cli ping`，间隔 5 秒、超时 3 秒、重试 5 次。
- `app` 的 `depends_on.redis.condition` 为 `service_healthy`，只有 Redis 健康后才启动。
- `Dockerfile` 基于 `python:3.11-slim`，先复制 `requirements.txt`，再执行 `pip install --no-cache-dir -r requirements.txt`，随后复制项目并运行默认命令。
- `.dockerignore` 已排除 `.env`、虚拟环境、缓存、数据库和 Git 元数据，避免将本地敏感文件及运行产物带入镜像。

## 3. 实际启动验证

执行：

```powershell
docker compose config
docker compose up --build --abort-on-container-exit --exit-code-from app app
docker compose ps -a
```

实测结果：

1. Compose 配置成功解析，语法有效。
2. App 镜像构建成功，依赖安装层正常复用，项目代码成功复制进镜像。
3. `wine-after-sales-agent-redis-1` 状态为 `Up ... (healthy)`。
4. Compose 明确输出 `redis-1 Waiting` → `redis-1 Healthy` → `app-1 Started`，健康依赖生效。
5. App 默认命令在容器内完成全量测试：`159 passed, 1 warning in 8.50s`，随后 `app-1 exited with code 0`。
6. 唯一警告为既有的 LangChain `allowed_objects` 弃用提醒，不影响运行结果。

验证结束后保留原有 Redis 服务，不执行 `down`，避免中断验证前已运行的本地环境；App 容器以成功状态退出。

## 4. 冻结说明

自本记录生成起，M6 核心业务实现、Mock 数据和评测脚本均进入冻结状态。M7 仅进行交付物整理、复现核验与发布，不再新增功能或调整业务行为；如确需改动，须重新执行 M6 15 条评测及全量回归。
