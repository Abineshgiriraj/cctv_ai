import os
import re
import time
from collections import Counter, defaultdict, deque

import cv2


class AdvancedDetector:
    """Helmet, motorcycle plate and road-damage detection on top of YOLO tracking."""

    def __init__(self, config, store, session_id, log):
        self.cfg = config
        self.store = store
        self.session_id = session_id
        self.log = log
        self.models = {}
        self.ocr = None
        self.last_violation = defaultdict(float)
        self.last_road_event = defaultdict(float)
        self.helmet_votes = defaultdict(lambda: deque(maxlen=self.cfg.HELMET_CONFIRM_WINDOW))
        self.plate_votes = defaultdict(lambda: deque(maxlen=self.cfg.PLATE_CONFIRM_WINDOW))
        self.plate_cache = {}
        self._load_models()
        self._load_ocr()

    def _load_models(self):
        try:
            from ultralytics import YOLO
        except Exception as exc:
            self.log.warning("Advanced detectors unavailable: %s", exc)
            return

        for key, path in {
            "helmet": self.cfg.HELMET_MODEL,
            "plate": self.cfg.PLATE_MODEL,
            "road_damage": self.cfg.ROAD_DAMAGE_MODEL,
            "road_obstruction": self.cfg.ROAD_OBSTRUCTION_MODEL,
        }.items():
            if not path or not os.path.isfile(path):
                self.log.warning("Advanced model missing name=%s path=%s", key, path)
                continue
            try:
                model = YOLO(path)
                self.models[key] = model
                self.log.info(
                    "Advanced model loaded name=%s path=%s classes=%s",
                    key,
                    path,
                    getattr(model, "names", {}),
                )
            except Exception as exc:
                self.log.warning("Unable to load %s model: %s", key, exc)

    def _load_ocr(self):
        if not self.cfg.OCR_ENABLED:
            return
        try:
            import easyocr
            self.ocr = easyocr.Reader(["en"], gpu=False, verbose=False)
            self.log.info("EasyOCR ready for number-plate reading")
        except Exception as exc:
            self.log.warning("OCR disabled: %s", exc)

    @staticmethod
    def readiness(config):
        return {
            name: {"configured_path": path, "available": bool(path and os.path.isfile(path))}
            for name, path in {
                "helmet": config.HELMET_MODEL,
                "plate": config.PLATE_MODEL,
                "road_damage": config.ROAD_DAMAGE_MODEL,
                "road_obstruction": config.ROAD_OBSTRUCTION_MODEL,
            }.items()
        }

    def runtime_readiness(self):
        result = self.readiness(self.cfg)
        for name, row in result.items():
            model = self.models.get(name)
            row["loaded"] = model is not None
            row["classes"] = getattr(model, "names", {}) if model is not None else {}
        result["ocr"] = {
            "available": self.ocr is not None,
            "loaded": self.ocr is not None,
            "engine": "EasyOCR",
        }
        return result

    @staticmethod
    def _jpeg(frame):
        if frame is None or frame.size == 0:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        return buf.tobytes() if ok else None

    @staticmethod
    def _crop(frame, box, pad=0):
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in box]
        x1 = max(0, x1 - pad)
        y1 = max(0, y1 - pad)
        x2 = min(w, x2 + pad)
        y2 = min(h, y2 + pad)
        return frame[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else None

    @staticmethod
    def _track_key(camera, obj, divisor=64):
        track_id = obj.get("track_id")
        if track_id is not None:
            return camera["camera_key"], int(track_id)
        x1, y1, _, _ = obj["box"]
        return camera["camera_key"], int(x1 / divisor), int(y1 / divisor)

    @staticmethod
    def _primary_objects(result, model):
        persons, bikes = [], []
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return persons, bikes
        for box in boxes:
            try:
                cls_id = int(box.cls[0].item())
                conf = float(box.conf[0].item())
                coords = [int(v) for v in box.xyxy[0].tolist()]
                track_id = int(box.id[0].item()) if box.id is not None else None
            except Exception:
                continue
            row = {"box": coords, "conf": conf, "track_id": track_id}
            if cls_id == 0:
                persons.append(row)
            elif cls_id == 3:
                bikes.append(row)
        return persons, bikes

    @staticmethod
    def _primary_vehicles(result):
        """Return all road-vehicle objects so plate OCR is not motorcycle-only."""
        vehicles = []
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return vehicles
        for box in boxes:
            try:
                cls_id = int(box.cls[0].item())
                if cls_id not in {1, 2, 3, 5, 7}:
                    continue
                conf = float(box.conf[0].item())
                coords = [int(v) for v in box.xyxy[0].tolist()]
                track_id = int(box.id[0].item()) if box.id is not None else None
            except Exception:
                continue
            vehicles.append({
                "box": coords,
                "conf": conf,
                "track_id": track_id,
                "cls_id": cls_id,
            })
        return vehicles

    @staticmethod
    def _rider_score(person_box, bike_box):
        px1, py1, px2, py2 = person_box
        bx1, by1, bx2, by2 = bike_box
        pcx = (px1 + px2) / 2.0
        pbottom = py2
        bcx = (bx1 + bx2) / 2.0
        bw = max(1.0, bx2 - bx1)
        bh = max(1.0, by2 - by1)
        if not (bx1 - bw * 0.75 <= pcx <= bx2 + bw * 0.75):
            return None
        if not (by1 - bh * 2.7 <= pbottom <= by2 + bh * 0.65):
            return None
        horizontal = abs(pcx - bcx) / bw
        vertical = abs(pbottom - by1) / max(1.0, bh * 2.0)
        size_bonus = min(0.35, max(0.0, (py2 - py1) / max(1.0, bh) * 0.04))
        return horizontal + vertical - size_bonus

    def _best_rider(self, persons, bike):
        candidates = []
        for person in persons:
            score = self._rider_score(person["box"], bike["box"])
            if score is not None:
                candidates.append((score, person))
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    @staticmethod
    def _helmet_label(name):
        normalized = str(name).strip().lower().replace("-", "_").replace(" ", "_")
        if any(term in normalized for term in (
            "no_helmet", "nohelmet", "without_helmet", "withouthelmet",
            "helmetless", "barehead", "bare_head", "nohardhat", "no_hardhat",
        )):
            return "no_helmet"
        if "helmet" in normalized or "hardhat" in normalized:
            return "helmet"
        return None

    def _helmet_threshold(self, status):
        return self.cfg.NO_HELMET_CONFIDENCE if status == "no_helmet" else self.cfg.HELMET_CONFIDENCE

    @staticmethod
    def _rider_region(frame, bike_box, person_box=None):
        h, w = frame.shape[:2]
        bx1, by1, bx2, by2 = bike_box
        bw, bh = max(1, bx2 - bx1), max(1, by2 - by1)
        if person_box:
            px1, py1, px2, py2 = person_box
            pw, ph = max(1, px2 - px1), max(1, py2 - py1)
            box = [
                px1 - int(pw * 0.35), py1 - int(ph * 0.20),
                px2 + int(pw * 0.35), py1 + int(ph * 0.72),
            ]
        else:
            box = [
                bx1 - int(bw * 0.75), by1 - int(bh * 2.65),
                bx2 + int(bw * 0.75), by1 + int(bh * 0.65),
            ]
        return [max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3])]

    def _helmet_status(self, frame, bike, person):
        model = self.models.get("helmet")
        if model is None:
            return None
        region = self._rider_region(frame, bike["box"], person["box"] if person else None)
        crop = self._crop(frame, region, 5)
        if crop is None or crop.size == 0:
            return None
        ch, cw = crop.shape[:2]
        scale = 3.0 if max(ch, cw) < 180 else 2.0 if max(ch, cw) < 360 else 1.0
        source = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale > 1 else crop
        try:
            results = model.predict(
                source,
                conf=min(self.cfg.HELMET_CONFIDENCE, self.cfg.NO_HELMET_CONFIDENCE),
                imgsz=self.cfg.HELMET_IMGSZ,
                verbose=False,
            )
        except Exception as exc:
            self.log.debug("Helmet inference failed: %s", exc)
            return None

        found = []
        for result in results or []:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                try:
                    cls_id = int(box.cls[0].item())
                    confidence = float(box.conf[0].item())
                    names = getattr(model, "names", {})
                    name = names.get(cls_id, cls_id) if isinstance(names, dict) else names[cls_id]
                    status = self._helmet_label(name)
                    if not status or confidence < self._helmet_threshold(status):
                        continue
                    x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
                    found.append({
                        "status": status,
                        "confidence": confidence,
                        "box": [
                            int(region[0] + x1 / scale), int(region[1] + y1 / scale),
                            int(region[0] + x2 / scale), int(region[1] + y2 / scale),
                        ],
                        "search_box": region,
                    })
                except Exception:
                    continue
        if not found:
            return None
        found.sort(key=lambda item: item["confidence"] + (0.08 if item["status"] == "helmet" else 0.0), reverse=True)
        return found[0]

    def _helmet_confirmed(self, camera, bike, observation):
        key = self._track_key(camera, bike)
        votes = self.helmet_votes[key]
        votes.append((observation["status"], observation["confidence"]))
        same = [conf for status, conf in votes if status == observation["status"]]
        if len(same) < self.cfg.HELMET_CONFIRM_FRAMES:
            return None
        counts = Counter(status for status, _ in votes)
        if counts.most_common(1)[0][0] != observation["status"]:
            return None
        average = sum(same) / len(same)
        if average < self._helmet_threshold(observation["status"]):
            return None
        return observation["status"], average, len(same), len(votes)

    @staticmethod
    def _draw(frame, box, label, color, thickness=2):
        x1, y1, x2, y2 = [int(v) for v in box]
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)
        cv2.putText(frame, label, (max(3, x1), max(22, y1 - 7)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.58, color, 2, cv2.LINE_AA)

    def _detect_plates_frame(self, frame):
        model = self.models.get("plate")
        if model is None:
            return []
        try:
            results = model.predict(frame, conf=self.cfg.PLATE_CONFIDENCE,
                                    imgsz=self.cfg.PLATE_IMGSZ, verbose=False)
        except Exception as exc:
            self.log.debug("Plate detector failed: %s", exc)
            return []
        detections = []
        for result in results or []:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                try:
                    confidence = float(box.conf[0].item())
                    coords = [int(v) for v in box.xyxy[0].tolist()]
                except Exception:
                    continue
                crop = self._crop(frame, coords, 4)
                if crop is not None and crop.size:
                    detections.append({"confidence": confidence, "box": coords, "crop": crop})
        return detections

    @staticmethod
    def _match_plate_to_vehicle(vehicle_box, plate_detections):
        vx1, vy1, vx2, vy2 = vehicle_box
        vw, vh = max(1.0, vx2 - vx1), max(1.0, vy2 - vy1)
        vcx, vcy = (vx1 + vx2) / 2.0, (vy1 + vy2) / 2.0
        candidates = []
        for detection in plate_detections:
            px1, py1, px2, py2 = detection["box"]
            pcx, pcy = (px1 + px2) / 2.0, (py1 + py2) / 2.0
            # Plates can sit slightly outside a detector's vehicle box at distance,
            # but should remain close to the vehicle and normally in its lower area.
            if not (vx1 - vw * 0.20 <= pcx <= vx2 + vw * 0.20):
                continue
            if not (vy1 + vh * 0.15 <= pcy <= vy2 + vh * 0.20):
                continue
            score = (
                abs(pcx - vcx) / vw
                + abs(pcy - (vy1 + vh * 0.72)) / vh
                - float(detection["confidence"]) * 0.35
            )
            candidates.append((score, detection))
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    @staticmethod
    def _match_plate_to_bike(bike_box, plate_detections):
        return AdvancedDetector._match_plate_to_vehicle(bike_box, plate_detections)

    @staticmethod
    def _plate_variants(crop):
        if crop is None or crop.size == 0:
            return []
        width = crop.shape[1]
        scale = min(6.0, max(2.0, 360.0 / max(1, width)))
        enlarged = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(2.5, (8, 8)).apply(gray)
        blur = cv2.GaussianBlur(clahe, (0, 0), 1.0)
        sharp = cv2.addWeighted(clahe, 1.9, blur, -0.9, 0)
        _, otsu = cv2.threshold(sharp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        adaptive = cv2.adaptiveThreshold(sharp, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                         cv2.THRESH_BINARY, 31, 7)
        return [enlarged, clahe, sharp, otsu, adaptive]

    @staticmethod
    def _clean_plate_text(raw):
        return re.sub(r"[^A-Z0-9]", "", str(raw).upper())

    @staticmethod
    def _plate_shape_bonus(text):
        bonus = 0.18 if text.startswith("TN") else 0.0
        if re.match(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}$", text):
            bonus += 0.40
        return bonus

    def _ocr_plate(self, crop):
        if self.ocr is None:
            return None, None
        best = None
        for image in self._plate_variants(crop):
            try:
                reads = self.ocr.readtext(
                    image, detail=1, paragraph=False,
                    allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
                )
            except Exception:
                continue
            for _, raw, score in reads or []:
                text = self._clean_plate_text(raw)
                score = float(score)
                if not 6 <= len(text) <= 12 or score < self.cfg.PLATE_OCR_MIN_CONFIDENCE:
                    continue
                rank = score + self._plate_shape_bonus(text)
                if best is None or rank > best[0]:
                    best = (rank, text, score)
        return (best[1], best[2]) if best else (None, None)

    def _plate_consensus(self, camera, bike, detection):
        key = self._track_key(camera, bike, divisor=80)
        now = time.time()
        cached = self.plate_cache.get(key)
        if detection is None:
            if cached and now - cached["updated_at"] < self.cfg.PLATE_CACHE_SECONDS:
                return cached
            return None

        text, ocr_confidence = self._ocr_plate(detection["crop"])
        observation = {
            **detection,
            "raw_text": text,
            "raw_ocr_confidence": ocr_confidence,
            "text": None,
            "ocr_confidence": None,
            "confirmed": False,
            "updated_at": now,
        }
        if text:
            votes = self.plate_votes[key]
            votes.append((text, float(ocr_confidence or 0.0)))
            counts = Counter(value for value, _ in votes)
            candidate, count = counts.most_common(1)[0]
            scores = [score for value, score in votes if value == candidate]
            if count >= self.cfg.PLATE_CONFIRM_READS:
                observation["text"] = candidate
                observation["ocr_confidence"] = sum(scores) / len(scores)
                observation["confirmed"] = True

        if observation["confirmed"]:
            self.plate_cache[key] = observation
            return observation
        if cached and cached.get("confirmed") and now - cached["updated_at"] < self.cfg.PLATE_CACHE_SECONDS:
            kept = dict(cached)
            kept["box"] = detection["box"]
            return kept
        self.plate_cache[key] = observation
        return observation

    def _store_no_helmet(self, camera, frame, bike, person, confirmation, region, plate):
        if self.store is None:
            return None
        status, confidence, vote_count, vote_window = confirmation
        person_track_id = person.get("track_id") if person else None
        key = (camera["camera_key"], bike.get("track_id"), person_track_id if person_track_id is not None else -1)
        now = time.time()
        if now - self.last_violation[key] < self.cfg.VIOLATION_COOLDOWN_SECONDS:
            return None
        self.last_violation[key] = now

        if person:
            pb, bb = person["box"], bike["box"]
            union = [min(bb[0], pb[0]), min(bb[1], pb[1]), max(bb[2], pb[2]), max(bb[3], pb[3])]
        else:
            bb = bike["box"]
            union = [min(bb[0], region[0]), min(bb[1], region[1]),
                     max(bb[2], region[2]), max(bb[3], region[3])]
        evidence = self._crop(frame, union, 40)
        plate_confirmed = bool(plate and plate.get("confirmed"))
        return self.store.record_violation(
            session_id=self.session_id,
            camera=camera,
            violation_type="no_helmet",
            vehicle_type="motorcycle",
            vehicle_track_id=bike.get("track_id"),
            person_track_id=person_track_id,
            helmet_status=status,
            plate_number=plate.get("text") if plate_confirmed else None,
            plate_confidence=plate.get("ocr_confidence") if plate_confirmed else None,
            detection_confidence=confidence,
            evidence_image=self._jpeg(evidence if evidence is not None else frame),
            plate_image=self._jpeg(plate.get("crop") if plate else None),
            metadata={
                "bike_confidence": bike.get("conf"),
                "helmet_vote_count": vote_count,
                "helmet_vote_window": vote_window,
                "plate_detected": bool(plate),
                "plate_confirmed": plate_confirmed,
                "plate_raw_text": plate.get("raw_text") if plate else None,
                "plate_ocr_text": plate.get("text") if plate_confirmed else None,
            },
        )

    def _road_roi(self, frame):
        h, w = frame.shape[:2]
        top = int(h * self.cfg.ROAD_ROI_TOP_RATIO)
        top = max(0, min(h - 1, top))
        return frame[top:h, 0:w], top

    def _run_road_model(self, camera, frame, model_key, event_type, draw_frame=None):
        model = self.models.get(model_key)
        if model is None:
            return []
        source, y_offset = self._road_roi(frame)
        confidence_threshold = self.cfg.ROAD_DAMAGE_CONFIDENCE if model_key == "road_damage" else self.cfg.ADVANCED_CONFIDENCE
        try:
            results = model.predict(
                source, conf=confidence_threshold,
                imgsz=self.cfg.ROAD_DAMAGE_IMGSZ, verbose=False,
            )
        except Exception as exc:
            self.log.debug("%s inference failed: %s", model_key, exc)
            return []

        events = []
        for result in results or []:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                try:
                    cls_id = int(box.cls[0].item())
                    confidence = float(box.conf[0].item())
                    x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                    full_box = [x1, y1 + y_offset, x2, y2 + y_offset]
                    names = getattr(model, "names", {})
                    label = str(names.get(cls_id, cls_id) if isinstance(names, dict) else names[cls_id])
                except Exception:
                    continue

                if draw_frame is not None:
                    self._draw(draw_frame, full_box,
                               f"ROAD {label.upper()} {confidence * 100:.0f}%", (0, 165, 255))
                if self.store is None:
                    continue

                cx = (full_box[0] + full_box[2]) // 2
                cy = (full_box[1] + full_box[3]) // 2
                spatial_key = (camera["camera_key"], event_type, label.lower(), int(cx / 160), int(cy / 120))
                now = time.time()
                if now - self.last_road_event[spatial_key] < self.cfg.ROAD_EVENT_COOLDOWN_SECONDS:
                    continue
                self.last_road_event[spatial_key] = now
                crop = self._crop(frame, full_box, 30)
                event_id = self.store.record_road_event(
                    session_id=self.session_id,
                    camera=camera,
                    event_type=event_type,
                    model_label=label,
                    confidence=confidence,
                    evidence_image=self._jpeg(crop if crop is not None else frame),
                    metadata={"box": full_box, "road_roi_top_ratio": self.cfg.ROAD_ROI_TOP_RATIO},
                )
                events.append({"id": event_id, "label": label,
                               "confidence": confidence, "box": full_box})
        return events

    def process(self, camera, clean_frame, primary_result, primary_model,
                processed_index, draw_frame=None):
        draw_frame = draw_frame if draw_frame is not None else clean_frame
        summary = {
            "helmet_checked": 0,
            "helmet_detected": 0,
            "no_helmet_detected": 0,
            "helmet_violations": 0,
            "plate_detected": 0,
            "plate_read": 0,
            "road_events": 0,
        }
        if not self.cfg.ADVANCED_DETECTION_ENABLED or primary_result is None:
            return summary

        run_rider_ai = processed_index % self.cfg.ADVANCED_EVERY_N_FRAMES == 0
        run_road_ai = processed_index % self.cfg.ROAD_EVERY_N_FRAMES == 0

        if run_rider_ai:
            persons, bikes = self._primary_objects(primary_result, primary_model)
            plate_detections = self._detect_plates_frame(clean_frame) if "plate" in self.models and bikes else []

            for bike in bikes:
                rider = self._best_rider(persons, bike)
                matched_plate = self._match_plate_to_bike(bike["box"], plate_detections)
                plate = self._plate_consensus(camera, bike, matched_plate) if matched_plate else None
                if matched_plate:
                    summary["plate_detected"] += 1
                if plate:
                    label = "PLATE"
                    if plate.get("confirmed") and plate.get("text"):
                        label += f" {plate['text']}"
                        summary["plate_read"] += 1
                    elif plate.get("raw_text"):
                        label += f" ? {plate['raw_text']}"
                    else:
                        label += " ?"
                    self._draw(draw_frame, plate["box"], label, (255, 160, 0))

                observation = self._helmet_status(clean_frame, bike, rider)
                if not observation:
                    continue
                summary["helmet_checked"] += 1
                confirmation = self._helmet_confirmed(camera, bike, observation)
                status, confidence = observation["status"], observation["confidence"]
                suffix = " ?"
                if confirmation:
                    status, confidence, _, _ = confirmation
                    suffix = ""
                color = (0, 0, 255) if status == "no_helmet" else (0, 220, 80)
                label = "NO HELMET" if status == "no_helmet" else "HELMET"
                self._draw(draw_frame, observation["box"],
                           f"{label}{suffix} {confidence * 100:.0f}%", color)
                if not confirmation:
                    continue
                if status == "helmet":
                    summary["helmet_detected"] += 1
                    continue
                summary["no_helmet_detected"] += 1
                if self._store_no_helmet(camera, clean_frame, bike, rider,
                                         confirmation, observation["search_box"], plate):
                    summary["helmet_violations"] += 1

        if run_road_ai:
            summary["road_events"] += len(self._run_road_model(
                camera, clean_frame, "road_damage", "road_damage", draw_frame=draw_frame))
            summary["road_events"] += len(self._run_road_model(
                camera, clean_frame, "road_obstruction", "road_obstruction", draw_frame=draw_frame))

        return summary
