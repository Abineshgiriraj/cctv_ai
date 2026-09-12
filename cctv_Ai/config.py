import os
from dotenv import load_dotenv

load_dotenv()


def _int_list(name: str, default: str):
    value = os.getenv(name, default)
    items = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            items.append(int(part))
        except ValueError:
            pass
    return items


class Config:
    # Camera settings remain server-side. Do not expose credentials to the browser.
    CAMERA_IPS = [ip.strip() for ip in os.getenv("CAMERA_IPS", "192.168.0.241").split(",") if ip.strip()]
    CAMERA_USERNAME = os.getenv("CAMERA_USERNAME", "admin")
    CAMERA_PASSWORD = os.getenv("CAMERA_PASSWORD", "")
    CAMERA_CHANNEL = int(os.getenv("CAMERA_CHANNEL", 1))
    CAMERA_SUBTYPE = int(os.getenv("CAMERA_SUBTYPE", 1))

    # Existing model file. Default target set = person + road vehicles.
    YOLO_MODEL = os.getenv("YOLO_MODEL", "yolov8n.pt")
    CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", 0.35))
    IOU_THRESHOLD = float(os.getenv("IOU_THRESHOLD", 0.45))
    TARGET_CLASSES = _int_list("TARGET_CLASSES", "0,1,2,3,5,7")
    VEHICLE_CLASSES = {1, 2, 3, 5, 7}

    # Limit AI work so detection cannot starve the camera capture/MJPEG threads.
    AI_MAX_FPS = float(os.getenv("AI_MAX_FPS", 5))
    YOLO_IMGSZ = int(os.getenv("YOLO_IMGSZ", 640))
    YOLO_DEVICE = os.getenv("YOLO_DEVICE", "").strip()  # blank = Ultralytics auto/CPU
    TRACK_TRAIL_LENGTH = int(os.getenv("TRACK_TRAIL_LENGTH", 18))
    JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", 80))
