import math
import time
from collections import defaultdict, deque

import cv2
import numpy as np


class IncidentDetector:
    """Model-free accident and persistent road-obstruction detection.

    Accident detection combines tracked-vehicle proximity, pre-impact motion,
    sudden slowing/stopping and an optional fallen-person cue. Road obstruction
    uses a low-learning-rate fixed-camera foreground model and ignores regions
    currently occupied by normal YOLO road users.
    """

    VEHICLE_CLASSES = {1, 2, 3, 5, 7}

    def __init__(self, config, store, session_id, log):
        self.cfg = config
        self.store = store
        self.session_id = session_id
        self.log = log

        self.history = defaultdict(lambda: deque(maxlen=10))
        self.accident_votes = defaultdict(int)
        self.accident_last_seen = defaultdict(float)
        self.accident_pair_history = defaultdict(
            lambda: deque(maxlen=self.cfg.ACCIDENT_PAIR_HISTORY)
        )
        self.last_incident = defaultdict(float)

        self.bg_models = {}
        self.bg_warmup = defaultdict(int)
        self.obstruction_votes = defaultdict(int)
        self.obstruction_last_seen = defaultdict(float)

    @staticmethod
    def _jpeg(frame):
        if frame is None or frame.size == 0:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        return buf.tobytes() if ok else None

    @staticmethod
    def _center(box):
        x1, y1, x2, y2 = box
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

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
        aa = max(1.0, (ax2 - ax1) * (ay2 - ay1))
        ab = max(1.0, (bx2 - bx1) * (by2 - by1))
        return inter / (aa + ab - inter)

    @staticmethod
    def _box_diag(box):
        x1, y1, x2, y2 = box
        return math.hypot(max(1.0, x2 - x1), max(1.0, y2 - y1))

    def _objects(self, result):
        vehicles, persons = [], []
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return vehicles, persons
        for box in boxes:
            try:
                cls_id = int(box.cls[0].item())
                confidence = float(box.conf[0].item())
                coords = [int(v) for v in box.xyxy[0].tolist()]
                track_id = int(box.id[0].item()) if box.id is not None else None
            except Exception:
                continue
            row = {
                "cls_id": cls_id,
                "confidence": confidence,
                "box": coords,
                "track_id": track_id,
            }
            if cls_id in self.VEHICLE_CLASSES:
                vehicles.append(row)
            elif cls_id == 0:
                persons.append(row)
        return vehicles, persons

    def _motion(self, camera_key, vehicle, frame_diag):
        track_id = vehicle.get("track_id")
        if track_id is None:
            return 0.0, 0.0
        key = (camera_key, track_id)
        now = time.time()
        center = self._center(vehicle["box"])
        hist = self.history[key]
        hist.append((now, center))

        if len(hist) < 2:
            return 0.0, 0.0

        def step_speed(a, b):
            dt = max(0.05, b[0] - a[0])
            px = math.hypot(b[1][0] - a[1][0], b[1][1] - a[1][1])
            return (px / max(1.0, frame_diag)) / dt

        current = step_speed(hist[-2], hist[-1])
        older = []
        items = list(hist)
        for idx in range(max(1, len(items) - 6), len(items) - 1):
            older.append(step_speed(items[idx - 1], items[idx]))
        previous = max(older, default=current)
        return current, previous

    @staticmethod
    def _fallen_person_near(persons, collision_box):
        cx1, cy1, cx2, cy2 = collision_box
        cw, ch = max(1, cx2 - cx1), max(1, cy2 - cy1)
        for person in persons:
            px1, py1, px2, py2 = person["box"]
            pw, ph = max(1, px2 - px1), max(1, py2 - py1)
            pcx, pcy = (px1 + px2) / 2.0, (py1 + py2) / 2.0
            nearby = (
                cx1 - cw <= pcx <= cx2 + cw
                and cy1 - ch <= pcy <= cy2 + ch
            )
            horizontal = pw / ph >= 1.15
            if nearby and horizontal:
                return True
        return False

    def _store(self, camera, frame, incident_type, severity, confidence, metadata):
        if self.store is None:
            return None
        cooldown_key = (camera["camera_key"], incident_type)
        now = time.time()
        if now - self.last_incident[cooldown_key] < self.cfg.INCIDENT_COOLDOWN_SECONDS:
            return None
        try:
            row_id = self.store.record_incident(
                session_id=self.session_id,
                camera=camera,
                incident_type=incident_type,
                severity=severity,
                confidence=confidence,
                evidence_image=self._jpeg(frame),
                metadata=metadata,
            )
        except Exception as exc:
            self.log.exception(
                "Incident storage failed camera=%s type=%s: %s",
                camera["camera_key"], incident_type, exc,
            )
            return None
        self.last_incident[cooldown_key] = now
        return row_id

    def _detect_accident(self, camera, frame, vehicles, persons, draw_frame):
        """High-precision accident screening.

        A normal traffic stop is not enough. A pair must show:
        1) genuine contact/very-close geometry,
        2) a clear closing trajectory before contact,
        3) meaningful pre-impact motion,
        4) abrupt deceleration OR a nearby fallen-person cue,
        5) the condition for several consecutive processed frames.
        """
        camera_key = camera["camera_key"]
        h, w = frame.shape[:2]
        frame_diag = math.hypot(w, h)

        motion = {}
        for vehicle in vehicles:
            track_id = vehicle.get("track_id")
            if track_id is not None:
                motion[track_id] = self._motion(
                    camera_key, vehicle, frame_diag
                )

        stored = 0
        now = time.time()

        for i in range(len(vehicles)):
            a = vehicles[i]
            if a.get("track_id") is None:
                continue

            for j in range(i + 1, len(vehicles)):
                b = vehicles[j]
                if b.get("track_id") is None:
                    continue

                a_id, b_id = sorted(
                    (int(a["track_id"]), int(b["track_id"]))
                )
                pair_key = (camera_key, a_id, b_id)

                ac = self._center(a["box"])
                bc = self._center(b["box"])
                distance = math.hypot(
                    ac[0] - bc[0],
                    ac[1] - bc[1],
                )

                size_ref = max(
                    self._box_diag(a["box"]),
                    self._box_diag(b["box"]),
                    1.0,
                )
                normalized_distance = distance / size_ref
                iou = self._iou(a["box"], b["box"])

                pair_hist = self.accident_pair_history[pair_key]
                pair_hist.append((now, normalized_distance, iou))

                # Measure whether the two tracks were actually closing before
                # the current near-contact frame. Tailgating / queueing usually
                # produces nearly constant pair distance and is rejected.
                closing_ratio = 0.0
                if len(pair_hist) >= 3:
                    older = [row[1] for row in list(pair_hist)[:-1]]
                    older_ref = max(older) if older else normalized_distance
                    closing_ratio = max(
                        0.0,
                        older_ref - normalized_distance,
                    )

                current_a, previous_a = motion.get(
                    a["track_id"], (0.0, 0.0)
                )
                current_b, previous_b = motion.get(
                    b["track_id"], (0.0, 0.0)
                )

                moved_before = (
                    max(previous_a, previous_b)
                    >= self.cfg.ACCIDENT_MIN_MOTION_RATIO
                )

                abrupt_stop_a = (
                    previous_a >= self.cfg.ACCIDENT_MIN_MOTION_RATIO
                    and current_a
                    <= max(
                        0.0015,
                        previous_a * self.cfg.ACCIDENT_STOP_RATIO,
                    )
                )
                abrupt_stop_b = (
                    previous_b >= self.cfg.ACCIDENT_MIN_MOTION_RATIO
                    and current_b
                    <= max(
                        0.0015,
                        previous_b * self.cfg.ACCIDENT_STOP_RATIO,
                    )
                )
                abrupt_stop = abrupt_stop_a or abrupt_stop_b

                strong_contact = (
                    iou >= self.cfg.ACCIDENT_IOU_THRESHOLD
                    or normalized_distance
                    <= self.cfg.ACCIDENT_PROXIMITY_RATIO
                )
                closing = (
                    closing_ratio
                    >= self.cfg.ACCIDENT_MIN_CLOSING_RATIO
                )

                union = [
                    min(a["box"][0], b["box"][0]),
                    min(a["box"][1], b["box"][1]),
                    max(a["box"][2], b["box"][2]),
                    max(a["box"][3], b["box"][3]),
                ]
                fallen = self._fallen_person_near(
                    persons, union
                )

                # Two independent high-precision paths:
                # - vehicle-to-vehicle impact: contact + closing + abrupt stop
                # - possible rider crash: contact/near + motion + fallen person
                vehicle_impact = (
                    strong_contact
                    and moved_before
                    and closing
                    and abrupt_stop
                )
                rider_impact = (
                    strong_contact
                    and moved_before
                    and fallen
                    and (
                        closing
                        or abrupt_stop
                    )
                )
                candidate = vehicle_impact or rider_impact

                if candidate:
                    self.accident_votes[pair_key] += 1
                    self.accident_last_seen[pair_key] = now
                else:
                    # Reset quickly. Normal traffic should not accumulate
                    # accident votes from unrelated intermittent frames.
                    if (
                        now - self.accident_last_seen[pair_key]
                        > 0.8
                    ):
                        self.accident_votes[pair_key] = 0

                if (
                    self.accident_votes[pair_key]
                    < self.cfg.ACCIDENT_CONFIRM_FRAMES
                ):
                    continue

                score = 0.50
                score += min(0.20, iou * 0.80)
                score += min(0.12, closing_ratio * 0.35)
                if abrupt_stop:
                    score += 0.12
                if fallen:
                    score += 0.16
                score = min(0.99, score)

                # Only draw after the strict multi-frame confirmation.
                if draw_frame is not None:
                    x1, y1, x2, y2 = union
                    cv2.rectangle(
                        draw_frame,
                        (x1, y1),
                        (x2, y2),
                        (0, 0, 255),
                        3,
                    )
                    cv2.putText(
                        draw_frame,
                        f"POSSIBLE ACCIDENT {score * 100:.0f}%",
                        (x1, max(25, y1 - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.62,
                        (0, 0, 255),
                        2,
                        cv2.LINE_AA,
                    )

                row_id = self._store(
                    camera,
                    frame,
                    "accident",
                    "High",
                    score,
                    {
                        "track_ids": [a_id, b_id],
                        "iou": iou,
                        "distance_px": distance,
                        "normalized_distance": normalized_distance,
                        "closing_ratio": closing_ratio,
                        "previous_motion": [
                            previous_a,
                            previous_b,
                        ],
                        "current_motion": [
                            current_a,
                            current_b,
                        ],
                        "abrupt_stop": abrupt_stop,
                        "fallen_person_cue": fallen,
                        "confirm_frames": self.cfg.ACCIDENT_CONFIRM_FRAMES,
                        "method": (
                            "high_precision_contact_closing_"
                            "deceleration_multi_frame"
                        ),
                    },
                )
                if row_id:
                    stored += 1

                self.accident_votes[pair_key] = 0

        # Remove stale pair state so track-id reuse cannot inherit old votes.
        for key in list(self.accident_pair_history):
            if key[0] != camera_key:
                continue
            history = self.accident_pair_history[key]
            if history and now - history[-1][0] > 5.0:
                self.accident_pair_history.pop(key, None)
                self.accident_votes.pop(key, None)
                self.accident_last_seen.pop(key, None)

        return stored

    def _detect_obstruction(self, camera, frame, vehicles, persons, draw_frame):
        camera_key = camera["camera_key"]
        h, w = frame.shape[:2]
        roi_top = int(h * self.cfg.OBSTRUCTION_ROI_TOP_RATIO)
        roi = frame[roi_top:h, :]
        if roi.size == 0:
            return 0

        bg = self.bg_models.get(camera_key)
        if bg is None:
            bg = cv2.createBackgroundSubtractorMOG2(
                history=600,
                varThreshold=36,
                detectShadows=False,
            )
            self.bg_models[camera_key] = bg

        mask = bg.apply(roi, learningRate=self.cfg.OBSTRUCTION_LEARNING_RATE)
        self.bg_warmup[camera_key] += 1
        if self.bg_warmup[camera_key] <= self.cfg.OBSTRUCTION_WARMUP_FRAMES:
            return 0

        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

        occupied = [row["box"] for row in vehicles] + [row["box"] for row in persons]
        frame_area = float(max(1, w * h))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        now = time.time()
        stored = 0
        active_keys = set()

        for contour in contours:
            area = cv2.contourArea(contour)
            if area / frame_area < self.cfg.OBSTRUCTION_MIN_AREA_RATIO:
                continue
            x, y, bw, bh = cv2.boundingRect(contour)
            box = [x, y + roi_top, x + bw, y + roi_top + bh]

            if any(self._iou(box, normal_box) >= 0.18 for normal_box in occupied):
                continue

            cx = (box[0] + box[2]) // 2
            cy = (box[1] + box[3]) // 2
            spatial = (
                camera_key,
                int(cx / max(80, w * 0.08)),
                int(cy / max(60, h * 0.08)),
            )
            active_keys.add(spatial)
            self.obstruction_votes[spatial] += 1
            self.obstruction_last_seen[spatial] = now

            if self.obstruction_votes[spatial] < self.cfg.OBSTRUCTION_CONFIRM_FRAMES:
                continue

            confidence = min(
                0.95,
                0.45 + (area / frame_area) * 4.0,
            )
            if draw_frame is not None:
                x1, y1, x2, y2 = box
                cv2.rectangle(draw_frame, (x1, y1), (x2, y2), (0, 165, 255), 3)
                cv2.putText(
                    draw_frame,
                    f"ROAD OBSTRUCTION {confidence * 100:.0f}%",
                    (x1, max(25, y1 - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.62,
                    (0, 165, 255),
                    2,
                    cv2.LINE_AA,
                )

            row_id = self._store(
                camera,
                frame,
                "road_obstruction",
                "High",
                confidence,
                {
                    "box": box,
                    "foreground_area_ratio": area / frame_area,
                    "method": "persistent_fixed_camera_foreground",
                    "note": "Large persistent roadway object; may include fallen tree, branch, debris or other blockage.",
                },
            )
            if row_id:
                stored += 1
            self.obstruction_votes[spatial] = 0

        for key in list(self.obstruction_votes):
            if key[0] != camera_key:
                continue
            if key not in active_keys and now - self.obstruction_last_seen[key] > 2.5:
                self.obstruction_votes[key] = 0
        return stored

    def process(self, camera, frame, result, processed_index, draw_frame=None):
        summary = {
            "accident_events": 0,
            "road_obstruction_events": 0,
        }
        if not self.cfg.INCIDENT_DETECTION_ENABLED or result is None:
            return summary
        if processed_index % self.cfg.INCIDENT_EVERY_N_FRAMES != 0:
            return summary

        vehicles, persons = self._objects(result)
        if self.cfg.ACCIDENT_DETECTION_ENABLED and len(vehicles) >= 2:
            summary["accident_events"] = self._detect_accident(
                camera, frame, vehicles, persons, draw_frame,
            )

        if self.cfg.ROAD_OBSTRUCTION_FALLBACK_ENABLED:
            summary["road_obstruction_events"] = self._detect_obstruction(
                camera, frame, vehicles, persons, draw_frame,
            )
        return summary
