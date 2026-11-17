"""Base reutilizable para un Hosted Agent de Microsoft Foundry.

El paquete no depende del mecanismo que lo aloja:

- ``contract``: tipos y formato del intercambio web <-> agente.
- ``handler``: punto de extensión ``handle_message`` (la lógica del agente).
- ``service``: validación, invocación del handler y archivos; sin HTTP.
- ``model``: configuración e inferencia del modelo, separada del alojamiento.
- ``files``: almacén de archivos de entrada/salida (ninguno, local o Blob).
- ``runtime``: construye las dependencias a partir de variables de entorno.

``agent/main.py`` es el adaptador Foundry (protocolo invocations) y
``starter_agent.local`` es el adaptador local sin Azure.
"""

__version__ = "0.1.0"
