# Known failures

A test that is known to fail, and whose failure is accepted for now, is listed
here so that it neither fails the "Test Results" check nor hides a real
regression behind a permanently red run.

There is one list per debian suite:

```
.github/known-failures/trixie.yaml
.github/known-failures/forky.yaml
```

The suite is what decides which list applies, because that is what the images
differ by; an entry that only applies to some kernel flavours says so with a
`kernels` filter.

## Format

A list maps a LAVA device type to the tests that are allowed to fail on it:

```yaml
qcs6490-rb3gen2:
  - test: some_failing_test
    comment: https://github.com/qualcomm-linux/qcom-deb-images/issues/1234
  - test: another_failing_test
    comment: "waiting for the firmware uprev, issue #1235"
    # without this the entry applies to every kernel flavour
    kernels: [linux-next, mainline]

# "*" applies to every device tested with this suite
"*":
  - test: test_failing_everywhere
    comment: https://github.com/qualcomm-linux/qcom-deb-images/issues/1236
```

* The device is the name the board has in `.github/lava-boards.yml`, which is
  also the column header of the summary table.
* The test name is the LAVA test case name, which is the row label of the
  summary table. The boot test is called `boot`.
* An entry listed for a device overrides the one listed under `"*"`.
* A bare string (`- some_failing_test`) is accepted as a shorthand, but write
  the `comment` — it is the only record of why the failure is accepted.
* An empty file, or one holding only comments, means no known failure.

## What it changes

In the job summary a listed failure is reported as `known failure` and counted
as a pass, and a listed test that *passes* is reported as `unexpected pass` and
counted as a failure — that is the signal the entry is stale and should be
removed.

In the "Test Results" check, `.github/scripts/apply-known-failures.py` rewrites
the LAVA JUnit files before they are published: a listed failure becomes
`<skipped type="known failure">` and a listed test that passed gains a
`<failure type="unexpected pass">`.

## Validation

`.github/workflows/known-failures.yml` runs

```
.github/scripts/apply-known-failures.py --validate
```

on every change to the lists, the script, the board table or itself. It rejects
a device or a kernel flavour that is not tested — its entries would silently
never be applied — and duplicate entries, and it warns about an entry with no
comment.

A pull request's own lists are used for its test run, so a change here takes
effect immediately; they are only accepted after the syntax check passes.
