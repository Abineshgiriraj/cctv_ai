import logging
import threading

from flask import Response, jsonify, request

import stream_server as base
from advanced_detection import AdvancedDetector
from config import Config

log = logging.getLogger("mjpeg-mysql")
advanced = AdvancedDetector(Config, base.traffic_store, base.SERVER_SESSION_ID, log)

# Make /health report helmet, plate, road-damage and road-obstruction readiness.
base.advanced_model_readiness = lambda: AdvancedDetector.readiness(Config)

_original_annotate = base._annotate_tracking


def _annotate_with_advanced(camera_ip, frame, result, model, history, last_seen,
                            processed_index, inference_ms, count_state):
    # Advanced models must run on the original frame. The normal tracker draws
    # boxes, trails, labels and the count line onto `frame`, which can obscure a
    # tiny helmet or number plate and reduce detector/OCR accuracy.
    clean_frame = frame.copy()

    persons, vehicles, class_counts = _original_annotate(
        camera_ip,
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
            camera_ip,
            clean_frame,
            result,
            model,
            processed_index,
            draw_frame=frame,
        )
        base.set_ai_status(camera_ip, advanced=summary)
    except Exception as exc:
        log.exception("Advanced detection failed camera=%s: %s", camera_ip, exc)
        base.set_ai_status(camera_ip, advanced={"error": str(exc)})
    return persons, vehicles, class_counts


base._annotate_tracking = _annotate_with_advanced


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


if __name__ == "__main__":
    cameras = base.allowed_ips()

    for ip in cameras:
        threading.Thread(
            target=base.capture_stream,
            args=(ip,),
            daemon=True,
            name=f"capture-{ip}",
        ).start()

    for ip in cameras:
        threading.Thread(
            target=base.ai_tracking_worker,
            args=(ip,),
            daemon=True,
            name=f"ai-{ip}",
        ).start()

    log.info(
        "Starting CCTV backend with MySQL analytics cameras=%s db=%s@%s:%s/%s",
        cameras,
        Config.DB_USER,
        Config.DB_HOST,
        Config.DB_PORT,
        Config.DB_NAME,
    )
    base.app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
