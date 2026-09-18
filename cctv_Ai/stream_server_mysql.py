import logging
import os
import threading
import time
import urllib.parse
from datetime import datetime

from flask import Response, jsonify, request

import stream_server as base
from rider_verified_detector import RiderVerifiedDetector
from road_report_routes import register_road_report_routes
from config import Config
from analytics_store import TrafficStore

log = logging.getLogger("mjpeg-mysql")
advanced = RiderVerifiedDetector(Config, base.traffic_store, base.SERVER_SESSION_ID, log)
_analytics_lock = threading.Lock()
_analytics_last_error = None
_analytics_last_attempt = 0.0


def _ensure_analytics_store(force=False):
    """Reconnect analytics automatically if MySQL was unavailable during startup."""
    global _analytics_last_error, _analytics_last_attempt
    if base.traffic_store is not None:
        if advanced.store is not base.traffic_store:
            advanced.store = base.traffic_store
        return base.traffic_store

    now = time.time()
    if not force and now - _analytics_last_attempt < 3.0:
        return None

    with _analytics_lock:
        if base.traffic_store is not None:
            advanced.store = base.traffic_store
            return base.traffic_store
        _analytics_last_attempt = time.time()
        try:
            store = TrafficStore(Config.ANALYTICS_DB)
            base.traffic_store = store
            advanced.store = store
            _analytics_last_error = None
            log.info("MySQL analytics store connected/recovered")
            return store
        except Exception as exc:
            _analytics_last_error = str(exc)
            log.error("MySQL analytics store unavailable: %s", exc)
            return None


def _json_safe(value):
    try:
        if hasattr(value, "item"):
            return _json_safe(value.item())
        if isinstance(value, dict):
            return {str(key): _json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)
    except Exception:
        return str(value)


def _safe_runtime_readiness():
    try:
        return _json_safe(advanced.runtime_readiness())
    except Exception as exc:
        log.exception("Unable to build advanced runtime status: %s", exc)
        return {"runtime_error": str(exc)}


# ---------------------------------------------------------------------------
# Multi-recorder compatibility patches
# ---------------------------------------------------------------------------
# Recorder credentials stay in local .env. Never hard-code camera passwords in
# source control. IPs listed in RECORDER_CAMERA_IPS use the recorder credential
# pair; all other cameras use CAMERA_USERNAME/CAMERA_PASSWORD.
def _patched_rtsp_url(camera):
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


def _patched_maybe_count_object(camera, state, *, track_id, cls_id, vehicle_type,
                                confidence, center, line_y):
    if (
        not Config.COUNTING_ENABLED
        or track_id is None
        or cls_id not in Config.COUNTED_CLASSES
    ):
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
    store = _ensure_analytics_store()
    if store is not None:
        try:
            stored = store.record_vehicle(
                session_id=base.SERVER_SESSION_ID,
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
        camera["camera_key"], vehicle_type, track_id, direction, confidence, stored,
    )


base.rtsp_url = _patched_rtsp_url
base._maybe_count_object = _patched_maybe_count_object
base.advanced_model_readiness = _safe_runtime_readiness


_original_annotate = base._annotate_tracking


def _annotate_with_advanced(camera, frame, result, model, history, last_seen,
                            processed_index, inference_ms, count_state):
    clean_frame = frame.copy()
    persons, vehicles, class_counts = _original_annotate(
        camera,
        frame,
        result,
        model,
        history,
        last_seen,
        processed_index,
        inference_ms,
        count_state,
    )
    try:
        store = _ensure_analytics_store()
        if store is not None:
            advanced.store = store
        summary = advanced.process(
            camera,
            clean_frame,
            result,
            model,
            processed_index,
            draw_frame=frame,
        )
        base.set_ai_status(camera["camera_key"], advanced=summary)
    except Exception as exc:
        log.exception("Advanced detection failed camera=%s: %s", camera["camera_key"], exc)
        base.set_ai_status(camera["camera_key"], advanced={"error": str(exc)})
    return persons, vehicles, class_counts


base._annotate_tracking = _annotate_with_advanced


@base.app.route("/analytics/report")
def analytics_report():
    store = _ensure_analytics_store()
    if store is None:
        return jsonify({"ok": False, "error": "MySQL analytics store is unavailable", "detail": _analytics_last_error}), 503

    today = datetime.now().date().isoformat()
    from_date = (request.args.get("from_date") or today).strip()
    to_date = (request.args.get("to_date") or from_date).strip()
    from_time = (request.args.get("from_time") or "00:00").strip()
    to_time = (request.args.get("to_time") or "23:59").strip()
    camera_key = (request.args.get("camera_key") or request.args.get("camera_ip") or "").strip() or None

    if camera_key and camera_key not in base.allowed_keys():
        return jsonify({"ok": False, "error": "Camera not configured"}), 404

    try:
        data = store.report(
            from_date=from_date,
            to_date=to_date,
            from_time=from_time,
            to_time=to_time,
            camera_key=camera_key,
        )
        return jsonify({"ok": True, **data})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        log.exception("Report query failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@base.app.route("/analytics/today_summary")
def today_summary():
    store = _ensure_analytics_store()
    if store is None:
        return jsonify({"ok": False, "error": "MySQL analytics store is unavailable", "detail": _analytics_last_error}), 503
    today = datetime.now().date().isoformat()
    try:
        report = store.report(
            from_date=today,
            to_date=today,
            from_time="00:00",
            to_time="23:59",
        )
        return jsonify({
            "ok": True,
            "date": today,
            "vehicles": int((report.get("summary") or {}).get("total") or 0),
            "no_helmet": store.no_helmet_count(event_date=today),
            "by_camera": report.get("by_camera") or [],
        })
    except Exception as exc:
        log.exception("Today summary query failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@base.app.route("/analytics/recent_violations")
def recent_violations():
    store = _ensure_analytics_store()
    if store is None:
        return jsonify({"ok": False, "error": "MySQL analytics store is unavailable", "detail": _analytics_last_error}), 503
    try:
        limit = int(request.args.get("limit", 25))
    except ValueError:
        limit = 25
    try:
        return jsonify({"ok": True, "violations": store.recent_violations(limit)})
    except Exception as exc:
        log.exception("Violation query failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@base.app.route("/analytics/violation_image/<int:violation_id>/<image_type>")
def violation_image(violation_id, image_type):
    if image_type not in {"evidence", "plate"}:
        return jsonify({"ok": False, "error": "Invalid image type"}), 400
    store = _ensure_analytics_store()
    if store is None:
        return jsonify({"ok": False, "error": "MySQL analytics store is unavailable", "detail": _analytics_last_error}), 503
    image = store.violation_image(violation_id, image_type)
    if not image:
        return jsonify({"ok": False, "error": "Image not found"}), 404
    return Response(image, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


register_road_report_routes(base.app, _ensure_analytics_store, base.allowed_keys, log)


@base.app.route("/analytics/status")
def analytics_status():
    store = _ensure_analytics_store()
    return jsonify({
        "ok": store is not None,
        "connected": store is not None,
        "database": Config.DB_NAME,
        "host": Config.DB_HOST,
        "error": _analytics_last_error,
    }), (200 if store is not None else 503)


@base.app.route("/advanced/status")
def advanced_status():
    status = _safe_runtime_readiness()
    status["accuracy_mode"] = {
        "multi_camera_keys": True,
        "helmet_observation_confidence": getattr(advanced, "helmet_observation_confidence", None),
        "road_tiled_inference": hasattr(advanced, "road_tile_columns"),
        "road_tile_columns": getattr(advanced, "road_tile_columns", None),
    }
    status = _json_safe(status)
    return jsonify({"ok": "runtime_error" not in status, "models": status})


if __name__ == "__main__":
    cameras = Config.CAMERAS

    for cam in cameras:
        threading.Thread(
            target=base.capture_stream,
            args=(cam,),
            daemon=True,
            name=f"capture-{cam['camera_key']}",
        ).start()

    for cam in cameras:
        threading.Thread(
            target=base.ai_tracking_worker,
            args=(cam,),
            daemon=True,
            name=f"ai-{cam['camera_key']}",
        ).start()

    log.info(
        "Starting CCTV backend with MySQL analytics cameras=%s db=%s@%s:%s/%s",
        [cam["camera_key"] for cam in cameras],
        Config.DB_USER,
        Config.DB_HOST,
        Config.DB_PORT,
        Config.DB_NAME,
    )
    log.info("Advanced AI status: %s", _safe_runtime_readiness())
    base.app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
