# Coral / Axiom — GitHub Workflow Dependencies

> **Purpose**: This file is a heads-up reference for `qcom-deb-images`
> maintainers.  Before modifying any GitHub Actions workflow listed here,
> check whether it is a dependency of a Coral workflow — breaking it will
> block Coral pre-merge validation or nightly tests.

Coral project: **QLI Open Development**
Coral base URL: <https://qswat.qualcomm.com/coral/qli-open-development/workflows/>

---

## Pre-merge

**Coral workflow**: `Debian_Pre-Merge_Workflow`

| Property | Value |
|----------|-------|
| Coral URL | <https://qswat.qualcomm.com/coral/qli-open-development/workflows/> |
| Purpose | PR-level Axiom validation before merge |
| Trigger | `pull_request` |
| Platform | `glymur-crd` |

### GitHub workflow dependency chain

```
pull_request
│
└─► build-on-pr.yml  (ID: 159226426)
    "Build on PR"
    https://github.com/qualcomm-linux/qcom-deb-images/actions/workflows/build-on-pr.yml
    │
    ├─ job: build  (matrix: suite × [trixie, forky], variant × [default, weston-multimedia])
    │   └─► debos.yml  (ID: 151938321)  "Build debos recipe"
    │       └─ job: build-debos
    │           check-run: "Build and upload debos recipes (<suite>, <variant>)"
    │           output: artifacts_url  ──────────────────────────────────────────────────┐
    │                                                                                     │
    └─ job: schema-check                                                                  │
        └─► lava-schema-check.yml  (ID: 163868026)  "Check LAVA templates"               │
            └─ job: schema-check                                                          │
                check-run: "schema-check"                                                 │
                                                                                          │
[after build-on-pr.yml completes — workflow_run trigger]                                  │
│                                                                                         │
└─► test-on-pr.yml  (ID: 281462353)                                                       │
    "Test PR build"                                                                       │
    │                                                                                     │
    ├─ job: retrieve-build-url  (downloads artifacts_url from build-on-pr run) ◄──────────┘
    │
    ├─ job: test  (suite: trixie)
    │   └─► lava-test.yml  (ID: 193557765)  "Tests"
    │       boards_exclude: ["monaco-arduino-monza", "qrb2210-arduino-imola"]
    │       │
    │       ├─ job: prepare-job-list
    │       ├─ job: submit-job  (matrix per ci/lava/ board template)
    │       │   ★ check-run: "test (trixie) / Submit glymur-crd boot"
    │       │                  ▲▲▲  CORAL GATE CHECK-RUN  ▲▲▲
    │       └─ job: publish-test-results
    │           check-run: "Publish Tests Results"
    │
    └─ job: publish-test-results  (PR comment with LAVA job URLs)
```

### Check-run Coral gates on

| Check-run name | Workflow | Job | Workflow ID |
|----------------|----------|-----|-------------|
| `test (trixie) / Submit glymur-crd boot` | `lava-test.yml` | `submit-job` | `193557765` |

### Workflows that must not break

| Workflow file | Workflow name | Workflow ID | Role |
|---------------|--------------|-------------|------|
| `build-on-pr.yml` | Build on PR | `159226426` | Entry point — triggered on every PR |
| `debos.yml` | Build debos recipe | `151938321` | Builds rootfs + images; produces `artifacts_url` |
| `lava-schema-check.yml` | Check LAVA templates | `163868026` | Validates `ci/lava/` YAML before test submission |
| `test-on-pr.yml` | Test PR build | `281462353` | `workflow_run` bridge; submits LAVA jobs and posts check-runs back to the PR |
| `lava-test.yml` | Tests | `193557765` | Submits LAVA job for `glymur-crd`; produces the Coral gate check-run |

---

## Post-merge

**Coral workflow**: `Debian_CRM_Nightly_Test_Hyd`

| Property | Value |
|----------|-------|
| Coral URL | <https://qswat.qualcomm.com/coral/qli-open-development/workflows/> |
| Purpose | Nightly Axiom build and hardware tests after merge to `main` |
| Trigger | `workflow_run` on "qcom-next linux build" completed |
| Platform | `glymur-crd` |

### GitHub workflow dependency chain

```
[qcom-next linux build completes on main]
│
└─► build.yml  (ID: 333132766)
    "Build"
    https://github.com/qualcomm-linux/qcom-deb-images/actions/workflows/build.yml
    Guard: github.repository == 'qualcomm-linux/qcom-deb-images'
           && (workflow_dispatch || github.ref == 'refs/heads/main')
    │
    ├─ job: build  (matrix: suite × [trixie, forky], variant × [default, weston-multimedia])
    │   └─► debos.yml  (ID: 151938321)  "Build debos recipe"
    │       └─ job: build-debos
    │           check-run: "Build and upload debos recipes (<suite>, <variant>)"
    │           output: artifacts_url  ──────────────────────────────────────────┐
    │                                                                             │
    └─ job: test  (matrix: suite × [trixie, forky])                              │
        needs: build                                                              │
        └─► lava-test.yml  (ID: 193557765)  "Tests"  ◄────────────────────────────┘
            boards_exclude: ["monaco-arduino-monza", "qrb2210-arduino-imola"]
            │
            ├─ job: prepare-job-list
            ├─ job: submit-job  (matrix per ci/lava/ board template)
            │   ★ check-run: "test (trixie) / Submit glymur-crd boot"
            │                  ▲▲▲  CORAL GATE CHECK-RUN  ▲▲▲
            └─ job: publish-test-results
                check-run: "Publish Tests Results"
```

### Check-run Coral gates on

| Check-run name | Workflow | Job | Workflow ID |
|----------------|----------|-----|-------------|
| `test (trixie) / Submit glymur-crd boot` | `lava-test.yml` | `submit-job` | `193557765` |

### Workflows that must not break

| Workflow file | Workflow name | Workflow ID | Role |
|---------------|--------------|-------------|------|
| `build.yml` | Build | `333132766` | Entry point — triggered when qcom-next kernel build completes |
| `debos.yml` | Build debos recipe | `151938321` | Builds rootfs + images; produces `artifacts_url` |
| `lava-test.yml` | Tests | `193557765` | Submits LAVA job for `glymur-crd`; produces the Coral gate check-run |

---

## Shared dependency: `lava-test.yml` and `ci/lava/glymur-crd/`

Both Coral workflows ultimately depend on the same two components:

| Component | What it does | Impact if broken |
|-----------|-------------|-----------------|
| `.github/workflows/lava-test.yml` (ID: `193557765`) | Scans `ci/lava/`, builds board matrix, submits LAVA jobs, publishes check-runs | Coral gate check-run never appears → both workflows blocked |
| `ci/lava/glymur-crd/boot.yaml` | LAVA job definition for the `glymur-crd` board | `glymur-crd` dropped from matrix → Coral gate check-run never appears |

**Key `lava-test.yml` behaviours to preserve**:
- Check-run name format: `Submit <board> <test-name>` — Coral matches on the
  exact string `test (trixie) / Submit glymur-crd boot`.
- `result_file_name` pattern: `test-results-<suite>-<slug>` — used by
  `publish-test-results` to download artifacts; changing it breaks result
  publishing.
- `boards_exclude` must **not** include `glymur-crd` in either caller
  (`build.yml` or `test-on-pr.yml`).

---

## Quick-reference: Coral gate check-run

Both Coral workflows gate on the **same** check-run name:

```
test (trixie) / Submit glymur-crd boot
```

Produced by: `lava-test.yml` → job `submit-job` → matrix entry for
`ci/lava/glymur-crd/boot.yaml`, suite `trixie`.
