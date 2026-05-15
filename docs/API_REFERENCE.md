# Auto Agent Workflow — API 参考

> 版本：对应代码版本 `0.1.0`
> 更新时间：2026-05-08
> 说明：本文件给出当前 REST / WebSocket 接口目录，OpenAPI 细节以运行时 `/docs` 为准。

## 1. 通用约定

- 基础前缀：`/api`
- 默认返回：JSON
- 实时接口：WebSocket
- 典型错误码：`400` 参数错误，`404` 资源不存在，`409` 冲突，`500` 服务异常
- 说明重点：本文件强调“接口面和职责”，不重复展开所有 schema 字段

## 2. 健康检查

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 基础健康检查 |
| GET | `/api/health/details` | 详细状态，包括 LLM、Vision、记录器和高级模块状态 |

## 3. Desktop

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/desktop/windows` | 获取窗口列表 |
| GET | `/api/desktop/processes` | 获取进程列表 |
| GET | `/api/desktop/active-element` | 获取当前聚焦元素 |
| GET | `/api/desktop/state` | 获取完整桌面状态 |
| POST | `/api/desktop/snapshot` | 创建快照 |
| GET | `/api/desktop/snapshot/{snapshot_id}` | 查询单个快照 |
| GET | `/api/desktop/snapshots` | 列出快照 |
| POST | `/api/desktop/periodic-capture/start` | 开启周期性快照 |
| POST | `/api/desktop/periodic-capture/stop` | 停止周期性快照 |
| GET | `/api/desktop/screenshot` | 获取截图 |
| POST | `/api/desktop/event` | 写入桌面事件 |

## 4. Sessions

### REST

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/sessions` | 创建录制会话 |
| GET | `/api/sessions` | 列出会话 |
| GET | `/api/sessions/{session_id}` | 获取会话详情 |
| POST | `/api/sessions/{session_id}/start` | 启动录制 |
| POST | `/api/sessions/{session_id}/pause` | 暂停录制 |
| POST | `/api/sessions/{session_id}/resume` | 恢复录制 |
| POST | `/api/sessions/{session_id}/stop` | 停止录制 |
| GET | `/api/sessions/{session_id}/operations` | 获取操作列表 |
| POST | `/api/sessions/batch-operations` | 批量写入操作 |
| DELETE | `/api/sessions/{session_id}` | 删除会话 |
| POST | `/api/sessions/{session_id}/analyze` | 分析会话并生成流程候选 |
| POST | `/api/sessions/{session_id}/copilot` | 生成单人 AI 工作流提炼摘要、风险点和优化建议 |

### WebSocket

| 方法 | 路径 | 说明 |
|------|------|------|
| WS | `/api/sessions/{session_id}/events` | 实时操作事件流 |
| WS | `/api/sessions/{session_id}/analysis` | 实时分析状态与建议 |

### 主要请求模型

- `CreateSessionRequest`：`name`、`description`、`tags`
- `StartRecordingRequest`：`mode`、`url`、`browser`、`headless`
- `BatchOperationsRequest`：`session_id`、`operations`

## 5. Automations

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/automations` | 列出流程 |
| POST | `/api/automations` | 创建流程 |
| GET | `/api/automations/{flow_id}` | 获取流程详情 |
| PUT | `/api/automations/{flow_id}` | 全量更新流程 |
| PATCH | `/api/automations/{flow_id}` | 局部更新流程 |
| POST | `/api/automations/{flow_id}/steps` | 新增步骤 |
| PATCH | `/api/automations/{flow_id}/steps/{step_id}` | 更新步骤 |
| DELETE | `/api/automations/{flow_id}/steps/{step_id}` | 删除步骤 |
| POST | `/api/automations/{flow_id}/steps/{step_id}/branches` | 新增或更新分支 |
| DELETE | `/api/automations/{flow_id}/steps/{step_id}/branches` | 删除分支 |
| DELETE | `/api/automations/{flow_id}` | 删除流程 |
| POST | `/api/automations/{flow_id}/execute` | 执行流程 |
| POST | `/api/automations/{flow_id}/dry-run` | 干跑流程 |
| POST | `/api/automations/{flow_id}/export` | 导出流程 JSON 或 Python |
| GET | `/api/automations/{flow_id}/score` | 获取流程评分 |
| WS | `/api/automations/ws/{flow_id}/execute-live` | 实时执行反馈 |

### 主要请求模型

- `AutomationFlow`
- `AutomationFlowPatchRequest`
- `AutomationStep`
- `AutomationStepPatchRequest`
- `StepBranchUpsertRequest`
- `StepBranchDeleteRequest`

## 6. Executions

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/executions` | 列出执行记录 |
| GET | `/api/executions/{execution_id}` | 获取执行详情 |
| POST | `/api/executions/{execution_id}/pause` | 暂停执行 |
| POST | `/api/executions/{execution_id}/resume` | 恢复执行 |
| POST | `/api/executions/{execution_id}/stop` | 停止执行 |
| POST | `/api/executions/{execution_id}/skip-step` | 跳过当前步骤 |
| POST | `/api/executions/{execution_id}/step-over` | 单步越过 |
| WS | `/api/executions/{execution_id}/feedback` | 订阅执行反馈 |

## 7. LLM

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/llm/status` | 查看 LLM 配置状态 |
| POST | `/api/llm/chat` | 通用对话 |
| POST | `/api/llm/analyze-flow` | 分析流程 |
| POST | `/api/llm/suggest-name` | 生成流程名 |
| POST | `/api/llm/explain-operations` | 解释录制操作 |
| POST | `/api/llm/enhance-script` | 生成增强脚本 |
| POST | `/api/llm/chat-vision` | 多图视觉对话 |

### 主要请求模型

- `ChatRequest`
- `AnalyzeFlowRequest`
- `SuggestNameRequest`
- `ExplainOperationsRequest`
- `EnhanceScriptRequest`
- `ChatVisionRequest`

## 8. Agent

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/agent/skills` | 获取代理技能列表 |
| GET | `/api/agent/tools` | 获取代理工具列表 |
| POST | `/api/agent/sessions` | 创建代理会话 |
| GET | `/api/agent/sessions` | 列出代理会话 |
| GET | `/api/agent/sessions/{session_id}` | 获取代理会话详情 |
| POST | `/api/agent/chat` | 代理聊天 |
| POST | `/api/agent/analyze-workflow` | 代理分析流程 |
| POST | `/api/agent/debug-execution` | 代理调试执行 |
| POST | `/api/agent/map-process` | 代理映射流程 |
| WS | `/api/agent/sessions/{session_id}/stream` | 代理会话流式响应 |

## 9. Settings

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/settings` | 获取用户设置 |
| PUT | `/api/settings` | 更新用户设置 |
| GET | `/api/settings/hotkeys` | 获取快捷键配置 |
| PUT | `/api/settings/hotkeys/{action}` | 更新单个快捷键 |
| GET | `/api/settings/hotkeys/conflicts` | 检查快捷键冲突 |
| POST | `/api/settings/reset` | 重置设置 |
| GET | `/api/settings/defaults` | 获取默认设置 |

## 10. Collaboration

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/collab/local-llm/status` | 本地模型状态 |
| GET | `/api/collab/local-llm/test` | 测试本地模型 |
| GET | `/api/collab/rooms` | 列出协作房间 |
| GET | `/api/collab/rooms/{room_id}` | 获取房间详情 |
| POST | `/api/collab/analyzer/{session_id}/start` | 启动录制分析器 |
| POST | `/api/collab/analyzer/{session_id}/stop` | 停止录制分析器 |
| GET | `/api/collab/analyzer/{session_id}/suggestions` | 获取实时建议 |
| WS | `/api/collab/ws/{room_id}` | 协作房间 WS |
| WS | `/api/collab/analyzer-ws/{session_id}` | 实时分析 WS |

## 11. Vision

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/vision/status` | Vision 配置状态 |
| GET | `/api/vision/metrics` | Vision 指标汇总 |
| POST | `/api/vision/analyze-screenshot` | 分析截图 |
| POST | `/api/vision/ground-action` | grounding 单个动作 |
| POST | `/api/vision/ground-batch` | 批量 grounding |
| POST | `/api/vision/compare-screenshots` | 比较两张截图 |
| POST | `/api/vision/extract-ui-elements` | 抽取 UI 元素 |
| POST | `/api/vision/generate-workflow` | 从截图和操作生成流程建议 |
| POST | `/api/vision/verify-action` | 验证动作是否生效 |

## 12. Auth

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/users` | 创建用户 |
| GET | `/api/auth/users` | 列出用户 |
| GET | `/api/auth/users/{user_id}` | 获取用户详情 |
| PUT | `/api/auth/users/{user_id}/role` | 更新角色 |
| POST | `/api/auth/users/{user_id}/deactivate` | 停用用户 |
| GET | `/api/auth/permissions/{user_id}/{permission}` | 检查权限 |
| GET | `/api/auth/audit` | 查询审计日志 |

## 13. Vault

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/vault/credentials` | 存储凭据 |
| GET | `/api/vault/credentials` | 列出凭据 |
| GET | `/api/vault/credentials/{credential_id}` | 按 ID 读取凭据 |
| GET | `/api/vault/credentials/by-name/{name}` | 按名称读取凭据 |
| DELETE | `/api/vault/credentials/{credential_id}` | 删除凭据 |

## 14. Scheduler

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/scheduler/schedules` | 创建调度 |
| GET | `/api/scheduler/schedules` | 列出调度 |
| GET | `/api/scheduler/schedules/{schedule_id}` | 获取调度详情 |
| PUT | `/api/scheduler/schedules/{schedule_id}` | 更新调度 |
| POST | `/api/scheduler/schedules/{schedule_id}/pause` | 暂停调度 |
| POST | `/api/scheduler/schedules/{schedule_id}/resume` | 恢复调度 |
| DELETE | `/api/scheduler/schedules/{schedule_id}` | 删除调度 |

## 15. Marketplace

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/marketplace/templates` | 发布模板 |
| GET | `/api/marketplace/templates` | 搜索模板 |
| GET | `/api/marketplace/templates/{template_id}` | 获取模板详情 |
| POST | `/api/marketplace/templates/{template_id}/download` | 下载模板 |
| POST | `/api/marketplace/templates/{template_id}/rate` | 为模板评分 |
| POST | `/api/marketplace/templates/import` | 导入模板 |
| GET | `/api/marketplace/templates/{template_id}/export` | 导出模板 |
| GET | `/api/marketplace/categories` | 获取模板分类 |

## 16. Fusion

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/groups` | 创建录制组 |
| GET | `/api/groups` | 列出录制组 |
| GET | `/api/groups/{group_id}` | 获取组详情 |
| POST | `/api/groups/{group_id}/sessions` | 向组中加入会话 |
| DELETE | `/api/groups/{group_id}/sessions/{session_id}` | 从组中移除会话 |
| POST | `/api/fuse` | 执行会话融合 |
| GET | `/api/results` | 列出融合结果 |
| GET | `/api/results/{result_id}` | 获取融合结果详情 |

## 17. Notifications

注意：路由文件内部前缀为 `/notifications`，最终路径如下。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/notifications/history` | 获取通知历史 |
| POST | `/api/notifications/send` | 发送通知 |
| POST | `/api/notifications/webhooks` | 配置 Webhook |
| POST | `/api/notifications/email` | 配置邮件 |
| DELETE | `/api/notifications/history` | 清空通知历史 |

## 18. 对内部接入团队的建议

1. 优先接入 `sessions`、`automations`、`executions` 三组接口完成主链联调。
2. 对写接口统一采用 JSON body，避免把请求体字段误放到查询参数中。
3. 接入前同时参考 `/docs` 的 OpenAPI schema 和 `INTERNAL_INTERFACE_REVIEW.md` 的调用建议。
4. 对凭据、权限、通知类接口增加内部封装，不建议各团队直接散落调用。
