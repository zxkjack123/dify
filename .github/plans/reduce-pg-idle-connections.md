# 减少 PostgreSQL Idle 连接数

## 背景与目标
- **问题/需求描述**：当前 Dify 部署有 171 个 idle PostgreSQL 连接（仅 1 个 active），浪费大量内存。根因是连接池配置过大：`docker-compose.override.yaml` 中 api（pool=100, overflow=50）、worker（pool=60, overflow=30）、worker_dataset（pool=120, overflow=60）三个服务的连接池远超日常需求。`.env` 中 PostgreSQL 自身也配置了过大的 `shared_buffers=16GB`、`max_connections=300`。
- **根因分析**：
  1. **override 文件是实际生效配置**：`docker-compose.override.yaml` 硬编码了每个服务的 `SQLALCHEMY_POOL_SIZE` 和 `SQLALCHEMY_MAX_OVERFLOW`，这些值会覆盖 `.env` 中的同名变量。当前理论最大连接数：api(2 workers × 150) + worker(90) + worker_dataset(180) = 570，远超 `max_connections=300`。
  2. **连接池预分配**：SQLAlchemy 连接池在创建后会逐步建立到 `pool_size` 数量的连接并保持 idle，即使没有请求也不释放。
  3. **PostgreSQL 内存配置过大**：`shared_buffers=16GB` 对日常单人使用场景过大。
- **目标**：将 idle 连接从 ~171 降至 ~30-40 个；释放约 13-14GB 内存（PostgreSQL shared_buffers + 减少的连接内存）。
- **非目标（不做什么）**：
  - 不修改 Celery 并发数或 gunicorn worker 数 — 与连接池大小无关
  - 不修改 Qdrant / Redis / 插件等无关配置 — 保持最小变更范围
  - 不改变 `SQLALCHEMY_POOL_PRE_PING` / `SQLALCHEMY_POOL_USE_LIFO` 等策略 — 当前策略合理
- **已有代码/流程复用分析**：
  - `.env` 中的 PostgreSQL 参数：修改（直接更新值）
  - `docker-compose.override.yaml` 中的服务级池配置：修改（这是实际生效的配置，**必须修改否则 .env 的变更无效**）

## 技术方案
- **方案概述**：同时修改 `docker/.env`（PostgreSQL 服务端参数 + 默认连接池参数）和 `docker/docker-compose.override.yaml`（各服务实际连接池参数），然后重启 docker-compose 使变更生效。
- **关键设计决策**：
  1. **worker_dataset 保留较大池**：该服务负责文档索引，偶尔批量处理时需要高并发。设置 pool=20, overflow=10（共30），16 个 gevent 协程够用。
  2. **api 服务适度缩减**：2 个 gunicorn worker 各 pool=10, overflow=5，理论最大 2×15=30 连接，日常足够。
  3. **max_connections=100**：新理论最大 = api(30) + worker(30) + worker_dataset(30) + 系统预留(~10) = 100，刚好匹配。
- **影响范围**：
  - `docker/.env` — PostgreSQL 服务端参数、默认连接池参数
  - `docker/docker-compose.override.yaml` — api / worker / worker_dataset 三个服务的连接池硬编码值

## Error & Rescue Map（关键失败路径映射）

| 代码路径/操作 | 可能的失败 | 错误类型 | 已处理？ | 处理方式 | 用户可见行为 |
|-------------|-----------|---------|---------|---------|------------|
| 修改 .env 后 docker compose up | 新 shared_buffers 值超过系统 shmmax | PostgreSQL 启动失败 | Y | 4GB 远低于 16GB，不可能超限 | 无影响 |
| 连接池缩小后批量文档处理 | pool 耗尽，请求排队等待连接 | SQLAlchemy TimeoutError | Y | 保留 overflow=10 提供缓冲；POOL_TIMEOUT=30s 有超时保护 | 处理速度略降但不会失败 |
| max_connections=100 但实际连接需求 >100 | PostgreSQL 拒绝新连接 | FATAL: too many connections | Y | 理论最大 ~90 + 系统连接 <100；若不够可改回 150 | 连接错误日志 |
| docker compose restart 期间 | 服务短暂不可用 | 计划内停机 | Y | 用户知晓需重启 | 几十秒不可用 |

## 执行计划

### Phase 1: 修改配置文件

#### ✅ Task 1.1: 修改 docker/.env 中的 PostgreSQL 服务端参数
- **目标**：降低 PostgreSQL 服务端资源占用
- **修改内容**：
  - 文件 `docker/.env`：
    - `POSTGRES_MAX_CONNECTIONS`：300 → 100
    - `POSTGRES_SHARED_BUFFERS`：16GB → 4GB
    - `POSTGRES_WORK_MEM`：64MB → 16MB
    - `POSTGRES_MAINTENANCE_WORK_MEM`：4GB → 512MB
    - `POSTGRES_EFFECTIVE_CACHE_SIZE`：80GB → 20GB
- **修改边界**：不得修改 `.env` 中 PostgreSQL 连接参数以外的内容
- **测试要求**：
  - 运行 `grep -E 'POSTGRES_(MAX_CONNECTIONS|SHARED_BUFFERS|WORK_MEM|MAINTENANCE_WORK_MEM|EFFECTIVE_CACHE_SIZE)' docker/.env`
  - 预期输出：显示新值 100, 4GB, 16MB, 512MB, 20GB
- **验收标准**：
  - ✅ `POSTGRES_MAX_CONNECTIONS=100`
  - ✅ `POSTGRES_SHARED_BUFFERS=4GB`
  - ✅ `POSTGRES_WORK_MEM=16MB`
  - ✅ `POSTGRES_MAINTENANCE_WORK_MEM=512MB`
  - ✅ `POSTGRES_EFFECTIVE_CACHE_SIZE=20GB`
- **潜在风险**：如果后续需要高并发批量处理，max_connections=100 可能需要临时调大

#### ✅ Task 1.2: 修改 docker/.env 中的默认连接池参数
- **目标**：降低 SQLAlchemy 默认连接池大小（作为 fallback 值）
- **修改内容**：
  - 文件 `docker/.env`：
    - `SQLALCHEMY_POOL_SIZE`：30 → 10
    - `SQLALCHEMY_MAX_OVERFLOW`：10 → 5
- **修改边界**：不得修改 `SQLALCHEMY_POOL_RECYCLE`、`SQLALCHEMY_ECHO`、`SQLALCHEMY_POOL_PRE_PING`、`SQLALCHEMY_POOL_USE_LIFO`、`SQLALCHEMY_POOL_TIMEOUT`
- **测试要求**：
  - 运行 `grep -E 'SQLALCHEMY_POOL_SIZE|SQLALCHEMY_MAX_OVERFLOW' docker/.env`
  - 预期输出：`SQLALCHEMY_POOL_SIZE=10` 和 `SQLALCHEMY_MAX_OVERFLOW=5`
- **验收标准**：
  - ✅ `SQLALCHEMY_POOL_SIZE=10`
  - ✅ `SQLALCHEMY_MAX_OVERFLOW=5`
- **潜在风险**：该值仅作为 docker-compose.yaml 的默认值，实际被 override 覆盖；风险极低

#### Task 1.3: 修改 docker/docker-compose.override.yaml 中各服务连接池参数
- **目标**：修改实际生效的连接池配置（这是最关键的步骤）
- **修改内容**：
  - 文件 `docker/docker-compose.override.yaml`：
    - api 服务：`SQLALCHEMY_POOL_SIZE` 100 → 10，`SQLALCHEMY_MAX_OVERFLOW` 50 → 5
    - worker 服务：`SQLALCHEMY_POOL_SIZE` 60 → 10，`SQLALCHEMY_MAX_OVERFLOW` 30 → 5
    - worker_dataset 服务：`SQLALCHEMY_POOL_SIZE` 120 → 20，`SQLALCHEMY_MAX_OVERFLOW` 60 → 10
- **修改边界**：不得修改 `SQLALCHEMY_POOL_TIMEOUT`、`SQLALCHEMY_POOL_PRE_PING`、`SQLALCHEMY_POOL_USE_LIFO` 以及非连接池相关配置
- **测试要求**：
  - 运行 `grep -E 'SQLALCHEMY_POOL_SIZE|SQLALCHEMY_MAX_OVERFLOW' docker/docker-compose.override.yaml`
  - 预期输出：api(10, 5), worker(10, 5), worker_dataset(20, 10)
- **验收标准**：
  - ✅ api 的 `SQLALCHEMY_POOL_SIZE` = "10"，`SQLALCHEMY_MAX_OVERFLOW` = "5"
  - ✅ worker 的 `SQLALCHEMY_POOL_SIZE` = "10"，`SQLALCHEMY_MAX_OVERFLOW` = "5"
  - ✅ worker_dataset 的 `SQLALCHEMY_POOL_SIZE` = "20"，`SQLALCHEMY_MAX_OVERFLOW` = "10"
- **潜在风险**：worker_dataset 的 16 协程共用 20+10=30 的连接池，如果同时处理 16 个任务且每个都需要 DB 连接，仍可能排队。但实际上不会 16 个同时密集查库。

### Phase 2: 重启服务并验证

#### Task 2.1: 重启 docker compose 服务
- **目标**：使新配置生效
- **修改内容**：
  - 执行 `cd docker && docker compose down && docker compose up -d`
- **修改边界**：不得修改任何文件
- **测试要求**：
  - 运行 `docker compose ps` 确认所有服务 healthy/running
  - 运行 `docker compose exec db_postgres psql -U postgres -d dify -c "SELECT count(*), state FROM pg_stat_activity GROUP BY state;"`
  - 预期输出：total connections ~30-40，idle 明显减少
- **验收标准**：
  - ✅ 所有服务状态为 running 或 healthy
  - ✅ idle 连接数从 ~171 降至 ≤60
  - ✅ PostgreSQL `SHOW shared_buffers;` 返回 `4GB`
  - ✅ PostgreSQL `SHOW max_connections;` 返回 `100`
- **潜在风险**：PostgreSQL 容器可能需要重建数据目录才能使 `max_connections` 和 `shared_buffers` 生效（取决于是否使用 initdb 参数还是 postgresql.conf）。如果不生效，需要 `docker compose down -v` 重建 pg volume 或手动进入容器修改 postgresql.conf。

## 回归检查清单
- [ ] 所有 docker compose 服务正常运行（`docker compose ps`）
- [ ] Dify Web UI 可正常登录和使用
- [ ] 知识库检索功能正常（执行一次查询）
- [ ] idle 连接数 ≤60（`pg_stat_activity` 查询）
- [ ] PostgreSQL 内存参数已生效（`SHOW shared_buffers; SHOW max_connections; SHOW work_mem;`）

## 审查日志

| 轮次 | 聚焦 | 发现问题数 | 已修正 | 剩余 |
|------|------|-----------|--------|------|
| R1 | 结构完整性 | 3 | 3 | 0 |
| R2 | 可执行性 | 2 | 2 | 0 |
| R3 | 风险与边缘 | 1 | 1 | 0 |
| **终止** | **T1 — 收敛终止** | | | **0** |

### Completion Summary

| 维度 | 结果 |
|------|------|
| 背景与目标 | 完整 |
| 技术方案 | 完整 |
| Error & Rescue Map | 4 路径已覆盖，0 CRITICAL GAP |
| 执行计划 | 2 Phase、4 Task |
| 回归检查清单 | 5 项目特定检查项 |
| 已知局限 | 无 |

### R1 Issues
- **Issue R1-1**: 缺少"已有代码/流程复用分析"字段 → 已在背景与目标中补充 ✅ 已修正
- **Issue R1-2**: Error & Rescue Map 未列出 max_connections 不足的失败路径 → 已添加第 3 行 ✅ 已修正
- **Issue R1-3**: Task 1.3 缺少"修改边界"中对非连接池配置的说明 → 已补充 ✅ 已修正

### R2 Issues
- **Issue R2-1**: Task 2.1 测试要求中的 psql 命令未指定数据库名 → 已修正添加 `-d dify` ✅ 已修正
- **Issue R2-2**: 时序推演缺失 — 4 个 Task 需列出实施各阶段的关键决策 → Phase 1 的 3 个 Task 可并行但建议顺序执行以便逐步验证；Phase 2 必须在 Phase 1 全部完成后执行。关键决策点：Task 2.1 如果 PostgreSQL 因 shared_buffers 改变未生效而需要 volume 重建，需要用户确认（涉及数据） ✅ 已修正

### R3 Issues
- **Issue R3-1**: PostgreSQL 服务端参数（max_connections, shared_buffers）通常在 initdb 时写入 postgresql.conf，后续修改环境变量可能不生效 → 已在 Task 2.1 潜在风险中说明，并提供手动修改 postgresql.conf 的降级方案 ✅ 已修正
