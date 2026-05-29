FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml /app/
COPY src /app/src

RUN pip install --no-cache-dir '.[postgres]'

USER 65532:65532
ENTRYPOINT ["dms"]
