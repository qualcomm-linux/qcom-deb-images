# The QEMU tests

`ci/qemu_test.py` boots an image under QEMU and checks it from the inside. The
tests need no hardware, so `debos.yml` runs them for every image it builds,
in the build job and against the `disk-ufs.img` that job just produced.

Because they run for *every* image, they have to hold for all of them. What
differs between images is therefore described to the tests rather than
expressed by running a different set of them: a caller says how it built the
image, and the same tests assert accordingly. See
[Passing variables to the tests](#passing-variables-to-the-tests) below.

## Running them by hand

From the directory holding the image under test:

```bash
py.test-3 --verbose --capture=no --ignore=rootfs
```

The tests boot a copy-on-write overlay of `./disk-ufs.img` in the working
directory, so they need that file and leave it untouched. Dependencies are
`python3-pexpect`, `python3-pytest`, `qemu-system-arm`, `qemu-efi-aarch64` and
`qemu-utils`. The whole module shares one VM, booted and logged into once, and
the guest CPU is emulated, so expect a few minutes for the boot and seconds per
test after it.

`--capture=no` is worth passing: the tests report what they were told about the
image while the module is imported, and pytest swallows anything printed during
collection unless collection then fails.

## Passing variables to the tests

### `EXPECTED_SUITE`

The Debian suite the image was built for, as `/etc/os-release` spells it, e.g.
`trixie`. `test_suite()` checks it against `VERSION_CODENAME` — what the image
*is*, rather than how the recipe happens to name its APT sources, and it comes
from the `base-files` the bootstrap pulled in, so a suite applied only to the
later package installs is caught too.

`debos.yml` sets this for every image it builds, from the `suite` input it was
already passing to debos, so no caller has to. Running by hand:

```bash
EXPECTED_SUITE=trixie py.test-3 --verbose --capture=no --ignore=rootfs
```

There is nothing to assert when it is unset — every image is some suite — so
the check is skipped instead. Don't pass `sid` or `unstable`: those track
whichever codename is next, and `/etc/os-release` records neither, so the check
would fail on a perfectly good image.

### How they are passed

`debos.yml` takes a `qemu_test_env` input — `NAME=value` lines, one per line —
so that a caller which builds an image a certain way says so through the input
rather than by running its own test files. It hands the whole set to pytest as
one `QEMU_TEST_ENV` payload, set on the test step only, and
`image_environment()` in `ci/qemu_test.py` splits it at import.

When `QEMU_TEST_ENV` is set it is the *whole* of what the tests are told — the
ambient environment is ignored, so a stray `EXPECTED_*` on a runner cannot
contribute — and when it is unset the variables are read from the environment
as normal, which is what running by hand does.

Either way the log lists every variable the tests understand, with where it was
read from and whether it was set. A line which is not an assignment, or which
sets a name no test reads, fails the run rather than being dropped silently:
the variables are whitelisted in `KNOWN`, because a name nothing reads is a
mistake in the caller rather than a no-op.

## Reference

- `ci/qemu_test.py` — the tests themselves, and `image_environment()`, which
  turns the `QEMU_TEST_ENV` payload into the variables they read.
- `.github/workflows/debos.yml` — runs them for every image it builds, and
  passes `EXPECTED_SUITE` for all of them.
- [`docs/snapshot.md`](snapshot.md) — the `snapshot` build option.
