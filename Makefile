# DEBOS_OPTS can be overridden with:
#     make DEBOS_OPTS=... all
# USE_CONTAINER can be set to yes/no/auto (default: auto)
#     make USE_CONTAINER=yes all    # Force container use
#     make USE_CONTAINER=no all     # Force native debos

# To build large images, the debos resource defaults are not sufficient. These
# provide defaults that work for us as universally as we can manage.
FAKEMACHINE_BACKEND = $(shell [ -c /dev/kvm ] && echo kvm || echo qemu)
DEBOS_OPTS := --fakemachine-backend $(FAKEMACHINE_BACKEND) --memory 1GiB --scratchsize 4GiB

# Container support: auto-detect if debos is available, otherwise use container
USE_CONTAINER ?= auto
CONTAINER_IMAGE ?= ghcr.io/go-debos/debos:latest

ifeq ($(USE_CONTAINER),auto)
    ifdef GITHUB_ACTIONS
        # Disable container in GitHub Actions
        USE_CONTAINER := no
    else
        # Local development: enable container if debos not installed
        USE_CONTAINER := $(shell command -v debos >/dev/null 2>&1 && echo no || echo yes)
    endif
endif

ifeq ($(USE_CONTAINER),yes)
    # Only pass --device /dev/kvm if KVM is available on the host
    KVM_DEVICE := $(if $(wildcard /dev/kvm),--device /dev/kvm)
    # Working directory as seen from inside the container
    DEBOS_WORKDIR := /recipes
    DEBOS_CMD := docker run --rm --interactive --tty \
        $(KVM_DEVICE) \
        --user $(shell id -u) --workdir $(DEBOS_WORKDIR) \
        --mount "type=bind,source=$(CURDIR),destination=$(DEBOS_WORKDIR)" \
        --security-opt label=disable \
        $(CONTAINER_IMAGE) \
        $(DEBOS_OPTS)
else
    # Working directory for native debos
    DEBOS_WORKDIR := $(CURDIR)
    DEBOS_CMD := debos $(DEBOS_OPTS)
endif

# Use http_proxy from the environment, or apt's http_proxy if set, to speed up
# builds.
http_proxy ?= $(shell apt-config dump --format '%v%n' Acquire::http::Proxy)
export http_proxy

all: disk-ufs.img.gz disk-sdcard.img.gz

rootfs.tar: debos-recipes/qualcomm-linux-debian-rootfs.yaml
	$(DEBOS_CMD) $<

disk-ufs.img disk-ufs.img.gz: debos-recipes/qualcomm-linux-debian-image.yaml rootfs.tar
	$(DEBOS_CMD) $<

disk-sdcard.img.gz: debos-recipes/qualcomm-linux-debian-image.yaml rootfs.tar
	$(DEBOS_CMD) -t imagetype:sdcard $<

test: disk-ufs.img
	# rootfs/ is a build artifact, so should not be scanned for tests
	py.test-3 --ignore=rootfs

.PHONY: all test
