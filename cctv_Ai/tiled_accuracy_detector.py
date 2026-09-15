import math
import os

from accuracy_detector import AccuracyDetector


class TiledAccuracyDetector(AccuracyDetector):
    """Run the helmet model on high-resolution road tiles, then match results to bikes.

    Tiled inference improves small-rider visibility, but full-image tiles can also make
    faces and people printed on posters look like helmet/no-helmet candidates. This
    class therefore requires a real moving motorcycle/rider relationship before a
    tiled candidate is allowed into the multi-frame helmet vote.
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

        # False-positive protection for posters / wall photos / parked motorcycles.
        self.helmet_tile_require_person = os.getenv(
            "HELMET_TILE_REQUIRE_PERSON", "1"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.helmet_tile_min_person_overlap = min(
            0.95,
            max(0.10, float(os.getenv("HELMET_TILE_MIN_PERSON_OVERLAP", "0.35"))),
        )
        self.helmet_tile_min_bike_motion_px = max(
            0.0, float(os.getenv("HELMET_TILE_MIN_BIKE_MOTION_PX", "8"))
        )
        self.helmet_tile_motion_frames = max(
            1, min(5, int(os.getenv("HELMET_TILE_MOTION_FRAMES", "2")))
        )
        self.helmet_tile_max_relative_shift = min(
            1.5,
            max(0.10, float(os.getenv("HELMET_TILE_MAX_RELATIVE_SHIFT", "0.55"))),
        )

        self._bike_motion_state = {}
        self._tile_relative_state = {}

        self.log.info(
            "Tiled helmet mode enabled=%s grid=%sx%s overlap=%.2f imgsz=%s "
            "roi=%.2f..%.2f require_person=%s min_bike_motion=%.1fpx motion_frames=%s",
            self.helmet_tiled_detection,
            self.helmet_tile_columns,
            self.helmet_tile_rows,
            self.helmet_tile_overlap,
            self.helmet_tile_imgsz,
            self.helmet_tile_roi_top_ratio,
            self.helmet_tile_roi_bottom_ratio,
            self.helmet_tile_require_person,
            self.helmet_tile_min_bike_motion_px,
            self.helmet_tile_motion_frames,
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

    @staticmethod
    def _intersection_over_detection(detection_box, region_box):
        dx1, dy1, dx2, dy2 = detection_box
        rx1, ry1, rx2, ry2 = region_box
        ix1, iy1 = max(dx1, rx1), max(dy1, ry1)
        ix2, iy2 = min(dx2, rx2), min(dy2, ry2)
        iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
        intersection = iw * ih
        detection_area = max(1, (dx2 - dx1) * (dy2 - dy1))
        return intersection / float(detection_area)

    def _bike_motion_ok(self, camera_ip, bike):
        key = self._track_key(camera_ip, bike)
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

    def _relative_motion_ok(self, camera_ip, bike, detection):
        key = self._track_key(camera_ip, bike)
        bx1, by1, bx2, by2 = bike["box"]
        dx1, dy1, dx2, dy2 = detection["box"]
        bw = max(1.0, bx2 - bx1)
        bh = max(1.0, by2 - by1)
        bcx = (bx1 + bx2) / 2.0
        bcy = (by1 + by2) / 2.0
        dcx = (dx1 + dx2) / 2.0
        dcy = (dy1 + dy2) / 2.0
        relative = ((dcx - bcx) / bw, (dcy - bcy) / bh)

        state_key = (key, detection["status"])
        state = self._tile_relative_state.get(state_key)
        if state is None:
            self._tile_relative_state[state_key] = {"relative": relative, "stable": 1}
            return self.helmet_tile_motion_frames <= 1

        previous = state["relative"]
        shift = math.hypot(relative[0] - previous[0], relative[1] - previous[1])
        if shift <= self.helmet_tile_max_relative_shift:
            state["stable"] += 1
        else:
            state["stable"] = 1
            # A poster remains fixed in the image while a motorcycle moves past it,
            # so its relative position to that motorcycle changes. Clear previous
            # helmet votes when that relationship breaks.
            self.helmet_votes[key].clear()
        state["relative"] = relative
        return state["stable"] >= self.helmet_tile_motion_frames

    def _match_tiled_helmet(self, camera_ip, frame, bike, person, detections):
        if not detections:
            return None

        # Do not classify parked motorcycles. This also blocks the common case in
        # Camera 1 where a parked bike sits in front of large face posters.
        if not self._bike_motion_ok(camera_ip, bike):
            return None

        # Tile mode is deliberately person-linked by default. If YOLO cannot find
        # a rider, the existing bike-relative crop detector remains available as a
        # fallback, but a random poster from a full tile is not allowed to vote.
        if self.helmet_tile_require_person and person is None:
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

            in_person_head = bool(
                person_region and self._point_inside(person_region, dcx, dcy)
            )
            in_bike_head = self._point_inside(bike_region, dcx, dcy)

            if self.helmet_tile_require_person:
                if not in_person_head:
                    continue
                overlap = self._intersection_over_detection(
                    detection["box"], person_region
                )
                if overlap < self.helmet_tile_min_person_overlap:
                    continue
                source = "person_head"
                location_bonus = 0.22
            else:
                if not in_person_head and not in_bike_head:
                    continue
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
        if helmet and no_helmet:
            helmet_conf = float(helmet[2]["confidence"])
            no_helmet_conf = float(no_helmet[2]["confidence"])
            chosen = (
                helmet
                if no_helmet_conf < helmet_conf + self.helmet_conflict_margin
                else no_helmet
            )
        else:
            chosen = helmet or no_helmet

        if not chosen:
            return None

        _, source, detection = chosen
        if not self._relative_motion_ok(camera_ip, bike, detection):
            return None

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
            tiled_helmet_detections = (
                self._detect_tiled_helmets(clean_frame) if bikes else []
            )
            summary["helmet_tile_candidates"] = len(tiled_helmet_detections)

            for bike in bikes:
                rider = self._best_rider(persons, bike)
                matched_plate = self._match_plate_to_bike(
                    bike["box"], plate_detections
                )
                plate = (
                    self._plate_consensus(camera_ip, bike, matched_plate)
                    if matched_plate else None
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
                    self._draw(draw_frame, plate["box"], label, (255, 160, 0))

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
                    # Existing rider/bike crop remains a fallback. It is local to
                    # the motorcycle, unlike the full tile, so it is safer when no
                    # person association is available.
                    observation = self._helmet_status(clean_frame, bike, rider)

                if not observation:
                    continue

                summary["helmet_checked"] += 1
                confirmation = self._helmet_confirmed(
                    camera_ip, bike, observation
                )
                status = observation["status"]
                confidence = observation["confidence"]
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
