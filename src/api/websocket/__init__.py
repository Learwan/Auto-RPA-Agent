from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.recorder.event_bus import EventBus

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/sessions/{session_id}/events")
async def session_events_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()
    bus = EventBus.get_instance()
    topic = f"session.{session_id}.events"
    queue = bus.subscribe(topic)

    try:
        sender_task = asyncio.create_task(_send_events(websocket, queue))
        receiver_task = asyncio.create_task(_receive_commands(websocket, session_id, bus))

        done, pending = await asyncio.wait(
            [sender_task, receiver_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for session: %s", session_id)
    finally:
        bus.unsubscribe(topic, queue)


@router.websocket("/sessions/{session_id}/analysis")
async def session_analysis_ws(websocket: WebSocket, session_id: str):
    await websocket.accept()
    bus = EventBus.get_instance()
    topic = f"session.{session_id}.analysis"
    queue = bus.subscribe(topic)

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_json(
                    {
                        "type": "analysis_update",
                        "payload": event.payload,
                        "timestamp": event.timestamp,
                    }
                )
            except TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        logger.info("Analysis WebSocket disconnected for session: %s", session_id)
    finally:
        bus.unsubscribe(topic, queue)


async def _send_events(websocket: WebSocket, queue: asyncio.Queue) -> None:
    while True:
        event = await queue.get()
        try:
            await websocket.send_json(
                {
                    "type": "event",
                    "topic": event.topic,
                    "payload": event.payload,
                    "timestamp": event.timestamp,
                }
            )
        except Exception:
            break


async def _receive_commands(websocket: WebSocket, session_id: str, bus: EventBus) -> None:
    while True:
        try:
            data = await websocket.receive_text()
            command = json.loads(data)
            cmd_type = command.get("type", "")

            if cmd_type == "start_recording":
                await bus.publish(f"session.{session_id}.control", {"action": "start_recording"})
            elif cmd_type == "stop_recording":
                await bus.publish(f"session.{session_id}.control", {"action": "stop_recording"})
            elif cmd_type == "pause_recording":
                await bus.publish(f"session.{session_id}.control", {"action": "pause_recording"})
        except WebSocketDisconnect:
            break
        except json.JSONDecodeError:
            continue
