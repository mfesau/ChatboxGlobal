FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# python3-saml (inicio de sesión único de la consola) depende de `xmlsec`,
# que compila contra las cabeceras de libxml2/libxmlsec1; sin esto, la
# instalación de requirements.txt falla al construir esa extensión nativa.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config libxml2-dev libxmlsec1-dev libxmlsec1-openssl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Raíz de certificación corporativa (p. ej. la inspección TLS de un antivirus
# como Kaspersky) además de las públicas de `certifi`: sin esto, cualquier
# llamada saliente desde el contenedor a una API externa (WhatsApp, IA,
# Microsoft) falla con "self-signed certificate in certificate chain" en una
# red que intercepta TLS. La carpeta puede estar vacía: el resultado es
# entonces idéntico al bundle normal de `certifi`.
COPY docker/certs/ /usr/local/share/ca-certificates/extra/
RUN python -c "import certifi; print(certifi.where())" > /tmp/certifi-path && \
    { cat "$(cat /tmp/certifi-path)"; \
      cat /usr/local/share/ca-certificates/extra/*.crt 2>/dev/null; } \
        > /etc/ssl/certs/app-ca-bundle.pem
ENV SSL_CERT_FILE=/etc/ssl/certs/app-ca-bundle.pem

COPY app ./app
COPY db ./db
COPY scripts ./scripts
COPY alembic.ini ./

# Usuario sin privilegios. Solo necesita escribir en `uploads`, las imágenes
# que suben el chatbox y la consola.
RUN useradd --create-home --uid 10001 chatbox && \
    mkdir -p /app/uploads && \
    chown chatbox:chatbox /app/uploads
USER chatbox

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
