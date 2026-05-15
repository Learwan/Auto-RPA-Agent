# Auto Agent Workflow — 内部接口调用与规范性评估

> 评估日期：2026-05-08
> 评估范围：CLI、API、Service、核心执行/分析链、外围治理能力

## 一、结论摘要

当前内部能力调用面已经相当丰富，但“功能已实现”与“团队易于稳定接入”之间仍有明显距离。主链能力已经具备较好的可调用性，外围能力则存在接口风格不统一、调用入口分散、文档此前不足的问题。

本次修订后，文档覆盖已经补齐，同时还修复了一个真实调用问题：CLI 创建会话与执行流程时的 JSON body 错配。

## 二、调用面梳理

### 1. CLI -> API

当前 CLI 覆盖如下：

| CLI 命令组 | 下游 API | 覆盖度 |
|------------|----------|--------|
| `record` | `/api/sessions` | 高 |
| `analyze` | `/api/sessions/{id}/analyze` | 高 |
| `flow` | `/api/automations` | 中 |
| `run` | `/api/automations/{id}/execute` / `dry-run` | 高 |
| `executions` | `/api/executions` | 中 |
| `llm` | `/api/llm/*` | 中 |
| 其他外围能力 | 无统一 CLI | 低 |

结论：CLI 适合主链联调，不适合覆盖全产品能力。

### 2. API -> Service / Engine

| API 模块 | 主要调用对象 | 评估 |
|----------|--------------|------|
| `sessions.py` | `RecordingService`、`AnalysisService` | 主链清晰 |
| `automations.py` | `Repository`、`ExecutionService`、`AnalysisService` | 主链清晰 |
| `executions.py` | `ExecutionService` | 主链清晰 |
| `llm.py` | `LLMService` | 清晰 |
| `vision.py` | `MultimodalLLMService`、`GUIGroundingEngine` | 清晰 |
| `agent.py` | `AgentEngine` | 清晰但更偏扩展能力 |
| `settings.py` | `SettingsStore` | 清晰 |
| `collab.py` | `CollaborationService`、`LocalLLMEngine` | 中等 |
| `auth/vault/scheduler/marketplace/fusion/notifications` | 各自服务或内存管理对象 | 清晰但风格分散 |

### 3. Service -> 核心链路

#### 分析链路

`AnalysisService` 的调用形态清晰，主问题不在链路，而在增强能力是否被充分消费。

#### 执行链路

`ExecutionService -> ExecutionEngine -> StepExecutor -> ElementLocator` 是当前最稳定的核心调用面，也是最适合作为内部集成标准路径的部分。

## 三、规范性评估

### 优点

1. 主链命名清晰，模块边界基本合理。
2. 关键路径大量使用 Pydantic 模型和枚举，约束较明确。
3. 执行控制 API 设计完整，覆盖暂停、恢复、停止、跳步和 WebSocket 反馈。
4. Sessions、Automations、Executions 三组接口的职责分层相对清晰。

### 问题

1. **请求风格不统一**

   - 有的写接口要求 JSON body（如创建会话）。
   - 有的写接口主要通过查询参数传递输入（如部分调度、权限、模板市场接口）。
   - 对内部调用团队而言，这会增加出错概率，并提升 SDK 封装成本。

2. **响应模型不统一**

   - 关键接口有 `response_model`。
   - 很多外围接口直接返回裸字典，导致 schema 稳定性与自动生成客户端能力较弱。

3. **入口覆盖不统一**

   - 主链可从 CLI、API、Web 三个入口访问。
   - 外围能力多数只能通过 API 直接使用，内部接入成本更高。

4. **能力成熟度不一致**

   - 有些高级模块已经接入执行或分析主链。
   - 有些模块虽然存在实现，但仍属于部分接入或扩展面，不能按“同等成熟能力”对外承诺。

## 四、易用性评估

### 主链

主链易用性整体较好：

- 会话管理直观
- 流程 CRUD 明确
- 执行控制完整
- WebSocket 反馈利于调试

### 外围能力

外围能力易用性一般：

- 功能点多，但入口分散
- 缺少统一客户端或内部 SDK
- 文档此前不完整，团队需要读源码才能确认正确调用方式

## 五、文档完整性评估

### 修订前

- README 未覆盖完整产品面
- 架构文档与高级模块状态存在偏差
- 缺少完整 API 目录和内部调用图谱

### 修订后

当前已补齐：

- 产品总览
- 架构与工程事实
- API 目录
- 产品方向复评
- 内部接口评估

结论：文档完整性从“仅适合理解主链”提升到“可以支持内部接入与规划判断”。

## 六、已确认并已修复的问题

### CLI 请求体错配

发现：

- `record start` 在创建会话时原先把 `name`、`tags` 放在查询参数里，而后端该接口要求 JSON body。
- `run` 在执行和干跑时原先把 `variables` 当作查询参数发送，而后端将其视为请求体输入。

处理：

- 已在 `src/cli.py` 修复为按 JSON body 发送。

影响：

- 降低了 CLI 触发 `422` 或契约错配的风险。

## 七、仍需改进的问题

1. 为外围写接口补齐统一的请求模型。
2. 为外围接口补齐 `response_model`。
3. 增加主链 CLI/API 契约测试。
4. 提供内部 Python client 或最小 SDK，减少团队重复封装。
5. 把“已初始化”“已接入主链”“已产品化”三种能力状态在文档中持续区分。

## 八、建议优先级

### P0

1. 统一写接口的请求模式
2. 为核心接口加契约测试
3. 为主链和外围能力分别定义稳定等级

### P1

1. 增加内部 SDK
2. 为设置、权限、调度、通知、市场等外围能力补调用示例
3. 将外围能力纳入统一导航和权限模型

### P2

1. 对 Agent、Fusion、Evolution 等扩展能力建立成熟度分级
2. 建立自动文档同步检查，防止文档再次漂移

## 九、最终判断

内部团队当前已经可以高效接入主链能力，但如果要把整个产品面当作统一平台使用，还需要继续推进接口规范化、SDK 化和能力成熟度分层。当前最应该做的是“统一契约”，而不是继续增加更多入口或更多模块。