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

  function ensureTrafficUi() {
    if (!q('link[data-traffic-css]')) {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = 'assets/traffic.css';
      link.dataset.trafficCss = '1';
      document.head.appendChild(link);
    }
    if (q('#traffic-analytics')) return;

    const integration = q('.integration-panel');
    if (!integration) return;
    const panel = document.createElement('article');
    panel.className = 'panel traffic-panel';
    panel.id = 'traffic-analytics';
    panel.innerHTML = `
      <div class="panel-head">
        <div><h2>Vehicle Counts Today</h2><p>Persistent crossing counts. Each tracked vehicle is counted once when it crosses the AI count line.</p></div>
        <div><span class="badge live"><i></i> BYTETRACK COUNTER</span></div>
      </div>
      <div class="traffic-count-grid">
        <div class="traffic-count-card"><small>Motorcycle</small><b id="countMotorcycle">0</b><span>Today</span></div>
        <div class="traffic-count-card"><small>Car</small><b id="countCar">0</b><span>Today</span></div>
        <div class="traffic-count-card"><small>Bus</small><b id="countBus">0</b><span>Today</span></div>
        <div class="traffic-count-card"><small>Truck</small><b id="countTruck">0</b><span>Today</span></div>
        <div class="traffic-count-card"><small>Bicycle</small><b id="countBicycle">0</b><span>Today</span></div>
        <div class="traffic-count-card"><small>Total vehicles</small><b id="countVehicleTotal">0</b><span>All configured cameras</span></div>
      </div>
      <div class="count-line-note">Counting line: <b id="countLineStatus">Loading…</b> · Counts are stored in the backend SQLite analytics database.</div>
      <div class="panel-head">
        <div><h2>Advanced Detection Model Readiness</h2><p>These detections require custom trained weights; stock COCO yolov8n.pt cannot reliably detect them.</p></div>
      </div>
      <div class="advanced-readiness">
        <div id="helmetReadiness"><i class="bi bi-person-badge"></i><span><b>Helmet / No Helmet</b><small>Checking model slot…</small></span></div>
        <div id="roadDamageReadiness"><i class="bi bi-cone-striped"></i><span><b>Road Damage / Pothole</b><small>Checking model slot…</small></span></div>
        <div id="roadObstructionReadiness"><i class="bi bi-signpost-split"></i><span><b>Fallen Tree / Road Obstruction</b><small>Checking model slot…</small></span></div>
      </div>`;
    integration.parentNode.insertBefore(panel, integration);
  }

  ensureTrafficUi();

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
    const cumulative = ai?.session_vehicle_counts || {};
    const cumulativeReadable = Object.entries(cumulative)
      .filter(([, value]) => Number(value) > 0)
      .map(([name, value]) => `${name.replace(/\b\w/g, (c) => c.toUpperCase())}: ${value}`)
      .join(' · ');
    setText(
      `classCounts${number}`,
      `${readable || 'No target objects in current frame'}${cumulativeReadable ? `  |  Session counted: ${cumulativeReadable}` : ''}`
    );

    let aiState = 'Starting';
    if (ai?.last_error) aiState = 'AI Error';
    else if (ai?.has_frame) aiState = 'Tracking';
    else if (ai?.model_loaded) aiState = 'Waiting for frame';
    setText(`aiState${number}`, aiState);
  }

  function updateTraffic(traffic) {
    const today = traffic?.today || {};
    const totals = today?.totals || {};
    setText('countMotorcycle', Number(totals.motorcycle || 0).toLocaleString('en-IN'));
    setText('countCar', Number(totals.car || 0).toLocaleString('en-IN'));
    setText('countBus', Number(totals.bus || 0).toLocaleString('en-IN'));
    setText('countTruck', Number(totals.truck || 0).toLocaleString('en-IN'));
    setText('countBicycle', Number(totals.bicycle || 0).toLocaleString('en-IN'));
    setText('countVehicleTotal', Number(today?.grand_total || 0).toLocaleString('en-IN'));
    const ratio = Number(traffic?.count_line_y_ratio);
    const enabled = Boolean(traffic?.counting_enabled);
    setText('countLineStatus', enabled ? `${Number.isFinite(ratio) ? Math.round(ratio * 100) : '—'}% frame height · enabled` : 'disabled');
  }

  function setReadiness(id, model) {
    const el = q(`#${id}`);
    if (!el) return;
    const small = q('small', el);
    const available = Boolean(model?.available);
    el.classList.toggle('ready', available);
    if (small) small.textContent = available ? 'Model file found · integration slot ready' : 'Custom .pt model required';
  }

  function updateAdvancedModels(models) {
    setReadiness('helmetReadiness', models?.helmet);
    setReadiness('roadDamageReadiness', models?.road_damage);
    setReadiness('roadObstructionReadiness', models?.road_obstruction);
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
      updateTraffic(data?.traffic || {});
      updateAdvancedModels(data?.advanced_models || {});
      setSystemState(liveCount, total, true, aiLiveCount, aiErrors);
    } catch (_) {
      config.cameraIps.forEach((_, index) => {
        setCameraState(index + 1, 'offline', 'BACKEND OFFLINE', NaN);
        updateAiCard(index + 1, {});
      });
      updateTraffic({});
      updateAdvancedModels({});
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
