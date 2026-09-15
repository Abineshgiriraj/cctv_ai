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
    CAMERA_IPS = [ip.strip() for ip in os.getenv("CAMERA_IPS", "192.168.0.241").split(",") if ip.strip()]
    CAMERA_USERNAME = os.getenv("CAMERA_USERNAME", "admin")
    CAMERA_PASSWORD = os.getenv("CAMERA_PASSWORD", "")
    CAMERA_CHANNEL = int(os.getenv("CAMERA_CHANNEL", 1))
    CAMERA_SUBTYPE = int(os.getenv("CAMERA_SUBTYPE", 1))

    YOLO_MODEL = os.getenv("YOLO_MODEL", "yolov8n.pt")
    CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", 0.35))
    IOU_THRESHOLD = float(os.getenv("IOU_THRESHOLD", 0.45))
    TARGET_CLASSES = _int_list("TARGET_CLASSES", "0,1,2,3,5,7")
    VEHICLE_CLASSES = {1, 2, 3, 5, 7}
    COUNTED_CLASSES = set(TARGET_CLASSES)

    AI_MAX_FPS = float(os.getenv("AI_MAX_FPS", 5))
    YOLO_IMGSZ = int(os.getenv("YOLO_IMGSZ", 640))
    YOLO_DEVICE = os.getenv("YOLO_DEVICE", "").strip()
    TRACK_TRAIL_LENGTH = int(os.getenv("TRACK_TRAIL_LENGTH", 18))
    JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", 80))

    COUNTING_ENABLED = _bool("COUNTING_ENABLED", True)
    COUNT_LINE_Y_RATIO = min(0.95, max(0.05, float(os.getenv("COUNT_LINE_Y_RATIO", 0.62))))
    COUNT_MIN_TRACK_AGE = max(1, int(os.getenv("COUNT_MIN_TRACK_AGE", 3)))

    DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT = int(os.getenv("DB_PORT", 3306))
    DB_NAME = os.getenv("DB_NAME", "cctv_ai")
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")

    ANALYTICS_DB = "mysql"

    HELMET_MODEL = os.getenv("HELMET_MODEL", os.path.join("models", "helmet.pt"))
    PLATE_MODEL = os.getenv("PLATE_MODEL", os.path.join("models", "license_plate.pt"))
    ROAD_DAMAGE_MODEL = os.getenv("ROAD_DAMAGE_MODEL", os.path.join("models", "road_damage.pt"))
    ROAD_OBSTRUCTION_MODEL = os.getenv("ROAD_OBSTRUCTION_MODEL", os.path.join("models", "road_obstruction.pt"))

    ADVANCED_DETECTION_ENABLED = _bool("ADVANCED_DETECTION_ENABLED", True)
    ADVANCED_EVERY_N_FRAMES = max(1, int(os.getenv("ADVANCED_EVERY_N_FRAMES", 3)))
    ADVANCED_CONFIDENCE = float(os.getenv("ADVANCED_CONFIDENCE", 0.40))

    # Helmet-specific accuracy controls. No-helmet is deliberately stricter
    # because a false violation is worse than a missed low-confidence frame.
    HELMET_CONFIDENCE = float(os.getenv("HELMET_CONFIDENCE", 0.50))
    NO_HELMET_CONFIDENCE = float(os.getenv("NO_HELMET_CONFIDENCE", 0.58))
    HELMET_IMGSZ = max(640, int(os.getenv("HELMET_IMGSZ", 960)))
    HELMET_CONFIRM_FRAMES = max(1, int(os.getenv("HELMET_CONFIRM_FRAMES", 2)))
    HELMET_CONFIRM_WINDOW = max(HELMET_CONFIRM_FRAMES, int(os.getenv("HELMET_CONFIRM_WINDOW", 4)))
    HELMET_FULL_FRAME_INTERVAL = max(1, int(os.getenv("HELMET_FULL_FRAME_INTERVAL", 2)))

    # License-plate detector/OCR controls. Small CCTV plates need a lower model
    # threshold plus larger inference size; OCR still filters weak text.
    PLATE_CONFIDENCE = float(os.getenv("PLATE_CONFIDENCE", 0.20))
    PLATE_IMGSZ = max(640, int(os.getenv("PLATE_IMGSZ", 960)))
    PLATE_OCR_MIN_CONFIDENCE = float(os.getenv("PLATE_OCR_MIN_CONFIDENCE", 0.18))
    PLATE_RETRY_SECONDS = max(0.5, float(os.getenv("PLATE_RETRY_SECONDS", 1.5)))
    PLATE_CACHE_SECONDS = max(1.0, float(os.getenv("PLATE_CACHE_SECONDS", 8.0)))

    VIOLATION_COOLDOWN_SECONDS = max(10, int(os.getenv("VIOLATION_COOLDOWN_SECONDS", 90)))
    ROAD_EVENT_COOLDOWN_SECONDS = max(10, int(os.getenv("ROAD_EVENT_COOLDOWN_SECONDS", 120)))
    OCR_ENABLED = _bool("OCR_ENABLED", True)
