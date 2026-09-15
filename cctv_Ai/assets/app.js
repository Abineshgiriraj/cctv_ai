(() => {
  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const config = window.CCTV_UI_CONFIG || {};
  const baseUrl = config.baseUrl || (config.healthUrl || 'http://127.0.0.1:5000/health').replace('/health', '');
  const reportApiUrl = config.reportApiUrl || 'report_api.php';
  const cameraIps = Array.isArray(config.cameraIps) ? config.cameraIps : [];

  function setText(id, value) {
    const el = q(`#${id}`);
    if (el) el.textContent = value;
  }

  function fmt(value) {
    return Number(value || 0).toLocaleString('en-IN');
  }

  function esc(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  q('#menu')?.addEventListener('click', () => document.body.classList.toggle('nav-open'));
  qa('.sidebar a[href^="#"]').forEach((link) => {
    link.addEventListener('click', () => {
      qa('.sidebar a').forEach((item) => item.classList.remove('active'));
      link.classList.add('active');
      if (window.innerWidth <= 760) document.body.classList.remove('nav-open');
    });
  });

  function ensureCameraModal() {
    if (q('#cameraFocusModal')) return;
    const modal = document.createElement('div');
    modal.id = 'cameraFocusModal';
    modal.className = 'camera-focus-modal';
    modal.innerHTML = `
      <div class="camera-focus-shell" role="dialog" aria-modal="true">
        <div class="camera-focus-head">
          <b id="focusCameraTitle">Camera</b>
          <span id="focusCameraIp"></span>
          <button type="button" id="focusClose" aria-label="Close"><i class="bi bi-x-lg"></i></button>
        </div>
        <div class="camera-focus-body"><img id="focusCameraStream" alt="Focused CCTV stream"></div>
        <div class="camera-focus-foot"><i class="live-dot"></i><b id="focusCameraMode">AI TRACKING LIVE</b><span>Press Esc to close</span></div>
      </div>`;
    document.body.appendChild(modal);
    q('#focusClose')?.addEventListener('click', closeCameraModal);
    modal.addEventListener('click', (event) => { if (event.target === modal) closeCameraModal(); });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeCameraModal(); });
  }

  function openCameraModal(number) {
    const img = q(`#cameraStream${number}`);
    const card = img?.closest('.operator-camera-card');
    if (!img || !card) return;
    const mode = img.dataset.streamMode || 'ai';
    const url = mode === 'ai' ? img.dataset.aiStreamUrl : img.dataset.rawStreamUrl;
    setText('focusCameraTitle', `Camera ${number}`);
    setText('focusCameraIp', card.dataset.cameraIp || '');
    setText('focusCameraMode', mode === 'ai' ? 'AI TRACKING LIVE' : 'RAW LIVE');
    const focus = q('#focusCameraStream');
    if (focus) focus.src = `${url}?focus=${Date.now()}`;
    q('#cameraFocusModal')?.classList.add('open');
    document.body.classList.add('modal-open');
  }

  function closeCameraModal() {
    q('#cameraFocusModal')?.classList.remove('open');
    document.body.classList.remove('modal-open');
    q('#focusCameraStream')?.removeAttribute('src');
  }

  ensureCameraModal();
  qa('.operator-camera-card').forEach((card) => {
    const number = Number(card.dataset.cameraNumber || 0);
    q('.operator-camera-frame', card)?.addEventListener('click', () => openCameraModal(number));
  });

  function tickClock() {
    const time = new Date().toLocaleTimeString('en-IN', { hour12: true });
    qa('.camera-clock').forEach((el) => { el.textContent = time; });
  }
  tickClock();
  setInterval(tickClock, 1000);

  function cameraMode(number) {
    return q(`#cameraStream${number}`)?.dataset.streamMode || 'ai';
  }

  function setCameraState(number, isLive, label) {
    const card = q(`.operator-camera-card[data-camera-number="${number}"]`);
    card?.classList.toggle('is-live', Boolean(isLive));
    setText(`cameraState${number}`, label);
    q(`#streamMessage${number}`)?.classList.toggle('hidden', Boolean(isLive));
  }

  function setReadiness(id, model, readyText, missingText) {
    const el = q(`#${id}`);
    if (!el) return;
    const ready = Boolean(model?.available);
    el.classList.toggle('ready', ready);
    const span = q('span', el);
    if (span) span.textContent = ready ? readyText : missingText;
  }

  function updateAdvancedModels(models) {
    setReadiness('helmetReadiness', models?.helmet, 'Helmet model ready', 'Helmet model missing');
    setReadiness('plateReadiness', models?.plate, 'Plate OCR ready', 'Plate model missing');
    setReadiness('roadDamageReadiness', models?.road_damage, 'Road damage ready', 'Road damage model missing');
    setReadiness('roadObstructionReadiness', models?.road_obstruction, 'Road obstruction ready', 'Road obstruction model missing');
  }

  function setSystemState(liveCount, aiLiveCount, aiErrors, reachable) {
    const total = cameraIps.length;
    const system = q('#systemLive');
    const label = q('span', system);
    system?.classList.remove('offline', 'pending');

    if (!reachable) {
      system?.classList.add('offline');
      if (label) label.textContent = 'BACKEND OFFLINE';
      setText('cameraNetworkText', 'Start the CCTV backend');
      setText('aiDetectionStatus', 'OFFLINE');
      setText('aiDetectionText', 'Backend is not reachable');
    } else if (liveCount === total && total > 0) {
      if (label) label.textContent = 'SYSTEM LIVE';
      setText('cameraNetworkText', 'All configured cameras are streaming');
      setText('aiDetectionStatus', aiErrors ? 'AI ERROR' : (aiLiveCount === total ? 'TRACKING' : 'STARTING'));
      setText('aiDetectionText', `${aiLiveCount}/${total} AI feeds active`);
    } else {
      system?.classList.add('pending');
      if (label) label.textContent = 'PARTIAL STREAM';
      setText('cameraNetworkText', `${liveCount}/${total} cameras connected`);
      setText('aiDetectionStatus', aiErrors ? 'AI ERROR' : 'STARTING');
      setText('aiDetectionText', `${aiLiveCount}/${total} AI feeds active`);
    }
    setText('activeCameraCount', `${liveCount} / ${total}`);
  }

  async function refreshHealth() {
    try {
      const response = await fetch(`${config.healthUrl}?t=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error('Health endpoint unavailable');
      const data = await response.json();
      let liveCount = 0;
      let aiLiveCount = 0;
      let aiErrors = 0;

      cameraIps.forEach((ip, index) => {
        const number = index + 1;
        const st = data?.cameras?.[ip] || {};
        const ai = st?.ai || {};
        const rawLive = Boolean(st.connected && st.has_frame);
        const aiLive = Boolean(ai.model_loaded && ai.has_frame && !ai.last_error);
        const mode = cameraMode(number);
        const selectedLive = mode === 'ai' ? aiLive : rawLive;

        if (rawLive) liveCount += 1;
        if (aiLive) aiLiveCount += 1;
        if (ai.last_error) aiErrors += 1;

        let label = mode === 'ai' ? 'AI STARTING' : 'RAW CONNECTING';
        if (mode === 'ai' && aiLive) label = 'AI LIVE';
        if (mode === 'raw' && rawLive) label = 'RAW LIVE';
        if (mode === 'ai' && ai.last_error) label = 'AI ERROR';
        if (!rawLive && mode === 'raw') label = 'OFFLINE';

        setCameraState(number, selectedLive, label);
        setText(`vehicleCount${number}`, fmt(ai.vehicles));
        setText(`personCount${number}`, fmt(ai.persons));
        const inference = Number(ai.last_inference_ms);
        setText(`inferenceMs${number}`, Number.isFinite(inference) ? `${Math.round(inference)} ms` : '—');
      });

      updateAdvancedModels(data?.advanced_models || {});
      setSystemState(liveCount, aiLiveCount, aiErrors, true);
    } catch (_) {
      cameraIps.forEach((_, index) => setCameraState(index + 1, false, 'BACKEND OFFLINE'));
      setSystemState(0, 0, 0, false);
    }
  }

  window.switchCameraMode = (number, mode) => {
    const img = q(`#cameraStream${number}`);
    if (!img || !['ai', 'raw'].includes(mode)) return;
    img.dataset.streamMode = mode;
    q(`#aiMode${number}`)?.classList.toggle('active', mode === 'ai');
    q(`#rawMode${number}`)?.classList.toggle('active', mode === 'raw');
    const url = mode === 'ai' ? img.dataset.aiStreamUrl : img.dataset.rawStreamUrl;
    setCameraState(number, false, mode === 'ai' ? 'AI STARTING' : 'RAW CONNECTING');
    img.src = `${url}?t=${Date.now()}`;
    setTimeout(refreshHealth, 900);
  };

  window.reconnectCamera = (number) => {
    const img = q(`#cameraStream${number}`);
    if (!img) return;
    const mode = img.dataset.streamMode || 'ai';
    const url = mode === 'ai' ? img.dataset.aiStreamUrl : img.dataset.rawStreamUrl;
    setCameraState(number, false, 'RECONNECTING');
    img.src = `${url}?reconnect=${Date.now()}`;
    setTimeout(refreshHealth, 1000);
  };

  async function refreshTodaySummary() {
    try {
      const response = await fetch(`${baseUrl}/analytics/today_summary?t=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error('Today summary unavailable');
      const data = await response.json();
      setText('todayVehicleCount', fmt(data.vehicles));
      setText('todayNoHelmetCount', fmt(data.no_helmet));
      const cameraMap = new Map((data.by_camera || []).map((row) => [row.camera_ip, row]));
      cameraIps.forEach((ip, index) => setText(`cameraTodayCount${index + 1}`, fmt(cameraMap.get(ip)?.total)));
    } catch (_) {
      setText('todayVehicleCount', '—');
      setText('todayNoHelmetCount', '—');
    }
  }

  function renderVehicleSummary(summary = {}) {
    setText('rptTotalVehicles', fmt(summary.total ?? summary.vehicles));
    setText('rptTotalMotorcycles', fmt(summary.motorcycle));
    setText('rptTotalCars', fmt(summary.car));
    setText('rptTotalBuses', fmt(summary.bus));
    setText('rptTotalTrucks', fmt(summary.truck));
    setText('rptTotalBicycles', fmt(summary.bicycle));
  }

  function vehicleCells(row) {
    return `
      <td class="number">${fmt(row.motorcycle)}</td>
      <td class="number">${fmt(row.car)}</td>
      <td class="number">${fmt(row.bus)}</td>
      <td class="number">${fmt(row.truck)}</td>
      <td class="number">${fmt(row.bicycle)}</td>
      <td class="number"><b>${fmt(row.total ?? row.vehicles)}</b></td>`;
  }

  function renderReport(data) {
    renderVehicleSummary(data.summary || {});
    const dailyBody = q('#tblDailyReport tbody');
    if (dailyBody) dailyBody.innerHTML = (data.daily || []).length
      ? data.daily.map((row) => `<tr><td>${esc(row.date)}</td>${vehicleCells(row)}</tr>`).join('')
      : '<tr><td colspan="7" class="empty-row">No vehicle crossings found in this period</td></tr>';

    const hourlyBody = q('#tblHourlyReport tbody');
    if (hourlyBody) hourlyBody.innerHTML = (data.hourly || []).length
      ? data.hourly.map((row) => `<tr><td>${esc(row.date)}</td><td>${esc(row.hour)}</td>${vehicleCells(row)}</tr>`).join('')
      : '<tr><td colspan="8" class="empty-row">No hourly vehicle counts in this period</td></tr>';

    const cameraBody = q('#tblCameraReport tbody');
    if (cameraBody) {
      const cameraNames = new Map(cameraIps.map((ip, index) => [ip, `Camera ${index + 1}`]));
      cameraBody.innerHTML = (data.by_camera || []).length
        ? data.by_camera.map((row) => `<tr><td><b>${esc(cameraNames.get(row.camera_ip) || 'Camera')}</b></td><td><code>${esc(row.camera_ip)}</code></td>${vehicleCells(row)}</tr>`).join('')
        : '<tr><td colspan="8" class="empty-row">No camera counts in this period</td></tr>';
    }
  }

  function clearReportTables(message) {
    const dailyBody = q('#tblDailyReport tbody');
    const hourlyBody = q('#tblHourlyReport tbody');
    const cameraBody = q('#tblCameraReport tbody');
    if (dailyBody) dailyBody.innerHTML = `<tr><td colspan="7" class="empty-row">${esc(message)}</td></tr>`;
    if (hourlyBody) hourlyBody.innerHTML = `<tr><td colspan="8" class="empty-row">${esc(message)}</td></tr>`;
    if (cameraBody) cameraBody.innerHTML = `<tr><td colspan="8" class="empty-row">${esc(message)}</td></tr>`;
  }

  async function fetchReportData() {
    const fromDate = q('#reportFromDate')?.value;
    const toDate = q('#reportToDate')?.value;
    const fromTime = q('#reportFromTime')?.value || '00:00';
    const toTime = q('#reportToTime')?.value || '23:59';
    const cameraIp = q('#reportCamera')?.value || '';
    const message = q('#reportMessage');
    if (!fromDate || !toDate) return;

    if (message) {
      message.className = 'report-message';
      message.textContent = 'Loading stored MySQL vehicle crossing data...';
    }

    const params = new URLSearchParams({
      from_date: fromDate,
      to_date: toDate,
      from_time: fromTime,
      to_time: toTime,
      camera_ip: cameraIp,
      t: String(Date.now()),
    });

    try {
      const response = await fetch(`${reportApiUrl}?${params.toString()}`, { cache: 'no-store' });
      const raw = await response.text();
      let data;
      try {
        data = JSON.parse(raw);
      } catch (_) {
        throw new Error(`Report API returned invalid JSON${raw ? `: ${raw.slice(0, 180)}` : ''}`);
      }
      if (!response.ok || !data.ok) throw new Error(data.error || 'Report request failed');
      renderReport(data);

      if (message) {
        message.className = 'report-message success';
        const sourceText = data.source === 'daily_vehicle_counts' ? 'daily aggregate records' : 'vehicle crossing events';
        const note = data.note ? ` ${data.note}` : '';
        message.textContent = `${fmt(data.summary?.total ?? data.summary?.vehicles)} vehicles loaded from ${sourceText} · ${data.from} to ${data.to}${data.camera_ip ? ` · ${data.camera_ip}` : ' · all cameras'}.${note}`;
      }
    } catch (error) {
      renderVehicleSummary({});
      clearReportTables('Unable to load report data');
      if (message) {
        message.className = 'report-message error';
        message.textContent = `Report error: ${error.message}`;
      }
    }
  }

  function setTodayFilter() {
    const today = new Date();
    const y = today.getFullYear();
    const m = String(today.getMonth() + 1).padStart(2, '0');
    const d = String(today.getDate()).padStart(2, '0');
    const date = `${y}-${m}-${d}`;
    if (q('#reportFromDate')) q('#reportFromDate').value = date;
    if (q('#reportToDate')) q('#reportToDate').value = date;
    if (q('#reportFromTime')) q('#reportFromTime').value = '00:00';
    if (q('#reportToTime')) q('#reportToTime').value = '23:59';
    fetchReportData();
  }

  async function refreshViolations() {
    const grid = q('#violationsGrid');
    const status = q('#violationStatus');
    if (status) {
      status.className = 'report-message';
      status.textContent = 'Loading recent confirmed violations...';
    }
    try {
      const response = await fetch(`${baseUrl}/analytics/recent_violations?limit=12&t=${Date.now()}`, { cache: 'no-store' });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'Violation request failed');
      const rows = data.violations || [];
      if (grid) {
        grid.innerHTML = rows.length ? rows.map((row) => {
          const confidence = Math.round(Number(row.detection_confidence || 0) * 100);
          const plate = row.plate_number ? esc(row.plate_number) : 'Plate not read';
          return `<article class="violation-card">
            <img loading="lazy" src="${baseUrl}/analytics/violation_image/${Number(row.id)}/evidence?t=${Date.now()}" alt="Violation evidence">
            <div class="violation-body">
              <b>NO HELMET · ${confidence}%</b>
              <div class="violation-meta"><span>${esc(row.camera_ip)}</span><span>${esc(row.captured_at)}</span></div>
              <span class="plate-pill">${plate}</span>
            </div>
          </article>`;
        }).join('') : '<div class="report-message">No confirmed violations recorded yet.</div>';
      }
      if (status) {
        status.className = 'report-message success';
        status.textContent = rows.length ? `${rows.length} recent violation records loaded.` : 'No confirmed violations recorded yet.';
      }
    } catch (error) {
      if (status) {
        status.className = 'report-message error';
        status.textContent = `Violation report error: ${error.message}`;
      }
    }
  }

  q('#btnRefreshReport')?.addEventListener('click', fetchReportData);
  q('#btnTodayReport')?.addEventListener('click', setTodayFilter);
  q('#reportCamera')?.addEventListener('change', fetchReportData);
  q('#btnRefreshViolations')?.addEventListener('click', refreshViolations);

  refreshHealth();
  refreshTodaySummary();
  fetchReportData();
  refreshViolations();

  setInterval(refreshHealth, 3000);
  setInterval(refreshTodaySummary, 10000);
  setInterval(refreshViolations, 30000);
})();
