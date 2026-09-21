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
from incident_detector import IncidentDetector

log = logging.getLogger("mjpeg-mysql")
advanced = RiderVerifiedDetector(Config, base.traffic_store, base.SERVER_SESSION_ID, log)
incidents = IncidentDetector(Config, base.traffic_store, base.SERVER_SESSION_ID, log)
_advanced_lock = threading.Lock()
_incident_lock = threading.Lock()

# Heavy helmet/plate/road/incident analysis must never block the primary
# vehicle-tracking loop. Keep only the newest pending frame per camera.
_analysis_task_lock = threading.Lock()
_analysis_task_condition = threading.Condition(_analysis_task_lock)
_advanced_latest_tasks = {}
_incident_latest_tasks = {}
_analysis_workers_started = False

_analytics_lock = threading.Lock()
_analytics_last_error = None
_analytics_last_attempt = 0.0


def _ensure_analytics_store(force=False):
    """Reconnect analytics automatically if MySQL was unavailable during startup."""
    global _analytics_last_error, _analytics_last_attempt
    if base.traffic_store is not None:
        if advanced.store is not base.traffic_store:
            advanced.store = base.traffic_store
        if incidents.store is not base.traffic_store:
            incidents.store = base.traffic_store
        return base.traffic_store

    now = time.time()
    if not force and now - _analytics_last_attempt < 3.0:
        return None

    with _analytics_lock:
        if base.traffic_store is not None:
            advanced.store = base.traffic_store
            incidents.store = base.traffic_store
            return base.traffic_store
        _analytics_last_attempt = time.time()
        try:
            store = TrafficStore(Config.ANALYTICS_DB)
            base.traffic_store = store
            advanced.store = store
            incidents.store = store
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


def _put_latest_task(target, task):
    camera_key = task["camera"]["camera_key"]
    with _analysis_task_condition:
        target[camera_key] = task
        _analysis_task_condition.notify_all()


def _pop_latest_task(target):
    with _analysis_task_condition:
        while not target:
            _analysis_task_condition.wait(timeout=0.5)
        # Focused cameras get first priority so helmet/no-helmet and incident
        # checks remain responsive on the feeds the operator is viewing. Within
        # each priority group, process the oldest waiting camera.
        camera_key, task = min(
            target.items(),
            key=lambda item: (
                0 if base.is_camera_focused(item[0]) else 1,
                item[1]["queued_at"],
            ),
        )
        target.pop(camera_key, None)
        return task


def _advanced_analysis_loop():
    while True:
        task = _pop_latest_task(_advanced_latest_tasks)
        camera = task["camera"]
        try:
            store = _ensure_analytics_store()
            if store is not None:
                advanced.store = store
            with _advanced_lock:
                summary = advanced.process(
                    camera,
                    task["frame"],
                    task["result"],
                    task["model"],
                    task["processed_index"],
                    draw_frame=None,
                )
            helmet_live = summary.get("helmet_live") or []
            base.set_ai_status(
                camera["camera_key"],
                advanced=summary,
                helmet_detections=helmet_live,
                helmet_detections_at=time.time(),
            )
        except Exception as exc:
            log.exception(
                "Async advanced detection failed camera=%s: %s",
                camera["camera_key"],
                exc,
            )
            base.set_ai_status(
                camera["camera_key"],
                advanced={"error": str(exc)},
            )


def _incident_analysis_loop():
    while True:
        task = _pop_latest_task(_incident_latest_tasks)
        camera = task["camera"]
        try:
            store = _ensure_analytics_store()
            incidents.store = store
            with _incident_lock:
                summary = incidents.process(
                    camera,
                    task["frame"],
                    task["result"],
                    task["processed_index"],
                    draw_frame=None,
                )
            base.set_ai_status(camera["camera_key"], incidents=summary)
        except Exception as exc:
            log.exception(
                "Async incident detection failed camera=%s: %s",
                camera["camera_key"],
                exc,
            )
            base.set_ai_status(
                camera["camera_key"],
                incidents={"error": str(exc)},
            )


def _start_analysis_workers():
    global _analysis_workers_started
    if _analysis_workers_started:
        return
    _analysis_workers_started = True
    threading.Thread(
        target=_advanced_analysis_loop,
        daemon=True,
        name="advanced-analysis",
    ).start()
    threading.Thread(
        target=_incident_analysis_loop,
        daemon=True,
        name="incident-analysis",
    ).start()
    log.info("Non-blocking advanced/incident analysis workers started")


def _annotate_with_advanced(camera, frame, result, model, history, last_seen,
                            processed_index, inference_ms, count_state):
    # Primary person/vehicle tracking and counting completes immediately.
    # Expensive helmet/plate/road/incident models run asynchronously on the
    # newest available frame and therefore cannot freeze live vehicle boxes.
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

    now = time.time()
    base_task = {
        "camera": camera,
        "result": result,
        "model": model,
        "processed_index": processed_index,
        "queued_at": now,
    }

    run_advanced = (
        Config.ADVANCED_DETECTION_ENABLED
        and (
            processed_index % Config.ADVANCED_EVERY_N_FRAMES == 0
            or processed_index % Config.ROAD_EVERY_N_FRAMES == 0
        )
    )
    if run_advanced:
        advanced_task = dict(base_task)
        advanced_task["frame"] = frame.copy()
        _put_latest_task(_advanced_latest_tasks, advanced_task)

    run_incident = (
        Config.INCIDENT_DETECTION_ENABLED
        and processed_index % Config.INCIDENT_EVERY_N_FRAMES == 0
    )
    if run_incident:
        incident_task = dict(base_task)
        incident_task["frame"] = frame.copy()
        _put_latest_task(_incident_latest_tasks, incident_task)

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


@base.app.route("/analytics/incidents")
def incident_report():
    store = _ensure_analytics_store()
    if store is None:
        return jsonify({
            "ok": False,
            "error": "MySQL analytics store is unavailable",
            "detail": _analytics_last_error,
        }), 503

    today = datetime.now().date().isoformat()
    from_date = (request.args.get("from_date") or today).strip()
    to_date = (request.args.get("to_date") or from_date).strip()
    camera_key = (request.args.get("camera_key") or "").strip() or None
    incident_type = (request.args.get("incident_type") or "").strip() or None
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        limit = 100

    if camera_key and camera_key not in base.allowed_keys():
        return jsonify({"ok": False, "error": "Camera not configured"}), 404

    try:
        rows = store.recent_incidents(
            limit=limit,
            from_date=from_date,
            to_date=to_date,
            camera_key=camera_key,
            incident_type=incident_type,
        )
        summary = {
            "total": len(rows),
            "accident": sum(1 for row in rows if row.get("incident_type") == "accident"),
            "road_obstruction": sum(
                1 for row in rows if row.get("incident_type") == "road_obstruction"
            ),
            "cameras": len({row.get("camera_key") for row in rows if row.get("camera_key")}),
        }
        return jsonify({"ok": True, "summary": summary, "events": rows})
    except Exception as exc:
        log.exception("Incident report failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@base.app.route("/analytics/incident_image/<int:incident_id>")
def incident_image(incident_id):
    store = _ensure_analytics_store()
    if store is None:
        return jsonify({
            "ok": False,
            "error": "MySQL analytics store is unavailable",
            "detail": _analytics_last_error,
        }), 503
    image = store.incident_image(incident_id)
    if not image:
        return jsonify({"ok": False, "error": "Image not found"}), 404
    return Response(image, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


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
    status["incident_detection"] = {
        "enabled": Config.INCIDENT_DETECTION_ENABLED,
        "accident_enabled": Config.ACCIDENT_DETECTION_ENABLED,
        "accident_method": "YOLO/ByteTrack trajectory + proximity + sudden-stop",
        "road_obstruction_fallback": Config.ROAD_OBSTRUCTION_FALLBACK_ENABLED,
        "road_obstruction_method": "persistent fixed-camera foreground",
        "requires_accident_pt": False,
        "requires_obstruction_pt": False,
    }
    status = _json_safe(status)
    return jsonify({"ok": "runtime_error" not in status, "models": status})


if __name__ == "__main__":
    cameras = Config.CAMERAS
    _start_analysis_workers()

    # RTSP capture remains active for every configured camera when
    # MONITOR_ALL_CAMERAS=1. UI pagination no longer stops monitoring.
    for cam in cameras:
        threading.Thread(
            target=base.capture_stream,
            args=(cam,),
            daemon=True,
            name=f"capture-{cam['camera_key']}",
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
                target=base.shared_ai_worker,
                args=(worker_id, partition, Config.FOREGROUND_BYTETRACK_ENABLED),
                daemon=True,
                name=f"shared-ai-{worker_id}",
            ).start()

        if Config.FOREGROUND_BYTETRACK_ENABLED:
            threading.Thread(
                target=base.foreground_tracker_manager,
                args=(cameras,),
                daemon=True,
                name="foreground-bytetrack-manager",
            ).start()
    else:
        for cam in cameras:
            threading.Thread(
                target=base.ai_tracking_worker,
                args=(cam,),
                daemon=True,
                name=f"ai-{cam['camera_key']}",
            ).start()

    log.info(
        "Starting CCTV backend cameras=%s monitor_all=%s shared_workers=%s db=%s@%s:%s/%s",
        len(cameras),
        Config.MONITOR_ALL_CAMERAS,
        Config.SHARED_AI_WORKERS if Config.MONITOR_ALL_CAMERAS else 0,
        Config.DB_USER,
        Config.DB_HOST,
        Config.DB_PORT,
        Config.DB_NAME,
    )
    log.info("Advanced AI status: %s", _safe_runtime_readiness())
    base.app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
