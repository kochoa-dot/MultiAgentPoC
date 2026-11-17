"""Configuración del modelo y traducción de errores de la API a errores del contrato."""
import httpx
import openai
import pytest

from starter_agent.contract import AgentBusyError, AgentConfigError
from starter_agent.model import DEFAULT_TOKEN_SCOPE, ModelSettings, OpenAIModelClient

ENV = {"MODEL_ENDPOINT": "https://acct.openai.azure.com/", "MODEL_DEPLOYMENT_NAME": "chat"}


def api_error(cls, status, headers=None):
    request = httpx.Request("POST", "https://acct.openai.azure.com/openai/v1/chat/completions")
    return cls("error", response=httpx.Response(status, request=request, headers=headers or {}), body=None)


class FakeCompletions:
    def __init__(self, outcome):
        self.outcome = outcome
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.outcome, Exception):
            raise self.outcome
        message = type("M", (), {"content": self.outcome})()
        return type("R", (), {"choices": [type("C", (), {"message": message})()]})()


def fake_client(outcome):
    completions = FakeCompletions(outcome)
    return type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})(), completions


def test_settings_from_env_are_normalized():
    settings = ModelSettings.from_env({**ENV, "MODEL_ENDPOINT": "https://acct.openai.azure.com/openai/v1"})
    assert settings.endpoint == "https://acct.openai.azure.com"
    assert settings.token_scope == DEFAULT_TOKEN_SCOPE and settings.temperature is None


@pytest.mark.parametrize("env", [{}, {"MODEL_ENDPOINT": "http://x", "MODEL_DEPLOYMENT_NAME": "c"},
                                 {**ENV, "MODEL_MAX_OUTPUT_TOKENS": "muchos"}])
def test_invalid_settings_raise_config_error(env):
    with pytest.raises(AgentConfigError):
        ModelSettings.from_env(env)


def test_completion_uses_deployment_and_completion_tokens():
    client, completions = fake_client(" hola ")
    model = OpenAIModelClient(ModelSettings.from_env(ENV), client=client)
    assert model.complete([{"role": "user", "content": "hi"}]) == "hola"
    assert completions.kwargs["model"] == "chat"
    assert completions.kwargs["max_completion_tokens"] == 2000
    assert "temperature" not in completions.kwargs


def test_rate_limit_becomes_retryable_busy_error():
    client, _ = fake_client(api_error(openai.RateLimitError, 429, {"retry-after-ms": "2500"}))
    with pytest.raises(AgentBusyError) as error:
        OpenAIModelClient(ModelSettings.from_env(ENV), client=client).complete([])
    assert error.value.retryable and error.value.retry_after_ms == 2500


@pytest.mark.parametrize("cls, status, code", [
    (openai.AuthenticationError, 401, "model_forbidden"),
    (openai.PermissionDeniedError, 403, "model_forbidden"),
    (openai.NotFoundError, 404, "model_not_found"),
])
def test_permission_and_deployment_errors_need_operator(cls, status, code):
    client, _ = fake_client(api_error(cls, status))
    with pytest.raises(AgentConfigError) as error:
        OpenAIModelClient(ModelSettings.from_env(ENV), client=client).complete([])
    assert error.value.code == code and not error.value.retryable


def test_timeout_is_retryable():
    request = httpx.Request("POST", "https://acct.openai.azure.com")
    client, _ = fake_client(openai.APITimeoutError(request=request))
    with pytest.raises(AgentBusyError):
        OpenAIModelClient(ModelSettings.from_env(ENV), client=client).complete([])
