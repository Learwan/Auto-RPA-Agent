# 项目架构

> 文档状态：当前有效
> 更新时间：2026-05-08
> 配套文档：`ENGINEERING_DOCUMENT.md`、`API_REFERENCE.md`、`INTERNAL_INTERFACE_REVIEW.md`

## 总览

Auto Agent Workflow 当前是一套以 FastAPI 为核心控制面、以 Typer CLI 和静态 Web 页面为主要入口、以录制分析和自动执行为主链路的自动化系统。

系统不再只是“录制 -> 分析 -> 执行”三段式能力，而是已经扩展出协作、视觉分析、调度、凭据管理、模板市场、通知、会话融合等外围能力。当前最稳定的主路径仍然是：

1. 创建并启动录制会话
2. 对录制结果进行分析并生成流程
3. 在流程编辑器或 API 中修改自动化流程
4. 执行、干跑、暂停、恢复、停止或单步控制流程
5. 通过执行反馈、知识沉淀和可选 AI 能力持续优化

## 系统分层

### 入口层

- CLI：`src/cli.py`
- Web UI：`static/index.html`、`static/flow-editor.html`、`static/collab.html`、`static/marketplace.html`
- REST / WebSocket：`src/api/app.py` 统一装配

### API 编排层

当前 API 由以下模块组成：

- `desktop`：桌面状态、截图、快照
- `sessions`：录制会话、操作流、分析入口、实时事件
- `automations`：自动化流程 CRUD、分支编辑、导出、执行
- `executions`：执行历史与实时控制
- `llm`：LLM 对话、解释、分析、增强
- `agent`：代理会话、聊天、流程分析、执行调试
- `settings`：用户设置与快捷键
- `collab`：协作房间与实时分析
- `vision`：截图分析、动作 grounding、界面元素抽取
- `auth`：用户、角色、权限、审计
- `vault`：凭据管理
- `scheduler`：调度任务管理
- `marketplace`：模板发布、搜索、导入导出
- `fusion`：多录制会话融合
- `notifications`：站内、Webhook、邮件通知

### 服务与核心模块层

- `RecordingService`：录制会话生命周期
- `AnalysisService`：预处理、分段、模式检测、流程生成、评分、可选 AI 增强
- `ExecutionService`：执行创建、执行控制、反馈队列、执行后知识沉淀
- `CollaborationService`：协作房间和广播
- `SettingsStore`：设置与快捷键持久化入口

### 引擎层

- `ExecutionEngine`：执行状态机、依赖队列、失败恢复、分支推进
- `StepExecutor`：单步动作执行
- `ElementLocator`：多策略定位与自愈定位
- `StepVerifier` / `ScreenshotComparator`：执行前验证和视觉比对
- `ErrorHandler`：重试、跳过、中止等通用错误策略

### 平台与 AI 层

- 平台适配：macOS、Windows、Linux、Web
- LLM：云端兼容接口 + 本地引擎
- Vision：截图分析、元素 grounding、界面比较
- Agent：工作流分析、执行调试、过程映射

### 数据与治理层

- SQLAlchemy + Alembic：核心数据持久化
- 审计、限流、监控：运行治理能力
- Vault、Scheduler、Marketplace、Notifications：外围运营能力

## 核心链路

### 录制链路

`CLI/Web -> /api/sessions -> RecordingService -> SessionManager -> recorder 集合 -> EventBus / Repository`

### 分析链路

`/api/sessions/{id}/analyze -> AnalysisService -> Preprocessor -> SemanticSegmenter -> PatternDetector -> ScriptGenerator -> ConfidenceScorer`

当 LLM 可用时，`AnalysisService` 还会补充：

- 流程命名
- 流程解释
- 基于知识图谱的模式沉淀

### 执行链路

`/api/automations/{id}/execute -> ExecutionService -> ExecutionEngine -> StepExecutor -> ElementLocator / PlatformAdapter`

执行链路支持：

- 同步执行
- 干跑
- 暂停 / 恢复 / 停止
- 跳过当前步骤 / step-over
- WebSocket 实时反馈

### 运行后反馈链路

执行完成后，`ExecutionService` 会更新：

- 执行记录
- 流程执行次数与成功次数
- 工作流知识图谱中的操作模式
- 自进化系统的反馈样本

## 当前架构边界

### 已进入主路径的能力

- 录制、分析、流程 CRUD、执行控制
- LLM 在分析链路中的可选命名和分析增强
- 智能恢复在执行引擎中的失败分析和恢复动作选择
- 自适应工作流在执行引擎中的循环检查和下一步解析
- 知识图谱在分析与执行反馈中的模式沉淀

### 已初始化但尚未深度产品化的能力

- `CrossPlatformAdapter` 已在 `ExecutionService` 初始化，但尚未成为主适配器选择链的核心决策器
- `SelfEvolutionSystem` 当前以反馈采集为主，自动调参和闭环优化还未成为默认运行路径
- `Agent`、`Fusion`、`Marketplace`、`Scheduler` 更像产品扩展面，尚未与核心录制执行体验完全合流

## 当前设计判断

1. 这是一个“核心主链稳定、外围产品面扩张较快”的架构。
2. 代码中的 AI 与高级模块不再适合被描述为“全部死代码”，但它们的接入深度并不一致。
3. 下一阶段最关键的工作不是继续扩表面能力，而是统一接口规范、补调用契约、提高可靠性回归和文档完备度。