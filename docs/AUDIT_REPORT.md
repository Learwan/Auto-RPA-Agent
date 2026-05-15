# Auto Agent Workflow — 文档一致性审计与修订报告

> 审计日期：2026-05-08
> 审计范围：README、架构文档、工程文档、API 面、CLI 对接、产品方向评估、内部接口可调用性
> 审计目标：让文档表述与当前代码事实重新对齐

## 一、总体结论

本轮审计确认，仓库此前的部分文档存在三类主要失真：

1. **产品面描述偏窄**：旧文档主要围绕录制、分析、执行，遗漏了协作、设置、权限、凭据、调度、市场、通知、会话融合等已存在的产品面。
2. **高级模块状态表述失真**：旧审计曾把多项 AI / 高级模块归为“全部死代码”，但当前代码里智能恢复、自适应工作流、知识图谱已进入实际调用链，自进化与跨平台降级则属于部分接入。
3. **内部调用文档缺位**：CLI、API、Service、引擎的调用关系没有被系统记录，导致内部团队很难快速判断应该从哪里接入、哪些接口稳定、哪些能力还在扩展期。

## 二、本次修订动作

本次已完成以下修订：

| 交付物 | 目的 |
|--------|------|
| `README.md` | 更新产品能力、CLI 用法、API 版图和当前能力边界 |
| `docs/ARCHITECTURE.md` | 更新高层架构与主链说明 |
| `docs/ENGINEERING_DOCUMENT.md` | 更新工程事实、模块职责和高级模块状态 |
| `docs/API_REFERENCE.md` | 新增当前 API 目录与分组说明 |
| `docs/PRODUCT_DIRECTION_REEVALUATION.md` | 新增产品发展方向复评 |
| `docs/INTERNAL_INTERFACE_REVIEW.md` | 新增内部接口调用与规范性评估 |

同时，本次还修复了一个影响内部可调用性的实际问题：

- CLI 在创建会话与执行流程时，原先使用查询参数调用要求 JSON body 的接口；现已改为按后端契约发送请求体。

## 三、当前统一后的事实口径

### 1. 产品能力边界

当前产品应被描述为：

- 一套跨平台桌面与 Web 自动化系统
- 核心主链为录制、分析、编排、执行
- 已具备 AI / Vision 增强能力
- 已具备协作、设置、权限、凭据、调度、模板市场、通知、会话融合等外围产品面

### 2. AI / 高级模块状态

当前统一口径如下：

- `IntelligentErrorRecovery`：已接入执行引擎
- `AdaptiveWorkflowEngine`：已接入执行引擎
- `WorkflowKnowledgeGraph`：已接入分析增强与执行后沉淀
- `SelfEvolutionSystem`：已接入反馈记录，未默认自动调参
- `CrossPlatformAdapter`：已初始化，尚未成为主适配器决策核心

### 3. 内部调用事实

主调用面当前为：

- CLI -> 主链 API（录制、分析、流程、执行、LLM）
- API -> RecordingService / AnalysisService / ExecutionService / LLMService 等
- ExecutionService -> ExecutionEngine -> StepExecutor -> ElementLocator / 平台适配器

## 四、仍然存在的真实问题

本轮修订并未把所有工程问题消除，当前仍有以下风险需要后续处理：

1. API 写接口风格不统一，请求体与查询参数混用。
2. `response_model` 使用不一致，影响内部 SDK 和契约测试。
3. 外围能力虽然已实现，但 CLI 与统一产品入口覆盖不足。
4. 文档已被拉齐，但仍缺少自动化文档回归机制，后续仍有再次漂移风险。

## 五、建议的维护机制

建议以后每次产品迭代至少同步完成以下动作：

1. 更新 `README.md` 中的能力边界和 CLI/API 入口。
2. 对照 `src/api/app.py` 检查是否有新增路由组未写入 `API_REFERENCE.md`。
3. 对照 `ExecutionService`、`AnalysisService` 的构造与调用链，更新高级模块状态描述。
4. 对 CLI 与对应 API 的请求形态做一轮契约检查。
5. 在重大方向调整后，刷新 `PRODUCT_DIRECTION_REEVALUATION.md` 的优先级判断。

## 六、结论

这次审计后，文档与当前代码事实已重新对齐，且“产品说明、API 目录、方向评估、内部接口评估”四个层次已经形成完整闭环。后续工作的重点应从“继续补叙述”转向“用测试和约束防止文档再次过期”。