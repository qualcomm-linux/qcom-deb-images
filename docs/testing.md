# Testing

Every image built by CI is flashed onto real boards in the LAVA lab and tested
there. This page describes how that is wired up and how to change it.

## The pipeline

```
build workflow  ──▶  test.yml  ──▶  test-distro.yml  ──▶  LAVA
(build.yml,          picks the      boot, then the
 linux.yml,          boards         tests on whatever
 build-debian.yml)                  booted
```

* **`test.yml`** is the entry point every build workflow calls. It reads the
  boards to test from `.github/lava-boards.yml`, pins the two external
  revisions the jobs depend on, and publishes the JUnit results once the tests
  are done.
* **`test-distro.yml`** renders the LAVA job definitions, submits them, waits
  for them and writes the summary. It runs the boot jobs first and then the
  `pre-merge` tests on every board that booted — a board that fails to boot no
  longer stops the others from being tested.
* The job definitions themselves are **not** in this repository. They are
  rendered by [lava-test-plans][ltp] from the `qcom-deb-images` project, which
  holds the per-board flashing parameters, and the tests come from
  [qcom-linux-testkit][testkit].

[ltp]: https://github.com/qualcomm-linux/lava-test-plans
[testkit]: https://github.com/qualcomm-linux/qcom-linux-testkit

## Which boards are tested

`.github/lava-boards.yml` maps a kernel flavour to the boards tested with it.
It is the only place board coverage is described: the daily builds, the weekly
mainline build and the pull request and push builds all read it.

A board name there is a device in the `qcom-deb-images` project of
lava-test-plans, i.e. a file under
`lava_test_plans/projects/qcom-deb-images/devices/`. Adding a board means
adding that device file first.

## Which tests run

After the boot test, each board runs the `pre-merge` plans shared with
meta-qcom, which live in lava-test-plans under `lava_test_plans/testcases/`:
`pre-merge-basic`, `pre-merge-bt` and `pre-merge-display-gfx`.

Only `pre-merge-basic` actually runs today. The bluetooth, audio and display
plans ask for a lab fixture through a LAVA tag (`has-bt`, `display`), and a job
whose tag no device carries does not fail, it waits — so every board lists them
in `EXCLUDED_TESTPLANS` in its device file. Give a board the hardware, delete
its entry, and the plan starts running there.

A single test that does not apply to one board is excluded the same way, with
`EXCLUDED_TESTS`, rather than being dropped from the shared list.

Two revisions are pinned. The jobs are rendered by the action that ships in
lava-test-plans itself, so its `uses:` lines in
`.github/workflows/test-distro.yml` pin the renderer and the test plans
together:

```yaml
uses: qualcomm-linux/lava-test-plans@<sha>
```

The qcom-linux-testkit revision, which is what the rendered jobs clone the
tests from, is pinned at the top of `.github/workflows/test.yml`:

```yaml
env:
  TESTKIT_REF: "testkit-YYYY.MM.DD"
```

Bump both deliberately. The testkit moves paths between releases, so an
unpinned test plan breaks without warning - `pre-merge-basic` still names the
Ethernet suite as a single test, which the testkit split into seven after
`testkit-2026.08.23`, so the pin cannot move past that tag until the shared
testcase is updated.

## Known issues

A failure that is understood and accepted is listed in
`.github/known-failures/<suite>.yaml` so that it neither keeps the results
check red nor hides a real regression. See
[`.github/known-failures/README.md`](../.github/known-failures/README.md) for
the format and what it changes.

## Job metadata

Every generated LAVA job records where it came from, so a result can be traced
back without digging through workflow logs: `build-url`, `build-run-id`,
`source-repo`, `source-branch`, `source-sha`, `suite`, `variant`, `kernel`,
`testplan`, `testkit-ref`, `lava-test-plans-ref`, `gh-workflow-url` and, for a
pull request build, `pr-number` and `pr-url`.

Recording a new key needs only a line in the `variables:` block of
`.github/workflows/test-distro.yml`: everything after the `[EXTRA_METADATA]`
header is passed through to the job metadata, so neither the action nor
lava-test-plans needs a change of its own.

## Running the tests against a branch

The build workflows carry the tests, so run one of them; see
[CONTRIBUTING.md](../CONTRIBUTING.md#running-the-github-workflows). The
cheapest full path is `gh workflow run build-debian.yml --ref "$BRANCH"`: two
image builds and one board.

The branch has to live in `qualcomm-linux/qcom-deb-images` — the LAVA
credentials are not available to forks.
