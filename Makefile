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
	# Only pass --device /dev/kvm if KVM is available on the host; also add
	# the device's owning group so the (non-root) container user may open it
	KVM_DEVICE := $(if $(wildcard /dev/kvm),--device /dev/kvm --group-add $(shell stat -c %g /dev/kvm))
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

# arm64 used to be the only architecture and its artifacts had no architecture
# in their name. The flashing pipeline, the published artifacts and existing
# flashing instructions still use those names, so keep them available as
# symlinks to the arm64 artifacts.
COMPAT_LINKS := rootfs.tar dtbs.tar.gz \
	disk-ufs.img disk-ufs.img1 disk-ufs.img2 \
	disk-sdcard.img disk-sdcard.img1 disk-sdcard.img2

.PHONY: all
all: arm64

.PHONY: arm64
arm64: disk-ufs-arm64.img disk-sdcard-arm64.img $(COMPAT_LINKS)

# armhf images are not built by default; ask for them with `make armhf`
.PHONY: armhf
armhf: disk-ufs-armhf.img disk-sdcard-armhf.img

rootfs-arm64.tar dtbs-arm64.tar.gz: debos-recipes/qualcomm-linux-debian-rootfs.yaml
	$(DEBOS_CMD) -t architecture:arm64 $<

rootfs-armhf.tar dtbs-armhf.tar.gz: debos-recipes/qualcomm-linux-debian-rootfs.yaml
	$(DEBOS_CMD) -t architecture:armhf $<

DISK_UFS_ARM64_IMAGES := disk-ufs-arm64.img \
	disk-ufs-arm64.img1 \
	disk-ufs-arm64.img2

$(DISK_UFS_ARM64_IMAGES): debos-recipes/qualcomm-linux-debian-image.yaml rootfs-arm64.tar
	$(DEBOS_CMD) -t architecture:arm64 $<

# armhf images have no ESP, so there is no second partition to extract
DISK_UFS_ARMHF_IMAGES := disk-ufs-armhf.img \
	disk-ufs-armhf.img1

$(DISK_UFS_ARMHF_IMAGES): debos-recipes/qualcomm-linux-debian-image.yaml rootfs-armhf.tar
	$(DEBOS_CMD) -t architecture:armhf $<

DISK_SDCARD_ARM64_IMAGES := disk-sdcard-arm64.img \
	disk-sdcard-arm64.img1 \
	disk-sdcard-arm64.img2

$(DISK_SDCARD_ARM64_IMAGES): debos-recipes/qualcomm-linux-debian-image.yaml rootfs-arm64.tar
	$(DEBOS_CMD) -t architecture:arm64 -t imagetype:sdcard $<

DISK_SDCARD_ARMHF_IMAGES := disk-sdcard-armhf.img \
	disk-sdcard-armhf.img1

$(DISK_SDCARD_ARMHF_IMAGES): debos-recipes/qualcomm-linux-debian-image.yaml rootfs-armhf.tar
	$(DEBOS_CMD) -t architecture:armhf -t imagetype:sdcard $<

rootfs.tar: rootfs-arm64.tar
	ln -sf $< $@

dtbs.tar.gz: dtbs-arm64.tar.gz
	ln -sf $< $@

disk-ufs.img disk-ufs.img1 disk-ufs.img2: disk-ufs%: disk-ufs-arm64%
	ln -sf $< $@

disk-sdcard.img disk-sdcard.img1 disk-sdcard.img2: disk-sdcard%: disk-sdcard-arm64%
	ln -sf $< $@

.PHONY: flash
flash: debos-recipes/qualcomm-linux-debian-flash.yaml dtbs.tar.gz
	$(DEBOS_CMD) $<

.PHONY: test
test: disk-ufs-arm64.img
	# rootfs/ is a build artifact, so should not be scanned for tests
	py.test-3 --ignore=rootfs

.PHONY: clean
clean:
	rm -f $(DISK_UFS_ARM64_IMAGES) $(DISK_UFS_ARMHF_IMAGES)
	rm -f $(DISK_SDCARD_ARM64_IMAGES) $(DISK_SDCARD_ARMHF_IMAGES)
	rm -f rootfs-arm64.tar rootfs-armhf.tar
	rm -f dtbs-arm64.tar.gz dtbs-armhf.tar.gz
	rm -f $(COMPAT_LINKS)
	rm -f dtb-multidtb.bin
	rm -f dtb-combineddtb.bin

.PHONY: clean-debos
clean-debos:
	rm -rf .debos-*
