# Key variables (override on the command line):
#   DEBOS_OPTS="..."           - override debos options
#   USE_CONTAINER=yes/no/auto  - control container use (default: auto)
#   DEBOS_ARGS="..."           - pass extra arguments to debos
#   KERNEL_REPO/KERNEL_REF     - kernel source for 'make kernel-deb'
#   KERNEL_PACKAGE             - kernel package name (auto-detected from local-apt-repo/)

# To build large images, the debos resource defaults are not sufficient. These
# provide defaults that work for us as universally as we can manage.
FAKEMACHINE_BACKEND = $(shell [ -c /dev/kvm ] && echo kvm || echo qemu)
DEBOS_OPTS := --fakemachine-backend $(FAKEMACHINE_BACKEND) --memory 1GiB --scratchsize 6GiB
DEBOS_ARGS ?=

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

.PHONY: all
all: disk-ufs.img disk-sdcard.img

rootfs.tar: debos-recipes/qualcomm-linux-debian-rootfs.yaml
	$(DEBOS_CMD) $(DEBOS_ARGS) $<

disk-ufs.img: debos-recipes/qualcomm-linux-debian-image.yaml rootfs.tar
	$(DEBOS_CMD) $(DEBOS_ARGS) $<

disk-sdcard.img: debos-recipes/qualcomm-linux-debian-image.yaml rootfs.tar
	$(DEBOS_CMD) $(DEBOS_ARGS) -t imagetype:sdcard $<

# Kernel build variables - override to build a specific kernel
KERNEL_REPO ?= https://github.com/torvalds/linux
KERNEL_REF ?= master
KERNEL_DIR ?=
# Set to 'yes' to use qcom-next defaults (auto-finds latest dated tag)
USE_QCOM_NEXT ?= no
# Set to 'yes' to use linux-next defaults (auto-finds latest dated tag)
USE_LINUX_NEXT ?= no
KERNEL_DEB_EXTRA_ARGS ?=

# Local APT repo directory - mirrors what CI sets up via aptlocalrepo
LOCAL_APT_REPO ?= local-apt-repo

# KERNEL_PACKAGE: auto-detected from $(LOCAL_APT_REPO) after 'make kernel-deb'.
# Override to select a specific package, or pass KERNEL_PACKAGE= to disable.
KERNEL_PACKAGE ?= $(shell find $(LOCAL_APT_REPO) -type f -name 'linux-image-*' \
    -not -name '*-dbg_*' 2>/dev/null | xargs -n1 basename 2>/dev/null | \
    cut -f1 -d_ | sort | tail -1)
ifneq ($(KERNEL_PACKAGE),)
    override DEBOS_ARGS += -t aptlocalrepo:$(DEBOS_WORKDIR)/$(LOCAL_APT_REPO) \
                           -t kernelpackage:$(KERNEL_PACKAGE)
endif

# Rebuild the APT repo index in $(LOCAL_APT_REPO).
# Requires: apt-utils (apt-ftparchive)
.PHONY: setup-apt-repo
setup-apt-repo:
	cd $(LOCAL_APT_REPO) && apt-ftparchive packages . > Packages && apt-ftparchive release . > Release
	@echo ""
	@echo "Local APT repo ready at: $(LOCAL_APT_REPO)/"
	@echo ""
	@echo "KERNEL_PACKAGE will be auto-detected; to build rootfs and images:"
	@echo "  make rootfs.tar && make disk-ufs.img disk-sdcard.img"

# Build a kernel deb package and add it to the local APT repo.
# Examples:
#   make kernel-deb USE_QCOM_NEXT=yes
#   make kernel-deb KERNEL_REPO=https://github.com/qualcomm-linux/kernel KERNEL_REF=qcom-next
#   make kernel-deb KERNEL_DIR=/path/to/linux   # use existing source
.PHONY: kernel-deb
kernel-deb:
	@if [ "$(USE_QCOM_NEXT)" = "yes" ] && [ "$(USE_LINUX_NEXT)" = "yes" ]; then \
	    echo "Error: Cannot use both USE_QCOM_NEXT=yes and USE_LINUX_NEXT=yes"; \
	    exit 1; \
	elif [ "$(USE_QCOM_NEXT)" = "yes" ]; then \
	    scripts/build-linux-deb.py \
	        --qcom-next \
	        $(if $(KERNEL_DIR),--local-dir $(KERNEL_DIR)) \
	        $(KERNEL_DEB_EXTRA_ARGS) \
	        $(sort $(wildcard kernel-configs/*.config)); \
	elif [ "$(USE_LINUX_NEXT)" = "yes" ]; then \
	    scripts/build-linux-deb.py \
	        --linux-next \
	        $(if $(KERNEL_DIR),--local-dir $(KERNEL_DIR)) \
	        $(KERNEL_DEB_EXTRA_ARGS) \
	        $(sort $(wildcard kernel-configs/*.config)); \
	else \
	    scripts/build-linux-deb.py \
	        $(if $(KERNEL_DIR),--local-dir $(KERNEL_DIR)) \
	        --repo $(KERNEL_REPO) \
	        --ref $(KERNEL_REF) \
	        $(KERNEL_DEB_EXTRA_ARGS) \
	        $(sort $(wildcard kernel-configs/*.config)); \
	fi
	mkdir -p $(LOCAL_APT_REPO)/kernel
	@# Kernel debs are created in parent dir of kernel source
	@if [ -n "$(KERNEL_DIR)" ]; then \
	    mv -v $(dir $(abspath $(KERNEL_DIR)))*.deb $(LOCAL_APT_REPO)/kernel/ 2>/dev/null || \
	    mv -v $(abspath $(KERNEL_DIR))/../*.deb $(LOCAL_APT_REPO)/kernel/; \
	else \
	    mv -v *.deb $(LOCAL_APT_REPO)/kernel/; \
	fi
	$(MAKE) setup-apt-repo

.PHONY: test
test: disk-ufs.img
	# rootfs/ is a build artifact, so should not be scanned for tests
	py.test-3 --ignore=rootfs

.PHONY: clean
clean:
	rm -f rootfs.tar
	rm -f dtbs.tar.gz
	rm -f disk-*.img disk-*.img.gz disk-*.img[0-9]
	rm -rf rootfs/
	rm -rf linux/
	rm -rf $(LOCAL_APT_REPO)/
