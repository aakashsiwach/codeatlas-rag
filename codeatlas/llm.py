"""LLM providers behind one small interface: Anthropic API, OpenAI(-compatible) and AWS Bedrock."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from codeatlas.config import Settings


class LLMNotConfiguredError(RuntimeError):
    pass


# Provider failures, normalised so callers can tell a throttle from a missing permission.
_ERROR_KINDS = (
    ("throttled", ("Throttling", "TooManyRequests", "RateLimit", "ServiceQuotaExceeded")),
    ("access", ("AccessDenied", "Unrecognized", "ExpiredToken", "NoCredentials", "Authentication",
                "PermissionDenied", "InvalidSignature", "Credential", "Unauthorized")),
    ("timeout", ("Timeout",)),
    ("network", ("Connection", "EndpointConnection")),
    ("unavailable", ("ServiceUnavailable", "InternalServer", "Overloaded", "ModelNotReady")),
    ("validation", ("Validation", "BadRequest", "NotFound", "UnprocessableEntity", "ResourceNotFound")),
)
_PROVIDER_MODULES = ("botocore", "boto3", "anthropic", "openai", "httpx", "httpx2", "urllib3", "requests")


class LLMError(RuntimeError):
    """A provider call failed. kind: throttled | access | timeout | network | unavailable | validation | other."""

    def __init__(self, kind: str, provider: str, error_class: str, message: str):
        super().__init__(f"{provider}: {kind} ({error_class}): {message}")
        self.kind = kind
        self.provider = provider
        self.error_class = error_class
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "provider": self.provider, "error_class": self.error_class, "message": self.message}


def _is_provider_error(exc: BaseException) -> bool:
    return any(type(c).__module__.split(".")[0] in _PROVIDER_MODULES for c in (exc, exc.__cause__) if c)


def as_llm_error(provider: str, exc: BaseException) -> LLMError:
    """Map an SDK exception (boto3 ClientError codes included) to an LLMError."""
    response = getattr(exc, "response", None)
    code = response.get("Error", {}).get("Code", "") if isinstance(response, dict) else ""
    error_class = code or type(exc).__name__
    message = (response.get("Error", {}).get("Message") if code else None) or str(exc)
    for kind, markers in _ERROR_KINDS:
        if any(m in error_class for m in markers):
            return LLMError(kind, provider, error_class, message)
    return LLMError("other", provider, error_class, message)


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str | None = None  # set on the final item of a stream


@dataclass
class LLMResponse:
    text: str
    usage: LLMUsage
    stop_reason: str | None = None  # normalised: "end_turn", "max_tokens", or the provider's value


def price_key(settings: Settings) -> str:
    return f"{settings.llm_provider.lower()}:{settings.llm_model}"


def llm_cost_usd(settings: Settings, input_tokens: int, output_tokens: int) -> float | None:
    """Cost of a call at the configured price for this provider and model; None when unpriced."""
    entry = settings.llm_prices.get(price_key(settings))
    if not entry:
        return None
    try:
        price_in, price_out = float(entry["input_per_mtok"]), float(entry["output_per_mtok"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"CODEATLAS_LLM_PRICES[{price_key(settings)!r}] needs numeric input_per_mtok and output_per_mtok"
        ) from exc
    return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 6)


def is_truncated(stop_reason: str | None) -> bool:
    return stop_reason == "max_tokens"


def thinking_for(settings: Settings, role: str) -> str | None:
    """Thinking type to send for a role ("answer" or "judge") with the configured model.

    CODEATLAS_THINKING_BY_MODEL (per model id, per role) wins over the global setting
    (CODEATLAS_ANSWER_THINKING / CODEATLAS_JUDGE_THINKING). Empty means "send nothing, use the
    model's default"."""
    per_model = settings.thinking_by_model.get(settings.llm_model or "", {})
    value = per_model.get(role) if role in per_model else getattr(settings, f"{role}_thinking")
    return value or None


class LLM(Protocol):
    name: str

    def complete(self, system: str, prompt: str, max_tokens: int, thinking: str | None = None) -> LLMResponse:
        """thinking: None keeps the model's default; otherwise sent as the thinking type (e.g. "disabled")."""
        ...

    def stream(
        self, system: str, prompt: str, max_tokens: int, thinking: str | None = None
    ) -> Iterator[str | LLMUsage]:
        """Yield text deltas, then a final LLMUsage carrying the stop reason."""
        ...


class _Provider:
    """Shared wrapper: SDK exceptions become LLMError; programming errors pass through unchanged."""

    name: str

    def complete(self, system: str, prompt: str, max_tokens: int, thinking: str | None = None) -> LLMResponse:
        try:
            return self._complete(system, prompt, max_tokens, thinking)
        except Exception as exc:
            if not _is_provider_error(exc):
                raise
            raise as_llm_error(self.name, exc) from exc

    def stream(
        self, system: str, prompt: str, max_tokens: int, thinking: str | None = None
    ) -> Iterator[str | LLMUsage]:
        try:
            yield from self._stream(system, prompt, max_tokens, thinking)
        except Exception as exc:
            if not _is_provider_error(exc):
                raise
            raise as_llm_error(self.name, exc) from exc

    def _complete(self, system: str, prompt: str, max_tokens: int, thinking: str | None) -> LLMResponse:
        raise NotImplementedError

    def _stream(self, system: str, prompt: str, max_tokens: int, thinking: str | None) -> Iterator[str | LLMUsage]:
        raise NotImplementedError


class AnthropicLLM(_Provider):
    def __init__(self, model: str, timeout: float = 120.0, max_retries: int = 4):
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError("Install the anthropic extra: pip install 'codeatlas[anthropic]'") from exc
        self._client = anthropic.Anthropic(timeout=timeout, max_retries=max_retries)
        self.model = model
        self.name = f"anthropic:{model}"

    def _complete(self, system: str, prompt: str, max_tokens: int, thinking: str | None = None) -> LLMResponse:
        extra = {"thinking": {"type": thinking}} if thinking else {}
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            **extra,
        )
        text = "".join(block.text for block in msg.content if block.type == "text")
        return LLMResponse(text, LLMUsage(msg.usage.input_tokens, msg.usage.output_tokens), msg.stop_reason)

    def _stream(
        self, system: str, prompt: str, max_tokens: int, thinking: str | None = None
    ) -> Iterator[str | LLMUsage]:
        extra = {"thinking": {"type": thinking}} if thinking else {}
        with self._client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            **extra,
        ) as stream:
            yield from stream.text_stream
            final = stream.get_final_message()
        yield LLMUsage(final.usage.input_tokens, final.usage.output_tokens, final.stop_reason)


class OpenAILLM(_Provider):
    def __init__(self, model: str, base_url: str | None = None, timeout: float = 120.0, max_retries: int = 4):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError("Install the openai extra: pip install 'codeatlas[openai]'") from exc
        self._client = OpenAI(base_url=base_url, timeout=timeout, max_retries=max_retries)
        self.model = model
        self.name = f"openai:{model}"

    @staticmethod
    def _messages(system: str, prompt: str) -> list[dict[str, str]]:
        return [{"role": "system", "content": system}, {"role": "user", "content": prompt}]

    def _complete(self, system: str, prompt: str, max_tokens: int, thinking: str | None = None) -> LLMResponse:
        # thinking is not part of the OpenAI-compatible chat API; it is ignored here.
        resp = self._client.chat.completions.create(model=self.model, messages=self._messages(system, prompt))
        usage = resp.usage
        finish = resp.choices[0].finish_reason
        return LLMResponse(
            resp.choices[0].message.content or "",
            LLMUsage(usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0),
            {"length": "max_tokens", "stop": "end_turn"}.get(finish, finish),
        )

    def _stream(
        self, system: str, prompt: str, max_tokens: int, thinking: str | None = None
    ) -> Iterator[str | LLMUsage]:
        usage = LLMUsage()
        finish: str | None = None
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=self._messages(system, prompt),
            stream=True,
            stream_options={"include_usage": True},
        )
        for event in stream:
            if event.usage:
                usage = LLMUsage(event.usage.prompt_tokens, event.usage.completion_tokens)
            if event.choices and event.choices[0].finish_reason:
                finish = event.choices[0].finish_reason
            if event.choices and event.choices[0].delta.content:
                yield event.choices[0].delta.content
        usage.stop_reason = {"length": "max_tokens", "stop": "end_turn"}.get(finish, finish) if finish else None
        yield usage


class BedrockLLM(_Provider):
    """AWS Bedrock through the model-agnostic Converse API."""

    def __init__(self, model: str, region: str | None = None, timeout: float = 120.0, max_retries: int = 4):
        try:
            import boto3
        except ImportError as exc:
            raise ImportError("Install the bedrock extra: pip install 'codeatlas[bedrock]'") from exc
        from botocore.config import Config

        # Explicit, not botocore defaults: adaptive retries back off on throttling; generation can take
        # longer than the default 60 s read timeout.
        config = Config(
            retries={"mode": "adaptive", "total_max_attempts": max_retries + 1}, read_timeout=timeout, connect_timeout=10
        )
        self._client = boto3.client("bedrock-runtime", region_name=region, config=config)
        self.model = model
        self.name = f"bedrock:{model}"

    def _request(self, system: str, prompt: str, max_tokens: int, thinking: str | None = None) -> dict:
        request = {
            "modelId": self.model,
            "system": [{"text": system}],
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": max_tokens},
        }
        if thinking:  # model-specific request fields pass through Converse unchanged
            request["additionalModelRequestFields"] = {"thinking": {"type": thinking}}
        return request

    def _complete(self, system: str, prompt: str, max_tokens: int, thinking: str | None = None) -> LLMResponse:
        resp = self._client.converse(**self._request(system, prompt, max_tokens, thinking))
        text = "".join(part.get("text", "") for part in resp["output"]["message"]["content"])
        usage = resp.get("usage", {})
        return LLMResponse(
            text, LLMUsage(usage.get("inputTokens", 0), usage.get("outputTokens", 0)), resp.get("stopReason")
        )

    def _stream(
        self, system: str, prompt: str, max_tokens: int, thinking: str | None = None
    ) -> Iterator[str | LLMUsage]:
        resp = self._client.converse_stream(**self._request(system, prompt, max_tokens, thinking))
        usage = LLMUsage()
        stop_reason: str | None = None
        for event in resp["stream"]:
            if "contentBlockDelta" in event:
                text = event["contentBlockDelta"]["delta"].get("text")
                if text:
                    yield text
            elif "messageStop" in event:
                stop_reason = event["messageStop"].get("stopReason")
            elif "metadata" in event:
                u = event["metadata"].get("usage", {})
                usage = LLMUsage(u.get("inputTokens", 0), u.get("outputTokens", 0))
        usage.stop_reason = stop_reason
        yield usage


def build_llm(settings: Settings) -> LLM:
    provider = settings.llm_provider.lower()
    if provider == "none":
        raise LLMNotConfiguredError("LLM provider is 'none'; set CODEATLAS_LLM_PROVIDER to generate answers.")
    if not settings.llm_model:
        raise LLMNotConfiguredError("Set CODEATLAS_LLM_MODEL to the model id to use for answers.")
    if provider == "anthropic":
        return AnthropicLLM(settings.llm_model, settings.llm_timeout_s, settings.llm_max_retries)
    if provider == "openai":
        return OpenAILLM(settings.llm_model, settings.llm_base_url, settings.llm_timeout_s, settings.llm_max_retries)
    if provider == "bedrock":
        return BedrockLLM(settings.llm_model, settings.aws_region, settings.llm_timeout_s, settings.llm_max_retries)
    raise LLMNotConfiguredError(f"Unknown LLM provider: {settings.llm_provider!r}")
