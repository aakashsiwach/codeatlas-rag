"""LLM providers behind one small interface: Anthropic API, OpenAI(-compatible), AWS Bedrock and Azure OpenAI."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from codeatlas.config import Settings


class LLMNotConfiguredError(RuntimeError):
    pass


# Provider failures, normalised so callers can tell a throttle from a missing permission.
_ERROR_KINDS = (
    ("content_filter", ("ContentFilter", "ResponsibleAIPolicyViolation")),
    ("throttled", ("Throttling", "TooManyRequests", "RateLimit", "ServiceQuotaExceeded")),
    ("access", ("AccessDenied", "Unrecognized", "ExpiredToken", "NoCredentials", "Authentication",
                "PermissionDenied", "InvalidSignature", "Credential", "Unauthorized")),
    ("timeout", ("Timeout",)),
    ("network", ("Connection", "EndpointConnection")),
    ("unavailable", ("ServiceUnavailable", "InternalServer", "Overloaded", "ModelNotReady")),
    ("validation", ("Validation", "BadRequest", "NotFound", "UnprocessableEntity", "ResourceNotFound")),
)
_PROVIDER_MODULES = (
    "botocore", "boto3", "anthropic", "openai", "httpx", "httpx2", "urllib3", "requests", "azure", "msal",
)


class LLMError(RuntimeError):
    """A provider call failed.

    kind: throttled | access | timeout | network | unavailable | validation | content_filter | other."""

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


def _content_filter_message(exc: BaseException) -> str | None:
    """A clear message for an Azure OpenAI content-filter rejection (HTTP 400, code "content_filter"), or None.

    Names the filtered categories and severities only, never the prompt text."""
    body = getattr(exc, "body", None)
    body = body if isinstance(body, dict) else {}
    inner = body.get("innererror") if isinstance(body.get("innererror"), dict) else {}
    if getattr(exc, "code", None) != "content_filter" and inner.get("code") != "ResponsibleAIPolicyViolation":
        return None
    results = inner.get("content_filter_result") or {}
    filtered = [
        f"{name}={r['severity']}" if r.get("severity") else name
        for name, r in results.items()
        if isinstance(r, dict) and r.get("filtered")
    ]
    categories = f" (filtered: {', '.join(filtered)})" if filtered else ""
    return (
        f"the Azure OpenAI content filter blocked the prompt{categories}. Rephrase the question, or review the "
        "content filter configured on the deployment"
    )


def as_llm_error(provider: str, exc: BaseException) -> LLMError:
    """Map an SDK exception (boto3 ClientError codes included) to an LLMError."""
    filtered = _content_filter_message(exc)
    if filtered:
        return LLMError("content_filter", provider, type(exc).__name__, filtered)
    response = getattr(exc, "response", None)
    code = response.get("Error", {}).get("Code", "") if isinstance(response, dict) else ""
    error_class = code or type(exc).__name__
    body = getattr(exc, "body", None)  # openai SDK: the error object, whose "message" is the readable part
    body_message = body.get("message") if isinstance(body, dict) and isinstance(body.get("message"), str) else None
    message = (response.get("Error", {}).get("Message") if code else None) or body_message or str(exc)
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


def model_id(settings: Settings) -> str | None:
    """The configured model: the deployment name for Azure OpenAI, CODEATLAS_LLM_MODEL for the others."""
    if settings.llm_provider.lower() == "azure_openai":
        return settings.azure_openai_deployment
    return settings.llm_model


def price_key(settings: Settings) -> str:
    return f"{settings.llm_provider.lower()}:{model_id(settings)}"


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
    per_model = settings.thinking_by_model.get(model_id(settings) or "", {})
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

    def _limits(self, max_tokens: int) -> dict[str, Any]:
        """Extra request fields. None here: OpenAI-compatible servers differ on the output-limit field."""
        return {}

    def _stop_reason(self, finish: str | None) -> str | None:
        return {"length": "max_tokens", "stop": "end_turn"}.get(finish, finish) if finish else None

    def _complete(self, system: str, prompt: str, max_tokens: int, thinking: str | None = None) -> LLMResponse:
        # thinking is not part of the OpenAI-compatible chat API; it is ignored here.
        resp = self._client.chat.completions.create(
            model=self.model, messages=self._messages(system, prompt), **self._limits(max_tokens)
        )
        usage = resp.usage
        return LLMResponse(
            resp.choices[0].message.content or "",
            LLMUsage(usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0),
            self._stop_reason(resp.choices[0].finish_reason),
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
            **self._limits(max_tokens),
        )
        for event in stream:
            if event.usage:
                usage = LLMUsage(event.usage.prompt_tokens, event.usage.completion_tokens)
            if event.choices and event.choices[0].finish_reason:
                finish = event.choices[0].finish_reason
            if event.choices and event.choices[0].delta and event.choices[0].delta.content:
                yield event.choices[0].delta.content
        usage.stop_reason = self._stop_reason(finish)
        yield usage


AZURE_OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default"
AZURE_AUTH_MODES = ("auto", "entra", "key")


def _azure_auth(auth: str, credential: Any = None) -> tuple[dict[str, Any], str]:
    """Client keyword arguments, and a label for the auth in use (never the key itself).

    Microsoft Entra ID comes first: DefaultAzureCredential finds a managed identity in Azure and `az login`
    locally. In "auto" mode the AZURE_OPENAI_API_KEY environment variable is the fallback, used only when it
    is set and no Entra token can be obtained (tried once, here)."""
    key = os.environ.get("AZURE_OPENAI_API_KEY")
    if auth == "key":
        if not key:
            raise LLMNotConfiguredError(
                "CODEATLAS_AZURE_OPENAI_AUTH=key needs the AZURE_OPENAI_API_KEY environment variable"
            )
        return {"api_key": key}, "api key (AZURE_OPENAI_API_KEY)"
    try:
        from azure.core.exceptions import ClientAuthenticationError
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    except ImportError as exc:
        if auth == "auto" and key:
            return {"api_key": key}, "api key (AZURE_OPENAI_API_KEY; azure-identity is not installed)"
        raise ImportError("Install the azure extra for Entra ID auth: pip install 'codeatlas[azure]'") from exc
    credential = credential or DefaultAzureCredential(exclude_interactive_browser_credential=True)
    if auth == "auto" and key:
        try:
            credential.get_token(AZURE_OPENAI_SCOPE)
        except ClientAuthenticationError as exc:  # CredentialUnavailableError is a subclass
            return {"api_key": key}, f"api key (AZURE_OPENAI_API_KEY; no Entra ID token: {type(exc).__name__})"
    return {"azure_ad_token_provider": get_bearer_token_provider(credential, AZURE_OPENAI_SCOPE)}, "entra id"


class AzureOpenAILLM(OpenAILLM):
    """Azure OpenAI through the openai SDK's AzureOpenAI client. The model is the deployment name.

    The SDK retries throttling (429) and transient errors with exponential backoff, honouring Azure's
    retry-after headers, up to max_retries times. A content-filter rejection becomes LLMError kind
    "content_filter", also when the filter stops an answer part-way (finish_reason "content_filter")."""

    def __init__(
        self,
        deployment: str,
        endpoint: str,
        api_version: str,
        auth: str = "auto",
        timeout: float = 120.0,
        max_retries: int = 4,
        credential: Any = None,
        http_client: Any = None,
    ):
        try:
            from openai import AzureOpenAI
        except ImportError as exc:
            raise ImportError("Install the azure extra: pip install 'codeatlas[azure]'") from exc
        client_auth, self.auth = _azure_auth(auth, credential)
        self._client = AzureOpenAI(
            azure_endpoint=endpoint,
            azure_deployment=deployment,
            api_version=api_version,
            timeout=timeout,
            max_retries=max_retries,
            http_client=http_client,
            **client_auth,
        )
        self.model = deployment
        self.name = f"azure_openai:{deployment}"

    def _limits(self, max_tokens: int) -> dict[str, Any]:
        # max_completion_tokens, not max_tokens: reasoning models on Azure reject max_tokens.
        return {"max_completion_tokens": max_tokens}

    def _stop_reason(self, finish: str | None) -> str | None:
        if finish == "content_filter":
            raise LLMError(
                "content_filter",
                self.name,
                "ContentFilterFinishReason",
                "the Azure OpenAI content filter stopped the answer. Rephrase the question, or review the "
                "content filter configured on the deployment",
            )
        return super()._stop_reason(finish)


class BedrockLLM(_Provider):
    """AWS Bedrock through the model-agnostic Converse API."""

    def __init__(
        self, model: str, region: str | None = None, timeout: float = 120.0, max_retries: int = 4,
        profile: str | None = None,
    ):
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
        session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        self._client = session.client("bedrock-runtime", region_name=region, config=config)
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


class OfflineLLM:
    """Provider "fake": a deterministic, offline stand-in for demos, the UI and end-to-end tests.

    It never calls a model. It cites the first one or two context blocks so citations, the source viewer and
    streaming can be exercised without credentials or cost. Asked to judge an answer, it returns a fixed,
    parseable verdict, so judged eval runs can be tested offline (their scores mean nothing)."""

    def __init__(self, model: str = "offline"):
        self.model = model
        self.name = f"fake:{model}"

    @staticmethod
    def _reply(prompt: str) -> str:
        if "Answer to evaluate:" in prompt:  # the LLM-judge prompt (codeatlas.eval.judge)
            return '{"claims": 1, "supported": 1, "correct": null}'
        if '<block id="1"' not in prompt:
            return "I don't know based on the indexed code."
        related = " Related code is in block [2]." if '<block id="2"' in prompt else ""
        return f"Offline demo answer (no language model was called). The closest match is block [1].{related}"

    def complete(self, system: str, prompt: str, max_tokens: int, thinking: str | None = None) -> LLMResponse:
        text = self._reply(prompt)
        return LLMResponse(text, LLMUsage(len(prompt) // 4, len(text) // 4), "end_turn")

    def stream(
        self, system: str, prompt: str, max_tokens: int, thinking: str | None = None
    ) -> Iterator[str | LLMUsage]:
        text = self._reply(prompt)
        words = text.split(" ")
        for i, word in enumerate(words):
            yield word if i == len(words) - 1 else word + " "
        yield LLMUsage(len(prompt) // 4, len(text) // 4, "end_turn")


def _build_azure(settings: Settings) -> AzureOpenAILLM:
    endpoint, deployment = settings.azure_openai_endpoint, settings.azure_openai_deployment
    required = (("CODEATLAS_AZURE_OPENAI_ENDPOINT", endpoint), ("CODEATLAS_AZURE_OPENAI_DEPLOYMENT", deployment))
    missing = [name for name, value in required if not value]
    if missing:
        raise LLMNotConfiguredError(f"Set {' and '.join(missing)} for the azure_openai provider.")
    if not endpoint.startswith("https://"):
        raise LLMNotConfiguredError(
            "CODEATLAS_AZURE_OPENAI_ENDPOINT must be an https:// URL, e.g. https://<resource>.openai.azure.com"
        )
    if not settings.azure_openai_api_version:
        raise LLMNotConfiguredError("Set CODEATLAS_AZURE_OPENAI_API_VERSION, e.g. 2024-10-21.")
    auth = settings.azure_openai_auth.lower()
    if auth not in AZURE_AUTH_MODES:
        raise LLMNotConfiguredError(
            f"CODEATLAS_AZURE_OPENAI_AUTH must be one of {', '.join(AZURE_AUTH_MODES)}, not {auth!r}"
        )
    return AzureOpenAILLM(
        deployment, endpoint, settings.azure_openai_api_version, auth, settings.llm_timeout_s,
        settings.llm_max_retries,
    )


def build_llm(settings: Settings) -> LLM:
    provider = settings.llm_provider.lower()
    if provider == "fake":
        return OfflineLLM(settings.llm_model or "offline")
    if provider == "none":
        raise LLMNotConfiguredError("LLM provider is 'none'; set CODEATLAS_LLM_PROVIDER to generate answers.")
    if provider == "azure_openai":
        return _build_azure(settings)
    if not settings.llm_model:
        raise LLMNotConfiguredError("Set CODEATLAS_LLM_MODEL to the model id to use for answers.")
    if provider == "anthropic":
        return AnthropicLLM(settings.llm_model, settings.llm_timeout_s, settings.llm_max_retries)
    if provider == "openai":
        return OpenAILLM(settings.llm_model, settings.llm_base_url, settings.llm_timeout_s, settings.llm_max_retries)
    if provider == "bedrock":
        return BedrockLLM(
            settings.llm_model, settings.aws_region, settings.llm_timeout_s, settings.llm_max_retries,
            profile=settings.aws_profile,
        )
    raise LLMNotConfiguredError(f"Unknown LLM provider: {settings.llm_provider!r}")
