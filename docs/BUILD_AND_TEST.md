# 构建与测试

## 环境要求

- Python 3.12
- macOS 下若要启用完整桌面录制，需要系统辅助功能权限与屏幕录制权限
- Windows 下若要启用真实元素定位，需要安装 .[windows] extra 以提供 UI Automation 绑定
- Linux 下若要启用真实窗口与元素定位，需要系统安装 wmctrl、xprop，并启用 AT-SPI 可访问性栈
- Web 下若要启用真实浏览器元素定位与执行，需要安装 .[web] extra 并执行 playwright install chromium

## 安装步骤

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m ensurepip --upgrade
python -m pip install -e ".[dev]"
```

如需 macOS 能力：

```bash
python -m pip install -e ".[dev,macos]"
```

如需 Windows UI Automation 能力：

```bash
python -m pip install -e ".[dev,windows]"
```

如需 Web Playwright 能力：

```bash
python -m pip install -e ".[dev,web]"
playwright install chromium
```

## 环境变量

推荐通过 .env 或 shell 环境注入：

```bash
export AUTO_AGENT_DATA_DIR=data
export AUTO_AGENT_DB_URL=sqlite+aiosqlite:///data/auto_agent.db
export AUTO_AGENT_RECORD_ENABLE_MOUSE=true
export AUTO_AGENT_RECORD_ENABLE_KEYBOARD=true
export AUTO_AGENT_RECORD_ENABLE_WINDOW=true
export AUTO_AGENT_RECORD_ENABLE_CLIPBOARD=true
export AUTO_AGENT_RECORD_ENABLE_FILESYSTEM=false
export AUTO_AGENT_RECORD_FILESYSTEM_PATHS=
export AUTO_AGENT_WEB_BROWSER=chromium
export AUTO_AGENT_WEB_HEADLESS=true
export AUTO_AGENT_WEB_TIMEOUT_MS=10000
export AUTO_AGENT_WEB_VIEWPORT_WIDTH=1440
export AUTO_AGENT_WEB_VIEWPORT_HEIGHT=900
```

测试环境推荐禁用原生 recorder：

```bash
export AUTO_AGENT_RECORD_ENABLE_MOUSE=false
export AUTO_AGENT_RECORD_ENABLE_KEYBOARD=false
export AUTO_AGENT_RECORD_ENABLE_WINDOW=false
export AUTO_AGENT_RECORD_ENABLE_CLIPBOARD=false
export AUTO_AGENT_RECORD_ENABLE_FILESYSTEM=false
```

## 启动服务

```bash
source .venv/bin/activate
auto-agent serve
```

如果 CLI 需要连到非默认地址，可以通过环境变量覆盖：

```bash
export AUTO_AGENT_API_BASE=http://127.0.0.1:8011/api
# 或拆分配置
export AUTO_AGENT_API_HOST=127.0.0.1
export AUTO_AGENT_API_PORT=8011
export AUTO_AGENT_API_SCHEME=http
```

## 测试命令

完整测试：

```bash
python -m pytest -q
```

一键 smoke 验证：

```bash
python data/scripts/run_smoke.py
```

前端流程编辑器 smoke 验证：

```bash
python data/scripts/run_flow_editor_smoke.py
```

这个脚本会验证：

- 图上的 branch edge quick edit
- 线性 flow 的拖拽重排与自动重连

这个脚本会顺序验证：

- FastAPI 服务可启动
- /api/health 与 /api/health/details 可访问
- 静态首页已挂载
- CLI 可通过可配置 API 地址访问服务
- WebRecorder 可真实录制一次本地页面交互并完成分析产出流程

核心测试：

```bash
python -m pytest -q tests/test_analyzer.py tests/test_executor.py tests/test_e2e_api.py
```

性能测试：

```bash
python -m pytest -q tests/test_benchmark.py
```

## 本次验证结果

本次构建完成后，已在当前仓库环境验证：

- python -m pytest -q
- python data/scripts/run_smoke.py
- python data/scripts/run_flow_editor_smoke.py
- 200 passed

## 常见问题

### 1. 虚拟环境里没有 pip

```bash
python -m ensurepip --upgrade
```

### 2. macOS 提示未授予辅助功能权限

如果你只是跑测试，请禁用原生 recorder。

如果你要录制真实桌面操作，请在系统设置中为 Python 或终端授予：

- Accessibility
- Screen Recording

### 3. Linux 下窗口或元素能力返回空结果

请先确认以下前提成立：

- 已安装 wmctrl
- 已安装 xprop
- 桌面环境暴露 X11 / EWMH 兼容窗口信息
- 已启用 AT-SPI 可访问性支持

### 4. Web 流程启动失败或无法定位元素

请先确认以下前提成立：

- 已安装 .[web] extra
- 已执行 playwright install chromium
- 流程 target 中为 Web 步骤填写了 selector、xpath、url 其中之一
- 目标页面未被 CSP、登录态或 iframe 边界阻断当前选择器

### 5. benchmark 测试缺少 numpy

当前已把 numpy 写入 dev 依赖。重新执行：

```bash
python -m pip install -e ".[dev]"
```
