from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from .memory_export import MemoryRecord


SYSTEM_PROMPT = (
    "You are a careful research assistant. Answer from the user's question and "
    "the supplied context, and do not invent facts."
)


class DeepSeekError(RuntimeError):
    """A sanitized, stable error from the DeepSeek adapter."""

    def __init__(self, category: str, message: str | None = None) -> None:
        self.category = category
        super().__init__(message or category)


@dataclass(frozen=True)
class DeepSeekConfig:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    timeout_seconds: float = 30.0
    max_retries: int = 2

    @classmethod
    def from_env(cls) -> "DeepSeekConfig":
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise DeepSeekError("missing_api_key", "DeepSeek API key is required")
        base_url = os.getenv("DEEPSEEK_BASE_URL", cls.base_url).strip()
        model = os.getenv("DEEPSEEK_MODEL", cls.model).strip()
        timeout_raw = os.getenv("DEEPSEEK_TIMEOUT_SECONDS", str(cls.timeout_seconds))
        retries_raw = os.getenv("DEEPSEEK_MAX_RETRIES", str(cls.max_retries))
        try:
            timeout_seconds = float(timeout_raw)
            max_retries = int(retries_raw)
        except (TypeError, ValueError) as error:
            raise ValueError("invalid DeepSeek timeout or retry count") from error
        config = cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        config._validate()
        return config

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if not self.api_key.strip():
            raise DeepSeekError("missing_api_key", "DeepSeek API key is required")
        parsed = urlparse(self.base_url)
        try:
            port = parsed.port
        except ValueError:
            port = -1
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.deepseek.com"
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
        ):
            raise ValueError("base_url must use the official DeepSeek HTTPS endpoint")
        if not self.model.strip():
            raise ValueError("model must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")


@dataclass(frozen=True)
class DeepSeekResult:
    content: str
    model: str
    request_id: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class DeepSeekClient:
    def __init__(
        self,
        config: DeepSeekConfig,
        session: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.session = session if session is not None else requests.Session()
        self.sleep = sleep

    def complete(
        self,
        question: str,
        *,
        context: list[MemoryRecord] = (),
        model: str | None = None,
        reasoning_effort: str = "high",
    ) -> DeepSeekResult:
        selected_model = model or self.config.model
        payload = {
            "model": selected_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(question, context)},
            ],
            "thinking": {"type": "enabled"},
            "reasoning_effort": reasoning_effort,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        url = self.config.base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"

        for attempt in range(self.config.max_retries + 1):
            try:
                response = self.session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self.config.timeout_seconds,
                )
            except requests.Timeout as error:
                raise DeepSeekError("provider_unavailable", "DeepSeek request timed out") from error
            except requests.RequestException as error:
                raise DeepSeekError("provider_unavailable", "DeepSeek request failed") from error

            status = response.status_code
            if status == 401 or status == 403:
                raise DeepSeekError("provider_auth", "DeepSeek authentication failed")
            if status == 429:
                if attempt < self.config.max_retries:
                    self.sleep(min(0.5 * 2**attempt, 2.0))
                    continue
                raise DeepSeekError("provider_rate_limit", "DeepSeek rate limit reached")
            if 500 <= status <= 599:
                if attempt < self.config.max_retries:
                    self.sleep(min(0.5 * 2**attempt, 2.0))
                    continue
                raise DeepSeekError("provider_unavailable", "DeepSeek provider unavailable")
            if status < 200 or status >= 300:
                raise DeepSeekError("provider_response_invalid", "DeepSeek request was rejected")
            return _parse_result(response)

        raise DeepSeekError("provider_unavailable", "DeepSeek provider unavailable")


def build_user_prompt(question: str, context: list[MemoryRecord] | tuple[MemoryRecord, ...]) -> str:
    records = []
    for record in context:
        records.append(
            f"[{record.title}]\n{record.body}\nTags: {', '.join(record.tags)}"
        )
    context_text = "\n\n".join(records) or "(no additional context)"
    return f"Question:\n{question}\n\nContext:\n{context_text}"


def _parse_result(response: Any) -> DeepSeekResult:
    try:
        payload = response.json()
    except (TypeError, ValueError) as error:
        raise DeepSeekError("provider_response_invalid", "DeepSeek returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise DeepSeekError("provider_response_invalid", "DeepSeek returned an invalid response")
    try:
        choices = payload["choices"]
        message = choices[0]["message"]
        content = message["content"]
        model = payload["model"]
    except (KeyError, IndexError, TypeError) as error:
        raise DeepSeekError("provider_response_invalid", "DeepSeek response is missing assistant content") from error
    if not isinstance(content, str) or not content.strip() or not isinstance(model, str) or not model:
        raise DeepSeekError("provider_response_invalid", "DeepSeek response is missing assistant content")
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return DeepSeekResult(
        content=content,
        model=model,
        request_id=payload.get("id") if isinstance(payload.get("id"), str) else None,
        prompt_tokens=_token_count(usage.get("prompt_tokens")),
        completion_tokens=_token_count(usage.get("completion_tokens")),
        total_tokens=_token_count(usage.get("total_tokens")),
    )


def _token_count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
