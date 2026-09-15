import os

import cv2

from tiled_accuracy_detector import TiledAccuracyDetector


class RiderVerifiedDetector(TiledAccuracyDetector):
    """Helmet detector that only stores violations for verified moving riders.

    An optional low-confidence candidate overlay can be enabled to make helmet
    analysis visible on moving motorcycles. Candidate overlays may use a
    motorcycle-relative head crop when the person detector misses the rider, but
    they are never written to the violations table.
    """

    def __init__(self, config, store, session_id, log):
        super().__init__(config, store, session_id, log)
        self.helmet_candidate_mode = os.getenv(
            "HELMET_CANDIDATE_MODE", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.helmet_candidate_min_confidence = min(
            0.50,
            max(
                0.05,
                float(os.getenv("HELMET_CANDIDATE_MIN_CONFIDENCE", "0.15")),
            ),
        )
        self.helmet_candidate_imgsz = max(
            640, int(os.getenv("HELMET_CANDIDATE_IMGSZ", "1280"))
        )
        self.helmet_show_rider_check = os.getenv(
            "HELMET_SHOW_RIDER_CHECK", "1"
        ).strip().lower() in {"1", "true", "yes", "on"}

        self.log.info(
            "Helmet candidate mode=%s candidate_conf>=%.2f imgsz=%s",
            self.helmet_candidate_mode,
            self.helmet_candidate_min_confidence,
            self.helmet_candidate_imgsz,
        )

    def _moving_bike(self, camera_ip, bike):
        return self._bike_motion_ok(camera_ip, bike)

    def _verified_rider(self, persons, bike):
        rider = self._best_rider(persons, bike)
        if rider is None:
            return None

        px1, py1, px2, py2 = rider["box"]
        bx1, by1, bx2, by2 = bike["box"]

        pcx = (px1 + px2) / 2.0
        pbottom = py2
        bcx = (bx1 + bx2) / 2.0
        bw = max(1.0, bx2 - bx1)
        bh = max(1.0, by2 - by1)

        horizontal = abs(pcx - bcx) / bw
        vertical = abs(pbottom - by1) / max(1.0, bh)
        if horizontal > 0.75:
            return None
        if vertical > 1.8:
            return None
        return rider

    def _candidate_observation(self, frame, bike, rider=None):
        """Return a low-threshold visual helmet candidate.

        This path is intentionally isolated from _helmet_confirmed and
        _store_no_helmet, so weak visual candidates cannot become stored
        violation records.
        """
        if not self.helmet_candidate_mode:
            return None

        model = self.models.get("helmet")
        if model is None:
            return None

        if rider is not None:
            region = self._head_region(frame, rider.get("box"))
            source = "candidate_person_head"
        else:
            region = self._bike_head_region(frame, bike.get("box"))
            source = "candidate_bike_head"

        if not region:
            return None

        crop = self._crop(frame, region, 4)
        if crop is None or crop.size == 0:
            return None

        h, w = crop.shape[:2]
        max_side = max(h, w)
        scale = (
            5.0
            if max_side < 120
            else 3.0
            if max_side < 220
            else 2.0
            if max_side < 360
            else 1.0
        )
        source_image = (
            cv2.resize(
                crop,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_CUBIC,
            )
            if scale > 1.0
            else crop
        )

        try:
            results = model.predict(
                source_image,
                conf=self.helmet_candidate_min_confidence,
                imgsz=self.helmet_candidate_imgsz,
                verbose=False,
            )
        except Exception as exc:
            self.log.debug("Helmet candidate inference failed: %s", exc)
            return None

        names = getattr(model, "names", {})
        candidates = []
        for result in results or []:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                try:
                    cls_id = int(box.cls[0].item())
                    confidence = float(box.conf[0].item())
                    raw_name = (
                        names.get(cls_id, cls_id)
                        if isinstance(names, dict)
                        else names[cls_id]
                    )
                    status = self._helmet_label(raw_name)
                    if (
                        not status
                        or confidence < self.helmet_candidate_min_confidence
                    ):
                        continue
                    x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
                    mapped = [
                        int(region[0] + x1 / scale),
                        int(region[1] + y1 / scale),
                        int(region[0] + x2 / scale),
                        int(region[1] + y2 / scale),
                    ]
                    candidates.append(
                        {
                            "status": status,
                            "confidence": confidence,
                            "box": mapped,
                            "source": source,
                        }
                    )
                except Exception:
                    continue

        if not candidates:
            return None

        best_helmet = max(
            (row for row in candidates if row["status"] == "helmet"),
            key=lambda row: row["confidence"],
            default=None,
        )
        best_no_helmet = max(
            (row for row in candidates if row["status"] == "no_helmet"),
            key=lambda row: row["confidence"],
            default=None,
        )

        if best_helmet and best_no_helmet:
            if (
                best_no_helmet["confidence"]
                >= best_helmet["confidence"] + 0.05
            ):
                return best_no_helmet
            return best_helmet

        return best_no_helmet or best_helmet

    def _draw_candidate(self, frame, bike, rider=None):
        observation = self._candidate_observation(frame, bike, rider)
        if observation:
            if observation["status"] == "no_helmet":
                label = (
                    f"POSSIBLE NO HELMET "
                    f"{observation['confidence'] * 100:.0f}%"
                )
                color = (0, 165, 255)
            else:
                label = (
                    f"POSSIBLE HELMET "
                    f"{observation['confidence'] * 100:.0f}%"
                )
                color = (255, 200, 0)

            self._draw(frame, observation["box"], label, color)
            return observation

        if self.helmet_show_rider_check:
            box = rider["box"] if rider is not None else bike["box"]
            self._draw(frame, box, "HELMET CHECK", (255, 180, 0))

        return None

    def process(
        self,
        camera_ip,
        clean_frame,
        primary_result,
        primary_model,
        processed_index,
        draw_frame=None,
    ):
        draw_frame = draw_frame if draw_frame is not None else clean_frame
        summary = {
            "helmet_checked": 0,
            "helmet_detected": 0,
            "no_helmet_detected": 0,
            "helmet_violations": 0,
            "helmet_tile_candidates": 0,
            "helmet_tile_matches": 0,
            "helmet_skipped_no_rider": 0,
            "helmet_skipped_stationary": 0,
            "helmet_visual_candidates": 0,
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
            plate_detections = (
                self._detect_plates_frame(clean_frame)
                if "plate" in self.models and bikes
                else []
            )
            tiled_helmet_detections = (
                self._detect_tiled_helmets(clean_frame) if bikes else []
            )
            summary["helmet_tile_candidates"] = len(tiled_helmet_detections)

            for bike in bikes:
                key = self._track_key(camera_ip, bike)
                rider = self._verified_rider(persons, bike)
                moving = self._moving_bike(camera_ip, bike)

                matched_plate = self._match_plate_to_bike(
                    bike["box"], plate_detections
                )
                plate = (
                    self._plate_consensus(camera_ip, bike, matched_plate)
                    if matched_plate
                    else None
                )
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
                    self._draw(
                        draw_frame,
                        plate["box"],
                        label,
                        (255, 160, 0),
                    )

                # Low-confidence visual path for moving motorcycles. These
                # candidates are never stored as violations.
                if self.helmet_candidate_mode and moving:
                    if self._draw_candidate(draw_frame, bike, rider):
                        summary["helmet_visual_candidates"] += 1

                # Stored violations remain strict: no verified rider means no
                # stored helmet decision.
                if rider is None:
                    self.helmet_votes[key].clear()
                    summary["helmet_skipped_no_rider"] += 1
                    continue

                if not moving:
                    self.helmet_votes[key].clear()
                    summary["helmet_skipped_stationary"] += 1
                    continue

                observation = self._match_tiled_helmet(
                    camera_ip,
                    clean_frame,
                    bike,
                    rider,
                    tiled_helmet_detections,
                )
                if observation:
                    summary["helmet_tile_matches"] += 1
                else:
                    observation = self._helmet_status(
                        clean_frame,
                        bike,
                        rider,
                    )

                if not observation:
                    continue

                if observation.get("source") != "person_head":
                    self.helmet_votes[key].clear()
                    continue

                summary["helmet_checked"] += 1
                confirmation = self._helmet_confirmed(
                    camera_ip,
                    bike,
                    observation,
                )
                status = observation["status"]
                confidence = observation["confidence"]
                suffix = " ?"

                if confirmation:
                    status, confidence, _, _ = confirmation
                    suffix = ""

                color = (
                    (0, 0, 255)
                    if status == "no_helmet"
                    else (0, 220, 80)
                )
                label = (
                    "NO HELMET"
                    if status == "no_helmet"
                    else "HELMET"
                )
                self._draw(
                    draw_frame,
                    observation["box"],
                    f"{label}{suffix} {confidence * 100:.0f}%",
                    color,
                )

                if not confirmation:
                    continue

                if status == "helmet":
                    summary["helmet_detected"] += 1
                    continue

                summary["no_helmet_detected"] += 1
                if self._store_no_helmet(
                    camera_ip,
                    clean_frame,
                    bike,
                    rider,
                    confirmation,
                    observation["search_box"],
                    plate,
                ):
                    summary["helmet_violations"] += 1

        if run_road_ai:
            summary["road_events"] += len(
                self._run_road_model(
                    camera_ip,
                    clean_frame,
                    "road_damage",
                    "road_damage",
                    draw_frame=draw_frame,
                )
            )
            summary["road_events"] += len(
                self._run_road_model(
                    camera_ip,
                    clean_frame,
                    "road_obstruction",
                    "road_obstruction",
                    draw_frame=draw_frame,
                )
            )

        return summary
