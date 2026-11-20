"""Azure OpenAI provider: the real openai AzureOpenAI client over a mocked HTTP transport (no network, no keys)."""

import json

import pytest

openai = pytest.importorskip("openai")
pytest.importorskip("azure.identity")
httpx2 = pytest.importorskip("httpx2")

from azure.core.credentials import AccessToken  # noqa: E402
from azure.identity import CredentialUnavailableError  # noqa: E402

from codeatlas.api.app import llm_label  # noqa: E402
from codeatlas.config import Settings  # noqa: E402
from codeatlas.doctor import FAIL, OK, WARN, check_llm_call, check_llm_config  # noqa: E402
from codeatlas.llm import (  # noqa: E402
    AzureOpenAILLM,
    LLMError,
    LLMNotConfiguredError,
    LLMUsage,
    build_llm,
    is_truncated,
    llm_cost_usd,
    price_key,
    thinking_for,
)

ENDPOINT = "https://my-resource.openai.azure.com"
DEPLOYMENT = "gpt-dep"
SECRET_KEY = "azure-key-that-must-never-be-printed"
URL = f"{ENDPOINT}/openai/deployments/{DEPLOYMENT}/chat/completions?api-version=2024-10-21"


@pytest.fixture(autouse=True)
def no_ambient_key(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)


class FakeCredential:
    """Stands in for DefaultAzureCredential: returns a token, or raises like a machine without a login."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.scopes: list[tuple[str, ...]] = []

    def get_token(self, *scopes, **_):
        self.scopes.append(scopes)
        if self.fail:
            raise CredentialUnavailableError("no managed identity and no az login")
        return AccessToken("entra-token", 9_999_999_999)

    def get_token_info(self, *scopes, **_):  # azure-identity's token provider prefers this protocol
        self.get_token(*scopes)
        from azure.core.credentials import AccessTokenInfo

        return AccessTokenInfo("entra-token", 9_999_999_999)


class Server:
    """Records requests and replays canned responses (the last one repeats)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list = []

    def __call__(self, request):
        self.requests.append(request)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    def client(self):
        return httpx2.Client(transport=httpx2.MockTransport(self))

    def body(self, i: int = -1) -> dict:
        return json.loads(self.requests[i].content)


def completion(text="Done [1].", finish="stop", usage=(120, 8)):
    return httpx2.Response(200, json={
        "id": "c1", "object": "chat.completion", "created": 1, "model": "gpt-x",
        "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1], "total_tokens": sum(usage)},
    })


def sse(*chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def chunk(content=None, finish=None):
    return {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "gpt-x",
            "choices": [{"index": 0, "delta": {"content": content} if content else {}, "finish_reason": finish}]}


def error(http_status, code, message="error", headers=None, **extra):
    return httpx2.Response(http_status, headers=headers or {}, json={"error": {"code": code, "message": message, **extra}})


def make(server, auth="entra", credential=None, max_retries=0):
    return AzureOpenAILLM(DEPLOYMENT, ENDPOINT, "2024-10-21", auth=auth, max_retries=max_retries,
                          credential=credential or FakeCredential(), http_client=server.client())


def azure_settings(tmp_path, **extra) -> Settings:
    values = {
        "index_dir": tmp_path / "idx", "embedding_provider": "hashing", "reranker_model": "",
        "llm_provider": "azure_openai", "azure_openai_endpoint": ENDPOINT, "azure_openai_deployment": DEPLOYMENT,
    }
    return Settings(_env_file=None, **{**values, **extra})


# --- configuration -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"azure_openai_endpoint": None}, "CODEATLAS_AZURE_OPENAI_ENDPOINT"),
        ({"azure_openai_deployment": None}, "CODEATLAS_AZURE_OPENAI_DEPLOYMENT"),
        ({"azure_openai_endpoint": None, "azure_openai_deployment": None}, "ENDPOINT and CODEATLAS_AZURE_OPENAI_DEPLOYMENT"),
        ({"azure_openai_endpoint": "http://my-resource.openai.azure.com"}, "https:// URL"),
        ({"azure_openai_api_version": ""}, "CODEATLAS_AZURE_OPENAI_API_VERSION"),
        ({"azure_openai_auth": "password"}, "auto, entra, key"),
    ],
)
def test_config_is_validated(tmp_path, extra, message):
    with pytest.raises(LLMNotConfiguredError, match=message):
        build_llm(azure_settings(tmp_path, **extra))


def test_build_needs_no_llm_model_and_uses_the_deployment(tmp_path, monkeypatch):
    monkeypatch.setattr("azure.identity.DefaultAzureCredential", lambda **_: FakeCredential())
    settings = azure_settings(tmp_path, llm_max_retries=3, llm_timeout_s=45)
    llm = build_llm(settings)
    assert isinstance(llm, AzureOpenAILLM) and settings.llm_model is None
    assert llm.name == "azure_openai:gpt-dep" and llm.auth == "entra id"
    assert llm._client.max_retries == 3 and llm._client.timeout == 45
    assert llm_label(settings) == "azure_openai:gpt-dep"


def test_price_key_and_thinking_use_the_deployment(tmp_path):
    settings = azure_settings(
        tmp_path,
        llm_model="ignored",
        llm_prices={"azure_openai:gpt-dep": {"input_per_mtok": 2.0, "output_per_mtok": 8.0}},
        thinking_by_model={"gpt-dep": {"judge": ""}},
    )
    assert price_key(settings) == "azure_openai:gpt-dep"
    assert llm_cost_usd(settings, 1_000_000, 500_000) == 6.0
    assert thinking_for(settings, "judge") is None and thinking_for(settings, "answer") is None


# --- authentication ----------------------------------------------------------------------------------


def test_entra_id_sends_a_bearer_token_for_the_cognitive_services_scope():
    server, cred = Server(completion()), FakeCredential()
    llm = make(server, auth="entra", credential=cred)
    llm.complete("system", "question", 50)
    req = server.requests[0]
    assert req.headers["authorization"] == "Bearer entra-token" and "api-key" not in req.headers
    assert cred.scopes and cred.scopes[-1] == ("https://cognitiveservices.azure.com/.default",)


def test_auto_prefers_entra_id_even_when_a_key_is_set(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", SECRET_KEY)
    server = Server(completion())
    llm = make(server, auth="auto")
    llm.complete("s", "p", 10)
    assert llm.auth == "entra id" and server.requests[0].headers["authorization"] == "Bearer entra-token"
    assert "api-key" not in server.requests[0].headers


def test_auto_falls_back_to_the_api_key_without_an_entra_token(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", SECRET_KEY)
    server = Server(completion())
    llm = make(server, auth="auto", credential=FakeCredential(fail=True))
    llm.complete("s", "p", 10)
    assert server.requests[0].headers["api-key"] == SECRET_KEY and "authorization" not in server.requests[0].headers
    assert llm.auth.startswith("api key") and "CredentialUnavailableError" in llm.auth
    assert SECRET_KEY not in llm.auth


def test_key_mode_uses_only_the_key(monkeypatch):
    cred = FakeCredential()
    with pytest.raises(LLMNotConfiguredError, match="AZURE_OPENAI_API_KEY"):
        make(Server(completion()), auth="key", credential=cred)
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", SECRET_KEY)
    server = Server(completion())
    make(server, auth="key", credential=cred).complete("s", "p", 10)
    assert server.requests[0].headers["api-key"] == SECRET_KEY and cred.scopes == []


def test_entra_failure_without_a_key_is_an_access_error():
    llm = make(Server(completion()), auth="auto", credential=FakeCredential(fail=True))  # no key: no fallback
    with pytest.raises(LLMError) as info:
        llm.complete("s", "p", 10)
    assert info.value.kind == "access" and info.value.error_class == "CredentialUnavailableError"


# --- generation --------------------------------------------------------------------------------------


def test_complete_maps_usage_stop_reason_and_sends_max_completion_tokens():
    server = Server(completion("Answer [1].", finish="stop", usage=(300, 12)))
    resp = make(server).complete("system prompt", "the question", 77)
    assert str(server.requests[0].url) == URL
    body = server.body()
    assert body["max_completion_tokens"] == 77 and "max_tokens" not in body
    assert body["messages"] == [{"role": "system", "content": "system prompt"}, {"role": "user", "content": "the question"}]
    assert (resp.text, resp.usage.input_tokens, resp.usage.output_tokens, resp.stop_reason) == ("Answer [1].", 300, 12, "end_turn")


def test_truncation_is_reported_as_max_tokens():
    resp = make(Server(completion("Partial", finish="length"))).complete("s", "p", 5)
    assert resp.stop_reason == "max_tokens" and is_truncated(resp.stop_reason)


def test_stream_yields_deltas_then_usage_with_the_stop_reason():
    prompt_filter_chunk = {"id": "", "object": "", "created": 0, "model": "", "choices": [],
                           "prompt_filter_results": [{"prompt_index": 0, "content_filter_results": {}}]}
    usage_chunk = {**chunk(), "choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 3, "total_tokens": 43}}
    server = Server(sse(prompt_filter_chunk, chunk("Hello"), chunk(" world"), chunk(finish="length"), usage_chunk))
    items = list(make(server).stream("s", "p", 64))
    assert items[:-1] == ["Hello", " world"]
    assert items[-1] == LLMUsage(40, 3, "max_tokens")
    body = server.body()
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert body["max_completion_tokens"] == 64


# --- errors ------------------------------------------------------------------------------------------


def test_throttling_is_retried_with_backoff_then_succeeds():
    throttled = error(429, "429", "Rate limit is exceeded.", headers={"retry-after-ms": "1"})
    server = Server(throttled, throttled, completion("ok"))
    resp = make(server, max_retries=2).complete("s", "p", 10)
    assert resp.text == "ok" and len(server.requests) == 3


def test_throttling_beyond_the_retries_is_a_throttled_error():
    server = Server(error(429, "429", "Rate limit is exceeded.", headers={"retry-after-ms": "1"}))
    with pytest.raises(LLMError) as info:
        make(server, max_retries=1).complete("s", "p", 10)
    assert len(server.requests) == 2  # 1 call + 1 retry
    assert (info.value.kind, info.value.error_class, info.value.provider) == ("throttled", "RateLimitError", "azure_openai:gpt-dep")


@pytest.mark.parametrize(
    ("response", "kind", "error_class"),
    [
        (error(401, "401", "Access denied due to invalid subscription key"), "access", "AuthenticationError"),
        (error(403, "PermissionDenied", "principal lacks Cognitive Services OpenAI User"), "access", "PermissionDeniedError"),
        (error(404, "DeploymentNotFound", "The API deployment for this resource does not exist."), "validation", "NotFoundError"),
        (error(400, "invalid_request_error", "Unsupported parameter"), "validation", "BadRequestError"),
        (error(500, "InternalServerError", "boom"), "unavailable", "InternalServerError"),
    ],
)
def test_http_errors_are_classified(response, kind, error_class):
    with pytest.raises(LLMError) as info:
        make(Server(response)).complete("s", "p", 10)
    assert (info.value.kind, info.value.error_class) == (kind, error_class)


@pytest.mark.parametrize(
    ("exc", "kind"),
    [(httpx2.ReadTimeout("read timed out"), "timeout"), (httpx2.ConnectError("refused"), "network")],
)
def test_transport_failures_are_classified(exc, kind):
    def fail(request):
        raise exc

    llm = AzureOpenAILLM(DEPLOYMENT, ENDPOINT, "2024-10-21", auth="entra", max_retries=0, credential=FakeCredential(),
                         http_client=httpx2.Client(transport=httpx2.MockTransport(fail)))
    with pytest.raises(LLMError) as info:
        llm.complete("s", "p", 10)
    assert info.value.kind == kind


def test_a_filtered_prompt_is_a_clear_content_filter_error():
    blocked = error(
        400, "content_filter", "The response was filtered due to the prompt triggering Azure OpenAI's content management policy.",
        param="prompt", status=400,
        innererror={"code": "ResponsibleAIPolicyViolation", "content_filter_result": {
            "hate": {"filtered": False, "severity": "safe"},
            "violence": {"filtered": True, "severity": "medium"},
            "jailbreak": {"filtered": True, "detected": True},
        }},
    )
    server = Server(blocked)
    with pytest.raises(LLMError) as info:
        make(server, max_retries=3).complete("s", "a secret question text", 10)
    err = info.value
    assert len(server.requests) == 1  # a 400 is not retried
    assert (err.kind, err.error_class) == ("content_filter", "BadRequestError")
    assert "content filter blocked the prompt" in err.message and "violence=medium" in err.message
    assert "jailbreak" in err.message and "hate" not in err.message
    assert "secret question" not in err.message


def test_an_answer_stopped_by_the_content_filter_is_an_error():
    with pytest.raises(LLMError) as info:
        make(Server(completion("Partial", finish="content_filter"))).complete("s", "p", 10)
    assert info.value.kind == "content_filter" and "stopped the answer" in info.value.message

    stream = make(Server(sse(chunk("Partial"), chunk(finish="content_filter")))).stream("s", "p", 10)
    assert next(stream) == "Partial"
    with pytest.raises(LLMError) as info:
        list(stream)
    assert info.value.kind == "content_filter"


# --- doctor ------------------------------------------------------------------------------------------


def test_doctor_config_shows_the_auth_path_but_never_the_key(tmp_path, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", SECRET_KEY)
    check = check_llm_config(azure_settings(tmp_path))
    assert check.status == WARN and "unpriced" in check.detail  # no price for azure_openai:gpt-dep yet
    assert "azure_openai:gpt-dep" in check.detail and "endpoint=my-resource.openai.azure.com" in check.detail
    assert "auth=Entra ID (DefaultAzureCredential), else AZURE_OPENAI_API_KEY (set)" in check.detail
    assert SECRET_KEY not in check.detail

    priced = azure_settings(tmp_path, llm_prices={"azure_openai:gpt-dep": {"input_per_mtok": 1, "output_per_mtok": 1}})
    assert check_llm_config(priced).status == OK

    key_only = check_llm_config(azure_settings(tmp_path, azure_openai_auth="key"))
    assert "auth=AZURE_OPENAI_API_KEY (set)" in key_only.detail and "Entra" not in key_only.detail


def test_doctor_config_fails_without_deployment_endpoint_or_credentials(tmp_path):
    no_dep = check_llm_config(azure_settings(tmp_path, azure_openai_deployment=None))
    assert no_dep.status == FAIL and "CODEATLAS_AZURE_OPENAI_DEPLOYMENT" in no_dep.detail
    no_endpoint = check_llm_config(azure_settings(tmp_path, azure_openai_endpoint=None))
    assert no_endpoint.status == FAIL and "CODEATLAS_AZURE_OPENAI_ENDPOINT" in no_endpoint.detail
    no_key = check_llm_config(azure_settings(tmp_path, azure_openai_auth="key"))  # key mode, no key set
    assert no_key.status == FAIL and "auth=none found" in no_key.detail


def test_doctor_llm_call_reports_the_auth_used_and_errors(tmp_path):
    settings = azure_settings(tmp_path)
    server = Server(completion("", finish="length", usage=(14, 1)))
    ok = check_llm_call(settings, make(server))
    assert ok.status == OK
    assert ok.detail.startswith("azure_openai:gpt-dep: stop_reason=max_tokens, tokens 14+1")
    assert ok.detail.endswith("auth=entra id") and server.body()["max_completion_tokens"] == 1

    denied = check_llm_call(settings, make(Server(error(403, "PermissionDenied", "principal lacks the role"))))
    assert denied.status == FAIL
    assert denied.detail == "azure_openai:gpt-dep: PermissionDeniedError (access): principal lacks the role"
