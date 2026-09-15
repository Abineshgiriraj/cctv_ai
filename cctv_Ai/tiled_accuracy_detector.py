import math
import os

from accuracy_detector import AccuracyDetector


class TiledAccuracyDetector(AccuracyDetector):
    """Run the helmet model on high-resolution road tiles, then match results to bikes.

    This is useful for fixed high-angle CCTV where riders stay small in the full frame.
    The existing per-bike crop detector remains as a fallback when tiled inference does
    not produce a usable helmet observation.
    """

    def __init__(self, config, store, session_id, log):
        super().__init__(config, store, session_id, log)

        self.helmet_tiled_detection = os.getenv("HELMET_TILED_DETECTION", "1").strip().lower() in {
            "1", "true", "yes", "on"
        }
        self.helmet_tile_columns = max(1, min(3, int(os.getenv("HELMET_TILE_COLUMNS", "2"))))
        self.helmet_tile_rows = max(1, min(3, int(os.getenv("HELMET_TILE_ROWS", "2"))))
        self.helmet_tile_overlap = min(0.35, max(0.05, float(os.getenv("HELMET_TILE_OVERLAP", "0.18"))))
        self.helmet_tile_imgsz = max(640, int(os.getenv("HELMET_TILE_IMGSZ", str(self.cfg.HELMET_IMGSZ))))
        self.helmet_tile_roi_top_ratio = min(
            0.75, max(0.0, float(os.getenv("HELMET_TILE_ROI_TOP_RATIO", "0.06")))
        )
        requested_bottom = float(os.getenv("HELMET_TILE_ROI_BOTTOM_RATIO", "0.78"))
        self.helmet_tile_roi_bottom_ratio = min(
            1.0,
            max(self.helmet_tile_roi_top_ratio + 0.10, requested_bottom),
        )

        self.log.info(
            "Tiled helmet mode enabled=%s grid=%sx%s overlap=%.2f imgsz=%s roi=%.2f..%.2f",
            self.helmet_tiled_detection,
            self.helmet_tile_columns,
            self.helmet_tile_rows,
            self.helmet_tile_overlap,
            self.helmet_tile_imgsz,
            self.helmet_tile_roi_top_ratio,
            self.helmet_tile_roi_bottom_ratio,
        )

    def _helmet_tiles(self, frame):
        h, w = frame.shape[:2]
        top = max(0, min(h - 1, int(h * self.helmet_tile_roi_top_ratio)))
        bottom = max(top + 1, min(h, int(h * self.helmet_tile_roi_bottom_ratio)))
        roi_h = bottom - top

        cols = self.helmet_tile_columns
        rows = self.helmet_tile_rows
        tile_w = int(math.ceil(w / cols))
        tile_h = int(math.ceil(roi_h / rows))
        x_overlap = int(tile_w * self.helmet_tile_overlap)
        y_overlap = int(tile_h * self.helmet_tile_overlap)

        tiles = []
        for row in range(rows):
            local_y1 = max(0, row * tile_h - (y_overlap if row else 0))
            local_y2 = min(
                roi_h,
                (row + 1) * tile_h + (y_overlap if row < rows - 1 else 0),
            )
            for col in range(cols):
                x1 = max(0, col * tile_w - (x_overlap if col else 0))
                x2 = min(
                    w,
                    (col + 1) * tile_w + (x_overlap if col < cols - 1 else 0),
                )
                y1 = top + local_y1
                y2 = top + local_y2
                tile = frame[y1:y2, x1:x2]
                if tile.size:
                    tiles.append((tile, x1, y1))
        return tiles

    def _detect_tiled_helmets(self, frame):
        model = self.models.get("helmet")
        if model is None or not self.helmet_tiled_detection:
            return []

        names = getattr(model, "names", {})
        detections = []
        for tile, x_offset, y_offset in self._helmet_tiles(frame):
            try:
                results = model.predict(
                    tile,
                    conf=self.helmet_observation_confidence,
                    imgsz=self.helmet_tile_imgsz,
                    verbose=False,
                )
            except Exception as exc:
                self.log.debug("Helmet tile inference failed: %s", exc)
                continue

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
                        x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
                        detections.append({
                            "status": status,
                            "confidence": confidence,
                            "box": [
                                x1 + x_offset,
                                y1 + y_offset,
                                x2 + x_offset,
                                y2 + y_offset,
                            ],
                            "detection_source": "tile",
                        })
                    except Exception:
                        continue

        # Remove duplicate detections caused by overlapping tiles.
        detections.sort(key=lambda row: row["confidence"], reverse=True)
        kept = []
        for detection in detections:
            if any(
                detection["status"] == existing["status"]
                and self._iou(detection["box"], existing["box"]) >= 0.45
                for existing in kept
            ):
                continue
            kept.append(detection)
        return kept

    @staticmethod
    def _point_inside(box, x, y):
        return box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def _match_tiled_helmet(self, frame, bike, person, detections):
        if not detections:
            return None

        bike_region = self._bike_head_region(frame, bike["box"])
        person_region = self._head_region(frame, person["box"]) if person else None
        bx1, by1, bx2, by2 = bike_region
        bcx = (bx1 + bx2) / 2.0
        bcy = (by1 + by2) / 2.0
        bw = max(1.0, bx2 - bx1)
        bh = max(1.0, by2 - by1)

        matched = []
        for detection in detections:
            dx1, dy1, dx2, dy2 = detection["box"]
            dcx = (dx1 + dx2) / 2.0
            dcy = (dy1 + dy2) / 2.0

            in_person_head = bool(person_region and self._point_inside(person_region, dcx, dcy))
            in_bike_head = self._point_inside(bike_region, dcx, dcy)
            if not in_person_head and not in_bike_head:
                continue

            # Prefer a helmet observation that lands inside an associated person's
            # head region. Otherwise accept the bike-relative head zone with the
            # stricter fallback confirmation rules.
            source = "person_head" if in_person_head else "bike_fallback"
            location_bonus = 0.18 if in_person_head else 0.0
            distance = abs(dcx - bcx) / bw + abs(dcy - bcy) / bh
            rank = float(detection["confidence"]) + location_bonus - 0.08 * distance
            matched.append((rank, source, detection))

        if not matched:
            return None

        best_by_status = {}
        for rank, source, detection in matched:
            status = detection["status"]
            current = best_by_status.get(status)
            if current is None or rank > current[0]:
                best_by_status[status] = (rank, source, detection)

        helmet = best_by_status.get("helmet")
        no_helmet = best_by_status.get("no_helmet")
        chosen = None
        if helmet and no_helmet:
            helmet_conf = float(helmet[2]["confidence"])
            no_helmet_conf = float(no_helmet[2]["confidence"])
            chosen = helmet if no_helmet_conf < helmet_conf + self.helmet_conflict_margin else no_helmet
        else:
            chosen = helmet or no_helmet

        if not chosen:
            return None

        _, source, detection = chosen
        return {
            **detection,
            "source": source,
            "search_box": person_region if source == "person_head" else bike_region,
        }

    def process(self, camera_ip, clean_frame, primary_result, primary_model,
                processed_index, draw_frame=None):
        draw_frame = draw_frame if draw_frame is not None else clean_frame
        summary = {
            "helmet_checked": 0,
            "helmet_detected": 0,
            "no_helmet_detected": 0,
            "helmet_violations": 0,
            "helmet_tile_candidates": 0,
            "helmet_tile_matches": 0,
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
                if "plate" in self.models and bikes else []
            )
            tiled_helmet_detections = self._detect_tiled_helmets(clean_frame) if bikes else []
            summary["helmet_tile_candidates"] = len(tiled_helmet_detections)

            for bike in bikes:
                rider = self._best_rider(persons, bike)
                matched_plate = self._match_plate_to_bike(bike["box"], plate_detections)
                plate = self._plate_consensus(camera_ip, bike, matched_plate) if matched_plate else None
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

                # Primary method: high-resolution tile detection. This avoids
                # depending on a tiny rider crop when the CCTV view is far away.
                observation = self._match_tiled_helmet(
                    clean_frame,
                    bike,
                    rider,
                    tiled_helmet_detections,
                )
                if observation:
                    summary["helmet_tile_matches"] += 1
                else:
                    # Keep the existing per-bike head crop as a fallback.
                    observation = self._helmet_status(clean_frame, bike, rider)

                if not observation:
                    continue

                summary["helmet_checked"] += 1
                confirmation = self._helmet_confirmed(camera_ip, bike, observation)
                status, confidence = observation["status"], observation["confidence"]
                suffix = " ?"
                if confirmation:
                    status, confidence, _, _ = confirmation
                    suffix = ""

                color = (0, 0, 255) if status == "no_helmet" else (0, 220, 80)
                label = "NO HELMET" if status == "no_helmet" else "HELMET"
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
