FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

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
