(() => {
  const q = (s, r = document) => r.querySelector(s);
  const qa = (s, r = document) => Array.from(r.querySelectorAll(s));
  const cfg = window.CCTV_UI_CONFIG || {};
  const baseUrl = cfg.baseUrl || 'http://127.0.0.1:5000';
  const healthUrl = cfg.healthUrl || `${baseUrl}/health`;
  const cameras = Array.isArray(cfg.cameras) ? cfg.cameras : [];

  const setText = (id, value) => { const el = q(`#${id}`); if (el) el.textContent = value; };
  const fmt = (value) => Number(value || 0).toLocaleString('en-IN');
  const esc = (value) => String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');

  q('#menu')?.addEventListener('click', () => document.body.classList.toggle('nav-open'));

  function setCameraState(number, isLive, label) {
    const card = q(`.operator-camera-card[data-camera-number="${number}"]`);
    card?.classList.toggle('is-live', !!isLive);
    setText(`cameraState${number}`, label);
    q(`#streamMessage${number}`)?.classList.toggle('hidden', !!isLive);
  }

  function setSystemState(liveCount, aiLiveCount, aiErrors, reachable) {
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
    if (liveCount === total && total > 0) {
      if (label) label.textContent = 'SYSTEM LIVE';
    } else {
      pill?.classList.add('pending');
      if (label) label.textContent = `PARTIAL STREAM ${liveCount}/${total}`;
    }
    setText('activeCameraCount', `${liveCount} / ${total}`);
    setText('aiDetectionStatus', aiErrors ? 'AI ERROR' : (aiLiveCount ? 'TRACKING' : 'STARTING'));
    setText('aiDetectionText', `${aiLiveCount}/${total} AI feeds active`);
  }

  async function refreshHealth() {
    try {
      const response = await fetch(`${healthUrl}?t=${Date.now()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      let liveCount = 0;
      let aiLiveCount = 0;
      let aiErrors = 0;

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

      setSystemState(liveCount, aiLiveCount, aiErrors, true);
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
      setText('rptTotalPersons', fmt(s.person));
      setText('rptTotalVehicles', fmt(s.total));
      setText('rptTotalCars', fmt(s.car));
      setText('rptTotalMotorcycles', fmt(s.motorcycle));
      setText('rptTotalBuses', fmt(s.bus));
      setText('rptTotalTrucks', fmt(s.truck));
      setText('rptTotalBicycles', fmt(s.bicycle));
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
    } catch (err) { console.warn('Report query failed', err); }
  }

  async function fetchViolations() {
    const grid = q('#violationsGrid');
    if (!grid) return;
    try {
      const res = await fetch(`${baseUrl}/analytics/recent_violations?limit=20&t=${Date.now()}`, { cache: 'no-store' });
      const data = await res.json();
      const rows = data.violations || [];
      setText('violationStatus', rows.length ? '' : 'No recent violations found.');
      grid.innerHTML = rows.map(v => `<article class="violation-card"><div class="violation-image"><img src="${baseUrl}/analytics/violation_image/${v.id}/evidence" loading="lazy" alt="Violation evidence"></div><div class="violation-details"><div><strong>${esc(v.camera_key || v.camera_ip)}</strong><small>${esc(v.captured_at || '')}</small></div><span class="status-chip danger">NO HELMET</span></div></article>`).join('');
    } catch (err) { console.warn('Violation query failed', err); }
  }

  q('#reportFromDate')?.addEventListener('change', fetchReportData);
  q('#reportToDate')?.addEventListener('change', fetchReportData);
  q('#reportFromTime')?.addEventListener('change', fetchReportData);
  q('#reportToTime')?.addEventListener('change', fetchReportData);
  q('#reportCamera')?.addEventListener('change', fetchReportData);
  q('#btnRefreshReport')?.addEventListener('click', fetchReportData);
  q('#btnRefreshViolations')?.addEventListener('click', fetchViolations);

  refreshHealth();
  refreshTodaySummary();
  fetchReportData();
  fetchViolations();
  setInterval(refreshHealth, 3000);
  setInterval(refreshTodaySummary, 5000);
  setInterval(fetchReportData, 5000);
  setInterval(fetchViolations, 5000);
})();
