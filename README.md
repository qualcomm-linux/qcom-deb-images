# Qualcomm Linux deb images

A collection of recipes to build Qualcomm Linux images for deb based operating systems. The current focus of this project is to provide mainline centric images for Qualcomm® IoT platforms as to demonstrate the state of upstream open source software, help developers getting started, and support continuous development and continuous testing efforts.

Initially, this repository provides [debos](https://github.com/go-debos/debos) recipes based on Debian trixie for boards such as the Qualcomm RB3 Gen 2.

We are also working towards providing ready-to-use, pre-built images – stay tuned!

## Branches

main: Primary development branch. Contributors should develop submissions based on this branch, and submit pull requests to this branch.

## Requirements

[debos](https://github.com/go-debos/debos) is required to build the debos recipes. Recent debos packages should be available in Debian and Ubuntu repositories; there are 
[debos installation instructions](https://github.com/go-debos/debos?tab=readme-ov-file#installation-from-source-under-debian) on the project's page, notably for Docker images and to build debos from source. Make sure to use at least version 1.1.5 which supports setting the sector size.

[qdl](https://github.com/linux-msm/qdl) is typically used for flashing. While recent versions are available in Debian and Ubuntu, make sure to use at least version 2.1 as it contains important fixes.

## Usage

To build flashable assets, run debos as follows:
```bash
# build tarballs of the root filesystem and DTBs
debos debos-recipes/qualcomm-linux-debian-rootfs.yaml

# build disk and filesystem images from the root filesystem; the default is to
# build an UFS image
debos debos-recipes/qualcomm-linux-debian-image.yaml

# build flashable assets from the DTBs and UFS filesystem images; currently these
# are only built for the RB3 Gen2 Vision Kit board
debos debos-recipes/qualcomm-linux-debian-flash.yaml
```

### Debos tips

By default, debos will try to pick a fast build backend; it will try to use its KVM backend ("-b kvm") when available, and otherwise an UML environment ("-b uml"). If none of these work, a solid backend is QEMU ("-b qemu"); because the target images are arm64, this can be really slow when building from another architecture such as amd64.

To build large images, the debos resource defaults might not be sufficient. Consider raising the default debos memory and scratchsize settings. This should provide a good set of minimum defaults:
```bash
debos --fakemachine-backend qemu --memory 1GiB --scratchsize 4GiB debos-recipes/qualcomm-linux-debian-image.yaml
```

### Build options

A few options are provided in the debos recipes; for the root filesystem recipe:
- experimentalkernel: update the linux kernel to the version from experimental; default: don't update the kernel
- xfcedesktop: install a Xfce desktop environment; default: console only environment

For the image recipe:
- dtb: override the firmware provided device tree with one from the linux kernel, e.g. `qcom/qcs6490-rb3gen2.dtb`; default: don't override
- imagetype: either `ufs` (the default) or (`sdcard`); UFS images are named disk-ufs.img and use 4096 bytes sectors and SD card images are named disk-sdcard.img and use 512 bytes sectors
- imagesize: set the output disk image size; default: `4GiB`

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

## Flashing Instructions
### Overview

The `disk-sdcard.img` disk image can simply be written to a SD card, albeit most Qualcomm boards boot from internal storage by default. With an SD card, the board will use boot firmware from internal storage (eMMC or UFS) and do an EFI boot from the SD card if the firmware can't boot from internal storage.

If there is no need to update the boot firmware, the `disk-ufs.img` disk image can also be flashed on the first LUN of the internal UFS storage with [qdl](https://github.com/linux-msm/qdl). Create a `rawprogram-ufs.xml` file as follows:
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
Make sure to use `prog_firehose_ddr.elf` for the target platform, such as this [version from the QCM6490 boot binaries](https://softwarecenter.qualcomm.com/download/software/chip/qualcomm_linux-spf-1-0/qualcomm-linux-spf-1-0_test_device_public/r1.0_00058.0/qcm6490-le-1-0/common/build/ufs/bin/QCM6490_bootbinaries.zip).

To flash a complete set of assets on UFS internal storage, put the board in EDL mode and run:
```bash
# use the RB3 Gen2 Vision Kit flashable assets
cd flash_rb3gen2-vision-kit
qdl --storage ufs prog_firehose_ddr.elf rawprogram[0-9].xml patch[0-9].xml
```

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

### RB1 instructions (alpha)

The RB1 board boots from eMMC by default and uses an Android style boot architecture. Read-on to flash a disk image to the eMMC storage of the RB1 board and emulate an UEFI boot architecture.

#### Build disk-sdcard.img with debos
As above, build a SD card image as it's using a 512 sector size, like eMMC on the RB1:
```bash
debos \
    --fakemachine-backend qemu \
    --memory 1GiB \
    --scratchsize 4GiB \
    -t xfcedesktop:true \
    debos-recipes/qualcomm-linux-debian-rootfs.yaml
debos \
    --fakemachine-backend qemu \
    --memory 1GiB \
    --scratchsize 4GiB \
    -t dtb:qcom/qrb2210-rb1.dtb \
    -t imagetype:sdcard \
    debos-recipes/qualcomm-linux-debian-image.yaml
```

#### Build U-Boot with RB1 support

U-Boot will be chainloaded from the first Android boot partition.

A convenience shell script is provided to checkout the relevant U-Boot branch and to build U-Boot for RB1 and wrap it in an Android boot image.

```bash
sudo apt install git build-essential crossbuild-essential-arm64 flex bison \
    libssl-dev gnutls-dev mkbootimg

scripts/build-u-boot-rb1.sh
```

#### Build an upstream Linux kernel to workaround boot issues

Linux 6.14 or later will just work, but 6.13 kernels need `CONFIG_CLK_QCM2290_GPUCC=m` ([upstream submission](https://lore.kernel.org/linux-arm-msm/20250214-rb1-enable-gpucc-v1-1-346b5b579fca@linaro.org/))

1. install build-dependencies, get latest kernel (or a stable one)
    ```bash
    sudo apt install git flex bison bc libelf-dev libssl-dev
    git clone --depth=1  https://github.com/torvalds/linux
    make defconfig
    make deb-pkg -j$(nproc)
    ```

1. on an arm64 capable machine, chroot into the disk image's root filesystem, mount the ESP and install the kernel
    ```bash
    # this mounts the image and starts a shell in the chroot
    host% sudo scripts/disk-image-edit.sh disk-sdcard.img 512
    chroot#

    # in another shell on the host, copy the kernel .deb to /mnt/root
    host% chroot sudo cp linux-image-6.13.0_6.13.0-1_arm64.deb /mnt/root/

    # from within the chroot, mount ESP and install the kernel
    chroot# mount /boot/efi
    chroot# dpkg -i /root/linux-image-6.13.0_6.13.0-1_arm64.deb
    # uncompress the kernel as systemd-boot doesn’t handle these
    chroot# zcat /boot/efi/*/6.13*/linux >/tmp/linux
    chroot# mv /tmp/linux /boot/efi/*/6.13*/linux
    # update systemd entry to point at uncompressed kernel
    vi /boot/efi/…
    chroot# umount /boot/efi

    # leave chroot and unmount image
    chroot# exit
    ```

#### Extract the root and ESP partitions from the disk image

This will create disk-sdcard.img1 and disk-sdcard.img2:
```bash
fdisk -l disk-sdcard.img | sed -n '1,/^Device/ d; p' |
    while read name start end sectors rest; do
        dd if=disk-sdcard.img of="${name}" bs=512 skip="${start}" count="${sectors}"
    done
```

#### Prepare a flashable image

1. download and unpack the [Linux eMMC RB1 recovery image version 23.12 from Linaro](https://releases.linaro.org/96boards/rb1/linaro/rescue/23.12/)

1. edit rawprogram0.xml and change the filename for the following partitions to these values to match files generated earlier:

|label|filename|
|---|---|
|`boot_a`|`u-boot-abootimg.img`|
|`esp`|`disk-sdcard.img1`|
|`rootfs`|`disk-sdcard.img2`|

#### Flash the image

You probably want to connect to the serial port during the whole process, to follow what’s happening on the target. Plug the type-B USB cable to your host and access the serial console with 115200 8N1, e.g. with screen:

Linux (tweak the name of the device):
```bash
screen /dev/ttyUSB* 115200
```
macOS (tweak the name of the device):
```bash
screen /dev/cu.usbserial-* 115200
```

Make sure that the 6th switch on the `DIP_SW_1` bank next to the eMMC is `ON` as to use the USB type-C port for flashing.

Put the board in "emergency download" mode (EDL) by removing any cable from the USB type-C port, and pressing the `F_DL` button while turning the power on.

Connect a cable from the flashing host to the USB type-C port on the board.

Unpack the pre-built tarball and run:
```bash
qdl --storage emmc prog_firehose_ddr.elf rawprogram*.xml patch*.xml
```

You should see:
```
Waiting for EDL device
waiting for programmer...
flashed "xbl_a" successfully
[...]
partition 0 is now bootable
```

And the board should boot to a LightDM greeter on HDMI. Login to the serial console or Xfce session with user / password debian / debian.

The USB ports and Ethernet should work after flipping the 6th switch on the `DIP_SW_1` bank next to the eMMC to `OFF`.

#### Installing “qbootctl” to reset the reboot counter on boot

In the installed Debian system, install “qbootctl” to make the current Android boot image as a successful:
```bash
sudo apt install qbootctl
```

## Development

Want to join in the development? Changes welcome! See [CONTRIBUTING.md file](CONTRIBUTING.md) for step by step instructions.

## Reporting Issues

We'd love to hear if you run into issues or have ideas for improvements. [Report an Issue on GitHub](../../issues) to discuss, and try to include as much information as possible on your specific environment.

## License

This project is licensed under the [BSD-3-clause License](https://spdx.org/licenses/BSD-3-Clause.html). See [LICENSE.txt](LICENSE.txt) for the full license text.
