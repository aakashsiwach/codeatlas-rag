"""LLM providers behind one small interface: Anthropic API, OpenAI(-compatible) and AWS Bedrock."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from codeatlas.config import Settings


class LLMNotConfiguredError(RuntimeError):
    pass


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMResponse:
    text: str
    usage: LLMUsage
    stop_reason: str | None = None  # normalised: "end_turn", "max_tokens", or the provider's value


class LLM(Protocol):
    name: str

    def complete(self, system: str, prompt: str, max_tokens: int, thinking: str | None = None) -> LLMResponse:
        """thinking: None keeps the model's default; otherwise sent as the thinking type (e.g. "disabled")."""
        ...

    def stream(self, system: str, prompt: str, max_tokens: int) -> Iterator[str | LLMUsage]:
        """Yield text deltas, then a final LLMUsage."""
        ...


class AnthropicLLM:
    def __init__(self, model: str):
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError("Install the anthropic extra: pip install 'codeatlas[anthropic]'") from exc
        self._client = anthropic.Anthropic()
        self.model = model
        self.name = f"anthropic:{model}"

    def complete(self, system: str, prompt: str, max_tokens: int, thinking: str | None = None) -> LLMResponse:
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

    def stream(self, system: str, prompt: str, max_tokens: int) -> Iterator[str | LLMUsage]:
        with self._client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            yield from stream.text_stream
            final = stream.get_final_message()
        yield LLMUsage(final.usage.input_tokens, final.usage.output_tokens)


class OpenAILLM:
    def __init__(self, model: str, base_url: str | None = None):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError("Install the openai extra: pip install 'codeatlas[openai]'") from exc
        self._client = OpenAI(base_url=base_url) if base_url else OpenAI()
        self.model = model
        self.name = f"openai:{model}"

    @staticmethod
    def _messages(system: str, prompt: str) -> list[dict[str, str]]:
        return [{"role": "system", "content": system}, {"role": "user", "content": prompt}]

    def complete(self, system: str, prompt: str, max_tokens: int, thinking: str | None = None) -> LLMResponse:
        # thinking is not part of the OpenAI-compatible chat API; it is ignored here.
        resp = self._client.chat.completions.create(model=self.model, messages=self._messages(system, prompt))
        usage = resp.usage
        finish = resp.choices[0].finish_reason
        return LLMResponse(
            resp.choices[0].message.content or "",
            LLMUsage(usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0),
            {"length": "max_tokens", "stop": "end_turn"}.get(finish, finish),
        )

    def stream(self, system: str, prompt: str, max_tokens: int) -> Iterator[str | LLMUsage]:
        usage = LLMUsage()
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=self._messages(system, prompt),
            stream=True,
            stream_options={"include_usage": True},
        )
        for event in stream:
            if event.usage:
                usage = LLMUsage(event.usage.prompt_tokens, event.usage.completion_tokens)
            if event.choices and event.choices[0].delta.content:
                yield event.choices[0].delta.content
        yield usage


class BedrockLLM:
    """AWS Bedrock through the model-agnostic Converse API."""

    def __init__(self, model: str, region: str | None = None):
        try:
            import boto3
        except ImportError as exc:
            raise ImportError("Install the bedrock extra: pip install 'codeatlas[bedrock]'") from exc
        self._client = boto3.client("bedrock-runtime", region_name=region)
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

    def complete(self, system: str, prompt: str, max_tokens: int, thinking: str | None = None) -> LLMResponse:
        resp = self._client.converse(**self._request(system, prompt, max_tokens, thinking))
        text = "".join(part.get("text", "") for part in resp["output"]["message"]["content"])
        usage = resp.get("usage", {})
        return LLMResponse(
            text, LLMUsage(usage.get("inputTokens", 0), usage.get("outputTokens", 0)), resp.get("stopReason")
        )

    def stream(self, system: str, prompt: str, max_tokens: int) -> Iterator[str | LLMUsage]:
        resp = self._client.converse_stream(**self._request(system, prompt, max_tokens))
        usage = LLMUsage()
        for event in resp["stream"]:
            if "contentBlockDelta" in event:
                text = event["contentBlockDelta"]["delta"].get("text")
                if text:
                    yield text
            elif "metadata" in event:
                u = event["metadata"].get("usage", {})
                usage = LLMUsage(u.get("inputTokens", 0), u.get("outputTokens", 0))
        yield usage


def build_llm(settings: Settings) -> LLM:
    provider = settings.llm_provider.lower()
    if provider == "none":
        raise LLMNotConfiguredError("LLM provider is 'none'; set CODEATLAS_LLM_PROVIDER to generate answers.")
    if not settings.llm_model:
        raise LLMNotConfiguredError("Set CODEATLAS_LLM_MODEL to the model id to use for answers.")
    if provider == "anthropic":
        return AnthropicLLM(settings.llm_model)
    if provider == "openai":
        return OpenAILLM(settings.llm_model, settings.llm_base_url)
    if provider == "bedrock":
        return BedrockLLM(settings.llm_model, settings.aws_region)
    raise LLMNotConfiguredError(f"Unknown LLM provider: {settings.llm_provider!r}")
