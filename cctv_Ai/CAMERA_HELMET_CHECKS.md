# Camera and helmet fix — restore-good-tracking-display-only

This update preserves this branch's server-rendered tracking boxes and helmet
search regions. AI mode loads /ai_snapshot and Raw mode loads /snapshot.
Finite JPEG requests, limited to two concurrent image downloads, avoid holding
browser connections open for every camera. Failed requests retry automatically;
page changes release image resources. Startup no longer waits for model downloads
or recorder-title synchronization. Helmet fixes correct padded crop coordinates,
clamp the vote window, recognize additional labels, and allow retry after a failed
DB insert. One failed insert does not interrupt other bikes in the frame.

Stop the existing backend with Ctrl+C. Run in PowerShell:

```powershell
cd D:\XAMPP\htdocs\test\cctv_ai
git switch restore-good-tracking-display-only
git pull --ff-only origin restore-good-tracking-display-only
cd cctv_Ai
.\start-mjpeg.bat
```

Keep the console open and hard-refresh Live Monitoring with Ctrl+F5.
Keep local .env settings and trained weights. No database reset is required.
If Git reports conflicting local changes, preserve them rather than forcing a reset.

Check on the backend PC:
- http://127.0.0.1:5000/health should respond. A visible camera needs connected=true;
  raw has_frame and ai.has_frame indicate the respective image is available.
- Select Raw then AI. Both must show frames, with tracking boxes retained in AI mode.
  If Raw works but AI does not, inspect the backend console for model/inference errors.
- http://127.0.0.1:5000/advanced/status needs helmet.loaded=true and
  helmet.no_helmet_supported=true. If weights are missing, stop the backend, run
  setup-ai-models.bat once, and restart. A head/heads label must mean uncovered
  heads in the trained model; check its class semantics.
- http://127.0.0.1:5000/analytics/status needs connected=true.
- Observe a clear no-helmet motorcycle rider over multiple detection frames.
  Check Helmet Violations for a new timestamp and evidence image. An unreadable
  number plate must not prevent saving. Check a helmeted rider and pedestrian
  clip for false positives as well.
- Show eight cameras, change pages, expand and close a feed, and test Reconnect.

Validation: node --test tests/test_camera_frames.js and
python -m unittest discover -s tests -v. These tests mock camera/model/DB access;
actual RTSP feeds, trained-model accuracy and MySQL saving need local verification.
