from __future__ import annotations

import json
import logging
import re
import time

import httpx
from openai import AsyncOpenAI

from src.config import settings
from src.models.vision import GroundingResult

logger = logging.getLogger(__name__)

_UI_TARS_SYSTEM = (
    "You are a GUI agent. You are given a task and a screenshot. "
    "You need to output the action to take.\n"
    "Output format:\n"
    "Thought: <your reasoning>\n"
    "Action: <action_type> <action_args>\n\n"
    "Supported actions:\n"
    "click(x, y) - Click at coordinates\n"
    "type(text) - Type text\n"
    "scroll(x, y, direction) - Scroll at position\n"
    "hotkey(key) - Press keyboard shortcut\n"
    "wait() - Wait for UI change\n"
)


class UITarsAdapter:
    def __init__(self, model_path: str | None = None, server_url: str | None = None):
        self._model_path = model_path or getattr(settings, "UI_TARS_MODEL_PATH", "")
        self._server_url = server_url or getattr(settings, "UI_TARS_SERVER_URL", "")
        self._loaded = False
        self._driver = None
        self._client: AsyncOpenAI | None = None

    @property
    def _server_enabled(self) -> bool:
        return bool(str(self._server_url or "").strip())

    @property
    def _server_model(self) -> str:
        return self._model_path or "ui-tars"

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                base_url=self._server_url,
                api_key="not-needed",
                http_client=httpx.AsyncClient(trust_env=False),
            )
        return self._client

    async def _ensure_loaded(self):
        if self._loaded:
            return
        from src.llm.local_engine import LocalLLMEngine

        engine = await LocalLLMEngine.get_instance()
        self._driver = engine
        self._loaded = True

    async def _ground_action_server(
        self,
        screenshot_base64: str,
        action_description: str,
        action_type: str,
        image_width: int = 1920,
        image_height: int = 1080,
    ) -> GroundingResult:
        prompt = (
            f"Task: Find the UI element for the following action.\n"
            f"Action type: {action_type}\n"
            f"Action description: {action_description}\n\n"
            f"Locate the target element and provide its bounding box."
        )

        response = await self.client.chat.completions.create(
            model=self._server_model,
            messages=[
                {"role": "system", "content": _UI_TARS_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{screenshot_base64}"},
                        },
                    ],
                },
            ],
            temperature=0.2,
            max_tokens=400,
            stream=False,
        )
        text = response.choices[0].message.content or ""
        return self._parse_ui_tars_result(text, action_type, action_description, image_width, image_height)

    async def _ground_action_local(
        self,
        screenshot_base64: str,
        action_description: str,
        action_type: str,
        image_width: int = 1920,
        image_height: int = 1080,
    ) -> GroundingResult:
        await self._ensure_loaded()

        prompt = (
            f"Task: Find the UI element for the following action.\n"
            f"Action type: {action_type}\n"
            f"Action description: {action_description}\n\n"
            f"Locate the target element and provide its bounding box."
        )

        result = await self._driver.analyze_with_image(
            prompt=prompt,
            image_base64=screenshot_base64,
        )
        return self._parse_ui_tars_result(result.text, action_type, action_description, image_width, image_height)

    async def ground_action(
        self,
        screenshot_base64: str,
        action_description: str,
        action_type: str,
        image_width: int = 1920,
        image_height: int = 1080,
    ) -> GroundingResult:
        t0 = time.perf_counter()
        last_error: Exception | None = None

        if self._server_enabled:
            try:
                result = await self._ground_action_server(
                    screenshot_base64,
                    action_description,
                    action_type,
                    image_width,
                    image_height,
                )
                latency_ms = (time.perf_counter() - t0) * 1000
                logger.info("ui_tars remote ground_action latency=%.0fms type=%s", latency_ms, action_type)
                return result
            except Exception as e:
                last_error = e
                logger.warning("UI-TARS remote grounding failed, falling back to local runtime: %s", e)

        try:
            result = await self._ground_action_local(
                screenshot_base64,
                action_description,
                action_type,
                image_width,
                image_height,
            )
        except Exception as e:
            logger.warning("UI-TARS grounding failed: %s", e)
            error_text = str(last_error or e)
            return GroundingResult(
                found=False,
                element_type=action_type,
                element_label=action_description,
                bbox_pixel=None,
                bbox_normalized=None,
                confidence=0.0,
                reasoning=f"UI-TARS error: {error_text}",
            )

        latency_ms = (time.perf_counter() - t0) * 1000
        logger.info("ui_tars ground_action latency=%.0fms type=%s", latency_ms, action_type)
        return result

    async def ground_batch(
        self,
        screenshot_base64: str,
        actions: list[dict],
        image_width: int = 1920,
        image_height: int = 1080,
    ) -> list[GroundingResult]:
        results = []
        for action in actions:
            gr = await self.ground_action(
                screenshot_base64=screenshot_base64,
                action_description=action.get("description", ""),
                action_type=action.get("type", "unknown"),
                image_width=image_width,
                image_height=image_height,
            )
            results.append(gr)
        return results

    def _parse_ui_tars_result(
        self,
        text: str,
        action_type: str,
        action_description: str,
        image_width: int,
        image_height: int,
    ) -> GroundingResult:
        click_match = re.search(r"click\((\d+)\s*,\s*(\d+)\)", text)
        if click_match:
            x, y = int(click_match.group(1)), int(click_match.group(2))
            bbox_pixel = (x, y, 1, 1)
            bbox_normalized = (
                int(x * 1000 / image_width),
                int(y * 1000 / image_height),
                1,
                1,
            )
            return GroundingResult(
                found=True,
                element_type=action_type,
                element_label=action_description,
                bbox_pixel=bbox_pixel,
                bbox_normalized=bbox_normalized,
                confidence=0.7,
                reasoning=text[:200],
            )

        json_data = self._extract_json(text)
        if json_data and isinstance(json_data, dict):
            found = bool(json_data.get("found", False))
            bbox_raw = json_data.get("bbox")
            bbox_pixel = None
            bbox_normalized = None
            confidence = float(json_data.get("confidence", 0.5))

            if found and isinstance(bbox_raw, list) and len(bbox_raw) == 4:
                bbox_normalized = tuple(bbox_raw)
                x_c, y_c, w_n, h_n = bbox_raw
                bbox_pixel = (
                    int(x_c * image_width / 1000),
                    int(y_c * image_height / 1000),
                    int(w_n * image_width / 1000),
                    int(h_n * image_height / 1000),
                )
            else:
                confidence = 0.0

            return GroundingResult(
                found=found and confidence >= 0.5,
                element_type=json_data.get("element_type", action_type),
                element_label=json_data.get("element_label", action_description),
                bbox_pixel=bbox_pixel,
                bbox_normalized=bbox_normalized,
                confidence=confidence,
                reasoning=json_data.get("reasoning", text[:200]),
            )

        coord_match = re.search(r"\((\d+)\s*,\s*(\d+)\)", text)
        if coord_match:
            x, y = int(coord_match.group(1)), int(coord_match.group(2))
            return GroundingResult(
                found=True,
                element_type=action_type,
                element_label=action_description,
                bbox_pixel=(x, y, 1, 1),
                bbox_normalized=(int(x * 1000 / image_width), int(y * 1000 / image_height), 1, 1),
                confidence=0.4,
                reasoning=text[:200],
            )

        return GroundingResult(
            found=False,
            element_type=action_type,
            element_label=action_description,
            bbox_pixel=None,
            bbox_normalized=None,
            confidence=0.0,
            reasoning=text[:200],
        )

    @staticmethod
    def _extract_json(text: str) -> dict | list | None:
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if json_match:
            text = json_match.group(1)
        text = text.strip()
        for start, end in [("[", "]"), ("{", "}")]:
            idx_start = text.find(start)
            idx_end = text.rfind(end)
            if idx_start != -1 and idx_end > idx_start:
                try:
                    return json.loads(text[idx_start : idx_end + 1])
                except json.JSONDecodeError:
                    continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
