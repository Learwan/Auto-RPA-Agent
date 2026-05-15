import ast
import json
import logging
import re

import httpx
from openai import AsyncOpenAI

from src.config import settings

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self):
        self._client: AsyncOpenAI | None = None
        self._model = settings.LLM_MODEL
        self._temperature = settings.LLM_TEMPERATURE
        self._top_p = settings.LLM_TOP_P
        self._max_tokens = settings.LLM_MAX_TOKENS

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                base_url=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY,
                http_client=httpx.AsyncClient(trust_env=False),
            )
        return self._client

    @property
    def is_configured(self) -> bool:
        return settings.llm_configured

    def _clean_response(self, text: str) -> str:
        text = re.sub(r"<think[^>]*>.*?</think\s*>", "", text, flags=re.DOTALL)
        return text.strip()

    def _extract_json(self, text: str) -> dict | list | None:
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if json_match:
            text = json_match.group(1)
        elif "```" in text:
            text = text.replace("```", "")

        text = text.strip()
        for candidate in [*self._balanced_json_candidates(text), text]:
            parsed = self._try_parse_json_candidate(candidate)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _balanced_json_candidates(text: str) -> list[str]:
        candidates: list[str] = []
        for opener, closer in (("{", "}"), ("[", "]")):
            depth = 0
            start_idx: int | None = None
            in_string = False
            escaped = False

            for idx, char in enumerate(text):
                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue

                if char == '"':
                    in_string = True
                    continue

                if char == opener:
                    if depth == 0:
                        start_idx = idx
                    depth += 1
                    continue

                if char == closer and depth > 0:
                    depth -= 1
                    if depth == 0 and start_idx is not None:
                        candidates.append(text[start_idx : idx + 1])
                        start_idx = None

        return candidates

    @staticmethod
    def _try_parse_json_candidate(candidate: str) -> dict | list | None:
        text = str(candidate or "").strip().lstrip("\ufeff")
        if not text:
            return None

        normalized = text.translate(str.maketrans({
            "“": '"',
            "”": '"',
            "‘": "'",
            "’": "'",
        }))
        normalized = re.sub(r",\s*([}\]])", r"\1", normalized)

        for variant in (text, normalized):
            try:
                parsed = json.loads(variant)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, (dict, list)):
                return parsed

        python_like = re.sub(r"\btrue\b", "True", normalized, flags=re.IGNORECASE)
        python_like = re.sub(r"\bfalse\b", "False", python_like, flags=re.IGNORECASE)
        python_like = re.sub(r"\bnull\b", "None", python_like, flags=re.IGNORECASE)
        try:
            parsed = ast.literal_eval(python_like)
        except (SyntaxError, ValueError):
            return None
        return parsed if isinstance(parsed, (dict, list)) else None

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
    ) -> str:
        if not self.is_configured:
            raise ValueError("LLM is not configured. Set LLM_API_KEY in .env file.")

        response = await self.client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature or self._temperature,
            top_p=top_p or self._top_p,
            max_tokens=max_tokens or self._max_tokens,
            stream=stream,
        )

        if stream:
            return await self._collect_stream(response)

        raw = response.choices[0].message.content or ""
        return self._clean_response(raw)

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
    ):
        if not self.is_configured:
            raise ValueError("LLM is not configured. Set LLM_API_KEY in .env file.")

        return await self.client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature or self._temperature,
            top_p=top_p or self._top_p,
            max_tokens=max_tokens or self._max_tokens,
            stream=True,
        )

    async def _collect_stream(self, stream) -> str:
        collected: list[str] = []
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                collected.append(chunk.choices[0].delta.content)
        return self._clean_response("".join(collected))

    async def analyze_flow(self, flow_description: str, operations_summary: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert automation analyst. Analyze the recorded desktop operations "
                    "and the detected automation flow. Provide:\n"
                    "1. A clear natural-language description of what this automation does\n"
                    "2. Potential risks or failure points\n"
                    "3. Specific improvement suggestions (e.g., adding wait steps, using accessibility IDs instead of coordinates)\n"
                    "4. An estimated reliability assessment\n"
                    "Respond in the same language as the user's input."
                ),
            },
            {
                "role": "user",
                "content": f"Operations Summary:\n{operations_summary}\n\nDetected Flow:\n{flow_description}",
            },
        ]
        return await self.chat(messages)

    async def analyze_flow_artifacts(
        self,
        flow_description: str,
        operations_summary: str,
        steps: list[dict[str, str | int | float | None]],
    ) -> dict:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert desktop automation analyst. Review the recorded operations and detected flow. "
                    "Return strictly valid JSON with this exact shape:\n"
                    "{\n"
                    '  "suggested_name": "short flow name",\n'
                    '  "summary": "short overall explanation",\n'
                    '  "risks": ["risk 1", "risk 2"],\n'
                    '  "improvements": ["improvement 1", "improvement 2"],\n'
                    '  "reliability_assessment": "short reliability assessment",\n'
                    '  "confidence": 0.0,\n'
                    '  "step_analyses": [\n'
                    "    {\n"
                    '      "step_index": 0,\n'
                    '      "intent": "what the user is trying to do",\n'
                    '      "ui_state": "what should be visible or true in UI",\n'
                    '      "risk": "main risk for this step",\n'
                    '      "suggested_next_actions": ["action 1", "action 2"],\n'
                    '      "refined_description": "better step description",\n'
                    '      "recommended_wait_ms": 1200,\n'
                    '      "verification_condition": "element_exists|window_exists|page_stable|none",\n'
                    '      "verification_timeout_ms": 5000,\n'
                    '      "target_text_hint": "better text hint",\n'
                    '      "window_title_hint": "better window title hint",\n'
                    '      "confidence": 0.0\n'
                    "    }\n"
                    "  ]\n"
                    "}\n"
                    "Use the same language as the user's data. Confidence must be between 0 and 1. "
                    "Only output JSON."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "operations_summary": operations_summary,
                        "flow_description": flow_description,
                        "steps": steps,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        raw = await self.chat(messages, max_tokens=1600)
        parsed = self._extract_json(raw)
        if not isinstance(parsed, dict):
            raise ValueError("LLM did not return a valid flow artifact JSON object")
        return parsed

    async def suggest_flow_name(self, flow_description: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "Generate a concise, descriptive name for this automation flow. "
                    "The name should be in the format: 'Verb + Target + Action' (e.g., 'Open Browser and Search', 'Fill Form and Submit'). "
                    "Return ONLY the name, nothing else."
                ),
            },
            {
                "role": "user",
                "content": flow_description,
            },
        ]
        return await self.chat(messages, max_tokens=100)

    async def generate_script_enhancement(self, flow_json: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a Python automation expert. Given an automation flow definition in JSON, "
                    "generate an enhanced Python script using pyautogui that includes:\n"
                    "- Proper error handling with try/except\n"
                    "- Wait/retry logic for unreliable steps\n"
                    "- Screenshot capture on failure\n"
                    "- Logging of each step\n"
                    "- Configurable delays between steps\n"
                    "Return only the Python code, no explanations."
                ),
            },
            {
                "role": "user",
                "content": flow_json,
            },
        ]
        return await self.chat(messages, max_tokens=4096)

    async def explain_operations(self, operations_text: str) -> str:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a desktop automation expert. Given a sequence of recorded desktop operations, "
                    "provide a clear, step-by-step explanation of what the user was doing. "
                    "Use natural language and group related actions together. "
                    "Respond in the same language as the user's input."
                ),
            },
            {
                "role": "user",
                "content": operations_text,
            },
        ]
        return await self.chat(messages)
