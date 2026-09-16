import math
import os
import time
from collections import defaultdict, deque

import cv2
import numpy as np


class IncidentDetector:
    """Model-free temporal incident detector using existing YOLO/ByteTrack output.

    Accident detection combines vehicle proximity, recent motion and sudden slowing,
    with an optional fallen-person cue. Road obstruction detection uses a very slow
    background model inside the roadway ROI and ignores regions that overlap current
    YOLO persons/vehicles. It is intentionally generic: a fallen tree, large branch,
    debris or another newly persistent object can be reported as road obstruction.
    """

    VEHICLE_CLASS_IDS = {1, 2, 3, 5, 7}

    def __init__(self, config, store, session_id, log):
        self.cfg = config
        self.store = store
        self.session_id = session_id
        self.log = log

        self.enabled = self._bool("INCIDENT_DETECTION_ENABLED", True)
        self.every_n_frames = max(1, int(os.getenv("INCIDENT_EVERY_N_FRAMES", "3")))

        self.accident_enabled = self._bool("ACCIDENT_DETECTION_ENABLED", True)
        self.accident_confirm_frames = max(1, int(os.getenv("ACCIDENT_CONFIRM_FRAMES", "2")))
        self.accident_window = max(self.accident_confirm_frames, int(os.getenv("ACCIDENT_CONFIRM_WINDOW", "5")))
        self.accident_min_motion_ratio = float(os.getenv("ACCIDENT_MIN_MOTION_RATIO", "0.055"))
        self.accident_stop_ratio = float(os.getenv("ACCIDENT_STOP_RATIO", "0.45"))
        self.accident_proximity_ratio = float(os.getenv("ACCIDENT_PROXIMITY_RATIO", "0.70"))
        self.accident_iou_threshold = float(os.getenv("ACCIDENT_IOU_THRESHOLD", "0.03"))
        self.accident_cooldown = max(10, int(os.getenv("ACCIDENT_COOLDOWN_SECONDS", "120")))

        self.obstruction_enabled = self._bool("ROAD_OBSTRUCTION_FALLBACK_ENABLED", True)
        self.obstruction_every_n_frames = max(1, int(os.getenv("OBSTRUCTION_EVERY_N_FRAMES", "5")))
        self.obstruction_roi_top = min(0.85, max(0.0, float(os.getenv("OBSTRUCTION_ROI_TOP_RATIO", "0.28"))))
        self.obstruction_min_area_ratio = max(0.002, float(os.getenv("OBSTRUCTION_MIN_AREA_RATIO", "0.012")))
        self.obstruction_confirm_frames = max(2, int(os.getenv("OBSTRUCTION_CONFIRM_FRAMES", "10")))
        self.obstruction_cooldown = max(15, int(os.getenv("OBSTRUCTION_COOLDOWN_SECONDS", "180")))
        self.obstruction_learning_rate = min(0.01, max(0.00005, float(os.getenv("OBSTRUCTION_LEARNING_RATE", "0.0005"))))
        self.obstruction_warmup_frames = max(10, int(os.getenv("OBSTRUCTION_WARMUP_FRAMES", "45")))

        self.track_history = defaultdict(lambda: deque(maxlen=8))
        self.accident_votes = defaultdict(lambda: deque(maxlen=self.accident_window))
        self.last_incident = defaultdict(float)

        self.bg_models = {}
        self.bg_frames = defaultdict(int)
        self.obstruction_votes = defaultdict(int)

        self.log.info(
            "Incident detector enabled=%s accident=%s obstruction_fallback=%s",
            self.enabled,
            self.accident_enabled,
            self.obstruction_enabled,
        )

    @staticmethod
    def _bool(name, default=False):
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _class_name(model, cls_id):
        names = getattr(model, "names", {})
        if isinstance(names, dict):
            return str(names.get(cls_id, cls_id))
        try:
            return str(names[cls_id])
        except Exception:
            return str(cls_id)

    @staticmethod
    def _iou(a, b):
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        inter = iw * ih
        if inter <= 0:
            return 0.0
        aa = max(1, (ax2 - ax1) * (ay2 - ay1))
        ba = max(1, (bx2 - bx1) * (by2 - by1))
        return inter / max(1.0, aa + ba - inter)

    @staticmethod
    def _center(box):
        x1, y1, x2, y2 = box
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

    @staticmethod
    def _diag(box):
        x1, y1, x2, y2 = box
        return max(1.0, math.hypot(x2 - x1, y2 - y1))

    @staticmethod
    def _dist(a, b):
        return math.hypot(a[0] - b[0], a[1] - b[1])

    @staticmethod
    def _jpeg(frame):
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
        return encoded.tobytes() if ok else None

    @staticmethod
    def _crop(frame, box, pad=35):
        if frame is None:
            return None
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in box]
        x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
        x2, y2 = min(w, x2 + pad), min(h, y2 + pad)
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2].copy()

    @staticmethod
    def _draw(frame, box, label, color):
        if frame is None:
            return
        x1, y1, x2, y2 = [int(v) for v in box]
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)
        cv2.putText(frame, label, (x1, max(24, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.58, color, 2, cv2.LINE_AA)

    def _objects(self, result, model):
        vehicles = []
        persons = []
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return persons, vehicles
        for box in boxes:
            try:
                cls_id = int(box.cls[0].item())
                conf = float(box.conf[0].item())
                coords = [int(v) for v in box.xyxy[0].tolist()]
                track_id = int(box.id[0].item()) if box.id is not None else None
            except Exception:
                continue
            row = {
                "class_id": cls_id,
                "class_name": self._class_name(model, cls_id).lower(),
                "confidence": conf,
                "track_id": track_id,
                "box": coords,
            }
            if cls_id == 0:
                persons.append(row)
            elif cls_id in self.VEHICLE_CLASS_IDS:
                vehicles.append(row)
        return persons, vehicles

    def _update_history(self, camera_key, vehicles):
        now = time.time()
        active = set()
        for vehicle in vehicles:
            track_id = vehicle.get("track_id")
            if track_id is None:
                continue
            key = (camera_key, track_id)
            active.add(key)
            self.track_history[key].append({
                "time": now,
                "center": self._center(vehicle["box"]),
                "box": vehicle["box"],
            })
        # Lightweight cleanup of very old tracks.
        for key in list(self.track_history.keys()):
            if key[0] != camera_key:
                continue
            history = self.track_history[key]
            if not history or now - history[-1]["time"] > 15:
                self.track_history.pop(key, None)

    def _motion_ratio(self, camera_key, vehicle):
        track_id = vehicle.get("track_id")
        if track_id is None:
            return 0.0, 0.0
        history = self.track_history.get((camera_key, track_id))
        if not history or len(history) < 3:
            return 0.0, 0.0
        prev = self._dist(history[-3]["center"], history[-2]["center"])
        curr = self._dist(history[-2]["center"], history[-1]["center"])
        diag = self._diag(vehicle["box"])
        return prev / diag, curr / diag

    def _fallen_person_near(self, persons, union_box):
        ux1, uy1, ux2, uy2 = union_box
        uw, uh = max(1, ux2 - ux1), max(1, uy2 - uy1)
        for person in persons:
            px1, py1, px2, py2 = person["box"]
            pw, ph = max(1, px2 - px1), max(1, py2 - py1)
            center = self._center(person["box"])
            nearby = (
                ux1 - uw * 0.7 <= center[0] <= ux2 + uw * 0.7
                and uy1 - uh * 0.7 <= center[1] <= uy2 + uh * 0.9
            )
            # Horizontal/low aspect is only a supporting cue, never a standalone accident.
            if nearby and pw / ph >= 0.90:
                return True
        return False

    def _accident_detection(self, camera, frame, persons, vehicles, draw_frame):
        camera_key = camera["camera_key"]
        events = []
        for i in range(len(vehicles)):
            a = vehicles[i]
            if a.get("track_id") is None:
                continue
            for j in range(i + 1, len(vehicles)):
                b = vehicles[j]
                if b.get("track_id") is None:
                    continue

                ac, bc = self._center(a["box"]), self._center(b["box"])
                max_diag = max(self._diag(a["box"]), self._diag(b["box"]))
                proximity = self._dist(ac, bc) / max_diag
                iou = self._iou(a["box"], b["box"])
                if iou < self.accident_iou_threshold and proximity > self.accident_proximity_ratio:
                    continue

                a_prev, a_curr = self._motion_ratio(camera_key, a)
                b_prev, b_curr = self._motion_ratio(camera_key, b)
                prior_motion = max(a_prev, b_prev)
                current_motion = max(a_curr, b_curr)

                union = [
                    min(a["box"][0], b["box"][0]),
                    min(a["box"][1], b["box"][1]),
                    max(a["box"][2], b["box"][2]),
                    max(a["box"][3], b["box"][3]),
                ]
                fallen_person = self._fallen_person_near(persons, union)
                sudden_slow = (
                    prior_motion >= self.accident_min_motion_ratio
                    and current_motion <= max(0.015, prior_motion * self.accident_stop_ratio)
                )

                candidate = bool(sudden_slow or fallen_person)
                pair = tuple(sorted((int(a["track_id"]), int(b["track_id"]))))
                vote_key = (camera_key, pair)
                votes = self.accident_votes[vote_key]
                votes.append(1 if candidate else 0)

                if candidate:
                    self._draw(draw_frame, union, "ACCIDENT CHECK", (0, 165, 255))

                if sum(votes) < self.accident_confirm_frames:
                    continue

                now = time.time()
                cooldown_key = (camera_key, "accident", pair)
                if now - self.last_incident[cooldown_key] < self.accident_cooldown:
                    continue
                self.last_incident[cooldown_key] = now

                confidence = 0.55
                if sudden_slow:
                    confidence += 0.18
                if fallen_person:
                    confidence += 0.17
                if iou >= 0.12:
                    confidence += 0.08
                confidence = min(0.98, confidence)

                severity = "High" if fallen_person or confidence >= 0.78 else "Medium"
                self._draw(draw_frame, union, f"ACCIDENT {confidence * 100:.0f}%", (0, 0, 255))
                evidence = self._crop(frame, union, 70)
                event_id = self.store.record(
                    session_id=self.session_id,
                    camera=camera,
                    incident_type="accident",
                    severity=severity,
                    confidence=confidence,
                    evidence_image=self._jpeg(evidence if evidence is not None else frame),
                    metadata={
                        "vehicle_a": a["class_name"],
                        "vehicle_b": b["class_name"],
                        "track_a": a["track_id"],
                        "track_b": b["track_id"],
                        "iou": round(iou, 4),
                        "proximity_ratio": round(proximity, 4),
                        "prior_motion_ratio": round(prior_motion, 4),
                        "current_motion_ratio": round(current_motion, 4),
                        "sudden_slow": sudden_slow,
                        "fallen_person_cue": fallen_person,
                        "box": union,
                    },
                )
                if event_id:
                    events.append(event_id)
                    votes.clear()
                    self.log.info("Accident event stored camera=%s id=%s", camera_key, event_id)
        return events

    @staticmethod
    def _overlap_ratio(box, other):
        x1, y1, x2, y2 = box
        ox1, oy1, ox2, oy2 = other
        ix1, iy1 = max(x1, ox1), max(y1, oy1)
        ix2, iy2 = min(x2, ox2), min(y2, oy2)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        area = max(1, (x2 - x1) * (y2 - y1))
        return inter / area

    def _obstruction_detection(self, camera, frame, persons, vehicles, draw_frame):
        camera_key = camera["camera_key"]
        if frame is None:
            return []
        h, w = frame.shape[:2]
        top = int(h * self.obstruction_roi_top)
        roi = frame[top:h, 0:w]
        if roi.size == 0:
            return []

        bg = self.bg_models.get(camera_key)
        if bg is None:
            bg = cv2.createBackgroundSubtractorMOG2(history=1200, varThreshold=34, detectShadows=True)
            self.bg_models[camera_key] = bg

        self.bg_frames[camera_key] += 1
        mask = bg.apply(roi, learningRate=self.obstruction_learning_rate)
        if self.bg_frames[camera_key] <= self.obstruction_warmup_frames:
            return []

        _, mask = cv2.threshold(mask, 220, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=3)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        roi_area = float(max(1, roi.shape[0] * roi.shape[1]))
        active_boxes = [row["box"] for row in persons + vehicles]
        touched_keys = set()
        events = []

        for contour in contours:
            area = cv2.contourArea(contour)
            area_ratio = area / roi_area
            if area_ratio < self.obstruction_min_area_ratio:
                continue
            x, y, bw, bh = cv2.boundingRect(contour)
            full_box = [x, y + top, x + bw, y + top + bh]
            if bw < 35 or bh < 25:
                continue

            # Current people/vehicles are normal traffic, not a generic obstruction.
            if any(self._overlap_ratio(full_box, other) >= 0.30 for other in active_boxes):
                continue

            cx = (full_box[0] + full_box[2]) // 2
            cy = (full_box[1] + full_box[3]) // 2
            spatial_key = (camera_key, int(cx / 180), int(cy / 140))
            touched_keys.add(spatial_key)
            self.obstruction_votes[spatial_key] += 1

            count = self.obstruction_votes[spatial_key]
            self._draw(draw_frame, full_box, f"ROAD OBSTRUCTION? {count}/{self.obstruction_confirm_frames}", (0, 165, 255))
            if count < self.obstruction_confirm_frames:
                continue

            now = time.time()
            cooldown_key = (camera_key, "road_obstruction", spatial_key[1], spatial_key[2])
            if now - self.last_incident[cooldown_key] < self.obstruction_cooldown:
                continue
            self.last_incident[cooldown_key] = now

            confidence = min(0.92, 0.50 + min(0.30, area_ratio * 4.0) + min(0.12, count / 100.0))
            severity = "High" if area_ratio >= 0.04 else "Medium"
            self._draw(draw_frame, full_box, f"ROAD OBSTRUCTION {confidence * 100:.0f}%", (0, 0, 255))
            evidence = self._crop(frame, full_box, 60)
            event_id = self.store.record(
                session_id=self.session_id,
                camera=camera,
                incident_type="road_obstruction",
                severity=severity,
                confidence=confidence,
                evidence_image=self._jpeg(evidence if evidence is not None else frame),
                metadata={
                    "source": "persistent_scene_change",
                    "area_ratio": round(area_ratio, 5),
                    "confirmation_frames": count,
                    "box": full_box,
                    "note": "Generic obstruction candidate; may represent fallen tree, branch, debris or another newly persistent object.",
                },
            )
            if event_id:
                events.append(event_id)
                self.obstruction_votes[spatial_key] = 0
                self.log.info("Road obstruction event stored camera=%s id=%s", camera_key, event_id)

        # Decay candidates not seen in this check.
        for key in list(self.obstruction_votes.keys()):
            if key[0] != camera_key or key in touched_keys:
                continue
            self.obstruction_votes[key] = max(0, self.obstruction_votes[key] - 1)
            if self.obstruction_votes[key] == 0:
                self.obstruction_votes.pop(key, None)
        return events

    def process(self, camera, clean_frame, primary_result, primary_model, processed_index, draw_frame=None):
        summary = {"accidents": 0, "road_obstructions": 0}
        if not self.enabled or primary_result is None:
            return summary
        if processed_index % self.every_n_frames != 0:
            return summary

        draw_frame = draw_frame if draw_frame is not None else clean_frame
        persons, vehicles = self._objects(primary_result, primary_model)
        camera_key = camera["camera_key"]
        self._update_history(camera_key, vehicles)

        if self.accident_enabled:
            summary["accidents"] = len(
                self._accident_detection(camera, clean_frame, persons, vehicles, draw_frame)
            )

        if self.obstruction_enabled and processed_index % self.obstruction_every_n_frames == 0:
            summary["road_obstructions"] = len(
                self._obstruction_detection(camera, clean_frame, persons, vehicles, draw_frame)
            )
        return summary
