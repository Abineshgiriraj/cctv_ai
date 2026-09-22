# Helmet detection and storage checks

Apply all files from this change together. Preserve your local `.env` and model weights.
Restart the backend with `start-mjpeg.bat`, then hard-refresh the browser (Ctrl+F5).
No database reset or schema import is required.

## Prerequisites

- `ADVANCED_DETECTION_ENABLED=1` and `HELMET_MODEL` must point to an installed, trained helmet/no-helmet model.
- Open `http://localhost:5000/advanced/status`: `models.helmet.loaded` and `models.helmet.no_helmet_supported` must both be true. Recognized `head`/`heads` labels assume the model defines them as uncovered heads; verify the model's class semantics before using that model.
- Open `http://localhost:5000/analytics/status`: `connected` must be true.
- Keep `ADVANCED_EVERY_N_FRAMES=1`, `HELMET_CONFIRM_FRAMES=2`, and `HELMET_CONFIRM_WINDOW=4` for the initial single-camera test. These are sampling/confirmation settings, not an accuracy guarantee. Do not lower confidence thresholds merely to force records.
- Generic vehicle/person YOLO weights alone cannot detect helmet status. Missing weights must be installed separately.

## Before/after acceptance test

Use the same recorded clip, camera configuration and weights on both versions.

1. A clearly visible rider without a helmet: confirm a tentative NO HELMET overlay, multi-frame confirmation, then one new Helmet Violations record with evidence, camera and timestamp. An unreadable/missing number plate must not prevent saving.
2. A helmeted rider and a pedestrian-only scene: inspect for false violations. Confirm no fabricated no-helmet record just because a person was detected.
3. Repeat the same track within the cooldown: no duplicate record. Distinct untracked bikes in separate image regions must not share a camera-wide cooldown.
4. In an isolated test database, interrupt MySQL, then restore it while the rider remains visible: verify a new confirmed observation retries the failed insert. No replay of riders that leave during the outage is implemented.
5. Set rider sampling to 2 and road sampling to 3: a road-only pass must not erase a recent helmet overlay or refresh its age. Return rider sampling to 1 after the test.
6. Increase from one camera to the normal visible grid, then all cameras. Record helmet observation age, DB errors and inference latency. The shared advanced worker still has finite throughput; this patch does not establish 98-camera capacity. Overlays older than four seconds expire as before.

Automated checks: `python -m unittest discover -s tests -v` and `node --check assets/app_fixed.js`.
Tests use fake inference/storage objects. Actual model accuracy, RTSP connectivity and MySQL inserts need local validation.
