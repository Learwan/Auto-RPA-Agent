import asyncio
import json
import os

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

console = Console()
app = typer.Typer(name="auto-agent", help="Automated Agent Workflow CLI", no_args_is_help=True)

record_app = typer.Typer(name="record", help="Recording operations", no_args_is_help=True)
app.add_typer(record_app, name="record")

flow_app = typer.Typer(name="flow", help="Manage automation flows", no_args_is_help=True)
app.add_typer(flow_app, name="flow")

analyze_app = typer.Typer(name="analyze", help="Analyze recorded sessions", no_args_is_help=True)
app.add_typer(analyze_app, name="analyze")

exec_app = typer.Typer(name="executions", help="View execution history", no_args_is_help=True)
app.add_typer(exec_app, name="executions")

llm_app = typer.Typer(name="llm", help="LLM-powered analysis and suggestions", no_args_is_help=True)
app.add_typer(llm_app, name="llm")

model_app = typer.Typer(name="model", help="Manage local AI models", no_args_is_help=True)
app.add_typer(model_app, name="model")

train_app = typer.Typer(name="train", help="Train and optimize workflows with RL", no_args_is_help=True)
app.add_typer(train_app, name="train")


def _normalize_api_base(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        return "http://localhost:8000/api"
    if normalized.endswith("/api"):
        return normalized
    return f"{normalized}/api"


def get_api_base() -> str:
    explicit = os.getenv("AUTO_AGENT_API_BASE", "").strip()
    if explicit:
        return _normalize_api_base(explicit)

    host = os.getenv("AUTO_AGENT_API_HOST", "localhost").strip() or "localhost"
    port = os.getenv("AUTO_AGENT_API_PORT", "8000").strip() or "8000"
    scheme = os.getenv("AUTO_AGENT_API_SCHEME", "http").strip() or "http"
    return f"{scheme}://{host}:{port}/api"


def api_url(path: str) -> str:
    return f"{get_api_base()}{path}"


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn

    console.print(f"[bold green]Starting Auto Agent Workflow API on {host}:{port}[/bold green]")
    uvicorn.run("src.api.app:app", host=host, port=port, reload=True)


@record_app.command("start")
def record_start(
    name: str = typer.Option(..., prompt=True, help="Session name"),
    tags: str = typer.Option("", help="Comma-separated tags"),
    mode: str = typer.Option("desktop", "--mode", help="Recording mode: desktop|web"),
    url: str = typer.Option("", "--url", help="Initial URL for web recording"),
    browser: str = typer.Option("", "--browser", help="Browser for web recording: chromium|firefox|webkit"),
    headless: bool = typer.Option(False, "--headless", help="Run managed browser headless for web recording"),
    hud: bool = typer.Option(True, "--hud/--no-hud", help="Show a click-through recording HUD while desktop recording"),
    hud_ai_assist: bool = typer.Option(
        False,
        "--hud-ai-assist/--no-hud-ai-assist",
        help="Let AI help infer weakly-recognized desktop components in the HUD",
    ),
):
    import httpx

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    with httpx.Client(trust_env=False) as client:
        resp = client.post(
            api_url("/sessions"),
            json={
                "name": name,
                "description": "",
                "tags": tags,
            },
        )
        if resp.status_code == 200:
            session = resp.json()
            session_id = session["id"]
            params = {"mode": mode}
            if url:
                params["url"] = url
            if browser:
                params["browser"] = browser
            if mode == "web":
                params["headless"] = str(headless).lower()
            else:
                params["hud"] = str(hud).lower()
                params["hud_ai_assist"] = str(hud_ai_assist).lower()

            start_resp = client.post(api_url(f"/sessions/{session_id}/start"), params=params)
            if start_resp.status_code == 200:
                console.print(f"[bold green]Recording started[/bold green] Session: {session_id}")
                console.print(f"  Name: {name}")
                console.print(f"  Mode: {mode}")
                console.print(f"  Tags: {tag_list}")
                if mode == "web":
                    console.print(f"  URL: {url or 'about:blank'}")
                    console.print(f"  Browser: {browser or 'chromium'}")
                else:
                    console.print(f"  HUD: {'on' if hud else 'off'}")
                    console.print(f"  HUD AI Assist: {'on' if hud_ai_assist else 'off'}")
            else:
                console.print(f"[bold red]Failed to start recording: {start_resp.text}[/bold red]")
        else:
            console.print(f"[bold red]Failed to create session: {resp.text}[/bold red]")


@record_app.command("stop")
def record_stop(session_id: str = typer.Option(..., prompt=True, help="Session ID to stop")):
    import httpx

    with httpx.Client(trust_env=False) as client:
        resp = client.post(api_url(f"/sessions/{session_id}/stop"))
        if resp.status_code == 200:
            console.print(f"[bold green]Recording stopped[/bold green] Session: {session_id}")
        else:
            console.print(f"[bold red]Failed to stop: {resp.text}[/bold red]")


@record_app.command("list")
def record_list():
    import httpx

    with httpx.Client(trust_env=False) as client:
        resp = client.get(api_url("/sessions"))
        if resp.status_code == 200:
            sessions = resp.json()
            table = Table(title="Recording Sessions")
            table.add_column("ID", style="cyan", max_width=12)
            table.add_column("Name", style="bold")
            table.add_column("Status", style="green")
            table.add_column("Operations", style="yellow")
            for s in sessions:
                table.add_row(s["id"][:12], s["name"], s["status"], str(s["operation_count"]))
            console.print(table)
        else:
            console.print(f"[bold red]Failed to list sessions: {resp.text}[/bold red]")


@record_app.command("show")
def record_show(session_id: str = typer.Argument(..., help="Session ID")):
    import httpx

    with httpx.Client(trust_env=False) as client:
        resp = client.get(api_url(f"/sessions/{session_id}"))
        if resp.status_code == 200:
            s = resp.json()
            console.print(f"[bold]Session:[/bold] {s['id']}")
            console.print(f"  Name: {s['name']}")
            console.print(f"  Status: {s['status']}")
            console.print(f"  Operations: {s['operation_count']}")
            console.print(f"  Tags: {s.get('tags', [])}")
        else:
            console.print("[bold red]Session not found[/bold red]")


@analyze_app.command("")
def analyze_session(
    session_id: str = typer.Argument(..., help="Session ID to analyze"),
    min_confidence: float = typer.Option(0.5, "--min-confidence", "-c", help="Minimum confidence threshold"),
    generate: bool = typer.Option(False, "--generate", "-g", help="Auto-generate and save flows"),
):
    import httpx

    with Progress(SpinnerColumn(), TextColumn("[bold]Analyzing session...[/bold]"), console=console) as progress:
        progress.add_task("analyze", total=None)
        with httpx.Client(timeout=60.0, trust_env=False) as client:
            resp = client.post(api_url(f"/sessions/{session_id}/analyze"), params={"min_confidence": min_confidence})

    if resp.status_code != 200:
        console.print(f"[bold red]Analysis failed: {resp.text}[/bold red]")
        return

    result = resp.json()
    flows_found = result["flows_found"]

    if flows_found == 0:
        console.print(Panel("[bold yellow]No automatable patterns detected[/bold yellow]", title="Analysis Result"))
        console.print("  Try recording more operations or lowering the confidence threshold.")
        return

    console.print(Panel(f"[bold green]Found {flows_found} automatable flow(s)[/bold green]", title="Analysis Result"))

    table = Table(title="Detected Flows")
    table.add_column("ID", style="cyan", max_width=12)
    table.add_column("Name", style="bold")
    table.add_column("Confidence", style="green")
    table.add_column("Reliable", style="yellow")
    table.add_column("Steps", style="blue")

    for flow in result["flows"]:
        reliable = "✓" if flow["is_reliable"] else "✗"
        table.add_row(
            flow["id"][:12],
            flow["name"],
            f"{flow['confidence']:.2f}",
            reliable,
            str(flow["steps_count"]),
        )
    console.print(table)

    for flow in result["flows"]:
        if flow.get("suggestions"):
            console.print(f"\n[bold]Suggestions for {flow['name']}:[/bold]")
            for suggestion in flow["suggestions"]:
                impact_color = (
                    "green"
                    if suggestion["impact"] == "high"
                    else "yellow"
                    if suggestion["impact"] == "medium"
                    else "dim"
                )
                console.print(f"  [{impact_color}][{suggestion['category']}][/{impact_color}] {suggestion['message']}")

    if generate:
        console.print("\n[bold green]Flows have been saved automatically.[/bold green]")
    else:
        console.print("\n[dim]Use --generate flag to auto-save flows, or use 'flow show <id>' to inspect.[/dim]")


@flow_app.command("list")
def flow_list():
    import httpx

    with httpx.Client(trust_env=False) as client:
        resp = client.get(api_url("/automations"))
        if resp.status_code == 200:
            flows = resp.json()
            if not flows:
                console.print("[bold yellow]No automation flows found[/bold yellow]")
                return
            table = Table(title="Automation Flows")
            table.add_column("ID", style="cyan", max_width=12)
            table.add_column("Name", style="bold")
            table.add_column("Confidence", style="green")
            table.add_column("Steps", style="blue")
            table.add_column("Executions", style="yellow")
            for f in flows:
                table.add_row(
                    f["id"][:12],
                    f["name"],
                    f"{f['confidence']:.2f}",
                    str(len(f.get("steps", []))),
                    str(f.get("execution_count", 0)),
                )
            console.print(table)
        else:
            console.print("[bold red]Failed to list flows[/bold red]")


@flow_app.command("show")
def flow_show(flow_id: str = typer.Argument(..., help="Flow ID")):
    import httpx

    with httpx.Client(trust_env=False) as client:
        resp = client.get(api_url(f"/automations/{flow_id}"))
        if resp.status_code == 200:
            f = resp.json()
            console.print(Panel(f"[bold]{f['name']}[/bold]", title=f"Flow: {f['id'][:12]}"))
            console.print(f"  Description: {f.get('description', 'N/A')}")
            console.print(f"  Confidence: {f['confidence']:.2f}")
            console.print(f"  Steps: {len(f.get('steps', []))}")
            console.print(f"  Executions: {f.get('execution_count', 0)}")
            console.print(f"  Success Rate: {f.get('success_count', 0)}/{f.get('execution_count', 0)}")

            steps = f.get("steps", [])
            if steps:
                console.print("\n[bold]Steps:[/bold]")
                for i, step in enumerate(steps):
                    step_type = step.get("type", "unknown")
                    target_desc = _describe_step_target(step)
                    console.print(f"  {i + 1}. [{step_type}] {target_desc}")

            score_resp = client.get(api_url(f"/automations/{flow_id}/score"))
            if score_resp.status_code == 200:
                score = score_resp.json()
                console.print(
                    f"\n[bold]Quality Score:[/bold] {score['overall_confidence']:.2f} ({'Reliable' if score['is_reliable'] else 'Unreliable'})"
                )
                for dim in score.get("dimensions", []):
                    bar_len = int(dim["score"] * 20)
                    bar = "█" * bar_len + "░" * (20 - bar_len)
                    console.print(f"  {dim['name']}: [{bar}] {dim['score']:.2f}")
        else:
            console.print("[bold red]Flow not found[/bold red]")


@flow_app.command("export")
def flow_export(
    flow_id: str = typer.Argument(..., help="Flow ID"),
    format: str = typer.Option("json", "--format", "-f", help="Export format: json|python"),
    output: str = typer.Option("", "--output", "-o", help="Output file path"),
):
    import httpx

    with httpx.Client(trust_env=False) as client:
        resp = client.post(api_url(f"/automations/{flow_id}/export"), params={"format": format})
        if resp.status_code == 200:
            result = resp.json()
            content = result.get("content", "")
            if output:
                with open(output, "w") as f:
                    f.write(content if isinstance(content, str) else json.dumps(content, indent=2))
                console.print(f"[bold green]Exported to {output}[/bold green]")
            else:
                if isinstance(content, str):
                    console.print(content)
                else:
                    console.print_json(json.dumps(content, indent=2))
        else:
            console.print(f"[bold red]Export failed: {resp.text}[/bold red]")


@app.command("run")
def run_automation(
    automation_id: str = typer.Argument(..., help="Automation flow ID"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate without executing"),
    variables: str = typer.Option("", "--vars", help="JSON variables for the flow"),
):
    import httpx

    var_dict = {}
    if variables:
        try:
            var_dict = json.loads(variables)
        except json.JSONDecodeError:
            console.print("[bold red]Invalid JSON for --vars[/bold red]")
            return

    endpoint = (
        api_url(f"/automations/{automation_id}/dry-run")
        if dry_run
        else api_url(f"/automations/{automation_id}/execute")
    )
    params = {}
    if var_dict:
        params["variables"] = json.dumps(var_dict)

    mode_label = "[bold blue]DRY-RUN[/bold blue]" if dry_run else "[bold green]EXECUTE[/bold green]"
    console.print(f"{mode_label} Running automation: {automation_id[:12]}...")

    with Progress(SpinnerColumn(), TextColumn("[bold]Executing...[/bold]"), console=console) as progress:
        progress.add_task("exec", total=None)
        with httpx.Client(timeout=120.0, trust_env=False) as client:
            resp = client.post(endpoint, params=params, json=var_dict if var_dict else None)

    if resp.status_code != 200:
        console.print(f"[bold red]Execution failed: {resp.text}[/bold red]")
        return

    record = resp.json()
    status = record.get("status", "unknown")
    status_color = "green" if status == "completed" else "red" if status == "failed" else "yellow"

    console.print(Panel(f"[bold {status_color}]{status.upper()}[/bold {status_color}]", title="Execution Result"))
    console.print(f"  Execution ID: {record.get('id', 'N/A')[:12]}")
    console.print(f"  Automation: {automation_id[:12]}")

    if dry_run:
        console.print("  Mode: Dry-Run (simulated)")

    step_logs = record.get("step_logs", [])
    if step_logs:
        console.print("\n[bold]Step Results:[/bold]")
        for i, log in enumerate(step_logs):
            step_status = log.get("status", "unknown")
            icon = "✓" if step_status == "completed" else "✗" if step_status == "failed" else "○"
            step_type = log.get("step_type", "unknown")
            duration = log.get("duration_ms", 0)
            console.print(f"  {icon} Step {i + 1}: [{step_type}] {duration}ms")
            if log.get("error"):
                console.print(f"    [red]Error: {log['error']}[/red]")


@exec_app.command("list")
def executions_list(limit: int = typer.Option(20, "--limit", "-n", help="Number of records")):
    import httpx

    with httpx.Client(trust_env=False) as client:
        resp = client.get(api_url("/executions"), params={"limit": limit})
        if resp.status_code == 200:
            records = resp.json()
            if not records:
                console.print("[bold yellow]No execution history[/bold yellow]")
                return
            table = Table(title="Execution History")
            table.add_column("ID", style="cyan", max_width=12)
            table.add_column("Automation", style="bold", max_width=12)
            table.add_column("Status", style="green")
            table.add_column("Step", style="blue")
            for r in records:
                status = r.get("status", "unknown")
                status_color = "green" if status == "completed" else "red" if status == "failed" else "yellow"
                table.add_row(
                    r.get("id", "")[:12],
                    r.get("automation_id", "")[:12],
                    f"[{status_color}]{status}[/{status_color}]",
                    f"{r.get('current_step_index', 0)}",
                )
            console.print(table)
        else:
            console.print(f"[bold red]Failed to list executions: {resp.text}[/bold red]")


@exec_app.command("show")
def executions_show(execution_id: str = typer.Argument(..., help="Execution ID")):
    import httpx

    with httpx.Client(trust_env=False) as client:
        resp = client.get(api_url(f"/executions/{execution_id}"))
        if resp.status_code == 200:
            r = resp.json()
            status = r.get("status", "unknown")
            status_color = "green" if status == "completed" else "red" if status == "failed" else "yellow"
            console.print(
                Panel(
                    f"[bold {status_color}]{status.upper()}[/bold {status_color}]",
                    title=f"Execution: {execution_id[:12]}",
                )
            )
            console.print(f"  Automation ID: {r.get('automation_id', 'N/A')[:12]}")
            console.print(f"  Current Step: {r.get('current_step_index', 0)}")

            step_logs = r.get("step_logs", [])
            if step_logs:
                console.print("\n[bold]Step Logs:[/bold]")
                for i, log in enumerate(step_logs):
                    step_status = log.get("status", "unknown")
                    icon = "✓" if step_status == "completed" else "✗" if step_status == "failed" else "○"
                    step_type = log.get("step_type", "unknown")
                    duration = log.get("duration_ms", 0)
                    console.print(f"  {icon} Step {i + 1}: [{step_type}] {duration}ms")
                    if log.get("error"):
                        console.print(f"    [red]Error: {log['error']}[/red]")
                    if log.get("action_taken"):
                        console.print(f"    Action: {log['action_taken']}")
        else:
            console.print("[bold red]Execution not found[/bold red]")


def _describe_step_target(step: dict) -> str:
    target = step.get("target")
    if not target:
        return step.get("data", {}).get("text", "")[:40] if step.get("data") else ""
    strategy = target.get("strategy", "")
    if strategy == "position":
        pos = target.get("position", {})
        return f"at ({pos.get('x', 0)}, {pos.get('y', 0)})"
    if strategy == "accessibility_id":
        return f"accessibility_id={target.get('accessibility_id', '')}"
    if strategy == "text_match":
        return f'text="{target.get("text_contains", target.get("title", ""))}"'
    if strategy == "css_selector":
        return f'selector="{target.get("selector", "")}"'
    if strategy == "xpath":
        return f'xpath="{target.get("xpath", "")}"'
    return strategy


@llm_app.command("status")
def llm_status():
    import httpx

    with httpx.Client(trust_env=False) as client:
        resp = client.get(api_url("/llm/status"))
        if resp.status_code == 200:
            data = resp.json()
            if data.get("configured"):
                console.print(Panel("[bold green]LLM Configured[/bold green]", title="LLM Status"))
                console.print(f"  Model: {data.get('model', 'N/A')}")
                console.print(f"  Base URL: {data.get('base_url', 'N/A')}")
            else:
                console.print(Panel("[bold yellow]LLM Not Configured[/bold yellow]", title="LLM Status"))
                console.print("  Set LLM_API_KEY in .env file to enable LLM features.")
        else:
            console.print("[bold red]Failed to check LLM status[/bold red]")


@llm_app.command("chat")
def llm_chat(
    message: str = typer.Argument(..., help="Message to send to LLM"),
    model: str = typer.Option("", "--model", "-m", help="Override model name"),
):
    import httpx

    payload: dict[str, object] = {"messages": [{"role": "user", "content": message}]}
    if model:
        payload["model"] = model

    with Progress(SpinnerColumn(), TextColumn("[bold]Thinking...[/bold]"), console=console) as progress:
        progress.add_task("chat", total=None)
        with httpx.Client(timeout=120.0, trust_env=False) as client:
            resp = client.post(api_url("/llm/chat"), json=payload)

    if resp.status_code != 200:
        console.print(f"[bold red]LLM request failed: {resp.text}[/bold red]")
        return

    result = resp.json()
    console.print(Panel(result["response"], title="LLM Response"))


@llm_app.command("analyze")
def llm_analyze(
    flow_id: str = typer.Argument(..., help="Automation flow ID to analyze"),
):
    import httpx

    with httpx.Client(timeout=30.0, trust_env=False) as client:
        flow_resp = client.get(api_url(f"/automations/{flow_id}"))
        if flow_resp.status_code != 200:
            console.print("[bold red]Flow not found[/bold red]")
            return
        flow_data = flow_resp.json()

    flow_desc = json.dumps(flow_data.get("steps", []), indent=2, ensure_ascii=False)
    ops_summary = f"Flow: {flow_data.get('name', 'Unknown')}, Steps: {len(flow_data.get('steps', []))}, Confidence: {flow_data.get('confidence', 0):.2f}"

    console.print("[bold]Analyzing flow with LLM...[/bold]")

    with Progress(SpinnerColumn(), TextColumn("[bold]LLM analyzing...[/bold]"), console=console) as progress:
        progress.add_task("analyze", total=None)
        with httpx.Client(timeout=120.0, trust_env=False) as client:
            resp = client.post(
                api_url("/llm/analyze-flow"),
                json={
                    "flow_description": flow_desc,
                    "operations_summary": ops_summary,
                },
            )

    if resp.status_code != 200:
        console.print(f"[bold red]Analysis failed: {resp.text}[/bold red]")
        return

    result = resp.json()
    console.print(Panel(result["analysis"], title=f"LLM Analysis: {flow_data.get('name', 'Flow')}"))


@llm_app.command("explain")
def llm_explain(
    session_id: str = typer.Argument(..., help="Session ID to explain"),
):
    import httpx

    with httpx.Client(timeout=30.0, trust_env=False) as client:
        session_resp = client.get(api_url(f"/sessions/{session_id}"))
        if session_resp.status_code != 200:
            console.print("[bold red]Session not found[/bold red]")
            return

    from src.analyzer.preprocessor import OperationPreprocessor
    from src.db.database import get_session_factory
    from src.db.repository import Repository

    async def _load_ops():
        factory = get_session_factory()
        async with factory() as db:
            repo = Repository(db)
            return await repo.get_operations_by_session(session_id)

    ops = asyncio.run(_load_ops())
    if not ops:
        console.print("[bold yellow]No operations found for this session[/bold yellow]")
        return

    preprocessor = OperationPreprocessor()
    normalized = preprocessor.preprocess(ops)
    ops_text = "\n".join(f"  {i + 1}. [{n.op_type}] {n.data}" for i, n in enumerate(normalized[:50]))

    console.print("[bold]Explaining operations with LLM...[/bold]")

    with Progress(SpinnerColumn(), TextColumn("[bold]LLM thinking...[/bold]"), console=console) as progress:
        progress.add_task("explain", total=None)
        with httpx.Client(timeout=120.0, trust_env=False) as client:
            resp = client.post(api_url("/llm/explain-operations"), json={"operations_text": ops_text})

    if resp.status_code != 200:
        console.print(f"[bold red]Explanation failed: {resp.text}[/bold red]")
        return

    result = resp.json()
    console.print(Panel(result["explanation"], title="LLM Explanation"))


@llm_app.command("enhance")
def llm_enhance(
    flow_id: str = typer.Argument(..., help="Flow ID to enhance"),
    output: str = typer.Option("", "--output", "-o", help="Output file path"),
):
    import httpx

    with httpx.Client(timeout=30.0, trust_env=False) as client:
        flow_resp = client.get(api_url(f"/automations/{flow_id}"))
        if flow_resp.status_code != 200:
            console.print("[bold red]Flow not found[/bold red]")
            return
        flow_data = flow_resp.json()

    flow_json = json.dumps(flow_data, indent=2, ensure_ascii=False)

    console.print("[bold]Generating enhanced script with LLM...[/bold]")

    with Progress(SpinnerColumn(), TextColumn("[bold]LLM generating...[/bold]"), console=console) as progress:
        progress.add_task("enhance", total=None)
        with httpx.Client(timeout=120.0, trust_env=False) as client:
            resp = client.post(api_url("/llm/enhance-script"), json={"flow_json": flow_json})

    if resp.status_code != 200:
        console.print(f"[bold red]Enhancement failed: {resp.text}[/bold red]")
        return

    result = resp.json()
    script = result["script"]

    if output:
        with open(output, "w") as f:
            f.write(script)
        console.print(f"[bold green]Enhanced script saved to {output}[/bold green]")
    else:
        console.print(Panel(script, title="Enhanced Script"))


@model_app.command("list")
def model_list():
    from src.llm.model_manager import ModelManager

    manager = ModelManager()
    models = manager.list_models()
    if not models:
        console.print("[bold yellow]No models registered[/bold yellow]")
        return
    table = Table(title="Available Models")
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="bold")
    table.add_column("Status", style="green")
    table.add_column("Size", style="yellow")
    table.add_column("Description", style="dim")
    for m in models:
        table.add_row(m.name, m.model_type, m.status.value, f"{m.size_mb:.0f}MB", m.description[:50])
    console.print(table)


@model_app.command("scan")
def model_scan():
    from src.llm.model_manager import ModelManager

    manager = ModelManager()
    models = manager.scan_models()
    ready = sum(1 for m in models if m.status.value == "ready")
    console.print(f"[bold green]Scan complete: {ready}/{len(models)} models ready[/bold green]")
    model_list()


@model_app.command("download")
def model_download(name: str = typer.Argument(..., help="Model name to download")):
    from src.llm.model_manager import ModelManager

    manager = ModelManager()

    async def _download():
        return await manager.download_model(name)

    model = asyncio.run(_download())
    if model:
        console.print(f"[bold green]Model '{name}' status: {model.status.value}[/bold green]")
    else:
        console.print(f"[bold red]Model '{name}' not found[/bold red]")


@model_app.command("switch")
def model_switch(
    name: str = typer.Argument(..., help="Model name to activate"),
    model_type: str = typer.Option("grounding", "--type", help="Model type: grounding|bt_generation"),
):
    from src.llm.model_manager import ModelManager

    manager = ModelManager()

    async def _switch():
        return await manager.switch_model(name, model_type)

    try:
        asyncio.run(_switch())
        console.print(f"[bold green]Switched {model_type} model to: {name}[/bold green]")
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")


@train_app.command("start")
def train_start(
    session_id: str = typer.Argument(..., help="Session ID to train from"),
    episodes: int = typer.Option(1000, "--episodes", "-e", help="Number of training episodes"),
    output: str = typer.Option("", "--output", "-o", help="Output file for enhanced flow"),
):
    from src.analyzer.service import AnalysisService
    from src.training.rl_trainer import RLTrainer, TrainingConfig

    console.print(f"[bold]Training from session: {session_id}[/bold]")
    console.print(f"  Episodes: {episodes}")

    config = TrainingConfig(episodes=episodes)

    async def _train():
        service = AnalysisService()
        scored_flows = await service.analyze_session(session_id)
        if not scored_flows:
            return None
        best_flow = scored_flows[0].flow
        trainer = RLTrainer(config)
        return await trainer.train(best_flow, session_id)

    with Progress(SpinnerColumn(), TextColumn("[bold]Training...[/bold]"), console=console) as progress:
        progress.add_task("train", total=None)
        result = asyncio.run(_train())

    if result is None:
        console.print("[bold red]No flow found for training[/bold red]")
        return

    console.print(Panel("[bold green]Training Complete[/bold green]", title="Training Result"))
    console.print(f"  Episodes: {result.episodes_completed}")
    console.print(f"  Success Rate: {result.success_rate:.1%}")
    console.print(f"  Avg Reward: {result.avg_reward:.3f}")

    if result.enhanced_flow:
        console.print(f"  Enhanced Flow Confidence: {result.enhanced_flow.confidence:.2f}")
        if output:
            flow_json = json.dumps(result.enhanced_flow.model_dump(), indent=2, ensure_ascii=False)
            with open(output, "w") as f:
                f.write(flow_json)
            console.print(f"[bold green]Enhanced flow saved to {output}[/bold green]")


if __name__ == "__main__":
    app()
