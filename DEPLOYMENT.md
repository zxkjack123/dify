# Dify Custom Deployment Guide

本文档说明如何在新的 macOS 系统上部署 `zxkjack123/dify@plugin_fix` 分支的定制版本。

## 前置要求

在新的 Mac 系统上安装以下依赖：

```bash
# 安装 Homebrew (如未安装)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 安装必需依赖
brew install python@3.12 node@20 git docker docker-compose
```

## 克隆和切换分支

```bash
# 克隆 fork 仓库
git clone https://github.com/zxkjack123/dify.git
cd dify

# 切换到 plugin_fix 分支
git checkout plugin_fix
git pull origin plugin_fix
```

## 后端设置 (API)

### 1. Python 环境

```bash
cd api

# 创建并激活 Python 虚拟环境 (uv or venv)
python3.12 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -U pip uv
uv sync --all-groups
```

### 2. 环境配置

根据部署环境，复制并编辑环境配置文件：

```bash
cp ../docker/.env.example .env
```

编辑 `.env` 文件，配置关键信息：

- **数据库连接**：PostgreSQL/Couchbase/MySQL
- **Redis 连接**：缓存和队列
- **对象存储**：S3/本地文件存储
- **模型供应商 API Key**（如 OpenAI、OpenRouter）

### 3. 数据库迁移

```bash
# 运行数据库迁移
flask db upgrade
```

### 4. 启动服务

开发模式：

```bash
# API 服务
flask run --host=0.0.0.0 --port=5001 --debug

# Worker 服务 (新终端)
celery -A celery_entrypoint worker --loglevel=info
```

生产模式：

```bash
# 使用 gunicorn
gunicorn -c gunicorn.conf.py app:app
```

---

## 前端设置 (Web)

```bash
cd ../web

# 安装依赖 (需要 pnpm)
npm install -g pnpm
pnpm install

# 配置环境变量
cp .env.example .env.local
# 编辑 .env.local，填写后端 API 地址等

# 开发模式
pnpm dev

# 生产构建
pnpm build
pnpm start
```

---

## Docker 部署 (推荐生产环境)

### 1. 环境配置

```bash
cd docker
cp .env.example .env
cp docker-compose.override.yaml.example docker-compose.override.yaml
# 编辑 .env 和 docker-compose.override.yaml
```

### 2. 启动所有服务

```bash
docker-compose up -d
```

关键服务端口：

- API: `http://localhost:5001`
- Web: `http://localhost:3000`
- PostgreSQL: `5432`
- Redis: `6379`

### 3. 查看日志

```bash
docker-compose logs -f api
docker-compose logs -f web
```

---

## 本仓库定制功能

本分支包含以下与上游 Dify 不同的定制修改：

### 1. **LLM 节点增强**
- **超时自动重试**：LLM 节点在遇到超时错误时自动重试（默认 3 次）
- **指数退避**：重试间隔采用指数退避策略（1s → 2s → 4s）
- **错误类型识别**：自动识别超时错误（支持中英文关键词）

**文件**：
- `api/core/workflow/nodes/llm/node.py`
- `api/core/workflow/graph_engine/error_handler.py`

### 2. **插件模型兼容层**
- 添加 `openai_api_compatible` 模型 Mock，当 plugin daemon 不可用时提供降级支持
- 支持自定义 API Base URL 和 Context Size

**文件**：
- `api/core/plugin/impl/model.py`

### 3. **Code 节点补丁**
- 自动注入 `upper_limit` 默认值（当变量为 None 时）

**文件**：
- `api/core/workflow/nodes/code/code_node.py`

### 4. **Workflow 自动化框架**
- 新增 `automation/` 目录，包含：
  - DSL 构建器和验证
  - Workflow 模板管理
  - API 客户端封装
  - 完整的单元测试、E2E 测试、性能测试套件

**文件**：
- `automation/` (完整目录结构)
- `docs/dev/workflow_automation.md`

### 5. **前端改进**
- 浏览器初始化逻辑调整
- Workflow checklist hooks 增强

**文件**：
- `web/app/components/browser-initializer.tsx`
- `web/app/components/workflow/hooks/use-checklist.ts`
- `web/app/components/workflow/hooks/use-nodes-meta-data.ts`
- `web/next.config.js`

### 6. **本地 Workflow 配置**
- `workflows_local/` 包含业务相关的 Essay 写作和批改流程
  - `essay_review/`：AO1/AO2 分项批改流程
  - `essay_write_step2/`：8/12 分 Essay Step 2 灵活流程
  - `ielts-essay-band8-review/`：雅思 Band 8 批改流程

**说明**：这些 workflow 的 DSL 配置已调整为使用 OpenRouter GPT-4.1 和 SiliconFlow reranker。

### 7. **开发工具**
- `api/dev/test_step2_flexible_offline.py`：离线测试 workflow code 节点编译和执行
- `api/check_db_config.py`：检查数据库配置
- `api/debug_network.py`：网络调试工具
- `api/dump_dsl.py`：导出 workflow DSL
- `api/mock_openai.py`：Mock OpenAI API 服务器

---

## workflows_local 说明

本仓库包含 3 套本地 workflow 配置（`workflows_local/` 目录）：

1. **essay_review/**
   - `eaasy_review_ao1_ao2_split.yml` / `eaasy_review_ao1_ao2_split_ecom.yml`：针对 AO1/AO2 的逐段批改流程
   - 使用 OpenRouter GPT-4.1 和 SiliconFlow BAAI/bge-reranker-v2-m3

2. **essay_write_step2/**
   - `[8][12] Essay Step 2 flexible.yml`：支持 8 分和 12 分的 Essay 第二步灵活写作流程
   - 所有 LLM 节点已迁移至 OpenRouter GPT-4.1
   - 所有知识检索节点统一使用 `Ecom-Essay` 数据集
   - AO1 小分解析节点同时支持 JSON 和 outline 输入，自动限制输出不超过 30 个关键词

3. **ielts-essay-band8-review/**
   - 雅思作文 Band 8 批改流程

**部署这些 workflow**：
1. 在 Dify Web UI 中，进入 "工作流" → "导入"
2. 选择对应的 `.yml` 文件上传
3. 确保已配置：
   - OpenRouter API Key（对应 provider `langgenius/openrouter/openrouter`）
   - SiliconFlow API Key（对应 reranker）
   - 知识库 `Ecom-Essay`（dataset id: `10f1ee00-0b33-452d-8564-3d17511ad78b`）

---

## 测试

### 后端单元测试

```bash
cd api
pytest tests/unit_tests/
```

### Automation 测试套件

```bash
cd automation
pytest tests/unit/
pytest tests/e2e/
pytest tests/perf/
```

### Step 2 离线验证

```bash
cd api
uv run --dev dev/pytest/pytest_unit_tests.sh
# 或
python dev/test_step2_flexible_offline.py
```

---

## 常见问题

### 1. 数据库连接失败
- 确保 PostgreSQL/Couchbase 已启动
- 检查 `.env` 中的数据库连接字符串

### 2. Redis 连接失败
- 检查 Redis 服务状态：`redis-cli ping`
- 确认 `.env` 中 Redis 配置正确

### 3. LLM API 超时
- 检查网络连接
- 验证 API Key 和 endpoint 配置
- LLM 节点会自动重试 3 次，间隔使用指数退避

### 4. Workflow 导入失败
- 确保 DSL 文件 `version: 0.4.0` 与 Dify 版本兼容
- 检查所有引用的 provider 和 model 是否已配置

---

## 维护和更新

### 同步上游更新

```bash
# 添加上游仓库 (如未添加)
git remote add upstream https://github.com/langgenius/dify.git

# 拉取上游最新代码
git fetch upstream
git checkout plugin_fix
git merge upstream/main

# 解决冲突后推送
git push origin plugin_fix
```

### 备份配置

定期备份：
- `.env` 文件
- `workflows_local/` 目录
- 数据库（PostgreSQL dump）
- 知识库数据（如 vector store）

---

## 支持

如遇问题，可参考：
- Dify 官方文档：https://docs.dify.ai/
- 本仓库 Issues：https://github.com/zxkjack123/dify/issues
- `docs/dev/workflow_automation.md`：Automation 框架详细说明

---

**最后更新时间**：2025-11-29
