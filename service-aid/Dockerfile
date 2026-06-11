FROM python:3.14-slim AS build-stage

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsodium23 libsodium-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY service-aid/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -t /deps/

COPY src/keri /deps/keri
COPY service-aid/serviceaid /deps/serviceaid
COPY service-aid/bootstrap.py /deps/bootstrap.py
# Developer handler module, baked into the image at build time.
# PLACEHOLDER until Task 13 lands examples/rating_engine/handler.py — `docker build`
# fails on this COPY until then (intentional; the example is part of this branch).
# Convention: the file's basename (minus .py) MUST equal the SERVICEAID_HANDLER env
# var the ServiceAid construct sets (runtime.init imports that module by name).
# For your own service: replace this COPY with your handler file.
COPY service-aid/examples/rating_engine/handler.py /deps/rating_handler.py

RUN mkdir -p /deps/lib && find /usr/lib -name 'libsodium.so*' -exec cp -P {} /deps/lib/ \;

FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsodium23 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir awslambdaric

WORKDIR /var/task
COPY --from=build-stage /deps/ /var/task/

ENTRYPOINT ["python", "-m", "awslambdaric"]
CMD ["bootstrap.handler"]
