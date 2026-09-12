CivicVision AI Tracking Update
==============================

What was added
--------------
- YOLOv8n detection for: person, bicycle, car, motorcycle, bus, truck.
- ByteTrack object IDs per camera.
- Motion trails for tracked IDs.
- Live person/vehicle counts and inference timing in the UI.
- /tracked_feed/<camera_ip> AI MJPEG endpoint.
- /ai_snapshot/<camera_ip> AI JPEG snapshot endpoint.
- Raw /video_feed/<camera_ip> is preserved as a fallback.

Important architecture rule
---------------------------
The AI worker DOES NOT connect to RTSP. stream_server.py opens each RTSP camera
once, then shares the already-captured OpenCV frame with the YOLO/ByteTrack
worker. This protects the working camera connection.

Install / start
---------------
From PowerShell in this cctv_Ai folder:

  .\.venv\Scripts\python.exe -m pip install ultralytics "lap>=0.5.12"
  .\.venv\Scripts\python.exe stream_server.py

Or, after dependencies are installed:

  .\start-mjpeg.bat

Browser tests
-------------
Health:
  http://127.0.0.1:5000/health

Raw Camera 1:
  http://127.0.0.1:5000/video_feed/192.168.0.241

AI Camera 1:
  http://127.0.0.1:5000/tracked_feed/192.168.0.241

Raw Camera 2:
  http://127.0.0.1:5000/video_feed/192.168.0.242

AI Camera 2:
  http://127.0.0.1:5000/tracked_feed/192.168.0.242

Performance tuning
------------------
Defaults are intentionally conservative for CPU use:
  CONFIDENCE_THRESHOLD=0.35
  AI_MAX_FPS=5
  YOLO_IMGSZ=640

Add these to .env only if you want to change them. Lower AI_MAX_FPS or
YOLO_IMGSZ if the PC becomes slow. The raw MJPEG stream remains separate.

Notes
-----
- Counts shown are CURRENT objects in the latest AI frame, not cumulative traffic totals.
- Track IDs are temporary and camera-local. If a person/vehicle leaves and later
  returns, ByteTrack may assign a new ID.
- Helmet/triple-riding/accident/fire/garbage/wrong-way detection are NOT added by
  this update; they require separate logic/models and should be added after basic
  tracking is stable.
