#!/bin/bash
exec python -m uvicorn mailbox_handler:app --host 0.0.0.0 --port "${AWS_LWA_PORT:-8080}"
