# Installer

`installer/` holds a [live-build](https://salsa.debian.org/live-team/live-build)
configuration for a Debian installer ISO.

This is an early prototype. It builds a stock debian-installer, with the
upstream d-i kernel and no custom kernel or DTB selection. It does not yet use
the kernel or root filesystem of the image it is built alongside.

## Requirements

Build on a Debian host with:

```bash
apt -y install live-build xorriso
```

live-build runs natively, not in the debos container, and has to run as root.

## Build the ISO

```bash
make installer
```

This runs `installer/scripts/build.sh` through `sudo`, unless make is already
running as root. The script accepts these options, which you can pass in
`INSTALLER_OPTS`:

| Option | Default | Description |
| ------ | ------- | ----------- |
| `--arch` | `arm64` | target architecture |
| `--suite` | `trixie` | Debian suite, e.g. `forky` |
| `--installer` | `cdrom` | installer type, `cdrom` or `netinst` |

For example:

```bash
make INSTALLER_OPTS="--suite forky --installer netinst" installer
```

Each build starts in a new `installer/build/` directory and writes the ISO
there. The script fails if `installer/build/` already exists, so remove it
before building again:

```bash
make clean-installer
```

## Customizations

`installer/overlays/$ARCH/` follows the layout of live-build's `config/`
directory, and is copied over the generated configuration after `lb config`.

The arm64 overlay contains a `debian-cd` udeb include list for the `cdrom`
installer. live-build only ships these lists for amd64, and the build fails
without one.

## CI

`.github/workflows/installer.yml` builds the `cdrom` ISO after the image
builds and publishes it next to the image it was built for as
`$SUITE-installer-cdrom.iso`.
