#!/usr/bin/env bash
# End-to-end live witness smoke test.
#
# Exercises the witness via two paths:
#   1. kli (real-world client) — init, oobi resolve, incept with witness.
#      This is what an actual KERI user/operator would do.
#   2. Direct Python flow — programmatic inception + POST /receipts +
#      receipt parse + signature verification. Rigorously asserts the
#      witness produced a cryptographically valid receipt.
#
# Default target is the deployed witness at https://witness.keri.host.
# Override via WITNESS_URL env var (e.g. http://localhost:3000).
#
# Requires: kli on PATH (pip install keri or run from keripy source tree).

set -euo pipefail

WITNESS_URL="${WITNESS_URL:-https://witness.keri.host}"
TEST_DIR="$(mktemp -d -t witness-live-test-XXXXXX)"
ALICE_NAME="alice"
ALICE_ALIAS="alice"
SALT="$(python3 -c 'from keri.core.signing import Salter; print(Salter().qb64)')"

trap 'rm -rf "$TEST_DIR"' EXIT
export HOME="$TEST_DIR"

step()  { printf '\n==> %s\n' "$*"; }
ok()    { printf '    \xe2\x9c\x93 %s\n' "$*"; }   # checkmark
warn()  { printf '    ! %s\n' "$*"; }
fail()  { printf '    \xe2\x9c\x97 %s\n' "$*" >&2; exit 1; }

step "1. Discover witness AID from $WITNESS_URL/"
WIT_AID=$(curl -fsS "$WITNESS_URL/" \
    | python3 -c 'import sys,json; print(json.loads(sys.stdin.read())["witness"])')
[ -n "$WIT_AID" ] || fail "could not extract witness AID from status endpoint"
ok "witness AID = $WIT_AID"

step "2. kli init: create fresh keystore (HOME=$HOME)"
kli init --name "$ALICE_NAME" --salt "$SALT" --nopasscode >/dev/null
ok "keystore created"

step "3. kli oobi resolve: fetch + verify witness OOBI"
kli oobi resolve --name "$ALICE_NAME" \
    --oobi "$WITNESS_URL/oobi/$WIT_AID/witness" --oobi-alias witness >/dev/null
ok "OOBI resolved (witness KEL + URL binding stored locally)"

step "4. kli incept: real-world client incept with witness"
INCEPT_FILE="$TEST_DIR/incept.json"
cat > "$INCEPT_FILE" <<JSON
{
    "transferable": true,
    "wits": ["$WIT_AID"],
    "toad": 1,
    "icount": 1,
    "isith": "1",
    "ncount": 1,
    "nsith": "1"
}
JSON
# --receipt-endpoint forces use of the synchronous /receipts path
# (sam-witness does not yet implement mailbox-based async delivery).
KLI_OUT=$(kli incept --name "$ALICE_NAME" --alias "$ALICE_ALIAS" \
    --file "$INCEPT_FILE" --receipt-endpoint 2>&1)
echo "$KLI_OUT" | tail -5
ALICE_AID=$(echo "$KLI_OUT" | grep -E '^Prefix' | awk '{print $2}')
[ -n "$ALICE_AID" ] || fail "kli incept did not produce an AID"
ok "alice incepted: $ALICE_AID"

# kli incept --receipt-endpoint may report Receipts: 0 against our witness
# even though the pytest conformance suite passes. After Phase 2 we confirmed:
#   - the witness returns a valid receipt for kli's exact wire format (body
#     + CESR-ATTACHMENT header) — see test_post_receipts_kli_format
#   - the receipt parses cleanly via hab.psr.parseOne and lands in db.wigs
#   - GET /receipts then returns it (see test_get_receipts_after_post)
# The differential is hio (kli's HTTP client) vs urllib (pytest). Suspected
# hio port handling on https URLs without explicit :443, hio HTTPS + AWS
# API Gateway interaction, or Receiptor.receipt() body handling. Tracking
# as Phase 2.5 (kli/hio interop investigation) in the witness roadmap.
# Step 5 below verifies the witness side directly via Python — that path
# is authoritative for "is the witness correct".
RECEIPT_LINE=$(kli status --name "$ALICE_NAME" --alias "$ALICE_ALIAS" --verbose 2>&1 \
    | grep -E "^Receipts:" || true)
if echo "$RECEIPT_LINE" | grep -qE "[Rr]eceipts:[[:space:]]+0\b"; then
    warn "kli reports 0 receipts (Phase 2.5 known issue — witness side verified by Step 5)"
fi

step "5. Direct verification: independently exercise witness receipt flow"
python3 <<PY
import urllib.request, tempfile, os
os.environ['HOME'] = tempfile.mkdtemp()  # fresh temp Habery, not alice's
from keri.app.habbing import Habery
from keri.core.signing import Salter

WITNESS = "$WIT_AID"
URL = "$WITNESS_URL"

hby = Habery(name='probe', temp=True, salt=Salter().qb64)

# resolve witness OOBI
req = urllib.request.Request(f'{URL}/oobi/{WITNESS}/witness',
                             headers={'Accept': 'application/cesr'})
with urllib.request.urlopen(req, timeout=15) as r:
    hby.psr.parse(ims=bytearray(r.read()))
assert WITNESS in hby.kevers, "OOBI parse did not register witness"

# incept a fresh controller bob with witness in wits
bob = hby.makeHab(name='bob', transferable=True,
                  isith='1', icount=1, ncount=1, nsith='1',
                  toad=1, wits=[WITNESS])

# POST inception to /receipts
req = urllib.request.Request(
    f'{URL}/receipts',
    data=bytes(bob.makeOwnInception()),
    headers={'Content-Type': 'application/cesr', 'Accept': 'application/cesr'},
    method='POST',
)
with urllib.request.urlopen(req, timeout=30) as r:
    rct_bytes = r.read()
assert len(rct_bytes) > 0, "witness returned empty body to /receipts"
assert b'"t":"rct"' in rct_bytes, f"response is not a receipt message: {rct_bytes[:80]!r}"

# parse the receipt and confirm wig was stored
hby.psr.parse(ims=bytearray(rct_bytes))
dgkey = (bob.pre.encode(), bob.kever.serder.saidb)
wigs = hby.db.wigs.get(keys=dgkey)
assert len(wigs) >= 1, f"no wigs stored for bob {bob.pre} after parse"

# verify the signature
from keri.core.coring import Verfer
w = wigs[0]
if w.verfer is None:
    w.verfer = Verfer(qb64=WITNESS)
assert w.verfer.qb64 == WITNESS, f"wig verfer {w.verfer.qb64} != {WITNESS}"
assert w.verfer.verify(sig=w.raw, ser=bob.kever.serder.raw), "signature verification failed"
print(f'    ✓ bob = {bob.pre}')
print(f'    ✓ {len(rct_bytes)}-byte receipt returned by witness')
print(f'    ✓ wig stored, signature verifies against witness verfer')
PY

printf '\nLive witness test PASSED against %s\n' "$WITNESS_URL"
printf '  alice (kli): %s\n' "$ALICE_AID"
printf '  witness:     %s\n' "$WIT_AID"
