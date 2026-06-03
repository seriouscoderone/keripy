SRCDIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../src)
LIBDIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/lib)
SAMDIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
ROOT   := $(abspath $(SAMDIR)/..)

# Default URL for `make verify`. Override per-stack:
#   MAILBOX_URL=https://mailbox.legitim.us make verify
MAILBOX_URL ?= https://mailbox.keri.host

.PHONY: verify verify-deployed

## verify: run pytest against a single deployed mailbox (override MAILBOX_URL)
verify:
	@echo "=== verifying $(MAILBOX_URL) ==="
	cd $(ROOT) && MAILBOX_URL=$(MAILBOX_URL) pytest sam-mailbox/test_live.py -v

## verify-deployed: discover every serverless-mailbox* CFN stack in $AWS_REGION
## and run pytest against each domain. Bails on the first failure so a
## regressed pool member surfaces immediately.
##
## Requires `aws` and `jq` on PATH. AWS_REGION defaults to us-east-1.
verify-deployed:
	@echo "=== discovering deployed mailbox stacks ==="
	@AWS_REGION=$${AWS_REGION:-us-east-1}; \
	urls=$$(aws cloudformation describe-stacks --region $$AWS_REGION --output json \
	    | jq -r '.Stacks[] | select(.StackName | startswith("serverless-mailbox")) | select(.StackName | contains("CompanionStack") | not) | .Outputs[]? | select(.OutputKey=="MailboxUrl") | .OutputValue' \
	    | sort -u); \
	if [ -z "$$urls" ]; then echo "no deployed mailbox stacks found in $$AWS_REGION"; exit 1; fi; \
	for url in $$urls; do \
	    echo ""; echo "=== verifying $$url ==="; \
	    cd $(ROOT) && MAILBOX_URL=$$url pytest sam-mailbox/test_live.py -v || exit 1; \
	done; \
	echo ""; echo "✓ all deployed mailboxes verified"

build-MailboxFunction:
	python3 -m pip install -r requirements.txt -t $(ARTIFACTS_DIR)/ --break-system-packages
	cp -r $(SRCDIR)/keri $(ARTIFACTS_DIR)/keri
	cp $(SAMDIR)/bootstrap.py $(ARTIFACTS_DIR)/bootstrap.py
	cp $(SAMDIR)/mailbox_handler.py $(ARTIFACTS_DIR)/mailbox_handler.py
	cp $(LIBDIR)/libsodium.so.* $(ARTIFACTS_DIR)/
	cd $(ARTIFACTS_DIR) && ln -sf libsodium.so.26 libsodium.so
	mkdir -p $(ARTIFACTS_DIR)/lib
	cp $(LIBDIR)/libsodium.so.* $(ARTIFACTS_DIR)/lib/
	cd $(ARTIFACTS_DIR)/lib && ln -sf libsodium.so.26 libsodium.so
