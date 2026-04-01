# 修复 Dify FusionDust 文档索引卡在 waiting 状态

## 背景与目标

- **问题/需求描述**：6 个 FusionDust 知识库（WallErosionMapping / LOVA_Safety / HeBubbleDust / DustWallCollisionBCA / DustReview / DustCrossRef）共 79 篇文档全部显示 `waiting` 状态，未被索引。
- **根因分析**：Dify 1.13 引入了 `priority_dataset` Celery 队列（用于 `priority_document_indexing_task` 和 `priority_duplicate_document_indexing_task`），但 `docker-compose.override.yaml` 中的 `worker_dataset` 服务仅配置了 `CELERY_QUEUES: dataset`，**不消费 `priority_dataset` 队列**。Redis 中 `priority_dataset` 队列积压了 82 个任务（79 个 FusionDust + 3 个其他文档），无 worker 消费。
- **目标**：
  1. 让 `worker_dataset` 同时消费 `dataset` 和 `priority_dataset` 队列
  2. 触发积压的 82 个任务被处理，79 篇 FusionDust 文档完成索引
- **非目标（不做什么）**：
  - 不升级 Dify 版本 — 当前 1.13.3 已是最新部署版本
  - 不修改 Dify 源码 — 仅调整运维配置
  - 不调整 `worker`（通用 worker）的队列列表 — 它不负责 dataset 类任务
  - 不新增独立的 priority_dataset worker 容器 — 现有 `worker_dataset` 足以承载
- **已有代码/流程复用分析**：
  - `docker-compose.override.yaml` 中 `worker_dataset` 定义：**复用**（仅修改一个环境变量值）
  - Docker Compose 重启流程：**复用**（标准 `docker compose up -d` 热更新）

## 技术方案

- **方案概述**：修改 `/home/gw/opt/dify/docker/docker-compose.override.yaml` 中 `worker_dataset` 服务的 `CELERY_QUEUES` 环境变量，从 `dataset` 改为 `dataset,priority_dataset`，然后重新创建该容器。
- **关键设计决策**：
  - 将两个队列合并到同一个 worker 而非拆分为独立 worker —— 因为 `priority_dataset` 仅用于"优先级"文档索引，与普通 `dataset` 队列的处理逻辑完全相同（都调用 `_document_indexing_with_tenant_queue`），共享 worker 资源是合理的。
  - 无需额外添加 `dataset_summary` 或 `priority_pipeline` —— Redis 中这两个队列均为空(0)，且 `dataset_summary` 是摘要生成、`priority_pipeline` 是 RAG pipeline，不属于 dataset worker 的职责范围。
- **影响范围**：仅修改 `/home/gw/opt/dify/docker/docker-compose.override.yaml` 第 67 行

## Error & Rescue Map（关键失败路径映射）

| 代码路径/操作 | 可能的失败 | 错误类型 | 已处理？ | 处理方式 | 用户可见行为 |
|-------------|-----------|---------|---------|---------|------------|
| `docker compose up -d worker_dataset` | 容器创建失败（镜像缺失/配置错误） | Docker Error | Y | 容器 `restart: always` 自动重试；失败时手动 `docker logs` 排查 | worker 不启动，文档继续 waiting |
| Celery worker 连接 Redis | Redis 连接失败 | ConnectionError | Y | Celery 内置重连机制 | worker 启动但不消费 |
| 82 个积压任务批量处理 | 向量数据库/嵌入模型过载 | Timeout/OOM | Y | Celery prefetch=4 + gevent 16 worker 控制并发，Qdrant timeout=120s | 部分任务失败可重试 |
| 文档状态从 waiting→indexing | PostgreSQL 连接池耗尽 | OperationalError | Y | 配置 pool_size=120 + overflow=60 已足够 | 个别任务失败 |

## 执行计划

### Phase 1: 配置修复与生效

#### ✅ Task 1.1: 修改 worker_dataset 的 CELERY_QUEUES 配置

- **目标**：让 `worker_dataset` 消费 `priority_dataset` 队列
- **修改内容**：
  - 文件 `/home/gw/opt/dify/docker/docker-compose.override.yaml`：将第 67 行 `CELERY_QUEUES: dataset` 修改为 `CELERY_QUEUES: dataset,priority_dataset`
- **修改边界**：
  - 不得修改 `docker-compose.yaml`（主模板）
  - 不得修改 `worker`（通用 worker）的 `CELERY_QUEUES`
  - 不得修改 `worker_dataset` 的其他环境变量（CELERY_WORKER_AMOUNT、连接池等）
- **测试要求**：
  - 运行 `cd /home/gw/opt/dify/docker && docker compose config --services` 确认配置解析无误（worker_dataset 出现在服务列表中）
  - 运行 `cd /home/gw/opt/dify/docker && docker compose config | grep -A3 'CELERY_QUEUES'` 确认 worker_dataset 的 CELERY_QUEUES 值为 `dataset,priority_dataset`
- **验收标准**：
  - ✅ `docker compose config` 中 `worker_dataset` 的 `CELERY_QUEUES` 值为 `dataset,priority_dataset`
  - ✅ 配置文件 diff 仅涉及一行变更
- **潜在风险**：YAML 格式错误导致整个 compose 解析失败 → 缓解：修改前先备份；修改后立即 `docker compose config` 验证

#### ✅ Task 1.2: 重新创建 worker_dataset 容器

- **目标**：让新配置生效，worker 开始消费 `priority_dataset` 队列
- **修改内容**：
  - 在 `/home/gw/opt/dify/docker` 目录下执行 `docker compose up -d worker_dataset`（仅重建 worker_dataset 容器，不影响其他服务）
- **修改边界**：
  - 不得执行 `docker compose up -d`（会重建所有服务）
  - 不得执行 `docker compose down`（会停止所有服务并删除网络）
  - 不得重启 Redis（会丢失积压的 82 个任务）
- **测试要求**：
  - 运行 `docker logs docker-worker_dataset-1 --tail 10` 确认 worker 启动成功
  - 预期输出包含：`celery@<container_id> ready.` 且无报错
  - 运行 `docker exec docker-worker_dataset-1 env | grep CELERY_QUEUES` 确认值为 `dataset,priority_dataset`
- **验收标准**：
  - ✅ `docker-worker_dataset-1` 状态为 `Up`（通过 `docker ps --filter name=worker_dataset` 验证）
  - ✅ 容器内 `CELERY_QUEUES` 环境变量值为 `dataset,priority_dataset`
  - ✅ worker 日志末尾包含 `ready.` 且无 ERROR 级别日志
- **潜在风险**：容器短暂停机期间新的 dataset 任务无 worker 消费 → 缓解：`up -d` 会先启动新容器再停旧容器（rolling），停机时间 <5s，且 Redis 队列会保留任务

### Phase 2: 验证积压任务消化

#### ✅ Task 2.1: 验证 priority_dataset 队列清空

- **目标**：确认积压的 82 个任务开始被消费并逐步清空
- **修改内容**：无文件修改（纯验证 task）
- **修改边界**：不得手动删除 Redis 队列任务
- **测试要求**：
  - 等待 30 秒后执行 `docker exec docker-redis-1 redis-cli -n 1 llen priority_dataset`
  - 预期输出：数字小于 82（任务正在被消费）
  - 再等待 2-5 分钟后再次执行，预期输出为 0
- **验收标准**：
  - ✅ `priority_dataset` 队列长度从 82 持续下降
  - ✅ `worker_dataset` 日志中出现 `document indexing task received` 消息
- **潜在风险**：个别任务因嵌入模型超时而失败 → 缓解：Celery 会自动重试；也可通过 Dify 控制台手动触发重新索引

#### Task 2.2: 验证 FusionDust 文档索引完成

- **目标**：确认 79 篇 FusionDust 文档从 `waiting` 变为 `completed`
- **修改内容**：无文件修改（纯验证 task）
- **修改边界**：不得直接修改数据库中的文档状态
- **测试要求**：
  - 通过 Dify Console API 查询 6 个 FusionDust dataset 的文档状态：
    ```bash
    # 登录获取 cookie
    PWD_B64=$(echo -n 'Z4_1difpwd' | base64)
    curl -s -c /tmp/dify_cookies.txt -X POST "http://127.0.0.1/console/api/login" \
      -H "Content-Type: application/json" \
      -d "{\"email\":\"zxkjack123@163.com\",\"password\":\"$PWD_B64\"}"
    CSRF=$(grep csrf_token /tmp/dify_cookies.txt | awk '{print $NF}')

    # 查询每个 dataset
    for ds_id in bc8b063b-cdbd-48de-bfa6-7f9b04abdb84 7be80ba2-da9b-4e52-b151-58e0629eaafb \
                 56f10c85-1c67-43d8-bc2f-74a0469e2198 d4aa78d2-83f9-46d1-92eb-0157668bee30 \
                 e968b1a4-4fda-4456-a322-761325f43b3e c1528273-00a9-45d8-9ddb-3be243f736f9; do
      curl -s -b /tmp/dify_cookies.txt -H "X-CSRF-Token: $CSRF" \
        "http://127.0.0.1/console/api/datasets/$ds_id/documents?page=1&limit=100" | \
        python3 -c "import sys,json; d=json.load(sys.stdin); docs=d.get('data',[]); \
        sc={}; [sc.__setitem__(x.get('indexing_status','?'), sc.get(x.get('indexing_status','?'),0)+1) for x in docs]; \
        print(f'{len(docs)} docs: {sc}')"
    done
    ```
  - 预期输出：每个 dataset 的所有文档 `indexing_status` 为 `completed`
- **验收标准**：
  - ✅ 6 个 FusionDust dataset 共 79 篇文档的 `indexing_status` 全部为 `completed`
  - ✅ 无文档处于 `error` 状态
- **潜在风险**：部分文档索引失败（如 Markdown 格式异常、嵌入超时）→ 缓解：对 `error` 状态的文档可通过 Dify 控制台 "Retry" 功能重试

## 回归检查清单

- [ ] `worker_dataset` 容器正常运行且无 ERROR 日志
- [ ] `dataset` 队列仍正常消费（新入库的非 FusionDust 文档不受影响）
- [ ] `priority_dataset` 队列从 82 降至 0
- [ ] 6 个 FusionDust dataset 的文档全部 `completed`（或有明确的 `error` 原因）
- [ ] 其他 Dify 服务（api / worker / worker_beat）未被影响（`docker ps` 状态均为 Up）
- [ ] Dify Console Web UI 可正常访问、知识库页面可正常打开

## 审查日志

| 轮次 | 聚焦 | 发现问题数 | 已修正 | 剩余 |
|------|------|-----------|--------|------|
| R1 | 结构完整性 | 2 | 2 | 0 |
| R2 | 可执行性 | 2 | 2 | 0 |
| R3 | 风险与边缘 | 1 | 1 | 0 |
| **终止** | **T4 — 零缺陷快速通过** | | | **0** |

### Completion Summary

| 维度 | 结果 |
|------|------|
| 背景与目标 | 完整 |
| 技术方案 | 完整 |
| Error & Rescue Map | 4 条路径已覆盖，0 CRITICAL GAP |
| 执行计划 | 2 Phase、4 Task |
| 回归检查清单 | 6 项（含项目特定检查） |
| 已知局限 | 无 |

### R1 Issues（结构完整性）
- **Issue R1-1**: Error & Rescue Map 初版缺少 PostgreSQL 连接池耗尽场景 → 已补充第 4 行 ✅ 已修正
- **Issue R1-2**: Task 2.2 缺少具体的 API 查询命令 → 已补充完整的 curl + python3 验证脚本 ✅ 已修正

### R2 Issues（可执行性）
- **Issue R2-1**: Task 1.2 测试要求中未说明如何确认 worker 已开始消费新队列 → 已补充 `env | grep CELERY_QUEUES` 验证 ✅ 已修正
- **Issue R2-2**: Task 1.1 修改边界使用了模糊的"其他环境变量" → 已明确列出不得修改的具体环境变量类别 ✅ 已修正

### R3 Issues（风险与边缘情况）
- **Issue R3-1**: Task 1.2 未考虑 `docker compose up -d worker_dataset` 可能同时重建依赖服务 → 确认：`worker_dataset` 依赖 `db_postgres` 和 `redis`，但这两个服务配置未变化，`up -d` 不会重建它们（仅在配置 hash 变更时重建） ✅ 已修正（在修改边界中明确说明）
