(() => {
  const q = (s, r = document) => r.querySelector(s);
  const qa = (s, r = document) => Array.from(r.querySelectorAll(s));
  const cfg = window.CCTV_UI_CONFIG || {};
  const baseUrl = cfg.baseUrl || 'http://127.0.0.1:5000';
  const healthUrl = cfg.healthUrl || `${baseUrl}/health`;
  const cameras = Array.isArray(cfg.cameras) ? cfg.cameras : [];
  let livePage = 1;
  let livePageSize = 8;
  let visibleCameraKeys = [];

  const setText = (id, value) => { const el = q(`#${id}`); if (el) el.textContent = value; };
  const fmt = (value) => Number(value || 0).toLocaleString('en-IN');
  const esc = (value) => String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');

  q('#menu')?.addEventListener('click', () => document.body.classList.toggle('nav-open'));

  const cameraCard = (number) => q(`.operator-camera-card[data-camera-number="${number}"]`);

  function pageCameraRows() {
    const start = (livePage - 1) * livePageSize;
    return cameras.slice(start, start + livePageSize);
  }

  async function syncBackendFocus(keys) {
    if (!q('#cameraPagination')) return;
    try {
      await fetch(`${baseUrl}/system/active_cameras`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        cache: 'no-store',
        body: JSON.stringify({camera_keys: keys})
      });
    } catch (err) {
      console.warn('Unable to update active camera page', err);
    }
  }

  function loadVisibleStream(number) {
    const img = q(`#cameraStream${number}`);
    if (!img) return;
    const mode = img.dataset.streamMode || 'ai';
    const url = mode === 'ai' ? img.dataset.aiStreamUrl : img.dataset.rawStreamUrl;
    if (!url) return;
    const wanted = `${url}?page=${livePage}&t=${Date.now()}`;
    if (!img.getAttribute('src')) img.src = wanted;
  }

  function unloadHiddenStream(number) {
    const img = q(`#cameraStream${number}`);
    if (!img) return;
    img.removeAttribute('src');
  }

  function applyCameraPage() {
    const pager = q('#cameraPagination');
    if (!pager) return;

    const totalPages = Math.max(1, Math.ceil(cameras.length / livePageSize));
    livePage = Math.min(Math.max(1, livePage), totalPages);
    const start = (livePage - 1) * livePageSize;
    const end = Math.min(cameras.length, start + livePageSize);
    visibleCameraKeys = cameras.slice(start, end).map(cam => cam.camera_key);

    cameras.forEach((cam, index) => {
      const number = index + 1;
      const visible = index >= start && index < end;
      const card = cameraCard(number);
      card?.classList.toggle('camera-page-hidden', !visible);
      if (visible) loadVisibleStream(number);
      else unloadHiddenStream(number);
    });

    setText('cameraPageSummary', cameras.length ? `Cameras ${start + 1}-${end}` : 'No cameras');
    setText('cameraPageConfigured', `${cameras.length} configured`);
    setText('cameraPageLabel', `Page ${livePage} / ${totalPages}`);
    const prev = q('#cameraPrevPage');
    const next = q('#cameraNextPage');
    if (prev) prev.disabled = livePage <= 1;
    if (next) next.disabled = livePage >= totalPages;

    syncBackendFocus(visibleCameraKeys);
  }

  function initLivePagination() {
    if (!q('#cameraPagination')) return;
    const size = q('#cameraPageSize');
    livePageSize = Number(size?.value || 8) === 4 ? 4 : 8;
    size?.addEventListener('change', () => {
      livePageSize = Number(size.value) === 4 ? 4 : 8;
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
      const totalPages = Math.max(1, Math.ceil(cameras.length / livePageSize));
      if (livePage < totalPages) {
        livePage++;
        applyCameraPage();
      }
    });
    applyCameraPage();
  }


  function setCameraState(number, isLive, label) {
    const card = q(`.operator-camera-card[data-camera-number="${number}"]`);
    card?.classList.toggle('is-live', !!isLive);
    card?.classList.toggle('is-standby', label === 'STANDBY');
    setText(`cameraState${number}`, label);
    q(`#streamMessage${number}`)?.classList.toggle('hidden', !!isLive);
  }

  function setSystemState(liveCount, aiLiveCount, aiErrors, reachable, activeTotal = cameras.length) {
    const total = cameras.length;
    const pill = q('#systemLive');
    const label = q('span', pill);
    pill?.classList.remove('offline', 'pending');
    if (!reachable) {
      pill?.classList.add('offline');
      if (label) label.textContent = 'BACKEND OFFLINE';
      setText('activeCameraCount', `0 / ${total}`);
      setText('aiDetectionStatus', 'OFFLINE');
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
        const selectedLive = mode === 'ai' ? aiLive : rawLive;
        const active = activeSet.has(key);

        if (!active) {
          setCameraState(number, false, 'STANDBY');
          return;
        }
        if (rawLive) liveCount++;
        if (aiLive) aiLiveCount++;
        if (ai.last_error) aiErrors++;

        let state = mode === 'ai' ? 'AI STARTING' : 'RAW CONNECTING';
        if (mode === 'ai' && aiLive) state = 'AI LIVE';
        if (mode === 'raw' && rawLive) state = 'RAW LIVE';
        if (mode === 'ai' && ai.last_error) state = 'AI ERROR';
        if (mode === 'raw' && !rawLive && st.last_error) state = 'OFFLINE';
        setCameraState(number, selectedLive, state);
        setText(`vehicleCount${number}`, fmt(ai.vehicles));
        setText(`personCount${number}`, fmt(ai.persons));
        const ms = Number(ai.last_inference_ms);
        setText(`inferenceMs${number}`, Number.isFinite(ms) ? `${Math.round(ms)} ms` : '—');
      });

      setSystemState(liveCount, aiLiveCount, aiErrors, true, activeSet.size);
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
    const url = mode === 'ai' ? img.dataset.aiStreamUrl : img.dataset.rawStreamUrl;
    if (!url) return;
    setCameraState(number, false, mode === 'ai' ? 'AI STARTING' : 'RAW CONNECTING');
    img.src = `${url}?t=${Date.now()}`;
  };

  window.reconnectCamera = (number) => {
    const img = q(`#cameraStream${number}`);
    if (!img) return;
    const mode = img.dataset.streamMode || 'ai';
    const url = mode === 'ai' ? img.dataset.aiStreamUrl : img.dataset.rawStreamUrl;
    if (!url) return;
    setCameraState(number, false, 'RECONNECTING');
    img.src = `${url}?reconnect=${Date.now()}`;
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
      if (!res.ok) throw new Error(`Report HTTP ${res.status}`);
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'Report query failed');
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
  setInterval(refreshHealth, 3000);
  setInterval(refreshTodaySummary, 5000);
  setInterval(fetchReportData, 5000);
  setInterval(fetchViolations, 5000);
})();
