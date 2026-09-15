import cv2
import urllib.parse
import os
import time
import logging
import threading
import uuid
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
# /video_feed/<camera_ip> path remains available as a fallback.
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

lock = threading.Lock()
# Serialize heavy YOLO inference across cameras so CPU/GPU work cannot starve
# the RTSP capture threads. Each camera still has its own model/tracker state.
inference_lock = threading.Lock()

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

try:
    traffic_store = TrafficStore(Config.ANALYTICS_DB)
    log.info("Traffic analytics DB ready path=%s session=%s", Config.ANALYTICS_DB, SERVER_SESSION_ID)
except Exception as exc:
    traffic_store = None
    log.exception("Traffic analytics persistence disabled: %s", exc)


def allowed_ips():
    return [ip.strip() for ip in Config.CAMERA_IPS if ip.strip()]


def rtsp_url(camera_ip: str) -> str:
    user = urllib.parse.quote(Config.CAMERA_USERNAME)
    password = urllib.parse.quote(Config.CAMERA_PASSWORD)
    return (
        f"rtsp://{user}:{password}@{camera_ip}:554/cam/realmonitor"
        f"?channel={Config.CAMERA_CHANNEL}&subtype={Config.CAMERA_SUBTYPE}"
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


def set_status(camera_ip: str, **fields):
    with lock:
        row = camera_status.setdefault(
            camera_ip,
            {
                "connected": False,
                "frames": 0,
                "last_jpeg_bytes": 0,
                "last_frame_at": None,
                "last_error": None,
                "last_error_at": None,
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


def set_ai_status(camera_ip: str, **fields):
    with lock:
        row = ai_status.setdefault(camera_ip, default_ai_row())
        row.update(fields)


def capture_stream(camera_ip: str):
    """Open the camera exactly once and publish both JPEG and raw OpenCV frames."""
    url = rtsp_url(camera_ip)
    reconnect_delay = 3

    while True:
        log.info(
            "Connecting RTSP camera=%s channel=%s subtype=%s transport=tcp",
            camera_ip,
            Config.CAMERA_CHANNEL,
            Config.CAMERA_SUBTYPE,
        )
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        if not cap.isOpened():
            msg = (
                f"OpenCV could not open RTSP for {camera_ip}:554 "
                f"(channel={Config.CAMERA_CHANNEL}, subtype={Config.CAMERA_SUBTYPE}). "
                "Check network, credentials, and that the camera RTSP service is up."
            )
            log.error(msg)
            set_status(camera_ip, connected=False, last_error=msg, last_error_at=time.time())
            time.sleep(reconnect_delay)
            continue

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        log.info("RTSP open camera=%s size=%sx%s fps=%s", camera_ip, width, height, fps)
        set_status(camera_ip, connected=True, last_error=None)

        fail_reads = 0
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                fail_reads += 1
                msg = f"RTSP read failed for {camera_ip} (consecutive={fail_reads})"
                log.warning(msg)
                set_status(camera_ip, connected=False, last_error=msg, last_error_at=time.time())
                if fail_reads >= 3:
                    break
                time.sleep(0.2)
                continue

            fail_reads = 0
            ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), Config.JPEG_QUALITY],
            )
            if not ok:
                msg = f"JPEG encode failed for {camera_ip}"
                log.error(msg)
                set_status(camera_ip, last_error=msg, last_error_at=time.time())
                continue

            jpeg = encoded.tobytes()
            if len(jpeg) < 100 or jpeg[:2] != b"\xff\xd8":
                msg = f"Invalid JPEG from {camera_ip} bytes={len(jpeg)}"
                log.error(msg)
                set_status(camera_ip, last_error=msg, last_error_at=time.time())
                continue

            with lock:
                # Preserve the original raw stream output.
                output_frames[camera_ip] = jpeg

                # Share the same captured frame with the AI worker. No second RTSP
                # connection is opened for detection/tracking.
                latest_cv_frames[camera_ip] = frame
                frame_sequence[camera_ip] = int(frame_sequence.get(camera_ip) or 0) + 1

                row = camera_status.setdefault(camera_ip, {})
                row["connected"] = True
                row["frames"] = int(row.get("frames") or 0) + 1
                row["last_jpeg_bytes"] = len(jpeg)
                row["last_frame_at"] = time.time()
                row["last_error"] = None

        cap.release()
        log.error("Reconnecting camera=%s in %ss", camera_ip, reconnect_delay)
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


def _maybe_count_object(camera_ip, state, *, track_id, cls_id, vehicle_type,
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

    previous_y = previous[1]
    current_y = center[1]
    direction = None
    if previous_y < line_y <= current_y:
        direction = "down"
    elif previous_y > line_y >= current_y:
        direction = "up"

    if direction is None:
        return

    # Mark immediately so a transient DB error cannot produce repeated counts.
    state["counted_ids"].add(track_id)
    state["session_counts"][vehicle_type] += 1
    state["session_total"] += 1

    stored = False
    if traffic_store is not None:
        stored = traffic_store.record_vehicle(
            session_id=SERVER_SESSION_ID,
            camera_ip=camera_ip,
            track_id=track_id,
            vehicle_type=vehicle_type,
            direction=direction,
            confidence=confidence,
        )

    log.info(
        "Vehicle counted camera=%s type=%s track=%s direction=%s confidence=%.2f stored=%s",
        camera_ip,
        vehicle_type,
        track_id,
        direction,
        confidence,
        stored,
    )


def _annotate_tracking(camera_ip, frame, result, model, history, last_seen,
                       processed_index, inference_ms, count_state):
    persons = 0
    vehicles = 0
    class_counts = defaultdict(int)

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
                    camera_ip,
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
    return persons, vehicles, dict(class_counts)


def ai_tracking_worker(camera_ip: str):
    """Process the latest captured frame with YOLO + ByteTrack.

    This worker consumes latest_cv_frames populated by capture_stream(); it never
    opens the camera or constructs an RTSP URL.
    """
    set_ai_status(camera_ip, enabled=True, model_loaded=False, last_error=None)

    try:
        from ultralytics import YOLO
    except Exception as exc:
        msg = f"Ultralytics import failed: {exc}"
        log.exception("AI disabled camera=%s: %s", camera_ip, msg)
        set_ai_status(camera_ip, model_loaded=False, last_error=msg, last_error_at=time.time())
        return

    try:
        log.info("Loading YOLO model camera=%s model=%s", camera_ip, Config.YOLO_MODEL)
        model = YOLO(Config.YOLO_MODEL)
        set_ai_status(camera_ip, model_loaded=True, last_error=None)
        log.info("AI tracker ready camera=%s classes=%s", camera_ip, Config.TARGET_CLASSES)
    except Exception as exc:
        msg = f"YOLO model load failed: {exc}"
        log.exception("AI disabled camera=%s: %s", camera_ip, msg)
        set_ai_status(camera_ip, model_loaded=False, last_error=msg, last_error_at=time.time())
        return

    last_seq = -1
    last_started = 0.0
    min_interval = 1.0 / max(Config.AI_MAX_FPS, 0.25)
    history = defaultdict(lambda: deque(maxlen=Config.TRACK_TRAIL_LENGTH))
    last_seen = {}
    processed_index = 0
    count_state = _new_traffic_state()
    with lock:
        traffic_state[camera_ip] = count_state

    while True:
        with lock:
            frame = latest_cv_frames.get(camera_ip)
            seq = int(frame_sequence.get(camera_ip) or 0)

        if frame is None or seq == last_seq:
            time.sleep(0.02)
            continue

        remaining = min_interval - (time.time() - last_started)
        if remaining > 0:
            time.sleep(min(remaining, 0.05))
            continue

        # cap.read() supplies a new ndarray on subsequent reads. We still copy the
        # selected frame so inference/annotation can never affect raw MJPEG output.
        work = frame.copy()
        last_seq = seq
        last_started = time.time()
        started = time.perf_counter()

        try:
            with inference_lock:
                results = model.track(
                    source=work,
                    persist=True,
                    tracker="bytetrack.yaml",
                    classes=Config.TARGET_CLASSES,
                    conf=Config.CONFIDENCE_THRESHOLD,
                    iou=Config.IOU_THRESHOLD,
                    imgsz=Config.YOLO_IMGSZ,
                    device=Config.YOLO_DEVICE or None,
                    verbose=False,
                )
            inference_ms = (time.perf_counter() - started) * 1000.0
            processed_index += 1

            result = results[0] if results else None
            if result is not None:
                persons, vehicles, class_counts = _annotate_tracking(
                    camera_ip,
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
                tracked_frames[camera_ip] = jpeg
                row = ai_status.setdefault(camera_ip, default_ai_row())
                row["model_loaded"] = True
                row["tracked_frames"] = int(row.get("tracked_frames") or 0) + 1
                row["last_jpeg_bytes"] = len(jpeg)
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
            set_ai_status(camera_ip, last_error=msg, last_error_at=time.time())
            time.sleep(0.5)


def generate_mjpeg(camera_ip: str, source: str = "raw"):
    last_log = 0.0
    while True:
        with lock:
            if source == "ai":
                jpeg = tracked_frames.get(camera_ip)
                err = (ai_status.get(camera_ip) or {}).get("last_error")
            else:
                jpeg = output_frames.get(camera_ip)
                err = (camera_status.get(camera_ip) or {}).get("last_error")

        if jpeg is None:
            now = time.time()
            if now - last_log > 5:
                log.warning(
                    "MJPEG client waiting for first %s frame camera=%s error=%s",
                    source,
                    camera_ip,
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
    return resp


@app.route("/health")
def health():
    now = time.time()
    cameras = {}
    with lock:
        for ip in allowed_ips():
            st = dict(camera_status.get(ip) or {})
            ai = dict(ai_status.get(ip) or default_ai_row())
            last = st.get("last_frame_at")
            ai_last = ai.get("last_processed_at")
            cameras[ip] = {
                "connected": bool(st.get("connected")),
                "frames": int(st.get("frames") or 0),
                "last_jpeg_bytes": int(st.get("last_jpeg_bytes") or 0),
                "age_seconds": None if last is None else round(now - last, 2),
                "last_error": st.get("last_error"),
                "has_frame": ip in output_frames,
                "ai": {
                    "enabled": bool(ai.get("enabled", True)),
                    "model_loaded": bool(ai.get("model_loaded")),
                    "has_frame": ip in tracked_frames,
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

    live = all(c["has_frame"] for c in cameras.values()) if cameras else False
    ai_live = all(c["ai"]["has_frame"] for c in cameras.values()) if cameras else False

    today_counts = None
    if traffic_store is not None:
        try:
            today_counts = traffic_store.counts()
        except Exception as exc:
            log.warning("Unable to read traffic counts: %s", exc)

    return jsonify({
        "ok": live,
        "ai_ok": ai_live,
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
    if camera_ip and camera_ip not in allowed_ips():
        abort(404, description="Camera not configured")

    try:
        data = traffic_store.counts(event_date=event_date, camera_ip=camera_ip)
        return jsonify({"ok": True, **data})
    except Exception as exc:
        log.exception("Vehicle count query failed: %s", exc)
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


@app.route("/snapshot/<camera_ip>")
def snapshot(camera_ip):
    if camera_ip not in allowed_ips():
        abort(404, description="Camera not configured")
    with lock:
        jpeg = output_frames.get(camera_ip)
        err = (camera_status.get(camera_ip) or {}).get("last_error")
    if not jpeg:
        return jsonify({
            "ok": False,
            "error": err or f"No JPEG frame received yet from {camera_ip}",
        }), 503
    return Response(jpeg, mimetype="image/jpeg")


@app.route("/ai_snapshot/<camera_ip>")
def ai_snapshot(camera_ip):
    if camera_ip not in allowed_ips():
        abort(404, description="Camera not configured")
    with lock:
        jpeg = tracked_frames.get(camera_ip)
        err = (ai_status.get(camera_ip) or {}).get("last_error")
    if not jpeg:
        return jsonify({
            "ok": False,
            "error": err or f"No AI frame available yet from {camera_ip}",
        }), 503
    return Response(jpeg, mimetype="image/jpeg")


@app.route("/video_feed/<camera_ip>")
def video_feed(camera_ip):
    """Original raw MJPEG endpoint retained unchanged for fallback/testing."""
    if camera_ip not in allowed_ips():
        abort(404, description="Camera not configured")
    return Response(
        generate_mjpeg(camera_ip, "raw"),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"X-Accel-Buffering": "no"},
    )


@app.route("/tracked_feed/<camera_ip>")
def tracked_feed(camera_ip):
    """AI-annotated MJPEG generated from the existing captured frames."""
    if camera_ip not in allowed_ips():
        abort(404, description="Camera not configured")
    return Response(
        generate_mjpeg(camera_ip, "ai"),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    cameras = allowed_ips()

    # Start the original capture threads first.
    for ip in cameras:
        t = threading.Thread(target=capture_stream, args=(ip,), daemon=True, name=f"capture-{ip}")
        t.start()

    # AI workers consume latest_cv_frames from those capture threads. There is one
    # tracker/model instance per camera so ByteTrack IDs cannot leak across feeds.
    for ip in cameras:
        t = threading.Thread(target=ai_tracking_worker, args=(ip,), daemon=True, name=f"ai-{ip}")
        t.start()

    log.info(
        "Starting MJPEG + YOLO/ByteTrack server on 0.0.0.0:5000 cameras=%s ai_fps=%s counting=%s line=%.2f",
        cameras,
        Config.AI_MAX_FPS,
        Config.COUNTING_ENABLED,
        Config.COUNT_LINE_Y_RATIO,
    )
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
