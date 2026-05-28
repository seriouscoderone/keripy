SRCDIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../src)
LIBDIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/lib)
SAMDIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))

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
