# Workflows

This page describes when the scheduled workflows run and the constraints that
led to those times. All times are UTC unless stated. The `cron:` lines in the
workflows deliberately carry no comments; update this page in the same commit
as any change to them.

## Schedule

| Time  | Days    | Workflow                       | File                     |
|-------|---------|--------------------------------|--------------------------|
| 19:00 | Sunday  | EFS Maintenance                | `efs-maintenance.yml`    |
| 21:00 | daily   | Build (qcom-next QLI images)   | `build.yml`              |
| 23:00 | daily   | qcom-7.2 linux build           | `linux-qcom-7.2.yml`     |
| 01:00 | daily   | arduino linux build            | `linux-arduino.yml`      |
| 03:00 | daily   | qcom-laptops linux build       | `linux-qcom-laptops.yml` |
| 05:00 | daily   | linux-next linux build         | `linux-next.yml`         |
| 07:00 | daily   | Build Debian                   | `build-debian.yml`       |
| 09:00 | Monday  | mainline linux build           | `linux-mainline.yml`     |
| 11:00 | Monday  | Build U-Boot for RB1           | `u-boot.yml`             |

`stale-issues.yaml` also runs daily at 01:30, but only on a GitHub-hosted
runner and without LAVA jobs, so it is not part of the sequence above.
`linux-qcom-next.yml` is deprecated and is only manually triggered.

## Why the schedule looks like this

* The QLI images are preferred. The `qcom-next` and `qcom-7.2` images and their
  test results are shared with the test teams, who expect them around 8:00am IST
  (2:30am UTC). The LAVA tests in this repository run fully-featured test cases
  and can take a long time, so these builds start the evening before.
* Builds start two hours apart, on the hour. They share the self-hosted
  runners and the LAVA queue; spacing them out stops each workflow's LAVA jobs
  from queuing behind the previous workflows.
* The rest follow in no particular order. The project-specific kernels
  (arduino, qcom-laptops) come next, then linux-next. Vanilla Debian is the
  last daily build as it tracks upstream Debian rather than what QLI ships.
* The weekly builds run on Monday after the daily ones. The Mainline and U-Boot
  workflows only start once the daily builds are done with the runners and LAVA,
  so they don't add their jobs on top.
* EFS maintenance runs before the first build, to prune old artifacts, which is
  best avoided while a build is publishing to EFS.


## Changing the schedule

* Keep the `qcom-next` and `qcom-7.2` results landing before the 2:30am UTC
  deadline.
* Keep the two hour gap between builds and don't overlap EFS maintenance.
* Update the table above.
