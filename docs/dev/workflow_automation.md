# Dify Workflow 自动化生成/编辑/修改/测试运行方案（客户端/API 驱动版）

> 目标：在 VS Code + Copilot 环境下，实现对 Dify 工作流的"需求→规划→构造→验证→持久化→测试→发布"全链路自动化。
> **核心原则**：自动化工具作为 **Dify API Client** 运行，通过 API 与 Dify 服务端交互，避免直接操作数据库或在本地模拟复杂的后端执行逻辑。

---

## ⚠️ 已知风险与限制

### 1. API 鉴权复杂度
- **Console API** 需要账号登录态（Session/Cookie），不支持简单的 API Key 鉴权
- **Service API** 仅支持发布后的 Workflow 运行，无法调试 Draft
- **现状**：文档中提到的 API Key 方案需调整为 Console Session 管理

### 2. Draft 更新流程限制
- `POST /apps/{app_id}/workflows/draft` 需提供完整的 `graph` + `features` + `hash`（防止并发冲突）
- 必须先 `GET /apps/{app_id}/workflows/draft` 获取当前 hash，否则更新失败
- **风险**：多客户端同时编辑时可能因 hash 不匹配导致冲突

### 3. DSL 导入的两阶段提交
- `POST /apps/imports` 返回 `import_id` + 状态（PENDING/COMPLETED/FAILED）
- 需轮询状态或调用 `POST /apps/imports/{import_id}/confirm` 确认
- **复杂度**：比直接更新 Draft 更重，适合完整替换而非增量修改

### 4. 测试运行的鉴权限制
- **单节点运行**：`POST /apps/{app_id}/workflows/draft/nodes/{node_id}/run` 需要 Console 登录
- **完整运行**：`POST /apps/{app_id}/workflows/draft/run` 同样需要 Console Session
- **无 Service API**：Draft Workflow 不能通过 Service API（app-xxx 密钥）调用

---

## 1. 基础认知

Dify 工作流虽然在数据库中以 `graph` JSON 存储，但为了解耦环境差异（本地开发 vs 远程服务），自动化工具应通过 **OpenAPI** 或 **Internal API** 进行交互。

- **数据源**：通过 API 获取当前工作流的 DSL (YAML) 或 Draft Graph (JSON)。
- **执行引擎**：利用 Dify 服务端真实的执行引擎，而非在本地重写一套模拟器。
- **外部检索**：Crawl4AI 仅作为 Copilot 的辅助工具（Design-time），不嵌入 Runtime。

## 2. 总体自动化流程 (Pipeline)

1. **需求输入 (Specification Intake)**：开发者用自然语言描述目标（例如“构建一个作文评分工作流”）。
2. **LLM 规划 (Planning)**：Copilot 根据需求生成结构化“Workflow Spec JSON”（期望节点列表、输入/输出、模型、工具）。
3. **蓝图生成 (Blueprint Generation)**：将 Spec 转为节点蓝图（Node Blueprints），包含节点类型、变量引用、提示词/代码初稿。
4. **图装配 (Graph Assembly)**：在本地生成符合 Dify 结构的 `graph` JSON 或 DSL YAML。
5. **验证 (Validation)**：本地进行静态语法校验（字段完整性、代码可解析、变量引用链）。
6. **持久化 (Persistence)**：**通过 Dify API** 将生成的 Graph/DSL 推送至服务端更新 Draft。
7. **测试运行 (Execution Testing)**：**调用 Dify Debug API** 触发远程运行，拉取执行日志（Trace Log）进行断言验证。
8. **差异与审查 (Diff & Review)**：生成旧/新 graph 的结构 diff，供代码审查。
9. **自迭代优化 (Refinement)**：根据 API 返回的错误日志或输出结果，对 prompt 或 code 节点做增量 patch。
10. **发布 (Publish)**：调用 API 将 Draft 发布为稳定版本。

## 3. 模块划分与职责

| 模块               | 文件建议                            | 职责                                                |
| ------------------ | ----------------------------------- | --------------------------------------------------- |
| 规格解析           | `automation/spec_parser.py`         | 自然语言 → 结构化 Spec JSON                         |
| 蓝图生成           | `automation/blueprint_generator.py` | Spec → Node Blueprints                              |
| **DSL 生成**       | `automation/dsl_builder.py`         | **[重要]** Blueprints → Dify DSL YAML（推荐主路径） |
| 图装配             | `automation/graph_builder.py`       | Blueprints → 内部 Graph JSON（备用，风险高）        |
| 验证               | `automation/graph_validator.py`     | 静态检查：必填字段、代码语法、变量引用              |
| **Console 客户端** | `automation/dify_console_client.py` | **[新]** 封装 Console API 请求，管理 Session 鉴权   |
| **远程运行**       | `automation/remote_runner.py`       | **[新]** 调用 Draft Run API，解析 SSE/JSON 响应     |
| Diff               | `automation/graph_diff.py`          | DSL YAML 差异对比（推荐）或 JSON Patch              |
| 自迭代             | `automation/auto_refine.py`         | 根据节点执行错误生成修复建议                        |
| 冲突合并           | `automation/merge_strategy.py`      | **[新]** 处理 hash 冲突的三路合并逻辑               |
| CLI                | `automation/cli.py`                 | 命令行入口（gen/push/run/diff/publish）             |

**关键变更说明**：
1. **优先使用 DSL YAML**：避免直接拼接内部 Graph JSON，降低版本兼容性风险
2. **Session 管理**：`dify_console_client.py` 需实现登录流程（账号密码或 Cookie 保持）
3. **冲突处理**：当 hash 不匹配时，需要智能合并本地修改和远程变更

---

## 4. 节点蓝图结构示例（内部模型）

*(保持不变，用于本地生成逻辑)*

```jsonc
{
  "nodes": [
    {
      "name": "ExtractEssayText",
      "type": "code",
      "purpose": "清理与标准化原始作文输入",
      "inputs": ["sys.files"],
      "outputs": ["essay_text"],
      "code_hint": "# 将上传文件内容读取并转为纯文本"  
    },
    // ...
  ]
}
```

## 5. Graph 构造要点（仅当无法使用 DSL 时）

⚠️ **警告**：直接构造 Graph JSON 属于**高风险操作**，仅在以下情况下考虑：
- DSL 导入失败且无法通过官方解析器修复
- 需要操作 DSL 尚未支持的实验性节点类型

### 5.1 基本结构
```jsonc
{
  "nodes": [
    {
      "id": "1732180234567",  // 时间戳或 UUIDv4
      "position": { "x": 100, "y": 200 },
      "data": {
        "title": "Node Title",
        "type": "llm",  // 节点类型
        // ... 类型特定字段
      }
    }
  ],
  "edges": [
    {
      "id": "edge-xxx",
      "source": "1732180234567",
      "target": "1732180234999",
      "sourceHandle": "source",  // 或具体输出名
      "targetHandle": "target"
    }
  ],
  "viewport": { "x": 0, "y": 0, "zoom": 1 }
}
```

### 5.2 关键风险点
| 风险             | 描述                              | 后果              | 缓解措施                                                  |
| ---------------- | --------------------------------- | ----------------- | --------------------------------------------------------- |
| **内部字段变更** | Dify 升级后 `data` 结构可能调整   | 前端渲染错误/白屏 | 仅生成当前版本兼容的结构；定期对比最新导出 DSL            |
| **必填字段缺失** | 缺少 `data.outputs` 等字段        | 后端验证失败      | 严格遵循 `WorkflowService.sync_draft_workflow` 的验证逻辑 |
| **变量引用错误** | `value_selector` 指向不存在的节点 | 运行时报错        | 本地拓扑排序验证所有引用链                                |
| **ID 冲突**      | 节点 ID 重复                      | 渲染异常          | 使用 `uuid.uuid4()` 或带时间戳的唯一 ID                   |

### 5.3 推荐做法
1. **学习模式**：手动在 Dify UI 创建目标节点 → 导出 DSL → 研究生成的 Graph 结构
2. **模板库**：预先准备常用节点的 JSON 模板，减少从零拼装
3. **双重验证**：生成后立即推送到测试 App，观察前端是否正常渲染

---

## 6. 验证策略 (本地静态)

| 类型 | 检查内容                             | 处理方式      |
| ---- | ------------------------------------ | ------------- |
| 结构 | 必填字段（`id`, `data`, `position`） | 阻断          |
| 代码 | Python `ast.parse()` 语法检查        | 警告/自动修复 |
| 变量 | 引用链（Reference）是否存在          | 阻断          |

## 7. 持久化与版本 (API 驱动)

### 7.1 鉴权配置
- **Console API 访问**：需要账号 Session（通过浏览器登录后获取 Cookie）或实现 OAuth 流程
- **配置文件**：`.env` 管理
  ```bash
  DIFY_CONSOLE_URL=http://localhost/console/api
  DIFY_SESSION_COOKIE=<从浏览器开发者工具获取>
  # 或使用账号密码自动登录（需实现）
  DIFY_EMAIL=your@email.com
  DIFY_PASSWORD=your_password
  ```
- **安全提示**：Session Cookie 敏感，禁止提交到 Git；CI 环境需使用专用服务账号

### 7.2 更新 Draft 的两种方式

#### 方式 A：直接更新 Draft Graph（推荐用于增量修改）
**API**: `POST /console/api/apps/{app_id}/workflows/draft`

**流程**：
1. `GET /console/api/apps/{app_id}/workflows/draft` 获取当前 `graph`, `features`, `hash`
2. 修改 `graph` 中的节点/边
3. `POST` 回去，附带旧的 `hash` 值（防止并发冲突）
4. 返回新的 `hash` 和 `updated_at`

**错误处理**：
- `400 draft_workflow_not_sync`：hash 不匹配，需重新获取最新版本并合并变更
- **建议**：实现三路合并（three-way merge）或提示用户手动解决冲突

**限制**：
- 需要精确构造符合内部格式的 `graph` JSON（易因版本升级而失效）

#### 方式 B：导入 DSL YAML（推荐用于完整替换）
**API**: `POST /console/api/apps/imports`

**流程**：
1. 本地生成符合 Dify DSL 标准的 YAML
2. 调用 `POST /apps/imports` 传入：
   ```json
   {
     "mode": "yaml_content",  // 或 yaml_url
     "yaml_content": "<DSL YAML>",
     "app_id": "existing_app_id"  // 覆盖模式
   }
   ```
3. 返回 `import_id` 和 `status`：
   - `COMPLETED`: 直接成功
   - `PENDING`: 需调用 `POST /apps/imports/{import_id}/confirm`
   - `FAILED`: 查看 `error` 字段原因
4. 轮询或 confirm 后，Draft 被完整替换

**优势**：
- 利用 Dify 原生的 DSL 解析器，兼容性更好
- 自动处理版本迁移和结构补全

**劣势**：
- 无法做增量修改，每次都是全量替换

### 7.3 版本管理建议
- **导出备份**：修改前先 `GET /apps/{app_id}/export?include_secret=false` 保存当前版本
- **Git 管理**：将导出的 YAML 提交到 Git，利用 diff 追踪变更
- **回滚机制**：保留最近 N 个导出版本，支持快速回滚

### 7.4 插件依赖处理
自动化工具需确保目标环境具备 Workflow 所需的插件（如 Google Search, Web Scraper）：
1. **检测**：调用 `GET /console/api/apps/imports/{app_id}/check-dependencies`
2. **分析**：解析返回的 `plugin_dependencies` 列表
3. **策略**：
   - **缺失**：CLI 报错并列出缺失插件，提示用户在 UI 安装（因涉及授权，暂不支持自动安装）
   - **版本不匹配**：发出警告，允许用户选择强制继续或中止

### 7.5 环境变量与密钥注入
DSL 导入通常不包含敏感密钥（Secrets），CI/CD 部署时需额外注入：

**流程**：
1. **准备密钥文件**：`secrets.prod.json` (仅在 CI 环境生成，不提交 Git)
   ```json
   {
     "OPENAI_API_KEY": "sk-...",
     "GOOGLE_SEARCH_API_KEY": "..."
   }
   ```
2. **执行注入**：
   - `wf push` 完成后，读取密钥文件
   - 调用 `POST /console/api/apps/{app_id}/environment-variables` (需确认端点) 或通过 `features` 更新接口
   - 将密钥值填入对应的 `environment_variables` 字段

---

## 8. 测试运行 (远程调试)

### 8.1 鉴权要求
⚠️ **所有 Draft Workflow 调试接口均需 Console Session 鉴权，不支持 Service API Key**

### 8.2 单节点测试
**API**: `POST /console/api/apps/{app_id}/workflows/draft/nodes/{node_id}/run`

**请求**：
```json
{
  "inputs": {
    "essay_text": "Sample essay...",
    "rubric_ao1": "Scoring guideline..."
  }
}
```

**响应**：
```json
{
  "id": "exec_xxx",
  "status": "succeeded",  // 或 failed
  "outputs": {
    "ao1_score": 5,
    "ao1_reason": "..."
  },
  "error": null,
  "elapsed_time": 1.23,
  "total_tokens": 450
}
```

**使用场景**：快速验证单个节点逻辑（特别是 LLM/Code 节点）

### 8.3 完整流程测试
**API**: `POST /console/api/apps/{app_id}/workflows/draft/run`

**请求**：
```json
{
  "inputs": {
    "sys.files": [
      {
        "type": "image",
        "transfer_method": "local_file",
        "upload_file_id": "file-uuid-xxx"
      }
    ],
    "custom_var": "value"
  },
  "files": [...]  // 可选，用于覆盖 sys.files
}
```

**响应**：Streaming SSE 或 Blocking JSON，包含：
- `event: workflow_started`
- `event: node_started` / `node_finished`
- `event: workflow_finished`

**解析策略**：
1. 收集所有 `node_finished` 事件，建立 `node_id → outputs` 映射
2. 检查最终 `workflow_finished` 的 `status`
3. 提取关键节点的 `outputs` 进行断言

### 8.4 文件上传管理
若测试用例涉及文件输入（如 `sys.files`），需先调用上传接口获取 ID：

**Console API (Draft 调试)**:
- Endpoint: `POST /console/api/files/upload`
- Form-Data: `file=@local_path.pdf`
- Response: `{ "id": "uuid", "name": "..." }`

**Service API (生产运行)**:
- Endpoint: `POST /v1/files/upload`
- Header: `Authorization: Bearer app-xxx`
- Form-Data: `file=@local_path.pdf`
- Response: `{ "id": "uuid", ... }`

**注意**：Console API 上传的文件属于当前用户，Service API 上传的文件属于 EndUser，两者 ID 不通用。

### 8.5 发布后测试（生产验证）
**API**: `POST /v1/workflows/run` (Service API)

**差异**：
- 使用 `app-xxx` 密钥（而非 Session）
- 仅能调用已发布的 Workflow
- 返回 `workflow_run_id` 用于异步查询

**建议**：
- Draft 调试完成后，调用 `POST /console/api/apps/{app_id}/workflows/publish` 发布
- 使用 Service API 进行最终的回归测试和性能压测

---

## 9. Diff 引擎

- **Local vs Remote**：比较本地生成的 Graph 与通过 API 获取的远程 Draft。
- **DSL Diff**：比较 YAML 文本差异，直观展示逻辑变动。

## 10. 自迭代优化 (Refine)

1. **运行失败**：`remote_runner` 捕获 API 返回的错误信息（如 Code 节点执行异常）。
2. **LLM 修复**：将错误堆栈 + 原代码 + 意图 发送给 Copilot。
3. **Patch**：生成修正后的代码/Prompt，更新本地 Graph。
4. **重试**：再次 Push Draft 并触发 Remote Run。

## 11. 安全与机密处理

### 11.1 鉴权信息保护
- **Session Cookie**：敏感度极高，泄露后可完全接管账号
  - 本地存储：使用加密存储（如 `keyring` 库）
  - CI/CD：使用 Secrets（GitHub Actions: `secrets.DIFY_SESSION`）
  - 过期检测：401 响应时自动重新登录
  
- **账号密码**：如需存储，必须加密
  - 推荐：使用系统密钥环（macOS Keychain / Windows Credential Manager）
  - 禁止：明文写入 `.env` 文件并提交到 Git

### 11.2 Code 节点安全扫描
虽然代码最终在 Dify 服务端执行，本地生成时仍应扫描：

**禁用列表（AST 静态检查）**：
```python
BANNED_IMPORTS = ['os', 'subprocess', 'sys', 'importlib']
BANNED_CALLS = ['eval', 'exec', '__import__', 'open', 'compile']
```

**允许列表（沙箱安全）**：
```python
ALLOWED_IMPORTS = ['json', 're', 'math', 'datetime', 'typing']
```

**检查流程**：
1. 使用 `ast.parse()` 解析 Code 节点代码
2. 遍历 AST，检测 `Import`, `Call` 节点
3. 发现违规项 → 阻止生成并提示 LLM 重写

### 11.3 Prompt 注入防护
- **系统提示词隔离**：确保用户输入变量不会污染 System Message
- **输出格式校验**：强制 JSON Schema 验证，防止 LLM 输出逃逸

### 11.4 日志脱敏
- 运行日志中包含的 `inputs` 可能含敏感信息（如学生 PII）
- 建议：本地存储时自动脱敏关键字段（姓名、邮箱、ID 号）

---

## 12. CI/CD 集成建议

| 阶段             | 动作                                              |
| ---------------- | ------------------------------------------------- |
| Lint             | 校验自动化脚本本身                                |
| Build            | `wf gen` 生成 DSL/Graph                           |
| Deploy (Staging) | `wf push --target staging` 更新测试应用           |
| E2E Test         | `wf run --target staging` 触发远程测试            |
| Deploy (Prod)    | `wf publish --target prod` (需谨慎，通常人工确认) |

## 13. 端到端示例 (CLI)

```bash
# 1. 配置鉴权（选择其一）
# 方式 A: 使用浏览器 Session Cookie
export DIFY_CONSOLE_URL="http://localhost/console/api"
export DIFY_SESSION="session=xxx; _ga=xxx"  # 从浏览器开发者工具复制

# 方式 B: 使用账号密码自动登录
export DIFY_EMAIL="dev@example.com"
export DIFY_PASSWORD="your_password"

# 2. 生成 DSL YAML（而非直接生成 Graph JSON）
wf gen --spec specs/essay_scoring.txt --output essay_scoring.yml

# 3. 预览并验证
wf validate --file essay_scoring.yml

# 4. 推送到 Dify 更新 Draft（覆盖模式）
wf push --file essay_scoring.yml --app-id "uuid" --mode overwrite

# 5. 单节点测试
wf run-node --app-id "uuid" --node-id "ScoreAO1" --inputs '{"essay_text":"..."}'

# 6. 完整流程测试
wf run --app-id "uuid" --inputs inputs/test_case_1.json --wait

# 7. 查看结果
# > [SUCCESS] Workflow completed in 3.2s
# > [OUTPUT] final_score: 85

# 8. 发布稳定版本
wf publish --app-id "uuid"

# 9. 使用 Service API 进行生产验证
export DIFY_API_KEY="app-xxx"
wf run-prod --workflow-id "wf_xxx" --inputs inputs/batch.json
```

**注意事项**：
- `wf push` 会先获取当前 Draft 的 hash，确保无冲突
- 如果遇到 `draft_workflow_not_sync` 错误，CLI 会提示用户选择：
  - `pull`: 拉取远程最新版本，放弃本地修改
  - `merge`: 尝试三路合并
  - `force`: 强制覆盖（危险）

---

## 14. 与外部资料检索的关系

- **Design-time Only**：Crawl4AI 仅在 `wf gen` 阶段运行，帮助 Copilot 理解领域知识（如“查找最新的雅思写作评分标准”），生成的知识直接硬编码到 Prompt 或作为知识库上下文配置。
- **Runtime**：Dify 服务端运行时不依赖 Crawl4AI。

## 15. 下一步行动清单

### 阶段 1：基础设施搭建
1. **Console API 客户端**：实现 `dify_console_client.py`
   - [ ] Session 登录（账号密码）
   - [ ] Session Cookie 持久化与刷新
   - [ ] 封装 GET/POST 请求，自动处理 401 重新登录
   
2. **DSL 生成器**：实现 `dsl_builder.py`
   - [ ] 学习 Dify DSL YAML 结构（参考 `AppDslService.export_dsl` 输出）
   - [ ] 从 Node Blueprints 生成符合标准的 YAML
   - [ ] 单元测试：验证生成的 YAML 可被 Dify 导入

### 阶段 2：核心功能实现
3. **Draft 同步逻辑**：实现 `workflow_persistence.py`
   - [ ] 获取当前 Draft（含 hash）
   - [ ] 检测 hash 冲突，实现冲突提示
   - [ ] 调用 DSL 导入 API 覆盖 Draft
   
4. **远程运行**：实现 `remote_runner.py`
   - [ ] 单节点运行并解析结果
   - [ ] 完整流程运行（处理 SSE streaming）
   - [ ] 错误日志提取与结构化

### 阶段 3：CLI 与自动化
5. **CLI 工具**：实现 `cli.py`
   - [ ] `wf gen` 命令（调用 Copilot 生成 Spec）
   - [ ] `wf push` 命令（DSL 导入）
   - [ ] `wf run` 和 `wf run-node` 命令
   - [ ] `wf publish` 命令
   
6. **冲突解决**：实现 `merge_strategy.py`
   - [ ] 三路合并算法（base, local, remote）
   - [ ] 交互式冲突解决（CLI 提示用户选择）

### 阶段 4：测试与文档
7. **集成测试**：
   - [ ] 端到端测试：从 Spec → DSL → Push → Run → Publish
   - [ ] 冲突场景测试：模拟并发修改
   
8. **用户文档**：
   - [ ] 快速开始指南（含鉴权配置）
   - [ ] CLI 命令参考
   - [ ] 故障排查（常见错误码解释）

### 阶段 5：高级特性（可选）
9. **自迭代优化**：实现 `auto_refine.py`
10. **可视化工具**：生成 Workflow 拓扑图
11. **批量测试**：支持测试用例集自动运行

---

## 16. 变更记录与风险评估

| 版本     | 变化点                                  | 原因                              | 风险等级                 |
| -------- | --------------------------------------- | --------------------------------- | ------------------------ |
| v1       | 本地模拟执行，直接写库                  | 环境割裂，模拟器难以维护          | 🔴 高（部署时行为不一致） |
| **v2**   | **API 驱动，远程调试**                  | **解耦环境，利用原生执行引擎**    | 🟡 中（Session 管理复杂） |
| **v2.1** | **明确 Console API 鉴权需求**           | **发现 Service API 不支持 Draft** | 🟢 低（文档已说明）       |
| **v2.2** | **推荐 DSL YAML 优先，Graph JSON 备用** | **降低版本兼容性风险**            | 🟢 低（利用官方解析器）   |

### 当前方案的核心风险
1. **Session 管理复杂度** 🟡
   - **影响**：需要处理登录、Cookie 过期、刷新
   - **缓解**：实现健壮的自动重连机制；或使用专用服务账号长期 Session
   
2. **并发冲突处理** 🟡
   - **影响**：多人/多工具同时编辑 Draft 时 hash 冲突
   - **缓解**：实现三路合并；建议团队约定：自动化工具独占编辑权，人工编辑前先禁用自动化

3. **DSL 结构学习成本** 🟢
   - **影响**：需要深入理解 Dify DSL YAML 格式
   - **缓解**：通过手动创建示例 Workflow 并导出 DSL 学习；参考 `AppDslService` 源码

4. **API 文档缺失** 🟡
   - **影响**：Console API 无公开文档，需逆向工程前端代码
   - **缓解**：本文档已整理核心端点；建议 Dify 官方补充 API 文档

### 未来改进方向
- [ ] 请求 Dify 官方提供 Console API 的正式文档和 SDK
- [ ] 支持 WebSocket 连接替代轮询（更高效的 Session 保持）
- [ ] 实现 Workflow 版本化管理（类似 Git 分支）

---

## 附录 A：Dify Console API 端点参考

### A.1 鉴权相关
| 端点                  | 方法 | 说明                              |
| --------------------- | ---- | --------------------------------- |
| `/console/api/login`  | POST | 账号密码登录，返回 Session Cookie |
| `/console/api/logout` | POST | 登出当前 Session                  |

### A.2 应用管理
| 端点                                | 方法 | 说明                                                   |
| ----------------------------------- | ---- | ------------------------------------------------------ |
| `/console/api/apps`                 | GET  | 列出所有应用                                           |
| `/console/api/apps/{app_id}`        | GET  | 获取应用详情                                           |
| `/console/api/apps/{app_id}/export` | GET  | 导出 DSL YAML<br>参数: `include_secret`, `workflow_id` |

### A.3 DSL 导入
| 端点                                                    | 方法 | 说明                                                           |
| ------------------------------------------------------- | ---- | -------------------------------------------------------------- |
| `/console/api/apps/imports`                             | POST | 导入 DSL（新建或覆盖）<br>Body: `{mode, yaml_content, app_id}` |
| `/console/api/apps/imports/{import_id}/confirm`         | POST | 确认异步导入（当状态为 PENDING 时）                            |
| `/console/api/apps/imports/{app_id}/check-dependencies` | GET  | 检查插件依赖                                                   |

### A.4 Draft Workflow 管理
| 端点                                           | 方法 | 说明                                                                 |
| ---------------------------------------------- | ---- | -------------------------------------------------------------------- |
| `/console/api/apps/{app_id}/workflows/draft`   | GET  | 获取 Draft（含 graph, features, hash）                               |
| `/console/api/apps/{app_id}/workflows/draft`   | POST | 更新 Draft<br>Body: `{graph, features, hash, environment_variables}` |
| `/console/api/apps/{app_id}/workflows/publish` | POST | 发布 Draft 为稳定版本                                                |

### A.5 Draft 调试
| 端点                                                             | 方法 | 说明                                               |
| ---------------------------------------------------------------- | ---- | -------------------------------------------------- |
| `/console/api/apps/{app_id}/workflows/draft/run`                 | POST | 运行完整 Draft Workflow<br>Body: `{inputs, files}` |
| `/console/api/apps/{app_id}/workflows/draft/nodes/{node_id}/run` | POST | 运行单个节点<br>Body: `{inputs}`                   |

### A.6 文件管理
| 端点                        | 方法 | 鉴权             | 说明                                           |
| --------------------------- | ---- | ---------------- | ---------------------------------------------- |
| `/console/api/files/upload` | POST | Session          | 上传文件（Draft 调试用）<br>Form: `file`       |
| `/v1/files/upload`          | POST | Bearer `app-xxx` | 上传文件（生产运行用）<br>Form: `file`, `user` |

### A.7 Service API（发布后使用）
| 端点                          | 方法 | 鉴权             | 说明                  |
| ----------------------------- | ---- | ---------------- | --------------------- |
| `/v1/workflows/run`           | POST | Bearer `app-xxx` | 运行已发布的 Workflow |
| `/v1/workflows/runs/{run_id}` | GET  | Bearer `app-xxx` | 查询运行状态          |

**关键差异**：
- **Console API**：需要 Session Cookie，可访问 Draft
- **Service API**：使用 API Key，只能访问已发布版本

---

## 附录 B：错误码参考

| 错误码                     | HTTP | 说明         | 解决方案                              |
| -------------------------- | ---- | ------------ | ------------------------------------- |
| `draft_workflow_not_exist` | 404  | Draft 不存在 | 首次需要在 UI 中手动创建应用          |
| `draft_workflow_not_sync`  | 400  | hash 不匹配  | 重新 GET Draft，合并变更后重试        |
| `unauthorized`             | 401  | Session 过期 | 重新登录获取新 Cookie                 |
| `app_not_found`            | 404  | 应用不存在   | 检查 `app_id` 是否正确                |
| `import_failed`            | 400  | DSL 导入失败 | 检查 YAML 格式；查看 `error` 字段详情 |

---
