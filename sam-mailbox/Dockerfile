FROM python:3.14-slim AS build-stage

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsodium23 libsodium-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY sam-mailbox/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -t /deps/

COPY src/keri /deps/keri
COPY sam-mailbox/bootstrap.py /deps/bootstrap.py
COPY sam-mailbox/mailbox_handler.py /deps/mailbox_handler.py

RUN mkdir -p /deps/lib && find /usr/lib -name 'libsodium.so*' -exec cp -P {} /deps/lib/ \;

FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsodium23 \
    && rm -rf /var/lib/apt/lists/*

# AWS Lambda Web Adapter extension — intercepts Lambda invokes and proxies
# them as HTTP to the uvicorn server on port 8080.
# Pinned to v1.0.0 (GA 2024-03-27 per
# https://github.com/awslabs/aws-lambda-web-adapter/releases). Re-verify
# the latest release before deploying.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:1.0.0 /lambda-adapter /opt/extensions/lambda-adapter

WORKDIR /var/task

COPY --from=build-stage /deps/ /var/task/

CMD ["python", "bootstrap.py"]
