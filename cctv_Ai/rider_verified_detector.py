import math

from accuracy_detector import AccuracyDetector


class RiderVerifiedDetector(AccuracyDetector):
    """Helmet detector that only evaluates a verified rider on a moving motorcycle.

    This blocks poster/background-face false positives by refusing bike-only helmet
    decisions. If no rider/person is associated with the motorcycle, helmet status is
    treated as uncertain and no violation is stored.
    """

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

        # Keep the rider tightly related to the motorcycle. This is stricter than
        # the base association so nearby pedestrians/posters do not become riders.
        horizontal = abs(pcx - bcx) / bw
        vertical = abs(pbottom - by1) / max(1.0, bh)
        if horizontal > 0.75:
            return None
        if vertical > 1.8:
            return None
        return rider

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
            "helmet_skipped_no_rider": 0,
            "helmet_skipped_stationary": 0,
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
            tiled_helmet_detections = []
            summary["helmet_tile_candidates"] = len(tiled_helmet_detections)

            for bike in bikes:
                key = self._track_key(camera_ip, bike)
                rider = self._verified_rider(persons, bike)

                matched_plate = self._match_plate_to_bike(bike["box"], plate_detections)
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

                # Critical safety rule: no rider means no helmet decision.
                if rider is None:
                    self.helmet_votes[key].clear()
                    summary["helmet_skipped_no_rider"] += 1
                    continue

                # Critical safety rule: stationary/parked motorcycles are ignored.
                if not self._moving_bike(camera_ip, bike):
                    self.helmet_votes[key].clear()
                    summary["helmet_skipped_stationary"] += 1
                    continue

                # First try tiled high-resolution detection, but only against the
                # verified rider's head region.
                observation = None
                if observation:
                    summary["helmet_tile_matches"] += 1
                else:
                    # Fallback is still allowed, but only with a verified rider.
                    observation = self._helmet_status(clean_frame, bike, rider)

                if not observation:
                    continue

                # Refuse any bike-relative fallback result. The final accepted
                # observation must be person-head based.
                if observation.get("source") != "person_head":
                    self.helmet_votes[key].clear()
                    continue

                summary["helmet_checked"] += 1
                confirmation = self._helmet_confirmed(camera_ip, bike, observation)
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
