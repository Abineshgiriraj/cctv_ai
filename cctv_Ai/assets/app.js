(() => {
  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const config = window.CCTV_UI_CONFIG || { healthUrl: 'http://127.0.0.1:5000/health', cameraIps: [] };

  q('#menu')?.addEventListener('click', () => document.body.classList.toggle('nav-open'));
  qa('.sidebar a[href^="#"]:not(.disabled)').forEach((link) => {
    link.addEventListener('click', () => {
      if (window.innerWidth <= 760) document.body.classList.remove('nav-open');
    });
  });

  function tick() {
    const now = new Date().toLocaleTimeString('en-IN', { hour12: true });
    qa('.camera-clock').forEach((clock) => { clock.textContent = now; });
  }
  tick();
  setInterval(tick, 1000);

  function cameraMode(number) {
    return q(`#cameraStream${number}`)?.dataset.streamMode || 'ai';
  }

  function setCameraState(number, state, label, frames) {
    const stateEl = q(`#cameraState${number}`);
    const messageEl = q(`#streamMessage${number}`);
    const frameEl = q(`#frameCount${number}`);
    if (stateEl) {
      stateEl.classList.remove('pending', 'live', 'offline');
      stateEl.classList.add(state);
      const labelEl = q('span', stateEl);
      if (labelEl) labelEl.textContent = label;
    }
    if (messageEl) messageEl.classList.toggle('hidden', state === 'live');
    if (frameEl) frameEl.textContent = Number.isFinite(frames) ? frames.toLocaleString('en-IN') : '—';
  }

  function setText(id, value) {
    const el = q(`#${id}`);
    if (el) el.textContent = value;
  }

  function updateAiCard(number, ai) {
    const persons = Number(ai?.persons || 0);
    const vehicles = Number(ai?.vehicles || 0);
    const inference = Number(ai?.last_inference_ms);
    const fps = Number.isFinite(inference) && inference > 0 ? Math.min(99, 1000 / inference) : NaN;

    setText(`personCount${number}`, persons.toLocaleString('en-IN'));
    setText(`vehicleCount${number}`, vehicles.toLocaleString('en-IN'));
    setText(`aiFps${number}`, Number.isFinite(fps) ? fps.toFixed(1) : '—');
    setText(`detPerson${number}`, persons.toLocaleString('en-IN'));
    setText(`detVehicle${number}`, vehicles.toLocaleString('en-IN'));
    setText(`inferenceMs${number}`, Number.isFinite(inference) ? `${inference.toFixed(0)} ms` : '—');

    const classCounts = ai?.class_counts || {};
    const readable = Object.entries(classCounts)
      .filter(([, value]) => Number(value) > 0)
      .map(([name, value]) => `${name.replace(/\b\w/g, (c) => c.toUpperCase())}: ${value}`)
      .join('  ·  ');
    setText(`classCounts${number}`, readable || 'No target objects in the current AI frame');

    let aiState = 'Starting';
    if (ai?.last_error) aiState = 'AI Error';
    else if (ai?.has_frame) aiState = 'Tracking';
    else if (ai?.model_loaded) aiState = 'Waiting for frame';
    setText(`aiState${number}`, aiState);
  }

  function setSystemState(liveCount, total, backendReachable, aiLiveCount, aiErrors) {
    const system = q('#systemLive');
    const count = q('#activeCameraCount');
    const text = q('#cameraNetworkText');
    const backend = q('#backendHealth');
    if (count) count.innerHTML = `${liveCount} <em>/ ${total}</em>`;

    if (system) {
      system.classList.remove('offline', 'pending');
      const systemLabel = q('span', system);
      if (!backendReachable) {
        system.classList.add('offline');
        if (systemLabel) systemLabel.textContent = 'BACKEND OFFLINE';
        if (text) text.textContent = 'MJPEG server not reachable';
        if (backend) backend.textContent = 'Offline · start start-mjpeg.bat';
      } else if (liveCount === total && total > 0) {
        if (systemLabel) systemLabel.textContent = 'SYSTEM LIVE';
        if (text) text.textContent = 'All configured cameras streaming';
        if (backend) backend.textContent = `Online · raw ${liveCount}/${total} · AI ${aiLiveCount}/${total}`;
      } else {
        system.classList.add('pending');
        if (systemLabel) systemLabel.textContent = 'PARTIAL STREAM';
        if (text) text.textContent = `${liveCount} of ${total} cameras have raw frames`;
        if (backend) backend.textContent = 'Online · waiting for one or more camera frames';
      }
    }

    const aiStatus = q('#aiDetectionStatus');
    const aiText = q('#aiDetectionText');
    if (aiStatus) {
      aiStatus.classList.remove('muted-stat');
      if (!backendReachable) {
        aiStatus.textContent = 'OFFLINE';
        aiStatus.classList.add('muted-stat');
      } else if (aiErrors > 0) {
        aiStatus.textContent = 'ERROR';
        aiStatus.classList.add('muted-stat');
      } else if (aiLiveCount === total && total > 0) {
        aiStatus.textContent = 'TRACKING';
      } else {
        aiStatus.textContent = 'STARTING';
      }
    }
    if (aiText) aiText.textContent = aiErrors > 0 ? 'Check backend console / health endpoint' : `YOLOv8n + ByteTrack · ${aiLiveCount}/${total} AI feeds`;
  }

  async function refreshHealth() {
    const total = config.cameraIps.length;
    try {
      const response = await fetch(`${config.healthUrl}?t=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error('health unavailable');
      const data = await response.json();
      let liveCount = 0;
      let aiLiveCount = 0;
      let aiErrors = 0;

      config.cameraIps.forEach((ip, index) => {
        const number = index + 1;
        const st = data?.cameras?.[ip] || {};
        const ai = st?.ai || {};
        const rawLive = Boolean(st.has_frame && st.connected);
        const aiLive = Boolean(ai.has_frame && ai.model_loaded && !ai.last_error);
        const mode = cameraMode(number);
        const selectedLive = mode === 'ai' ? aiLive : rawLive;
        if (rawLive) liveCount += 1;
        if (aiLive) aiLiveCount += 1;
        if (ai.last_error) aiErrors += 1;

        let label = mode === 'ai' ? 'AI STARTING' : 'RAW LIVE';
        if (mode === 'ai' && ai.last_error) label = 'AI ERROR';
        else if (mode === 'ai' && aiLive) label = 'AI LIVE';
        else if (mode === 'raw' && !rawLive) label = 'NO FRAME';

        setCameraState(number, selectedLive ? 'live' : (ai.last_error && mode === 'ai' ? 'offline' : 'pending'), label, Number(st.frames || 0));
        updateAiCard(number, ai);
      });
      setSystemState(liveCount, total, true, aiLiveCount, aiErrors);
    } catch (_) {
      config.cameraIps.forEach((_, index) => {
        setCameraState(index + 1, 'offline', 'BACKEND OFFLINE', NaN);
        updateAiCard(index + 1, {});
      });
      setSystemState(0, total, false, 0, 0);
    }
  }

  window.switchCameraMode = (number, mode) => {
    const img = q(`#cameraStream${number}`);
    if (!img || !['ai', 'raw'].includes(mode)) return;
    if (img.dataset.streamMode === mode) return;

    const url = mode === 'ai' ? img.dataset.aiStreamUrl : img.dataset.rawStreamUrl;
    img.dataset.streamMode = mode;
    setText(`streamModeLabel${number}`, mode === 'ai' ? 'AI TRACKING LIVE' : 'RAW MJPEG LIVE');
    q(`#aiMode${number}`)?.classList.toggle('active', mode === 'ai');
    q(`#rawMode${number}`)?.classList.toggle('active', mode === 'raw');
    setCameraState(number, 'pending', mode === 'ai' ? 'AI STARTING' : 'CONNECTING', NaN);
    img.src = `${url}?t=${Date.now()}`;
    window.setTimeout(refreshHealth, 800);
  };

  window.reconnectCamera = (number) => {
    const img = q(`#cameraStream${number}`);
    if (!img) return;
    const mode = img.dataset.streamMode || 'ai';
    const base = mode === 'ai' ? img.dataset.aiStreamUrl : img.dataset.rawStreamUrl;
    setCameraState(number, 'pending', 'RECONNECTING', NaN);
    img.src = `${base}?t=${Date.now()}`;
    window.setTimeout(refreshHealth, 1000);
  };

  refreshHealth();
  window.setInterval(refreshHealth, 3000);
})();
