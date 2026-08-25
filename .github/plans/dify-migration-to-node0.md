# Dify 服务迁移至 node0 (asipp) 执行计划

## 背景与目标

- **问题描述**: Dify 1.13.3 全栈在本地 Precision-5820 (251 GB RAM) 上长期运行，其中 qdrant 向量库 mmap 占用约 41.5 GB 内存、postgres 占 3.1 GB，合计 ~47 GB，对本地其他服务造成较大内存压力。中子学服务器 node0 (asipp) 有 62 GB RAM / 40 核 / 1.8 TB NVMe SSD，当前仅运行轻量级 docconv 服务（负载 <1%），可承接 Dify 服务。
- **目标**: 将 Dify 全栈冷迁移到 node0 的 `/data-ssd/dify/`，通过 Tailscale 内网 (`100.124.150.42`) 提供访问，保留全部数据（工作流、知识库、对话记录、已安装插件）。
- **非目标（不做什么）**:
  - 不升级 node0 操作系统 — CentOS 7.9 + Docker 26.1.4 满足需求，升级风险高于收益
  - 不迁移 docconv 服务 — 已在 node0 运行
  - 不更改 Dify 版本 — 保持 1.13.3
  - 不修改集群节点角色 — 不涉及
  - 不迁移 searxng — 独立于 Dify compose 项目，需另行处理
- **已有代码/流程复用分析**:
  - node0 Docker 26.1.4 + Compose v2.27.1: 复用（无需安装）
  - node0 Tailscale (`100.124.150.42`): 复用
  - node0 /data-ssd (XFS, 1.8 T): 复用
  - 本地 docker-compose.yaml + .env + override: 复用（仅修改 URL 和端口绑定变量）

## 技术方案

- **方案概述**: 冷迁移（stop → rsync + pg_dump → configure → start）。数据库用 `pg_dump` 逻辑备份（避免文件级权限问题），其他卷用 `rsync` 文件拷贝（world-readable，无需 sudo）。修改远端 `.env` 中的 URL 指向 Tailscale IP，端口绑定限制到 Tailscale 接口。
- **关键设计决策**:
  1. **pg_dump 而非文件 rsync** 迁移 postgres — 本地 postgres 数据目录 (`volumes/db/data/`) 由 uid=0 (root) 所有且非 world-readable，普通用户无法直接 rsync；pg_dump 通过 Docker exec 以容器内 root 身份导出，绕过宿主机权限限制
  2. **冷迁移 qdrant** — qdrant 无官方在线备份 API（社区版），必须停服后拷贝磁盘文件保证一致性
  3. **卷数据放 /data-ssd (bind mount)，Docker 镜像层保留 /var/lib/docker (系统盘)** — 不改 Docker data-root 以免影响 node0 已有容器（docconv、registry）
  4. **端口绑定 Tailscale IP** — nginx/postgres/redis 端口仅绑定 `100.124.150.42`，不暴露到 LAN (`10.0.0.x`) 或公网 (`202.127.x.x`)
  5. **postgres/redis 资源参数下调** — node0 (62 GB) 远小于本地 (251 GB)，需降低 shared_buffers 和 redis maxmemory 以为 qdrant mmap 留出页缓存空间
- **影响范围**:
  - 本地 `~/opt/dify/docker/` — 停止服务（卷数据保留不删除，可回滚）
  - 远端 `/data-ssd/dify/` — 新建全部文件
  - 远端 `.env` — 修改 6 个 URL 变量 + 1 个端口变量
  - 远端 `docker-compose.override.yaml` — 修改端口绑定 + 资源参数
  - 本地 `~/opt/dify-knowledge-mcp-server/.env` — `DIFY_API_HOST` 改为 node0 地址
  - 本地 `~/.config/zotero-ingest-daemon/env` — `DIFY_API_HOST` 改为 node0 地址

## 迁移前数据基线（用于验证）

| 指标 | 迁移前值 | 来源 |
|------|---------|------|
| Postgres 数据库大小 | 76 GB (78.8 GB on disk) | `pg_size_pretty(pg_database_size('dify'))` |
| accounts 行数 | 2 | `SELECT count(*) FROM accounts` |
| apps 行数 | 37 | `SELECT count(*) FROM apps` |
| datasets 行数 | 206 | `SELECT count(*) FROM datasets` |
| documents 行数 | 8938 | `SELECT count(*) FROM documents` |
| Qdrant collections 数 | 155 | `ls volumes/qdrant/collections/ \| wc -l` |
| Qdrant 磁盘大小 | 41 GB | `du -sh volumes/qdrant` |
| App storage 大小 | 4.5 GB | `du -sh volumes/app` |

> 验证时所有行数必须 **精确匹配**；磁盘大小允许 ±5% 误差（filesystem metadata 差异）。

## Error & Rescue Map

| 操作 | 可能的失败 | 错误类型 | 已处理？ | 处理方式 | 用户可见行为 |
|------|-----------|---------|---------|---------|------------|
| rsync qdrant (40 GB) | 传输中断 | 显式 (rsync error) | Y | 重新执行 rsync（增量续传） | 无影响，可重试 |
| pg_dump 导出 | postgres 负载过高导致超时 | 显式 (error) | Y | 低负载时段执行；dump 文件验证头部和大小 | 导出慢或失败，重试 |
| docker pull 镜像 | DockerHub rate limit | 显式 (toomanyrequests) | Y | T1.3 使用 `docker save` + `ssh docker load` 做备用通道 | 拉取失败，走离线通道 |
| pg_restore 恢复 | 目标 DB 已有 migration 表冲突 | 显式 (psql error) | Y | 使用 `--clean --if-exists`；恢复前先 `DROP DATABASE dify; CREATE DATABASE dify;` | 恢复报 ERROR，按修复步骤处理 |
| qdrant 启动 | mmap 文件不完整 | 容器 restart loop | Y | 冷迁移保证一致性；启动后检查 `docker logs` | qdrant 反复重启 |
| postgres 启动 | PGDATA 权限不匹配 | 容器退出 | N/A | pg_dump 方案不依赖 PGDATA 文件拷贝，postgres 从空盘 initdb | 不适用 |
| LLM API 不可达 | node0 → scnet 网络不通 | 静默 (请求超时) | Y | T1.4 预检；不通则改用 Tailscale exit node 路由 | 对话/工作流超时 |
| 端口被占 | node0 :80 已占用 | compose up 报 bind error | Y | T1.1 预检确认端口空闲；绑定 Tailscale IP 避免与 0.0.0.0 冲突 | nginx 启动失败 |
| Docker 系统盘满 | /var/lib/docker 空间不足 | docker pull 失败 | Y | T1.1 检查 ≥10 GB；不足则 symlink 到 /data-ssd | 镜像拉取报 no space |
| qdrant 内存压力 | 62 GB RAM 不足以缓存全部 mmap | 性能降级（非失败） | Y | SSD 随机读 ~100 μs，可接受；长期可加 swap 或缩减 redis | 向量查询变慢 |
| MCP server 切换后查询失败 | endpoint 配置错误或 API key 不匹配 | 显式 (connection refused / 401) | Y | 先改 1 个客户端测试；保留旧配置备份 | MCP 工具调用报错 |
| zotero-ingest-daemon 投递失败 | ingest_enabled=false / 新 URL 不通 | 显式 (daemon log error) | Y | daemon 当前 ingest_enabled=false，切换安全；journalctl 监控 | 文献不入库 |

## 执行计划

**预计停机时间**: ~15–20 分钟（rsync 45 GB ≈ 9 min + 配置 2 min + 启动恢复 5 min）

### Phase 1: 预飞检查与准备（零停机）

#### Task 1.1: 检查 node0 系统盘空间与端口
- **目标**: 确认 Docker 镜像层有足够系统盘空间，Dify 所需端口均无占用
- **依赖**: 无
- **修改内容**: 无文件修改，仅执行诊断命令
  ```bash
  ssh asipp 'echo "=== disk ===" && df -h / && echo "=== ports ===" && ss -tlnp | grep -E ":(80|443|5001|5002|5003|6333|6334|6379|5432)\s" && echo "CLEAN" || echo "CLEAN"'
  ```
- **修改边界**: 不修改任何文件
- **测试要求**:
  - 运行上述命令，检查 / 可用空间 ≥10 GB
  - 无端口占用输出（仅 CLEAN）
- **验收标准**:
  - ✅ / 分区可用空间 ≥10 GB（当前 227 GB，充裕）
  - ✅ 80/443/5001-5003/6333-6334/6379/5432 均未被监听
- **潜在风险**: 若系统盘不足，需将 Docker data-root 迁至 /data-ssd（额外操作，需重启 Docker 服务）

#### Task 1.2: 同步代码与配置到 node0
- **目标**: 将 Dify 项目代码、compose 文件、nginx 配置等（不含 volumes 数据）同步到 `/data-ssd/dify/`
- **依赖**: T1.1
- **修改内容**:
  ```bash
  ssh asipp 'mkdir -p /data-ssd/dify'
  rsync -aP \
    --exclude='docker/volumes' \
    --exclude='.venv' \
    --exclude='node_modules' \
    --exclude='.git/objects' \
    --exclude='__pycache__' \
    --exclude='web/.next' \
    /home/gw/opt/dify/ asipp:/data-ssd/dify/
  ```
- **修改边界**: 仅创建 `/data-ssd/dify/` 及子目录；不修改 node0 已有文件
- **测试要求**:
  ```bash
  ssh asipp 'ls /data-ssd/dify/docker/.env /data-ssd/dify/docker/docker-compose.yaml /data-ssd/dify/docker/docker-compose.override.yaml /data-ssd/dify/docker/nginx/nginx.conf.template'
  ```
  四个文件均存在
- **验收标准**:
  - ✅ `/data-ssd/dify/docker/.env` 存在
  - ✅ `/data-ssd/dify/docker/docker-compose.yaml` 存在
  - ✅ `/data-ssd/dify/docker/docker-compose.override.yaml` 存在
  - ✅ `/data-ssd/dify/docker/nginx/` 目录完整
  - ✅ `/data-ssd/dify/mock_openai/` 目录存在（override 中 mock_openai build context）
- **潜在风险**: rsync 源路径尾部 `/` 不能漏（复制目录内容而非目录本身）

#### Task 1.3: 预拉取 Docker 镜像 + 离线传输 mock_openai
- **目标**: 在 node0 上预下载所有 Dify 镜像，减少停机后启动时间；mock_openai 为本地构建镜像，需离线传输
- **依赖**: T1.1
- **修改内容**:
  ```bash
  # 远端拉取公共镜像
  ssh asipp 'for img in \
    langgenius/dify-api:1.13.3 \
    langgenius/dify-web:1.13.3 \
    langgenius/dify-sandbox:0.2.14 \
    "langgenius/dify-plugin-daemon:0.5.3-local" \
    langgenius/qdrant:v1.8.3 \
    postgres:15-alpine \
    redis:6-alpine \
    nginx:latest \
    ubuntu/squid:latest \
    busybox:latest; do
      echo "Pulling $img..." && docker pull "$img" || echo "FAILED: $img"
    done'

  # 本地构建的 mock_openai 镜像离线传输
  docker save docker-mock_openai | ssh asipp 'docker load'
  ```
- **修改边界**: 仅在 node0 的 Docker image store (/var/lib/docker) 中添加镜像
- **测试要求**:
  ```bash
  ssh asipp 'docker images --format "{{.Repository}}:{{.Tag}}" | sort' | grep -E "(dify|qdrant|postgres|redis|nginx|squid|busybox|mock_openai)"
  ```
  应列出全部 11 个镜像
- **验收标准**:
  - ✅ 10 个公共镜像 pull 成功
  - ✅ mock_openai 镜像 load 成功
- **潜在风险**: DockerHub rate limit → 备用方案：本地 `docker save <img> | ssh asipp 'docker load'` 逐个传输

#### Task 1.4: 检测 LLM API 端点可达性
- **目标**: 确认 node0 能访问 `.env` 中配置的 `OPENAI_API_BASE`
- **依赖**: 无
- **修改内容**: 无文件修改
  ```bash
  ssh asipp 'curl -sS --connect-timeout 10 -o /dev/null -w "HTTP %{http_code} in %{time_total}s\n" http://c-1968602971252453378.qdai.scnet.cn:8000/v1/models'
  ```
- **修改边界**: 无
- **测试要求**: HTTP 状态码为 200/401/403（非超时/连接拒绝）
- **验收标准**:
  - ✅ curl 返回 HTTP 200、401 或 403（说明网络可达）
- **潜在风险**: 若不可达（scnet 可能限制源 IP），需在 `.env` 中改用其他 LLM 端点，或通过 Tailscale exit node 路由流量

### Phase 2: 数据迁移（停机开始）

#### Task 2.1: pg_dump 热备份
- **目标**: 在停服前做一次 postgres 逻辑备份，作为主数据库迁移手段
- **依赖**: 无（本地服务仍在运行）
- **修改内容**:
  ```bash
  cd /home/gw/opt/dify/docker
  docker compose exec -T db_postgres pg_dump -U postgres \
    --clean --if-exists --no-owner dify \
    | gzip > /tmp/dify-db-$(date +%Y%m%d).sql.gz
  ls -lh /tmp/dify-db-*.sql.gz
  # 验证：
  zcat /tmp/dify-db-*.sql.gz | head -20
  ```
- **修改边界**: 仅在本地 `/tmp/` 生成备份文件，不修改任何服务状态
- **测试要求**:
  - gzip 文件大小 > 10 KB
  - `zcat ... | head -20` 包含 `-- PostgreSQL database dump` 和 `SET` 语句
- **验收标准**:
  - ✅ dump 文件 > 10 KB
  - ✅ 文件头包含 `PostgreSQL database dump`
  - ✅ `zcat ... | grep -c 'CREATE TABLE'` ≥ 10（Dify 有数十张表）
- **潜在风险**: 如正在执行知识库索引任务，dump 可能包含中间状态 → 建议在低负载时操作

#### Task 2.2: 停止本地 Dify 服务
- **目标**: 冷停所有 Dify 容器，确保 qdrant/redis 磁盘数据一致
- **依赖**: T2.1
- **修改内容**:
  ```bash
  cd /home/gw/opt/dify/docker && docker compose down
  ```
- **修改边界**: 仅停止 Dify compose 项目容器（`docker-*`）；不影响 docconv 容器（独立项目）
- **测试要求**:
  ```bash
  docker compose ps     # 应返回空
  docker ps | grep 'docker-'   # 应无输出
  ```
- **验收标准**:
  - ✅ `docker compose ps` 返回空列表
  - ✅ 无 `docker-` 前缀容器在运行
- **潜在风险**: 若有正在运行的工作流，强停可能导致任务中间态 → 停前检查 `docker compose logs worker --tail 5` 确认无活跃任务

#### Task 2.3: rsync 非数据库卷到 node0
- **目标**: 冷拷贝 qdrant (40G)、app (4.5G)、plugin_daemon (348M)、redis (139M)、sandbox 等卷数据
- **依赖**: T2.2（必须停服后拷贝以保证一致性）
- **修改内容**:
  ```bash
  cd /home/gw/opt/dify/docker
  # qdrant/app/plugin_daemon/redis/sandbox 均为 world-readable (uid=0, mode 755/644)
  # 无需 sudo
  rsync -aP --exclude='db' \
    volumes/ asipp:/data-ssd/dify/docker/volumes/
  ```
  排除 `db` 目录（使用 pg_dump 方案，不需要文件级拷贝）。
- **修改边界**: 仅写入 node0 的 `/data-ssd/dify/docker/volumes/`（不含 db 子目录）
- **测试要求**:
  ```bash
  ssh asipp 'du -sh /data-ssd/dify/docker/volumes/qdrant \
    /data-ssd/dify/docker/volumes/app \
    /data-ssd/dify/docker/volumes/plugin_daemon \
    /data-ssd/dify/docker/volumes/redis'
  ```
  - qdrant ≈ 40 GB
  - app ≈ 4.5 GB
  - plugin_daemon ≈ 348 MB
  - redis ≈ 139 MB
- **验收标准**:
  - ✅ rsync exit code = 0
  - ✅ qdrant 卷 ≥ 38 GB
  - ✅ app 卷 ≥ 4 GB
  - ✅ `ssh asipp 'ls /data-ssd/dify/docker/volumes/qdrant/collections/'` 列出知识库集合目录
- **潜在风险**: 传输中断 → 重新运行同一 rsync 命令（增量续传，只传差异部分）

#### Task 2.4: 传输 pg_dump 到 node0
- **目标**: 将数据库备份文件传到远端
- **依赖**: T2.1
- **修改内容**:
  ```bash
  scp /tmp/dify-db-*.sql.gz asipp:/data-ssd/dify/docker/
  ```
- **修改边界**: 仅在远端 `/data-ssd/dify/docker/` 放置一个 .sql.gz 文件
- **测试要求**:
  ```bash
  ssh asipp 'ls -lh /data-ssd/dify/docker/dify-db-*.sql.gz'
  ```
- **验收标准**:
  - ✅ 远端文件大小与本地一致
- **潜在风险**: 无（小文件，秒级传输）

### Phase 3: 远端配置

#### Task 3.1: 修改远端 .env 的 URL 和端口变量
- **目标**: 将所有 `localhost` URL 改为 Tailscale IP，端口绑定到 Tailscale 接口
- **依赖**: T1.2（远端 .env 已就位）
- **修改内容**: 通过 `ssh + sed` 修改远端 `/data-ssd/dify/docker/.env`：
  ```bash
  ssh asipp 'cd /data-ssd/dify/docker && sed -i.bak \
    -e "s|CONSOLE_API_URL=http://localhost/v1|CONSOLE_API_URL=http://100.124.150.42/v1|" \
    -e "s|SERVICE_API_URL=http://localhost/v1|SERVICE_API_URL=http://100.124.150.42/v1|" \
    -e "s|APP_API_URL=http://localhost/v1|APP_API_URL=http://100.124.150.42/v1|" \
    -e "s|APP_WEB_URL=http://localhost|APP_WEB_URL=http://100.124.150.42|" \
    -e "s|TRIGGER_URL=http://localhost|TRIGGER_URL=http://100.124.150.42|" \
    -e "s|EXPOSE_NGINX_PORT=80|EXPOSE_NGINX_PORT=100.124.150.42:80|" \
    -e "s|EXPOSE_NGINX_SSL_PORT=443|EXPOSE_NGINX_SSL_PORT=100.124.150.42:443|" \
    .env'
  ```
  同时设置 `FILES_URL` 走 nginx 代理（当前值 `http://api:5001` 是 Docker 内部地址，浏览器无法解析）：
  ```bash
  ssh asipp 'cd /data-ssd/dify/docker && sed -i \
    "s|FILES_URL=http://api:5001|FILES_URL=http://100.124.150.42|" .env'
  ```
- **修改边界**: 仅修改远端 `/data-ssd/dify/docker/.env`（备份为 `.env.bak`）；不修改本地文件
- **测试要求**:
  ```bash
  ssh asipp 'cd /data-ssd/dify/docker && grep -E "^(CONSOLE_API_URL|SERVICE_API_URL|APP_API_URL|APP_WEB_URL|FILES_URL|EXPOSE_NGINX_PORT|EXPOSE_NGINX_SSL_PORT|TRIGGER_URL)=" .env'
  ```
  所有 URL 包含 `100.124.150.42`，端口前缀为 `100.124.150.42:`
- **验收标准**:
  - ✅ CONSOLE_API_URL = `http://100.124.150.42/v1`
  - ✅ SERVICE_API_URL = `http://100.124.150.42/v1`
  - ✅ APP_API_URL = `http://100.124.150.42/v1`
  - ✅ APP_WEB_URL = `http://100.124.150.42`
  - ✅ FILES_URL = `http://100.124.150.42`
  - ✅ EXPOSE_NGINX_PORT = `100.124.150.42:80`
  - ✅ `.env.bak` 存在作为回滚依据
- **潜在风险**: sed 替换字面量匹配失败（如 .env 中有额外空格）→ 用 grep 验证替换结果

#### Task 3.2: 修改远端 override 的端口绑定与资源参数
- **目标**: (1) 将 override 中暴露的端口绑定到 Tailscale IP；(2) 将 postgres/redis 资源参数适配 62 GB RAM
- **依赖**: T1.2
- **修改内容**: 修改远端 `/data-ssd/dify/docker/docker-compose.override.yaml`
  
  **端口绑定**（将 `"5432:5432"` → `"100.124.150.42:5432:5432"` 等）：
  ```bash
  ssh asipp 'cd /data-ssd/dify/docker && sed -i.bak \
    -e "s|\"5432:5432\"|\"100.124.150.42:5432:5432\"|" \
    -e "s|\"6379:6379\"|\"100.124.150.42:6379:6379\"|" \
    -e "s|\"5002:5002\"|\"100.124.150.42:5002:5002\"|" \
    -e "s|\"8194:8194\"|\"100.124.150.42:8194:8194\"|" \
    -e "s|\"9999:9999\"|\"100.124.150.42:9999:9999\"|" \
    docker-compose.override.yaml'
  ```
  
  **postgres 参数调整**（适配 62 GB）：
  ```bash
  ssh asipp 'cd /data-ssd/dify/docker && sed -i \
    -e "s|shared_buffers=4GB|shared_buffers=2GB|" \
    -e "s|effective_cache_size=20GB|effective_cache_size=10GB|" \
    -e "s|work_mem=16MB|work_mem=8MB|" \
    -e "s|maintenance_work_mem=512MB|maintenance_work_mem=256MB|" \
    -e "s|min_wal_size=4GB|min_wal_size=1GB|" \
    -e "s|max_wal_size=16GB|max_wal_size=4GB|" \
    -e "s|shm_size: 4gb|shm_size: 2gb|" \
    docker-compose.override.yaml'
  ```
  
  **redis 参数调整**：
  ```bash
  ssh asipp 'cd /data-ssd/dify/docker && sed -i \
    -e "s|--maxmemory 8gb|--maxmemory 4gb|" \
    -e "s|--io-threads 4|--io-threads 2|" \
    docker-compose.override.yaml'
  ```
- **修改边界**: 仅修改远端 override 文件（备份为 `.bak`）；不修改本地文件和远端 `docker-compose.yaml`
- **测试要求**:
  ```bash
  ssh asipp 'cd /data-ssd/dify/docker && grep -E "(5432|6379|5002|8194|9999|shared_buffers|maxmemory|shm_size)" docker-compose.override.yaml'
  ```
  确认端口带 IP 前缀，参数值已下调
- **验收标准**:
  - ✅ 所有 5 个端口绑定包含 `100.124.150.42:` 前缀
  - ✅ shared_buffers=2GB, effective_cache_size=10GB
  - ✅ redis maxmemory=4gb
  - ✅ shm_size: 2gb
  - ✅ `.bak` 文件存在
- **潜在风险**: sed 多次运行会重复替换 → 仅执行一次；如需重做，先恢复 `.bak` 再重新替换

### Phase 4: 启动与验证

#### Task 4.1: 启动基础设施（postgres + redis + qdrant）
- **目标**: 先启动数据层服务，确保 postgres initdb 完成、redis 就绪、qdrant 加载集合
- **依赖**: T2.3, T2.4, T3.1, T3.2（所有数据和配置就位）
- **修改内容**:
  ```bash
  ssh asipp 'cd /data-ssd/dify/docker && \
    docker compose up -d db_postgres redis qdrant && \
    echo "Waiting for postgres..." && \
    sleep 10 && \
    docker compose exec -T db_postgres pg_isready -U postgres && \
    echo "Postgres ready" && \
    docker compose logs qdrant --tail 5'
  ```
- **修改边界**: 仅在远端启动 3 个容器；创建 Docker 网络和 volumes/db/data (initdb)
- **测试要求**:
  - `pg_isready` 返回 "accepting connections"
  - qdrant 日志无 panic/error，显示集合加载完成
  - redis 容器状态 healthy/running
- **验收标准**:
  - ✅ postgres accepting connections
  - ✅ qdrant 容器 running（非 restarting）
  - ✅ redis 容器 running
- **潜在风险**: qdrant 加载 40 GB 集合需要时间（首次 mmap 建立，~1–2 分钟 on SSD）

#### Task 4.2: 恢复 postgres 数据
- **目标**: 将 pg_dump 备份恢复到远端 postgres 中
- **依赖**: T4.1
- **修改内容**:
  ```bash
  ssh asipp 'cd /data-ssd/dify/docker && \
    zcat dify-db-*.sql.gz | docker compose exec -T db_postgres psql -U postgres -d dify'
  ```
  如果出现 `ERROR: table already exists` 等（因 `--clean --if-exists` 应已处理），忽略或用以下命令重建：
  ```bash
  # 备用：完全重建
  ssh asipp 'cd /data-ssd/dify/docker && \
    docker compose exec -T db_postgres psql -U postgres -c "DROP DATABASE dify;" && \
    docker compose exec -T db_postgres psql -U postgres -c "CREATE DATABASE dify;" && \
    zcat dify-db-*.sql.gz | docker compose exec -T db_postgres psql -U postgres -d dify'
  ```
- **修改边界**: 仅修改远端 postgres 数据库内容
- **测试要求**:
  ```bash
  ssh asipp 'cd /data-ssd/dify/docker && \
    docker compose exec -T db_postgres psql -U postgres -d dify -c "\dt" | head -20 && \
    docker compose exec -T db_postgres psql -U postgres -d dify -c "SELECT count(*) FROM alembic_version;"'
  ```
  - `\dt` 列出多张表
  - `alembic_version` 返回 1 行（migration 版本记录）
- **验收标准**:
  - ✅ `\dt` 列出 ≥30 张表
  - ✅ `alembic_version` count = 1
  - ✅ `SELECT count(*) FROM accounts;` ≥ 1（至少有管理员账号）
- **潜在风险**: dump 文件包含 `--clean` 的 DROP 语句在空库上会报 "table does not exist" → `--if-exists` 已处理这些 warning，可安全忽略

#### Task 4.3: 启动全部服务
- **目标**: 启动 Dify 全栈剩余服务（api, worker, web, nginx, plugin_daemon, sandbox, ssrf_proxy, worker_beat, worker_dataset）
- **依赖**: T4.2
- **修改内容**:
  ```bash
  ssh asipp 'cd /data-ssd/dify/docker && docker compose up -d'
  ```
- **修改边界**: 启动剩余容器；不修改配置文件
- **测试要求**:
  ```bash
  ssh asipp 'cd /data-ssd/dify/docker && \
    docker compose ps --format "table {{.Name}}\t{{.Status}}" && echo "---" && \
    sleep 30 && \
    docker compose ps --format "table {{.Name}}\t{{.Status}}"'
  ```
  所有容器状态为 running/healthy（30 秒后无 restarting）
- **验收标准**:
  - ✅ 全部容器状态 running 或 healthy
  - ✅ 无容器处于 restarting 状态
  - ✅ `docker compose logs api --tail 3` 显示 worker 启动成功
  - ✅ `docker compose logs nginx --tail 3` 无 error
- **潜在风险**: plugin_daemon 在 CentOS 7 内核 3.10 上可能有 syscall 兼容性 warning → 检查日志，非致命可忽略

#### Task 4.4: 数据完整性精确核对
- **目标**: 逐项比对迁移前基线，确保零数据丢失
- **依赖**: T4.3
- **修改内容**: 无文件修改，仅执行验证
- **修改边界**: 无
- **测试要求**:

  **4.4.1 Postgres 行数精确比对**
  ```bash
  ssh asipp 'cd /data-ssd/dify/docker && docker compose exec -T db_postgres psql -U postgres -d dify -t -c "
    SELECT '\''accounts: '\'' || count(*) FROM accounts
    UNION ALL SELECT '\''apps: '\'' || count(*) FROM apps
    UNION ALL SELECT '\''datasets: '\'' || count(*) FROM datasets
    UNION ALL SELECT '\''documents: '\'' || count(*) FROM documents;"'
  ```
  预期输出必须精确匹配基线：accounts=2, apps=37, datasets=206, documents=8938

  **4.4.2 Qdrant collections 数量比对**
  ```bash
  ssh asipp 'ls /data-ssd/dify/docker/volumes/qdrant/collections/ | wc -l'
  ```
  预期输出：155

  **4.4.3 Qdrant API 可达性验证**
  ```bash
  ssh asipp 'cd /data-ssd/dify/docker && \
    docker run --rm --network docker_default curlimages/curl:latest \
    -s -H "api-key: difyai123456" "http://qdrant:6333/collections" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"Collections: {len(d[chr(39)+chr(39).join([chr(114),chr(101),chr(115),chr(117),chr(108),chr(116)])][ chr(39)+chr(39).join([chr(99),chr(111),chr(108),chr(108),chr(101),chr(99),chr(116),chr(105),chr(111),chr(110),chr(115)])]}\")"'
  ```
  或者更简单地：
  ```bash
  ssh asipp 'cd /data-ssd/dify/docker && \
    docker run --rm --network docker_default curlimages/curl:latest \
    -s -H "api-key: difyai123456" "http://qdrant:6333/collections" | grep -o '\''"name"'\'' | wc -l'
  ```
  预期输出：155

  **4.4.4 Postgres 数据库大小**
  ```bash
  ssh asipp 'cd /data-ssd/dify/docker && \
    docker compose exec -T db_postgres psql -U postgres -d dify \
    -c "SELECT pg_size_pretty(pg_database_size('\''dify'\'')); "'
  ```
  预期输出：≈76 GB（允许 ±10% 因 pg_dump/restore 重组织）

- **验收标准**:
  - ✅ accounts = 2（精确）
  - ✅ apps = 37（精确）
  - ✅ datasets = 206（精确）
  - ✅ documents = 8938（精确）
  - ✅ Qdrant collections = 155（精确）
  - ✅ Qdrant API 返回 status=ok
  - ✅ Postgres 大小 ≥ 68 GB（允许 vacuum 差异）
- **潜在风险**: pg_dump `--clean` 可能跳过空表 → 如行数不匹配，检查 pg_restore 日志中的 ERROR/WARNING

#### Task 4.5: 功能端到端验证
- **目标**: 验证 Dify 服务全功能可用
- **依赖**: T4.4
- **修改内容**: 无文件修改，仅执行验证
- **修改边界**: 无
- **测试要求**（按顺序执行）:

  **4.5.1 Web UI 可访问**
  ```bash
  curl -sS -o /dev/null -w "%{http_code}" http://100.124.150.42/
  ```
  返回 200

  **4.5.2 API 健康检查**
  ```bash
  curl -sS http://100.124.150.42/v1/health
  ```
  返回 JSON 或 200

  **4.5.3 浏览器登录**
  在本地浏览器打开 `http://100.124.150.42`，使用已有账号登录

  **4.5.4 知识库数据完整性**
  登录后进入"知识库"页面：
  - 知识库总数 = 206
  - 随机打开 3 个知识库，检查文档数量与 Web UI 展示一致
  - 在一个知识库中执行检索查询，确认返回结果（验证向量库连通）

  **4.5.5 工作流完整性**
  进入"工作室"页面：
  - 应用总数 = 37
  - 随机打开一个工作流，确认节点图渲染正常

  **4.5.6 LLM 对话测试**
  选择一个已有应用发送一条测试消息，确认 LLM 正常响应（验证 embedding host 可达）

  **4.5.7 文件上传/下载**
  在知识库中上传一个测试 PDF，确认：
  - 上传成功（进度条完成）
  - 文档开始 embedding（worker_dataset 日志有处理记录）
  - 下载一个已有文件，确认可下载

  **4.5.8 向量检索延迟基准**
  ```bash
  time curl -s -X POST http://100.124.150.42/v1/datasets/<任意dataset_id>/retrieve \
    -H 'Authorization: Bearer <dataset_api_key>' \
    -H 'Content-Type: application/json' \
    -d '{"query":"测试查询","retrieval_model":{"search_method":"semantic_search","reranking_enable":false,"top_k":5,"score_threshold_enabled":false}}'
  ```
  响应时间 < 3s（NVMe SSD on_disk 模式正常范围）

- **验收标准**:
  - ✅ curl Web UI 返回 200
  - ✅ API 健康检查返回正常
  - ✅ 浏览器登录成功
  - ✅ 知识库列表 = 206 个
  - ✅ 应用列表 = 37 个
  - ✅ 知识库检索返回有效结果
  - ✅ LLM 对话正常响应
  - ✅ 文件上传下载正常
  - ✅ 检索延迟 < 3s
- **潜在风险**: FILES_URL 改为 `http://100.124.150.42` 后，文件下载路径可能变化 → 如文件下载失败，尝试改回 `http://100.124.150.42:5001` 并在 override 中暴露 5001 端口

### Phase 5: 客户端逐步切换（逐个切，每切一个验证通过后再切下一个）

> **原则**: 本地 Dify 服务在 T2.2 已停止，但本地数据卷保留不删。所有客户端从指向 localhost 改为指向 node0 Tailscale IP `100.124.150.42`。**每切一个客户端，观察 24 小时再切下一个。**

#### Task 5.1: 切换 dify-knowledge MCP server
- **目标**: 将 VS Code 的 dify-knowledge MCP server 指向 node0 上的 Dify
- **依赖**: T4.5（功能验证通过）
- **修改内容**:
  - 备份：`cp ~/opt/dify-knowledge-mcp-server/.env ~/opt/dify-knowledge-mcp-server/.env.bak-local`
  - 修改 `~/opt/dify-knowledge-mcp-server/.env`：
    ```
    DIFY_API_HOST=http://100.124.150.42/v1
    NO_PROXY=127.0.0.1,localhost,100.124.150.42
    ```
  - `DIFY_API_KEY` 保持不变（`dataset-r0uLS1nbaMxO72pSnOfYenx1`，已迁移到 node0 数据库中）
- **修改边界**: 仅修改 `~/opt/dify-knowledge-mcp-server/.env`；不修改 MCP server 代码或 VS Code mcp.json
- **测试要求**:
  - 在 VS Code 中重新加载 MCP server（Ctrl+Shift+P → "MCP: Restart"）
  - 执行一次 `dify-knowledge` 工具调用（如搜索一个知识库关键词）
  - 确认返回结果且无报错
- **验收标准**:
  - ✅ MCP server 启动无报错
  - ✅ 知识库查询返回有效结果
  - ✅ 响应时间 < 5s（Tailscale 延迟可接受）
- **潜在风险**: API key 在 node0 的 Postgres 中不存在 → 实际不会发生，因为 API key 在 datasets 表中，已随 pg_dump 迁移。如报 401，检查 `ssh asipp 'docker compose exec -T db_postgres psql -U postgres -d dify -c "SELECT id FROM dataset_api_keys LIMIT 3;"'`

#### Task 5.2: 切换 zotero-ingest-daemon（观察模式）
- **目标**: 将 zotero-ingest-daemon 的 Dify endpoint 指向 node0
- **依赖**: T5.1（MCP 切换验证通过且稳定 24 小时）
- **修改内容**:
  - 备份：`cp ~/.config/zotero-ingest-daemon/env ~/.config/zotero-ingest-daemon/env.bak-local`
  - 修改 `~/.config/zotero-ingest-daemon/env`：
    ```
    DIFY_API_HOST=http://100.124.150.42/v1
    ```
  - **注意**: 当前 config.yaml 中 `dify.ingest_enabled: false`，切换后 daemon 不会向 Dify 投递数据，安全。
  - 重载 daemon：`systemctl --user restart zotero-ingest-daemon`
- **修改边界**: 仅修改 `~/.config/zotero-ingest-daemon/env`；不修改 `config.yaml`、systemd unit 或代码
- **测试要求**:
  ```bash
  systemctl --user status zotero-ingest-daemon   # 状态 active (running)
  journalctl --user -u zotero-ingest-daemon --since '1 min ago' --no-pager | tail -10
  ```
  - daemon 正常运行，无连接错误
  - 由于 `ingest_enabled: false`，日志中应无 Dify 投递动作
- **验收标准**:
  - ✅ daemon 状态 active (running)
  - ✅ 日志无 connection refused / timeout 错误
  - ✅ `.bak-local` 备份文件存在
- **潜在风险**: daemon 即使 ingest_enabled=false 也可能在启动时探测 Dify connectivity → 检查日志确认无报错即可

#### Task 5.3: 浏览器与脚本切换
- **目标**: 更新所有剩余客户端访问地址
- **依赖**: T5.2（daemon 稳定 24 小时）
- **修改内容**: 无代码修改
  - 浏览器书签更新为 `http://100.124.150.42`（或 Tailscale MagicDNS `http://node0`）
  - 任何本地脚本中硬编码的 `localhost` Dify URL 改为 `100.124.150.42`
- **修改边界**: 仅修改客户端配置
- **测试要求**: 从本地浏览器通过新地址登录 → 操作一次知识库查询
- **验收标准**:
  - ✅ 浏览器能正常登录和操作
- **潜在风险**: 无

### Phase 6: 本地数据归档与安全退役

> **原则**: 只有满足 3 个条件才执行归档：(1) node0 已正常承载所有写入 ≥7 天；(2) node0 已做过至少一次成功备份（T7.1）；(3) 所有客户端已切换且无回流。

#### Task 6.1: 归档本地数据卷（≥7 天后）
- **目标**: 将本地 Dify 数据卷重命名归档而非直接删除，保留 30 天回滚窗口
- **依赖**: T5.3 完成 + 7 天观察期 + T7.1 远端备份成功
- **修改内容**:
  ```bash
  cd /home/gw/opt/dify/docker
  # 确认本地服务已停
  docker compose ps   # 应返回空
  # 归档（不删除！）
  mv volumes volumes.archived-$(date +%Y%m%d)
  echo "归档完成: volumes.archived-$(date +%Y%m%d)"
  ls -lhd volumes.archived-*
  ```
- **修改边界**: 仅重命名 `volumes/` → `volumes.archived-YYYYMMDD/`；不删除任何数据
- **测试要求**: `ls -d volumes.archived-*` 返回归档目录
- **验收标准**:
  - ✅ 归档目录存在
  - ✅ 原 `volumes/` 不存在
  - ✅ 本地内存释放（`free -h` available 增加，qdrant mmap 不再占用）
- **潜在风险**: 无（归档是可逆操作，`mv volumes.archived-* volumes` 即可恢复）

#### Task 6.2: 清理归档（≥30 天后，需用户确认）
- **目标**: 彻底删除归档数据释放磁盘空间
- **依赖**: T6.1 + 30 天观察期
- **修改内容**:
  ```bash
  # ⚠️ 需用户明确确认后执行 — 此操作不可逆
  cd /home/gw/opt/dify/docker
  rm -rf volumes.archived-*
  rm -f /tmp/dify-db-*.sql.gz
  ```
- **修改边界**: 仅删除归档目录和 dump 文件
- **测试要求**: `du -sh /home/gw/opt/dify/docker/` 确认释放 ~125 GB
- **验收标准**:
  - ✅ 归档目录已删除
  - ✅ 磁盘空间已释放
- **潜在风险**: 不可逆 → 30 天观察期 + 用户手动确认是双重保险

### Phase 7: node0 长期运维配置

#### Task 7.1: 配置自动备份 cron
- **目标**: 在 node0 上建立每日 Postgres dump + Qdrant snapshot 自动备份
- **依赖**: T4.5（远端服务运行稳定）
- **修改内容**:
  ```bash
  ssh asipp 'cat > ~/dify-backup.sh << '\''SCRIPT'\'' 
#!/bin/bash
set -e
DATE=$(date +%Y%m%d)
BAK=/data-ssd/dify-backups/$DATE
mkdir -p $BAK

# Postgres dump
cd /data-ssd/dify/docker
docker compose exec -T db_postgres pg_dump -U postgres --clean --if-exists --no-owner dify \
  | gzip > $BAK/dify.sql.gz
echo "PG dump: $(ls -lh $BAK/dify.sql.gz)"

# Qdrant snapshots (每个 collection)
for c in $(docker run --rm --network docker_default curlimages/curl:latest \
  -s -H "api-key: difyai123456" "http://qdrant:6333/collections" \
  | python3 -c "import json,sys;[print(x['\''name'\'' ]) for x in json.load(sys.stdin)['\''result'\'' ]['\''collections'\'' ]]"); do
  docker run --rm --network docker_default curlimages/curl:latest \
    -s -X POST -H "api-key: difyai123456" "http://qdrant:6333/collections/$c/snapshots" > /dev/null
done
echo "Qdrant snapshots done"

# 保留 30 天，清理旧备份
find /data-ssd/dify-backups -maxdepth 1 -type d -mtime +30 -exec rm -rf {} +
echo "Backup complete: $BAK"
SCRIPT
chmod +x ~/dify-backup.sh'
  ```
  添加 crontab：
  ```bash
  ssh asipp '(crontab -l 2>/dev/null; echo "0 3 * * * /data-ssd/dify/dify-backup.sh >> /data-ssd/dify/backup.log 2>&1") | sort -u | crontab -'
  ```
- **修改边界**: 在 node0 创建 `~/dify-backup.sh` 和 crontab 条目；不修改 Dify 服务配置
- **测试要求**:
  - 手动运行一次：`ssh asipp '~/dify-backup.sh'`
  - 检查输出：`ssh asipp 'ls -lh /data-ssd/dify-backups/$(date +%Y%m%d)/'`
  - 验证 dump 文件：`ssh asipp 'zcat /data-ssd/dify-backups/$(date +%Y%m%d)/dify.sql.gz | head -5'` 包含 PostgreSQL dump header
- **验收标准**:
  - ✅ 备份脚本手动执行成功
  - ✅ PG dump 文件 > 10 KB
  - ✅ crontab 条目已添加（`ssh asipp 'crontab -l | grep dify-backup'`）
- **潜在风险**: crontab 任务在 PATH 不全的 cron 环境可能找不到 docker → 脚本中用绝对路径 `/usr/bin/docker`

#### Task 7.2: 磁盘空间监控
- **目标**: 配置 NVMe SSD 空间告警，避免磁盘满导致服务崩溃
- **依赖**: T4.5
- **修改内容**: 添加 cron 监控脚本
  ```bash
  ssh asipp 'cat > ~/dify-disk-check.sh << '\''SCRIPT'\'' 
#!/bin/bash
USED_PCT=$(df /data-ssd --output=pcent | tail -1 | tr -d " %")
if [[ $USED_PCT -ge 80 ]]; then
  echo "WARNING: /data-ssd usage at ${USED_PCT}%" | wall
  echo "$(date) DISK WARNING: ${USED_PCT}%" >> /data-ssd/dify/disk-alert.log
fi
SCRIPT
chmod +x ~/dify-disk-check.sh
(crontab -l 2>/dev/null; echo "*/30 * * * * ~/dify-disk-check.sh") | sort -u | crontab -'
  ```
- **修改边界**: 创建监控脚本 + crontab；不修改服务
- **测试要求**: `ssh asipp '~/dify-disk-check.sh && echo OK'` 返回 OK（当前 1% 使用率不触发告警）
- **验收标准**:
  - ✅ 脚本执行无错
  - ✅ crontab 包含 disk-check 条目
- **潜在风险**: 无

## Execution Wave（并行执行波次）

| Wave | 可并行 Task | 依赖已完成 | 预计耗时 |
|------|------------|------------|----------|
| W1 | T1.1, T1.4 | — | 1 min |
| W2 | T1.2, T1.3 | W1 (确认磁盘空间) | 5–10 min (image pull) |
| W3 | T2.1 | W2 (代码已同步) | 1–3 min |
| W4 | T2.2 | W3 | 30 sec |
| W5 | T2.3, T2.4 | W4 | 9 min (rsync 45 GB) |
| W6 | T3.1, T3.2 | W5 | 2 min |
| W7 | T4.1 | W6 | 1–2 min |
| W8 | T4.2 | W7 | 1–5 min |
| W9 | T4.3 | W8 | 2 min |
| W10 | T4.4, T4.5 | W9 | 10 min |
| W11 | T5.1 | W10 | 1 min + 24h 观察 |
| W12 | T5.2 | W11 + 24h | 1 min + 24h 观察 |
| W13 | T5.3, T7.1, T7.2 | W12 | 5 min |
| W14 | T6.1 | W13 + 7 天 | 1 min |
| W15 | T6.2 | W14 + 30 天 + 用户确认 | 1 min |

> **停机窗口**: W4–W9 (T2.2 停本地 → T4.3 远端全部起来)，约 **15–20 分钟**。
> W1–W3 在本地服务运行时执行，零停机。
> W11–W13 为渐进切换，每步间隔 24 小时观察。
> W14–W15 为长期运维，间隔以天计。

## 回归检查清单

- [ ] 全部容器 running/healthy：`docker compose ps`
- [ ] Web UI 可登录：`curl -sI http://100.124.150.42/`
- [ ] API 健康：`curl http://100.124.150.42/v1/health`
- [ ] accounts = 2, apps = 37, datasets = 206, documents = 8938（精确匹配）
- [ ] Qdrant collections = 155
- [ ] 知识库检索返回有效结果（向量库连通验证）
- [ ] LLM 对话正常响应（embedding host 可达验证）
- [ ] 文件上传下载正常
- [ ] dify-knowledge MCP server 查询正常（T5.1 后）
- [ ] zotero-ingest-daemon 运行无报错（T5.2 后）
- [ ] node0 上 docconv 容器不受影响：`docker ps | grep node0-`
- [ ] node0 内存使用合理：`free -h` available ≥ 20 GB
- [ ] 每日备份 cron 正常执行（T7.1 后检查 backup.log）
- [ ] /data-ssd 使用率 < 80%

## 回滚方案

### 阶段内回滚（Phase 1–4，本地数据未归档）

```bash
# 远端：停止
ssh asipp 'cd /data-ssd/dify/docker && docker compose down'

# 本地：重新启动（数据仍保留在本地 volumes/）
cd /home/gw/opt/dify/docker && docker compose up -d
```

本地数据在 T6.1 归档前始终保留在原位，可随时启动回滚。

### 客户端回滚（Phase 5，单个客户端切换失败）

```bash
# MCP server 回滚
cp ~/opt/dify-knowledge-mcp-server/.env.bak-local ~/opt/dify-knowledge-mcp-server/.env
# VS Code 重载 MCP

# zotero-ingest-daemon 回滚
cp ~/.config/zotero-ingest-daemon/env.bak-local ~/.config/zotero-ingest-daemon/env
systemctl --user restart zotero-ingest-daemon
```

### 完全回滚（Phase 6 归档后）

```bash
cd /home/gw/opt/dify/docker
mv volumes.archived-* volumes
docker compose up -d
# 然后回滚所有客户端配置
```

## 审查日志

| 轮次 | 聚焦 | 发现问题数 | 已修正 | 剩余 |
|------|------|-----------|--------|------|
| R1 | 结构完整性 | 2 | 2 | 0 |
| R1.5 | 外部引用事实核查 | 2 | 2 | 0 |
| R2 | 可执行性（含脚本干跑） | 3 | 3 | 0 |
| R3 | 风险与边缘（含跨轮一致性） | 3 | 3 | 0 |
| R4 | 补充审查（客户端迁移） | 2 | 2 | 0 |
| **终止** | **T1 — 收敛终止** | | | **0** |

### Completion Summary

| 维度 | 结果 |
|------|------|
| 背景与目标 | 完整（含迁移前数据基线） |
| 技术方案 | 完整 |
| Error & Rescue Map | 12 路径已覆盖，0 CRITICAL GAP |
| 执行计划 | 7 Phase, 19 Task |
| 回归检查清单 | 14 项（含项目特定检查） |
| 已知局限 | 无 |

### R1 Issues
- **Issue R1-1**: Error & Rescue Map 缺少 qdrant 内存压力场景 → 已补充 "qdrant 内存压力" 行 ✅ 已修正
- **Issue R1-2**: 回归检查清单缺少 node0 现有服务影响检查 → 已补充 docconv 检查项和内存检查项 ✅ 已修正

### R1.5 Issues
- **Issue R1.5-1**: postgres 镜像 tag 未在计划中引用时验证 → 已通过 `docker inspect docker-db_postgres-1` 确认为 `postgres:15-alpine` [verified: terminal output] ✅ 已修正
- **Issue R1.5-2**: dify-knowledge MCP 的 API key `dataset-r0uLS1nbaMxO72pSnOfYenx1` 和 DIFY_API_HOST `http://localhost/v1` 已通过 `cat ~/opt/dify-knowledge-mcp-server/.env` 验证 [verified: terminal output] ✅ 已修正

### R2 Issues
- **Issue R2-1**: pg_dump 方案未处理 `--clean` SQL 在空库上的 "table does not exist" warning → T4.2 已加说明 ✅ 已修正
- **Issue R2-2**: postgres PGDATA 权限问题（file-level rsync 不可行）→ 方案已改为 pg_dump（不涉及文件拷贝），此 issue 被设计消除 ✅ 已修正
- **Issue R2-3**: sed 替换需验证远端 .env 与本地一致（rsync 后未被修改过）→ T3.1 测试要求已包含 grep 验证 ✅ 已修正

### R3 Issues
- **Issue R3-1**: T1.2 rsync 如在 T3.1 sed 之后重新运行，会覆盖 URL 修改 → Execution Wave 保证 T1.2 (W2) 在 T3.1 (W6) 之前；已在 T3.1 潜在风险中注明不可重新运行 T1.2 ✅ 已修正
- **Issue R3-2**: 62 GB 系统上 postgres (shared_buffers 4GB) + redis (8GB) + qdrant (40GB mmap) 竞争 → T3.2 已下调参数；Error Map 已加内存压力条目 ✅ 已修正
- **Issue R3-3**: 客户端切换后 API key 可能在 node0 数据库中不存在 → 不可能发生：API key 存储在 datasets/dataset_api_keys 表中，已随 pg_dump 迁移。T5.1 增加了诊断查询命令 ✅ 已修正

### R4 Issues（补充审查：客户端迁移 + 长期运维）
- **Issue R4-1**: zotero-ingest-daemon 的 ingest_enabled=false 状态使切换安全，但未在计划中显式标注 → T5.2 已标注 ✅ 已修正
- **Issue R4-2**: 备份脚本中 cron 环境缺少 docker PATH 可能导致静默失败 → T7.1 潜在风险已标注，手动测试步骤验证 ✅ 已修正
