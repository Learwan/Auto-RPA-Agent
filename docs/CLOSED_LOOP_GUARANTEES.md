# 闭环保证（录制 → 流程可视化 → 自动化执行）

> 本文档说明 Auto Agent Workflow 当前的闭环执行契约：
>
> * 录制端要保证每个交互事件都带可重放的稳定锚；
> * 分析端要保证生成的流程通过闭环评估；
> * 执行端要拒绝脆弱节点（典型是硬坐标）作为主路径。

## 一、为什么这件事一度无法工作

仓库历史 `.gitignore` 里有一行 `models/`，原本是想忽略大模型权重目录，
但同一条规则也匹配到了 `src/models/`，于是整个 Pydantic 数据模型层从未
被提交。`from src.models.automation import AutomationFlow` 之类的导入
在每个真实环境里都会直接抛 `ModuleNotFoundError`，闭环的第一步就走不
通。本次修订做了三件事：

1. 把 `.gitignore` 的规则锚定到仓库根（`/models/`），重建 `src/models/`
   并把它正式提交进 git。
2. 在数据模型里把 “定位器是否稳定 / 是否需要确认” 的判定收敛到一处，让
   分析端、执行端、可视化端共用同一套语义。
3. 在执行端加上 *拒绝* 脆弱节点的运行时守卫，让 “生成 ≠ 执行” 之间不再
   被悄悄降级成裸坐标点击。

## 二、闭环 5 段的契约

| 阶段 | 入口 | 不变量 |
| --- | --- | --- |
| 录制 | `RecordingService` / `MouseRecorder` / `KeyboardRecorder` | 每个 `OperationEvent.context.focused_element` 至少要带 `identifier / selector / xpath / functional_label / role+title` 中的一项；纯坐标事件被视为脆弱事件。 |
| 预处理 | `OperationPreprocessor` | 去抖、合并、回填 `active_window` 与 `process_name`，确保下游有完整上下文。 |
| 流程生成 | `ScriptGenerator` | 每个 `AutomationStep.target` 必须填充元数据 `locator_requires_confirmation` 与 `locator_stability`；缺锚的步骤进入 ASK\_USER 分支而非 RETRY。 |
| 闭环评估 | `FlowClosureAssessor` | 流程要被认证为 ready，必须 `interactive_count > 0`、`certified_ratio ≥ 1.0`、`checkpoint_ratio ≥ 1.0`、`context_ratio ≥ 1.0`、整体分 ≥ 0.9，且不允许出现 `position_only_target` 等关键问题。 |
| 执行 | `ExecutionService → ExecutionEngine → StepExecutor → ElementLocator` | 未通过闭环评估的流程在 `start_execution` 之前直接抛 `FlowClosureError`；通过的流程在 `StepExecutor` 仍会再次拒绝 *任何* 纯坐标 / 未确认步骤，除非显式开启逃生开关。 |

## 三、脆弱节点的运行时守卫

### 1. 元素定位器 (`src/executor/element_locator.py`)

* `_get_strategy_order`：当 *未显式* 选择 `POSITION` 时，自动回退链不再把
  `POSITION` 加进来。
* `_self_healing_locate`：自愈链使用 `SAFE_HEALING_STRATEGY_CHAIN`，不
  退化到像素坐标。

### 2. 步骤执行器 (`src/executor/step_executor.py`)

* 进入步骤之前如果 `step.locator_requires_confirmation()` 返回 `True`，
  直接 raise `RuntimeError("拒绝执行：步骤定位尚未确认…")`，安全控制器
  视情况累计失败计数。
* `_execute_click` 在所有定位策略都失败之后，**不再** 自动 fallback 到
  录制时的 `(x, y)`。该路径只在显式开启逃生开关时被允许。

### 3. 闭环评估器 (`src/analyzer/closure_assessor.py`)

* 与运行时同源：任何 `target.strategy == POSITION` 或缺少窗口/页面
  上下文、前后置检查的步骤都会产出 `critical` / `high` 问题码，并把
  `ready` 拉为 `False`。

## 四、逃生开关

```bash
# 仅在极少数情况下临时打开（例如对画布执行无锚拖拽）。
export AUTO_AGENT_ALLOW_COORDINATE_FALLBACK=1
```

打开之后：

* 元素定位器恢复 `POSITION` 作为最后回退；
* 步骤执行器允许未确认/纯坐标步骤继续执行（但仍会记录 warning）。

未设置或设为 `0/false` 时全部恢复严格模式。

## 五、自检脚本

```bash
.venv/bin/python scripts/closure_smoke.py
```

该脚本会依次：

1. 构造一段录制日志；
2. 走完 `OperationPreprocessor → ScriptGenerator`；
3. 用 `FlowClosureAssessor` 验证脆弱流程被拒、加固流程被接受；
4. 用 stub 适配器 dry-run 加固流程并校验 `ExecutionStepLog` 形状。

退出码为 0 时代表闭环 100% 通过。CI 里建议作为快速门禁。

## 六、单元测试覆盖

* `tests/unit/test_element_locator.py`：覆盖正常定位、自愈链、安全默认值
  和逃生开关。
* `tests/unit/test_step_executor.py`：覆盖未确认步骤被拒、逃生开关下
  恢复执行、窗口前置守卫。
* `tests/unit/test_closure_assessor.py` / `test_script_generator.py` /
  `test_confidence_scorer.py` / `test_analysis_service.py`：覆盖分析与
  闭环评估的所有关键判定。

执行：

```bash
.venv/bin/python -m pytest tests/unit -q
```

应当全部通过（仅 `test_filesystem_recorder_ignores_data_dir_and_sqlite_journal`
属于已知与本次改动无关的 tmp_path 路径过滤问题）。
