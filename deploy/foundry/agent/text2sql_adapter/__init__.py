"""Puente entre ``text2sql_core`` (lógica Text-to-SQL original) y el contrato ``starter.v1``.

- ``settings``: opciones de ejecución propias (``TEXT2SQL_*``), separadas del alojamiento.
- ``bridge``: ``answer(message, state, context) -> AgentResult``; reutiliza prompt,
  validación AST, ejecución y ``RespuestaAgente`` del núcleo.
- ``simulated``: dobles deterministas (generador y base de datos) para pruebas locales.

La configuración SQL (``SQL_*``) la sigue leyendo ``text2sql_core.Configuracion``;
la del modelo (``MODEL_*``), ``starter_agent.model``.
"""
