/* Finite JPEG requests leave browser connections available for AI/status APIs. */
(function (root) {
  class CameraFrames {
    constructor(options = {}) {
      this.fetch = options.fetch || root.fetch.bind(root);
      this.createImage = options.createImage || (() => new root.Image());
      this.urls = options.urls || root.URL;
      this.limit = options.limit || 2;
      this.active = 0;
      this.entries = new Map();
      this.timer = root.setInterval(() => this.tick(), 100);
    }
    add(key, image, url, onFrame = () => {}, onError = () => {}) {
      const old = this.entries.get(key);
      if (old && old.url === url && old.image === image) return;
      this.remove(key);
      this.entries.set(key, {image, url, onFrame, onError, due: 0, busy: false});
      this.tick();
    }
    remove(key) {
      const entry = this.entries.get(key);
      if (!entry) return;
      this.entries.delete(key);
      entry.controller?.abort();
      entry.image.removeAttribute('src');
      if (entry.objectUrl) this.urls.revokeObjectURL(entry.objectUrl);
    }
    tick() {
      const now = Date.now();
      const waiting = [...this.entries.entries()]
        .filter(([, entry]) => !entry.busy && entry.due <= now)
        .sort((a, b) => a[1].due - b[1].due);
      for (const [key, entry] of waiting) {
        if (this.active >= this.limit) break;
        this.read(key, entry);
      }
    }
    async read(key, entry) {
      entry.busy = true;
      this.active++;
      const controller = new AbortController();
      entry.controller = controller;
      const timeout = root.setTimeout(() => controller.abort(), 5000);
      let objectUrl;
      try {
        const separator = entry.url.includes('?') ? '&' : '?';
        const response = await this.fetch(`${entry.url}${separator}t=${Date.now()}`, {
          cache: 'no-store', signal: controller.signal
        });
        if (!response.ok) throw new Error(`Camera frame unavailable (HTTP ${response.status})`);
        const blob = await response.blob();
        if (!blob.type.startsWith('image/')) throw new Error('Backend did not return a camera image');
        objectUrl = this.urls.createObjectURL(blob);
        const decoded = this.createImage();
        decoded.src = objectUrl;
        await decoded.decode();
        if (this.entries.get(key) !== entry || controller.signal.aborted) return;
        const previous = entry.objectUrl;
        entry.image.src = objectUrl;
        entry.objectUrl = objectUrl;
        objectUrl = null;
        if (previous) this.urls.revokeObjectURL(previous);
        entry.due = Date.now() + 100;
        entry.onFrame();
      } catch (error) {
        if (this.entries.get(key) === entry) {
          entry.due = Date.now() + 1500;
          entry.onError(error);
        }
      } finally {
        root.clearTimeout(timeout);
        if (objectUrl) this.urls.revokeObjectURL(objectUrl);
        entry.busy = false;
        this.active--;
        this.tick();
      }
    }
    close() {
      root.clearInterval(this.timer);
      for (const key of [...this.entries.keys()]) this.remove(key);
    }
  }
  root.CameraFrames = CameraFrames;
  if (typeof module !== 'undefined') module.exports = CameraFrames;
})(typeof window !== 'undefined' ? window : globalThis);
