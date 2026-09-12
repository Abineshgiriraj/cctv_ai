"""Legacy detector helper retained for compatibility.

The live application does NOT import this module. Real-time person/vehicle
tracking is integrated in stream_server.py so it can reuse the existing camera
capture frames without opening a second RTSP connection.
"""

from ultralytics import YOLO
from config import Config


class Detector:
    def __init__(self):
        self.model = YOLO(Config.YOLO_MODEL)

    def detect(self, frame):
        """Run non-persistent detection on an already-decoded OpenCV frame."""
        return self.model(
            frame,
            classes=Config.TARGET_CLASSES,
            conf=Config.CONFIDENCE_THRESHOLD,
            iou=Config.IOU_THRESHOLD,
            imgsz=Config.YOLO_IMGSZ,
            verbose=False,
        )
