import pytest
import requests

from stock_monitor.deepseek_harness import (
    DeepSeekClient,
    DeepSeekConfig,
    DeepSeekError,
)
from stock_monitor.memory_export import MemoryRecord


class FakeResponse:
    def __init__(self, status_code, payload=None, *, json_error=False):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("provider body: secret")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def post(self, url, *, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return next(self.responses)


def memory_record():
    return MemoryRecord(
        id="mem-1",
        title="Data source decision",
        kind="context",
        body="Use licensed data.",
        source_harness="codex",
        target_harness="all",
        tags=("data",),
        created_at="2026-08-20T00:00:00Z",
        sha256="fixture",
    )


def test_complete_builds_bounded_deepseek_request():
    fake = FakeSession([FakeResponse(200, {
        "id": "req-1",
        "model": "deepseek-v4-pro",
        "choices": [{"message": {"role": "assistant", "content": "answer"}}],
    })])
    client = DeepSeekClient(
        DeepSeekConfig(api_key="secret", max_retries=0),
        session=fake,
        sleep=lambda _: None,
    )

    result = client.complete(
        "What changed?",
        context=[memory_record()],
    )

    assert result.content == "answer"
    assert result.model == "deepseek-v4-pro"
    assert result.request_id == "req-1"
    assert fake.calls[0]["url"] == "https://api.deepseek.com/chat/completions"
    assert fake.calls[0]["headers"] == {
        "Authorization": "Bearer secret",
        "Content-Type": "application/json",
    }
    assert fake.calls[0]["timeout"] == 30.0
    assert fake.calls[0]["json"]["thinking"] == {"type": "enabled"}
    assert fake.calls[0]["json"]["reasoning_effort"] == "high"
    assert fake.calls[0]["json"]["stream"] is False
    assert "Use licensed data." in fake.calls[0]["json"]["messages"][-1]["content"]


def test_complete_selects_model_and_reasoning_effort():
    fake = FakeSession([FakeResponse(200, {
        "id": "req-2",
        "model": "deepseek-v4-flash",
        "choices": [{"message": {"content": "ok"}}],
    })])

    result = DeepSeekClient(
        DeepSeekConfig(api_key="secret", max_retries=0),
        session=fake,
        sleep=lambda _: None,
    ).complete("question", model="deepseek-v4-flash", reasoning_effort="low")

    assert result.content == "ok"
    assert fake.calls[0]["json"]["model"] == "deepseek-v4-flash"
    assert fake.calls[0]["json"]["reasoning_effort"] == "low"


def test_transient_failures_retry_with_bounded_backoff():
    fake = FakeSession([
        FakeResponse(429, {"error": {"message": "token=secret"}}),
        FakeResponse(500, {"error": {"message": "prompt text"}}),
        FakeResponse(200, {"id": "req-3", "model": "deepseek-v4-pro",
                           "choices": [{"message": {"content": "ok"}}]}),
    ])
    sleeps = []

    result = DeepSeekClient(
        DeepSeekConfig(api_key="secret", max_retries=2),
        fake,
        sleeps.append,
    ).complete("private question")

    assert result.content == "ok"
    assert len(fake.calls) == 3
    assert sleeps == [0.5, 1.0]


def test_auth_failure_is_sanitized_and_not_retried():
    fake = FakeSession([FakeResponse(401, {"error": {"message": "secret"}})])

    with pytest.raises(DeepSeekError) as caught:
        DeepSeekClient(DeepSeekConfig(api_key="secret", max_retries=2), fake, lambda _: None).complete(
            "private question"
        )

    assert caught.value.category == "provider_auth"
    assert "secret" not in str(caught.value)
    assert "private question" not in str(caught.value)
    assert len(fake.calls) == 1


def test_rate_limit_after_retries_is_sanitized():
    fake = FakeSession([FakeResponse(429, {"error": {"message": "secret body"}})] * 3)

    with pytest.raises(DeepSeekError) as caught:
        DeepSeekClient(DeepSeekConfig(api_key="secret", max_retries=2), fake, lambda _: None).complete(
            "private question"
        )

    assert caught.value.category == "provider_rate_limit"
    assert "secret body" not in str(caught.value)
    assert "private question" not in str(caught.value)
    assert len(fake.calls) == 3


def test_timeout_maps_to_provider_unavailable_without_leaking_prompt():
    class TimeoutSession:
        def post(self, *args, **kwargs):
            raise requests.Timeout("secret provider detail")

    with pytest.raises(DeepSeekError) as caught:
        DeepSeekClient(DeepSeekConfig(api_key="secret"), TimeoutSession(), lambda _: None).complete(
            "private question"
        )

    assert caught.value.category == "provider_unavailable"
    assert "secret provider detail" not in str(caught.value)
    assert "private question" not in str(caught.value)


@pytest.mark.parametrize("response", [
    FakeResponse(200, {}, json_error=True),
    FakeResponse(200, {"id": "req", "choices": []}),
    FakeResponse(200, {"id": "req", "choices": [{"message": {"content": ""}}]}),
])
def test_invalid_success_response_is_sanitized(response):
    fake = FakeSession([response])

    with pytest.raises(DeepSeekError) as caught:
        DeepSeekClient(DeepSeekConfig(api_key="secret"), fake, lambda _: None).complete(
            "private question"
        )

    assert caught.value.category == "provider_response_invalid"
    assert "private question" not in str(caught.value)


def test_missing_api_key_is_rejected_without_request(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(DeepSeekError) as caught:
        DeepSeekConfig.from_env()

    assert caught.value.category == "missing_api_key"


def test_empty_config_api_key_is_rejected():
    with pytest.raises(DeepSeekError) as caught:
        DeepSeekConfig(api_key=" ")

    assert caught.value.category == "missing_api_key"


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.deepseek.com",
        "https://example.com",
        "https://api.deepseek.com:8443",
        "https://user@api.deepseek.com",
    ],
)
def test_from_env_rejects_non_official_deepseek_base_urls(monkeypatch, base_url):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", base_url)

    with pytest.raises(ValueError, match="official DeepSeek HTTPS endpoint"):
        DeepSeekConfig.from_env()


@pytest.mark.parametrize(("name", "value"), [
    ("DEEPSEEK_BASE_URL", "not-a-url"),
    ("DEEPSEEK_MODEL", " "),
    ("DEEPSEEK_TIMEOUT_SECONDS", "0"),
    ("DEEPSEEK_MAX_RETRIES", "-1"),
])
def test_from_env_rejects_invalid_configuration(monkeypatch, name, value):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        DeepSeekConfig.from_env()
