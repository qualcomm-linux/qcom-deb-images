"""Tests that are entirely qemu based, so do not require test hardware"""

# Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
# SPDX-License-Identifier: BSD-3-Clause

import os
import shutil
import signal
import subprocess
import sys
import tempfile

import pexpect
import pytest


def _cow_disk(image, tmpdir):
    """A copy-on-write overlay on top of image, so that a test never modifies
    the image it was given"""
    qcow_path = os.path.join(tmpdir, "disk1.qcow")
    subprocess.run(
        [
            "qemu-img",
            "create",
            "-b",
            os.path.join(os.getcwd(), image),
            "-f",
            "qcow",
            "-F",
            "raw",
            qcow_path,
        ],
        check=True,
    )
    return qcow_path


def _spawn(command, args):
    """Start qemu and hand back its serial console"""
    child = pexpect.spawn(
        command,
        args,
        # Emulated ARM (no KVM) on a loaded CI runner is slow, so give
        # every expect() a generous default. The initial boot still
        # overrides this with a longer per-call timeout below.
        timeout=120,
    )
    child.logfile = sys.stdout.buffer
    return child


def _e2fsprogs(tool):
    """Path of an e2fsprogs tool; these live in /usr/sbin, which is not in
    PATH for regular users"""
    return shutil.which(tool, path=f"{os.defpath}:/usr/sbin:/sbin") or tool


def _debugfs(partition, command):
    """Run a debugfs command on an ext4 partition image; this reads the
    filesystem without needing root or a loop mount"""
    return subprocess.run(
        [_e2fsprogs("debugfs"), "-R", command, partition],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _extract_boot_files(partition, destdir):
    """Copy the kernel and initrd out of an ext4 root partition image

    armhf images are not booted by EFI and carry neither an ESP nor a
    bootloader, so qemu has to be handed the kernel and initrd directly.
    """
    versions = []
    for line in _debugfs(partition, "ls -p /boot").splitlines():
        # debugfs "ls -p" prints /inode/mode/uid/gid/name/size/
        fields = line.split("/")
        if len(fields) > 5 and fields[5].startswith("vmlinuz-"):
            versions.append(fields[5].removeprefix("vmlinuz-"))
    assert versions, f"no kernel in /boot of {partition}"
    # these images ship a single kernel; if that ever changes, the last one
    # sorts close enough to the newest to boot
    version = sorted(versions)[-1]

    kernel = os.path.join(destdir, "vmlinuz")
    initrd = os.path.join(destdir, "initrd.img")
    _debugfs(partition, f"dump /boot/vmlinuz-{version} {kernel}")
    _debugfs(partition, f"dump /boot/initrd.img-{version} {initrd}")
    return kernel, initrd


def _fs_uuid(partition):
    """UUID of the filesystem in a partition image, to boot it by UUID like
    the image's own fstab does"""
    output = subprocess.run(
        [_e2fsprogs("dumpe2fs"), "-h", partition],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    for line in output.splitlines():
        if line.startswith("Filesystem UUID:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"no filesystem UUID in {partition}")


def _shutdown(child):
    # No need to be nice; that would take time
    child.kill(signal.SIGKILL)

    # If this blocks then we have a problem. Better to hang than build up
    # excess qemu processes that won't die.
    child.wait()


@pytest.fixture
def vm():
    """A pexpect.spawn object attached to the serial console of a VM freshly
    booting with a CoW base of disk-ufs-arm64.img"""
    with tempfile.TemporaryDirectory() as tmpdir:
        qcow_path = _cow_disk("disk-ufs-arm64.img", tmpdir)
        child = _spawn(
            "qemu-system-aarch64",
            [
                "-cpu",
                "cortex-a57",
                "-m",
                "2048",
                "-M",
                "virt",
                "-drive",
                f"if=none,file={qcow_path},format=qcow,id=disk1,cache=unsafe",
                "-device",
                "virtio-scsi-pci,id=scsi1",
                "-device",
                "scsi-hd,bus=scsi1.0,drive=disk1,physical_block_size=4096,logical_block_size=4096",
                "-nographic",
                "-bios",
                "/usr/share/AAVMF/AAVMF_CODE.fd",
            ],
        )
        yield child

        _shutdown(child)


@pytest.fixture
def vm_armhf():
    """A pexpect.spawn object attached to the serial console of a VM freshly
    booting with a CoW base of disk-ufs-armhf.img"""
    image = "disk-ufs-armhf.img"
    # an armhf image has no ESP, so its root filesystem is the first partition
    partition = "disk-ufs-armhf.img1"
    for path in (image, partition):
        if not os.path.exists(path):
            pytest.skip(f"{path} has not been built")

    with tempfile.TemporaryDirectory() as tmpdir:
        qcow_path = _cow_disk(image, tmpdir)
        kernel, initrd = _extract_boot_files(partition, tmpdir)
        child = _spawn(
            "qemu-system-arm",
            [
                "-cpu",
                "cortex-a15",
                "-m",
                "2048",
                # Debian's ARM multiplatform kernel is not LPAE, so it cannot
                # address the PCIe window that the virt machine places above
                # 4GiB by default; highmem=off keeps everything below it
                "-M",
                "virt,highmem=off",
                # nothing in the image boots itself, see _extract_boot_files()
                "-kernel",
                kernel,
                "-initrd",
                initrd,
                "-append",
                f"root=UUID={_fs_uuid(partition)} rw console=ttyAMA0",
                "-drive",
                f"if=none,file={qcow_path},format=qcow,id=disk1,cache=unsafe",
                "-device",
                "virtio-scsi-pci,id=scsi1",
                "-device",
                "scsi-hd,bus=scsi1.0,drive=disk1,physical_block_size=4096,logical_block_size=4096",
                "-nographic",
            ],
        )
        yield child

        _shutdown(child)


def _first_login(vm, timeout):
    """Log in as the default user, which walks through the mandatory password
    reset of a freshly built image"""
    # an image whose initramfs cannot mount the root filesystem lands in a
    # rescue shell and would otherwise keep the test waiting for the full
    # timeout, so catch that and report what actually happened
    prompt = vm.expect_exact(["debian login:", "(initramfs)"], timeout=timeout)
    assert prompt == 0, "the initramfs could not mount the root filesystem"

    vm.send("debian\r\n")
    vm.expect_exact("Password:")
    vm.send("debian\r\n")
    vm.expect_exact("You are required to change your password immediately")
    vm.expect_exact("Current password:")
    vm.send("debian\r\n")
    vm.expect_exact("New password:")
    vm.send("new password\r\n")
    vm.expect_exact("Retype new password:")
    vm.send("new password\r\n")
    vm.expect_exact("debian@debian:~$")


def test_password_reset_required(vm):
    """On first login, there should be a mandatory reset password flow"""
    # https://github.com/qualcomm-linux/qcom-deb-images/issues/69

    # This takes a minute or two on a ThinkPad T14s Gen 6 Snapdragon
    _first_login(vm, timeout=240)

    # The /boot/efi/loader/random-seed file should not be readable to users
    # https://github.com/qualcomm-linux/qcom-deb-images/issues/279
    vm.send("journalctl | grep 'is world accessible, which is a security hole' || echo not found\r\n")
    # Need to match twice because of the serial echo of the command above
    vm.expect_exact("not found")
    vm.expect_exact("not found")


def test_armhf_image_boots(vm_armhf):
    """An armhf image should boot to a login prompt on an armhf userland"""
    # 32-bit ARM is emulated with TCG even on an arm64 host, so this is
    # several times slower than the arm64 boot above
    _first_login(vm_armhf, timeout=900)

    vm_armhf.send("dpkg --print-architecture\r\n")
    vm_armhf.expect_exact("armhf")
