# Dify Workflow 自动化实施方案（增强版 v2.3）

> 基于 `docs/dev/workflow_automation.md` 的架构规范，并融合风险与优化建议的迭代版本。
> 本实施方案不仅给出任务拆分，还补充验收标准 (Acceptance Criteria)、测试分层、KPI 指标、风险矩阵、可扩展架构与 CLI 扩展命令。

## 🔄 变更概要（相对上一版）
- 前置“插件依赖校验 & 密钥注入”到核心路径，避免后期回填。
- 增加“最小闭环 Vertical Slice（MVP）”策略：最快得到可运行端到端工作流。
- 引入架构分层（clients / domain / services / adapters / infra）。
- 为关键任务补充验收标准与失败模式说明。
- 增加 DSL `meta.version` 与 `dry-run` / `diff` 能力，提升变更透明度与安全性。
- 提出测试金字塔：Unit / Contract / E2E / Property / Regression / Performance。
- 前置安全：AST 扫描、Secrets 管理、审计日志、错误分类。
- 扩展 CLI：`dry-run`, `diff`, `template`, `alias`, `secrets`, `rotate-secrets`, `audit-log`, `verbose`。
- 增加 KPI 与风险矩阵，便于后续持续度量与治理。

## 🧱 架构分层建议
```
automation/
  infra/          # 配置、日志、重试、审计、缓存
  clients/        # Console / Service / Plugin API 封装
  domain/         # 核心领域模型：WorkflowSpec, NodeBlueprint, ExecutionResult
  services/       # 用例服务：GenerateSpec, ImportWorkflow, RunDraft, DiffWorkflow
  adapters/       # LLM、Secrets Provider、外部插件对接
  cli/            # CLI 命令入口与解析
  tests/          # unit / contract / e2e / fixtures / regression / perf
```
好处：隔离变更、可测试性提升、便于未来扩展插件生态与多 App 管理。

## 🚀 阶段 0：最小闭环 (Vertical Slice MVP)
**目标**：在最短时间内打通最小端到端路径：配置加载 → 登录 → 构建最简单 DSL → push → 远程运行 → 输出结果。
**范围**：仅支持 `start -> llm -> end`，不含分支/代码节点/插件。
**验收标准**：
- 单条命令或脚本完成：`wf mvp --prompt '翻译：Hello'`。
- 运行结果返回包含 LLM 响应文本，耗时 < 3s（网络正常条件下）。
- 失败场景（鉴权错误/DSL 校验失败）输出结构化错误（包含 error_code / message / suggestion）。

### 任务 0.1：BaseSettings 配置
- 使用 `pydantic.BaseSettings` 声明必需变量：`DIFY_CONSOLE_URL`, `DIFY_EMAIL`, `DIFY_PASSWORD`。 
- 支持 `.env` + 环境变量优先级。
- 提供 `validate()` 输出缺失项。
**验收**：变量缺失时返回精确列表；不泄露明文密码于日志。

### 任务 0.2：最小 Console Client
- 支持 `login()` + `get_apps()`。
- 自动处理过期 Cookie：首次 401 静默重试一次。
**验收**：
  - 登录失败区分：`invalid_credentials` vs `network_error`。
  - 重试后仍失败抛出 `AuthenticationError`。

### 任务 0.3：最小 DSL Builder
- 生成 3 节点结构：start / llm / end。
- 注入输入变量与输出映射。
**验收**：YAML 内含 `graph.nodes` 数量=3；`edges`=2；可被导入。

### 任务 0.4：最小 push & run
- `push_dsl()`：调用 `/console/api/apps/imports` (Method B) 全量覆盖；失败返回结构化错误。
- `run_workflow()`：发起远程运行，聚合最终输出。
- **前置检查**：运行前检查 Dify 实例是否已配置默认 LLM 供应商（避免 MVP 运行失败）。
**验收**：完整结果含最终 end 节点输出；SSE 中断自动重连一次。


## 🏗 阶段一：基础设施与安全基线 (Infrastructure & Security Baseline)
**新增安全前置**：早期集成有助于后续流程不返工。

### 任务 1.1：目录与分层初步落地
- 建立上述分层骨架与占位文件。
**验收**：`tree automation` 显示至少含 `infra/ clients/ domain/ services/ cli/ tests/`。

### 任务 1.2：增强 Console API 客户端
- 增加 `_request()` 重试逻辑（`backoff`：指数退避，最大 3 次）。
- 设计错误分类：`AuthError` / `NetworkError` / `RateLimitError` / `ServerError`。
- 审计日志记录：endpoint + 耗时 + 状态码 + trace_id。
**验收**：
  - 连续网络超时可重试，超出次数抛出 `NetworkError`。
  - 日志不含敏感字段（密码、Cookie）。

### 任务 1.3：配置与 Secrets 基线
- 将密码/Key 标记敏感；日志中统一使用 `<redacted>`。
- 提供 `secrets validate` 命令：检测所需密钥是否齐全。
**验收**：缺失密钥输出 JSON：`{"missing": [...]}`。

### 任务 1.4：审计与指标收集基础
- 审计日志写入本地 `automation_audit.log`。
- 基础指标：请求耗时、成功率、失败类型分布。
**验收**：运行 10 次 `get_apps()` 后日志有 10 条记录，指标聚合脚本可输出统计。

## 🧠 阶段二：核心逻辑实现 (Core Logic)
扩展原 2.x，前置插件依赖校验、DSL 版本与 dry-run。

### 任务 2.1：DSL Builder 完整版
- 支持节点类型：`start`, `end`, `llm`, `code`, `if-else`, `parallel`, `aggregate`。
- 增加 DSL `meta`：`version`, `generated_at`, `generator`。
- 内置 `schema validate`：若结构缺字段抛出 `DSLValidationError`。
**验收**：随机生成 20 个节点的 DSL 仍可通过 schema 校验；`meta.version` 初始为 `1`。

### 任务 2.2：Spec Parser（LLM 驱动）
- System Prompt 约束输出 JSON Schema；失败重试 2 次。
- Fallback：输出最小 spec（仅 start -> llm -> end）。
**验收**：
  - 输入“情感分析工作流”→ 输出包含 input 描述 / llm 任务 / output 映射。
  - 非法输出（如非 JSON）进入重试逻辑。

### 任务 2.3：插件依赖与 DSL dry-run/diff
- `check_dependencies()`：列出缺失插件 ID。
- `dry_run()`：不真正导入，只做 schema + 依赖 + 结构分析。
- `diff(remote_dsl, local_dsl)`：输出节点新增/删除/修改摘要。
**验收**：
  - 缺失插件时 `push_dsl()` 阻断并列出清单。
  - `dry_run` 返回结构：`{"valid": true, "missing_plugins": [], "changes": {...}}`。

### 任务 2.4：持久化模块增强 (`workflow_persistence.py`)
- **API 选择**：明确使用 `/console/api/apps/imports` (DSL 模式) 以利用官方解析器。
- 增加幂等性：计算 DSL 内容哈希，若与远端一致跳过导入。
- 处理状态：`PENDING` → 自动确认；`FAILED` → 返回详细原因。
- 冲突检测（乐观锁）：
  1. 获取远端 Draft hash。
  2. 比较本地缓存的 "base hash" 与远端是否一致。
  3. 不一致则提示 "Remote Changed"，需 `pull` 或 `force`。
**验收**：多次重复 push 不会重复导入；修改后哈希变化再导入。


## 🏃 阶段三：执行引擎与稳健性 (Execution Engine & Robustness)
### 任务 3.1：SSE 流运行器
- 解析事件：`node_started`, `node_finished`, `workflow_finished`, `error`。
- 断线重连：保留 last event id，一次自动重连。
- 超时策略：节点超时 vs 全局超时分开配置。
**验收**：模拟断线仍能完整收集输出；超时触发后工作流终止返回错误码。

### 任务 3.2：单节点运行调试
- 支持注入临时变量覆盖工作流默认变量。
- 输出包含：节点输入/输出/执行耗时。
**验收**：LLM 节点调试时输出 token usage（若 API 支持）。

### 任务 3.3：执行结果审计与指标
- 将一次运行摘要写入 `runs_history.jsonl`。
- 聚合统计：平均耗时、失败率、最慢节点列表。
**验收**：运行 5 次后统计脚本输出 JSON 指标表。

## 🖥 阶段四：CLI 与开发者体验 (Automation Interface & DX)
### 任务 4.1：CLI 基础命令
- `wf gen` / `wf push` / `wf run`。
**验收**：帮助信息含示例；异常输出结构化。

### 任务 4.2：CLI 扩展命令
- `wf dry-run`：验证 DSL。
- `wf diff`：展示与远端差异。
- `wf template list/apply`：基于内置模板库。
- `wf alias set/get`：维护 app 别名映射。
- `wf secrets set/list/rotate`：密钥管理与轮换。
- `wf audit show`：展示最近操作记录。
- `wf run-node`：单节点调试。
**验收**：所有命令均支持 `--json` 输出；rotate 生成新占位并提示更新。

### 任务 4.3：E2E 与回归测试集
- 构建 10 个典型工作流（翻译 / 情感分析 / 条件分支 / 并行聚合 / 代码执行 / 插件调用等）。
- 脚本一键验证：全量成功率必须 100%。
**验收**：`python -m automation.tests.e2e.run_all` 返回所有通过。

## 🧪 阶段五：测试体系与质量保障 (Testing & Quality)
### 任务 5.1：单元测试 (unit)
- 覆盖：DSL 构建、哈希、diff、重试逻辑。
**指标**：语句覆盖率 ≥ 80%。

### 任务 5.2：合约测试 (contract)
- 固定 API 响应 fixture（录制或模拟）。
**验收**：版本升级后 diff 测试能捕获 schema 漂移。

### 任务 5.3：属性测试 (property-based)
- 随机生成节点图，验证 builder 不崩溃且输出合法结构。

### 任务 5.4：回归测试 (regression)
- 每次变更跑基准集，保证无功能退化。

### 任务 5.5：性能与负载测试 (perf)
- 压测并发执行 20 次运行；记录平均耗时与资源消耗。

## 🔐 阶段六：高级特性与安全增强 (Advanced & Security)
### 任务 6.1：冲突处理与三路合并
- **策略**：由于 DSL Import 是全量覆盖，真正的三路合并（Merge）难度较大。
- **MVP 策略**：检测到冲突时，提供 `wf pull` 拉取远端 YAML，由用户在本地 IDE 解决冲突（利用 Git/IDE 的 diff 能力），解决后再 push。
- **进阶**：尝试基于 YAML 结构的自动合并（仅无冲突字段）。


### 任务 6.2：Code 节点安全扫描
- AST + 正则双层：阻止 `os.system`, `subprocess`, 网络外泄调用。
**验收**：危险样例被阻断并给出替代建议。

### 任务 6.3：Secrets 轮换与最小权限
- 支持读取过期策略，提示即将到期的密钥。

### 任务 6.4：审计与可追踪性增强
- 每次 push/run 生成 trace_id；用户可查询指定 trace 的操作链。

## 📊 KPI 指标与验收矩阵
| 类别       | 指标               | 目标值        | 说明                   |
| ---------- | ------------------ | ------------- | ---------------------- |
| DSL 导入   | 成功率             | ≥ 98%         | 不含插件缺失导致的阻断 |
| 规格解析   | 结构正确率         | ≥ 90%         | 人工标注集字段匹配     |
| 执行稳定性 | SSE 完整率         | ≥ 99%         | 不丢失事件             |
| 运行性能   | 单次简单工作流耗时 | ≤ 1.2x 控制台 | 常规网络条件           |
| 安全扫描   | 漏报率             | ≤ 5%          | 通过标注样例测评       |
| CLI 体验   | 常用命令冷启动耗时 | ≤ 3s          | gen/push/run           |
| 覆盖率     | 单元测试覆盖       | ≥ 80%         | 语句/分支并列跟踪      |
| 回归稳健   | 基准集通过率       | 100%          | 10 个工作流样例        |

## ⚠️ 风险矩阵与缓解
| 风险            | 描述         | 影响           | 缓解措施                           |
| --------------- | ------------ | -------------- | ---------------------------------- |
| DSL Schema 漂移 | 平台格式更新 | 导入失败       | schema 版本 + 合约测试 + 兼容层    |
| LLM 幻觉        | 输出结构缺失 | 生成错误工作流 | JSON Schema 校验 + 重试 + fallback |
| 插件缺失        | 运行时报错   | 流程中断       | 前置依赖扫描 + 缺失清单            |
| SSE 中断        | 网络波动     | 结果不完整     | last-event 重连 + 缓存已完成节点   |
| 秘钥泄露        | 日志不脱敏   | 安全事故       | 日志脱敏 + rotation + 最小权限     |
| 并发 push 冲突  | 多人协作覆盖 | 更改丢失       | revision hash + diff 提示          |
| 代码节点风险    | 注入危险操作 | 环境破坏       | AST + denylist + 人工审核标记      |
| 误操作强制覆盖  | 误用 --force | 历史丢失       | 强制操作需二次确认 + 审计记录      |

## 🛠 测试目录规划
```
automation/tests/
  unit/
  contract/        # 固定响应/Schema 验证
  e2e/
  regression/
  perf/
  fixtures/        # 录制的响应与基准 DSL
```

## 🧪 验收标准示例（提炼）
- `login()`: 401 → 重试 1 次；仍失败抛 `AuthenticationError`；网络超时抛 `NetworkError`。
- `push_dsl()`: 若哈希一致输出 `{"status":"skipped"}`；缺插件阻断并返回 `missing_plugins` 数组。
- `run_workflow()`: SSE 中断自动重连一次；超过全局超时返回 `workflow_timeout`。
- `dry_run()`: 不触发导入；返回结构化校验结果；DSL 结构缺失字段返回 `valid=false` + `errors`。
- `diff()`: 显示新增/删除/修改节点数量与 id 列表。

## 🧩 CLI 命令一览（完整版）
| 命令                                | 功能             | 关键参数               | JSON 输出 |
| ----------------------------------- | ---------------- | ---------------------- | --------- |
| wf gen                              | 规格转 DSL       | --prompt / --model     | 支持      |
| wf dry-run                          | 检查 DSL         | --file / --app-id      | 支持      |
| wf diff                             | 本地 vs 远端差异 | --file / --app-id      | 支持      |
| wf push                             | 导入 DSL         | --force / --skip-check | 支持      |
| wf run                              | 运行工作流       | --inputs-json          | 支持      |
| wf run-node                         | 单节点调试       | --node-id              | 支持      |
| wf secrets list/set/rotate/validate | 密钥管理         | --name / --file        | 支持      |
| wf template list/apply              | 模板库操作       | --name                 | 支持      |
| wf alias set/get                    | 应用别名         | --alias / --app-id     | 支持      |
| wf audit show                       | 查看审计         | --limit                | 支持      |
| wf audit trace                      | Trace 追踪       | --trace-id             | 支持      |

## 📝 进度日志 (Progress Log)
- **[Day 1 Completed]**: 
  - 完成目录重构 (`automation/infra`, `clients`, `domain`, `services`, `cli`, `tests`)。
  - 实现 `pydantic.BaseSettings` 配置管理。
  - 实现 `DifyConsoleClient` 支持 `login`, `get_apps`, `import_app`, `run_workflow`。
  - 实现 `DSLBuilder` 支持基础节点。
- **[Day 8 Completed]**:
  - **Alias Management**: Added `AliasService` with local persistence plus CLI `wf alias` subcommands to list/set/get/delete app aliases (Task 4.2).
  - **CLI Integration**: `diff`, `run`, `run-node`, `pull`, `push` now accept `--alias` and share a resolver helper to look up stored app IDs.
  - **Testing**: Added unit tests covering alias persistence and CLI resolution helper behavior.
  - 实现 `WorkflowService` 串联 MVP 流程 (`create_and_run_mvp`)。
  - 实现 CLI 入口 `wf mvp`。
  - 单元测试全部通过。
- **[Day 9 Completed]**:
  - **Template Library**: Added `TemplateService` with YAML registry + library to power starter workflows (Task 4.2 `wf template list/apply`).
  - **CLI**: Introduced `wf template list` and `wf template apply` commands supporting JSON output, variable overrides (`--set key=value`), and safe overwrite flags.
  - **Testing**: Added dedicated unit tests for template registry parsing, rendering, and overwrite safeguards.
- **[Day 10 Completed]**:
  - **Console Contract Tests**: Recorded JSON fixtures for login, listing apps, importing, and exporting workflows (Task 5.2) and wired new tests under `automation/tests/contract` to guard API schema assumptions.
  - **Regression Guardrails**: Contract suite validates request payloads (mode/app_id inclusion, include_secret flag) and ensures Dify Console client remains compatible with captured fixtures.
  - **CI Ready**: Added fixtures to `automation/tests/fixtures/console` so the suite runs deterministically without network access.
- **[Day 11 Completed]**:
  - **Property Harness**: Added `automation/tests/property/test_dsl_builder_property.py`, a deterministic randomized suite that generates dozens of DSLs per run to assert structure validity and YAML round-tripping (Task 5.3).
  - **Random Builders**: Introduced helper generators for start variables, LLM nodes, and code nodes so the DSL builder is stress-tested under varied configurations without external dependencies.
  - **Testing**: Property suite runs alongside existing DSL unit tests via `pytest automation/tests/property/test_dsl_builder_property.py automation/tests/unit/test_dsl_builder.py`.
- **[Day 12 Completed]**:
  - **Regression Suite**: Added `automation/tests/regression/run_suite.py` with template-aware DSL materialization, fake console/runner stubs, and baseline cases defined in `automation/tests/regression/cases.json` (Task 5.4).
  - **CLI Support**: Introduced `wf regression list|run` subcommands (JSON-friendly) so release engineers can inspect or execute the suite via `automation/cli/main.py`.
  - **Testing**: Created deterministic Pytest coverage in `automation/tests/regression/test_run_suite.py` to validate case listing and execution summaries.
- **[Day 13 Completed]**:
  - **Performance Harness**: Added `automation/tests/perf/run_suite.py` with thread-pooled iterations, DSL auto-materialization, latency simulation, and metric aggregation (avg/p95/throughput) to satisfy Task 5.5 load-test requirements.
  - **CLI Integration**: Wired `wf perf list|run` subcommands in `automation/cli/main.py`, defaulting to `automation/tests/perf/config.json` while supporting JSON-formatted output for dashboards.
  - **Config & Fixtures**: Added `automation/tests/perf/config.json` plus reusable DSL fixtures so perf cases can reference either templates or workflow YAML assets across the repo using automatic root detection.
  - **Testing**: Authored `automation/tests/perf/test_perf_runner.py` to validate case catalog reporting and metrics summaries via isolated temp configs.
- **[Day 14 Completed]**:
  - **Auto-Merge Drafts**: `WorkflowService.pull_app` now snapshots the last-synced DSL into `*.base` files and `push_app` attempts YAML-aware three-way merges, surfacing `.auto-merged.yml` drafts plus conflict hints (Task 6.1 advanced strategy).
  - **Workflow Sync Tests**: Expanded `automation/tests/unit/test_workflow_sync.py` to cover base snapshot refreshes and to assert auto-merge artifacts plus messaging when remote changes diverge.
  - **Developer Guidance**: Documented the enhanced conflict workflow in this plan so future contributors know to expect `.base` + `.auto-merged` files during collaborative pushes.
- **[Day 15 Completed]**:
  - **Code Node Security (Task 6.2)**: Hardened `SecurityService` with AST alias resolution, explicit module/function deny-lists, and regex heuristics so dangerous patterns like `os.system`, `subprocess.Popen`, or `requests.post` are blocked even when they hide behind aliases or template strings.
  - **Violation Messaging**: Every finding now carries remediation guidance (“use workflow-approved integrations instead of direct system/network calls”) to steer builders toward compliant solutions.
  - **Unit Tests**: `automation/tests/unit/test_security_service.py` gained coverage for attribute detection, alias handling, and regex-triggered findings, ensuring the scanner guards both structural and textual attack paths.
- **[Day 16 Completed]**:
  - **Secrets Policy (Task 6.3)**: `SecretService` now consumes `automation/secrets/policy.json`, applying default/max TTLs, per-secret warn windows, and policy-derived required lists so rotations follow least-privilege SLAs automatically.
  - **CLI Visibility**: Added `wf secrets policy` plus enhanced `wf secrets validate` behavior so teams can inspect policy sources, warn thresholds, and scoped secrets directly from the terminal.
  - **Testing & Samples**: Expanded `automation/tests/unit/test_secret_service.py` to cover policy enforcement and published `automation/secrets/policy.example.json` as a template for defining rotation scopes and expiry budgets.
- **[Day 17 Completed]**:
  - **Idempotent Pushes (Task 2.4)**: `WorkflowService.push_app` now hashes local DSLs, fetches the latest remote draft (even under `--force`), and skips imports when nothing changed while updating local hash snapshots.
  - **Import Status Guardrails**: Pushes inspect `/console/api/apps/imports` responses, surfacing `failed/error` statuses immediately so validation issues never silently succeed.
  - **Test Coverage**: `automation/tests/unit/test_workflow_sync.py` now asserts the skip pathway and failed-import handling, ensuring the CLI reports deterministic outcomes for dry pushes.
- **[Day 18 Completed]**:
  - **Request Metrics Baseline (Task 1.4)**: Introduced `RequestMetricsRecorder` under `automation/infra/metrics.py` and instrumented `DifyConsoleClient` to log method, endpoint, status, latency, and trace IDs for every Console API call (including SSE runs).
  - **CLI Visibility**: Added `wf audit requests --window/--tail/--json` so operators can inspect aggregate success rates, latency percentiles, and recent HTTP calls directly from the terminal.
  - **Testing**: Authored `automation/tests/unit/test_request_metrics.py` to cover recording, summarization, and tail retrieval logic, ensuring the metrics store remains reliable.
- **[Day 2 Completed]**:
  - **DSL Builder**: Upgraded to support full Dify DSL specs, validation, and metadata (Task 2.1).
  - **Workflow Generator**: Implemented `WorkflowGenerator` service (Task 2.2).
  - **CLI**: Added `generate` and `dry-run` commands (Task 2.3, 4.1, 4.2).
  - **Workflow Service**: Added `dry_run` logic with structure validation.
  - **Testing**: Added unit tests for DSL Builder and verified CLI commands.
  - **Diff & Dependencies**: Implemented `DiffService` and `check_dependencies` (Task 2.3, 2.4). Added `diff` command to CLI.
  - **Console Client**: Added `export_app` to fetch remote DSL.
- **[Day 3 Completed]**:
  - **Execution Engine**: Implemented `WorkflowRunner` with SSE stream parsing and event handling (Task 3.1).
  - **Single Node Debugging**: Added `run-node` command to CLI and Service (Task 3.2).
  - **Audit & Metrics**: Implemented `AuditService` and `audit` CLI command (show/stats) (Task 3.3, 4.2).
  - **Testing**: Added unit tests for `WorkflowRunner` and `AuditService` (Task 5.1).
- **[Day 4 Completed]**:
  - **E2E Test Suite**: Created `automation/tests/e2e` with `run_all.py` runner and fixtures (`simple_greeting`, `code_math`) (Task 4.3).
- **[Day 5 Completed]**:
  - **Security Scan**: Implemented `SecurityService` with AST-based code scanning (Task 6.2).
  - **Workflow Sync**: Implemented `pull` and `push` commands with conflict detection (Task 6.1).
  - **CLI**: Added `pull` and `push` commands to CLI.
  - **Testing**: Added unit tests for `SecurityService` and `WorkflowSync`.
- **[Day 6 Completed]**:
  - **Secrets Management**: Added `SecretService` with rotation metadata and expiry tracking (Task 6.3).
  - **CLI Enhancements**: Introduced `wf secrets list/set/rotate/validate` commands with JSON output.
  - **Validation**: Implemented expiry warnings and required-secret validation flows.
  - **Testing**: Added unit tests covering secret lifecycle and validation paths.
- **[Day 7 Completed]**:
  - **Trace IDs**: Implemented trace logging for workflow runs and push operations (Task 6.4).
  - **Audit CLI**: Added `wf audit trace` for querying trace timelines with JSON output.
  - **Automation Runner**: All runs now emit trace identifiers and persist metadata in `trace_history`.
  - **Testing**: Added unit tests for `TraceService` and ran full unit suite.

## 🗓 更新后的实施路线图（里程碑）
1. **Day 1**: 阶段 0 MVP + 1.1 目录骨架 (✅ 已完成)。
2. **Day 2**: DSL Builder 完整版 + dry-run + 插件依赖校验。

## 📎 附录（建议后续补充独立文件或章节）
- **附录 A**：DSL Schema & 示例
- **附录 B**：错误码对照表
- **附录 C**：审计日志格式规范
- **附录 D**：测试基准工作流清单
- **附录 E**：安全策略与黑名单/白名单规则

## ✅ 后续执行建议
1. 先将现有代码迁移至建议的分层目录；不改变已有逻辑，保持测试通过。
2. 引入 `pydantic.BaseSettings` 替换当前 `Config`，新增敏感字段脱敏打印。
3. 添加 DSL `meta.version` 与 dry-run 入口；再实现 diff 对比。
4. 并行搭建测试框架（unit + fixtures）与审计日志模块。
5. 逐步补齐 CLI 命令并在 README 中加入使用示例。

（本文件为实施蓝图，执行过程中建议开启“变更登记”，记录每次偏离决策原因以便回溯。）
