# Imagen del agente con Microsoft ODBC Driver 18 for SQL Server.
#
# Es la imagen que se publica con agentHosting.mode=container: scripts/build-agent-image.ps1 la
# construye (linux/amd64), la prueba en local y, con -Push, sube ESA MISMA imagen al ACR del piloto;
# publish-agent.ps1 la referencia por digest en azure.yaml. Con mode=code no se usa (remote_build).
# Está fuera de agent/ para que no entre en el paquete de código. Contexto de compilación: agent/.
#
#   ./scripts/build-agent-image.ps1          # build + prueba de humo local, sin Azure
#   docker build --platform linux/amd64 -f docker/agent.Dockerfile -t text2sql-agent:local agent
#
# Al instalar msodbcsql18 con ACCEPT_EULA=Y se acepta la licencia de Microsoft ODBC Driver 18.
# Pasos de instalación: Microsoft Learn, «Install the Microsoft ODBC driver for SQL Server (Linux)», Debian 12.
FROM python:3.13-slim-bookworm

ENV PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8088

# Sin «apt-get autoremove»: eliminaría libgssapi-krb5-2, que el driver necesita (Learn, mssql-django).
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl gnupg2 ca-certificates && \
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
      > /etc/apt/sources.list.d/mssql-release.list && \
    apt-get update && \
    ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc && \
    apt-get purge -y curl gnupg2 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Mismas versiones exactas (Linux x86_64, Python 3.13) que usa el paquete publicado.
COPY requirements.lock.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt

COPY . .
RUN useradd --create-home --uid 10001 agent && chown -R agent /app
USER agent

EXPOSE 8088
CMD ["python", "main.py"]
