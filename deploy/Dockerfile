FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml /app/
COPY src /app/src

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir '.[postgres,ldap]'

USER 65532:65532
ENTRYPOINT ["dms"]
