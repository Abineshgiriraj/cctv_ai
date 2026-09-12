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


def _bool(name: str, default: bool = False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    # Camera settings remain server-side. Do not expose credentials to the browser.
    CAMERA_IPS = [ip.strip() for ip in os.getenv("CAMERA_IPS", "192.168.0.241").split(",") if ip.strip()]
    CAMERA_USERNAME = os.getenv("CAMERA_USERNAME", "admin")
    CAMERA_PASSWORD = os.getenv("CAMERA_PASSWORD", "")
    CAMERA_CHANNEL = int(os.getenv("CAMERA_CHANNEL", 1))
    CAMERA_SUBTYPE = int(os.getenv("CAMERA_SUBTYPE", 1))

    # Main model: person + bicycle + car + motorcycle + bus + truck.
    YOLO_MODEL = os.getenv("YOLO_MODEL", "yolov8n.pt")
    CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", 0.35))
    IOU_THRESHOLD = float(os.getenv("IOU_THRESHOLD", 0.45))
    TARGET_CLASSES = _int_list("TARGET_CLASSES", "0,1,2,3,5,7")
    VEHICLE_CLASSES = {1, 2, 3, 5, 7}

    # Limit AI work so detection cannot starve capture/MJPEG threads.
    AI_MAX_FPS = float(os.getenv("AI_MAX_FPS", 5))
    YOLO_IMGSZ = int(os.getenv("YOLO_IMGSZ", 640))
    YOLO_DEVICE = os.getenv("YOLO_DEVICE", "").strip()
    TRACK_TRAIL_LENGTH = int(os.getenv("TRACK_TRAIL_LENGTH", 18))
    JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", 80))

    # Persistent traffic analytics. The normalized horizontal line is drawn on
    # the AI feed; a vehicle is counted once when its tracked center crosses it.
    COUNTING_ENABLED = _bool("COUNTING_ENABLED", True)
    COUNT_LINE_Y_RATIO = min(0.95, max(0.05, float(os.getenv("COUNT_LINE_Y_RATIO", 0.62))))
    COUNT_MIN_TRACK_AGE = max(1, int(os.getenv("COUNT_MIN_TRACK_AGE", 3)))
    ANALYTICS_DB = os.getenv("ANALYTICS_DB", os.path.join("data", "cctv_analytics.sqlite3"))

    # Optional custom-model slots. These are readiness hooks only until trained
    # weights are supplied; the stock COCO yolov8n.pt has no helmet/pothole/tree classes.
    HELMET_MODEL = os.getenv("HELMET_MODEL", os.path.join("models", "helmet.pt"))
    ROAD_DAMAGE_MODEL = os.getenv("ROAD_DAMAGE_MODEL", os.path.join("models", "road_damage.pt"))
    ROAD_OBSTRUCTION_MODEL = os.getenv("ROAD_OBSTRUCTION_MODEL", os.path.join("models", "road_obstruction.pt"))
