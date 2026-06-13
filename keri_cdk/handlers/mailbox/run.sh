#!/bin/bash
# LWA's /opt/bootstrap exec-wrapper launches this script in a fresh process, so
# the Lambda-managed runtime's automatic sys.path injection of the layer dir
# (/opt/python) and the task dir (/var/task, where mailbox_handler lives) does
# NOT apply to the `python` we spawn here. Set PYTHONPATH explicitly so uvicorn,
# keri, and the handler module are all importable.
export PYTHONPATH="/var/task:/opt/python:${PYTHONPATH:-}"
exec python -m uvicorn mailbox_handler:app --host 0.0.0.0 --port "${AWS_LWA_PORT:-8080}"
