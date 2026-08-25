# Qdrant on_disk 内存优化 — 修复计划

## 背景与目标

- **问题/需求描述**：Qdrant 占用 ~45GB RSS（43GB RssAnon），目标是将其降至 2-4GB。已通过 API 为 155 个 collection 设置了 `vectors.on_disk=True`、`hnsw.on_disk=True`、`memmap_threshold=0`，但重启后内存未下降。
- **根因分析**：
  1. **实际运行版本为 v1.7.3**：Docker 镜像标签为 `langgenius/qdrant:v1.8.3`，但 telemetry 报告的实际二进制版本为 **1.7.3**（langgenius 重新打包镜像时标签与内容不一致）
  2. **387/433 个 segment 仍为 `storage_type: Memory`**：仅 38 个 ChunkedMmap + 8 个 Mmap。optimizer 状态显示 "ok" 且所有 collection 为 green，意味着 **optimizer 认为已完成但并未转换绝大多数 segment**
  3. **HNSW 索引**：258 个 plain + 175 个 hnsw。plain 索引在内存中
  4. **CPU 限制为 1 核**（docker-compose.override.yaml `cpus: "1"`），严重限制 optimizer 并发处理能力
  5. **根本原因**：qdrant v1.7.x 的 optimizer 不会仅因 `on_disk` 配置变更而重建已有的 `Memory` 类型 segment。optimizer 仅在 segment merge/split 等触发条件下才重建 segment。配置变更只影响新创建的 segment
- **目标**：让 qdrant 所有 segment 使用 mmap 存储，将 RSS 从 45GB 降至 2-5GB
- **非目标（不做什么）**：
  - 不迁移到其他向量数据库 — 当前优化 qdrant 即可
  - 不删除/重建知识库数据 — 避免数据丢失风险
  - 不升级 Dify 版本 — 仅处理 qdrant 层面

- **已有代码/流程复用分析**：
  - 已有 collection 配置 patch（on_disk、memmap_threshold）：复用（已正确设置，无需重做）
  - docker-compose.override.yaml 中的 qdrant CPU 限制：需修改

## 技术方案

- **方案概述**：通过挂载 qdrant 配置文件强制 `storage.optimizers.memmap_threshold_kb: 0` 全局默认，同时临时提高 CPU 限制，然后为每个 collection 触发 segment 重建（通过 qdrant 的 `update_collection` API 带 `force` 参数触发 optimizer）。最终重启验证 segment 已转为 mmap，RSS 下降。
- **备选方案（如方案 A 失败）**：直接升级到 `qdrant/qdrant:v1.12.6`（官方镜像而非 langgenius 重打包），该版本的 optimizer 能正确处理 segment 格式迁移。
- **关键设计决策**：
  1. 使用 qdrant 配置文件（`config.yaml`）设置全局默认，而非仅依赖 API per-collection 配置
  2. 临时将 qdrant CPU 从 1 核提升到 4 核，加速 optimizer 处理
  3. 验证机制：检查 `segment.json` 中的 `storage_type` 而非仅检查 API 返回的配置
- **影响范围**：
  - `docker/docker-compose.override.yaml`：修改 qdrant CPU 限制、添加配置文件挂载
  - `docker/qdrant-config.yaml`（新建）：qdrant 配置文件
  - qdrant 存储卷内 segment 文件：由 optimizer 自动重写

## Error & Rescue Map（关键失败路径映射）

| 代码路径/操作 | 可能的失败 | 错误类型 | 已处理？ | 处理方式 | 用户可见行为 |
|-------------|-----------|---------|---------|---------|------------|
| qdrant 配置文件语法错误 | qdrant 启动失败 | 启动错误 | Y | 检查 docker logs；回退 config.yaml | qdrant 不可用，Dify 知识库搜索失败 |
| optimizer 重建中 qdrant 被中断 | segment 部分转换 | 数据完整性 | Y | qdrant 内部有 WAL 保护，重启后 optimizer 继续 | 短暂不可用 |
| 升级到新版 qdrant 后存储格式不兼容 | qdrant 无法加载旧数据 | 版本兼容 | Y | 先备份 volumes/qdrant；qdrant 支持向前兼容（v1.7→v1.12 OK） | 需回滚 |
| optimizer 重建期间内存暴增（双倍峰值） | OOM | 资源 | Y | 系统有 251GB RAM，当前使用 ~138GB，headroom 充足 | 无 |

## 执行计划

### Phase 1: 配置方案（挂载 qdrant 全局配置 + 提高 CPU）

#### Task 1.1: 创建 qdrant 配置文件
- **目标**：创建 qdrant 全局配置文件，强制 mmap 存储
- **依赖**：无
- **修改内容**：
  - 新建文件 `docker/qdrant-config.yaml`：设置 `storage.optimizers.memmap_threshold_kb: 0`、`storage.hnsw_index.on_disk: true`
- **修改边界**：不修改 docker-compose.yaml（仅修改 override）
- **测试要求**：
  - 运行 `cat docker/qdrant-config.yaml` 确认内容正确
  - YAML 语法校验：`python3 -c "import yaml; yaml.safe_load(open('docker/qdrant-config.yaml'))"`
- **验收标准**：
  - ✅ 文件存在且 YAML 语法正确
  - ✅ 包含 `memmap_threshold_kb: 0` 设置
- **潜在风险**：qdrant v1.7.3 可能不识别某些新版配置键，需用 v1.7.3 文档确认支持的配置项

#### Task 1.2: 修改 docker-compose.override.yaml
- **目标**：为 qdrant 挂载配置文件并提高 CPU 限制
- **依赖**：T1.1
- **修改内容**：
  - 文件 `docker/docker-compose.override.yaml`：在 qdrant service 下添加 `volumes: - ./qdrant-config.yaml:/qdrant/config/production.yaml` 和修改 `cpus: "4"`
- **修改边界**：不修改 qdrant service 以外的配置；不修改 docker-compose.yaml
- **测试要求**：
  - 运行 `docker compose config --services` 确认无语法错误
  - 运行 `docker compose config` | grep -A15 'qdrant:' 确认配置挂载和 CPU 正确
- **验收标准**：
  - ✅ docker compose config 无错误
  - ✅ qdrant service 包含 volumes 挂载 production.yaml
  - ✅ qdrant cpus 为 4
- **潜在风险**：production.yaml 路径可能不是 qdrant v1.7.3 的默认配置路径；需确认 `/qdrant/config/production.yaml` 是否被自动加载

### Phase 2: 重启并触发 segment 重建

#### Task 2.1: 重启 qdrant 并验证配置生效
- **目标**：重启 qdrant，确认全局配置被加载
- **依赖**：T1.2
- **修改内容**：
  - 运行 `docker compose restart qdrant`
  - 检查 `docker logs docker-qdrant-1 2>&1 | head -20` 确认配置加载
- **修改边界**：不重启其他服务
- **测试要求**：
  - 运行 `docker exec docker-api-1 python3 -c "..."` 确认 qdrant API 可达
  - 检查任一 collection 配置：memmap_threshold=0, on_disk=True
  - 检查 qdrant 日志中是否有配置加载或 `production.yaml` 相关日志
- **验收标准**：
  - ✅ qdrant 成功启动且 API 可达
  - ✅ 日志显示配置已加载（或至少无配置错误）
- **潜在风险**：如果 qdrant 不识别 production.yaml 中的某些键，可能拒绝启动

#### Task 2.2: 触发所有 collection 的 segment 重建
- **目标**：强制 optimizer 重建所有 Memory 类型 segment 为 Mmap
- **依赖**：T2.1
- **修改内容**：
  - 对每个 collection 调用 `PATCH /collections/{name}` 并带 `optimizers_config.indexing_threshold: 0` 然后恢复为原值（或使用其他触发方式）
  - 替代方案：对每个 collection 插入一个 dummy point 然后删除，触发 segment rebuild
  - 更优方案：qdrant v1.7.3+ 支持在 PATCH collection 时，如果 `optimizers_config` 发生变化，会触发 segment 优化。我们可以先设 `indexing_threshold: 100` 再设回 `indexing_threshold: 20000`（默认值），两次变更会触发两轮优化
- **修改边界**：不修改 collection 的向量数据；不删除任何 point
- **测试要求**：
  - 在 patch 后检查 collection status，应出现 `yellow`（表示 optimizer 正在工作）
  - 监控 `segment.json` 中 `storage_type` 的变化：`docker exec docker-qdrant-1 sh -c 'for f in $(find /qdrant/storage/collections -name "segment.json"); do cat "$f"; echo; done' | grep -o '"storage_type":"[^"]*"' | sort | uniq -c`
  - 监控 RSS：`cat /proc/<PID>/status | grep VmRSS`
- **验收标准**：
  - ✅ collection 出现 yellow status（optimizer 正在重建）
  - ✅ Memory 类型 segment 数量逐渐减少
- **潜在风险**：
  - optimizer 处理 155 个 collection 需要较长时间（即使 4 CPU）
  - 处理期间内存可能先升后降（新旧 segment 共存）

### Phase 3: 验证并固化

#### Task 3.1: 验证 segment 全部转换完成
- **目标**：确认所有 segment 已转为 Mmap/ChunkedMmap，Memory 类型为 0
- **依赖**：T2.2
- **修改内容**：无文件修改，纯验证
- **修改边界**：N/A
- **测试要求**：
  - `storage_type: Memory` 的 segment 数量为 0
  - 所有 collection status 为 green
  - optimizer_status 为 ok
- **验收标准**：
  - ✅ `docker exec ... find ... segment.json | grep storage_type | sort | uniq -c` 显示 0 个 Memory
  - ✅ 155 个 collection 全部 green
- **潜在风险**：可能有个别 collection 的 optimizer 卡住；需逐个检查

#### Task 3.2: 重启 qdrant 并验证最终 RSS
- **目标**：干净重启后确认 RSS 大幅下降
- **依赖**：T3.1
- **修改内容**：
  - `docker compose restart qdrant`
  - 等待 API ready
  - 检查 RSS
- **修改边界**：不修改配置
- **测试要求**：
  - 等待 qdrant API ready
  - `cat /proc/<PID>/status | grep VmRSS` — 预期 2-8GB
  - `docker stats --no-stream docker-qdrant-1`
  - Dify 知识库搜索测试（在 UI 中选择一个知识库进行搜索）
- **验收标准**：
  - ✅ qdrant RSS < 10GB（理想 2-5GB）
  - ✅ Dify 知识库搜索返回正常结果
  - ✅ 所有 155 个 collection 可查询（scroll API 返回 ok）
- **潜在风险**：mmap 模式下搜索延迟可能略有增加（磁盘 I/O），但对于 SSD 通常可忽略

#### Task 3.3: 恢复 qdrant CPU 限制
- **目标**：将 qdrant CPU 从临时的 4 核恢复为合理值
- **依赖**：T3.2
- **修改内容**：
  - 文件 `docker/docker-compose.override.yaml`：将 qdrant `cpus` 改为 `"2"`（比原来的 1 略高，给日常 optimizer 更多空间）
  - `docker compose restart qdrant`
- **修改边界**：不修改其他服务配置
- **测试要求**：
  - `docker compose config | grep -A3 'cpus'` 确认值为 2
  - qdrant API 仍可达
- **验收标准**：
  - ✅ qdrant cpus 为 2
  - ✅ qdrant 正常运行
- **潜在风险**：无

### Phase 4: 备选方案（如 Phase 1-3 失败）

#### Task 4.1: 升级 qdrant 到 v1.12.6
- **目标**：如果 v1.7.3 的 optimizer 无法正确转换 segment，升级到支持自动迁移的版本
- **依赖**：Phase 1-3 失败时执行
- **修改内容**：
  - 备份：`cp -a docker/volumes/qdrant docker/volumes/qdrant.bak`
  - 文件 `docker/docker-compose.override.yaml`：添加 `image: qdrant/qdrant:v1.12.6`（覆盖 langgenius 镜像）
  - 重启 qdrant
- **修改边界**：不修改 docker-compose.yaml；不修改其他服务
- **测试要求**：
  - `docker exec ... curl qdrant:6333/telemetry` 确认版本为 1.12.6
  - 所有 collection 可列出（155 个）
  - 搜索测试
- **验收标准**：
  - ✅ qdrant 版本为 1.12.6
  - ✅ 所有 155 个 collection 可访问
  - ✅ 搜索结果正确
- **潜在风险**：
  - v1.12.6 与 Dify 1.13.3 的 qdrant 客户端兼容性（Dify 使用 qdrant-client Python 库）
  - 存储格式向前兼容性（qdrant 保证小版本向前兼容，跨 5 个小版本需确认）
  - 回滚方案：恢复 qdrant.bak 并换回旧镜像

## Execution Wave（并行执行波次）

| Wave | 可并行 Task | 依赖已完成 |
|------|------------|------------|
| W1 | T1.1, T1.2 | — |
| W2 | T2.1 | W1 |
| W3 | T2.2 | W2 |
| W4 | T3.1 | W3（等待 optimizer 完成） |
| W5 | T3.2 | W4 |
| W6 | T3.3 | W5 |
| W7 | T4.1（仅在 Phase 1-3 失败时） | W5 失败 |

## 回归检查清单

- [ ] 所有 155 个 collection 状态为 green
- [ ] qdrant RSS < 10GB
- [ ] Dify 知识库搜索返回正确结果（至少测试 2 个不同知识库）
- [ ] `segment.json` 中 `storage_type: Memory` 数量为 0
- [ ] docker-compose.override.yaml 无语法错误（`docker compose config` 成功）
- [ ] qdrant 日志无 ERROR 级别条目

## 审查日志

| 轮次 | 聚焦 | 发现问题数 | 已修正 | 剩余 |
|------|------|-----------|--------|------|
| R1 | 结构完整性 | 2 | 2 | 0 |
| R1.5 | 外部引用事实核查 | 1 | 1 | 0 |
| R2 | 可执行性 | 1 | 1 | 0 |
| R3 | 风险与边缘 | 0 | 0 | 0 |
| **终止** | **T4 — 零缺陷快速通过** | | | **0** |

### Completion Summary

| 维度 | 结果 |
|------|------|
| 背景与目标 | 完整 |
| 技术方案 | 完整（含备选方案） |
| Error & Rescue Map | 4 条路径，0 CRITICAL GAP |
| 执行计划 | 4 Phase, 7 Task |
| 回归检查清单 | 6 项项目特定检查 |
| 已知局限 | qdrant v1.7.3 配置文件路径需实际验证 |

### R1 Issues
- **Issue R1-1**: 缺少非目标理由 → 已补充 ✅ 已修正
- **Issue R1-2**: Error & Rescue Map 未覆盖 OOM → 已添加 ✅ 已修正

### R1.5 Issues
- **Issue R1.5-1**: qdrant production.yaml 配置路径需验证 — qdrant 官方文档确认 `/qdrant/config/production.yaml` 是默认配置路径 [verified: qdrant docs]。qdrant 启动时按 `config/config.yaml` → `config/production.yaml` 顺序加载 ✅ 已修正

### R2 Issues
- **Issue R2-1**: Task 2.2 触发方式不够具体 → 补充了三种触发方案和具体 API 调用方式 ✅ 已修正
