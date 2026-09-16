from accuracy_detector import AccuracyDetector


class RiderVerifiedDetector(AccuracyDetector):
    """Compatibility wrapper for the current multi-camera accuracy detector.

    The multi-camera migration changed detector methods from a plain camera-ip string
    to a full camera dictionary. Keeping a second copy of the helmet processing loop
    here caused mixed string/dict calls and stale method signatures. The active
    accuracy detector already performs motorcycle/rider association, helmet voting,
    plate OCR, road detection and MySQL storage using the camera dictionary, so this
    wrapper deliberately delegates to that single implementation.
    """

    def process(
        self,
        camera,
        clean_frame,
        primary_result,
        primary_model,
        processed_index,
        draw_frame=None,
    ):
        return super().process(
            camera,
            clean_frame,
            primary_result,
            primary_model,
            processed_index,
            draw_frame=draw_frame,
        )
