# Qualcomm Linux deb images

[![Build on push to branch](https://github.com/qualcomm-linux/qcom-deb-images/actions/workflows/build-on-push.yml/badge.svg)](https://github.com/qualcomm-linux/qcom-deb-images/actions/workflows/build-on-push.yml)
[![Daily Build](https://github.com/qualcomm-linux/qcom-deb-images/actions/workflows/build-daily.yml/badge.svg)](https://github.com/qualcomm-linux/qcom-deb-images/actions/workflows/build-daily.yml)

A collection of recipes to build Qualcomm Linux images for deb based operating systems. The current focus of this project is to provide mainline centric images for Qualcomm® IoT platforms as to demonstrate the state of upstream open source software, help developers getting started, and support continuous development and continuous testing efforts.

Initially, this repository provides [debos](https://github.com/go-debos/debos) recipes based on Debian trixie for boards such as the Qualcomm RB3 Gen 2.

We are also working towards providing ready-to-use, pre-built images – stay tuned!

## Firmware updates

On standard Linux distros like Debian, firmware updates are generally delivered via Linux Vendor Firmware Service ([LVFS](https://fwupd.org/)). The OEM/ODM vendors usually upload latest firmware releases on LVFS (refer [here](https://lvfs.readthedocs.io/en/latest/upload.html)) as cabinet (.cab) firmware archive files containing at least one metadata (.metainfo.xml) file describing the firmware update. On the device, fwupd is installed which provides a system-activated daemon listening on D-Bus for installing any firmware updates.

### Firmware delivery

On a Desktop system, its usually GNOME Software which monitors LVFS for any firmware updates and pushes to fwupd if any. On a headless system like most embedded devices, the fwupdmgr command line tool can be used to monitor LVFS for firmware updates as follows:

```bash
# Download latest metadata from LVFS
fwupdmgr refresh

# Fetch device specific firmware updates from LVFS
fwupdmgr get-updates

# Install firmware updates
fwupdmgr update
```

### Firmware on devices supported by Qualcomm Linux

The firmware on Qualcomm devices is expected to support UEFI UpdateCapsule plugin for fwupd daemon. However, currently firmware for Qualcomm devices in not available in LVFS which is a work in progress as of now. In order to play with UEFI firmware capsule updates, one can use fwupdtool to locally update firmware like on RB1 as follows:

```bash
# Transfer U-Boot firmware cabinet archive build from scripts/build-u-boot-rb1.sh to RB1
sudo fwupdtool install u-boot.cab
# It will ask for a reboot for the UEFI firmware capsule update to happen
```

## Branches

main: Primary development branch. Contributors should develop submissions based on this branch, and submit pull requests to this branch.

## Requirements

[debos](https://github.com/go-debos/debos) is required to build the debos recipes. Recent debos packages should be available in Debian and Ubuntu repositories; there are [debos installation instructions](https://github.com/go-debos/debos?tab=readme-ov-file#installation-from-source-under-debian) on the project's page, notably for Docker images and to build debos from source. Make sure to use at least version 1.1.5 which supports setting the sector size.

[qdl](https://github.com/linux-msm/qdl) is typically used for flashing. While recent versions are available in Debian and Ubuntu, make sure to use at least version 2.1 as it contains important fixes.

### Optional requirements

Building U-Boot for the RB1 requires the following build-dependencies:
```bash
apt -y install git crossbuild-essential-arm64 make bison flex bc libssl-dev gnutls-dev xxd coreutils gzip mkbootimg
```

Building a Linux kernel deb requires the following build-dependencies:
```bash
apt -y install git crossbuild-essential-arm64 make flex bison bc libdw-dev libelf-dev libssl-dev libssl-dev:arm64 dpkg-dev debhelper-compat kmod python3 rsync coreutils
```

## Usage

To build flashable assets for all supported boards, follow these steps:

1. (optional) build U-Boot for the RB1
    ```bash
    scripts/build-u-boot-rb1.sh
    ```

1. (optional) build a local Linux kernel deb from mainline with a recommended config fragment
    ```bash
    scripts/build-linux-deb.sh kernel-configs/qcom-imsdk.config kernel-configs/systemd-boot.config
    ```

1. build tarballs of the root filesystem and DTBs
    ```bash
    debos debos-recipes/qualcomm-linux-debian-rootfs.yaml

    # (optional) if you've built a local kernel, copy it to local-debs/ and run
    # this instead:
    #debos -t localdebs:local-debs/ debos-recipes/qualcomm-linux-debian-rootfs.yaml
    ```

1. build disk and filesystem images from the root filesystem tarball
    ```bash
    # the default is to build an UFS image
    debos debos-recipes/qualcomm-linux-debian-image.yaml

    # (optional) if you want SD card images or support for eMMC boards, run
    # this as well:
    debos -t imagetype:sdcard debos-recipes/qualcomm-linux-debian-image.yaml
    ```

1. build flashable assets from downloaded boot binaries, the DTBs, and pointing at the UFS/SD card disk images
    ```bash
    debos debos-recipes/qualcomm-linux-debian-flash.yaml

    # (optional) if you've built U-Boot for the RB1, run this instead:
    #debos -t u_boot_rb1:u-boot/rb1-boot.img debos-recipes/qualcomm-linux-debian-flash.yaml
    ```

1. enter Emergency Download Mode (see section below) and flash the resulting images with QDL
    ```bash
    # for RB3 Gen2 Vision Kit or UFS boards in general
    cd flash_rb3gen2-vision-kit
    qdl --storage ufs prog_firehose_ddr.elf rawprogram[0-9].xml patch[0-9].xml

    # for RB1 or eMMC boards in general
    qdl --allow-missing --storage emmc prog_firehose_ddr.elf rawprogram[0-9].xml patch[0-9].xml
    ```

### Debos tips

By default, debos will try to pick a fast build backend. It will prefer to use its KVM backend (`-b kvm`) when available, and otherwise an UML environment (`-b uml`). If none of these work, a solid backend is QEMU (`-b qemu`). Because the target images are arm64, building under QEMU can be really slow, especially when building from another architecture such as amd64.

To build large images, the debos resource defaults might not be sufficient. Consider raising the default debos memory and scratchsize settings. This should provide a good set of minimum defaults:
```bash
debos --fakemachine-backend qemu --memory 1GiB --scratchsize 4GiB debos-recipes/qualcomm-linux-debian-image.yaml
```

### Options for debos recipes

A few options are provided in the debos recipes; for the root filesystem recipe:
- `localdebs`: path to a directory with local deb packages to install (NB: debos expects relative pathnames)
- `xfcedesktop`: install a Xfce desktop environment; default: console only environment

For the image recipe:
- `dtb`: override the firmware provided device tree with one from the linux kernel, e.g. `qcom/qcs6490-rb3gen2.dtb`; default: don't override
- `imagetype`: either `ufs` (the default) or (`sdcard`); UFS images are named disk-ufs.img and use 4096 bytes sectors and SD card images are named disk-sdcard.img and use 512 bytes sectors
- `imagesize`: set the output disk image size; default: `4.5GiB`

For the flash recipe:
- `u_boot_rb1`: prebuilt U-Boot binary for RB1 in Android boot image format -- see below (NB: debos expects relative pathnames)

Here are some example invocations:
```bash
# build the root filesystem with Xfce and a kernel from experimental
debos -t xfcedesktop:true -t experimentalkernel:true debos-recipes/qualcomm-linux-debian-rootfs.yaml

# build an image where systemd overrides the firmware device tree with the one
# for RB3 Gen2
debos -t dtb:qcom/qcs6490-rb3gen2.dtb debos-recipes/qualcomm-linux-debian-image.yaml

# build an SD card image
debos -t imagetype:sdcard debos-recipes/qualcomm-linux-debian-image.yaml
```

### Flashing tips

The `disk-sdcard.img` disk image can simply be written to a SD card, albeit most Qualcomm boards boot from internal storage by default. With an SD card, the board will use boot firmware from internal storage (eMMC or UFS) and do an EFI boot from the SD card if the firmware can't boot from internal storage.

For UFS boards, if there is no need to update the boot firmware, the `disk-ufs.img` disk image can also be flashed on the first LUN of the internal UFS storage with [qdl](https://github.com/linux-msm/qdl). Create a `rawprogram-ufs.xml` file as follows:
```xml
<?xml version="1.0" ?>
<data>
  <program SECTOR_SIZE_IN_BYTES="4096" file_sector_offset="0" filename="disk-ufs.img" label="image" num_partition_sectors="0" partofsingleimage="false" physical_partition_number="0" start_sector="0"/>
</data>
```
Put the board in "emergency download mode" (EDL; see next section) and run:
```bash
qdl --storage ufs prog_firehose_ddr.elf rawprogram-ufs.xml
```
Make sure to use `prog_firehose_ddr.elf` for the target platform, such as this [version from the QCM6490 boot binaries](https://softwarecenter.qualcomm.com/download/software/chip/qualcomm_linux-spf-1-0/qualcomm-linux-spf-1-0_test_device_public/r1.0_00058.0/qcm6490-le-1-0/common/build/ufs/bin/QCM6490_bootbinaries.zip) or this [version from the RB1 rescue image](https://releases.linaro.org/96boards/rb1/linaro/rescue/23.12/rb1-bootloader-emmc-linux-47528.zip).

### Emergency Download Mode (EDL)

In EDL mode, the board will receive a flashing program over its USB type-C cable, and that program will receive data to flash on the internal storage. This is a lower level mode than fastboot which is implemented by a higher-level bootloader.

To enter EDL mode:
1. remove power to the board
1. remove any cable from the USB type-C port
1. on some boards, it's necessary to set some DIP switches
1. press the `F_DL` button while turning the power on
1. connect a cable from the flashing host to the USB type-C port on the board
1. run qdl to flash the board

NB: It's also possible to run qdl from the host while the baord is not connected, and starting the board directly in EDL mode.

## Development

Want to join in the development? Changes welcome! See [CONTRIBUTING.md file](CONTRIBUTING.md) for step by step instructions.

### Boot an image locally with QEMU (helper script)

Use the `scripts/run-qemu.py` helper to boot generated disk images under QEMU. It automatically:
- Detects your OS and locates an aarch64 UEFI firmware (Debian/Ubuntu: qemu-efi-aarch64; macOS Homebrew: edk2-aarch64).
- Presents the image via SCSI with the correct sector size (4096 for UFS, 512 for SD/eMMC).
- Creates a temporary qcow2 copy-on-write overlay by default (your base image remains unchanged).
- Provides GUI display by default (Gtk on Linux, Cocoa on macOS) and headless mode with `--headless`.

Dependencies:
- Debian/Ubuntu: `sudo apt install qemu-efi-aarch64 qemu-system-arm qemu-utils`
- macOS (Homebrew): `brew install qemu`

Basic usage:
```bash
# Auto-detects disk-ufs.img or disk-sdcard.img in the current directory
scripts/run-qemu.py

# Explicit storage type (sector size set accordingly)
scripts/run-qemu.py --storage ufs
scripts/run-qemu.py --storage sdcard

# Use a specific image path
scripts/run-qemu.py --image /path/to/disk-ufs.img

# Run headless (no GUI), with serial console on stdio
scripts/run-qemu.py --headless

# Disable the COW overlay to persist changes to the image
scripts/run-qemu.py --no-cow

# Pass extra QEMU arguments (example: 4 vCPUs and 4 GiB RAM)
scripts/run-qemu.py --qemu-args "-smp 4 -m 4096"
```

Notes:
- If neither `disk-ufs.img` nor `disk-sdcard.img` is found and `--image` is not provided, the script will exit with an error.
- On Linux, the script looks for `/usr/share/qemu-efi-aarch64/QEMU_EFI.fd`. On macOS with Homebrew, it uses `share/qemu/edk2-aarch64-code.fd` from the `qemu` formula.
- The overlay is cleaned up automatically when QEMU exits. Use `--no-cow` to make changes persistent on the base image.

## Reporting Issues

We'd love to hear if you run into issues or have ideas for improvements. [Report an Issue on GitHub](../../issues) to discuss, and try to include as much information as possible on your specific environment.

## License

This project is licensed under the [BSD-3-clause License](https://spdx.org/licenses/BSD-3-Clause.html). See [LICENSE.txt](LICENSE.txt) for the full license text.
