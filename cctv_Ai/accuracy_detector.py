import math
import os
import time
from collections import Counter, defaultdict, deque
import cv2

from advanced_detection import AdvancedDetector


class AccuracyDetector(AdvancedDetector):
    def __init__(self, config, store, session_id, log):
        super().__init__(config, store, session_id, log)

        self.helmet_observation_confidence = float(os.getenv("HELMET_OBSERVATION_CONFIDENCE", "0.15"))
        self.no_helmet_final_avg_confidence = float(os.getenv("NO_HELMET_FINAL_AVG_CONFIDENCE", "0.20"))
        self.strict_no_helmet_vote_ratio = float(os.getenv("STRICT_NO_HELMET_VOTE_RATIO", "0.50"))
        
        self.helmet_confirm_frames = int(os.getenv("HELMET_CONFIRM_FRAMES", "2"))
        self.helmet_confirm_window = int(os.getenv("HELMET_CONFIRM_WINDOW", "4"))
        
        self.helmet_conflict_margin = float(os.getenv("HELMET_CONFLICT_MARGIN", "0.12"))
        self.helmet_head_bounds_required = os.getenv(
            "HELMET_HEAD_BOUNDS_REQUIRED", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}

        self.road_tile_overlap = float(os.getenv("ROAD_TILE_OVERLAP", "0.18"))
        self.road_tile_columns = max(1, min(3, int(os.getenv("ROAD_TILE_COLUMNS", "2"))))
        self.road_display_confidence = float(os.getenv("ROAD_DISPLAY_CONFIDENCE", "0.12"))
        
        self._bike_motion_state = {}
        self.helmet_tile_min_bike_motion_px = float(os.getenv("HELMET_TILE_MIN_BIKE_MOTION_PX", "0"))
        
        self.helmet_votes = defaultdict(lambda: deque(maxlen=self.helmet_confirm_window))
        self.log.info("AccuracyDetector initialized with multi-frame tracking logic.")

    def _bike_motion_ok(self, camera, bike):
        key = self._track_key(camera, bike)
        x1, y1, x2, y2 = bike["box"]
        center = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
        state = self._bike_motion_state.get(key)
        if state is None:
            self._bike_motion_state[key] = {
                "origin": center,
                "last": center,
                "max_displacement": 0.0,
            }
            return self.helmet_tile_min_bike_motion_px <= 0

        ox, oy = state["origin"]
        displacement = math.hypot(center[0] - ox, center[1] - oy)
        state["last"] = center
        state["max_displacement"] = max(state["max_displacement"], displacement)
        return state["max_displacement"] >= self.helmet_tile_min_bike_motion_px

    @staticmethod
    def _expanded_bike_region(frame, bike_box, person_box=None):
        h, w = frame.shape[:2]
        bx1, by1, bx2, by2 = [int(v) for v in bike_box]
        if person_box:
            px1, py1, px2, py2 = [int(v) for v in person_box]
            bx1 = min(bx1, px1)
            by1 = min(by1, py1)
            bx2 = max(bx2, px2)
            by2 = max(by2, py2)
        bw = max(1, bx2 - bx1)
        bh = max(1, by2 - by1)
        pad_x = int(bw * 0.40)
        pad_top = int(bh * 0.50)
        pad_bottom = int(bh * 0.20)
        return [max(0, bx1 - pad_x), max(0, by1 - pad_top), min(w, bx2 + pad_x), min(h, by2 + pad_bottom)]

    @staticmethod
    def _is_head_in_bounds(head_box, bike_box, person_box):
        hx1, hy1, hx2, hy2 = head_box
        hcx = (hx1 + hx2) / 2.0
        hcy = (hy1 + hy2) / 2.0
        
        # We check if the center of the head box falls reasonably within the top part of the person or bike
        if person_box:
            px1, py1, px2, py2 = person_box
            pw = px2 - px1
            if (px1 - pw*0.3) <= hcx <= (px2 + pw*0.3) and (py1 - 50) <= hcy <= py2:
                return True
        
        bx1, by1, bx2, by2 = bike_box
        bw = bx2 - bx1
        bh = by2 - by1
        # Allow heads to be above the bike bounding box (by up to 1.5x bike height) and horizontally within it
        if (bx1 - bw*0.5) <= hcx <= (bx2 + bw*0.5) and (by1 - bh*1.5) <= hcy <= by2:
            return True
            
        return False

    def _helmet_status(self, camera, frame, bike, person):
        model = self.models.get("helmet")
        if model is None:
            return None
            
        if not self._bike_motion_ok(camera, bike):
            return None

        bike_box = bike.get("box")
        person_box = person.get("box") if person else None
        region = self._expanded_bike_region(frame, bike_box, person_box)
        source_name = "bike_person_combo"

        if region is None:
            return None
        crop = self._crop(frame, region, 4)
        if crop is None or crop.size == 0:
            return None

        ch, cw = crop.shape[:2]
        scale = 3.0 if max(ch, cw) < 180 else 2.0 if max(ch, cw) < 360 else 1.0
        source = (
            cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            if scale > 1.0 else crop
        )

        try:
            results = model.predict(
                source,
                conf=self.helmet_observation_confidence,
                imgsz=self.cfg.HELMET_IMGSZ,
                verbose=False,
            )
        except Exception as exc:
            self.log.error(f"Helmet predict error: {exc}")
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
                    
                    if not status or confidence < self.helmet_observation_confidence:
                        continue
                        
                    x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
                    mapped = [
                        int(region[0] + x1 / scale), int(region[1] + y1 / scale),
                        int(region[0] + x2 / scale), int(region[1] + y2 / scale)
                    ]
                    
                    if (
                        self.helmet_head_bounds_required
                        and not self._is_head_in_bounds(mapped, bike_box, person_box)
                    ):
                        self.log.debug(
                            "Rejected out-of-bounds helmet detection bike=%s",
                            bike.get("track_id"),
                        )
                        continue
                        
                    self.log.info(f"Observed on bike={bike.get('track_id')}: {raw_name} -> {status} (conf={confidence:.2f})")

                    candidates[status].append({
                        "status": status,
                        "confidence": confidence,
                        "box": mapped,
                        "search_box": region,
                        "source": source_name,
                    })
                except Exception:
                    continue

        best_helmet = max(candidates["helmet"], key=lambda row: row["confidence"], default=None)
        best_no_helmet = max(candidates["no_helmet"], key=lambda row: row["confidence"], default=None)

        if best_helmet and best_no_helmet:
            if best_helmet["confidence"] >= self.helmet_observation_confidence and best_no_helmet["confidence"] < best_helmet["confidence"] + self.helmet_conflict_margin:
                return best_helmet
            return best_no_helmet
        return best_helmet or best_no_helmet

    def _helmet_confirmed(self, camera, bike, observation):
        key = self._track_key(camera, bike)
        votes = self.helmet_votes[key]
        votes.append((observation["status"], observation["confidence"]))
        
        status = observation["status"]
        same = [conf for vote_status, conf in votes if vote_status == status]
        
        if not same:
            return None

        # self.log.info(f"Bike {bike.get('track_id')} votes: {votes}")

        if status == "no_helmet":
            if len(votes) < self.helmet_confirm_frames or len(same) < self.helmet_confirm_frames:
                self.log.info(f"Bike {bike.get('track_id')} NH rejected: Not enough frames (votes={len(votes)}, same={len(same)})")
                return None
            ratio = len(same) / max(1, len(votes))
            if ratio < self.strict_no_helmet_vote_ratio:
                self.log.info(f"Bike {bike.get('track_id')} NH rejected: Ratio {ratio:.2f} < {self.strict_no_helmet_vote_ratio}")
                return None

            scores = [conf for conf in same]
            average = sum(scores) / len(scores)
            if average < self.no_helmet_final_avg_confidence:
                self.log.info(f"Bike {bike.get('track_id')} NH rejected: Avg conf {average:.2f} < {self.no_helmet_final_avg_confidence}")
                return None
                
        else:
            required = max(2, int(self.cfg.HELMET_CONFIRM_FRAMES))
            if len(same) < required:
                return None
            scores = [conf for conf in same]
            average = sum(scores) / len(scores)
            if average < float(self.cfg.HELMET_CONFIDENCE):
                return None

        counts = Counter(vote_status for vote_status, _ in votes)
        if counts.most_common(1)[0][0] != status:
            return None
            
        self.log.info(f"Bike {bike.get('track_id')} CONFIRMED {status} (avg={average:.2f})")
        return status, average, len(same), len(votes)

    @staticmethod
    def _box_iou(a, b):
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        inter = iw * ih
        if inter <= 0:
            return 0.0
        area_a = max(1, (ax2 - ax1) * (ay2 - ay1))
        area_b = max(1, (bx2 - bx1) * (by2 - by1))
        return inter / float(area_a + area_b - inter)

    def _road_sources(self, frame):
        """Return the road ROI plus overlapping horizontal tiles for distant defects."""
        roi, y_offset = self._road_roi(frame)
        sources = [(roi, 0, y_offset)]
        h, w = roi.shape[:2]
        columns = max(1, int(self.road_tile_columns))
        if columns <= 1 or w < 320:
            return sources

        tile_width = int((w + (columns - 1) * w * self.road_tile_overlap) / columns)
        step = max(1, int(tile_width * (1.0 - self.road_tile_overlap)))
        for idx in range(columns):
            x1 = min(max(0, idx * step), max(0, w - 1))
            x2 = w if idx == columns - 1 else min(w, x1 + tile_width)
            if x2 - x1 < 120:
                continue
            sources.append((roi[:, x1:x2], x1, y_offset))
        return sources

    def _run_road_model(self, camera, frame, model_key, event_type, draw_frame=None):
        model = self.models.get(model_key)
        if model is None:
            return []

        if model_key == "road_damage":
            threshold = min(
                float(self.cfg.ROAD_DAMAGE_CONFIDENCE),
                float(self.road_display_confidence),
            )
        else:
            threshold = float(self.cfg.ADVANCED_CONFIDENCE)

        candidates = []
        names = getattr(model, "names", {})
        for source, x_offset, y_offset in self._road_sources(frame):
            if source is None or source.size == 0:
                continue
            try:
                results = model.predict(
                    source,
                    conf=max(0.05, threshold),
                    imgsz=self.cfg.ROAD_DAMAGE_IMGSZ,
                    verbose=False,
                )
            except Exception as exc:
                self.log.warning("%s inference failed: %s", model_key, exc)
                continue

            for result in results or []:
                boxes = getattr(result, "boxes", None)
                if boxes is None:
                    continue
                for box in boxes:
                    try:
                        cls_id = int(box.cls[0].item())
                        confidence = float(box.conf[0].item())
                        x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                        label = str(
                            names.get(cls_id, cls_id)
                            if isinstance(names, dict)
                            else names[cls_id]
                        )
                    except Exception:
                        continue
                    candidates.append({
                        "label": label,
                        "confidence": confidence,
                        "box": [
                            x1 + x_offset,
                            y1 + y_offset,
                            x2 + x_offset,
                            y2 + y_offset,
                        ],
                    })

        # Keep the highest-confidence detection when full ROI + tile inference overlap.
        candidates.sort(key=lambda row: row["confidence"], reverse=True)
        kept = []
        for candidate in candidates:
            duplicate = any(
                candidate["label"].lower() == existing["label"].lower()
                and self._box_iou(candidate["box"], existing["box"]) >= 0.35
                for existing in kept
            )
            if not duplicate:
                kept.append(candidate)

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
                camera["camera_key"],
                event_type,
                label.lower(),
                int(cx / 120),
                int(cy / 90),
            )
            now = time.time()
            if now - self.last_road_event[spatial_key] < self.cfg.ROAD_EVENT_COOLDOWN_SECONDS:
                continue

            crop = self._crop(frame, full_box, 30)
            try:
                event_id = self.store.record_road_event(
                    session_id=self.session_id,
                    camera=camera,
                    event_type=event_type,
                    model_label=label,
                    confidence=confidence,
                    evidence_image=self._jpeg(crop if crop is not None else frame),
                    metadata={
                        "box": full_box,
                        "road_roi_top_ratio": self.cfg.ROAD_ROI_TOP_RATIO,
                        "tiled_inference": True,
                    },
                )
            except Exception as exc:
                self.log.exception(
                    "Road event storage failed camera=%s label=%s: %s",
                    camera["camera_key"], label, exc,
                )
                continue

            self.last_road_event[spatial_key] = now
            events.append({
                "id": event_id,
                "label": label,
                "confidence": confidence,
                "box": full_box,
            })

        return events

    def process(self, camera, clean_frame, primary_result, primary_model,
                processed_index, draw_frame=None):
        draw_frame = draw_frame if draw_frame is not None else clean_frame
        summary = {
            "helmet_checked": 0, "helmet_detected": 0, "no_helmet_detected": 0,
            "helmet_violations": 0, "plate_detected": 0, "plate_read": 0, "road_events": 0,
        }
        if not self.cfg.ADVANCED_DETECTION_ENABLED or primary_result is None:
            return summary

        run_rider_ai = processed_index % self.cfg.ADVANCED_EVERY_N_FRAMES == 0
        run_road_ai = processed_index % self.cfg.ROAD_EVERY_N_FRAMES == 0

        if run_rider_ai:
            persons, bikes = self._primary_objects(primary_result, primary_model)
            vehicles = self._primary_vehicles(primary_result)
            plate_detections = (
                self._detect_plates_frame(clean_frame)
                if "plate" in self.models and vehicles
                else []
            )
            plate_by_vehicle = {}

            # Number-plate detection/OCR now runs for cars, buses, trucks,
            # motorcycles and bicycles instead of motorcycles only.
            for vehicle in vehicles:
                matched_plate = self._match_plate_to_vehicle(vehicle["box"], plate_detections)
                if not matched_plate:
                    continue
                summary["plate_detected"] += 1
                plate = self._plate_consensus(camera, vehicle, matched_plate)
                if not plate:
                    continue
                plate_by_vehicle[self._track_key(camera, vehicle)] = plate

                label = "PLATE"
                if plate.get("confirmed") and plate.get("text"):
                    label += f" {plate['text']}"
                    summary["plate_read"] += 1
                elif plate.get("raw_text"):
                    label += f" ? {plate['raw_text']}"
                else:
                    label += " ?"
                self._draw(draw_frame, plate["box"], label, (255, 160, 0))

            for bike in bikes:
                rider = self._best_rider(persons, bike)
                plate = plate_by_vehicle.get(self._track_key(camera, bike))
                observation = self._helmet_status(camera, clean_frame, bike, rider)
                
                track_id = bike.get("track_id")
                if track_id is not None:
                    key = self._track_key(camera, bike)
                    votes = self.helmet_votes.get(key, [])
                    no_helmet_votes = sum(1 for v in votes if v[0] == "no_helmet")
                    helmet_votes = sum(1 for v in votes if v[0] == "helmet")
                    
                    debug_lines = [
                        f"BIKE #{track_id}",
                        f"Rider: {'YES' if rider else 'NO'}",
                        f"Obs: {len(votes)} (N:{no_helmet_votes} H:{helmet_votes})"
                    ]
                    
                    if no_helmet_votes > 0:
                        nh_confs = [v[1] for v in votes if v[0] == "no_helmet"]
                        avg = sum(nh_confs) / len(nh_confs)
                        debug_lines.append(f"No-Hel Avg: {avg*100:.0f}%")
                    
                    bx1, by1, _, _ = bike["box"]
                    dy = max(10, by1 - 70)
                    for line in debug_lines:
                        cv2.putText(draw_frame, line, (bx1, dy), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
                        dy += 15
                
                if not observation:
                    continue
                summary["helmet_checked"] += 1
                
                confirmation = self._helmet_confirmed(camera, bike, observation)
                status = observation["status"]
                confidence = observation["confidence"]
                
                if confirmation:
                    status, confidence, _, _ = confirmation
                    suffix = ""
                    if track_id is not None:
                        bx1, by1, _, _ = bike["box"]
                        cv2.putText(draw_frame, f"Final: {status.upper()}", (bx1, dy), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
                else:
                    suffix = " ?"

                color = (0, 0, 255) if status == "no_helmet" else (0, 220, 80)
                label = "NO HELMET" if status == "no_helmet" else "HELMET"
                self._draw(draw_frame, observation["box"], f"{label}{suffix} {confidence * 100:.0f}%", color)

                if not confirmation:
                    continue
                if status == "helmet":
                    summary["helmet_detected"] += 1
                    continue
                    
                summary["no_helmet_detected"] += 1
                
                stored = self._store_no_helmet(camera, clean_frame, bike, rider, confirmation, observation["search_box"], plate)
                if stored:
                    summary["helmet_violations"] += 1
                    self.log.info(f"Bike {track_id} violation STORED.")
                else:
                    self.log.info(f"Bike {track_id} violation NOT stored (cooldown or error).")

        if run_road_ai:
            summary["road_events"] += len(self._run_road_model(camera, clean_frame, "road_damage", "road_damage", draw_frame=draw_frame))
            summary["road_events"] += len(self._run_road_model(camera, clean_frame, "road_obstruction", "road_obstruction", draw_frame=draw_frame))

        return summary
