import math
import os
from collections import defaultdict, deque

from tiled_accuracy_detector import TiledAccuracyDetector


class RiderVerifiedDetector(TiledAccuracyDetector):
    """Helmet detector that only evaluates a verified rider on a moving motorcycle.

    Background posters and parked motorcycles are rejected before helmet voting.
    A faster confirmation path is available for real moving riders so useful
    low-confidence no-helmet observations are not discarded while waiting for
    the stricter confirmation path.
    """

    def __init__(self, config, store, session_id, log):
        super().__init__(config, store, session_id, log)
        self.fast_no_helmet_enabled = os.getenv(
            "FAST_NO_HELMET_ENABLED", "1"
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.fast_no_helmet_min_confidence = min(
            0.60,
            max(0.15, float(os.getenv("FAST_NO_HELMET_MIN_CONFIDENCE", "0.28"))),
        )
        self.fast_no_helmet_confirm_frames = max(
            2, min(4, int(os.getenv("FAST_NO_HELMET_CONFIRM_FRAMES", "2")))
        )
        self.fast_no_helmet_window = max(
            self.fast_no_helmet_confirm_frames,
            min(6, int(os.getenv("FAST_NO_HELMET_WINDOW", "4"))),
        )
        self.fast_no_helmet_avg_confidence = min(
            0.70,
            max(
                self.fast_no_helmet_min_confidence,
                float(os.getenv("FAST_NO_HELMET_AVG_CONFIDENCE", "0.32")),
            ),
        )
        self.fast_helmet_veto_confidence = min(
            0.95,
            max(0.35, float(os.getenv("FAST_HELMET_VETO_CONFIDENCE", "0.55"))),
        )
        self.fast_helmet_votes = defaultdict(
            lambda: deque(maxlen=self.fast_no_helmet_window)
        )

        self.log.info(
            "Fast rider helmet confirmation enabled=%s min=%.2f avg=%.2f frames=%s window=%s",
            self.fast_no_helmet_enabled,
            self.fast_no_helmet_min_confidence,
            self.fast_no_helmet_avg_confidence,
            self.fast_no_helmet_confirm_frames,
            self.fast_no_helmet_window,
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

    def _clear_fast_votes(self, camera_ip, bike):
        key = self._track_key(camera_ip, bike)
        self.fast_helmet_votes[key].clear()

    def _fast_no_helmet_confirmation(self, camera_ip, bike, observation):
        """Confirm only a person-head no-helmet result on a verified moving bike.

        This path never receives poster/full-frame candidates because the caller has
        already required a verified rider and `source == person_head`.
        """
        if not self.fast_no_helmet_enabled:
            return None

        key = self._track_key(camera_ip, bike)
        votes = self.fast_helmet_votes[key]
        status = observation.get("status")
        confidence = float(observation.get("confidence") or 0)

        if status == "helmet":
            if confidence >= self.fast_helmet_veto_confidence:
                votes.clear()
            return None

        if status != "no_helmet" or confidence < self.fast_no_helmet_min_confidence:
            return None

        votes.append(confidence)
        if len(votes) < self.fast_no_helmet_confirm_frames:
            return None

        recent = list(votes)[-self.fast_no_helmet_confirm_frames:]
        average = sum(recent) / len(recent)
        if average < self.fast_no_helmet_avg_confidence:
            return None

        return "no_helmet", average, len(recent), len(votes)

    def process(self, camera_ip, clean_frame, primary_result, primary_model,
                processed_index, draw_frame=None):
        draw_frame = draw_frame if draw_frame is not None else clean_frame
        summary = {
            "helmet_checked": 0,
            "helmet_detected": 0,
            "no_helmet_detected": 0,
            "helmet_violations": 0,
            "helmet_fast_confirmed": 0,
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
            tiled_helmet_detections = (
                self._detect_tiled_helmets(clean_frame) if bikes else []
            )
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

                # A real rider is mandatory. Posters cannot reach helmet voting.
                if rider is None:
                    self.helmet_votes[key].clear()
                    self._clear_fast_votes(camera_ip, bike)
                    summary["helmet_skipped_no_rider"] += 1
                    continue

                # Parked motorcycles are ignored.
                if not self._moving_bike(camera_ip, bike):
                    self.helmet_votes[key].clear()
                    self._clear_fast_votes(camera_ip, bike)
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
                    observation = self._helmet_status(clean_frame, bike, rider)

                if not observation:
                    continue

                # Final observations must be inside an associated person's head ROI.
                if observation.get("source") != "person_head":
                    self.helmet_votes[key].clear()
                    self._clear_fast_votes(camera_ip, bike)
                    continue

                summary["helmet_checked"] += 1

                # Keep the existing strict confirmation first.
                confirmation = self._helmet_confirmed(camera_ip, bike, observation)

                # If strict confirmation has not completed, allow a faster decision
                # only for repeated no-helmet observations on this verified rider.
                if confirmation is None:
                    confirmation = self._fast_no_helmet_confirmation(
                        camera_ip, bike, observation
                    )
                    if confirmation is not None:
                        summary["helmet_fast_confirmed"] += 1

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
                    self._clear_fast_votes(camera_ip, bike)
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
                    self._clear_fast_votes(camera_ip, bike)

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
