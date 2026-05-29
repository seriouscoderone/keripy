#!/usr/bin/env bash
# Quick live smoke test for mailbox.keri.host
set -euo pipefail
MAILBOX_URL="${MAILBOX_URL:-https://mailbox.keri.host}"

echo "=== GET / ==="
curl -sf "${MAILBOX_URL}/"
echo

echo "=== GET /oobi (head) ==="
curl -sf "${MAILBOX_URL}/oobi" | head -c 400
echo "..."

echo "=== OOBI roles advertised ==="
curl -sf "${MAILBOX_URL}/oobi" | grep -oE '"role":"[^"]*"' | sort -u

echo
echo "=== pytest test_live.py -v ==="
exec pytest "$(dirname "$0")/test_live.py" -v
