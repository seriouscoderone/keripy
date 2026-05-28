SRCDIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../src)
LIBDIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/lib)
SAMDIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))

build-WitnessFunction:
	python3 -m pip install -r requirements.txt -t $(ARTIFACTS_DIR)/ --break-system-packages
	cp -r $(SRCDIR)/keri $(ARTIFACTS_DIR)/keri
	cp $(SAMDIR)/bootstrap.py $(ARTIFACTS_DIR)/bootstrap.py
	cp $(SAMDIR)/witness_handler.py $(ARTIFACTS_DIR)/witness_handler.py
	cp $(LIBDIR)/libsodium.so.* $(ARTIFACTS_DIR)/
	cd $(ARTIFACTS_DIR) && ln -sf libsodium.so.26 libsodium.so
	mkdir -p $(ARTIFACTS_DIR)/lib
	cp $(LIBDIR)/libsodium.so.* $(ARTIFACTS_DIR)/lib/
	cd $(ARTIFACTS_DIR)/lib && ln -sf libsodium.so.26 libsodium.so
