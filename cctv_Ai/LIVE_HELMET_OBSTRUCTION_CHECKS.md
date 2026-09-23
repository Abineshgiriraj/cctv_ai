# Live view, helmet and obstruction changes

Branch: `restore-good-tracking-display-only`.

## Update on Windows

Stop the running backend with Ctrl+C, then run:

```powershell
cd D:\XAMPP\htdocs\test\cctv_ai
git switch restore-good-tracking-display-only
git pull --ff-only origin restore-good-tracking-display-only
cd cctv_Ai
.\start-mjpeg.bat
```

Hard-refresh the browser with Ctrl+F5 once to load the new JS/CSS.

## What changed

- Live Monitoring remembers page, page size, recorder/health filters and selected cameras in this browser. Browser back/forward also restarts the JPEG loader after a cached page returns. Navigation still opens a new document; it does not keep the old video DOM alive.
- Desktop camera cards use two columns (six = three rows of two). Small screens use one column. Existing 2/4/6/8/10 limits remain.
- Each detected person is assigned to the closest eligible motorcycle. Rider and passenger are checked separately, with separate confirmation votes. A head outside the selected person's upper body is rejected to reduce cross-rider attribution. Bike-region fallback remains when no person was detected.
- Real, confirmed no-helmet observations use the existing violation storage, with evidence and rider/vehicle IDs. No plate model is required. No synthetic records are inserted.
- Road-model inference no longer holds the shared helmet lock or delays publication of that camera's annotated frame. One background worker keeps at most ten pending frames, replaces old pending frames, and drops work for inactive cameras. Road jobs are submitted at most every ten seconds per camera. Model road results remain in their reports; their boxes are no longer painted onto the live frame. Vehicle and helmet boxes still use the matching source frame.
- Road Obstructions has its own navigation entry, camera/date filters and evidence images, backed by existing `incident_events` rows and evidence files. Foreground confirmation counts one observation per spatial cell per frame. Failed storage does not consume the cooldown or reset confirmed votes.

## Check the behavior

1. Choose six cameras, go to page 5 (with enough matching cameras), open Helmet Violations, then return to Live Monitoring. Page 5, size and selection should be restored. Test browser Back as well.
2. Start with two active cameras for helmet validation. Use footage where a motorcycle and rider's head are clear. A red `NO HELMET ?` box is an observation; repeated qualifying observations produce a confirmed violation. Check Helmet Violations with today's date and the correct camera. Review the saved image. Plate can be blank.
3. Inspect actual analysis counters if there are no events:

```powershell
$h = Invoke-RestMethod 'http://127.0.0.1:5000/health'
$h.cameras.'192.168.0.241_ch1'.ai | ConvertTo-Json -Depth 8
Invoke-RestMethod 'http://127.0.0.1:5000/advanced/status' | ConvertTo-Json -Depth 8
```

`advanced.motorcycles=0` means no motorcycle reached the helmet stage. `helmet_checked=0` with motorcycles present means the helmet model produced no accepted head observation. Check the terminal for inference/database errors. This change does not guarantee detection of distant or blurred heads, nor does it classify every pedestrian as a traffic helmet violation.

4. For obstruction validation use a recorded fixed-camera scene, with clear-road background first and then a persistent obstruction. After background warmup (default 20 analyzed frames) and confirmation (default six observations), inspect Road Obstructions. No need to create a real road hazard. Records and evidence should remain after backend restart.

Foreground obstruction detection flags candidates: trees, branches, debris, lighting changes and missed vehicles can resemble one another. It does not identify tree species, prove a tree fell, or reliably detect objects already present during warmup. Human review or a validated dedicated model is needed to confirm fallen trees.

## Automated checks

```powershell
python -m unittest discover -s tests -p 'test_helmet_pipeline.py' -v
python -m unittest discover -s tests -p 'test_riders_and_obstructions.py' -v
node --test tests/test_live_preferences.js
```

The regression tests use model/store doubles: they verify rider-specific confirmation, violation writes without plates, cooldown retry, bounded road queue and preference restoration. Actual Windows RTSP, model accuracy, PHP rendering and MySQL integration require testing on the installation.
