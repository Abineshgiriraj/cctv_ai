import json
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


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
    def _parse_camera_config():
        config_str = os.getenv("CAMERA_CONFIG", "")
        cameras = []
        if config_str:
            parts = [p.strip() for p in config_str.split(",") if p.strip()]
            for part in parts:
                items = [x.strip() for x in part.split("|")]
                if len(items) >= 4:
                    ip, ch_str, name, area = items[0], items[1], items[2], items[3]
                    try:
                        ch = int(ch_str)
                    except ValueError:
                        ch = 1
                    cameras.append({
                        "camera_key": f"{ip}_ch{ch}",
                        "camera_ip": ip,
                        "channel_no": ch,
                        "camera_name": name,
                        "area_name": area
                    })
        else:
            # Fallback to older format if CAMERA_CONFIG is not set
            ips = [ip.strip() for ip in os.getenv("CAMERA_IPS", "192.168.0.241").split(",") if ip.strip()]
            for idx, ip in enumerate(ips):
                cameras.append({
                    "camera_key": f"{ip}_ch1",
                    "camera_ip": ip,
                    "channel_no": 1,
                    "camera_name": f"Camera {idx+1}",
                    "area_name": f"Camera {idx+1} Area"
                })
        cache_path = BASE_DIR / "data" / "nvr_channel_titles.json"
        if cache_path.is_file():
            try:
                title_cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                title_cache = {}
            for camera in cameras:
                cached = title_cache.get(camera["camera_key"]) or {}
                title = str(cached.get("camera_name") or "").strip()
                if title:
                    camera["camera_name"] = title
                    # Use the actual NVR title as the location/area as well when
                    # the configured area is only a generic recorder label.
                    current_area = str(camera.get("area_name") or "").strip()
                    if not current_area or current_area.lower().startswith("recorder "):
                        camera["area_name"] = str(cached.get("area_name") or title).strip() or title
        return cameras

    CAMERAS = _parse_camera_config()
    # Provide CAMERA_IPS list for anything that still expects it
    CAMERA_IPS = [c["camera_ip"] for c in CAMERAS]

    CAMERA_USERNAME = os.getenv("CAMERA_USERNAME", "admin")
    CAMERA_PASSWORD = os.getenv("CAMERA_PASSWORD", "")
    CAMERA_SUBTYPE = int(os.getenv("CAMERA_SUBTYPE", 0))
    CAMERA_CONNECT_STAGGER_SECONDS = max(
        0.0, float(os.getenv("CAMERA_CONNECT_STAGGER_SECONDS", 0.15))
    )
    RTSP_RECONNECT_SECONDS = max(
        1.0, float(os.getenv("RTSP_RECONNECT_SECONDS", 3.0))
    )
    LIVE_STREAM_MAX_FPS = max(
        2.0, min(20.0, float(os.getenv("LIVE_STREAM_MAX_FPS", 12.0)))
    )

    YOLO_MODEL = os.getenv("YOLO_MODEL", "yolov8n.pt")

    # Stage 1: permissive candidate threshold used by YOLO + ByteTrack. This lets
    # distant motorcycles/persons enter the pipeline instead of disappearing
    # before helmet analysis starts.
    TRACKING_CANDIDATE_CONFIDENCE = float(
        os.getenv("TRACKING_CANDIDATE_CONFIDENCE", os.getenv("CONFIDENCE_THRESHOLD", "0.28"))
    )
    # Kept for compatibility with older configuration/code.
    CONFIDENCE_THRESHOLD = TRACKING_CANDIDATE_CONFIDENCE
    IOU_THRESHOLD = float(os.getenv("IOU_THRESHOLD", 0.45))
    TARGET_CLASSES = _int_list("TARGET_CLASSES", "0,1,2,3,5,7")
    VEHICLE_CLASSES = {1, 2, 3, 5, 7}
    COUNTED_CLASSES = set(TARGET_CLASSES)

    AI_MAX_FPS = float(os.getenv("AI_MAX_FPS", 5))
    # Large NVR deployments should only open/process the cameras on the current UI page.
    PAGED_CAMERA_MODE = _bool("PAGED_CAMERA_MODE", True)
    # When enabled, every configured camera keeps RTSP capture + background AI
    # monitoring active. Pagination only controls which feeds are shown in the UI.
    MONITOR_ALL_CAMERAS = _bool("MONITOR_ALL_CAMERAS", False)
    SHARED_AI_WORKERS = max(1, min(4, int(os.getenv("SHARED_AI_WORKERS", 1))))
    ACTIVE_CAMERA_LIMIT = max(1, min(16, int(os.getenv("ACTIVE_CAMERA_LIMIT", 8))))
    AI_MODEL_IDLE_UNLOAD_SECONDS = max(2.0, float(os.getenv("AI_MODEL_IDLE_UNLOAD_SECONDS", 15)))
    FOREGROUND_AI_FPS = max(0.1, float(os.getenv("FOREGROUND_AI_FPS", 1.0)))
    BACKGROUND_AI_FPS = max(0.02, float(os.getenv("BACKGROUND_AI_FPS", 0.20)))

    # Focused cameras can use the original Ultralytics ByteTrack path (model.track
    # with persist=True) instead of the lightweight shared-camera matcher. This
    # restores frame-by-frame IDs/trajectories for the cameras the operator is
    # actively viewing, while hidden cameras remain on the shared background pool.
    FOREGROUND_BYTETRACK_ENABLED = _bool("FOREGROUND_BYTETRACK_ENABLED", True)
    FOREGROUND_TRACK_FPS = max(0.0, float(os.getenv("FOREGROUND_TRACK_FPS", 0.0)))
    FOREGROUND_TRACK_IMGSZ = max(
        320, int(os.getenv("FOREGROUND_TRACK_IMGSZ", 512))
    )
    FOREGROUND_TRACK_CONCURRENCY = max(
        1, min(8, int(os.getenv("FOREGROUND_TRACK_CONCURRENCY", 2)))
    )
    FOREGROUND_TRACK_IDLE_SECONDS = max(
        1.0, float(os.getenv("FOREGROUND_TRACK_IDLE_SECONDS", 3.0))
    )
    BACKGROUND_CAMERAS_PER_CYCLE = max(
        1, min(4, int(os.getenv("BACKGROUND_CAMERAS_PER_CYCLE", 1)))
    )
    BACKGROUND_SCAN_EVERY_N_FOREGROUND = max(
        1, int(os.getenv("BACKGROUND_SCAN_EVERY_N_FOREGROUND", 3))
    )
    YOLO_IMGSZ = int(os.getenv("YOLO_IMGSZ", 640))
    FOREGROUND_YOLO_IMGSZ = max(
        320, int(os.getenv("FOREGROUND_YOLO_IMGSZ", min(YOLO_IMGSZ, 512)))
    )
    BACKGROUND_YOLO_IMGSZ = max(
        256, int(os.getenv("BACKGROUND_YOLO_IMGSZ", min(FOREGROUND_YOLO_IMGSZ, 384)))
    )
    GENERATE_TRACKED_MJPEG = _bool("GENERATE_TRACKED_MJPEG", False)
    YOLO_DEVICE = os.getenv("YOLO_DEVICE", "").strip()
    TRACK_TRAIL_LENGTH = int(os.getenv("TRACK_TRAIL_LENGTH", 18))
    JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", 80))
    LIVE_OVERLAY_MIN_CONFIDENCE = min(
        0.95, max(0.05, float(os.getenv("LIVE_OVERLAY_MIN_CONFIDENCE", 0.35)))
    )

    COUNTING_ENABLED = _bool("COUNTING_ENABLED", True)
    COUNT_LINE_Y_RATIO = min(0.95, max(0.05, float(os.getenv("COUNT_LINE_Y_RATIO", 0.62))))
    COUNT_MIN_TRACK_AGE = max(1, int(os.getenv("COUNT_MIN_TRACK_AGE", 3)))
    # Stage 2: counting remains stricter than candidate detection, so lowering the
    # tracker threshold does not automatically inflate vehicle reports.
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
    ADVANCED_EVERY_N_FRAMES = max(1, int(os.getenv("ADVANCED_EVERY_N_FRAMES", 1)))
    ADVANCED_CONFIDENCE = float(os.getenv("ADVANCED_CONFIDENCE", 0.40))

    # Stage 3: helmet/no-helmet is decided independently from motorcycle confidence.
    HELMET_CONFIDENCE = float(os.getenv("HELMET_CONFIDENCE", 0.25))
    NO_HELMET_CONFIDENCE = float(os.getenv("NO_HELMET_CONFIDENCE", 0.20))
    HELMET_IMGSZ = max(640, int(os.getenv("HELMET_IMGSZ", 960)))
    HELMET_CONFIRM_FRAMES = max(1, int(os.getenv("HELMET_CONFIRM_FRAMES", 2)))
    HELMET_CONFIRM_WINDOW = max(HELMET_CONFIRM_FRAMES, int(os.getenv("HELMET_CONFIRM_WINDOW", 4)))

    PLATE_CONFIDENCE = float(os.getenv("PLATE_CONFIDENCE", 0.20))
    PLATE_IMGSZ = max(640, int(os.getenv("PLATE_IMGSZ", 960)))
    PLATE_OCR_MIN_CONFIDENCE = float(os.getenv("PLATE_OCR_MIN_CONFIDENCE", 0.18))
    PLATE_CACHE_SECONDS = max(1.0, float(os.getenv("PLATE_CACHE_SECONDS", 8.0)))
    PLATE_CONFIRM_READS = max(1, int(os.getenv("PLATE_CONFIRM_READS", 2)))
    PLATE_CONFIRM_WINDOW = max(PLATE_CONFIRM_READS, int(os.getenv("PLATE_CONFIRM_WINDOW", 5)))

    ROAD_DAMAGE_CONFIDENCE = float(os.getenv("ROAD_DAMAGE_CONFIDENCE", 0.15))
    ROAD_DAMAGE_IMGSZ = max(640, int(os.getenv("ROAD_DAMAGE_IMGSZ", 960)))
    ROAD_EVERY_N_FRAMES = max(1, int(os.getenv("ROAD_EVERY_N_FRAMES", 3)))
    ROAD_ROI_TOP_RATIO = min(0.85, max(0.0, float(os.getenv("ROAD_ROI_TOP_RATIO", 0.22))))

    # Accident detection uses YOLO/ByteTrack trajectories, not a separate accident .pt.
    INCIDENT_DETECTION_ENABLED = _bool("INCIDENT_DETECTION_ENABLED", True)
    INCIDENT_EVERY_N_FRAMES = max(1, int(os.getenv("INCIDENT_EVERY_N_FRAMES", 1)))
    ACCIDENT_DETECTION_ENABLED = _bool("ACCIDENT_DETECTION_ENABLED", True)
    ACCIDENT_CONFIRM_FRAMES = max(1, int(os.getenv("ACCIDENT_CONFIRM_FRAMES", 2)))
    ACCIDENT_MIN_MOTION_RATIO = max(0.0005, float(os.getenv("ACCIDENT_MIN_MOTION_RATIO", 0.004)))
    ACCIDENT_STOP_RATIO = min(0.95, max(0.05, float(os.getenv("ACCIDENT_STOP_RATIO", 0.35))))
    ACCIDENT_PROXIMITY_RATIO = max(0.20, float(os.getenv("ACCIDENT_PROXIMITY_RATIO", 0.80)))
    ACCIDENT_IOU_THRESHOLD = min(0.80, max(0.0, float(os.getenv("ACCIDENT_IOU_THRESHOLD", 0.01))))

    # Fallen trees/branches/debris can be detected as persistent roadway
    # obstructions without a custom obstruction model.
    ROAD_OBSTRUCTION_FALLBACK_ENABLED = _bool("ROAD_OBSTRUCTION_FALLBACK_ENABLED", True)
    OBSTRUCTION_ROI_TOP_RATIO = min(0.85, max(0.0, float(os.getenv("OBSTRUCTION_ROI_TOP_RATIO", 0.25))))
    OBSTRUCTION_MIN_AREA_RATIO = min(0.50, max(0.002, float(os.getenv("OBSTRUCTION_MIN_AREA_RATIO", 0.01))))
    OBSTRUCTION_CONFIRM_FRAMES = max(2, int(os.getenv("OBSTRUCTION_CONFIRM_FRAMES", 6)))
    OBSTRUCTION_WARMUP_FRAMES = max(5, int(os.getenv("OBSTRUCTION_WARMUP_FRAMES", 20)))
    OBSTRUCTION_LEARNING_RATE = min(0.05, max(0.00001, float(os.getenv("OBSTRUCTION_LEARNING_RATE", 0.0005))))
    INCIDENT_COOLDOWN_SECONDS = max(15, int(os.getenv("INCIDENT_COOLDOWN_SECONDS", 120)))

    VIOLATION_COOLDOWN_SECONDS = max(10, int(os.getenv("VIOLATION_COOLDOWN_SECONDS", 90)))
    ROAD_EVENT_COOLDOWN_SECONDS = max(10, int(os.getenv("ROAD_EVENT_COOLDOWN_SECONDS", 120)))
    OCR_ENABLED = _bool("OCR_ENABLED", True)
