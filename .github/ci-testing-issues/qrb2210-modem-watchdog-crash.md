Repository: qualcomm-linux/qcom-deb-images
Title: qrb2210 boards: the modem crashes now and then during startup

On qrb2210-rb1 and qrb2210-arduino-imola the modem sometimes crashes while starting, around 55 s after boot and again around 95 s:

```
qcom_q6v5_pas 6080000.remoteproc: fatal error received: dog_hal_common.c:173:DOG detects stalled initialization, triage with IMAGE OWNER
remoteproc remoteproc0: crash detected in modem: type fatal error
```

Since testkit-2026.11.15 this fails DSP_AudioPD, which now fails on remoteproc errors in dmesg.

In push run https://github.com/qualcomm-linux/qcom-deb-images/actions/runs/44701459331 it happened on three of the six pre-merge boots of these boards. Each time, the other suite with the same kernel passed, on a different board:

| Board | Kernel | Crashed | Did not crash |
|---|---|---|---|
| qrb2210-rb1 | qcom-next | forky, rb1-06 (LAVA 593007) | trixie, rb1-08 (LAVA 593016) |
| qrb2210-rb1 | linux-next | forky, rb1-04 (LAVA 593001) | trixie, rb1-01 (LAVA 593006) |
| qrb2210-arduino-imola | arduino | trixie, unoq-01 (LAVA 592934) | forky, unoq-04 (LAVA 592938) |

Since it doesn't happen every time, it has no known-failures entry: one would fail every boot that doesn't crash as an unexpected pass.

Possibly related, but not the same crash: #209.
