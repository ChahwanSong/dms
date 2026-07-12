FROM python:3.11-slim

ARG KUBECTL_VERSION=v1.34.0

ENV PYTHONUNBUFFERED=1 \
    HOME=/home/dms

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        openssh-client

# Optional corporate CA (staged into the build context by build-images.sh CA_CERT=...).
# The dir always exists (.gitkeep); with no cert this is a clean no-op. Installed BEFORE
# the HTTPS fetches below (kubectl, pip) so they trust a TLS-intercepting proxy, and kept
# in the runtime image so DMS also trusts internally-signed endpoints (k8s API, LDAP,
# PostgreSQL) at run time.
COPY install/docker/certs/ /usr/local/share/ca-certificates/dms-extra/
RUN update-ca-certificates
ENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

RUN curl -fsSLo /usr/local/bin/kubectl \
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
