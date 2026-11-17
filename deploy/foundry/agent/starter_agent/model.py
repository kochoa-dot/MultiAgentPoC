"""Inferencia del modelo, separada del alojamiento del agente.

La configuración solo describe *qué modelo* usar y *cómo autenticarse*; no sabe
si el agente corre en Foundry o en local. Variables:

- ``MODEL_ENDPOINT``: ``https://<cuenta>.openai.azure.com`` (sin ``/openai/v1``).
- ``MODEL_DEPLOYMENT_NAME``: nombre del despliegue del modelo.
- ``MODEL_TOKEN_SCOPE``: audiencia del token Entra.
- ``MODEL_TIMEOUT_SECONDS``, ``MODEL_MAX_OUTPUT_TOKENS``, ``MODEL_TEMPERATURE`` (opcional).

Errores de la API se traducen a errores del contrato. El SDK no reintenta
(``max_retries=0``): el reintento lo decide el cliente, una sola vez por capa.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache

from .contract import AgentBusyError, AgentConfigError

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_SCOPE = "https://cognitiveservices.azure.com/.default"


@dataclass(frozen=True)
class ModelSettings:
    endpoint: str
    deployment: str
    token_scope: str = DEFAULT_TOKEN_SCOPE
    timeout_seconds: float = 45.0
    max_output_tokens: int = 2000
    temperature: float | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ModelSettings":
        env = os.environ if env is None else env
        endpoint = env.get("MODEL_ENDPOINT", "").strip().rstrip("/")
        deployment = env.get("MODEL_DEPLOYMENT_NAME", "").strip()
        if not endpoint or not deployment:
            raise AgentConfigError("Falta MODEL_ENDPOINT o MODEL_DEPLOYMENT_NAME.", code="model_config")
        if not endpoint.startswith("https://"):
            raise AgentConfigError("MODEL_ENDPOINT debe usar HTTPS.", code="model_config")
        if endpoint.endswith("/openai/v1"):
            endpoint = endpoint[: -len("/openai/v1")]
        try:
            temperature = env.get("MODEL_TEMPERATURE", "").strip()
            return cls(
                endpoint=endpoint,
                deployment=deployment,
                token_scope=env.get("MODEL_TOKEN_SCOPE", "").strip() or DEFAULT_TOKEN_SCOPE,
                timeout_seconds=float(env.get("MODEL_TIMEOUT_SECONDS", "") or 45),
                max_output_tokens=int(env.get("MODEL_MAX_OUTPUT_TOKENS", "") or 2000),
                temperature=float(temperature) if temperature else None,
            )
        except ValueError:
            raise AgentConfigError("Valor numérico de modelo no válido.", code="model_config") from None


@lru_cache(maxsize=1)
def _credential():
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


def _retry_after_ms(exc: Exception) -> int:
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    for key, factor in (("retry-after-ms", 1), ("retry-after", 1000)):
        try:
            value = headers.get(key)
            if value is not None:
                return int(float(value) * factor)
        except (TypeError, ValueError):
            continue
    return 0


class OpenAIModelClient:
    """Chat Completions del endpoint ``/openai/v1`` con token Entra (sin claves)."""

    def __init__(self, settings: ModelSettings, *, client=None, credential=None):
        self.settings = settings
        if client is None:
            from azure.identity import get_bearer_token_provider
            from openai import OpenAI

            token = get_bearer_token_provider(credential or _credential(), settings.token_scope)
            client = OpenAI(base_url=settings.endpoint + "/openai/v1/", api_key=token,
                            timeout=settings.timeout_seconds, max_retries=0)
        self._client = client

    def complete(self, messages: list[dict[str, str]]) -> str:
        import openai

        options = {"model": self.settings.deployment, "messages": messages,
                   "max_completion_tokens": self.settings.max_output_tokens}
        if self.settings.temperature is not None:
            options["temperature"] = self.settings.temperature
        try:
            response = self._client.chat.completions.create(**options)
        except openai.RateLimitError as exc:
            logger.warning("Model rate limited: deployment=%s", self.settings.deployment)
            raise AgentBusyError("El modelo alcanzó su cuota. Espera unos segundos y reintenta.",
                                 code="model_rate_limited", retry_after_ms=_retry_after_ms(exc)) from None
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            logger.warning("Model unavailable: type=%s", type(exc).__name__)
            raise AgentBusyError("El modelo no respondió a tiempo. Reintenta.", code="model_timeout") from None
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            logger.error("Model authorization failed: status=%s", getattr(exc, "status_code", None))
            raise AgentConfigError("El agente no tiene permiso para usar el modelo.", code="model_forbidden") from None
        except openai.NotFoundError:
            logger.error("Model deployment not found: deployment=%s", self.settings.deployment)
            raise AgentConfigError("El despliegue del modelo no existe.", code="model_not_found") from None
        except openai.InternalServerError:
            raise AgentBusyError("El modelo devolvió un error temporal. Reintenta.", code="model_unavailable") from None
        content = response.choices[0].message.content if response.choices else None
        return (content or "").strip()
