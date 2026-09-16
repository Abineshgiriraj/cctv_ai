import json
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


def _camera_from_values(ip, channel, name, area):
    ip = str(ip or "").strip()
    if not ip:
        return None
    try:
        channel = max(1, int(channel))
    except (TypeError, ValueError):
        channel = 1
    return {
        "camera_key": f"{ip}_ch{channel}",
        "camera_ip": ip,
        "channel_no": channel,
        "camera_name": str(name or f"Camera {ip}").strip(),
        "area_name": str(area or "Unknown Area").strip(),
    }


def _load_inventory_file():
    if not _bool("CAMERA_INVENTORY_ENABLED", True):
        return []
    path = os.getenv("CAMERA_INVENTORY_FILE", "camera_inventory.json").strip()
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(__file__), path)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            rows = json.load(handle)
    except Exception:
        return []
    cameras = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        camera = _camera_from_values(
            row.get("camera_ip"),
            row.get("channel_no", 1),
            row.get("camera_name"),
            row.get("area_name"),
        )
        if camera:
            cameras.append(camera)
    return cameras


class Config:
    def _parse_camera_config():
        cameras = []
        config_str = os.getenv("CAMERA_CONFIG", "").strip().strip('"').strip("'")
        if config_str:
            parts = [p.strip() for p in config_str.split(",") if p.strip()]
            for part in parts:
                items = [x.strip().strip('"').strip("'") for x in part.split("|")]
                if len(items) >= 4:
                    camera = _camera_from_values(items[0], items[1], items[2], items[3])
                    if camera:
                        cameras.append(camera)
        else:
            # Fallback to older format if CAMERA_CONFIG is not set.
            ips = [ip.strip() for ip in os.getenv("CAMERA_IPS", "192.168.0.241").split(",") if ip.strip()]
            areas = [area.strip() for area in os.getenv("CAMERA_AREAS", "").split(",")]
            for idx, ip in enumerate(ips):
                camera = _camera_from_values(
                    ip,
                    1,
                    f"Camera {idx + 1}",
                    areas[idx] if idx < len(areas) and areas[idx] else f"Camera {idx + 1} Area",
                )
                if camera:
                    cameras.append(camera)

        # Merge the non-secret municipal inventory committed with the project.
        # Local .env cameras win when the same IP+channel appears in both places.
        seen = {camera["camera_key"] for camera in cameras}
        for camera in _load_inventory_file():
            if camera["camera_key"] not in seen:
                cameras.append(camera)
                seen.add(camera["camera_key"])
        return cameras

    CAMERAS = _parse_camera_config()
    CAMERA_IPS = [c["camera_ip"] for c in CAMERAS]

    CAMERA_USERNAME = os.getenv("CAMERA_USERNAME", "admin")
    CAMERA_PASSWORD = os.getenv("CAMERA_PASSWORD", "")
    CAMERA_SUBTYPE = int(os.getenv("CAMERA_SUBTYPE", 0))

    YOLO_MODEL = os.getenv("YOLO_MODEL", "yolov8n.pt")

    TRACKING_CANDIDATE_CONFIDENCE = float(
        os.getenv("TRACKING_CANDIDATE_CONFIDENCE", os.getenv("CONFIDENCE_THRESHOLD", "0.28"))
    )
    CONFIDENCE_THRESHOLD = TRACKING_CANDIDATE_CONFIDENCE
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
    COUNT_MIN_CONFIDENCE = float(os.getenv("COUNT_MIN_CONFIDENCE", 0.35))

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

    HELMET_CONFIDENCE = float(os.getenv("HELMET_CONFIDENCE", 0.50))
    NO_HELMET_CONFIDENCE = float(os.getenv("NO_HELMET_CONFIDENCE", 0.68))
    HELMET_IMGSZ = max(640, int(os.getenv("HELMET_IMGSZ", 960)))
    HELMET_CONFIRM_FRAMES = max(1, int(os.getenv("HELMET_CONFIRM_FRAMES", 3)))
    HELMET_CONFIRM_WINDOW = max(HELMET_CONFIRM_FRAMES, int(os.getenv("HELMET_CONFIRM_WINDOW", 5)))

    PLATE_CONFIDENCE = float(os.getenv("PLATE_CONFIDENCE", 0.20))
    PLATE_IMGSZ = max(640, int(os.getenv("PLATE_IMGSZ", 960)))
    PLATE_OCR_MIN_CONFIDENCE = float(os.getenv("PLATE_OCR_MIN_CONFIDENCE", 0.18))
    PLATE_CACHE_SECONDS = max(1.0, float(os.getenv("PLATE_CACHE_SECONDS", 8.0)))
    PLATE_CONFIRM_READS = max(1, int(os.getenv("PLATE_CONFIRM_READS", 2)))
    PLATE_CONFIRM_WINDOW = max(PLATE_CONFIRM_READS, int(os.getenv("PLATE_CONFIRM_WINDOW", 5)))

    ROAD_DAMAGE_CONFIDENCE = float(os.getenv("ROAD_DAMAGE_CONFIDENCE", 0.25))
    ROAD_DAMAGE_IMGSZ = max(640, int(os.getenv("ROAD_DAMAGE_IMGSZ", 960)))
    ROAD_EVERY_N_FRAMES = max(1, int(os.getenv("ROAD_EVERY_N_FRAMES", 5)))
    ROAD_ROI_TOP_RATIO = min(0.85, max(0.0, float(os.getenv("ROAD_ROI_TOP_RATIO", 0.22))))

    VIOLATION_COOLDOWN_SECONDS = max(10, int(os.getenv("VIOLATION_COOLDOWN_SECONDS", 90)))
    ROAD_EVENT_COOLDOWN_SECONDS = max(10, int(os.getenv("ROAD_EVENT_COOLDOWN_SECONDS", 120)))
    OCR_ENABLED = _bool("OCR_ENABLED", True)
