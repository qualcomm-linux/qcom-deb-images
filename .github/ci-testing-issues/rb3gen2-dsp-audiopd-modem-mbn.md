Repository: qualcomm-linux/qcom-deb-images
Title: qcs6490-rb3gen2: DSP_AudioPD fails when the modem fails to load modem.mbn

Since testkit-2026.11.15, DSP_AudioPD fails on any remoteproc error in dmesg. On qcs6490-rb3gen2 some boots log the following, and the test fails exactly then:

```
remoteproc remoteproc0: Direct firmware load for qcom/qcs6490/modem.mbn failed with error -2
remoteproc remoteproc0: request_firmware failed: -2
remoteproc remoteproc0: Boot failed: -2
```

This is the modem remoteproc rather than the audio DSP, but the test reports it as `Captured FastRPC or ADSP kernel errors`.

In push run https://github.com/qualcomm-linux/qcom-deb-images/actions/runs/44701459331 it happened:

| Kernel | trixie | forky |
|---|---|---|
| mainline | yes (LAVA 592994) | yes (LAVA 593003) |
| debian | yes (LAVA 592993) | yes (LAVA 592997) |
| qcom-next | yes (LAVA 593014) | no (LAVA 593008) |
| linux-next | no (LAVA 593004) | no (LAVA 593000) |

It does not follow the board: rb3g2-01 hit it with mainline but not with linux-next.

It is listed as a known failure for mainline and debian only, as an entry for a kernel where the test passes would fail the run as an unexpected pass.

Open questions:

- Should the image ship `qcom/qcs6490/modem.mbn`, or should the modem remoteproc not be started on this board?
- Should DSP_AudioPD count modem remoteproc errors at all? If not, that is for qualcomm-linux/qcom-linux-testkit.
