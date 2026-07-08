# DEBOS_OPTS can be overridden with:
#     make DEBOS_OPTS=... all
# USE_CONTAINER can be set to yes/no/auto (default: auto)
#     make USE_CONTAINER=yes all    # Force container use
#     make USE_CONTAINER=no all     # Force native debos

# To build large images, the debos resource defaults are not sufficient. These
# provide defaults that work for us as universally as we can manage.
FAKEMACHINE_BACKEND = $(shell [ -c /dev/kvm ] && echo kvm || echo qemu)
FAKEMACHINE_OPTS ?= --fakemachine-backend $(FAKEMACHINE_BACKEND)

EXTRA_DEBOS_OPTS ?=
DEBOS_OPTS := $(FAKEMACHINE_OPTS) --memory 1GiB --scratchsize 6GiB $(EXTRA_DEBOS_OPTS)

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

# AUTO_INSTALL_DEPS=yes lets the dependency check install missing packages
# instead of failing; off by default so a local build never installs without
# being asked. CI installs everything up front instead (see debos.yml); the
# throwaway container can't install (non-root), so the check is skipped there.
AUTO_INSTALL_DEPS ?= no
CHECK_INSTALL := $(if $(filter yes,$(AUTO_INSTALL_DEPS)),--install,)

# $(call run_debos,<use-case>,<recipe>[,<extra debos opts>])
# Native builds validate (or install) the use-case's host dependencies before
# running debos. The container path runs debos directly; provisioning the
# throwaway container's tools is a separate, image-level concern.
ifeq ($(USE_CONTAINER),yes)
define run_debos
$(DEBOS_CMD) $(3) $(2)
endef
else
define run_debos
scripts/check-deps.sh $(CHECK_INSTALL) $(1) && $(DEBOS_CMD) $(3) $(2)
endef
endif

# Use http_proxy from the environment, or apt's http_proxy if set, to speed up
# builds.
http_proxy ?= $(shell apt-config dump --format '%v%n' Acquire::http::Proxy)
export http_proxy

.PHONY: all
all: disk-ufs.img disk-sdcard.img

rootfs.tar dtbs.tar.gz: debos-recipes/qualcomm-linux-debian-rootfs.yaml
	$(call run_debos,rootfs,$<)

DISK_UFS_IMAGES := disk-ufs.img \
	disk-ufs.img1 \
	disk-ufs.img2

$(DISK_UFS_IMAGES): debos-recipes/qualcomm-linux-debian-image.yaml rootfs.tar
	$(call run_debos,image,$<)

DISK_SDCARD_IMAGES := disk-sdcard.img \
	disk-sdcard.img1 \
	disk-sdcard.img2

$(DISK_SDCARD_IMAGES): debos-recipes/qualcomm-linux-debian-image.yaml rootfs.tar
	$(call run_debos,image,$<,-t imagetype:sdcard)

.PHONY: flash
flash: debos-recipes/qualcomm-linux-debian-flash.yaml dtbs.tar.gz
	$(call run_debos,flash,$<)

.PHONY: test
test: disk-ufs.img
	# rootfs/ is a build artifact, so should not be scanned for tests
	scripts/check-deps.sh $(CHECK_INSTALL) test && py.test-3 --ignore=rootfs

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
