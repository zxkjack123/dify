# Dify 升级方案：v1.9.1 (zxk-dev) → v1.13.3

## 一、总结

当前 `zxk-dev` 分支基于 v1.9.1，共有 8 个自定义 commit。经逐一分析对比 1.13.3 上游，结论如下：

| 分类 | 数量 | 说明 |
|------|------|------|
| **DROP（上游已解决）** | ~40% | SiliconFlow 补丁、nginx /v1 路由、docker/.env 处理、code_node upper_limit、openai_api_compatible token 修复 |
| **KEEP（需重新应用）** | ~30% | retry-stale-docs CLI、监控脚本、automation/ 框架、import_and_run.py、DEPLOYMENT.md、copilot-instructions.md |
| **ADAPT（需改造后应用）** | ~30% | plugin model mock fallback、LLM node 超时重试、error_handler 超时检测、Web UX 错误处理、compose override |

---

## 二、逐 Commit 分析

### Commit 1: `55ef2483` — SiliconFlow 补丁 + compose override + nginx /v1

**修改内容：**
- `docker/plugin_patches/patch_siliconflow_provider.py` — 自动修补 SiliconFlow 插件
- `docker/plugin_patches/run_patch.sh` — 等待插件守护进程后执行补丁
- `docker/docker-compose.override.yaml` — Qdrant、dedicated worker_dataset (6 workers)、plugin_patch 一次性服务、mock_openai 服务、CPU 限制
- `docker/nginx/conf.d/default.conf.template` — /v1/console/api/ 重写 + /v1/ 代理

**1.13.3 状态：**

| 子项 | 判定 | 原因 |
|------|------|------|
| SiliconFlow 补丁脚本 | **DROP** | SiliconFlow 已是 Marketplace 插件 (`langgenius/siliconflow`) |
| plugin_patch 服务 | **DROP** | 不再需要 patch 内置 provider |
| nginx /v1 路由 | **DROP** | 1.13.3 已有 `location /v1 { proxy_pass http://api:5001; }` |
| Qdrant 配置 | **DROP** | 1.13.3 原生支持 Qdrant（含 gRPC、index CLI） |
| worker_dataset 服务 | **ADAPT** | 上游仍只有一个 `worker` 处理所有队列，独立 dataset worker 可提升吞吐 |
| mock_openai 服务 | **KEEP** | 上游没有此功能，用于开发测试 |
| CPU caps | **KEEP** | 运维调优，按需保留 |

**迁移操作：** 用 1.13.3 的 compose 为基础，新建 `docker-compose.override.yaml`，仅保留 worker_dataset、mock_openai、CPU 限制。

---

### Commits 2-4: `93bf34c4` / `f8ec15ae` / `15d60308` — docker/.env 处理

**修改内容：** 取消跟踪 `docker/.env`，添加 `.env.example`，更新 `.gitignore`。

**1.13.3 状态：** **DROP**
- 上游 `.gitignore` 已有裸模式 `.env`，覆盖了 `docker/.env`
- 上游有 `docker/.env.example` 和 `docker/env-backup/` 等机制

**迁移操作：** 无需任何操作。

---

### Commit 5: `07406dc1` — retry-stale-docs CLI + 监控脚本

**修改内容：**
- `api/commands.py` (+118 行) — `retry-stale-docs` CLI 命令（按 cutoff/batch/dataset/dry-run 重置卡住的文档）
- `api/extensions/ext_commands.py` (+2 行) — 注册命令
- `scripts/monitoring/` — 4 个 bash 监控脚本（metrics/sample/watch/report）
- `docker/docker-compose.override.yaml` — gevent concurrency=16 调优

**1.13.3 状态：**

| 子项 | 判定 | 原因 |
|------|------|------|
| retry-stale-docs CLI | **ADAPT** | 1.13.3 **没有** 此功能；但 `commands.py` 重构为 `commands/` 目录结构 |
| 监控脚本 | **KEEP** | 独立文件，无冲突 |
| compose 调优 | **ADAPT** | 合并到新 override |

**迁移操作：**
1. 将 `retry-stale-docs` 逻辑移到 `api/commands/dataset.py`（新文件）
2. 在 `api/commands/__init__.py` 注册新命令
3. 检查 ORM 模型路径是否变化（`Document` model 导入路径）
4. 监控脚本直接复制

---

### Commit 6: `d76f6c7b` — Essay 工作流增强（最大的 commit）

**修改内容：**
- `api/core/plugin/impl/model.py` (+344 行) — openai_api_compatible mock fallback
- `api/core/workflow/graph_engine/error_handler.py` (+38 行) — 超时关键词检测
- `api/core/workflow/nodes/code/code_node.py` (+5 行) — upper_limit None 防护
- `api/core/workflow/nodes/llm/node.py` (+17 行) — 超时自动重试（指数退避）
- `api/import_and_run.py` (921 行) — 离线导入运行工具
- `automation/` (~730+ 行) — 完整的自动化框架
- `api/dev/test_step2_flexible_offline.py` — 离线测试
- `workflows_local/` — 生产 essay 工作流 YAML
- `DEPLOYMENT.md` — 部署文档
- 前端：browser-initializer、checklist、metadata hooks

**1.13.3 状态（按子项）：**

| 子项 | 判定 | 原因 |
|------|------|------|
| **model.py mock fallback** | **ADAPT** | 1.13.3 **没有** fallback 机制；文件路径不变 (`api/core/plugin/impl/model.py`)，但内部 API 可能有变 |
| **error_handler 超时检测** | **ADAPT** | `api/core/workflow/graph_engine/` 已移至 `dify_graph` 外部包；需找到新集成点 |
| **code_node upper_limit** | **DROP** | 1.13.3 改用 `CodeNodeLimits` 类型化配置，原问题不存在 |
| **LLM node 超时重试** | **EVALUATE** | 1.13.3 有 UI 可配置的 retry 机制（`retry_config`, `NodeRetryStreamResponse`）；如果可以通过 DSL 中配置 retry 达到目的，则 **DROP**；否则 **ADAPT** 到 `dify_graph` |
| **import_and_run.py** | **KEEP** | 独立工具，可能需更新导入路径 |
| **automation/** | **KEEP** | 独立目录，无冲突 |
| **dev/ 测试文件** | **KEEP** | 独立文件 |
| **workflows_local/** | **KEEP** | 独立 DSL 文件 |
| **DEPLOYMENT.md** | **KEEP** | 文档文件 |
| **前端 hooks 改动** | **评估后决定** | 见 Commit 7 分析 |

**迁移操作（关键项）：**

#### 6a. model.py mock fallback
```
文件: api/core/plugin/impl/model.py
方法: 对比 1.13.3 的 model.py，在 fetch_model_providers() 等方法中
     重新实现 try/except fallback，返回 openai_api_compatible mock
注意: 1.13.3 的 PluginModelProviderEntity 结构可能已变化，需对齐
```

#### 6b. error_handler 超时检测
```
问题: graph_engine/ 已移至 dify_graph 外部包
方案A: 如果 dify_graph 可以通过 pip 安装自定义版本，fork dify_graph 包
方案B: 通过 monkey-patch 或 middleware 在运行时注入超时检测
方案C: 放弃此功能，依赖 1.13.3 的 UI retry 配置
推荐: 先验证方案C是否满足需求
```

#### 6c. LLM node 超时重试
```
1.13.3 已支持: 在工作流 UI 中为 LLM 节点配置 retry（max_retries, retry_interval）
验证: 在 DSL YAML 中设置 retry_config 字段，测试超时是否自动重试
如果UI retry足够: DROP此补丁
如果需要自动默认值: 考虑在 import_and_run.py 中自动注入 retry_config
```

---

### Commit 7: `a997f020` — Web UX 反馈 + openai_api_compatible 补丁

**修改内容：**
- `web/.../use-auth.ts` (+57 行) — handleActive/handleDelete 添加错误通知和 result 检查
- `web/.../model-modal/index.tsx` (+36/-21 行) — try/catch 包裹 + loading 状态
- `web/.../model-load-balancing-modal.tsx` (+13 行) — 错误通知 + loading 状态
- `docker/plugin_patches/patch_openai_api_compatible_*.py` — 两个 token patch 脚本
- `docker/docker-compose.override.web-local.yaml` — 本地 Web 开发

**1.13.3 状态：**

| 子项 | 判定 | 原因 |
|------|------|------|
| use-auth.ts handleSave | **部分修复** | 1.13.3 有 `res.result === 'success'` 检查，但仍缺少 catch 块和错误通知 |
| use-auth.ts handleActive | **未修复** | 1.13.3 仍是 try/finally 没有 catch，无错误提示 |
| use-auth.ts handleDelete | **未修复** | 同上 |
| model-modal try/catch + loading | **未修复** | 1.13.3 仍无 try/catch 和 loading 状态 |
| model-load-balancing-modal | **未修复** | 同上 |
| openai_api_compatible token patches | **DROP** | token null crash 在 2024年11月已上游修复 (#11055)；provider 现在是插件 |
| web-local compose | **KEEP** | 开发便利工具 |

**迁移操作：**
- UX 错误处理改进：需要在 1.13.3 的新代码基础上**重新实现**（文件路径不变但内部逻辑重构了）
- 建议先升级运行，再逐步补充 UX 改进（非阻塞性问题）
- plugin patches 直接丢弃

---

### Commit 8: `ace6d647` — gitignore + copilot instructions

**修改内容：** `.gitignore` +7 行、`copilot-instructions.md` +13 行

**1.13.3 状态：** **ADAPT**（合并 .gitignore 差异）+ **KEEP**（copilot-instructions.md）

**迁移操作：** 合并时手动处理 .gitignore 冲突，保留自定义条目。

---

## 三、推荐迁移步骤

### Phase 0: 准备（30 分钟）
```bash
# 备份当前分支
git branch zxk-dev-backup

# 确保 upstream remote 指向正确
git remote set-url upstream https://github.com/langgenius/dify.git
git fetch upstream --tags

# 抽取需要保留的独立文件/目录
mkdir -p /tmp/dify-patches
cp -r automation/ /tmp/dify-patches/
cp -r scripts/monitoring/ /tmp/dify-patches/
cp api/import_and_run.py /tmp/dify-patches/
cp api/mock_openai.py /tmp/dify-patches/
cp api/dev/test_step2_flexible_offline.py /tmp/dify-patches/
cp api/dump_dsl.py /tmp/dify-patches/
cp api/debug_network.py /tmp/dify-patches/
cp api/check_db_config.py /tmp/dify-patches/
cp DEPLOYMENT.md /tmp/dify-patches/
cp copilot-instructions.md /tmp/dify-patches/
cp docker/docker-compose.override.web-local.yaml /tmp/dify-patches/
cp -r workflows_local/ /tmp/dify-patches/ 2>/dev/null || true
```

### Phase 1: 基于 1.13.3 创建新分支
```bash
git checkout 1.13.3 -b zxk-dev-v2

# 复制回独立文件
cp -r /tmp/dify-patches/automation/ .
cp -r /tmp/dify-patches/monitoring/ scripts/
cp /tmp/dify-patches/import_and_run.py api/
cp /tmp/dify-patches/mock_openai.py api/
cp /tmp/dify-patches/test_step2_flexible_offline.py api/dev/
cp /tmp/dify-patches/dump_dsl.py api/
cp /tmp/dify-patches/debug_network.py api/
cp /tmp/dify-patches/check_db_config.py api/
cp /tmp/dify-patches/DEPLOYMENT.md .
cp /tmp/dify-patches/copilot-instructions.md .
cp /tmp/dify-patches/docker-compose.override.web-local.yaml docker/
cp -r /tmp/dify-patches/workflows_local/ . 2>/dev/null || true

git add -A && git commit -m "chore: restore independent custom files from zxk-dev"
```

### Phase 2: 适配需改造的功能（按优先级）

#### P0 — compose override（阻塞部署）
```bash
# 以 1.13.3 compose 为基础创建新 override
# 只保留: worker_dataset 服务 + mock_openai 服务 + CPU 限制
# - worker_dataset: 用 1.13.3 的 worker 服务为基础，指定 CELERY_QUEUES=dataset
# - mock_openai: 保持原样
# - 注意 1.13.3 新增了 plugin_daemon 和 worker_beat 服务
```

#### P1 — retry-stale-docs CLI（阻塞运维）
```bash
# 创建 api/commands/dataset.py
# 从旧 commands.py 中提取 retry_stale_docs 逻辑
# 调整导入路径（Document model 等）
# 在 api/commands/__init__.py 中注册
```

#### P2 — model.py mock fallback（阻塞离线开发）
```bash
# 对比 1.13.3 的 api/core/plugin/impl/model.py
# 在 fetch_model_providers() 中重新添加 try/except fallback
# 需要对齐新版 PluginModelProviderEntity 的结构
```

#### P3 — LLM 超时重试（验证优先）
```bash
# 首先验证 1.13.3 的 UI retry 是否满足需求：
# 1. 导入一个 essay 工作流 DSL
# 2. 在 LLM 节点配置 retry_config: {max_retries: 3, retry_interval: 1000}
# 3. 测试超时场景
# 如果满足 → 不需要额外改造
# 如果不满足 → 在 import_and_run.py 中自动注入 retry_config
```

#### P4 — Web UX 错误处理（低优先级，不阻塞核心功能）
```bash
# 在 1.13.3 代码基础上重新实现:
# - use-auth.ts: handleActive/handleDelete 添加 catch + error notify
# - model-modal/index.tsx: try/catch + loading state
# - model-load-balancing-modal.tsx: error notify + loading state
```

### Phase 3: 更新 .gitignore 并提交
```bash
# 编辑 .gitignore，追加自定义条目
# 例如: automation/logs/, automation/secrets/, *.pyc 等
git add .gitignore && git commit -m "chore: add custom gitignore entries"
```

### Phase 4: 测试验证
1. `docker compose up -d` — 验证服务启动
2. 安装 xparse 插件（`intsig-textin/xparse`）— 验证子图像提取
3. 导入 essay 工作流 DSL — 验证兼容性
4. 运行 `retry-stale-docs --dry-run` — 验证 CLI
5. 测试 model provider 页面 — 验证 UX

### Phase 5: 数据库迁移
```bash
# 1.9.1 → 1.13.3 有 118 个 DB migration
# 确保先备份数据库！
docker exec -it dify-db pg_dump -U postgres dify > dify_backup_$(date +%Y%m%d).sql

# 升级后自动执行 migrations
docker compose up -d
```

---

## 四、完全可丢弃的改动清单

| 文件/功能 | 原 Commit | 丢弃原因 |
|-----------|-----------|----------|
| `docker/plugin_patches/patch_siliconflow_provider.py` | #1 | Marketplace 插件 |
| `docker/plugin_patches/run_patch.sh`（SiliconFlow 部分） | #1 | 同上 |
| `docker/nginx/conf.d/default.conf.template` 补丁 | #1 | 上游已修复 |
| Qdrant compose 配置 | #1 #5 | 上游原生支持 |
| `docker/.env` gitignore 3个commit | #2 #3 #4 | 上游已覆盖 |
| `api/core/workflow/nodes/code/code_node.py` upper_limit | #6 | 架构已变，问题不存在 |
| `docker/plugin_patches/patch_openai_api_compatible_*.py` | #7 | token 修复已上游 + provider 现在是插件 |

---

## 五、风险与注意事项

1. **数据库迁移**：118 个 migration 文件，必须备份后升级。回退成本高。
2. **dify_graph 外部包**：`error_handler` 和 `llm/node` 的自定义逻辑不能直接 patch，需要找 monkey-patch 方案或放弃。
3. **Plugin 系统**：1.13.3 的 model provider 完全依赖 plugin daemon，如果 daemon 不稳定，model.py mock fallback 的重要性增加。
4. **前端大重构**：React 19 + Next.js 15 + 新 UI 组件库，Web patch 不可直接移植。
5. **automation/ 框架**：依赖 Dify Console API，1.13.3 的 API 路由/接口可能有变化，需回归测试。
6. **import_and_run.py**：依赖内部 Python API，导入路径（如 `core.workflow.*` → `dify_graph.*`）需要全部更新。
