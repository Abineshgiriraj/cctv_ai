import cv2
import urllib.parse
import os
import time
import logging
import threading
import uuid
import gc
from collections import defaultdict, deque
from flask import Flask, Response, abort, jsonify, request

from config import Config
from analytics_store import TrafficStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("mjpeg")

app = Flask(__name__)

# Existing raw MJPEG state. Kept separate from AI output so the original
# /video_feed/<camera_key> path remains available as a fallback.
output_frames = {}
latest_cv_frames = {}
frame_sequence = {}
camera_status = {}

# AI output/status is produced from the already-captured OpenCV frames.
# AI workers NEVER open RTSP themselves.
tracked_frames = {}
ai_status = {}

# Per-camera traffic counting state. Counts are based on ByteTrack IDs crossing
# a configured horizontal line, not on repeated frame detections.
traffic_state = {}
SERVER_SESSION_ID = uuid.uuid4().hex[:16]

lock = threading.RLock()
# Serialize heavy YOLO inference across cameras so CPU/GPU work cannot starve
# the RTSP capture threads. Each camera still has its own model/tracker state.
inference_lock = threading.Lock()
foreground_inference_semaphore = threading.Semaphore(
    Config.FOREGROUND_TRACK_CONCURRENCY
)

# In paged-camera mode only the cameras visible on the current Live Monitoring
# page open RTSP and run AI. This prevents 90+ simultaneous NVR streams/models
# from exhausting the workstation and causing 10+ second inference latency.
active_camera_keys = set()
focus_initialized = False

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

try:
    traffic_store = TrafficStore(Config.ANALYTICS_DB)
    log.info("Traffic analytics DB ready path=%s session=%s", Config.ANALYTICS_DB, SERVER_SESSION_ID)
except Exception as exc:
    traffic_store = None
    log.exception("Traffic analytics persistence disabled: %s", exc)


def allowed_keys():
    return [c["camera_key"] for c in Config.CAMERAS]


def _ensure_initial_focus():
    global focus_initialized
    if not Config.PAGED_CAMERA_MODE:
        return
    with lock:
        if not focus_initialized:
            active_camera_keys.update(allowed_keys()[:Config.ACTIVE_CAMERA_LIMIT])
            focus_initialized = True


def is_camera_focused(camera_key: str) -> bool:
    """Whether a camera belongs to the currently displayed UI page."""
    if not Config.PAGED_CAMERA_MODE:
        return True
    _ensure_initial_focus()
    with lock:
        return camera_key in active_camera_keys


def is_camera_active(camera_key: str) -> bool:
    """Whether RTSP/background monitoring should remain active."""
    if Config.MONITOR_ALL_CAMERAS:
        return True
    return is_camera_focused(camera_key)


def set_active_cameras(keys):
    global focus_initialized
    valid = [key for key in keys if key in allowed_keys()]
    if Config.PAGED_CAMERA_MODE:
        valid = valid[:Config.ACTIVE_CAMERA_LIMIT]
    with lock:
        active_camera_keys.clear()
        active_camera_keys.update(valid if Config.PAGED_CAMERA_MODE else allowed_keys())
        focus_initialized = True
    return list(active_camera_keys)


def _clear_camera_buffers(camera_key: str):
    with lock:
        output_frames.pop(camera_key, None)
        latest_cv_frames.pop(camera_key, None)
        tracked_frames.pop(camera_key, None)


def rtsp_url(camera: dict) -> str:
    """Build RTSP URL without hard-coding credentials in source control."""
    camera_ip = camera["camera_ip"]
    user = Config.CAMERA_USERNAME
    password = Config.CAMERA_PASSWORD
    recorder_ips = {
        value.strip()
        for value in os.getenv("RECORDER_CAMERA_IPS", "").split(",")
        if value.strip()
    }
    if camera_ip in recorder_ips:
        user = os.getenv("RECORDER_CAMERA_USERNAME", user)
        password = os.getenv("RECORDER_CAMERA_PASSWORD", password)

    return (
        f"rtsp://{urllib.parse.quote(user, safe='')}:{urllib.parse.quote(password, safe='')}"
        f"@{camera_ip}:554/cam/realmonitor"
        f"?channel={int(camera['channel_no'])}&subtype={Config.CAMERA_SUBTYPE}"
    )


def advanced_model_readiness():
    models = {
        "helmet": Config.HELMET_MODEL,
        "road_damage": Config.ROAD_DAMAGE_MODEL,
        "road_obstruction": Config.ROAD_OBSTRUCTION_MODEL,
    }
    return {
        name: {
            "configured_path": path,
            "available": bool(path and os.path.isfile(path)),
        }
        for name, path in models.items()
    }


def set_status(camera_key: str, **fields):
    with lock:
        row = camera_status.setdefault(
            camera_key,
            {
                "connected": False,
                "frames": 0,
                "last_jpeg_bytes": 0,
                "last_frame_at": None,
                "last_error": None,
                "last_error_at": None,
                "standby": False,
            },
        )
        row.update(fields)


def default_ai_row():
    return {
        "enabled": True,
        "model_loaded": False,
        "tracked_frames": 0,
        "last_jpeg_bytes": 0,
        "last_processed_at": None,
        "last_inference_ms": None,
        "last_error": None,
        "last_error_at": None,
        "persons": 0,
        "vehicles": 0,
        "objects": 0,
        "class_counts": {},
        "session_vehicle_counts": {},
        "session_vehicle_total": 0,
    }


def set_ai_status(camera_key: str, **fields):
    with lock:
        row = ai_status.setdefault(camera_key, default_ai_row())
        row.update(fields)


def capture_stream(camera: dict):
    camera_key = camera["camera_key"]
    camera_ip = camera["camera_ip"]
    """Keep RTSP open for every camera when MONITOR_ALL_CAMERAS is enabled."""
    reconnect_delay = Config.RTSP_RECONNECT_SECONDS + (
        int(camera.get("channel_no") or 1) % 5
    ) * 0.35
    live_encode_interval = 1.0 / max(Config.LIVE_STREAM_MAX_FPS, 1.0)
    last_live_encode_at = 0.0

    while True:
        if not is_camera_active(camera_key):
            _clear_camera_buffers(camera_key)
            set_status(camera_key, connected=False, standby=True, last_error=None)
            time.sleep(0.25)
            continue

        url = rtsp_url(camera)
        log.info(
            "Connecting RTSP camera=%s channel=%s subtype=%s transport=tcp",
            camera_key,
            camera["channel_no"],
            Config.CAMERA_SUBTYPE,
        )
        set_status(camera_key, standby=False)
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        if not cap.isOpened():
            msg = (
                f"OpenCV could not open RTSP for {camera_ip}:554 "
                f"(channel={camera['channel_no']}, subtype={Config.CAMERA_SUBTYPE}). "
                "Check network, credentials, channel availability, and RTSP service."
            )
            log.error(msg)
            set_status(camera_key, connected=False, standby=False, last_error=msg, last_error_at=time.time())
            cap.release()
            time.sleep(reconnect_delay)
            continue

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        log.info("RTSP open camera=%s size=%sx%s fps=%s", camera_key, width, height, fps)
        set_status(camera_key, connected=True, standby=False, last_error=None)

        fail_reads = 0
        while is_camera_active(camera_key):
            ret, frame = cap.read()
            if not ret or frame is None:
                fail_reads += 1
                msg = f"RTSP read failed for {camera_ip} (consecutive={fail_reads})"
                log.warning(msg)
                set_status(camera_key, connected=False, last_error=msg, last_error_at=time.time())
                if fail_reads >= 3:
                    break
                time.sleep(0.2)
                continue

            fail_reads = 0

            # Every camera keeps its latest OpenCV frame for background AI.
            # MJPEG encoding is only needed for cameras currently visible in the UI,
            # which avoids encoding 98 streams continuously.
            with lock:
                latest_cv_frames[camera_key] = frame
                frame_sequence[camera_key] = int(frame_sequence.get(camera_key) or 0) + 1
                row = camera_status.setdefault(camera_key, {})
                row["connected"] = True
                row["standby"] = False
                row["frames"] = int(row.get("frames") or 0) + 1
                row["last_frame_at"] = time.time()
                row["last_error"] = None

            if Config.MONITOR_ALL_CAMERAS and not is_camera_focused(camera_key):
                with lock:
                    output_frames.pop(camera_key, None)
                    row = camera_status.setdefault(camera_key, {})
                    row["last_jpeg_bytes"] = 0
                continue

            # Keep capture/AI fed by every decoded frame, but encode only enough
            # frames for a smooth browser stream. Encoding every 25/30-FPS frame
            # on 4-8 cards wastes CPU and can make RTSP/AI threads stall.
            now = time.time()
            if now - last_live_encode_at < live_encode_interval:
                continue
            last_live_encode_at = now

            ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), Config.JPEG_QUALITY],
            )
            if not ok:
                msg = f"JPEG encode failed for {camera_ip}"
                log.error(msg)
                set_status(camera_key, last_error=msg, last_error_at=time.time())
                continue

            jpeg = encoded.tobytes()
            if len(jpeg) < 100 or jpeg[:2] != b"\xff\xd8":
                msg = f"Invalid JPEG from {camera_key} bytes={len(jpeg)}"
                log.error(msg)
                set_status(camera_key, last_error=msg, last_error_at=time.time())
                continue

            with lock:
                output_frames[camera_key] = jpeg
                row = camera_status.setdefault(camera_key, {})
                row["last_jpeg_bytes"] = len(jpeg)

        cap.release()
        if not is_camera_active(camera_key):
            log.info("Camera moved to standby camera=%s", camera_key)
            _clear_camera_buffers(camera_key)
            set_status(camera_key, connected=False, standby=True, last_error=None)
            continue

        log.error("Reconnecting camera=%s in %ss", camera_key, reconnect_delay)
        time.sleep(reconnect_delay)


def _class_name(model, cls_id: int) -> str:
    names = getattr(model, "names", {})
    if isinstance(names, dict):
        return str(names.get(cls_id, cls_id))
    try:
        return str(names[cls_id])
    except Exception:
        return str(cls_id)


def _draw_label(frame, text, x1, y1, color):
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.52
    thickness = 1
    (tw, th), baseline = cv2.getTextSize(text, font, scale, thickness)
    top = max(0, y1 - th - baseline - 8)
    right = min(frame.shape[1] - 1, x1 + tw + 10)
    cv2.rectangle(frame, (x1, top), (right, y1), color, -1)
    cv2.putText(
        frame,
        text,
        (x1 + 5, max(th + 2, y1 - 6)),
        font,
        scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )


def _draw_summary(frame, persons, vehicles, inference_ms, session_total=0):
    text = (
        f"Persons {persons}   Vehicles {vehicles}   "
        f"Counted {session_total}   AI {inference_ms:.0f} ms"
    )
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.58
    thickness = 2
    (tw, th), baseline = cv2.getTextSize(text, font, scale, thickness)
    overlay = frame.copy()
    cv2.rectangle(
        overlay,
        (12, 12),
        (min(frame.shape[1] - 12, tw + 34), th + baseline + 30),
        (7, 18, 29),
        -1,
    )
    cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)
    cv2.putText(frame, text, (24, th + 24), font, scale, (44, 223, 255), thickness, cv2.LINE_AA)


def _draw_count_line(frame, line_y):
    if not Config.COUNTING_ENABLED:
        return
    color = (44, 223, 255)
    cv2.line(frame, (0, line_y), (frame.shape[1] - 1, line_y), color, 2, cv2.LINE_AA)
    cv2.putText(
        frame,
        "COUNTING LINE",
        (12, max(22, line_y - 8)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        color,
        2,
        cv2.LINE_AA,
    )


def _new_traffic_state():
    return {
        "previous_centers": {},
        "track_age": defaultdict(int),
        "counted_ids": set(),
        "session_counts": defaultdict(int),
        "session_total": 0,
    }


def _maybe_count_object(camera: dict, state, *, track_id, cls_id, vehicle_type,
                         confidence, center, line_y):
    if not Config.COUNTING_ENABLED or track_id is None or cls_id not in Config.COUNTED_CLASSES:
        return

    state["track_age"][track_id] += 1
    previous = state["previous_centers"].get(track_id)
    state["previous_centers"][track_id] = center

    if previous is None or track_id in state["counted_ids"]:
        return
    if state["track_age"][track_id] < Config.COUNT_MIN_TRACK_AGE:
        return
    if float(confidence) < Config.COUNT_MIN_CONFIDENCE:
        return

    previous_y = previous[1]
    current_y = center[1]
    if previous_y < line_y <= current_y:
        direction = "down"
    elif previous_y > line_y >= current_y:
        direction = "up"
    else:
        return

    state["counted_ids"].add(track_id)
    state["session_counts"][vehicle_type] += 1
    state["session_total"] += 1

    stored = False
    if traffic_store is not None:
        try:
            stored = traffic_store.record_vehicle(
                session_id=SERVER_SESSION_ID,
                camera=camera,
                track_id=track_id,
                vehicle_type=vehicle_type,
                direction=direction,
                confidence=confidence,
            )
        except Exception as exc:
            log.exception("Vehicle storage failed camera=%s: %s", camera["camera_key"], exc)

    log.info(
        "Vehicle counted camera=%s type=%s track=%s direction=%s confidence=%.2f stored=%s",
        camera["camera_key"],
        vehicle_type,
        track_id,
        direction,
        confidence,
        stored,
    )



class _ScalarValue:
    def __init__(self, value):
        self.value = value

    def __getitem__(self, _index):
        return self

    def item(self):
        return self.value


class _VectorValue:
    def __init__(self, values):
        self.values = list(values)

    def __getitem__(self, _index):
        return self

    def tolist(self):
        return list(self.values)


class _TrackedBox:
    def __init__(self, cls_id, confidence, xyxy, track_id):
        self.cls = _ScalarValue(int(cls_id))
        self.conf = _ScalarValue(float(confidence))
        self.xyxy = _VectorValue(xyxy)
        self.id = _ScalarValue(int(track_id)) if track_id is not None else None


class _TrackedResult:
    def __init__(self, boxes):
        self.boxes = boxes


def _box_iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(1.0, (ax2 - ax1) * (ay2 - ay1))
    area_b = max(1.0, (bx2 - bx1) * (by2 - by1))
    return inter / (area_a + area_b - inter)


class _SimpleCameraTracker:
    """Per-camera motion tracker used by shared YOLO workers.

    The previous matcher required an exact class match and mostly compared the
    latest box. On busy junctions that made IDs jump whenever YOLO briefly
    changed car/truck/bus classification or when the next inference arrived a
    second later. This tracker predicts each track forward, allows compatible
    road-vehicle classes to match, keeps a class vote per track and smooths the
    box/velocity before emitting the next result.
    """

    VEHICLE_FAMILY = {1, 2, 3, 5, 7}

    def __init__(self):
        self.next_id = 1
        self.frame_index = 0
        self.tracks = {}

    @staticmethod
    def _center(box):
        x1, y1, x2, y2 = box
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

    @staticmethod
    def _diag(box):
        x1, y1, x2, y2 = box
        return max(1.0, ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)

    @staticmethod
    def _area(box):
        x1, y1, x2, y2 = box
        return max(1.0, (x2 - x1) * (y2 - y1))

    @classmethod
    def _compatible_class(cls, previous, current):
        if previous == current:
            return True
        return previous in cls.VEHICLE_FAMILY and current in cls.VEHICLE_FAMILY

    @staticmethod
    def _shift_box(box, dx, dy):
        return [
            box[0] + dx,
            box[1] + dy,
            box[2] + dx,
            box[3] + dy,
        ]

    @staticmethod
    def _blend_box(previous, current, current_weight=0.72):
        old_weight = 1.0 - current_weight
        return [
            old_weight * previous[i] + current_weight * current[i]
            for i in range(4)
        ]

    def _predict_box(self, track, age):
        vx, vy = track.get("velocity", (0.0, 0.0))
        return self._shift_box(track["box"], vx * age, vy * age)

    def _stable_class(self, track):
        votes = track.get("class_votes") or {}
        if not votes:
            return int(track["cls_id"])
        return int(max(votes.items(), key=lambda item: item[1])[0])

    def update(self, result):
        self.frame_index += 1
        detections = []
        boxes = getattr(result, "boxes", None)
        if boxes is not None:
            for box in boxes:
                try:
                    cls_id = int(box.cls[0].item())
                    confidence = float(box.conf[0].item())
                    xyxy = [float(v) for v in box.xyxy[0].tolist()]
                except Exception:
                    continue
                detections.append({
                    "cls_id": cls_id,
                    "confidence": confidence,
                    "box": xyxy,
                })

        assigned_tracks = set()
        tracked_boxes = []

        # High-confidence observations claim a track first.
        for detection in sorted(
            detections,
            key=lambda row: row["confidence"],
            reverse=True,
        ):
            cls_id = detection["cls_id"]
            box = detection["box"]
            center = self._center(box)
            area = self._area(box)
            best = None

            for track_id, track in self.tracks.items():
                if track_id in assigned_tracks:
                    continue

                age = self.frame_index - track["last_seen"]
                if age > 12:
                    continue
                if not self._compatible_class(track["cls_id"], cls_id):
                    continue

                predicted = self._predict_box(track, age)
                predicted_center = self._center(predicted)
                predicted_area = self._area(predicted)
                iou = _box_iou(box, predicted)

                distance = (
                    (center[0] - predicted_center[0]) ** 2
                    + (center[1] - predicted_center[1]) ** 2
                ) ** 0.5
                distance_limit = max(
                    self._diag(box),
                    self._diag(predicted),
                    24.0,
                ) * 1.45
                norm_distance = distance / max(1.0, distance_limit)
                size_ratio = min(area, predicted_area) / max(area, predicted_area)

                # Reject implausible jumps. A low-IoU match is still allowed when
                # the predicted center and object size remain plausible.
                if iou < 0.01 and norm_distance > 1.0:
                    continue
                if size_ratio < 0.20 and iou < 0.08:
                    continue

                class_penalty = 0.0 if track["cls_id"] == cls_id else 0.12
                score = (
                    iou * 2.40
                    + max(0.0, 1.0 - norm_distance) * 1.35
                    + size_ratio * 0.45
                    - class_penalty
                    - age * 0.02
                )
                if best is None or score > best[0]:
                    best = (score, track_id, predicted)

            if best is None or best[0] < 0.25:
                track_id = self.next_id
                self.next_id += 1
                class_votes = defaultdict(float)
                class_votes[cls_id] += max(0.05, detection["confidence"])
                self.tracks[track_id] = {
                    "cls_id": cls_id,
                    "box": box,
                    "last_seen": self.frame_index,
                    "velocity": (0.0, 0.0),
                    "confidence": detection["confidence"],
                    "class_votes": class_votes,
                }
            else:
                _score, track_id, predicted = best
                track = self.tracks[track_id]
                age = max(1, self.frame_index - track["last_seen"])
                previous_center = self._center(track["box"])
                observed_dx = (center[0] - previous_center[0]) / age
                observed_dy = (center[1] - previous_center[1]) / age
                old_vx, old_vy = track.get("velocity", (0.0, 0.0))
                velocity = (
                    old_vx * 0.55 + observed_dx * 0.45,
                    old_vy * 0.55 + observed_dy * 0.45,
                )

                smoothed_box = self._blend_box(predicted, box, 0.78)
                class_votes = track.get("class_votes")
                if class_votes is None:
                    class_votes = defaultdict(float)
                # Slowly decay old class evidence so a genuinely changed/stable
                # classification can eventually win without flickering each frame.
                for vote_cls in list(class_votes):
                    class_votes[vote_cls] *= 0.96
                class_votes[cls_id] += max(0.05, detection["confidence"])

                track.update({
                    "box": smoothed_box,
                    "last_seen": self.frame_index,
                    "velocity": velocity,
                    "confidence": (
                        float(track.get("confidence", detection["confidence"])) * 0.45
                        + detection["confidence"] * 0.55
                    ),
                    "class_votes": class_votes,
                })
                track["cls_id"] = self._stable_class(track)

            assigned_tracks.add(track_id)
            track = self.tracks[track_id]
            stable_cls = self._stable_class(track)
            track["cls_id"] = stable_cls
            tracked_boxes.append(
                _TrackedBox(
                    stable_cls,
                    float(track.get("confidence", detection["confidence"])),
                    track["box"],
                    track_id,
                )
            )

        stale = [
            track_id
            for track_id, track in self.tracks.items()
            if self.frame_index - track["last_seen"] > 20
        ]
        for track_id in stale:
            self.tracks.pop(track_id, None)

        return _TrackedResult(tracked_boxes)


def shared_ai_worker(worker_id: int, cameras, background_only: bool = False):
    """Continuously monitor cameras with one shared YOLO model.

    When background_only=True, focused UI cameras are deliberately excluded so
    they can use the original per-camera model.track(..., persist=True,
    bytetrack.yaml) path without competing with the shared predictor.
    """
    try:
        from ultralytics import YOLO
        model = YOLO(Config.YOLO_MODEL)
    except Exception as exc:
        log.exception("Shared AI worker %s failed to load model: %s", worker_id, exc)
        for camera in cameras:
            set_ai_status(
                camera["camera_key"],
                model_loaded=False,
                last_error=f"YOLO model load failed: {exc}",
                last_error_at=time.time(),
            )
        return

    log.info(
        "Shared AI worker %s ready cameras=%s",
        worker_id,
        [camera["camera_key"] for camera in cameras],
    )

    foreground_interval = 1.0 / max(Config.FOREGROUND_AI_FPS, 0.10)
    background_interval = 1.0 / max(Config.BACKGROUND_AI_FPS, 0.02)
    focus_cursor = 0
    background_cursor = 0
    foreground_turns = 0
    states = {}
    for camera in cameras:
        key = camera["camera_key"]
        state = {
            "last_seq": -1,
            "last_started": 0.0,
            "history": defaultdict(lambda: deque(maxlen=Config.TRACK_TRAIL_LENGTH)),
            "last_seen": {},
            "processed_index": 0,
            "count_state": _new_traffic_state(),
            "tracker": _SimpleCameraTracker(),
        }
        states[key] = state
        with lock:
            traffic_state[key] = state["count_state"]
        set_ai_status(
            key,
            enabled=True,
            model_loaded=True,
            last_error=None,
            monitor_mode="shared_all_cameras",
        )

    while True:
        did_work = False

        focused = [] if background_only else [
            camera for camera in cameras
            if is_camera_focused(camera["camera_key"])
        ]
        background = [
            camera for camera in cameras
            if not is_camera_focused(camera["camera_key"])
        ]

        # Give the visible feeds most inference turns so tracking looks like
        # tracking rather than a series of unrelated detections. Hidden cameras
        # still rotate through background monitoring, but only after several
        # foreground turns.
        ordered_cameras = []
        if focused:
            ordered_cameras.append(focused[focus_cursor % len(focused)])
            focus_cursor += 1
            foreground_turns += 1

        should_scan_background = (
            not focused
            or foreground_turns >= Config.BACKGROUND_SCAN_EVERY_N_FOREGROUND
        )
        if background and should_scan_background:
            take = min(Config.BACKGROUND_CAMERAS_PER_CYCLE, len(background))
            for _ in range(take):
                ordered_cameras.append(
                    background[background_cursor % len(background)]
                )
                background_cursor += 1
            foreground_turns = 0

        if not ordered_cameras:
            ordered_cameras = cameras[:1]

        for camera in ordered_cameras:
            camera_key = camera["camera_key"]
            state = states[camera_key]
            focused_now = is_camera_focused(camera_key)
            min_interval = foreground_interval if focused_now else background_interval

            with lock:
                frame = latest_cv_frames.get(camera_key)
                seq = int(frame_sequence.get(camera_key) or 0)

            if frame is None or seq == state["last_seq"]:
                continue
            if time.time() - state["last_started"] < min_interval:
                continue

            did_work = True
            state["last_seq"] = seq
            state["last_started"] = time.time()
            work = frame.copy()
            started = time.perf_counter()

            try:
                inference_size = (
                    Config.FOREGROUND_YOLO_IMGSZ
                    if focused_now
                    else Config.BACKGROUND_YOLO_IMGSZ
                )
                results = model.predict(
                    source=work,
                    classes=Config.TARGET_CLASSES,
                    conf=Config.CONFIDENCE_THRESHOLD,
                    iou=Config.IOU_THRESHOLD,
                    imgsz=inference_size,
                    device=Config.YOLO_DEVICE or None,
                    verbose=False,
                )
                inference_ms = (time.perf_counter() - started) * 1000.0
                state["processed_index"] += 1

                raw_result = results[0] if results else None
                result = state["tracker"].update(raw_result) if raw_result is not None else None

                if result is not None:
                    persons, vehicles, class_counts = _annotate_tracking(
                        camera,
                        work,
                        result,
                        model,
                        state["history"],
                        state["last_seen"],
                        state["processed_index"],
                        inference_ms,
                        state["count_state"],
                    )
                else:
                    persons, vehicles, class_counts = 0, 0, {}
                    _draw_count_line(work, int(work.shape[0] * Config.COUNT_LINE_Y_RATIO))
                    _draw_summary(
                        work,
                        persons,
                        vehicles,
                        inference_ms,
                        state["count_state"]["session_total"],
                    )

                jpeg = None
                if Config.GENERATE_TRACKED_MJPEG and is_camera_focused(camera_key):
                    ok, encoded = cv2.imencode(
                        ".jpg",
                        work,
                        [int(cv2.IMWRITE_JPEG_QUALITY), Config.JPEG_QUALITY],
                    )
                    if not ok:
                        raise RuntimeError("AI JPEG encode failed")
                    jpeg = encoded.tobytes()

                now = time.time()
                with lock:
                    if jpeg is not None:
                        tracked_frames[camera_key] = jpeg
                    else:
                        tracked_frames.pop(camera_key, None)
                    row = ai_status.setdefault(camera_key, default_ai_row())
                    row["model_loaded"] = True
                    row["monitor_mode"] = "shared_all_cameras"
                    row["tracked_frames"] = int(row.get("tracked_frames") or 0) + 1
                    row["last_jpeg_bytes"] = len(jpeg) if jpeg is not None else 0
                    row["last_processed_at"] = now
                    row["last_inference_ms"] = round(inference_ms, 1)
                    row["last_error"] = None
                    row["persons"] = persons
                    row["vehicles"] = vehicles
                    row["objects"] = persons + vehicles
                    row["class_counts"] = class_counts
                    row["session_vehicle_counts"] = dict(state["count_state"]["session_counts"])
                    row["session_vehicle_total"] = int(state["count_state"]["session_total"])

            except Exception as exc:
                msg = f"Shared AI inference failed: {exc}"
                log.exception("%s camera=%s", msg, camera_key)
                set_ai_status(
                    camera_key,
                    model_loaded=True,
                    last_error=msg,
                    last_error_at=time.time(),
                )

        if not did_work:
            time.sleep(0.02)


def _annotate_tracking(camera, frame, result, model, history, last_seen,
                       processed_index, inference_ms, count_state):
    persons = 0
    vehicles = 0
    class_counts = defaultdict(int)
    detection_rows = []

    # BGR colors chosen to remain visible on common road scenes.
    class_colors = {
        0: (52, 211, 153),    # person
        1: (250, 204, 21),    # bicycle
        2: (56, 189, 248),    # car
        3: (192, 132, 252),   # motorcycle
        5: (251, 146, 60),    # bus
        7: (244, 114, 182),   # truck
    }

    line_y = int(frame.shape[0] * Config.COUNT_LINE_Y_RATIO)
    _draw_count_line(frame, line_y)

    boxes = getattr(result, "boxes", None)
    if boxes is not None:
        for box in boxes:
            try:
                cls_id = int(box.cls[0].item())
                conf = float(box.conf[0].item())
                xyxy = box.xyxy[0].tolist()
                x1, y1, x2, y2 = [int(v) for v in xyxy]
            except Exception:
                continue

            x1 = max(0, min(frame.shape[1] - 1, x1))
            y1 = max(0, min(frame.shape[0] - 1, y1))
            x2 = max(0, min(frame.shape[1] - 1, x2))
            y2 = max(0, min(frame.shape[0] - 1, y2))
            if x2 <= x1 or y2 <= y1:
                continue

            track_id = None
            try:
                if box.id is not None:
                    track_id = int(box.id[0].item())
            except Exception:
                track_id = None

            name = _class_name(model, cls_id)
            normalized_name = name.lower()
            class_counts[normalized_name] += 1
            if cls_id == 0:
                persons += 1
            elif cls_id in Config.VEHICLE_CLASSES:
                vehicles += 1

            if conf >= Config.LIVE_OVERLAY_MIN_CONFIDENCE:
                detection_rows.append({
                    "class_id": cls_id,
                    "label": normalized_name,
                    "confidence": round(conf, 4),
                    "track_id": track_id,
                    "box": [x1, y1, x2, y2],
                })

            color = class_colors.get(cls_id, (44, 223, 255))
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            id_text = f" #{track_id}" if track_id is not None else ""
            _draw_label(frame, f"{name.upper()}{id_text} {conf * 100:.0f}%", x1, y1, color)

            if track_id is not None:
                last_seen[track_id] = processed_index
                center = ((x1 + x2) // 2, (y1 + y2) // 2)
                history[track_id].append(center)
                points = list(history[track_id])
                if len(points) > 1:
                    for p1, p2 in zip(points[:-1], points[1:]):
                        cv2.line(frame, p1, p2, color, 2, cv2.LINE_AA)

                _maybe_count_object(
                    camera,
                    count_state,
                    track_id=track_id,
                    cls_id=cls_id,
                    vehicle_type=normalized_name,
                    confidence=conf,
                    center=center,
                    line_y=line_y,
                )

    # Keep only transient tracking history bounded. counted_ids intentionally remains
    # for the server session so an occluded track cannot be counted twice.
    stale = [track_id for track_id, seen_at in last_seen.items() if processed_index - seen_at > 90]
    for track_id in stale:
        last_seen.pop(track_id, None)
        history.pop(track_id, None)
        count_state["previous_centers"].pop(track_id, None)
        count_state["track_age"].pop(track_id, None)

    _draw_summary(frame, persons, vehicles, inference_ms, count_state["session_total"])
    set_ai_status(
        camera["camera_key"],
        detections=detection_rows,
        source_width=int(frame.shape[1]),
        source_height=int(frame.shape[0]),
        detections_at=time.time(),
    )
    return persons, vehicles, dict(class_counts)


def ai_tracking_worker(camera: dict, focus_only: bool = False, exit_on_unfocus: bool = False):
    camera_key = camera["camera_key"]
    camera_ip = camera["camera_ip"]
    """Run true Ultralytics ByteTrack for one camera.

    focus_only=True restores the original frame-by-frame tracking path only for
    cameras currently displayed in the UI. Hidden cameras continue through the
    shared background monitor.
    """

    set_ai_status(camera_key, enabled=True, model_loaded=False, last_error=None)
    model = None
    last_active_at = 0.0
    unfocused_since = None
    last_seq = -1
    last_started = 0.0
    if focus_only and Config.FOREGROUND_TRACK_FPS <= 0:
        min_interval = 0.0
    elif focus_only:
        min_interval = 1.0 / max(Config.FOREGROUND_TRACK_FPS, 0.1)
    else:
        min_interval = 1.0 / max(Config.AI_MAX_FPS, 0.25)
    history = defaultdict(lambda: deque(maxlen=Config.TRACK_TRAIL_LENGTH))
    last_seen = {}
    processed_index = 0
    count_state = _new_traffic_state()
    with lock:
        traffic_state[camera_key] = count_state

    while True:
        active_now = is_camera_focused(camera_key) if focus_only else is_camera_active(camera_key)
        if not active_now:
            if unfocused_since is None:
                unfocused_since = time.time()
            idle_limit = (
                Config.FOREGROUND_TRACK_IDLE_SECONDS
                if focus_only
                else Config.AI_MODEL_IDLE_UNLOAD_SECONDS
            )
            if model is not None and (time.time() - last_active_at) >= idle_limit:
                log.info("Unloading idle YOLO tracker camera=%s focus_only=%s", camera_key, focus_only)
                model = None
                history.clear()
                last_seen.clear()
                with lock:
                    tracked_frames.pop(camera_key, None)
                set_ai_status(camera_key, model_loaded=False, last_error=None)
                gc.collect()
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass
            if focus_only and exit_on_unfocus and (time.time() - unfocused_since) >= idle_limit:
                return
            time.sleep(0.08 if focus_only else 0.20)
            continue

        unfocused_since = None
        last_active_at = time.time()

        if model is None:
            try:
                from ultralytics import YOLO
                log.info("Loading YOLO model camera=%s model=%s", camera_key, Config.YOLO_MODEL)
                model = YOLO(Config.YOLO_MODEL)
                set_ai_status(camera_key, model_loaded=True, last_error=None)
                log.info(
                    "AI tracker ready camera=%s classes=%s mode=%s",
                    camera_key,
                    Config.TARGET_CLASSES,
                    "focused-bytetrack" if focus_only else "dedicated",
                )
                last_seq = -1
            except Exception as exc:
                msg = f"YOLO model load failed: {exc}"
                log.exception("AI disabled camera=%s: %s", camera_key, msg)
                set_ai_status(camera_key, model_loaded=False, last_error=msg, last_error_at=time.time())
                time.sleep(3)
                continue

        with lock:
            frame = latest_cv_frames.get(camera_key)
            seq = int(frame_sequence.get(camera_key) or 0)

        if frame is None or seq == last_seq:
            time.sleep(0.02)
            continue

        remaining = min_interval - (time.time() - last_started)
        if remaining > 0:
            time.sleep(min(remaining, 0.05))
            continue

        work = frame.copy()
        last_seq = seq
        last_started = time.time()
        started = time.perf_counter()

        try:
            track_imgsz = (
                Config.FOREGROUND_TRACK_IMGSZ
                if focus_only
                else Config.YOLO_IMGSZ
            )
            gate = foreground_inference_semaphore if focus_only else inference_lock
            with gate:
                results = model.track(
                    source=work,
                    persist=True,
                    tracker="bytetrack.yaml",
                    classes=Config.TARGET_CLASSES,
                    conf=Config.CONFIDENCE_THRESHOLD,
                    iou=Config.IOU_THRESHOLD,
                    imgsz=track_imgsz,
                    device=Config.YOLO_DEVICE or None,
                    verbose=False,
                )
            inference_ms = (time.perf_counter() - started) * 1000.0
            processed_index += 1

            result = results[0] if results else None
            if result is not None:
                persons, vehicles, class_counts = _annotate_tracking(
                    camera,
                    work,
                    result,
                    model,
                    history,
                    last_seen,
                    processed_index,
                    inference_ms,
                    count_state,
                )
            else:
                persons, vehicles, class_counts = 0, 0, {}
                _draw_count_line(work, int(work.shape[0] * Config.COUNT_LINE_Y_RATIO))
                _draw_summary(work, persons, vehicles, inference_ms, count_state["session_total"])

            jpeg = None
            if Config.GENERATE_TRACKED_MJPEG:
                ok, encoded = cv2.imencode(
                    ".jpg",
                    work,
                    [int(cv2.IMWRITE_JPEG_QUALITY), Config.JPEG_QUALITY],
                )
                if not ok:
                    raise RuntimeError("AI JPEG encode failed")
                jpeg = encoded.tobytes()

            now = time.time()
            with lock:
                if jpeg is not None:
                    tracked_frames[camera_key] = jpeg
                else:
                    tracked_frames.pop(camera_key, None)
                row = ai_status.setdefault(camera_key, default_ai_row())
                row["model_loaded"] = True
                row["monitor_mode"] = "focused_bytetrack" if focus_only else "dedicated_bytetrack"
                row["tracked_frames"] = int(row.get("tracked_frames") or 0) + 1
                row["last_jpeg_bytes"] = len(jpeg) if jpeg is not None else 0
                row["last_processed_at"] = now
                row["last_inference_ms"] = round(inference_ms, 1)
                row["last_error"] = None
                row["persons"] = persons
                row["vehicles"] = vehicles
                row["objects"] = persons + vehicles
                row["class_counts"] = class_counts
                row["session_vehicle_counts"] = dict(count_state["session_counts"])
                row["session_vehicle_total"] = int(count_state["session_total"])

        except Exception as exc:
            msg = f"AI inference failed: {exc}"
            log.exception("%s camera=%s", msg, camera_ip)
            set_ai_status(camera_key, last_error=msg, last_error_at=time.time())
            time.sleep(0.5)


_foreground_tracker_threads = {}
_foreground_tracker_threads_lock = threading.Lock()


def foreground_tracker_manager(cameras):
    """Start/stop true ByteTrack workers as the operator changes visible cameras."""
    camera_by_key = {camera["camera_key"]: camera for camera in cameras}
    while True:
        _ensure_initial_focus()
        with lock:
            focused_keys = list(active_camera_keys)

        with _foreground_tracker_threads_lock:
            # Remove completed workers.
            for key, thread in list(_foreground_tracker_threads.items()):
                if not thread.is_alive():
                    _foreground_tracker_threads.pop(key, None)

            # A focused camera gets its own persistent ByteTrack state, matching
            # the original pre-shared-worker tracking behavior.
            for key in focused_keys:
                thread = _foreground_tracker_threads.get(key)
                if thread is not None and thread.is_alive():
                    continue
                camera = camera_by_key.get(key)
                if camera is None:
                    continue
                thread = threading.Thread(
                    target=ai_tracking_worker,
                    args=(camera, True, True),
                    daemon=True,
                    name=f"foreground-bytetrack-{key}",
                )
                _foreground_tracker_threads[key] = thread
                thread.start()

        time.sleep(0.15)


def generate_mjpeg(camera_key: str, source: str = "raw"):
    last_log = 0.0
    while True:
        with lock:
            if source == "ai":
                jpeg = tracked_frames.get(camera_key)
                err = (ai_status.get(camera_key) or {}).get("last_error")
            else:
                jpeg = output_frames.get(camera_key)
                err = (camera_status.get(camera_key) or {}).get("last_error")

        if jpeg is None:
            now = time.time()
            if now - last_log > 5:
                log.warning(
                    "MJPEG client waiting for first %s frame camera=%s error=%s",
                    source,
                    camera_key,
                    err or "no frame available yet",
                )
                last_log = now
            time.sleep(0.1)
            continue

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
            + jpeg
            + b"\r\n"
        )
        time.sleep(1 / 15)


@app.after_request
def add_headers(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.route("/system/active_cameras", methods=["GET", "POST"])
def active_cameras_api():
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        keys = payload.get("camera_keys") or []
        if not isinstance(keys, list):
            return jsonify({"ok": False, "error": "camera_keys must be a list"}), 400
        active = set_active_cameras([str(key) for key in keys])
    else:
        # Query-string mode avoids browser CORS/preflight problems between
        # localhost (PHP) and 127.0.0.1:5000 (Flask).
        raw_keys = (request.args.get("keys") or "").strip()
        if raw_keys:
            active = set_active_cameras([key for key in raw_keys.split(",") if key])
        else:
            _ensure_initial_focus()
            with lock:
                active = list(active_camera_keys) if Config.PAGED_CAMERA_MODE else allowed_keys()
    return jsonify({
        "ok": True,
        "paged_mode": Config.PAGED_CAMERA_MODE,
        "monitor_all_cameras": Config.MONITOR_ALL_CAMERAS,
        "limit": Config.ACTIVE_CAMERA_LIMIT,
        "active_camera_keys": active,
        "monitored_total": len(allowed_keys()) if Config.MONITOR_ALL_CAMERAS else len(active),
        "configured_total": len(allowed_keys()),
    })


@app.route("/live/detections")
def live_detections():
    requested = (request.args.get("keys") or "").strip()
    keys = [key for key in requested.split(",") if key] if requested else []
    if not keys:
        _ensure_initial_focus()
        with lock:
            keys = list(active_camera_keys)
    allowed = set(allowed_keys())
    keys = [key for key in keys if key in allowed][:16]
    now = time.time()
    payload = {}
    with lock:
        for key in keys:
            row = dict(ai_status.get(key) or default_ai_row())
            detected_at = row.get("detections_at")
            payload[key] = {
                "detections": row.get("detections") or [],
                "source_width": int(row.get("source_width") or 0),
                "source_height": int(row.get("source_height") or 0),
                "age_seconds": None if detected_at is None else round(now - detected_at, 2),
                "revision": int(row.get("tracked_frames") or 0),
                "last_inference_ms": row.get("last_inference_ms"),
                "last_error": row.get("last_error"),
            }
    return jsonify({"ok": True, "cameras": payload})


@app.route("/health")
def health():
    now = time.time()
    cameras = {}
    with lock:
        for key in allowed_keys():
            st = dict(camera_status.get(key) or {})
            ai = dict(ai_status.get(key) or default_ai_row())
            last = st.get("last_frame_at")
            ai_last = ai.get("last_processed_at")
            cameras[key] = {
                "connected": bool(st.get("connected")),
                "active": is_camera_focused(key),
                "monitored": is_camera_active(key),
                "standby": bool(st.get("standby")),
                "frames": int(st.get("frames") or 0),
                "last_jpeg_bytes": int(st.get("last_jpeg_bytes") or 0),
                "age_seconds": None if last is None else round(now - last, 2),
                "last_error": st.get("last_error"),
                "has_frame": key in output_frames,
                "ai": {
                    "enabled": bool(ai.get("enabled", True)),
                    "model_loaded": bool(ai.get("model_loaded")),
                    "has_frame": key in tracked_frames,
                    "tracked_frames": int(ai.get("tracked_frames") or 0),
                    "last_jpeg_bytes": int(ai.get("last_jpeg_bytes") or 0),
                    "age_seconds": None if ai_last is None else round(now - ai_last, 2),
                    "last_inference_ms": ai.get("last_inference_ms"),
                    "last_error": ai.get("last_error"),
                    "persons": int(ai.get("persons") or 0),
                    "vehicles": int(ai.get("vehicles") or 0),
                    "objects": int(ai.get("objects") or 0),
                    "class_counts": ai.get("class_counts") or {},
                    "session_vehicle_counts": ai.get("session_vehicle_counts") or {},
                    "session_vehicle_total": int(ai.get("session_vehicle_total") or 0),
                },
            }

    if Config.MONITOR_ALL_CAMERAS:
        monitored_rows = [row for row in cameras.values() if row.get("monitored")]
        connected_total = sum(1 for row in monitored_rows if row.get("connected"))
        ai_processed_total = sum(
            1
            for row in monitored_rows
            if row.get("ai", {}).get("age_seconds") is not None
            and not row.get("ai", {}).get("last_error")
        )
        live = connected_total == len(monitored_rows) if monitored_rows else False
        ai_live = ai_processed_total == len(monitored_rows) if monitored_rows else False
    else:
        connected_total = sum(1 for row in cameras.values() if row.get("connected"))
        ai_processed_total = sum(
            1 for row in cameras.values()
            if row.get("ai", {}).get("age_seconds") is not None
        )
        live = all(c["has_frame"] for c in cameras.values()) if cameras else False
        ai_live = all(c["ai"]["has_frame"] for c in cameras.values()) if cameras else False

    today_counts = None
    if traffic_store is not None:
        try:
            today_counts = traffic_store.counts()
        except Exception as exc:
            log.warning("Unable to read traffic counts: %s", exc)

    _ensure_initial_focus()
    with lock:
        active_list = list(active_camera_keys) if Config.PAGED_CAMERA_MODE else allowed_keys()

    return jsonify({
        "ok": live,
        "ai_ok": ai_live,
        "paged_mode": Config.PAGED_CAMERA_MODE,
        "monitor_all_cameras": Config.MONITOR_ALL_CAMERAS,
        "monitored_total": len(allowed_keys()) if Config.MONITOR_ALL_CAMERAS else len(active_list),
        "connected_total": connected_total,
        "ai_processed_total": ai_processed_total,
        "shared_ai_workers": Config.SHARED_AI_WORKERS if Config.MONITOR_ALL_CAMERAS else 0,
        "foreground_bytetrack": Config.FOREGROUND_BYTETRACK_ENABLED,
        "foreground_track_concurrency": Config.FOREGROUND_TRACK_CONCURRENCY,
        "active_camera_keys": active_list,
        "active_camera_limit": Config.ACTIVE_CAMERA_LIMIT,
        "configured_total": len(allowed_keys()),
        "cameras": cameras,
        "traffic": {
            "counting_enabled": Config.COUNTING_ENABLED,
            "count_line_y_ratio": Config.COUNT_LINE_Y_RATIO,
            "session_id": SERVER_SESSION_ID,
            "today": today_counts,
        },
        "advanced_models": advanced_model_readiness(),
    })


@app.route("/analytics/vehicle_counts")
def vehicle_counts():
    if traffic_store is None:
        return jsonify({"ok": False, "error": "Traffic analytics store is unavailable"}), 503

    event_date = (request.args.get("date") or "").strip() or None
    camera_ip = (request.args.get("camera_ip") or "").strip() or None
    if camera_ip and camera_ip not in allowed_keys():
        abort(404, description="Camera not configured")

    try:
        data = traffic_store.counts(event_date=event_date, camera_ip=camera_ip)
        return jsonify({"ok": True, **data})
    except Exception as exc:
        log.exception("Vehicle count query failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500



@app.route("/analytics/hourly_counts")
def hourly_counts():
    if traffic_store is None:
        return jsonify({"ok": False, "error": "Traffic analytics store is unavailable"}), 503

    event_date = (request.args.get("date") or "").strip() or None
    camera_ip = (request.args.get("camera_ip") or "").strip() or None
    if camera_ip and camera_ip not in allowed_keys():
        abort(404, description="Camera not configured")

    try:
        data = traffic_store.hourly_counts(event_date=event_date, camera_ip=camera_ip)
        return jsonify({"ok": True, **data})
    except Exception as exc:
        log.exception("Hourly count query failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/analytics/recent_vehicle_events")
def recent_vehicle_events():
    if traffic_store is None:
        return jsonify({"ok": False, "error": "Traffic analytics store is unavailable"}), 503
    try:
        limit = int(request.args.get("limit", 25))
    except ValueError:
        limit = 25
    try:
        return jsonify({"ok": True, "events": traffic_store.recent(limit)})
    except Exception as exc:
        log.exception("Recent vehicle event query failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/snapshot/<camera_key>")
def snapshot(camera_key):
    if camera_key not in allowed_keys():
        abort(404, description="Camera not configured")
    with lock:
        jpeg = output_frames.get(camera_key)
        err = (camera_status.get(camera_key) or {}).get("last_error")
    if not jpeg:
        return jsonify({
            "ok": False,
            "error": err or f"No JPEG frame received yet from {camera_key}",
        }), 503
    return Response(jpeg, mimetype="image/jpeg")


@app.route("/ai_snapshot/<camera_key>")
def ai_snapshot(camera_key):
    if camera_key not in allowed_keys():
        abort(404, description="Camera not configured")
    with lock:
        jpeg = tracked_frames.get(camera_key)
        err = (ai_status.get(camera_key) or {}).get("last_error")
    if not jpeg:
        return jsonify({
            "ok": False,
            "error": err or f"No AI frame available yet from {camera_key}",
        }), 503
    return Response(jpeg, mimetype="image/jpeg")


@app.route("/video_feed/<camera_key>")
def video_feed(camera_key):
    """Original raw MJPEG endpoint retained unchanged for fallback/testing."""
    if camera_key not in allowed_keys():
        abort(404, description="Camera not configured")
    return Response(
        generate_mjpeg(camera_key, "raw"),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"X-Accel-Buffering": "no"},
    )


@app.route("/tracked_feed/<camera_key>")
def tracked_feed(camera_key):
    """AI-annotated MJPEG generated from the existing captured frames."""
    if camera_key not in allowed_keys():
        abort(404, description="Camera not configured")
    return Response(
        generate_mjpeg(camera_key, "ai"),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    cameras = Config.CAMERAS

    for cam in cameras:
        camera_key = cam["camera_key"]
        threading.Thread(
            target=capture_stream,
            args=(cam,),
            daemon=True,
            name=f"capture-{camera_key}",
        ).start()
        if Config.CAMERA_CONNECT_STAGGER_SECONDS:
            time.sleep(Config.CAMERA_CONNECT_STAGGER_SECONDS)

    if Config.MONITOR_ALL_CAMERAS:
        worker_count = min(Config.SHARED_AI_WORKERS, max(1, len(cameras)))
        partitions = [cameras[index::worker_count] for index in range(worker_count)]
        for worker_id, partition in enumerate(partitions, start=1):
            if not partition:
                continue
            threading.Thread(
                target=shared_ai_worker,
                args=(worker_id, partition, Config.FOREGROUND_BYTETRACK_ENABLED),
                daemon=True,
                name=f"shared-ai-{worker_id}",
            ).start()

        if Config.FOREGROUND_BYTETRACK_ENABLED:
            threading.Thread(
                target=foreground_tracker_manager,
                args=(cameras,),
                daemon=True,
                name="foreground-bytetrack-manager",
            ).start()
    else:
        for cam in cameras:
            camera_key = cam["camera_key"]
            threading.Thread(
                target=ai_tracking_worker,
                args=(cam,),
                daemon=True,
                name=f"ai-{camera_key}",
            ).start()

    log.info(
        "Starting MJPEG AI server cameras=%s monitor_all=%s shared_workers=%s ai_fps=%s counting=%s line=%.2f",
        len(cameras),
        Config.MONITOR_ALL_CAMERAS,
        Config.SHARED_AI_WORKERS if Config.MONITOR_ALL_CAMERAS else 0,
        Config.AI_MAX_FPS,
        Config.COUNTING_ENABLED,
        Config.COUNT_LINE_Y_RATIO,
    )
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
