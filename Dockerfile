FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies are copied first so this layer is cached until
# requirements.txt itself changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1000 tio && chown -R tio:tio /app
USER tio

EXPOSE 8000

# 0.0.0.0 is correct inside a container - the container's network namespace
# is the isolation boundary. Exposure to the internet is decided by the port
# mapping and the reverse proxy in front of it.
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
