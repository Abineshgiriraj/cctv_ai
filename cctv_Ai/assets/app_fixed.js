(() => {
  const q = (s, r = document) => r.querySelector(s);
  const qa = (s, r = document) => Array.from(r.querySelectorAll(s));
  const cfg = window.CCTV_UI_CONFIG || {};
  const baseUrl = cfg.baseUrl || 'http://127.0.0.1:5000';
  const healthUrl = cfg.healthUrl || `${baseUrl}/health`;
  const cameras = Array.isArray(cfg.cameras) ? cfg.cameras : [];
  let frameLoader = new window.CameraFrames();
  window.addEventListener('pagehide', () => frameLoader.close());
  let livePage = 1;
  let livePageSize = 2;
  let visibleCameraKeys = [];
  let recorderFilter = '';
  let cameraHealthFilter = '';
  const selectedCameraKeys = new Set();
  let latestHealthData = null;
  const viewKey = `cctv-live:${location.pathname.replace(/[^/]*$/, '')}`;
  function saveLiveView() {
    try { localStorage.setItem(viewKey, JSON.stringify({page: livePage, size: livePageSize,
      recorder: recorderFilter, health: cameraHealthFilter, selected: [...selectedCameraKeys]})); } catch (_) {}
  }
  function restoreLiveView() {
    try {
      const state = JSON.parse(localStorage.getItem(viewKey) || 'null');
      if (!state || typeof state !== 'object') return;
      livePage = Number.isInteger(state.page) && state.page > 0 ? state.page : 1;
      if ([2, 4, 6, 8, 10].includes(state.size)) livePageSize = state.size;
      recorderFilter = cameras.some(c => c.ip === state.recorder) ? state.recorder : '';
      cameraHealthFilter = ['', 'online', 'offline', 'ai_ready', 'ai_error'].includes(state.health) ? state.health : '';
      selectedCameraKeys.clear();
      for (const key of Array.isArray(state.selected) ? state.selected : []) {
        if (cameras.some(c => c.camera_key === key)) selectedCameraKeys.add(key);
      }
    } catch (_) {}
  }
  window.addEventListener('pageshow', event => {
    if (!event.persisted) return;
    frameLoader = new window.CameraFrames();
    applyCameraPage();
  });

  const setText = (id, value) => { const el = q(`#${id}`); if (el) el.textContent = value; };
  const fmt = (value) => Number(value || 0).toLocaleString('en-IN');
  const esc = (value) => String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');

  q('#menu')?.addEventListener('click', () => document.body.classList.toggle('nav-open'));

  const cameraCard = (number) => q(`.operator-camera-card[data-camera-number="${number}"]`);

  function cameraMatchesHealth(cam) {
    if (!cameraHealthFilter || !latestHealthData) return true;
    const st = latestHealthData?.cameras?.[cam.camera_key] || {};
    const ai = st?.ai || {};
    if (cameraHealthFilter === 'online') return !!st.connected;
    if (cameraHealthFilter === 'offline') return !st.connected;
    if (cameraHealthFilter === 'ai_ready') {
      return ai.age_seconds !== null && ai.age_seconds !== undefined && !ai.last_error;
    }
    if (cameraHealthFilter === 'ai_error') return !!ai.last_error;
    return true;
  }

  function filteredCameras({ignoreSelection = false} = {}) {
    return cameras.filter(cam => {
      if (recorderFilter && cam.ip !== recorderFilter) return false;
      if (!cameraMatchesHealth(cam)) return false;
      if (!ignoreSelection && selectedCameraKeys.size && !selectedCameraKeys.has(cam.camera_key)) return false;
      return true;
    });
  }

  function updateCameraPickerLabel() {
    const count = selectedCameraKeys.size;
    setText('cameraPickerLabel', count ? `${count} selected` : 'Select cameras');
    setText(
      'cameraSelectionCount',
      count ? `${count} camera${count === 1 ? '' : 's'} selected` : '0 selected · showing normal camera list'
    );
  }

  function updatePickerHealth() {
    qa('.camera-picker-item').forEach(item => {
      const key = item.dataset.cameraKey;
      const st = latestHealthData?.cameras?.[key] || {};
      item.classList.toggle('is-online', !!st.connected);
      item.classList.toggle('is-offline', !!latestHealthData && !st.connected);
      item.classList.toggle('has-ai-error', !!st?.ai?.last_error);
      item.dataset.health = st.connected ? 'online' : (latestHealthData ? 'offline' : 'unknown');
    });
  }

  function applyPickerSearch() {
    const term = (q('#cameraPickerSearch')?.value || '').trim().toLowerCase();
    qa('.camera-picker-item').forEach(item => {
      const key = item.dataset.cameraKey;
      const cam = cameras.find(row => row.camera_key === key);
      const allowedByRecorder = !recorderFilter || cam?.ip === recorderFilter;
      const allowedByHealth = cam ? cameraMatchesHealth(cam) : true;
      const allowedBySearch = !term || (item.dataset.search || '').includes(term);
      item.classList.toggle('picker-item-hidden', !(allowedByRecorder && allowedByHealth && allowedBySearch));
    });
  }

  function setCameraSelection(keys) {
    selectedCameraKeys.clear();
    keys.forEach(key => {
      if (cameras.some(cam => cam.camera_key === key)) selectedCameraKeys.add(key);
    });
    qa('#cameraPickerList input[type="checkbox"]').forEach(input => {
      input.checked = selectedCameraKeys.has(input.value);
    });
    updateCameraPickerLabel();
    livePage = 1;
    applyCameraPage();
  }

  function pageCameraRows() {
    const rows = filteredCameras();
    const start = (livePage - 1) * livePageSize;
    return rows.slice(start, start + livePageSize);
  }

  async function syncBackendFocus(keys) {
    if (!q('#cameraPagination')) return;
    try {
      const params = new URLSearchParams({
        keys: keys.join(','),
        t: String(Date.now())
      });
      const response = await fetch(`${baseUrl}/system/active_cameras?${params.toString()}`, {
        method: 'GET',
        cache: 'no-store'
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (!data.ok) throw new Error(data.error || 'Camera activation failed');
    } catch (err) {
      console.warn('Unable to update active camera page', err);
    }
  }

  function snapshotUrl(img, mode) {
    const url = mode === 'ai' ? img.dataset.aiStreamUrl : img.dataset.rawStreamUrl;
    const endpoint = (url || '').replace('/tracked_feed/', '/ai_snapshot/').replace('/video_feed/', '/snapshot/');
    return endpoint && mode === 'ai' ? `${endpoint}?fallback=1` : endpoint;
  }

  function loadVisibleStream(number) {
    const img = q(`#cameraStream${number}`);
    if (!img) return;
    const mode = img.dataset.streamMode || 'ai';
    const url = snapshotUrl(img, mode);
    if (!url) return;
    frameLoader.add(`camera-${number}`, img, url, source => {
      const firstFrame = !img.dataset.frameLoadedAt || img.dataset.frameSource !== source;
      img.dataset.frameSource = source;
      img.dataset.frameLoadedAt = String(Date.now());
      if (firstFrame) setCameraState(number, true, mode === 'ai' ? (source === 'raw' ? 'RAW LIVE · AI WAIT' : 'AI LIVE') : 'RAW LIVE');
    }, error => {
      delete img.dataset.frameLoadedAt;
      setCameraState(number, false, 'FRAME RETRY');
      const message = q(`#streamMessage${number} span`);
      if (message) message.textContent = `${error.message}. Retrying automatically…`;
    });
  }

  function unloadHiddenStream(number) {
    const img = q(`#cameraStream${number}`);
    if (!img) return;
    frameLoader.remove(`camera-${number}`);
    delete img.dataset.frameLoadedAt;
  }

  function applyCameraPage() {
    const pager = q('#cameraPagination');
    if (!pager) return;

    const rows = filteredCameras();
    const totalPages = Math.max(1, Math.ceil(rows.length / livePageSize));
    livePage = Math.min(Math.max(1, livePage), totalPages);
    const start = (livePage - 1) * livePageSize;
    const end = Math.min(rows.length, start + livePageSize);
    const pageRows = rows.slice(start, end);
    visibleCameraKeys = pageRows.map(cam => cam.camera_key);
    const visibleSet = new Set(visibleCameraKeys);
    const grid = q('.operator-camera-grid');
    if (grid) grid.dataset.pageSize = String(livePageSize);

    cameras.forEach((cam, index) => {
      const number = index + 1;
      const visible = visibleSet.has(cam.camera_key);
      const card = cameraCard(number);
      card?.classList.toggle('camera-page-hidden', !visible);
      if (visible) loadVisibleStream(number);
      else unloadHiddenStream(number);
    });

    const prefix = recorderFilter ? `${recorderFilter} · ` : '';
    const selectionText = selectedCameraKeys.size ? ` · ${selectedCameraKeys.size} selected` : '';
    const healthText = cameraHealthFilter ? ` · ${cameraHealthFilter.replace('_', ' ')}` : '';
    setText('cameraPageSummary', rows.length ? `${prefix}Cameras ${start + 1}-${end}${selectionText}${healthText}` : `${prefix}No cameras match filters`);
    setText('cameraPageConfigured', `${rows.length} displayed by filters · ${cameras.length} total configured`);
    setText('cameraPageLabel', `Page ${livePage} / ${totalPages}`);
    const prev = q('#cameraPrevPage');
    const next = q('#cameraNextPage');
    if (prev) prev.disabled = livePage <= 1;
    if (next) next.disabled = livePage >= totalPages;

    saveLiveView();
    syncBackendFocus(visibleCameraKeys);
  }

  function initLivePagination() {
    if (!q('#cameraPagination')) return;
    const size = q('#cameraPageSize');
    const recorder = q('#cameraRecorderFilter');
    const health = q('#cameraHealthFilter');
    const picker = q('#cameraPicker');
    const pickerToggle = q('#cameraPickerToggle');
    const pickerMenu = q('#cameraPickerMenu');

    const requestedPageSize = Number(size?.value || 2);
    livePageSize = [2, 4, 6, 8, 10].includes(requestedPageSize) ? requestedPageSize : 2;
    recorderFilter = recorder?.value || '';
    cameraHealthFilter = health?.value || '';
    restoreLiveView();
    if (size) size.value = String(livePageSize);
    if (recorder) recorder.value = recorderFilter;
    if (health) health.value = cameraHealthFilter;
    qa('#cameraPickerList input[type="checkbox"]').forEach(input => {
      input.checked = selectedCameraKeys.has(input.value);
    });

    pickerToggle?.addEventListener('click', e => {
      e.stopPropagation();
      const open = picker?.classList.toggle('open');
      pickerToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) {
        applyPickerSearch();
        q('#cameraPickerSearch')?.focus();
      }
    });
    pickerMenu?.addEventListener('click', e => e.stopPropagation());
    document.addEventListener('click', () => {
      picker?.classList.remove('open');
      pickerToggle?.setAttribute('aria-expanded', 'false');
    });

    q('#cameraPickerSearch')?.addEventListener('input', applyPickerSearch);
    q('#cameraPickerList')?.addEventListener('change', e => {
      const input = e.target.closest('input[type="checkbox"]');
      if (!input) return;
      if (input.checked) selectedCameraKeys.add(input.value);
      else selectedCameraKeys.delete(input.value);
      updateCameraPickerLabel();
      livePage = 1;
      applyCameraPage();
    });
    q('#cameraSelectVisible')?.addEventListener('click', () => {
      const keys = qa('.camera-picker-item:not(.picker-item-hidden) input[type="checkbox"]').map(input => input.value);
      setCameraSelection(keys);
    });
    q('#cameraClearSelection')?.addEventListener('click', () => setCameraSelection([]));
    recorder?.addEventListener('change', () => {
      recorderFilter = recorder.value || '';
      livePage = 1;
      applyPickerSearch();
      applyCameraPage();
    });
    health?.addEventListener('change', () => {
      cameraHealthFilter = health.value || '';
      livePage = 1;
      applyPickerSearch();
      applyCameraPage();
    });
    size?.addEventListener('change', () => {
      const requested = Number(size.value);
      livePageSize = [2, 4, 6, 8, 10].includes(requested) ? requested : 2;
      livePage = 1;
      applyCameraPage();
    });
    q('#cameraPrevPage')?.addEventListener('click', () => {
      if (livePage > 1) {
        livePage--;
        applyCameraPage();
      }
    });
    q('#cameraNextPage')?.addEventListener('click', () => {
      const totalPages = Math.max(1, Math.ceil(filteredCameras().length / livePageSize));
      if (livePage < totalPages) {
        livePage++;
        applyCameraPage();
      }
    });
    updateCameraPickerLabel();
    applyPickerSearch();
    applyCameraPage();
  }


  function ensureCameraFocusModal() {
    let modal = q('#cameraFocusModal');
    if (modal) return modal;

    modal = document.createElement('div');
    modal.className = 'camera-focus-modal';
    modal.id = 'cameraFocusModal';
    modal.innerHTML = `
      <div class="camera-focus-shell" role="dialog" aria-modal="true" aria-label="Camera live view">
        <div class="camera-focus-head">
          <span class="live-dot"></span>
          <div class="camera-focus-title">
            <b id="cameraFocusTitle">Camera</b>
            <span id="cameraFocusMeta"></span>
          </div>
          <div class="camera-focus-controls">
            <button type="button" data-focus-action="zoom-out" title="Zoom out"><i class="bi bi-dash-lg"></i></button>
            <button type="button" data-focus-action="reset" title="Reset zoom">100%</button>
            <button type="button" data-focus-action="zoom-in" title="Zoom in"><i class="bi bi-plus-lg"></i></button>
            <button type="button" data-focus-action="close" title="Close"><i class="bi bi-x-lg"></i></button>
          </div>
        </div>
        <div class="camera-focus-body" id="cameraFocusViewport">
          <img id="cameraFocusImage" alt="Expanded live camera">
          <div class="camera-focus-loading" id="cameraFocusLoading"><i class="bi bi-camera-video"></i><span>Loading camera...</span></div>
        </div>
        <div class="camera-focus-foot">
          <span><i class="bi bi-mouse"></i> Mouse wheel: zoom · Drag: move image · Double-click: reset</span>
          <span id="cameraFocusZoom">100%</span>
        </div>
      </div>`;
    document.body.appendChild(modal);

    const viewport = q('#cameraFocusViewport', modal);
    const image = q('#cameraFocusImage', modal);
    let scale = 1;
    let translateX = 0;
    let translateY = 0;
    let dragging = false;
    let startX = 0;
    let startY = 0;

    const applyTransform = () => {
      image.style.transform = `translate(${translateX}px, ${translateY}px) scale(${scale})`;
      setText('cameraFocusZoom', `${Math.round(scale * 100)}%`);
    };
    const reset = () => {
      scale = 1;
      translateX = 0;
      translateY = 0;
      applyTransform();
    };
    const close = () => {
      modal.classList.remove('open');
      frameLoader.remove('focus');
      document.body.classList.remove('camera-modal-open');
      reset();
    };
    const zoom = amount => {
      scale = Math.min(5, Math.max(0.5, scale + amount));
      applyTransform();
    };

    modal.addEventListener('click', e => {
      const action = e.target.closest('[data-focus-action]')?.dataset.focusAction;
      if (action === 'close') close();
      else if (action === 'zoom-in') zoom(0.25);
      else if (action === 'zoom-out') zoom(-0.25);
      else if (action === 'reset') reset();
      else if (e.target === modal) close();
    });
    viewport.addEventListener('wheel', e => {
      e.preventDefault();
      zoom(e.deltaY < 0 ? 0.15 : -0.15);
    }, {passive:false});
    viewport.addEventListener('dblclick', e => {
      e.preventDefault();
      reset();
    });
    image.addEventListener('load', () => q('#cameraFocusLoading', modal)?.classList.add('hidden'));
    image.addEventListener('error', () => {
      const loading = q('#cameraFocusLoading', modal);
      loading?.classList.remove('hidden');
      if (loading) loading.innerHTML = '<i class="bi bi-exclamation-triangle"></i><span>Unable to open this camera stream.</span>';
    });
    image.addEventListener('mousedown', e => {
      dragging = true;
      startX = e.clientX - translateX;
      startY = e.clientY - translateY;
      image.classList.add('dragging');
      e.preventDefault();
    });
    window.addEventListener('mousemove', e => {
      if (!dragging) return;
      translateX = e.clientX - startX;
      translateY = e.clientY - startY;
      applyTransform();
    });
    window.addEventListener('mouseup', () => {
      dragging = false;
      image.classList.remove('dragging');
    });
    document.addEventListener('keydown', e => {
      if (!modal.classList.contains('open')) return;
      if (e.key === 'Escape') close();
      if (e.key === '+' || e.key === '=') zoom(0.25);
      if (e.key === '-') zoom(-0.25);
      if (e.key === '0') reset();
    });

    modal._cameraReset = reset;
    return modal;
  }

  function openCameraFocus(number) {
    const cam = cameras[number - 1];
    const source = q(`#cameraStream${number}`);
    if (!cam || !source) return;

    const modal = ensureCameraFocusModal();
    const image = q('#cameraFocusImage', modal);
    const loading = q('#cameraFocusLoading', modal);
    const mode = source.dataset.streamMode || 'ai';
    const url = snapshotUrl(source, mode);
    if (!url) return;

    setText('cameraFocusTitle', cam.name || `Camera ${number}`);
    setText('cameraFocusMeta', `${cam.area || ''} · ${cam.ip || ''} · CH${cam.channel || ''} · ${mode.toUpperCase()}`);
    if (loading) {
      loading.innerHTML = '<i class="bi bi-camera-video"></i><span>Loading camera...</span>';
      loading.classList.remove('hidden');
    }
    modal._cameraReset?.();
    frameLoader.remove('focus');
    frameLoader.add('focus', image, url,
      () => loading?.classList.add('hidden'),
      error => {
        loading?.classList.remove('hidden');
        const text = q('span', loading);
        if (text) text.textContent = `${error.message}. Retrying…`;
      });
    modal.classList.add('open');
    document.body.classList.add('camera-modal-open');
  }

  window.openCameraFocus = openCameraFocus;

  qa('[data-camera-popup]').forEach(frame => {
    frame.addEventListener('click', () => {
      const card = frame.closest('.operator-camera-card');
      const number = Number(card?.dataset.cameraNumber || 0);
      if (number) openCameraFocus(number);
    });
  });

  function setCameraState(number, isLive, label) {
    const card = q(`.operator-camera-card[data-camera-number="${number}"]`);
    card?.classList.toggle('is-live', !!isLive);
    card?.classList.toggle('is-offline', label === 'OFFLINE');
    card?.classList.toggle('is-standby', label === 'STANDBY');
    setText(`cameraState${number}`, label);
    q(`#streamMessage${number}`)?.classList.toggle('hidden', !!isLive);
  }

  function setSystemState(
    liveCount,
    aiLiveCount,
    aiErrors,
    reachable,
    activeTotal = cameras.length,
    monitorAll = false,
    monitoredTotal = cameras.length,
    connectedTotal = 0,
    aiProcessedTotal = 0
  ) {
    const total = cameras.length;
    const pill = q('#systemLive');
    const label = pill ? q('span', pill) : null;
    pill?.classList.remove('offline', 'pending');
    if (!reachable) {
      pill?.classList.add('offline');
      if (label) label.textContent = 'BACKEND OFFLINE';
      setText('activeCameraCount', `0 / ${total}`);
      setText('aiDetectionStatus', 'OFFLINE');
      return;
    }

    if (monitorAll) {
      if (label) label.textContent = `MONITORING ${connectedTotal}/${monitoredTotal}`;
      if (connectedTotal < monitoredTotal) pill?.classList.add('pending');
      setText('activeCameraCount', `${connectedTotal} / ${monitoredTotal}`);
      setText(
        'aiDetectionStatus',
        aiErrors ? 'AI WARNING' : `AI ${aiProcessedTotal}/${monitoredTotal}`
      );
      setText(
        'aiDetectionText',
        `All ${monitoredTotal} cameras are scheduled for background AI · ${connectedTotal} RTSP connected · ${aiProcessedTotal} AI processed`
      );
      return;
    }

    if (liveCount === activeTotal && activeTotal > 0) {
      if (label) label.textContent = total > activeTotal ? `PAGE LIVE ${liveCount}/${activeTotal}` : 'SYSTEM LIVE';
    } else {
      pill?.classList.add('pending');
      if (label) label.textContent = `PAGE STREAM ${liveCount}/${activeTotal}`;
    }
    setText('activeCameraCount', `${liveCount} / ${activeTotal}`);
    setText('aiDetectionStatus', aiErrors ? 'AI ERROR' : (aiLiveCount ? 'TRACKING' : 'STARTING'));
    setText('aiDetectionText', `${aiLiveCount}/${activeTotal} active-page AI feeds`);
  }

  async function refreshHealth() {
    try {
      const response = await fetch(`${healthUrl}?t=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      latestHealthData = data;
      updatePickerHealth();

      const visibleRows = visibleCameraKeys.map(key => data?.cameras?.[key] || {});
      const onlineTotal = visibleRows.filter(st => !!st.connected).length;
      const offlineTotal = Math.max(0, visibleRows.length - onlineTotal);
      const aiTotal = visibleRows.filter(st => {
        const ai = st?.ai || {};
        return ai.age_seconds !== null && ai.age_seconds !== undefined && !ai.last_error;
      }).length;
      setText('healthOnlineCount', onlineTotal);
      setText('healthOfflineCount', offlineTotal);
      setText('healthAiCount', aiTotal);
      setText('healthTotalCount', visibleRows.length);

      let liveCount = 0;
      let aiLiveCount = 0;
      let aiErrors = 0;
      const activeKeys = Array.isArray(data.active_camera_keys) && data.active_camera_keys.length
        ? data.active_camera_keys
        : (visibleCameraKeys.length ? visibleCameraKeys : cameras.map(c => c.camera_key));
      const activeSet = new Set(activeKeys);

      cameras.forEach((cam, index) => {
        const number = index + 1;
        const key = cam.camera_key;
        const st = data?.cameras?.[key] || {};
        const ai = st?.ai || {};
        const rawLive = !!(st.connected && st.has_frame);
        const aiLive = !!(ai.model_loaded && ai.has_frame && !ai.last_error);
        const img = q(`#cameraStream${number}`);
        const mode = img?.dataset.streamMode || 'ai';
        const fallback = mode === 'ai' && img?.dataset.frameSource === 'raw';
        const selectedLive = (mode === 'ai' && !fallback ? aiLive : rawLive) && Number(img?.dataset.frameLoadedAt || 0) > Date.now() - 8000;
        const active = activeSet.has(key);

        if (!active) {
          setCameraState(number, false, 'STANDBY');
          return;
        }
        if (rawLive) liveCount++;
        if (aiLive) aiLiveCount++;
        if (ai.last_error) aiErrors++;

        let state = st.connected ? (mode === 'ai' ? 'ONLINE · AI WAIT' : 'RAW CONNECTING') : 'OFFLINE';
        if (mode === 'ai' && selectedLive) state = fallback ? 'RAW LIVE · AI WAIT' : 'AI LIVE';
        if (mode === 'raw' && selectedLive) state = 'RAW LIVE';
        if (mode === 'ai' && ai.last_error) state = 'AI ERROR';
        if (!st.connected && st.last_error) state = 'OFFLINE';
        setCameraState(number, selectedLive, state);
        setText(`vehicleCount${number}`, fmt(ai.vehicles));
        setText(`personCount${number}`, fmt(ai.persons));
        const ms = Number(ai.last_inference_ms);
        setText(`inferenceMs${number}`, Number.isFinite(ms) ? `${Math.round(ms)} ms` : '—');
      });

      if (cameraHealthFilter) {
        applyPickerSearch();
        applyCameraPage();
      }

      setSystemState(
        liveCount,
        aiLiveCount,
        aiErrors,
        true,
        activeSet.size,
        !!data.monitor_all_cameras,
        Number(data.monitored_total || cameras.length),
        Number(data.connected_total || 0),
        Number(data.ai_processed_total || 0)
      );
    } catch (err) {
      cameras.forEach((_, i) => setCameraState(i + 1, false, 'BACKEND OFFLINE'));
      setSystemState(0, 0, 0, false);
      console.warn('Health check failed', err);
    }
  }

  window.switchCameraMode = (number, mode) => {
    const img = q(`#cameraStream${number}`);
    if (!img || !['ai', 'raw'].includes(mode)) return;
    img.dataset.streamMode = mode;
    q(`#aiMode${number}`)?.classList.toggle('active', mode === 'ai');
    q(`#rawMode${number}`)?.classList.toggle('active', mode === 'raw');
    const url = snapshotUrl(img, mode);
    if (!url) return;
    setCameraState(number, false, mode === 'ai' ? 'AI STARTING' : 'RAW CONNECTING');
    delete img.dataset.frameLoadedAt;
    loadVisibleStream(number);
  };

  window.reconnectCamera = (number) => {
    const img = q(`#cameraStream${number}`);
    if (!img) return;
    const mode = img.dataset.streamMode || 'ai';
    const url = snapshotUrl(img, mode);
    if (!url) return;
    setCameraState(number, false, 'RECONNECTING');
    frameLoader.remove(`camera-${number}`);
    delete img.dataset.frameLoadedAt;
    loadVisibleStream(number);
    saveLiveView();
    syncBackendFocus(visibleCameraKeys);
  };

  async function refreshTodaySummary() {
    try {
      const response = await fetch(`${baseUrl}/analytics/today_summary?t=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) return;
      const data = await response.json();
      setText('todayVehicleCount', fmt(data.vehicles));
      setText('todayNoHelmetCount', fmt(data.no_helmet));
      const map = new Map((data.by_camera || []).map(row => [row.camera_key || row.camera_ip, row]));
      cameras.forEach((cam, index) => setText(`cameraTodayCount${index + 1}`, fmt(map.get(cam.camera_key)?.total)));
    } catch (_) {}
  }

  async function fetchReportData() {
    const fromDateEl = q('#reportFromDate') || q('#reportDate');
    const toDateEl = q('#reportToDate');
    const fromTimeEl = q('#reportFromTime');
    const toTimeEl = q('#reportToTime');
    const cameraEl = q('#reportCamera');
    if (!fromDateEl && !q('#tblDailyReport') && !q('#tblCameraReport')) return;

    const today = new Date().toLocaleDateString('en-CA');
    if (fromDateEl && !fromDateEl.value) fromDateEl.value = today;
    if (toDateEl && !toDateEl.value) toDateEl.value = today;
    const params = new URLSearchParams({
      from_date: fromDateEl?.value || today,
      to_date: toDateEl?.value || fromDateEl?.value || today,
      from_time: fromTimeEl?.value || '00:00',
      to_time: toTimeEl?.value || '23:59',
      camera_key: cameraEl?.value || '',
      t: String(Date.now())
    });
    try {
      const res = await fetch(`${baseUrl}/analytics/report?${params.toString()}`, { cache: 'no-store' });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data.detail || data.error || `Report HTTP ${res.status}`);
      const s = data.summary || {};
      const reportMessage = q('#reportMessage');
      if (reportMessage) {
        reportMessage.classList.remove('error');
        reportMessage.classList.add('success');
        reportMessage.textContent = 'Vehicle count report loaded from MySQL.';
      }
      setText('rptTotalPersons', fmt(s.person));
      setText('rptTotalVehicles', fmt(s.total));
      setText('rptTotalCars', fmt(s.car));
      setText('rptTotalMotorcycles', fmt(s.motorcycle));
      setText('rptTotalBuses', fmt(s.bus));
      setText('rptTotalTrucks', fmt(s.truck));
      setText('rptTotalBicycles', fmt(s.bicycle));
      setText('rptTotalOther', fmt(s.other));
      setText('rptGrandTotal', fmt(s.grand_total));

      const daily = q('#tblDailyReport tbody');
      if (daily) daily.innerHTML = (data.daily || []).map(r => `<tr><td>${esc(r.date)}</td><td>${fmt(r.person)}</td><td>${fmt(r.car)}</td><td>${fmt(r.motorcycle)}</td><td>${fmt(r.bus)}</td><td>${fmt(r.truck)}</td><td>${fmt(r.bicycle)}</td><td>${fmt(r.grand_total)}</td></tr>`).join('') || '<tr><td colspan="8">No data</td></tr>';
      const hourly = q('#tblHourlyReport tbody') || q('#tblHourlyBreakdown tbody');
      if (hourly) hourly.innerHTML = (data.hourly || []).map(r => `<tr><td>${esc(r.date)}</td><td>${esc(r.hour)}</td><td>${fmt(r.person)}</td><td>${fmt(r.car)}</td><td>${fmt(r.motorcycle)}</td><td>${fmt(r.bus)}</td><td>${fmt(r.truck)}</td><td>${fmt(r.bicycle)}</td><td>${fmt(r.grand_total)}</td></tr>`).join('') || '<tr><td colspan="9">No data</td></tr>';
      const cameraBody = q('#tblCameraReport tbody') || q('#tblCameraBreakdown tbody');
      if (cameraBody) cameraBody.innerHTML = (data.by_camera || []).map(r => {
        const key = r.camera_key || r.camera_ip;
        const cam = cameras.find(c => c.camera_key === key);
        return `<tr><td><b>${esc(cam?.name || key)}</b></td><td><code>${esc(key)}</code></td><td>${fmt(r.person)}</td><td>${fmt(r.car)}</td><td>${fmt(r.motorcycle)}</td><td>${fmt(r.bus)}</td><td>${fmt(r.truck)}</td><td>${fmt(r.bicycle)}</td><td>${fmt(r.grand_total)}</td></tr>`;
      }).join('') || '<tr><td colspan="9">No data</td></tr>';
    } catch (err) {
      console.warn('Report query failed', err);
      const reportMessage = q('#reportMessage');
      if (reportMessage) {
        reportMessage.classList.remove('success');
        reportMessage.classList.add('error');
        reportMessage.textContent = `Unable to load MySQL report: ${err.message || err}`;
      }
    }
  }

  async function refreshHelmetModelStatus() {
    const el = q('#helmetModelStatus');
    if (!el) return;
    try {
      const res = await fetch(`${baseUrl}/advanced/status?t=${Date.now()}`, { cache: 'no-store' });
      const data = await res.json().catch(() => ({}));
      const helmet = data?.models?.helmet || {};
      el.classList.remove('error', 'success');
      if (res.ok && helmet.loaded && helmet.no_helmet_supported === false) {
        el.classList.add('error');
        el.textContent = 'Helmet model has no recognized no-helmet class. Check the model labels.';
      } else if (res.ok && helmet.loaded) {
        const classes = helmet.classes ? Object.values(helmet.classes).join(', ') : '';
        el.classList.add('success');
        el.textContent = `Helmet model ready${classes ? ` · Classes: ${classes}` : ''}`;
      } else {
        el.classList.add('error');
        el.textContent = `Helmet model not loaded · ${helmet.resolved_path || helmet.configured_path || 'models/helmet.pt'}`;
      }
    } catch (err) {
      el.classList.add('error');
      el.textContent = `Unable to read helmet model status: ${err.message || err}`;
    }
  }

  async function fetchViolations() {
    const grid = q('#violationsGrid');
    if (!grid) return;
    try {
      const res = await fetch(`${baseUrl}/analytics/recent_violations?limit=20&t=${Date.now()}`, { cache: 'no-store' });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
      const rows = data.violations || [];
      setText('violationStatus', rows.length ? '' : 'No recent violations found.');
      grid.innerHTML = rows.map(v => {
        const key = v.camera_key || v.camera_ip;
        const cam = cameras.find(c => c.camera_key === key);
        const plate = v.plate_number || 'Plate not read';
        return `<article class="violation-card">
          <div class="violation-image"><img src="${baseUrl}/analytics/violation_image/${v.id}/evidence" loading="lazy" alt="Violation evidence"></div>
          <div class="violation-body">
            <b>${esc(cam?.name || key)}</b>
            <div class="violation-meta"><span>${esc(cam?.area || '')}</span><span>${esc(v.captured_at || '')}</span></div>
            <span class="status-chip danger">NO HELMET</span>
            <span class="plate-pill">${esc(plate)}</span>
          </div>
        </article>`;
      }).join('');
    } catch (err) {
      console.warn('Violation query failed', err);
      const status = q('#violationStatus');
      if (status) {
        status.classList.add('error');
        status.textContent = `Unable to load helmet violations: ${err.message || err}`;
      }
    }
  }

  q('#reportFromDate')?.addEventListener('change', fetchReportData);
  q('#reportToDate')?.addEventListener('change', fetchReportData);
  q('#reportFromTime')?.addEventListener('change', fetchReportData);
  q('#reportToTime')?.addEventListener('change', fetchReportData);
  q('#reportCamera')?.addEventListener('change', fetchReportData);
  q('#btnRefreshReport')?.addEventListener('click', fetchReportData);
  q('#btnRefreshViolations')?.addEventListener('click', fetchViolations);

  // Lightbox feature
  const lightbox = document.createElement('div');
  lightbox.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.9);z-index:9999;display:none;align-items:center;justify-content:center;flex-direction:column;';
  
  const imgContainer = document.createElement('div');
  imgContainer.style.cssText = 'width:90%;height:90%;display:flex;align-items:center;justify-content:center;overflow:hidden;position:relative;';
  
  const lightboxImg = document.createElement('img');
  lightboxImg.style.cssText = 'max-width:100%;max-height:100%;object-fit:contain;transition:transform 0.2s ease;cursor:grab;';
  
  const hint = document.createElement('div');
  hint.style.cssText = 'color:#aaa;margin-top:15px;font-family:sans-serif;font-size:12px;';
  hint.textContent = 'Scroll to zoom in/out. Click outside to close.';
  
  imgContainer.appendChild(lightboxImg);
  lightbox.appendChild(imgContainer);
  lightbox.appendChild(hint);
  document.body.appendChild(lightbox);

  let scale = 1;
  let isDragging = false;
  let startX, startY, translateX = 0, translateY = 0;

  const updateTransform = () => {
    lightboxImg.style.transform = `translate(${translateX}px, ${translateY}px) scale(${scale})`;
  };

  window.openLightbox = (url) => {
    lightboxImg.src = url;
    scale = 1; translateX = 0; translateY = 0;
    updateTransform();
    lightbox.style.display = 'flex';
    document.body.style.overflow = 'hidden';
  };

  document.addEventListener('click', e => {
    if (e.target.matches('.violation-card img, .violation-image img, .road-evidence-card img')) {
      window.openLightbox(e.target.src);
    } else if (e.target === lightbox || e.target === imgContainer) {
      lightbox.style.display = 'none';
      document.body.style.overflow = '';
    }
  });

  imgContainer.addEventListener('wheel', e => {
    e.preventDefault();
    scale += e.deltaY * -0.002;
    scale = Math.min(Math.max(0.5, scale), 5);
    updateTransform();
  });

  lightboxImg.addEventListener('mousedown', e => {
    isDragging = true;
    startX = e.clientX - translateX;
    startY = e.clientY - translateY;
    lightboxImg.style.cursor = 'grabbing';
    e.preventDefault();
  });

  window.addEventListener('mousemove', e => {
    if (!isDragging) return;
    translateX = e.clientX - startX;
    translateY = e.clientY - startY;
    updateTransform();
  });

  window.addEventListener('mouseup', () => {
    isDragging = false;
    lightboxImg.style.cursor = 'grab';
  });

  initLivePagination();
  refreshHealth();
  refreshTodaySummary();
  fetchReportData();
  fetchViolations();
  refreshHelmetModelStatus();
  setInterval(refreshHealth, 3000);
  setInterval(refreshTodaySummary, 5000);
  setInterval(fetchReportData, 5000);
  setInterval(fetchViolations, 5000);
  setInterval(refreshHelmetModelStatus, 15000);
})();


