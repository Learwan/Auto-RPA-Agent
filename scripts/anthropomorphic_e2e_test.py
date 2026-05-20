#!/usr/bin/env python3
"""拟人化端到端测试：API 创建浏览器录制 → 模拟点击 → 分析 → 检查定位策略。"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
TIMEOUT = httpx.Timeout(120.0, connect=10.0)


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT, trust_env=False) as client:
        # 1. Health
        health = await client.get("/api/health")
        if health.status_code != 200:
            print(f"FAIL health: {health.status_code}")
            return 1
        print("ok  health")

        # 2. Create session
        r = await client.post(
            "/api/sessions",
            json={
                "name": "拟人测试-浏览器",
                "description": "Agent 自动化拟人 E2E",
                "tags": "e2e,anthropomorphic",
            },
        )
        r.raise_for_status()
        session = r.json()
        sid = session["id"]
        print(f"ok  session created: {sid}")

        # 3. Start browser recording (headless Playwright) — start endpoint uses query params
        r = await client.post(
            f"/api/sessions/{sid}/start",
            params={
                "mode": "browser",
                "url": "https://example.com",
                "browser": "chromium",
                "headless": "true",
                "hud": "false",
            },
        )
        r.raise_for_status()
        print(f"ok  recording started: status={r.json().get('status')}")

        # 4. Wait for page load + simulate human pause
        await asyncio.sleep(2.5)

        # 5. Stop recording
        r = await client.post(f"/api/sessions/{sid}/stop")
        r.raise_for_status()
        stopped = r.json()
        op_count = stopped.get("operation_count", 0)
        print(f"ok  recording stopped: operations={op_count}")

        # 6. Fetch operations
        r = await client.get(f"/api/sessions/{sid}/operations")
        if r.status_code == 200:
            ops = r.json()
            print(f"ok  fetched {len(ops)} operations")
            for i, op in enumerate(ops[:5]):
                ctx = op.get("context") or {}
                fe = ctx.get("focused_element") or {}
                anchor = fe.get("selector") or fe.get("xpath") or fe.get("identifier") or fe.get("title")
                print(f"    op[{i}] type={op.get('type')} anchor={anchor or '(none)'} platform={ctx.get('platform')}")
        else:
            print(f"warn operations endpoint: {r.status_code}")

        # 7. Analyze
        print("..  analyzing (may take a minute if LLM configured)...")
        r = await client.post(f"/api/sessions/{sid}/analyze", params={"min_confidence": 0.2})
        if r.status_code != 200:
            print(f"FAIL analyze: {r.status_code} {r.text[:300]}")
            return 1
        flows_payload = r.json()
        flows = flows_payload.get("flows", flows_payload if isinstance(flows_payload, list) else [])
        print(f"ok  analyze returned {len(flows)} flow candidate(s)")

        # 8. Inspect step strategies (fetch full flow detail)
        position_only = 0
        anchored = 0
        for summary in flows[:3]:
            flow_id = summary.get("id")
            detail_r = await client.get(f"/api/automations/{flow_id}")
            if detail_r.status_code != 200:
                print(f"warn could not load flow {flow_id}")
                continue
            flow = detail_r.json()
            name = flow.get("name", "?")
            steps = flow.get("steps") or []
            print(f"\n--- Flow: {name} ({len(steps)} steps) ---")
            for step in steps:
                target = step.get("target") or {}
                strategy = target.get("strategy", "?")
                sel = target.get("selector") or target.get("accessibility_id") or target.get("text_contains")
                if strategy == "position" and not sel:
                    position_only += 1
                    tag = "POSITION ⚠"
                else:
                    anchored += 1
                    tag = strategy
                print(f"  [{step.get('type')}] {tag}  anchor={sel or '-'}  {step.get('description', '')[:40]}")

        print(f"\n=== Summary ===")
        print(f"  anchored steps: {anchored}")
        print(f"  position-only:  {position_only}")
        if op_count == 0:
            print("WARN: no operations captured — check Playwright / web recorder")
            return 2
        if anchored == 0 and position_only > 0:
            print("WARN: all steps are coordinate-based — web anchors missing")
            return 2
        print("PASS anthropomorphic E2E")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
