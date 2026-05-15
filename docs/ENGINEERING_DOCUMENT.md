# Auto Agent Workflow — 工程架构文档

> 文档版本：v1.1
> 更新时间：2026-05-08
> 适用对象：开发、测试、产品、内部集成团队
> 文档定位：当前工程事实文档，替代历史自动生成快照

## 1. 当前工程结论

项目当前已经形成一条清晰的生产主链：

1. 会话录制
2. 操作分析与流程生成
3. 自动化流程编辑与导出
4. 执行编排与运行控制
5. AI / Vision 增强
6. 协作、设置、权限、凭据、调度、市场、通知等外围治理能力

当前代码库不是“AI 未接入的基础录制器”，也不是“所有高级模块都已经深度产品化的 Agent 平台”，而是介于两者之间：核心录制执行链已稳定成型，AI 和外围模块已接入但接入深度不完全一致。

## 2. 应用装配

应用由 `src/api/app.py` 创建，核心装配点包括：

- 生命周期：启动时建目录、初始化数据库；关闭时断开数据库
- 中间件：CORS、限流、监控
- 路由装配：统一在应用工厂中注册
- 静态文件：挂载 `static/` 作为 Web 控制台入口
- 健康检查：`/api/health` 与 `/api/health/details`

### 当前路由面

| 模块 | 前缀 | 作用 |
|------|------|------|
| Desktop | `/api/desktop` | 桌面状态、截图、快照 |
| Sessions | `/api/sessions` | 录制会话、操作流、分析入口、会话 WebSocket |
| Automations | `/api/automations` | 流程 CRUD、步骤编辑、执行、评分、导出 |
| Executions | `/api/executions` | 执行历史、暂停/恢复/停止/跳步、反馈 WS |
| LLM | `/api/llm` | 对话、解释、分析、增强 |
| Agent | `/api/agent` | 代理会话、聊天、分析、调试 |
| Settings | `/api/settings` | 用户设置与快捷键 |
| Collaboration | `/api/collab` | 协作房间、本地模型状态、实时分析 |
| Vision | `/api/vision` | 截图分析、grounding、UI 提取、动作验证 |
| Auth | `/api/auth` | 用户、角色、权限、审计 |
| Vault | `/api/vault` | 凭据存储与读取 |
| Scheduler | `/api/scheduler` | 定时任务管理 |
| Marketplace | `/api/marketplace` | 模板发布、搜索、导入导出 |
| Fusion | `/api/groups` 等 | 多录制会话融合 |
| Notifications | `/api/notifications` | 历史、Webhook、邮件通知 |

## 3. 核心模块与职责

### 3.1 录制模块

核心文件：

- `src/recorder/service.py`
- `src/recorder/session_manager.py`
- `src/recorder/*_recorder.py`
- `src/recorder/event_bus.py`

职责：

- 创建、启动、暂停、恢复、停止录制会话
- 采集鼠标、键盘、窗口、剪贴板、文件系统、Web 操作
- 通过事件总线把操作事件广播给实时消费方

### 3.2 分析模块

核心文件：

- `src/analyzer/service.py`
- `src/analyzer/preprocessor.py`
- `src/analyzer/semantic_segmenter.py`
- `src/analyzer/pattern_detector.py`
- `src/analyzer/script_generator.py`
- `src/analyzer/confidence_scorer.py`

职责：

- 把原始操作整理为可分析序列
- 做语义分段、模式检测和直接流生成
- 对生成流程进行置信度评分
- 在 LLM 可用时追加命名、分析和知识图谱沉淀

### 3.3 执行模块

核心文件：

- `src/executor/execution_service.py`
- `src/executor/engine.py`
- `src/executor/step_executor.py`
- `src/executor/element_locator.py`
- `src/executor/error_handler.py`

职责：

- 创建执行实例并持有执行反馈队列
- 调度步骤执行、依赖推进、失败恢复
- 提供暂停、恢复、停止、跳步、step-over 能力
- 执行完成后落库并回写流程统计

### 3.4 AI / Vision 模块

核心文件：

- `src/llm/service.py`
- `src/llm/local_engine.py`
- `src/llm/multimodal_service.py`
- `src/llm/gui_grounding.py`
- `src/api/routes/agent.py`
- `src/api/routes/vision.py`

职责：

- 提供云端 LLM 对话、解释、分析、脚本增强
- 提供本地视觉推理、grounding、截图比较
- 通过代理接口提供工作流分析与执行调试辅助

### 3.5 治理与扩展模块

核心文件：

- `src/api/routes/settings.py`
- `src/api/routes/auth.py`
- `src/api/routes/vault.py`
- `src/api/routes/scheduler.py`
- `src/api/routes/marketplace.py`
- `src/api/routes/fusion.py`
- `src/api/routes/notifications.py`

职责：

- 设置和快捷键管理
- 用户、角色、权限、审计
- 凭据管理
- 调度、模板分发、通知、会话融合

## 4. 主调用链

### 4.1 CLI 到 API

当前 CLI 主要覆盖：

- `record *` -> `/api/sessions`
- `analyze` -> `/api/sessions/{id}/analyze`
- `flow *` -> `/api/automations`
- `run` -> `/api/automations/{id}/execute` / `dry-run`
- `executions *` -> `/api/executions`
- `llm *` -> `/api/llm/*`

本次修订已同步修复 CLI 中创建会话和执行流程时的 JSON body 传参错配问题。

### 4.2 API 到 Service

主路径映射如下：

| API 模块 | 主要下游 |
|----------|----------|
| `sessions.py` | `RecordingService`、`AnalysisService` |
| `automations.py` | `Repository`、`ExecutionService`、`AnalysisService` |
| `executions.py` | `ExecutionService` |
| `llm.py` | `LLMService` |
| `vision.py` | `MultimodalLLMService`、`GUIGroundingEngine` |
| `agent.py` | `AgentEngine` |
| `settings.py` | `SettingsStore` |
| `collab.py` | `CollaborationService`、`LocalLLMEngine` |

### 4.3 Service 到核心引擎

#### 分析服务

`AnalysisService.analyze_session()` 会依次调用：

1. `_load_operations`
2. `OperationPreprocessor.preprocess`
3. `SemanticSegmenter.segment`
4. `PatternDetector.detect_patterns`
5. `ScriptGenerator.generate` / `generate_direct`
6. `ConfidenceScorer.score_flow`
7. `_enhance_with_ai`
8. `_save_flow`

#### 执行服务

`ExecutionService.start_execution()` 会依次调用：

1. 读取流程
2. 解析适配器
3. 创建 `ElementLocator`、`StepExecutor`、`ErrorHandler`
4. 创建 `ExecutionEngine`
5. 启动执行或干跑
6. 保存执行记录
7. 回收反馈、更新流程统计、写入知识与反馈样本

## 5. 高级模块实际状态

### 已明确接入主链或后处理链的模块

| 模块 | 当前状态 | 说明 |
|------|----------|------|
| `IntelligentErrorRecovery` | 已接入 | 由 `ExecutionEngine` 在失败恢复路径中调用 |
| `AdaptiveWorkflowEngine` | 已接入 | 由 `ExecutionEngine` 做循环检查和下一步解析 |
| `WorkflowKnowledgeGraph` | 已接入 | 分析增强和执行后模式沉淀均可使用 |
| `SelfEvolutionSystem` | 部分接入 | 当前用于记录执行反馈，未默认自动调优 |

### 已实例化但未成为核心决策器的模块

| 模块 | 当前状态 | 说明 |
|------|----------|------|
| `CrossPlatformAdapter` | 部分接入 | `ExecutionService` 初始化该对象，但主适配器选择仍走 `create_adapter_for_flow()` |

## 6. 当前工程风险

1. 接口风格仍不统一：部分写接口走请求体，部分走查询参数。
2. 路由层 `response_model` 覆盖度不一致，不利于 SDK 与契约测试。
3. CLI 只覆盖主链，外围治理能力仍需内部团队直接调 API。
4. 扩展模块较多，但统一产品化入口和权限边界仍需加强。

## 7. 推荐工程优先级

1. 统一 API 写接口请求模式与响应模型。
2. 为 CLI 和主要 API 补契约测试。
3. 把调度、市场、通知、融合等外围能力纳入统一产品导航与文档地图。
4. 让自进化、跨平台降级策略从“存在”走向“默认可观测、可验证”。