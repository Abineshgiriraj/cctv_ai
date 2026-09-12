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
        x1 = max(0, x1 - pad)
        y1 = max(0, y1 - pad)
        x2 = min(w, x2 + pad)
        y2 = min(h, y2 + pad)
        if x2 <= x1 or y2 <= y1:
            return None
        return frame[y1:y2, x1:x2]

    @staticmethod
    def _overlap_person_with_bike(person_box, bike_box):
        px1, py1, px2, py2 = person_box
        bx1, by1, bx2, by2 = bike_box
        pcx = (px1 + px2) / 2
        pbottom = py2
        expanded_x1 = bx1 - (bx2 - bx1) * 0.55
        expanded_x2 = bx2 + (bx2 - bx1) * 0.55
        expanded_y1 = by1 - (by2 - by1) * 2.2
        expanded_y2 = by2 + (by2 - by1) * 0.45
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

    @staticmethod
    def _helmet_label_status(name):
        normalized = str(name).strip().lower().replace("-", "_").replace(" ", "_")
        no_helmet_terms = (
            "no_helmet",
            "nohelmet",
            "without_helmet",
            "withouthelmet",
            "helmetless",
            "bare_head",
            "barehead",
            "no_hardhat",
            "nohardhat",
        )
        if any(term in normalized for term in no_helmet_terms):
            return "no_helmet"
        if "helmet" in normalized or "hardhat" in normalized:
            return "helmet"
        return None

    @staticmethod
    def _motorcycle_rider_region(frame, bike_box, person_box=None):
        h, w = frame.shape[:2]
        bx1, by1, bx2, by2 = [int(v) for v in bike_box]
        bw = max(1, bx2 - bx1)
        bh = max(1, by2 - by1)

        if person_box is not None:
            px1, py1, px2, py2 = [int(v) for v in person_box]
            # Helmet is normally within the upper half of the associated rider.
            ph = max(1, py2 - py1)
            x1 = px1 - int((px2 - px1) * 0.18)
            x2 = px2 + int((px2 - px1) * 0.18)
            y1 = py1 - int(ph * 0.12)
            y2 = py1 + int(ph * 0.58)
        else:
            # Primary COCO detection often sees the motorcycle but misses the rider,
            # especially at CCTV distance. Search a rider/head region above the bike.
            x1 = bx1 - int(bw * 0.55)
            x2 = bx2 + int(bw * 0.55)
            y1 = by1 - int(bh * 2.35)
            y2 = by1 + int(bh * 0.55)

        return [
            max(0, x1),
            max(0, y1),
            min(w, x2),
            min(h, y2),
        ]

    def _helmet_status(self, frame, bike, person=None):
        model = self.models.get("helmet")
        if not model:
            return None

        region_box = self._motorcycle_rider_region(
            frame,
            bike["box"],
            person["box"] if person is not None else None,
        )
        crop = self._safe_crop(frame, region_box, pad=5)
        if crop is None:
            return None

        try:
            results = model.predict(
                crop,
                conf=self.cfg.ADVANCED_CONFIDENCE,
                imgsz=640,
                verbose=False,
            )
        except Exception as exc:
            self.log.debug("Helmet inference failed: %s", exc)
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
                    names = getattr(model, "names", {})
                    name = names.get(cls_id, cls_id) if isinstance(names, dict) else names[cls_id]
                except Exception:
                    continue
                status = self._helmet_label_status(name)
                if status and (best is None or conf > best[1]):
                    best = (status, conf, region_box)
        return best

    @staticmethod
    def _draw_helmet_state(frame, status, confidence, anchor_box):
        x1, y1, _, _ = [int(v) for v in anchor_box]
        if status == "no_helmet":
            label = f"NO HELMET {confidence * 100:.0f}%"
            color = (0, 0, 255)
        else:
            label = f"HELMET {confidence * 100:.0f}%"
            color = (0, 220, 80)
        cv2.putText(
            frame,
            label,
            (max(4, x1), max(24, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            color,
            2,
            cv2.LINE_AA,
        )

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

    def _store_no_helmet(self, camera_ip, frame, bike, person, status_conf, evidence_box):
        if self.store is None:
            return None

        person_track_id = person.get("track_id") if person is not None else None
        key = (camera_ip, bike.get("track_id"), person_track_id if person_track_id is not None else -1)
        now = time.time()
        if now - self.last_violation[key] < self.cfg.VIOLATION_COOLDOWN_SECONDS:
            return None
        self.last_violation[key] = now

        if person is not None:
            x1 = min(bike["box"][0], person["box"][0])
            y1 = min(bike["box"][1], person["box"][1])
            x2 = max(bike["box"][2], person["box"][2])
            y2 = max(bike["box"][3], person["box"][3])
            union = [x1, y1, x2, y2]
        else:
            union = [
                min(bike["box"][0], evidence_box[0]),
                min(bike["box"][1], evidence_box[1]),
                max(bike["box"][2], evidence_box[2]),
                max(bike["box"][3], evidence_box[3]),
            ]

        evidence = self._safe_crop(frame, union, pad=35)
        plate_number, plate_conf, plate_crop = self._plate_read(frame, bike["box"])
        return self.store.record_violation(
            session_id=self.session_id,
            camera_ip=camera_ip,
            violation_type="no_helmet",
            vehicle_type="motorcycle",
            vehicle_track_id=bike.get("track_id"),
            person_track_id=person_track_id,
            helmet_status="no_helmet",
            plate_number=plate_number,
            plate_confidence=plate_conf,
            detection_confidence=status_conf,
            evidence_image=self._jpeg(evidence if evidence is not None else frame),
            plate_image=self._jpeg(plate_crop),
            metadata={
                "bike_confidence": bike.get("conf"),
                "person_confidence": person.get("conf") if person is not None else None,
                "person_box_available": person is not None,
                "helmet_search_box": evidence_box,
            },
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
        summary = {
            "helmet_checked": 0,
            "helmet_detected": 0,
            "no_helmet_detected": 0,
            "helmet_violations": 0,
            "road_events": 0,
        }
        if not self.cfg.ADVANCED_DETECTION_ENABLED or primary_result is None:
            return summary
        if processed_index % self.cfg.ADVANCED_EVERY_N_FRAMES != 0:
            return summary

        persons, bikes = self._primary_objects(primary_result, primary_model)
        for bike in bikes:
            rider = next(
                (p for p in persons if self._overlap_person_with_bike(p["box"], bike["box"])),
                None,
            )

            # Do not require the stock COCO person detector to see the rider. At CCTV
            # distance it often finds the motorcycle but misses the rider entirely.
            helmet = self._helmet_status(frame, bike, rider)
            if not helmet:
                continue

            status, confidence, search_box = helmet
            summary["helmet_checked"] += 1
            if status == "helmet":
                summary["helmet_detected"] += 1
            elif status == "no_helmet":
                summary["no_helmet_detected"] += 1

            self._draw_helmet_state(frame, status, confidence, search_box)

            if status == "no_helmet":
                violation_id = self._store_no_helmet(
                    camera_ip,
                    frame,
                    bike,
                    rider,
                    confidence,
                    search_box,
                )
                if violation_id:
                    summary["helmet_violations"] += 1

        summary["road_events"] += len(
            self._run_road_model(camera_ip, frame, "road_damage", "road_damage")
        )
        summary["road_events"] += len(
            self._run_road_model(camera_ip, frame, "road_obstruction", "road_obstruction")
        )
        return summary
