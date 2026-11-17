# Adaptar un agente existente

> **En esta carpeta la adaptación ya está aplicada**: `agent/starter_agent/handler.py` delega en `agent/text2sql_adapter/bridge.py`, que reutiliza `agent/text2sql_core.py`. Este documento se conserva como referencia de las reglas del contrato `handle_message`; el ejemplo de código es genérico. Ver README.md § Comportamiento original → dónde vive ahora.

La infraestructura, la web y los adaptadores no cambian. Su agente entra por **una función**:

```python
from starter_agent.contract import AgentContext, AgentResult

def handle_message(message: str, state: dict, context: AgentContext) -> AgentResult: ...
```

## Qué archivos modificar

| Archivo | Cambio | ¿Obligatorio? |
| --- | --- | --- |
| `agent/starter_agent/handler.py` | Sustituir el demo por una llamada a su agente | Sí |
| `agent/<su_paquete>/` | Copiar el código de su agente junto a `starter_agent/` | Sí, si no está en PyPI |
| `agent/requirements.txt` → `scripts/lock-dependencies.ps1` | Añadir sus dependencias y regenerar el lock Linux | Si tiene dependencias |
| `azure.yaml` (`env`) y `scripts/publish-agent.ps1` (`$settings`) | Variables de configuración nuevas (nunca `FOUNDRY_*` ni `AGENT_*`) | Si necesita configuración |
| `scripts/grant-agent-permissions.ps1` (`$grants`) | Roles para recursos nuevos que use la identidad del agente | Si accede a otros recursos |
| `infra/resources.bicep` | Recursos nuevos y sus salidas | Si necesita recursos |
| `tests/` | Pruebas de su lógica a través de `handle_message` y `service.process` | Sí |
| `infra/parameters/pilot.bicepparam`, `web.title` | Nombres, modelo y título | Sí |

No necesita tocar `agent/main.py`, `service.py`, `contract.py`, `web/` ni el resto de los scripts.

## Paso a paso

1. **Copie su código** en `agent/`, por ejemplo `agent/my_agent/`. Foundry ejecuta `python main.py` desde la raíz del ZIP, así que `import my_agent` funciona sin instalarlo. Si su agente es un paquete con `pyproject.toml`, copie la carpeta del paquete (la que contiene `__init__.py`), no el proyecto completo.
2. **Escriba el puente** en `handler.py`:

   ```python
   from my_agent import Agent, RateLimited, BadInput   # su código
   from .contract import AgentBusyError, AgentContext, AgentInputError, AgentResult, OutputFile

   _agent = Agent()  # se construye una vez por contenedor

   def handle_message(message, state, context: AgentContext) -> AgentResult:
       history = state.get("history", []) if isinstance(state, dict) else []
       try:
           answer = _agent.reply(message, history=history, request_id=context.request_id)
       except BadInput as exc:
           raise AgentInputError(str(exc)) from None
       except RateLimited as exc:
           raise AgentBusyError("El servicio está ocupado; reintenta.", retry_after_ms=exc.retry_after_ms) from None
       new_state = {"history": [*history, {"role": "user", "content": message},
                                {"role": "assistant", "content": answer.text}][-20:]}
       files = [OutputFile(f.name, f.content, f.mime) for f in answer.files]
       return AgentResult(reply=answer.text, state=new_state, files=files)
   ```

3. **Dependencias**: añada a `agent/requirements.txt` las de su agente (rangos compatibles) y ejecute `./scripts/lock-dependencies.ps1`. Evite paquetes que necesiten compilación nativa en Linux sin wheels. `validate-local.ps1` rechaza imports sin dependencia declarada; si el módulo y el paquete se llaman distinto (`yaml` → `pyyaml`, `jwt` → `pyjwt`), añada la pareja a `IMPORT_TO_DISTRIBUTION` en `scripts/package.py`.
4. **Configuración**: lea su configuración de variables propias (`MY_AGENT_*`). Añádalas al bloque `env` de `azure.yaml` como `${MY_AGENT_X}` y al diccionario `$settings` de `publish-agent.ps1`. No las declare con prefijo `FOUNDRY_` ni `AGENT_`.
5. **Modelo**: si su agente ya crea su cliente de modelo, puede ignorar `context.model` y leer `MODEL_ENDPOINT`/`MODEL_DEPLOYMENT_NAME` (ya publicados) o usar `ModelSettings.from_env()` y `OpenAIModelClient`. Con `STARTER_RESPONDER=echo`, `context.model` es `None`.
6. **Archivos**: use `context.files.read_input(nombre)` para entradas y devuelva `OutputFile` para salidas. No escriba en Blob directamente: el servicio aplica nombres seguros e idempotencia.
7. **Pruebas**: mantenga las suyas y añada al menos:
   - dos turnos encadenando `result.state`;
   - estado vacío (`{}`) = conversación nueva;
   - estado manipulado → `AgentInputError`;
   - `service.process(...)` devuelve el contrato (`reply`, `state`, `files`).
   Las pruebas de contrato y de web existentes deben seguir pasando sin cambios.
8. **Valide y publique**: `./scripts/validate-local.ps1`, después el orden del README.

## Reglas del contrato

| Regla | Detalle |
| --- | --- |
| Entrada | `message`: texto no vacío ≤ 4000 caracteres. `state`: dict ≤ 64 KB (lo que devolvió el turno anterior, `{}` al inicio). |
| Estado | Lo conserva el cliente. Debe ser JSON, pequeño y validado. No guarde secretos ni tokens. Versiónelo (`{"v": 1, …}`) para poder migrarlo. |
| Errores | `AgentInputError` (400, no reintentable), `AgentBusyError` (503, reintentable, `retry_after_ms`), `AgentConfigError` (500, operador). El mensaje se muestra al usuario: sin datos sensibles. Cualquier otra excepción se convierte en `internal_error` sin detalles. |
| Idempotencia | El mismo `context.request_id` puede llegar dos veces (reintento). Úselo como clave en efectos externos. |
| Archivos | Máximo 5 por turno y 10 MB cada uno; nombre `[A-Za-z0-9][A-Za-z0-9._-]{0,99}`. |
| Concurrencia | El handler es síncrono y corre en un hilo; no bloquea el servidor. Si su agente es `async`, use `asyncio.run(...)` dentro del handler o adapte `main.py` para llamarlo con `await`. |
| Registros | Registre metadatos (`request_id`, tiempos, códigos), nunca el contenido del usuario. |

## Casos frecuentes

- **Su agente guarda estado en servidor** (base de datos, memoria del framework): guarde en `state` solo una clave (`{"conversation_id": "…"}`) y genere una nueva cuando `state == {}`. Conceda a la identidad del agente acceso a ese almacén en `grant-agent-permissions.ps1`.
- **Microsoft Agent Framework, LangGraph, Semantic Kernel**: construya el agente o grafo una vez a nivel de módulo y llámelo dentro de `handle_message`; traduzca su historial a `state`.
- **Ya usa el protocolo `responses`**: esta base usa `invocations` porque el cuerpo y la respuesta son propios (estado explícito y archivos). Para `responses`, cambie el adaptador `main.py` y el transporte de `web/client.py`; `handle_message` no cambia.
- **Respuestas largas**: aumente `AGENT_TIMEOUT_SECONDS` (web) y `MODEL_TIMEOUT_SECONDS` (agente). El streaming no está implementado: `web/client.py` ya interpreta SSE si el adaptador lo emite.
- **Varios usuarios**: amplíe `allowedUserObjectIds` o pase a grupos (ARCHITECTURE.md § Extensiones). Los archivos ya quedan aislados por sesión de navegador.

## Comprobación final

- [ ] `./scripts/validate-local.ps1` pasa (incluye importación aislada del paquete y la ausencia de dependencias no declaradas).
- [ ] La conversación local de varios turnos y **Nueva conversación** funcionan con `./scripts/run-local.ps1`.
- [ ] Las pruebas propias del agente siguen pasando.
- [ ] `check-availability.ps1 -InvokeAgent` responde tras publicar.
