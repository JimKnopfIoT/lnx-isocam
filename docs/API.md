# SonoCam Pocket CRY8024 — HTTP API (port 9999)

On-device app **`com.cry.acoustic_pocket` v0.0.29 "Acoustic Pocket"** (Flutter),
HTTP server = AndServer. Reachable over WiFi when the camera is awake (display on;
it WiFi-sleeps when the screen is off). Example: `http://192.168.1.100:9999` (a placeholder — use whatever address the camera has on your own network).

## Auth
```
POST /v1/login?user=admin&password=crysound   ->  {"result":true,"data":"<UUID token>"}
```
Pass the token as `userId=<token>` on every other call. (`userLogin` = a separate
user-level password.) Health check: `GET /` -> `SERVER OK 123456`.

## Verified live endpoints (GET, need ?userId=)
| Endpoint | Returns |
|---|---|
| `/v1/devInfo` | appName, buildNumber, deviceId, deviceName, signature … |
| `/v1/deviceRunningParams` | `{doaType, micArraySize:6, micChannelNumber:64, sampleRate:130000}` |
| `/v1/sampleRate` | `{sampleRate:130000}` |
| `/v1/maxdB` | **LIVE** `[{value:<dB>, x, y}]` — loudest source(s) on the DOA grid |
| `/v1/getCameraImage` | **LIVE** optical camera frame (JPEG, ~560 KB) |
| `/v1/album` | album list |
| `/v1/getFile` , `/v1/file`, `/v1/downloadFile` | file download by path |

## Full route list (from decompiled HttpServerControllerAdapter)
login, userLogin, logout, heartBeat, devInfo, deviceRunningParams, sampleRate,
maxdB, frequency, doaProgramType, nahDistance, certificate, calibrationSwitch,
favorite, album, mobile/album, albumThumbnails, photoThumbnails, devReport,
report, createWord, getCreateReportStatus, getCameraImage, getFile, file,
downloadFile, getMicPhiPolarFile, networkInterfaces, updateResult,
updateResultGet, videoStreaming, videoStreamingStatus, audioCaptureLocal(+Status),
audioCaptureNetwork(+Status), remote/control, remote/status, screen/capture,
screen/stopRecord, task/distribution, task/query, task/queryAll, task/delete,
task/file.
(networkInterfaces, videoStreaming, audioCaptureNetwork report "unSupported" here.)

## File access (no auth)
File-browser web roots serve HTML listings: `/download /pictures /recordings
/dcim /update`. **Path traversal**: `/download/..` = `/sdcard`, e.g.
`GET /download/../imager/Default/<ts>/info.json`. Implemented in
`isocam/webclient.py`.

## Notes
- All processing is on-device (FPGA at /data/vendor/crysound + jTransforms FFT);
  raw 64-ch mic data is NOT exposed — only finished images + numeric results.
- "Live with all data" = poll `/v1/getCameraImage` + `/v1/maxdB` and overlay the
  dB peaks (x,y on the 32x24 DOA grid) onto the frame.
