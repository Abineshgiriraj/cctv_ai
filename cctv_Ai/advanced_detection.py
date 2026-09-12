import os
import re
import time
from collections import defaultdict

import cv2


class AdvancedDetector:
    """Optional advanced detections layered on top of the existing tracked frame.

    Custom model files are required for helmet, plate, road damage and road obstruction.
    Missing files simply disable that detector; the normal vehicle/person tracker still works.
    """

    def __init__(self, config, store, session_id, log):
        self.cfg = config
        self.store = store
        self.session_id = session_id
        self.log = log
        self.models = {}
        self.ocr = None
        self.last_violation = defaultdict(float)
        self.last_road_event = defaultdict(float)
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
            if path and os.path.isfile(path):
                try:
                    self.models[key] = YOLO(path)
                    self.log.info("Advanced model loaded name=%s path=%s", key, path)
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
            "helmet": {"configured_path": config.HELMET_MODEL, "available": os.path.isfile(config.HELMET_MODEL)},
            "plate": {"configured_path": config.PLATE_MODEL, "available": os.path.isfile(config.PLATE_MODEL)},
            "road_damage": {"configured_path": config.ROAD_DAMAGE_MODEL, "available": os.path.isfile(config.ROAD_DAMAGE_MODEL)},
            "road_obstruction": {"configured_path": config.ROAD_OBSTRUCTION_MODEL, "available": os.path.isfile(config.ROAD_OBSTRUCTION_MODEL)},
        }

    @staticmethod
    def _jpeg(frame):
        if frame is None or frame.size == 0:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        return buf.tobytes() if ok else None

    @staticmethod
    def _safe_crop(frame, box, pad=0):
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in box]
        x1 = max(0, x1 - pad); y1 = max(0, y1 - pad)
        x2 = min(w, x2 + pad); y2 = min(h, y2 + pad)
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2]

    @staticmethod
    def _overlap_person_with_bike(person_box, bike_box):
        px1, py1, px2, py2 = person_box
        bx1, by1, bx2, by2 = bike_box
        pcx = (px1 + px2) / 2
        pbottom = py2
        expanded_x1 = bx1 - (bx2 - bx1) * 0.45
        expanded_x2 = bx2 + (bx2 - bx1) * 0.45
        expanded_y1 = by1 - (by2 - by1) * 1.8
        expanded_y2 = by2 + (by2 - by1) * 0.35
        return expanded_x1 <= pcx <= expanded_x2 and expanded_y1 <= pbottom <= expanded_y2

    def _primary_objects(self, result, model):
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
            if cls_id == 0:
                persons.append({"box": coords, "conf": conf, "track_id": track_id})
            elif cls_id == 3:
                bikes.append({"box": coords, "conf": conf, "track_id": track_id})
        return persons, bikes

    def _helmet_status(self, frame, person):
        model = self.models.get("helmet")
        if not model:
            return None
        x1, y1, x2, y2 = person["box"]
        head_box = [x1, y1, x2, y1 + max(10, int((y2 - y1) * 0.42))]
        crop = self._safe_crop(frame, head_box, pad=8)
        if crop is None:
            return None
        try:
            results = model.predict(crop, conf=self.cfg.ADVANCED_CONFIDENCE, verbose=False)
        except Exception:
            return None
        best = None
        for res in results or []:
            boxes = getattr(res, "boxes", None)
            if boxes is None:
                continue
            for b in boxes:
                try:
                    cls_id = int(b.cls[0].item())
                    conf = float(b.conf[0].item())
                    name = str(model.names.get(cls_id, cls_id)).lower().replace("-", "_").replace(" ", "_")
                except Exception:
                    continue
                status = "no_helmet" if any(k in name for k in ("no_helmet", "nohelmet", "without_helmet")) else "helmet" if "helmet" in name else None
                if status and (best is None or conf > best[1]):
                    best = (status, conf)
        return best

    def _plate_read(self, frame, vehicle_box):
        model = self.models.get("plate")
        if not model:
            return None, None, None
        crop = self._safe_crop(frame, vehicle_box, pad=12)
        if crop is None:
            return None, None, None
        try:
            results = model.predict(crop, conf=self.cfg.ADVANCED_CONFIDENCE, verbose=False)
        except Exception:
            return None, None, None
        best_crop, best_conf = None, 0.0
        for res in results or []:
            boxes = getattr(res, "boxes", None)
            if boxes is None:
                continue
            for b in boxes:
                try:
                    conf = float(b.conf[0].item())
                    pbox = [int(v) for v in b.xyxy[0].tolist()]
                except Exception:
                    continue
                pcrop = self._safe_crop(crop, pbox, pad=4)
                if pcrop is not None and conf > best_conf:
                    best_crop, best_conf = pcrop, conf
        if best_crop is None:
            return None, None, None
        text = None
        if self.ocr is not None:
            try:
                reads = self.ocr.readtext(best_crop, detail=1, paragraph=False)
                candidates = []
                for _, raw, score in reads:
                    cleaned = re.sub(r"[^A-Z0-9]", "", raw.upper())
                    if 6 <= len(cleaned) <= 12:
                        candidates.append((cleaned, float(score)))
                if candidates:
                    text, ocr_conf = max(candidates, key=lambda x: x[1])
                    return text, min(best_conf, ocr_conf), best_crop
            except Exception:
                pass
        return None, best_conf, best_crop

    def _store_no_helmet(self, camera_ip, frame, bike, person, status_conf):
        if self.store is None:
            return None
        key = (camera_ip, bike.get("track_id"), person.get("track_id"))
        now = time.time()
        if now - self.last_violation[key] < self.cfg.VIOLATION_COOLDOWN_SECONDS:
            return None
        self.last_violation[key] = now

        x1 = min(bike["box"][0], person["box"][0]); y1 = min(bike["box"][1], person["box"][1])
        x2 = max(bike["box"][2], person["box"][2]); y2 = max(bike["box"][3], person["box"][3])
        union = [x1, y1, x2, y2]
        evidence = self._safe_crop(frame, union, pad=35)
        plate_number, plate_conf, plate_crop = self._plate_read(frame, bike["box"])
        return self.store.record_violation(
            session_id=self.session_id,
            camera_ip=camera_ip,
            violation_type="no_helmet",
            vehicle_type="motorcycle",
            vehicle_track_id=bike.get("track_id"),
            person_track_id=person.get("track_id"),
            helmet_status="no_helmet",
            plate_number=plate_number,
            plate_confidence=plate_conf,
            detection_confidence=status_conf,
            evidence_image=self._jpeg(evidence if evidence is not None else frame),
            plate_image=self._jpeg(plate_crop),
            metadata={"bike_confidence": bike.get("conf"), "person_confidence": person.get("conf")},
        )

    def _run_road_model(self, camera_ip, frame, model_key, event_type):
        model = self.models.get(model_key)
        if not model or self.store is None:
            return []
        try:
            results = model.predict(frame, conf=self.cfg.ADVANCED_CONFIDENCE, verbose=False)
        except Exception:
            return []
        events = []
        for res in results or []:
            boxes = getattr(res, "boxes", None)
            if boxes is None:
                continue
            for b in boxes:
                try:
                    cls_id = int(b.cls[0].item())
                    conf = float(b.conf[0].item())
                    box = [int(v) for v in b.xyxy[0].tolist()]
                    label = str(model.names.get(cls_id, cls_id))
                except Exception:
                    continue
                coarse_key = (camera_ip, event_type, label)
                now = time.time()
                if now - self.last_road_event[coarse_key] < self.cfg.ROAD_EVENT_COOLDOWN_SECONDS:
                    continue
                self.last_road_event[coarse_key] = now
                crop = self._safe_crop(frame, box, pad=25)
                event_id = self.store.record_road_event(
                    session_id=self.session_id,
                    camera_ip=camera_ip,
                    event_type=event_type,
                    model_label=label,
                    confidence=conf,
                    evidence_image=self._jpeg(crop if crop is not None else frame),
                    metadata={"box": box},
                )
                events.append({"id": event_id, "type": event_type, "label": label, "confidence": conf})
        return events

    def process(self, camera_ip, frame, primary_result, primary_model, processed_index):
        summary = {"helmet_violations": 0, "road_events": 0}
        if not self.cfg.ADVANCED_DETECTION_ENABLED or primary_result is None:
            return summary
        if processed_index % self.cfg.ADVANCED_EVERY_N_FRAMES != 0:
            return summary

        persons, bikes = self._primary_objects(primary_result, primary_model)
        for bike in bikes:
            rider = next((p for p in persons if self._overlap_person_with_bike(p["box"], bike["box"])), None)
            if rider is None:
                continue
            helmet = self._helmet_status(frame, rider)
            if helmet and helmet[0] == "no_helmet":
                violation_id = self._store_no_helmet(camera_ip, frame, bike, rider, helmet[1])
                if violation_id:
                    summary["helmet_violations"] += 1
                    x1, y1, x2, y2 = rider["box"]
                    cv2.putText(frame, "NO HELMET", (x1, max(24, y1 - 28)), cv2.FONT_HERSHEY_SIMPLEX,
                                0.7, (0, 0, 255), 2, cv2.LINE_AA)

        summary["road_events"] += len(self._run_road_model(camera_ip, frame, "road_damage", "road_damage"))
        summary["road_events"] += len(self._run_road_model(camera_ip, frame, "road_obstruction", "road_obstruction"))
        return summary
