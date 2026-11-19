# Docker 首次构建验证记录（2026-11-17 收工补充）

> 本记录对应《作业2-1-实训计划》R4 风险 / D11 偏差的闭环证据：11-17 基线要求当天完成 Docker 首次构建验证，实际于收工后补做并通过。

## 1. 环境信息

| 项目 | 取值 |
| --- | --- |
| 宿主系统 | Windows（x86_64） |
| Docker Desktop | 4.99.0（build 245822） |
| Docker Engine | 29.9.3 |
| 运行上下文 | desktop-linux（WSL2 后端） |
| WSL2 发行版 | Ubuntu-24.04（WSL 版本 2） |
| 存储驱动 | overlay2 |
| 容器内 OS/Arch | linux/amd64 |
| 项目根 | `D:\projects\Wine-After-Sales-Agent\wine-after-sales-agent` |

## 2. 底层环境搭建（构建成功的前提）

为解决 C 盘空间不足与首次拉取镜像的网络 EOF 问题，完成以下底层搭建：

| 项 | 做法 |
| --- | --- |
| WSL2 Rootfs | 手动下载 Ubuntu-24.04 镜像（.tar.gz），用 `wsl --import Ubuntu-24.04` 安装到 **D:\softData\WSL\Ubuntu2404**（避免占 C 盘） |
| Docker Desktop | 主程序安装到 **D:\Programs\Docker**；WSL 数据根目录迁移至 **D:\softData\DockerData** |
| 镜像加速 | 在 Docker Engine 配置（`%USERPROFILE%\.docker\daemon.json`）加入国内加速源：`https://docker.xuanyuan.me`、`https://docker.1ms.run`、`https://docker.m.daocloud.io`，解决首次拉取 `python:3.11-slim` 的 EOF/超时 |

> 以上三个 D 盘路径与 daemon.json 镜像源均已在宿主环境中核验存在。

## 3. 依赖文件

项目根目录已创建：

- `Dockerfile`（`python:3.11-slim` → 装 requirements（清华源）→ COPY 源码 → `CMD python -m pytest -q`）
- `.dockerignore`（排除 `.env`、`.env.*`（保留 `.env.example`）、`__pycache__/`、`.venv/`、`.git/`、`docs/` 等，镜像不含真实密钥与本地虚拟环境）

## 4. 构建命令与结果

```
docker build -t wine-agent-m1 .
```

关键输出：

```
[+] Building 2091.6s (10/10) FINISHED
=> => naming to docker.io/library/wine-agent-m1
```

- 总耗时约 2091.6s（~35 分钟），主要消耗在首次网络拉取基础镜像；后续构建走缓存可大幅缩短。
- 产物：镜像 `wine-agent-m1:latest`，大小约 728MB。

## 5. 容器内测试结果

```
docker run --rm wine-agent-m1
```

输出（已二次复跑核验，结果一致）：

```
...............                                                          [100%]
15 passed in 4.71s
```

- 15 条 = 9 条工具单测（`tests/test_readonly_tools.py`）+ 6 条路由单测（`tests/test_router.py`），0 failed。
- 证明：依赖可在干净容器内复现安装、测试可在容器内独立复现通过，环境具备可移植性。

## 6. 闭环结论

| 项 | 结论 |
| --- | --- |
| R4（依赖安装或 Docker 构建失败） | **已闭环**，完成度 100% |
| D11（11-17 当天完成首次构建验证） | **已消除**：首次构建已执行并通过，容器内 15 passed |
| 遗留 | 仅 `docker-compose` 一键编排留待 M7（11-24）补充；当前单镜像 `docker build` + `docker run` 已可复现 |

## 7. 复现命令

```powershell
cd D:\projects\Wine-After-Sales-Agent\wine-after-sales-agent
docker build -t wine-agent-m1 .
docker run --rm wine-agent-m1
docker images --format "{{.Repository}}:{{.Tag}} {{.Size}}"
```
