"""Bounded, latest-frame road inference, independent of live helmet rendering."""
import threading
from collections import OrderedDict


class RoadWorker:
    def __init__(self, process, active, on_error, limit=10):
        self.process = process
        self.active = active
        self.on_error = on_error
        self.limit = limit
        self.pending = OrderedDict()
        self.condition = threading.Condition()
        threading.Thread(target=self._run, name="road-inference", daemon=True).start()

    def submit(self, camera, frame):
        with self.condition:
            key = camera['camera_key']
            # Replacing a waiting camera retains its place: busy cameras cannot starve others.
            self.pending[key] = (camera, frame)
            while len(self.pending) > self.limit:
                self.pending.popitem(last=False)
            self.condition.notify()

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: bool(self.pending))
                key, (camera, frame) = self.pending.popitem(last=False)
            if not self.active(key):
                continue
            try:
                self.process(camera, frame)
            except Exception:
                self.on_error(key)
