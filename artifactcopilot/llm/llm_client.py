from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests

from .utils import ensure_dir, now_iso


DEEPSEEK_V4_MAX_OUTPUT_TOKENS = 384_000
DEEPSEEK_V4_MODEL_NAMES = {
    "deepseek-v4-flash",
    "deepseek-v4-pro",
}


def is_deepseek_v4_model(model_name: str) -> bool:
    return str(model_name or "").strip().lower().replace("_", "-") in DEEPSEEK_V4_MODEL_NAMES


@dataclass
class PromptBundle:
    system: str
    user: str
    output_format: str = "json"
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMRuntimeConfig:
    api_key: Optional[str]
    base_url: str
    model: str
    timeout_seconds: int
    connect_timeout_seconds: int
    read_timeout_seconds: int | None
    max_output_chars: int
    max_retries: int
    retry_backoff_seconds: float
    reset_session_on_retry: bool
    force_connection_close: bool
    thinking_type: str
    reasoning_effort: str
    temperature: float | None

    @property
    def ready(self) -> bool:
        return bool(self.api_key and self.api_key.strip())


class DeepSeekLLMClient:
    def __init__(self, config: LLMRuntimeConfig):
        self.config = config
        self.monitor_log_path: Path | None = None
        self.session = self._build_session()

    @classmethod
    def from_env(cls) -> "DeepSeekLLMClient":
        timeout_seconds = _env_timeout_seconds("LLM_TIMEOUT_SECONDS", default=3600)
        read_timeout_seconds = _env_timeout_seconds("LLM_READ_TIMEOUT_SECONDS", default=0)
        default_connect_timeout = min(timeout_seconds, 15) if timeout_seconds > 0 else 15
        config = LLMRuntimeConfig(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=int(os.getenv("LLM_CONNECT_TIMEOUT_SECONDS", str(default_connect_timeout))),
            read_timeout_seconds=read_timeout_seconds or None,
            max_output_chars=int(os.getenv("LLM_MAX_OUTPUT_CHARS", "200000")),
            max_retries=int(os.getenv("LLM_MAX_RETRIES", "3")),
            retry_backoff_seconds=float(os.getenv("LLM_RETRY_BACKOFF_SECONDS", "3")),
            reset_session_on_retry=os.getenv("LLM_RESET_SESSION_ON_RETRY", "1").strip().lower() not in {"0", "false", "no"},
            force_connection_close=os.getenv("LLM_FORCE_CONNECTION_CLOSE", "1").strip().lower() not in {"0", "false", "no"},
            thinking_type=os.getenv("DEEPSEEK_THINKING_TYPE", "disabled").strip().lower(),
            reasoning_effort=os.getenv("DEEPSEEK_REASONING_EFFORT", "").strip().lower(),
            temperature=_env_float_or_none("DEEPSEEK_TEMPERATURE"),
        )
        return cls(config)

    def is_ready(self) -> bool:
        return self.config.ready

    def set_monitor_log_path(self, path: Path | None) -> None:
        self.monitor_log_path = path

    def chat_json(
        self,
        prompt: PromptBundle,
        *,
        max_tokens: int = 2048,
        connect_timeout_seconds: int | None = None,
        read_timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        if not self.is_ready():
            raise RuntimeError("DeepSeek API key is not configured.")

        messages = self._make_messages(prompt)
        meta = dict(prompt.meta or {})
        effective_connect_timeout = (
            max(1, int(connect_timeout_seconds))
            if connect_timeout_seconds is not None
            else self.config.connect_timeout_seconds
        )
        effective_read_timeout = (
            max(1, int(read_timeout_seconds))
            if read_timeout_seconds is not None
            else self.config.read_timeout_seconds
        )
        monitor_payload = {
            "timestamp": now_iso(),
            "task": str(meta.get("task", "")).strip(),
            "label": str(meta.get("label", "")).strip(),
            "meta": meta,
            "model": self.config.model,
            "system_chars": len(prompt.system or ""),
            "user_chars": len(prompt.user or ""),
            "total_input_chars": len(prompt.system or "") + len(prompt.user or ""),
            "estimated_input_tokens": self._estimate_token_count((prompt.system or "") + "\n" + (prompt.user or "")),
            "requested_max_output_tokens": int(max_tokens),
            "connect_timeout_seconds": effective_connect_timeout,
            "read_timeout_seconds": effective_read_timeout,
            "force_connection_close": self.config.force_connection_close,
            "temperature": self.config.temperature,
        }
        payload = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if self.config.temperature is not None:
            payload["temperature"] = float(self.config.temperature)
        if self.config.thinking_type in {"enabled", "disabled"}:
            payload["thinking"] = {"type": self.config.thinking_type}
        if self.config.thinking_type == "enabled" and self.config.reasoning_effort in {"high", "max"}:
            payload["reasoning_effort"] = self.config.reasoning_effort

        total_attempts = max(1, int(self.config.max_retries) + 1)
        for attempt_index in range(1, total_attempts + 1):
            started_at = time.monotonic()
            response = None
            data: dict[str, Any] = {}
            try:
                response = self.session.post(
                    f"{self.config.base_url}/chat/completions",
                    json=payload,
                    timeout=self._request_timeout(
                        connect_timeout_seconds=effective_connect_timeout,
                        read_timeout_seconds=effective_read_timeout,
                    ),
                )
                response.raise_for_status()
                data = response.json()
                content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
                if not content:
                    raise RuntimeError("LLM returned empty content.")
                obj = self._parse_json(content)
                usage = data.get("usage", {}) if isinstance(data.get("usage", {}), dict) else {}
                if isinstance(obj, dict):
                    obj["usage"] = usage
                self._append_monitor_event(
                    {
                        **monitor_payload,
                        "status": "ok",
                        "attempt_index": attempt_index,
                        "attempt_count": total_attempts,
                        "latency_sec": round(time.monotonic() - started_at, 3),
                        "http_status": int(getattr(response, "status_code", 0) or 0),
                        "response_chars": len(content),
                        "estimated_output_tokens": self._estimate_token_count(content),
                        "api_prompt_tokens": usage.get("prompt_tokens"),
                        "api_completion_tokens": usage.get("completion_tokens"),
                        "api_total_tokens": usage.get("total_tokens"),
                        "api_prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens"),
                        "api_prompt_cache_miss_tokens": usage.get("prompt_cache_miss_tokens"),
                    }
                )
                return obj
            except Exception as exc:
                should_retry = attempt_index < total_attempts and _should_retry_llm_exception(
                    exc,
                    status_code=int(getattr(response, "status_code", 0) or 0) if response is not None else 0,
                )
                self._append_monitor_event(
                    {
                        **monitor_payload,
                        "status": "retrying" if should_retry else "error",
                        "attempt_index": attempt_index,
                        "attempt_count": total_attempts,
                        "latency_sec": round(time.monotonic() - started_at, 3),
                        "http_status": int(getattr(response, "status_code", 0) or 0) if response is not None else 0,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                if not should_retry:
                    raise
                if self.config.reset_session_on_retry:
                    self._reset_session()
                time.sleep(self.config.retry_backoff_seconds * max(1, 2 ** (attempt_index - 1)))
            finally:
                if response is not None:
                    self._safe_close(response)

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        if self.config.force_connection_close:
            headers["Connection"] = "close"
        session.headers.update(headers)
        return session

    def _reset_session(self) -> None:
        self._safe_close(self.session)
        self.session = self._build_session()

    def _request_timeout(
        self,
        *,
        connect_timeout_seconds: int | None = None,
        read_timeout_seconds: int | None = None,
    ) -> tuple[int, int | None]:
        connect_timeout = max(
            1,
            int(connect_timeout_seconds or self.config.connect_timeout_seconds or self.config.timeout_seconds or 60),
        )
        read_timeout = read_timeout_seconds if read_timeout_seconds is not None else self.config.read_timeout_seconds
        if read_timeout is None:
            return (connect_timeout, None)
        return (connect_timeout, max(1, int(read_timeout)))

    def _safe_close(self, obj: Any) -> None:
        close = getattr(obj, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                return

    def _make_messages(self, prompt: PromptBundle) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if prompt.system.strip():
            messages.append({"role": "system", "content": prompt.system.strip()})
        messages.append({"role": "user", "content": prompt.user.strip()})
        return messages

    def _append_monitor_event(self, event: dict[str, Any]) -> None:
        if self.monitor_log_path is None:
            return
        ensure_dir(self.monitor_log_path.parent)
        with self.monitor_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _estimate_token_count(self, text: str) -> int:
        compact = str(text or "")
        if not compact:
            return 0
        return max(1, (len(compact) + 3) // 4)

    def _parse_json(self, text: str) -> dict[str, Any]:
        try:
            return json.loads(text)
        except Exception:
            candidate = self._extract_json_object(text)
            try:
                return json.loads(candidate)
            except Exception:
                repaired = self._repair_incomplete_json(candidate)
                repaired = self._repair_common_json_issues(repaired)
                return json.loads(repaired)

    def _extract_json_object(self, text: str) -> str:
        fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
        if fenced:
            for block in fenced:
                block = block.strip()
                if block.startswith("{") and block.endswith("}"):
                    return block
        start = text.find("{")
        if start == -1:
            raise RuntimeError("No JSON object found in LLM response.")
        depth = 0
        for index in range(start, len(text)):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start:index + 1]
        return text[start:].strip()

    def _repair_incomplete_json(self, text: str) -> str:
        repaired = text.strip()
        stack: list[str] = []
        in_string = False
        escaped = False
        for char in repaired:
            if in_string:
                if escaped:
                    escaped = False
                    continue
                if char == "\\":
                    escaped = True
                    continue
                if char == "\"":
                    in_string = False
                continue
            if char == "\"":
                in_string = True
            elif char == "{":
                stack.append("}")
            elif char == "[":
                stack.append("]")
            elif char in {"}", "]"} and stack and stack[-1] == char:
                stack.pop()
        if in_string:
            repaired += "\""
        while stack:
            repaired += stack.pop()
        return repaired

    def _repair_common_json_issues(self, text: str) -> str:
        repaired = str(text or "").strip()
        if not repaired:
            return repaired
        repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
        repaired = re.sub(r'("(?:(?:\\.)|[^"\\])*")\s+("(?:(?:\\.)|[^"\\])*"\s*:)', r"\1, \2", repaired)
        repaired = re.sub(r'([}\]0-9truefalsenull"])\s+("(?:(?:\\.)|[^"\\])*"\s*:)', r"\1, \2", repaired)
        repaired = re.sub(r'([}\]0-9"eE])(\s*\n\s*)(")', r"\1,\2\3", repaired)
        return repaired


def _should_retry_llm_exception(exc: Exception, *, status_code: int) -> bool:
    if status_code == 400:
        return False
    if status_code in {408, 409, 429, 500, 502, 503, 504}:
        return True
    if isinstance(exc, requests.HTTPError):
        return False
    if isinstance(exc, requests.RequestException):
        return True
    if isinstance(exc, json.JSONDecodeError):
        return True
    if isinstance(exc, RuntimeError):
        lowered = str(exc).lower()
        return "empty content" in lowered or "no json object" in lowered
    return False


def _env_float_or_none(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _env_timeout_seconds(name: str, *, default: int) -> int:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return int(default)
    if raw in {"0", "none", "no", "false", "off", "unlimited", "disable", "disabled"}:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return int(default)
