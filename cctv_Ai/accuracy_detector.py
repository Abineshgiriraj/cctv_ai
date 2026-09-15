import math
import os
import time
from collections import Counter

import cv2

from advanced_detection import AdvancedDetector


class AccuracyDetector(AdvancedDetector):
    """Higher precision detector for fixed, high-angle municipal CCTV cameras.

    The base detector remains responsible for model loading, plate OCR, evidence
    storage and shared helpers. This layer tightens helmet decisions around the
    rider's head and gives small road defects more pixels by using overlapping
    road tiles instead of shrinking the complete roadway into one inference.
    """

    def __init__(self, config, store, session_id, log):
        super().__init__(config, store, session_id, log)
        self.strict_no_helmet_confidence = max(
            float(getattr(config, "NO_HELMET_CONFIDENCE", 0.58)),
            float(os.getenv("STRICT_NO_HELMET_MIN_CONFIDENCE", "0.68")),
        )
        self.strict_no_helmet_confirm_frames = max(
            int(getattr(config, "HELMET_CONFIRM_FRAMES", 2)),
            int(os.getenv("STRICT_NO_HELMET_CONFIRM_FRAMES", "3")),
        )
        self.helmet_vote_ratio = min(
            1.0,
            max(0.5, float(os.getenv("STRICT_NO_HELMET_VOTE_RATIO", "0.75"))),
        )
        self.helmet_conflict_margin = max(
            0.0, float(os.getenv("HELMET_CONFLICT_MARGIN", "0.12"))
        )
        self.road_tile_overlap = min(
            0.40, max(0.05, float(os.getenv("ROAD_TILE_OVERLAP", "0.18")))
        )
        self.road_tile_columns = max(1, min(3, int(os.getenv("ROAD_TILE_COLUMNS", "2"))))
        self.log.info(
            "Accuracy mode: no_helmet_conf>=%.2f confirm_frames=%s vote_ratio=%.2f road_tiles=%s",
            self.strict_no_helmet_confidence,
            self.strict_no_helmet_confirm_frames,
            self.helmet_vote_ratio,
            self.road_tile_columns,
        )

    @staticmethod
    def _head_region(frame, person_box):
        """Return a tight head/helmet ROI from a detected rider person box."""
        if person_box is None:
            return None
        h, w = frame.shape[:2]
        px1, py1, px2, py2 = [int(v) for v in person_box]
        pw = max(1, px2 - px1)
        ph = max(1, py2 - py1)

        # Top ~45% of the person contains head + shoulders. A modest horizontal
        # pad handles helmets extending outside the COCO person box.
        x1 = px1 - int(pw * 0.22)
        x2 = px2 + int(pw * 0.22)
        y1 = py1 - int(ph * 0.12)
        y2 = py1 + int(ph * 0.46)
        return [max(0, x1), max(0, y1), min(w, x2), min(h, y2)]

    def _helmet_threshold(self, status):
        if status == "no_helmet":
            return self.strict_no_helmet_confidence
        return float(self.cfg.HELMET_CONFIDENCE)

    def _helmet_status(self, frame, bike, person):
        model = self.models.get("helmet")
        if model is None:
            return None

        # Precision first: do not create a helmet violation from a guessed rider
        # region when the primary person detector has not actually found a rider.
        if person is None:
            return None

        region = self._head_region(frame, person.get("box"))
        if region is None:
            return None
        crop = self._crop(frame, region, 4)
        if crop is None or crop.size == 0:
            return None

        ch, cw = crop.shape[:2]
        max_side = max(ch, cw)
        scale = 4.0 if max_side < 120 else 3.0 if max_side < 220 else 2.0 if max_side < 360 else 1.0
        source = cv2.resize(
            crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
        ) if scale > 1.0 else crop

        infer_conf = min(float(self.cfg.HELMET_CONFIDENCE), self.strict_no_helmet_confidence)
        try:
            results = model.predict(
                source,
                conf=infer_conf,
                imgsz=int(self.cfg.HELMET_IMGSZ),
                verbose=False,
            )
        except Exception as exc:
            self.log.debug("Helmet head-ROI inference failed: %s", exc)
            return None

        candidates = {"helmet": [], "no_helmet": []}
        names = getattr(model, "names", {})
        for result in results or []:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                try:
                    cls_id = int(box.cls[0].item())
                    confidence = float(box.conf[0].item())
                    raw_name = names.get(cls_id, cls_id) if isinstance(names, dict) else names[cls_id]
                    status = self._helmet_label(raw_name)
                    if not status or confidence < self._helmet_threshold(status):
                        continue
                    x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
                    mapped = [
                        int(region[0] + x1 / scale),
                        int(region[1] + y1 / scale),
                        int(region[0] + x2 / scale),
                        int(region[1] + y2 / scale),
                    ]
                    candidates[status].append({
                        "status": status,
                        "confidence": confidence,
                        "box": mapped,
                        "search_box": region,
                    })
                except Exception:
                    continue

        best_helmet = max(candidates["helmet"], key=lambda row: row["confidence"], default=None)
        best_no_helmet = max(candidates["no_helmet"], key=lambda row: row["confidence"], default=None)

        if best_helmet and best_no_helmet:
            # If the model sees both classes on the same tiny head ROI, a visible
            # helmet is a safety veto unless the no-helmet score is clearly better.
            if best_no_helmet["confidence"] < best_helmet["confidence"] + self.helmet_conflict_margin:
                return best_helmet
            return best_no_helmet
        return best_helmet or best_no_helmet

    def _helmet_confirmed(self, camera_ip, bike, observation):
        key = self._track_key(camera_ip, bike)
        votes = self.helmet_votes[key]
        votes.append((observation["status"], observation["confidence"]))

        counts = Counter(status for status, _ in votes)
        status = observation["status"]
        same = [conf for vote_status, conf in votes if vote_status == status]
        if not same:
            return None

        if status == "no_helmet":
            required = self.strict_no_helmet_confirm_frames
            if len(votes) < required or len(same) < required:
                return None
            if len(same) / max(1, len(votes)) < self.helmet_vote_ratio:
                return None
            # Any strong positive helmet observation in the same recent window
            # prevents a violation until the track produces cleaner evidence.
            strong_helmet = [
                conf for vote_status, conf in votes
                if vote_status == "helmet" and conf >= float(self.cfg.HELMET_CONFIDENCE) + 0.08
            ]
            if strong_helmet:
                return None
        else:
            if len(same) < max(2, int(self.cfg.HELMET_CONFIRM_FRAMES)):
                return None

        if counts.most_common(1)[0][0] != status:
            return None
        average = sum(same) / len(same)
        if average < self._helmet_threshold(status):
            return None
        return status, average, len(same), len(votes)

    @staticmethod
    def _iou(box_a, box_b):
        ax1, ay1, ax2, ay2 = box_a
        bx1, by1, bx2, by2 = box_b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        inter = iw * ih
        if inter <= 0:
            return 0.0
        area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
        area_b = max(1, (bx2 - bx1) * (by2 - by1))
        return inter / float(area_a + area_b - inter)

    def _road_tiles(self, frame):
        road, y_offset = self._road_roi(frame)
        rh, rw = road.shape[:2]
        columns = self.road_tile_columns
        if columns <= 1 or rw < 900:
            return [(road, 0, y_offset)]

        tile_width = int(math.ceil(rw / columns))
        overlap = int(tile_width * self.road_tile_overlap)
        tiles = []
        for index in range(columns):
            x1 = max(0, index * tile_width - (overlap if index else 0))
            x2 = min(rw, (index + 1) * tile_width + (overlap if index < columns - 1 else 0))
            tile = road[:, x1:x2]
            if tile.size:
                tiles.append((tile, x1, y_offset))
        return tiles

    def _run_road_model(self, camera_ip, frame, model_key, event_type, draw_frame=None):
        model = self.models.get(model_key)
        if model is None:
            return []

        threshold = float(self.cfg.ROAD_DAMAGE_CONFIDENCE) if model_key == "road_damage" else float(self.cfg.ADVANCED_CONFIDENCE)
        detections = []
        for source, x_offset, y_offset in self._road_tiles(frame):
            try:
                results = model.predict(
                    source,
                    conf=threshold,
                    imgsz=int(self.cfg.ROAD_DAMAGE_IMGSZ),
                    verbose=False,
                )
            except Exception as exc:
                self.log.debug("%s tiled inference failed: %s", model_key, exc)
                continue

            names = getattr(model, "names", {})
            for result in results or []:
                boxes = getattr(result, "boxes", None)
                if boxes is None:
                    continue
                for box in boxes:
                    try:
                        cls_id = int(box.cls[0].item())
                        confidence = float(box.conf[0].item())
                        x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                        full_box = [x1 + x_offset, y1 + y_offset, x2 + x_offset, y2 + y_offset]
                        label = str(names.get(cls_id, cls_id) if isinstance(names, dict) else names[cls_id])
                    except Exception:
                        continue
                    detections.append({
                        "label": label,
                        "confidence": confidence,
                        "box": full_box,
                    })

        # Remove duplicate boxes created in the overlap between road tiles.
        detections.sort(key=lambda row: row["confidence"], reverse=True)
        kept = []
        for detection in detections:
            if any(
                detection["label"] == existing["label"]
                and self._iou(detection["box"], existing["box"]) >= 0.45
                for existing in kept
            ):
                continue
            kept.append(detection)

        events = []
        for detection in kept:
            label = detection["label"]
            confidence = detection["confidence"]
            full_box = detection["box"]
            if draw_frame is not None:
                self._draw(
                    draw_frame,
                    full_box,
                    f"ROAD {label.upper()} {confidence * 100:.0f}%",
                    (0, 165, 255),
                )

            if self.store is None:
                continue
            cx = (full_box[0] + full_box[2]) // 2
            cy = (full_box[1] + full_box[3]) // 2
            spatial_key = (
                camera_ip, event_type, label.lower(), int(cx / 160), int(cy / 120)
            )
            now = time.time()
            if now - self.last_road_event[spatial_key] < self.cfg.ROAD_EVENT_COOLDOWN_SECONDS:
                continue
            self.last_road_event[spatial_key] = now
            crop = self._crop(frame, full_box, 30)
            event_id = self.store.record_road_event(
                session_id=self.session_id,
                camera_ip=camera_ip,
                event_type=event_type,
                model_label=label,
                confidence=confidence,
                evidence_image=self._jpeg(crop if crop is not None else frame),
                metadata={
                    "box": full_box,
                    "road_roi_top_ratio": self.cfg.ROAD_ROI_TOP_RATIO,
                    "tiled_inference": True,
                    "tile_columns": self.road_tile_columns,
                },
            )
            events.append({
                "id": event_id,
                "label": label,
                "confidence": confidence,
                "box": full_box,
            })
        return events
