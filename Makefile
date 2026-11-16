# DEBOS_OPTS can be overridden with:
#     make DEBOS_OPTS=... all
# USE_CONTAINER can be set to yes/no/auto (default: auto)
#     make USE_CONTAINER=yes all    # Force container use
#     make USE_CONTAINER=no all     # Force native debos

# To build large images, the debos resource defaults are not sufficient. These
# provide defaults that work for us as universally as we can manage.
# The rootfs recipe builds the root filesystem in the scratch directory, so the
# rootfs build gets a scratch disk larger than the biggest rootfs we build
# (ROOTFS_FAKEMACHINE_OPTS). The image recipe unpacks rootfs.tar straight into
# the mounted image, so the other builds need little scratch space and use
# fakemachine's memory-backed default. debos starts the fakemachine with 2 CPUs
# by default, so give it all of the host's CPUs instead.
# In GitHub Actions, the debos builds run on the arm64-metal runners, which have
# /dev/kvm, so always use the kvm backend there: a runner without it then fails
# loudly rather than silently falling back to emulation.
ifdef GITHUB_ACTIONS
FAKEMACHINE_BACKEND = kvm
else
FAKEMACHINE_BACKEND = $(shell [ -c /dev/kvm ] && echo kvm || echo qemu)
endif
FAKEMACHINE_OPTS ?= --fakemachine-backend $(FAKEMACHINE_BACKEND) --cpus $(shell nproc)
ROOTFS_FAKEMACHINE_OPTS ?= --scratchsize 12GiB

EXTRA_DEBOS_OPTS ?=
DEBOS_OPTS := $(FAKEMACHINE_OPTS) $(EXTRA_DEBOS_OPTS)

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
	# Only pass --device /dev/kvm if KVM is available on the host; also add
	# the device's owning group so the (non-root) container user may open it
	KVM_DEVICE := $(if $(wildcard /dev/kvm),--device /dev/kvm --group-add $(shell stat -c %g /dev/kvm))
	# Working directory as seen from inside the container
	DEBOS_WORKDIR := /recipes
	DEBOS_CMD = docker run --rm --interactive --tty \
		$(KVM_DEVICE) \
		--user $(shell id -u) --workdir $(DEBOS_WORKDIR) \
		--mount "type=bind,source=$(CURDIR),destination=$(DEBOS_WORKDIR)" \
		--security-opt label=disable \
		$(CONTAINER_IMAGE) \
		$(DEBOS_OPTS)
else
	# Working directory for native debos
	DEBOS_WORKDIR := $(CURDIR)
	DEBOS_CMD = debos $(DEBOS_OPTS)
endif

# Use http_proxy from the environment, or apt's http_proxy if set, to speed up
# builds.
http_proxy ?= $(shell apt-config dump --format '%v%n' Acquire::http::Proxy)
export http_proxy

.PHONY: all
all: disk-ufs.img disk-sdcard.img

# put the rootfs defaults before EXTRA_DEBOS_OPTS, so that the caller can
# override them (debos uses the last value of an option)
rootfs.tar dtbs.tar.gz: DEBOS_OPTS = $(FAKEMACHINE_OPTS) $(ROOTFS_FAKEMACHINE_OPTS) $(EXTRA_DEBOS_OPTS)
rootfs.tar dtbs.tar.gz: debos-recipes/qualcomm-linux-debian-rootfs.yaml
	$(DEBOS_CMD) $<

DISK_UFS_IMAGES := disk-ufs.img \
	disk-ufs.img1 \
	disk-ufs.img2

$(DISK_UFS_IMAGES): debos-recipes/qualcomm-linux-debian-image.yaml rootfs.tar
	$(DEBOS_CMD) $<

DISK_SDCARD_IMAGES := disk-sdcard.img \
	disk-sdcard.img1 \
	disk-sdcard.img2

$(DISK_SDCARD_IMAGES): debos-recipes/qualcomm-linux-debian-image.yaml rootfs.tar
	$(DEBOS_CMD) -t imagetype:sdcard $<

.PHONY: flash
flash: debos-recipes/qualcomm-linux-debian-flash.yaml dtbs.tar.gz
	$(DEBOS_CMD) $<

# PYTEST_OPTS can be used to pass extra options to pytest, e.g.:
#     make PYTEST_OPTS=--verbose test
PYTEST_OPTS ?=

# run all the tests
.PHONY: test
test: test-qemu test-unit

# boot the disk images in QEMU and test them; test-ufs and test-sdcard only
# test one image each
.PHONY: test-qemu
test-qemu: test-ufs test-sdcard

.PHONY: test-ufs
test-ufs: disk-ufs.img
	py.test-3 $(PYTEST_OPTS) -k ufs ci/qemu_test.py

.PHONY: test-sdcard
test-sdcard: disk-sdcard.img
	py.test-3 $(PYTEST_OPTS) -k sdcard ci/qemu_test.py

# unit tests of the scripts; these don't need an image
.PHONY: test-unit
test-unit:
	py.test-3 $(PYTEST_OPTS) --ignore=ci/qemu_test.py ci/

.PHONY: clean
clean:
	rm -f $(DISK_UFS_IMAGES)
	rm -f $(DISK_SDCARD_IMAGES)
	rm -f rootfs.tar
	rm -f dtbs.tar.gz
	rm -f dtb-multidtb.bin
	rm -f dtb-combineddtb.bin

.PHONY: clean-debos
clean-debos:
	rm -rf .debos-*
