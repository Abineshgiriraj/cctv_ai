import math
import os
import time
from collections import Counter

import cv2

from advanced_detection import AdvancedDetector


class AccuracyDetector(AdvancedDetector):
    """Precision-first detector with recall support for high-angle CCTV."""

    def __init__(self, config, store, session_id, log):
        super().__init__(config, store, session_id, log)

        # Candidate observations and final decisions are intentionally separate.
        # Distant rider heads often produce 30-70% model confidence. If those
        # observations are discarded before voting, a real no-helmet rider can
        # never become confirmed even when the model sees the same result across
        # several consecutive frames.
        self.helmet_observation_confidence = min(
            0.60,
            max(0.10, float(os.getenv("HELMET_OBSERVATION_CONFIDENCE", "0.30"))),
        )

        # Existing strict values remain useful as a "strong frame" signal, but
        # they are no longer used as the minimum confidence for every vote.
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

        self.no_helmet_final_avg_confidence = min(
            self.strict_no_helmet_confidence,
            max(
                self.helmet_observation_confidence,
                float(os.getenv("NO_HELMET_FINAL_AVG_CONFIDENCE", "0.56")),
            ),
        )

        self.fallback_no_helmet_confidence = max(
            self.strict_no_helmet_confidence,
            float(os.getenv("FALLBACK_NO_HELMET_CONFIDENCE", "0.74")),
        )
        self.fallback_confirm_frames = max(
            self.strict_no_helmet_confirm_frames,
            int(os.getenv("FALLBACK_NO_HELMET_CONFIRM_FRAMES", "3")),
        )
        self.fallback_vote_ratio = min(
            1.0,
            max(0.60, float(os.getenv("FALLBACK_NO_HELMET_VOTE_RATIO", "0.80"))),
        )
        self.fallback_final_avg_confidence = min(
            self.fallback_no_helmet_confidence,
            max(
                self.helmet_observation_confidence,
                float(os.getenv("FALLBACK_NO_HELMET_FINAL_AVG_CONFIDENCE", "0.60")),
            ),
        )

        self.road_tile_overlap = min(
            0.40, max(0.05, float(os.getenv("ROAD_TILE_OVERLAP", "0.18")))
        )
        self.road_tile_columns = max(
            1, min(3, int(os.getenv("ROAD_TILE_COLUMNS", "2")))
        )
        self.road_display_confidence = min(
            float(getattr(config, "ROAD_DAMAGE_CONFIDENCE", 0.25)),
            max(0.05, float(os.getenv("ROAD_DISPLAY_CONFIDENCE", "0.12"))),
        )

        self.log.info(
            "Accuracy mode: helmet_observation>=%.2f rider_final_avg>=%.2f "
            "rider_strong>=%.2f fallback_final_avg>=%.2f fallback_strong>=%.2f "
            "confirm=%s fallback_confirm=%s road_tiles=%s",
            self.helmet_observation_confidence,
            self.no_helmet_final_avg_confidence,
            self.strict_no_helmet_confidence,
            self.fallback_final_avg_confidence,
            self.fallback_no_helmet_confidence,
            self.strict_no_helmet_confirm_frames,
            self.fallback_confirm_frames,
            self.road_tile_columns,
        )

    @staticmethod
    def _head_region(frame, person_box):
        if person_box is None:
            return None
        h, w = frame.shape[:2]
        px1, py1, px2, py2 = [int(v) for v in person_box]
        pw = max(1, px2 - px1)
        ph = max(1, py2 - py1)
        x1 = px1 - int(pw * 0.22)
        x2 = px2 + int(pw * 0.22)
        y1 = py1 - int(ph * 0.12)
        y2 = py1 + int(ph * 0.46)
        return [max(0, x1), max(0, y1), min(w, x2), min(h, y2)]

    @staticmethod
    def _bike_head_region(frame, bike_box):
        h, w = frame.shape[:2]
        bx1, by1, bx2, by2 = [int(v) for v in bike_box]
        bw = max(1, bx2 - bx1)
        bh = max(1, by2 - by1)
        cx = (bx1 + bx2) / 2.0
        x1 = int(cx - bw * 0.62)
        x2 = int(cx + bw * 0.62)
        y1 = int(by1 - bh * 1.60)
        y2 = int(by1 + bh * 0.18)
        return [max(0, x1), max(0, y1), min(w, x2), min(h, y2)]

    def _helmet_threshold_for(self, status, source):
        if status != "no_helmet":
            return float(self.cfg.HELMET_CONFIDENCE)
        if source == "bike_fallback":
            return self.fallback_final_avg_confidence
        return self.no_helmet_final_avg_confidence

    def _helmet_threshold(self, status):
        return self._helmet_threshold_for(status, "person_head")

    def _helmet_status(self, frame, bike, person):
        model = self.models.get("helmet")
        if model is None:
            return None

        if person is not None:
            region = self._head_region(frame, person.get("box"))
            source_name = "person_head"
        else:
            region = self._bike_head_region(frame, bike.get("box"))
            source_name = "bike_fallback"

        if region is None:
            return None
        crop = self._crop(frame, region, 4)
        if crop is None or crop.size == 0:
            return None

        ch, cw = crop.shape[:2]
        max_side = max(ch, cw)
        scale = (
            6.0 if max_side < 90
            else 4.5 if max_side < 140
            else 3.0 if max_side < 220
            else 2.0 if max_side < 360
            else 1.0
        )
        source = (
            cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            if scale > 1.0 else crop
        )

        # This is deliberately a candidate threshold. Final acceptance happens
        # only after multi-frame voting in _helmet_confirmed().
        try:
            results = model.predict(
                source,
                conf=self.helmet_observation_confidence,
                imgsz=int(self.cfg.HELMET_IMGSZ),
                verbose=False,
            )
        except Exception as exc:
            self.log.debug("Helmet ROI inference failed: %s", exc)
            return None

        candidates = {"helmet": [], "no_helmet": []}
        names = getattr(model, "names", {})
        rcx = (region[0] + region[2]) / 2.0
        rcy = (region[1] + region[3]) / 2.0
        rw = max(1.0, region[2] - region[0])
        rh = max(1.0, region[3] - region[1])

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
                    if not status or confidence < self.helmet_observation_confidence:
                        continue
                    x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
                    mapped = [
                        int(region[0] + x1 / scale),
                        int(region[1] + y1 / scale),
                        int(region[0] + x2 / scale),
                        int(region[1] + y2 / scale),
                    ]
                    dcx = (mapped[0] + mapped[2]) / 2.0
                    dcy = (mapped[1] + mapped[3]) / 2.0
                    distance = abs(dcx - rcx) / rw + abs(dcy - rcy) / rh
                    candidates[status].append({
                        "status": status,
                        "confidence": confidence,
                        "box": mapped,
                        "search_box": region,
                        "source": source_name,
                        "rank": confidence - 0.10 * distance,
                    })
                except Exception:
                    continue

        best_helmet = max(
            candidates["helmet"], key=lambda row: row["rank"], default=None
        )
        best_no_helmet = max(
            candidates["no_helmet"], key=lambda row: row["rank"], default=None
        )

        if best_helmet and best_no_helmet:
            # A meaningful helmet observation remains a safety veto unless the
            # no-helmet observation is clearly stronger on the same head ROI.
            if (
                best_helmet["confidence"] >= self.helmet_observation_confidence
                and best_no_helmet["confidence"]
                < best_helmet["confidence"] + self.helmet_conflict_margin
            ):
                return best_helmet
            return best_no_helmet
        return best_helmet or best_no_helmet

    def _helmet_confirmed(self, camera_ip, bike, observation):
        key = self._track_key(camera_ip, bike)
        votes = self.helmet_votes[key]
        source = observation.get("source", "person_head")
        votes.append((observation["status"], observation["confidence"], source))

        status = observation["status"]
        same = [
            (conf, vote_source)
            for vote_status, conf, vote_source in votes
            if vote_status == status
        ]
        if not same:
            return None

        if status == "no_helmet":
            fallback_track = source == "bike_fallback"
            required = (
                self.fallback_confirm_frames
                if fallback_track
                else self.strict_no_helmet_confirm_frames
            )
            required_ratio = (
                self.fallback_vote_ratio if fallback_track else self.helmet_vote_ratio
            )
            final_average_threshold = (
                self.fallback_final_avg_confidence
                if fallback_track
                else self.no_helmet_final_avg_confidence
            )
            strong_frame_threshold = (
                self.fallback_no_helmet_confidence
                if fallback_track
                else self.strict_no_helmet_confidence
            )

            if len(votes) < required or len(same) < required:
                return None
            if len(same) / max(1, len(votes)) < required_ratio:
                return None

            # Any clear helmet observation on the same recent track vetoes a
            # violation. Weak/conflicting tracks remain visible as '?' only.
            strong_helmet = [
                conf
                for vote_status, conf, _ in votes
                if vote_status == "helmet"
                and conf >= float(self.cfg.HELMET_CONFIDENCE) + 0.08
            ]
            if strong_helmet:
                return None

            scores = [conf for conf, _ in same]
            average = sum(scores) / len(scores)
            if average < final_average_threshold:
                return None

            # For a fast 3-frame decision require at least one strong frame.
            # Otherwise allow four consistent moderate observations to confirm.
            strong_frames = [score for score in scores if score >= strong_frame_threshold]
            if not strong_frames and len(same) < max(4, required):
                return None
        else:
            required = max(2, int(self.cfg.HELMET_CONFIRM_FRAMES))
            if len(same) < required:
                return None
            scores = [conf for conf, _ in same]
            average = sum(scores) / len(scores)
            if average < float(self.cfg.HELMET_CONFIDENCE):
                return None

        counts = Counter(vote_status for vote_status, _, _ in votes)
        if counts.most_common(1)[0][0] != status:
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
        _, rw = road.shape[:2]
        columns = self.road_tile_columns
        if columns <= 1 or rw < 900:
            return [(road, 0, y_offset)]
        tile_width = int(math.ceil(rw / columns))
        overlap = int(tile_width * self.road_tile_overlap)
        tiles = []
        for index in range(columns):
            x1 = max(0, index * tile_width - (overlap if index else 0))
            x2 = min(
                rw,
                (index + 1) * tile_width
                + (overlap if index < columns - 1 else 0),
            )
            tile = road[:, x1:x2]
            if tile.size:
                tiles.append((tile, x1, y_offset))
        return tiles

    def _run_road_model(self, camera_ip, frame, model_key, event_type, draw_frame=None):
        model = self.models.get(model_key)
        if model is None:
            return []

        store_threshold = (
            float(self.cfg.ROAD_DAMAGE_CONFIDENCE)
            if model_key == "road_damage"
            else float(self.cfg.ADVANCED_CONFIDENCE)
        )
        infer_threshold = (
            self.road_display_confidence
            if model_key == "road_damage"
            else store_threshold
        )
        detections = []
        for source, x_offset, y_offset in self._road_tiles(frame):
            try:
                results = model.predict(
                    source,
                    conf=infer_threshold,
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
                        full_box = [
                            x1 + x_offset,
                            y1 + y_offset,
                            x2 + x_offset,
                            y2 + y_offset,
                        ]
                        label = str(
                            names.get(cls_id, cls_id)
                            if isinstance(names, dict)
                            else names[cls_id]
                        )
                    except Exception:
                        continue
                    detections.append({
                        "label": label,
                        "confidence": confidence,
                        "box": full_box,
                    })

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
            confirmed = confidence >= store_threshold
            if draw_frame is not None:
                prefix = "ROAD" if confirmed else "ROAD?"
                self._draw(
                    draw_frame,
                    full_box,
                    f"{prefix} {label.upper()} {confidence * 100:.0f}%",
                    (0, 165, 255) if confirmed else (0, 210, 255),
                )

            if not confirmed or self.store is None:
                continue
            cx = (full_box[0] + full_box[2]) // 2
            cy = (full_box[1] + full_box[3]) // 2
            spatial_key = (
                camera_ip,
                event_type,
                label.lower(),
                int(cx / 160),
                int(cy / 120),
            )
            now = time.time()
            if (
                now - self.last_road_event[spatial_key]
                < self.cfg.ROAD_EVENT_COOLDOWN_SECONDS
            ):
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
