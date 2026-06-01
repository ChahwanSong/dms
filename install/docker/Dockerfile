FROM python:3.11-slim

ARG KUBECTL_VERSION=v1.34.0

ENV PYTHONUNBUFFERED=1 \
    HOME=/home/dms

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        openssh-client \
    && curl -fsSLo /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && chmod 0755 /usr/local/bin/kubectl \
    && addgroup --system --gid 65532 dms \
    && adduser --system --uid 65532 --ingroup dms --home /home/dms dms \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/
COPY src /app/src

RUN pip install --no-cache-dir '.[postgres,ldap,kubernetes]'

USER 65532:65532
ENTRYPOINT ["dms"]
