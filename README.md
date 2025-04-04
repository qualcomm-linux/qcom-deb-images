# Qualcomm Linux deb images

A collection of recipes to build Qualcomm Linux images for deb based operating systems. The current focus of this project is to provide mainline centric images for Qualcomm® IoT platforms as to demonstrate the state of upstream open source software, help developers getting started, and support continuous development and continuous testing efforts.

Initially, this repository provides [debos](https://github.com/go-debos/debos) recipes based on Debian trixie for boards such as the Qualcomm RB3 Gen 2.

We are also working towards providing ready-to-use, pre-built images – stay tuned!

## Branches

main: Primary development branch. Contributors should develop submissions based on this branch, and submit pull requests to this branch.

## Requirements

[debos](https://github.com/go-debos/debos) is required to build the debos recipes. Recent debos packages should be available in Debian and Ubuntu repositories; there are 
[debos installation instructions](https://github.com/go-debos/debos?tab=readme-ov-file#installation-from-source-under-debian) on the project's page, notably for Docker images and to build debos from source. Make sure to use at least version 1.1.5 which supports setting the sector size.

[qdl](https://github.com/linux-msm/qdl) is typically used for flashing. While recent versions are available in Debian and Ubuntu, make sure you have at least version 2.1 as it contains important fixes.

## Usage

To build a disk image, run debos as follows:
```bash
# build a root filesystem tarball
debos debos-recipes/qualcomm-linux-debian-rootfs.yaml

# build a disk image from the root filesystem
debos debos-recipes/qualcomm-linux-debian-image.yaml
```

### Build backends

By default, debos will try to pick a fast build backend; it will try to use its KVM backend ("-b kvm") when available, and otherwise an UML environment ("-b uml"). If none of these work, a solid backend is QEMU ("-b qemu"). Because the target images are arm64, this can be really slow, especially when building from another architecture such as amd64.

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
- image: set the output disk image filename; default: `disk-4096.img`
- imagesize: set the output disk image size; default: `4GiB`

These can be passed as follows:
```bash
debos -t xfcedesktop:true -t experimentalkernel:true debos-recipes/qualcomm-linux-debian-rootfs.yaml
debos -t dtb:qcom/qcs6490-rb3gen2.dtb debos-recipes/qualcomm-linux-debian-image.yaml
```

## Flashing Instructions
### Overview

Once a disk image is created, it is suitable for putting on an SD card, albeit most Qualcomm boards boot from internal storage by default. The disk image can also be flashed on the internal storage of your board with [qdl](https://github.com/linux-msm/qdl).

These images don't currently ambition to provide early boot assets such as boot firmware or data for other partitions containing board specific configuration or coprocessor firmware. Instead, start by provisioning an image with these early boot assets, such as the Yocto-based Qualcomm Linux images, and then flashing a debos generated image on top. Standalone, ready to flash (but probably not Debian based) images of the boot assets are planned to be made available publicly – stay tuned!

Depending on the target board and target boot media, it's also necessary to use the right sector size for the image: typically 512B vs 4096B. SD cards and eMMC typically use the historical 512B sector size, while UFS storage uses 4096B sector size. debos has just gained support for configurable sector sizes, but that requires building it from source; alernatively, you can post-process the image with a conversation script as explained below.

### RB3 Gen2 instructions

The RB3 Gen2 board boots from UFS by default. To flash a disk image to the UFS storage of the RB3 Gen2 board:
1. provision some known good early boot assets by flashing the Yocto edition of [Qualcomm Linux](https://www.qualcomm.com/developer/software/qualcomm-linux)
1. create a `rawprogram-ufs.xml` file instructing QDL to flash to the first UFS LUN (LUN0):
    ```xml
    <?xml version="1.0" ?>
    <data>
      <program SECTOR_SIZE_IN_BYTES="4096" file_sector_offset="0" filename="disk-4096.img" label="image" num_partition_sectors="0" partofsingleimage="false" physical_partition_number="0" start_sector="0"/>
    </data>
    ```
1. put the board in "emergency download" mode (EDL) by removing any cable from the USB type-C port, and pressing the `F_DL` button while turning the power on
1. connect a cable from the flashing host to the USB type-C port on the board
1. run qdl to flash the image:
    ```bash
    qdl prog_firehose_ddr.elf rawprogram-ufs.xml
    ```
The `prog_firehose_ddr.elf` payload is part of the the Yocto Qualcomm Linux image download.

## Development

Want to join in the development? Changes welcome! See [CONTRIBUTING.md file](CONTRIBUTING.md) for step by step instructions.

## Reporting Issues

We'd love to hear if you run into issues or have ideas for improvements. [Report an Issue on GitHub](../../issues) to discuss, and try to include as much information as possible on your specific environment.

## License

This project is licensed under the [BSD-3-clause License](https://spdx.org/licenses/BSD-3-Clause.html). See [LICENSE.txt](LICENSE.txt) for the full license text.
