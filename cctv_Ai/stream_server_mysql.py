import logging
import threading
from datetime import datetime

from flask import Response, jsonify, request

import stream_server as base
from rider_verified_detector import RiderVerifiedDetector
from road_report_routes import register_road_report_routes
from config import Config

log = logging.getLogger("mjpeg-mysql")
advanced = RiderVerifiedDetector(Config, base.traffic_store, base.SERVER_SESSION_ID, log)


def _json_safe(value):
    """Convert detector/model metadata into values Flask can always jsonify."""
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
    if base.traffic_store is None:
        return jsonify({"ok": False, "error": "MySQL analytics store is unavailable"}), 503

    today = datetime.now().date().isoformat()
    from_date = (request.args.get("from_date") or today).strip()
    to_date = (request.args.get("to_date") or from_date).strip()
    from_time = (request.args.get("from_time") or "00:00").strip()
    to_time = (request.args.get("to_time") or "23:59").strip()
    camera_key = (request.args.get("camera_key") or request.args.get("camera_ip") or "").strip() or None

    if camera_key and camera_key not in base.allowed_keys():
        return jsonify({"ok": False, "error": "Camera not configured"}), 404

    try:
        data = base.traffic_store.report(
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
    if base.traffic_store is None:
        return jsonify({"ok": False, "error": "MySQL analytics store is unavailable"}), 503
    today = datetime.now().date().isoformat()
    try:
        report = base.traffic_store.report(
            from_date=today,
            to_date=today,
            from_time="00:00",
            to_time="23:59",
        )
        return jsonify({
            "ok": True,
            "date": today,
            "vehicles": int((report.get("summary") or {}).get("total") or 0),
            "no_helmet": base.traffic_store.no_helmet_count(event_date=today),
            "by_camera": report.get("by_camera") or [],
        })
    except Exception as exc:
        log.exception("Today summary query failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@base.app.route("/analytics/recent_violations")
def recent_violations():
    if base.traffic_store is None:
        return jsonify({"ok": False, "error": "MySQL analytics store is unavailable"}), 503
    try:
        limit = int(request.args.get("limit", 25))
    except ValueError:
        limit = 25
    try:
        return jsonify({"ok": True, "violations": base.traffic_store.recent_violations(limit)})
    except Exception as exc:
        log.exception("Violation query failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@base.app.route("/analytics/violation_image/<int:violation_id>/<image_type>")
def violation_image(violation_id, image_type):
    if image_type not in {"evidence", "plate"}:
        return jsonify({"ok": False, "error": "Invalid image type"}), 400
    if base.traffic_store is None:
        return jsonify({"ok": False, "error": "MySQL analytics store is unavailable"}), 503
    image = base.traffic_store.violation_image(violation_id, image_type)
    if not image:
        return jsonify({"ok": False, "error": "Image not found"}), 404
    return Response(image, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


register_road_report_routes(base.app, base.traffic_store, base.allowed_keys, log)


@base.app.route("/advanced/status")
def advanced_status():
    status = _safe_runtime_readiness()
    status["accuracy_mode"] = {
        "head_only_helmet": True,
        "require_real_rider": True,
        "require_moving_motorcycle": True,
        "bike_only_no_helmet_storage": False,
        "tiled_helmet_detection": bool(getattr(advanced, "helmet_tiled_detection", False)),
        "helmet_tile_columns": getattr(advanced, "helmet_tile_columns", None),
        "helmet_tile_rows": getattr(advanced, "helmet_tile_rows", None),
        "helmet_tile_imgsz": getattr(advanced, "helmet_tile_imgsz", None),
        "helmet_tile_roi_top_ratio": getattr(advanced, "helmet_tile_roi_top_ratio", None),
        "helmet_tile_roi_bottom_ratio": getattr(advanced, "helmet_tile_roi_bottom_ratio", None),
        "helmet_observation_confidence": getattr(advanced, "helmet_observation_confidence", None),
        "strict_no_helmet_min_confidence": getattr(advanced, "strict_no_helmet_confidence", None),
        "strict_no_helmet_confirm_frames": getattr(advanced, "strict_no_helmet_confirm_frames", None),
        "strict_no_helmet_vote_ratio": getattr(advanced, "helmet_vote_ratio", None),
        "road_tiled_inference": hasattr(advanced, "road_tile_columns"),
        "road_tile_columns": getattr(advanced, "road_tile_columns", None),
    }
    status = _json_safe(status)
    return jsonify({
        "ok": "runtime_error" not in status,
        "models": status,
    })


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
        cameras,
        Config.DB_USER,
        Config.DB_HOST,
        Config.DB_PORT,
        Config.DB_NAME,
    )
    log.info("Advanced AI status: %s", _safe_runtime_readiness())
    base.app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
