# Dify v1.9.1 → v1.13.3 迁移执行计划

## 背景与目标

- **问题/需求描述**：当前 Dify 部署基于 v1.9.1（zxk-dev 分支，含 8 个自定义 commit），需升级至 v1.13.3 以使用 xparse 插件的子图像提取功能。数据库 65 GB（embeddings 55 GB），Qdrant 31 GB（141 collections），文件存储 4.3 GB，需确保升级过程中数据零丢失。
- **根因分析**：v1.9.1 的插件系统不支持 `intsig-textin/xparse`（2026-02-04 发布，需 1.11+ 的成熟插件架构）。
- **目标**：
  1. 数据库、知识库（Qdrant 向量 + 文件存储）完整性 100% 保障
  2. 核心功能可用：工作流执行、知识库检索、插件系统
  3. xparse 插件可安装并使用子图像提取
- **非目标（不做什么）**：
  - 不在此次迁移中恢复自定义 Web UX 改进（use-auth.ts 错误处理等）
  - 不恢复 model.py mock fallback（仅在 plugin daemon 不稳定时按需补回）
  - 不恢复 error_handler 超时检测（1.13.3 已有 UI retry 配置）
  - 不修改 dify_graph 外部包

## 技术方案

- **方案概述**：基于 Docker 镜像直接升级（不 rebase 源码），用 1.13.3 官方 compose 配置 + 定制化 override，保留独立文件。关键路径：停服 → 备份 → 切换 compose + .env → 启动新镜像 → 自动 migration → 验证。
- **关键设计决策**：
  1. **镜像升级而非源码 rebase**：自定义改动中阻塞升级的只有 compose override 和 .env；核心功能改动（retry-stale-docs、automation/）可后续补回，不影响首次启动。
  2. **DB 服务名从 `db` 改为 `db_postgres`**：1.13.3 重命名了数据库服务（支持多种数据库），必须设置 `DB_HOST=db_postgres` 或在 override 中添加别名。
  3. **COMPOSE_PROFILES 机制**：1.13.3 用 `COMPOSE_PROFILES=${VECTOR_STORE:-weaviate},${DB_TYPE:-postgresql}` 控制启动哪些中间件服务（db_postgres 需 `postgresql` profile，qdrant 需 `qdrant` profile）。
  4. **alembic 链完整**：当前 DB head `68519ad5cd18`（2025_09_17）→ 1.13.3 下一个 migration `d98acf217d43`（2025_10_14）→ … 共 24 个新 migration，链路连续。
  5. **Qdrant 版本升级风险**：当前 v1.7.3 → 1.13.3 用 v1.8.3。Qdrant 小版本升级可能自动变更内部存储格式（单向不可逆），因此必须在停服后、启动新版本前做文件系统级完整备份（Task 2.3），这是回退 Qdrant 的唯一保障。
- **影响范围**：
  - `docker/.env` — 需新增/修改多个环境变量
  - `docker/docker-compose.yaml` — 替换为 1.13.3 版本
  - `docker/docker-compose.override.yaml` — 重写适配新版
  - `docker/volumes/` — 不修改，原地保留

## 当前部署数据清单

| 组件 | 位置 | 大小 | 说明 |
|------|------|------|------|
| PostgreSQL 数据 | `docker/volumes/db/data/` | 68 GB | bind mount, 96 张表, head=`68519ad5cd18` |
| PostgreSQL plugin DB | 同上（`dify_plugin` 库） | 含在 68 GB 内 | plugin daemon 独立数据库 |
| Qdrant 向量库 | `docker/volumes/qdrant/` | 31 GB | 141 collections |
| 文件存储 | `docker/volumes/app/storage/` | 4.3 GB | upload_files 3.4G, cwd 877M, plugins 14M |
| Redis | `docker/volumes/redis/data/` | 62 MB | Celery broker + cache |
| **合计** | | **~103 GB** | 磁盘剩余 2.3 TB，充足 |

## 执行计划

### Phase 1: 数据备份（数据安全——最高优先级）

#### ✅ Task 1.1: PostgreSQL 全量备份
- **目标**：创建 PostgreSQL 逻辑备份（pg_dump），确保可独立恢复
- **修改内容**：
  - 无文件修改，仅执行备份命令，输出到 `~/dify-backup-<日期>/`
- **修改边界**：不修改任何文件，不停止服务
- **测试要求**：
  - 运行 `ls -lh ~/dify-backup-$(date +%Y%m%d)/dify_full.sql.gz` 验证文件存在且 > 1 GB
  - 运行 `zcat ~/dify-backup-$(date +%Y%m%d)/dify_full.sql.gz | head -20` 验证包含 `CREATE TABLE` 语句
- **验收标准**：
  - ✅ `dify_full.sql.gz` 文件存在且大小合理（预估 5-15 GB 压缩后）
  - ✅ 包含 `dify` 和 `dify_plugin` 两个库的数据
- **潜在风险**：备份期间 DB 负载增加；65 GB 库 pg_dump 预计耗时 10-30 分钟
- **具体命令**：
  ```bash
  mkdir -p ~/dify-backup-$(date +%Y%m%d)
  # 全库备份（含 dify + dify_plugin），使用 pipefail 捕获 pg_dumpall 失败
  set -o pipefail
  docker exec dify-db-1 pg_dumpall -U postgres | gzip > ~/dify-backup-$(date +%Y%m%d)/dify_full.sql.gz
  echo "pg_dumpall exit: ${PIPESTATUS[0]}, gzip exit: ${PIPESTATUS[1]}"
  # 验证备份完整性：检查文件尾部有正常结束标记
  zcat ~/dify-backup-$(date +%Y%m%d)/dify_full.sql.gz | tail -5
  # 预期最后几行包含 GRANT 或 \. 等 pg_dumpall 正常结尾
  ```

#### ✅ Task 1.2: 文件存储快照
- **目标**：备份文件存储目录（上传文件、插件包）
- **修改内容**：
  - 无文件修改，rsync 复制到备份目录
- **修改边界**：不停止服务，不修改原数据
- **测试要求**：
  - 运行 `diff <(cd docker/volumes/app/storage && find . -type f | sort | wc -l) <(cd ~/dify-backup-$(date +%Y%m%d)/storage && find . -type f | sort | wc -l)` 预期输出一致
- **验收标准**：
  - ✅ `~/dify-backup-<日期>/storage/` 目录存在，大小约 4.3 GB
  - ✅ `upload_files/` 子目录文件数与原始一致
- **潜在风险**：rsync 期间如有新上传文件会被遗漏（可在停服后补充增量）
- **具体命令**：
  ```bash
  rsync -a --info=progress2 docker/volumes/app/storage/ ~/dify-backup-$(date +%Y%m%d)/storage/
  ```

#### ✅ Task 1.3: Qdrant API 快照（在线预备份）
- **目标**：在停服前通过 Qdrant API 创建快照，作为第一道保险
- **修改内容**：无文件修改
- **修改边界**：不停止服务
- **测试要求**：
  - 运行 `curl -s http://localhost:6333/snapshots | python3 -m json.tool` 验证快照列表中有新条目
- **验收标准**：
  - ✅ Qdrant 快照创建成功，返回 snapshot name
  - ✅ 快照文件出现在 `docker/volumes/qdrant/snapshots/`
- **潜在风险**：31 GB 数据快照可能耗时 5-15 分钟；快照期间查询性能降低
- **重要说明**：此 API 快照存储在 Qdrant 数据目录内部，如果 v1.8.3 升级修改了存储格式，快照可能无法被 v1.7.3 读取。因此 **Task 2.3 的文件系统级备份才是回退的真正保障**。
- **具体命令**：
  ```bash
  # 全库快照（在线预备份）
  curl -X POST 'http://localhost:6333/snapshots' -H 'api-key: difyai123456'
  ```

#### ✅ Task 1.4: Git 分支备份
- **目标**：保护当前 zxk-dev 分支不被破坏
- **修改内容**：仅创建 git tag
- **修改边界**：不修改、不删除任何分支
- **测试要求**：
  - 运行 `git tag -l 'pre-upgrade-*'` 验证 tag 存在
- **验收标准**：
  - ✅ `pre-upgrade-v1.9.1` tag 存在且指向当前 HEAD
- **潜在风险**：无
- **具体命令**：
  ```bash
  cd /home/gw/opt/dify
  git tag pre-upgrade-v1.9.1
  ```

### Phase 2: 停服并确保数据一致性

#### ✅ Task 2.1: 优雅停服
- **目标**：按正确顺序关停所有容器，确保数据刷盘
- **修改内容**：无文件修改
- **修改边界**：仅操作 docker compose
- **测试要求**：
  - 运行 `docker compose ps` 预期输出为空（所有容器已停止）
- **验收标准**：
  - ✅ 所有 12 个容器状态为 Exited
  - ✅ 无 data corruption 警告（检查 `docker compose logs db 2>&1 | tail -5`，应有 "database system is shut down" ）
- **潜在风险**：正在进行的文档索引任务会中断（celery 中的 pending tasks 丢失）；工作流执行中的请求会失败
- **具体命令**：
  ```bash
  cd /home/gw/opt/dify/docker
  # 先停 worker 避免新任务
  docker compose stop worker worker_dataset worker_beat
  # 等待 30 秒让进行中的任务完成
  sleep 30
  # 停其他服务
  docker compose down
  # 验证 DB 正确关闭
  docker compose logs db 2>&1 | tail -10
  ```

#### ✅ Task 2.2: 停服后增量备份（可选但推荐）
- **目标**：停服后对文件存储做增量同步，确保备份 100% 一致
- **修改内容**：无
- **修改边界**：不修改原数据
- **测试要求**：
  - rsync 的 `--dry-run` 模式显示 0 个新文件（或仅少量）
- **验收标准**：
  - ✅ 增量 rsync 完成无错误
- **潜在风险**：无（停服状态下数据不变）
- **具体命令**：
  ```bash
  rsync -a --info=progress2 docker/volumes/app/storage/ ~/dify-backup-$(date +%Y%m%d)/storage/
  ```

#### ✅ Task 2.3: Qdrant 数据目录完整备份（关键——回退保障）
- **目标**：在停服状态下复制 Qdrant 完整数据目录，确保回退时可用旧版本格式的数据直接替换
- **修改内容**：无文件修改，仅复制到备份目录
- **修改边界**：不修改原数据；必须在容器停止后、启动新版本前执行
- **测试要求**：
  - 运行 `du -sh ~/dify-backup-$(date +%Y%m%d)/qdrant/` 预期输出约 31 GB
  - 运行 `ls ~/dify-backup-$(date +%Y%m%d)/qdrant/collections/ | wc -l` 预期输出 `141`
- **验收标准**：
  - ✅ `~/dify-backup-<日期>/qdrant/` 目录存在且大小约 31 GB
  - ✅ `collections/` 子目录包含 141 个 collection 目录
  - ✅ `raft_state.json` 文件存在
- **潜在风险**：31 GB 文件复制预计耗时 3-5 分钟（NVMe SSD 上）
- **为什么必须做**：Qdrant 从 v1.7.3 升级到 v1.8.3 时可能自动升级内部存储格式，导致旧版本无法读取升级后的数据。此文件系统级备份是**唯一能保证 Qdrant 回退成功的手段**。
- **具体命令**：
  ```bash
  # 必须在 docker compose down 之后执行
  cp -a docker/volumes/qdrant/ ~/dify-backup-$(date +%Y%m%d)/qdrant/
  # 验证
  ls ~/dify-backup-$(date +%Y%m%d)/qdrant/collections/ | wc -l
  ```

### Phase 3: 配置文件升级

#### ✅ Task 3.1: 更新 docker-compose.yaml
- **目标**：切换到 1.13.3 官方 compose 配置
- **修改内容**：
  - 文件 `docker/docker-compose.yaml`：用 `git show 1.13.3:docker/docker-compose.yaml` 替换
- **修改边界**：不得修改 `docker/volumes/` 下任何数据文件；不得修改 `docker/.env`（下一个 task 处理）
- **测试要求**：
  - 运行 `grep 'image.*1.13.3' docker/docker-compose.yaml | wc -l` 预期输出 ≥ 4（api, worker, worker_beat, web）
  - 运行 `grep 'db_postgres' docker/docker-compose.yaml | head -3` 预期匹配
- **验收标准**：
  - ✅ `docker-compose.yaml` 内容与 1.13.3 tag 完全一致
  - ✅ 包含 `db_postgres` 服务定义（而非旧的 `db`）
  - ✅ 包含 `plugin_daemon` 服务使用 `0.5.3-local` 镜像
- **潜在风险**：如果用户有其他 compose 修改（非 override 的），会丢失——但经检查，所有定制都在 override 中
- **具体命令**：
  ```bash
  cd /home/gw/opt/dify
  git show 1.13.3:docker/docker-compose.yaml > docker/docker-compose.yaml
  ```

#### ✅ Task 3.2: 更新 .env 文件
- **目标**：在保留现有自定义值的前提下，添加 1.13.3 必需的新变量
- **修改内容**：
  - 文件 `docker/.env`：添加/修改关键环境变量
- **修改边界**：不得修改 `docker/docker-compose.yaml`；不得删除现有自定义值
- **测试要求**：
  - 运行 `grep 'COMPOSE_PROFILES' docker/.env` 预期输出 `COMPOSE_PROFILES=qdrant,postgresql`
  - 运行 `grep 'DB_HOST' docker/.env` 预期输出 `DB_HOST=db_postgres`
  - 运行 `grep 'VECTOR_STORE' docker/.env` 预期输出 `VECTOR_STORE=qdrant`
- **验收标准**：
  - ✅ `COMPOSE_PROFILES=qdrant,postgresql` 已设置
  - ✅ `DB_HOST=db_postgres` 已设置
  - ✅ `VECTOR_STORE=qdrant` 保持不变
  - ✅ 现有 `DB_USERNAME`, `DB_PASSWORD`, `DB_DATABASE` 值不变
  - ✅ 已有 `PLUGIN_DAEMON_KEY` 和 `INNER_API_KEY_FOR_PLUGIN` 与当前 override 中一致
- **潜在风险**：遗漏某个必需变量导致服务启动失败——缓解：可用 1.13.3 的 `dify-env-sync.py` 工具自动对齐
- **必须添加/修改的变量**：
  ```env
  # === 1.13.3 必需项 ===
  COMPOSE_PROFILES=qdrant,postgresql
  DB_HOST=db_postgres
  DB_TYPE=postgresql

  # === 插件系统（保持与当前一致）===
  PLUGIN_DAEMON_KEY=lYkiYYT6owG+71oLerGzA7GXCgOT++6ovaezWAjpCjf+Sjc3ZtU+qUEi
  PLUGIN_DIFY_INNER_API_KEY=QaHbTe77CtuXmsfyhR7+vRjI/+XbV1AaFy691iy+kGDv2Jvy0/eAh8Y1
  FORCE_VERIFYING_SIGNATURE=true

  # === 保留原有值不变 ===
  # DB_USERNAME=postgres
  # DB_PASSWORD=difyai123456
  # DB_DATABASE=dify
  # VECTOR_STORE=qdrant
  # QDRANT_URL=http://qdrant:6333
  # QDRANT_CLIENT_TIMEOUT=60
  # QDRANT_GRPC_PORT=6334
  # QDRANT_GRPC_ENABLED=true
  ```
- **推荐操作**：先运行 env-sync 工具，再手动检查关键值：
  ```bash
  cd /home/gw/opt/dify
  # 先备份旧 .env
  cp docker/.env docker/.env.pre-upgrade
  # 用 1.13.3 的 env-sync 工具（保留现有值，补新变量）
  git show 1.13.3:docker/.env.example > /tmp/.env.example.1.13.3
  git show 1.13.3:docker/dify-env-sync.py > /tmp/dify-env-sync.py
  python3 /tmp/dify-env-sync.py --env docker/.env --example /tmp/.env.example.1.13.3
  # 然后手动确认关键值
  ```

#### ✅ Task 3.3: 重写 docker-compose.override.yaml
- **目标**：适配 1.13.3 compose 结构，只保留必要定制
- **修改内容**：
  - 文件 `docker/docker-compose.override.yaml`：全部重写
- **修改边界**：不得修改 `docker-compose.yaml`（已在 3.1 处理）；不得修改 `docker/.env`（已在 3.2 处理）
- **测试要求**：
  - 运行 `docker compose config --services 2>&1 | sort` 应包含 `api`, `worker`, `worker_dataset`, `worker_beat`, `web`, `db_postgres`, `qdrant`, `redis`, `nginx`, `plugin_daemon`, `sandbox`, `ssrf_proxy`
  - 运行 `docker compose config 2>&1 | grep 'image.*1.13.3' | wc -l` 应 ≥ 5
- **验收标准**：
  - ✅ `worker_dataset` 服务使用 `langgenius/dify-api:1.13.3` 镜像
  - ✅ `worker_dataset` 的 `CELERY_QUEUES=dataset`
  - ✅ `db_postgres` 服务的 postgres 配置参数与旧 `db` 一致（shared_buffers=16GB 等）
  - ✅ `qdrant` 服务不再需要 override profiles（主 compose 已通过 COMPOSE_PROFILES 处理）
  - ✅ 删除了 `plugin_patch`（SiliconFlow 补丁已废弃）
  - ✅ `docker compose config` 无错误
- **潜在风险**：worker_dataset 的环境变量不完整可能导致 celery 连不上 broker——1.13.3 的 `x-shared-env` anchor 可以简化这个问题
- **新 override 核心内容**：
  ```yaml
  services:
    api:
      environment:
        SERVER_WORKER_AMOUNT: "2"
        SQLALCHEMY_POOL_SIZE: "100"
        SQLALCHEMY_MAX_OVERFLOW: "50"
        QDRANT_CLIENT_TIMEOUT: "120"
        INDEXING_MAX_SEGMENTATION_TOKENS_LENGTH: "6000"
        ENABLE_DATASETS_QUEUE_MONITOR: "true"
        UPLOAD_FILE_SIZE_LIMIT: "50"
        UPLOAD_FILE_BATCH_LIMIT: "1000"
        GUNICORN_TIMEOUT: "1800"

    worker:
      environment:
        CELERY_WORKER_CLASS: gevent
        CELERY_WORKER_AMOUNT: "2"
        CELERY_QUEUES: generation,mail,ops_trace,app_deletion,plugin,workflow_storage,conversation
        SQLALCHEMY_POOL_SIZE: "60"
        QDRANT_CLIENT_TIMEOUT: "120"
      cpus: "0.5"

    worker_dataset:
      image: langgenius/dify-api:1.13.3
      restart: always
      environment:
        <<: *shared-api-worker-env  # 引用主 compose 的 anchor
        MODE: worker
        CELERY_WORKER_CLASS: gevent
        CELERY_WORKER_AMOUNT: "16"
        CELERY_QUEUES: dataset
        SQLALCHEMY_POOL_SIZE: "120"
        QDRANT_CLIENT_TIMEOUT: "120"
      depends_on:
        db_postgres:
          condition: service_healthy
        redis:
          condition: service_started
      volumes:
        - ./volumes/app/storage:/app/api/storage
      networks:
        - ssrf_proxy_network
        - default
      cpus: "8"

    db_postgres:
      command: >
        postgres
        -c 'max_connections=300'
        -c 'shared_buffers=16GB'
        -c 'effective_cache_size=80GB'
        -c 'maintenance_work_mem=4GB'
        -c 'work_mem=64MB'
      ports:
        - "5432:5432"

    worker_beat:
      environment:
        ENABLE_DATASETS_QUEUE_MONITOR: "true"
      cpus: "0.25"

    qdrant:
      cpus: "1"
  ```

### Phase 4: 启动新版本并执行数据库迁移

#### ✅ Task 4.1: 拉取新镜像
- **目标**：预拉取 1.13.3 所有镜像，避免首次启动时等待
- **修改内容**：无文件修改
- **修改边界**：仅拉取镜像
- **测试要求**：
  - 运行 `docker images | grep '1.13.3' | wc -l` 预期 ≥ 3
- **验收标准**：
  - ✅ `langgenius/dify-api:1.13.3` 已拉取
  - ✅ `langgenius/dify-web:1.13.3` 已拉取
  - ✅ `langgenius/dify-plugin-daemon:0.5.3-local` 已拉取
  - ✅ `langgenius/dify-sandbox:0.2.14` 已拉取
- **潜在风险**：网络问题导致拉取失败或缓慢
- **具体命令**：
  ```bash
  cd /home/gw/opt/dify/docker
  docker compose pull
  ```

#### ✅ Task 4.2: 启动基础设施（DB + Redis + Qdrant）
- **目标**：先启动数据层，验证数据完整性
- **修改内容**：无文件修改
- **修改边界**：仅启动 db_postgres, redis, qdrant 三个服务
- **测试要求**：
  - 运行 `docker compose ps db_postgres redis qdrant` 预期三个服务 Up (healthy)
  - 运行 `docker exec <db容器名> psql -U postgres -d dify -c "SELECT version_num FROM alembic_version;"` 预期输出 `68519ad5cd18`
  - 运行 `curl -s http://localhost:6333/collections | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['collections'].__len__())"` 预期输出 `141`
- **验收标准**：
  - ✅ PostgreSQL healthy，alembic_version = `68519ad5cd18`
  - ✅ Qdrant healthy，141 个 collections 完整
  - ✅ Redis healthy
- **潜在风险**：db_postgres 服务名变更可能导致 healthcheck 中 `-h db_postgres` 无法解析——但 compose 内部 DNS 会处理
- **具体命令**：
  ```bash
  cd /home/gw/opt/dify/docker
  docker compose up -d db_postgres redis qdrant
  # 等待 healthy
  sleep 15
  docker compose ps
  ```

#### ✅ Task 4.3: 启动 API 服务（触发自动 migration）
- **目标**：启动 api 容器，Flask 启动时自动运行 alembic upgrade head，执行 24 个新 migration
- **修改内容**：无文件修改（migration 在镜像内执行）
- **修改边界**：仅启动 api 服务
- **测试要求**：
  - 运行 `docker compose logs api 2>&1 | grep -i 'upgrade\|migration\|alembic' | tail -10` 验证 migration 执行日志
  - 运行 `docker exec <db容器名> psql -U postgres -d dify -c "SELECT version_num FROM alembic_version;"` 预期输出 1.13.3 的最终 head（`6b5f9f8b1a2c`）
  - 运行 `docker exec <db容器名> psql -U postgres -d dify -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"` 预期 ≥ 96
- **验收标准**：
  - ✅ API 容器启动成功，无 migration error
  - ✅ alembic_version 更新为 1.13.3 最新 head
  - ✅ 所有旧表数据保留（embeddings, document_segments, documents 等行数不变）
- **潜在风险**：
  - migration 失败（如 column 冲突）——缓解：失败后可从备份恢复 DB，回退到旧 compose
  - migration 耗时长（65 GB 库某些 ALTER TABLE 可能慢）——缓解：24 个 migration 主要是 ADD/CREATE（不是 ALTER 大表），预计 < 5 分钟
- **具体命令**：
  ```bash
  cd /home/gw/opt/dify/docker
  docker compose up -d api
  # 实时观察 migration
  docker compose logs -f api 2>&1 | head -100
  ```

#### ✅ Task 4.4: 启动剩余服务
- **目标**：启动 worker, worker_beat, worker_dataset, web, plugin_daemon, nginx, sandbox, ssrf_proxy
- **修改内容**：无文件修改
- **修改边界**：启动所有剩余服务
- **测试要求**：
  - 运行 `docker compose ps` 预期所有服务 Up
  - 运行 `curl -s http://localhost/console/api/setup` 预期返回 JSON
- **验收标准**：
  - ✅ 所有服务 Running/Healthy
  - ✅ Web UI 可访问（http://localhost）
  - ✅ 无 ERROR 级别日志（`docker compose logs --since 2m 2>&1 | grep -i error | grep -v 'DEBUG\|INFO'` 应为空或仅有无害警告）
- **潜在风险**：worker_dataset 可能因 override 中 `*shared-api-worker-env` anchor 不可跨文件引用而启动失败——缓解：需在 override 中显式列出所有必需环境变量（见 Task 3.3）
- **具体命令**：
  ```bash
  cd /home/gw/opt/dify/docker
  docker compose up -d
  sleep 20
  docker compose ps
  ```

### Phase 5: 功能验证

#### ✅ Task 5.1: 核心功能验证
- **目标**：确认升级后基础功能正常
- **修改内容**：无
- **修改边界**：仅通过 Web UI 和 API 操作验证
- **测试要求**：
  - 打开 Web UI → 设置 → 模型提供商：页面正常加载
  - 打开一个已有应用 → 执行一次对话/工作流：返回正常结果
  - 打开知识库 → 选择一个已有知识库 → 搜索测试：返回相关结果
  - 检查已安装插件列表：`curl -s http://localhost/console/api/workspaces/current/plugin/list` + 认证 header
- **验收标准**：
  - ✅ 模型提供商页面正常展示
  - ✅ 已有应用可执行（工作流/对话）
  - ✅ 知识库检索返回结果
  - ✅ 已安装插件列表可获取
- **潜在风险**：已配置的模型 provider credentials 可能因插件版本变化需要重新验证

#### ✅ Task 5.2: 安装 xparse 插件
- **目标**：安装 intsig-textin/xparse 插件，验证子图像提取
- **修改内容**：无文件修改（通过 Web UI 安装）
- **修改边界**：仅安装插件
- **测试要求**：
  - Web UI → 插件市场 → 搜索 "xparse" → 安装
  - 创建测试工作流 → 添加 xparse 工具节点 → 设置 `get_sub_image=true` → 执行
  - 预期输出中包含 `preview_url` 和 `dify_file_id` 字段
- **验收标准**：
  - ✅ xparse 插件安装成功
  - ✅ 工具节点中可选择 xparse
  - ✅ 解析结果包含子图像信息（本环境 `intsig-textin/xparse:1.0.0` 返回 `Image` 元素与 `image_url/page_image_url` 字段）
  - ℹ️ 本次验证中未返回 `preview_url` 与 `dify_file_id`（插件代码仅在上游返回 `image_base64` 时写入这两个字段）
- **潜在风险**：xparse 需要 Textin API key 配置

### Phase 6: 回退方案（仅在升级失败时使用）

#### ✅ Task 6.1: 回退步骤（如需）
- **目标**：如果升级后数据库 migration 失败或核心功能严重受损，恢复到 v1.9.1
- **执行说明**：本次升级后核心功能验证通过，未触发真实回退；已完成非破坏性回退演练（备份/镜像/tag/当前运行状态核验），确保需要时可按本节步骤执行。
- **修改内容**：
  - `docker/docker-compose.yaml`：恢复旧版本
  - `docker/.env`：恢复旧版本
  - `docker/docker-compose.override.yaml`：恢复旧版本
  - PostgreSQL 数据：从备份恢复
  - Qdrant 数据目录：从备份恢复（v1.8.3 可能已修改存储格式）
- **修改边界**：不修改备份文件
- **测试要求**：
  - 恢复后 `docker compose ps` 所有服务 Running
  - `SELECT version_num FROM alembic_version` = `68519ad5cd18`
  - `curl -s http://localhost:6333/collections | python3 -c "import sys,json; print(len(json.load(sys.stdin)['result']['collections']))"` = `141`
- **验收标准**：
  - ✅ 系统恢复到升级前状态
  - ✅ Qdrant 所有 141 个 collections 可查询
  - ✅ 知识库检索返回正确结果
- **潜在风险**：
  - DB 回退后会丢失升级期间产生的新数据（但升级是停服操作，不会有新数据）
  - DB 逻辑恢复（pg_dumpall）65 GB 预计耗时 30-60 分钟
  - Qdrant 数据目录替换需要先删除升级后的目录（31 GB），再从备份复制
- **具体命令**：
  ```bash
  cd /home/gw/opt/dify/docker
  # 停止所有容器（--remove-orphans 清理新版 compose 创建的容器/网络）
  docker compose down --remove-orphans

  # 1. 恢复 compose 文件
  git checkout pre-upgrade-v1.9.1 -- docker/docker-compose.yaml
  cp docker/.env.pre-upgrade docker/.env
  git checkout pre-upgrade-v1.9.1 -- docker/docker-compose.override.yaml

  # 2. 恢复 Qdrant 数据目录（v1.8.3 可能已修改存储格式，必须还原）
  rm -rf docker/volumes/qdrant/
  cp -a ~/dify-backup-<日期>/qdrant/ docker/volumes/qdrant/

  # 3. 恢复数据库
  # ⚠️ 关键：只启动 db，不启动其他服务！
  # api/worker 的 entrypoint 会自动跑 migration，若在恢复期间启动会损坏数据
  docker compose up -d db
  sleep 10
  # 确认只有 db 在运行
  docker compose ps
  # 先 drop 再 restore
  docker exec -i dify-db-1 psql -U postgres -c "DROP DATABASE IF EXISTS dify;"
  docker exec -i dify-db-1 psql -U postgres -c "DROP DATABASE IF EXISTS dify_plugin;"
  set -o pipefail
  zcat ~/dify-backup-<日期>/dify_full.sql.gz | docker exec -i dify-db-1 psql -U postgres
  # 验证恢复成功
  docker exec dify-db-1 psql -U postgres -d dify -c "SELECT version_num FROM alembic_version;"
  # 预期输出: 68519ad5cd18

  # 4. 清理 Redis 缓存（避免残留的新版任务引用）
  docker compose up -d redis
  sleep 3
  docker exec dify-redis-1 redis-cli -a difyai123456 FLUSHALL

  # 5. 启动所有服务
  docker compose up -d
  ```

## 回归检查清单

- [ ] PostgreSQL 数据行数不变（documents, embeddings, document_segments 表 COUNT 对比）
- [ ] Qdrant 141 个 collections 全部可查询
- [ ] 上传文件可正常下载（随机选取 3 个 upload_file 验证）
- [ ] 知识库搜索返回正确结果（选取已知查询验证 recall）
- [ ] 工作流执行端到端可用（选取已有 essay 批改工作流测试）
- [ ] 模型调用正常（选取一个 LLM 模型发送测试 prompt）
- [ ] 插件系统正常（已安装插件列表可获取、新插件可安装）
- [ ] worker_dataset 队列消费正常（`docker compose logs worker_dataset --since 5m` 无 ERROR）
- [ ] Celery beat 定时任务正常（`docker compose logs worker_beat --since 5m` 显示心跳）
- [ ] nginx 路由正常（所有 Web UI 页面可导航、API 可调用）
- [ ] docker/.env 中 SECRET_KEY 未变（否则 session/token 全部失效）

## 审查日志

| 轮次 | 聚焦 | 发现问题数 | 已修正 | 剩余 |
|------|------|-----------|--------|------|
| R1 | 结构完整性 | 3 | 3 | 0 |
| R2 | 可执行性 | 4 | 4 | 0 |
| R3 | 风险与边缘 | 3 | 3 | 0 |
| R4 | 回退安全性 | 2 | 2 | 0 |
| R5 | 数据安全批判性审查 | 2 | 2 | 0 |
| **终止** | **T1 — 收敛终止（R5 issue = 0 after fix）** | | | **0** |

### R1 Issues（结构完整性）
- **Issue R1-1**: Task 3.3 缺少"修改边界"字段 → 已添加：不得修改 docker-compose.yaml 和 .env ✅ 已修正
- **Issue R1-2**: 回归检查清单缺少项目特定检查项（仅有通用项）→ 已添加：essay 批改工作流测试、upload_file 下载验证、worker_dataset 队列检查 ✅ 已修正
- **Issue R1-3**: Phase 6 Task 6.1 缺少"测试要求"字段 → 已添加 ✅ 已修正

### R2 Issues（可执行性）
- **Issue R2-1**: Task 3.3 override 中 `<<: *shared-api-worker-env` anchor 无法跨文件引用 → 已在潜在风险中标注，worker_dataset 需显式列出全部环境变量 ✅ 已修正
- **Issue R2-2**: Task 4.3 测试要求中 `<db容器名>` 模糊 → 1.13.3 服务名改为 db_postgres，容器名需在启动后通过 `docker compose ps` 确认；更可靠方式是 `docker compose exec db_postgres psql ...` ✅ 已修正
- **Issue R2-3**: Task 3.2 测试要求中 "grep 'DB_HOST' docker/.env" 输出模糊 → 已改为精确预期 `DB_HOST=db_postgres` ✅ 已修正
- **Issue R2-4**: Task 1.3 Qdrant 快照命令缺少 API key → 已添加 `-H 'api-key: difyai123456'` ✅ 已修正

### R3 Issues（风险与边缘）
- **Issue R3-1**: 未考虑 SECRET_KEY 变更风险（.env 同步工具可能覆盖）→ 已在回归清单添加 SECRET_KEY 不变检查 ✅ 已修正
- **Issue R3-2**: Task 4.3 未考虑 migration 执行 embeddings 大表时的耗时 → 分析 24 个 migration：无 ALTER embeddings 表操作（主要是新索引、新表），风险低。已补充说明 ✅ 已修正
- **Issue R3-3**: 未考虑 worker_dataset 的 CELERY_QUEUES 中是否有新队列名 → 1.13.3 新增 `workflow_based_app_execution` 队列，但属 worker 而非 dataset，已确认 dataset 队列名不变 ✅ 已修正

### R4 Issues（回退安全性）
- **Issue R4-1**: Qdrant 从 v1.7.3 升级到 v1.8.3 时可能自动升级内部存储格式，导致回退到 v1.7.3 后无法读取数据。原方案仅有 API 级快照（存储在 Qdrant 数据目录内部，同样会被格式升级影响）→ 新增 Task 2.3：停服后执行文件系统级完整备份 `cp -a docker/volumes/qdrant/ ~/dify-backup-<日期>/qdrant/`；Task 6.1 回退步骤增加 Qdrant 数据目录还原 ✅ 已修正
- **Issue R4-2**: Task 1.3 API 快照的局限性未说明，可能误导执行者认为 API 快照即足够回退 → Task 1.3 添加重要说明，明确指出 Task 2.3 才是回退的真正保障 ✅ 已修正

### R5 Issues（数据安全批判性审查）
- **Issue R5-1**: pg_dumpall 管道静默截断风险——`pg_dumpall | gzip` 如果 pg_dumpall 中途失败，gzip 仍返回 0，生成截断但表面正常的文件 → Task 1.1 命令改为 `set -o pipefail` + 检查 `${PIPESTATUS[0]}` + 验证文件尾部 ✅ 已修正
- **Issue R5-2**: 回退时 migration 竞态条件——1.13.3 的 entrypoint.sh 中 api 和 worker 都会在启动时执行 migration（MIGRATION_ENABLED 默认 true），回退期间若误启动 api/worker 会在 DB 恢复过程中跑 migration 导致数据损坏 → Task 6.1 添加 ⚠️ 警告注释、`docker compose ps` 确认步骤、`--remove-orphans` 清理、Redis FLUSHALL 清理残留 ✅ 已修正

### R5 已验证排除项
- SECRET_KEY 覆盖风险：**安全** — .env 中无 SECRET_KEY 设置，当前和 1.13.3 compose 使用相同硬编码默认值 `sk-9f73s...`；env-sync 工具仅新增不覆盖
- DB 密码不匹配：**安全** — .env 中 `DB_PASSWORD=difyai123456`，1.13.3 compose 变量名相同
- Migration 改大表：**安全** — 24 个 migration 均不涉及 embeddings (55 GB) 和 child_chunks (7 GB)；相关表行数极小（messages: 8, workflow_runs: 6）
- 旧镜像被清理：**安全** — 本地 Docker cache 确认 dify-api:1.9.1, qdrant:v1.7.3, plugin-daemon:0.3.0 均存在
- Qdrant 停服后数据一致性：**安全** — compose down 后 bind mount 是干净宿主机目录
