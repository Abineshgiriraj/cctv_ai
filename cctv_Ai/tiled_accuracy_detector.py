import math
import os
import time

from accuracy_detector import AccuracyDetector

class TiledAccuracyDetector(AccuracyDetector):
    def __init__(self, config, store, session_id, log):
        super().__init__(config, store, session_id, log)
        
        self.helmet_tiled_detection = False
        self.helmet_tile_columns = 1
        self.helmet_tile_rows = 1
        self.helmet_tile_imgsz = 640
        self.helmet_tile_roi_top_ratio = 0.0
        self.helmet_tile_roi_bottom_ratio = 1.0

        self.log.info("Tiled helmet mode has been disabled in favor of motorcycle-bound tracking.")
        
    def process(self, camera_ip, clean_frame, primary_result, primary_model,
                processed_index, draw_frame=None):
        return super().process(camera_ip, clean_frame, primary_result, primary_model,
                               processed_index, draw_frame=draw_frame)
