(() => {
  const q = (s, r = document) => r.querySelector(s);
  const config = window.CCTV_UI_CONFIG || {};
  const baseUrl = config.baseUrl || 'http://127.0.0.1:5000';
  const cameras = Array.isArray(config.cameras) ? config.cameras : [];
  const cameraMap = new Map(cameras.map(cam => [cam.camera_key, cam]));

  const esc = (v) => String(v ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;');
  const fmt = (v) => Number(v || 0).toLocaleString('en-IN');
  const camName = (key) => cameraMap.get(key)?.name || key || 'Camera';
  const camArea = (key) => cameraMap.get(key)?.area || 'Unknown Area';
  const setText = (id, v) => { const el = q(`#${id}`); if (el) el.textContent = v; };

  function render(data) {
    const rows = data.events || [];
    const summary = data.summary || {};
    setText('roadTotal', fmt(summary.total));
    setText('roadHigh', fmt(summary.high));
    setText('roadMedium', fmt(summary.medium));
    setText('roadCameras', fmt(summary.cameras));
    setText('roadStatus', `${rows.length} event(s) shown`);

    const tbody = q('#roadTable tbody');
    if (tbody) {
      tbody.innerHTML = rows.length ? rows.map((row) => {
        const when = row.captured_at ? new Date(row.captured_at.replace(' ', 'T')).toLocaleString('en-IN') : '';
        const priority = String(row.damage_level || 'Low').toLowerCase();
        return `<tr>
          <td>${esc(when)}</td>
          <td><b>${esc(camName(row.camera_key || row.camera_ip))}</b><br><code>${esc(row.camera_key || row.camera_ip)}</code></td>
          <td>${esc(camArea(row.camera_key || row.camera_ip))}</td>
          <td>${esc(row.model_label)}</td>
          <td><span class="damage-badge ${esc(priority)}">${esc(row.damage_level)}</span></td>
          <td><b>${Math.round(Number(row.confidence || 0) * 100)}%</b></td>
          <td><button class="evidence-btn" type="button" data-road-id="${Number(row.id)}"><i class="bi bi-image"></i> View</button></td>
        </tr>`;
      }).join('') : '<tr><td colspan="7" class="empty-row">No road damage events found for this period.</td></tr>';
    }

    const grid = q('#roadEvidenceGrid');
    if (grid) {
      grid.innerHTML = rows.slice(0, 12).map((row) => `
        <article class="road-evidence-card">
          <img src="${baseUrl}/analytics/road_event_image/${row.id}?t=${Date.now()}" alt="Road damage evidence" loading="lazy">
          <div class="road-evidence-body">
            <div><b>${esc(row.model_label)}</b><span class="damage-badge ${String(row.damage_level || 'Low').toLowerCase()}">${esc(row.damage_level)}</span></div>
            <p>${esc(camName(row.camera_ip))} · ${esc(camArea(row.camera_ip))}</p>
            <small>AI confidence ${Math.round(Number(row.confidence || 0) * 100)}%</small>
          </div>
        </article>`).join('');
    }

    document.querySelectorAll('[data-road-id]').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (window.openLightbox) {
          window.openLightbox(`${baseUrl}/analytics/road_event_image/${btn.dataset.roadId}`);
        } else {
          window.open(`${baseUrl}/analytics/road_event_image/${btn.dataset.roadId}`, '_blank');
        }
      });
    });
  }

  async function loadRoadReport() {
    const today = new Date().toLocaleDateString('en-CA');
    const fromDate = q('#roadFromDate')?.value || today;
    const toDate = q('#roadToDate')?.value || fromDate;
    const cameraKey = q('#roadCamera')?.value || '';
    const label = q('#roadLabel')?.value || '';
    setText('roadStatus', 'Loading...');
    const url = `${baseUrl}/analytics/road_report?from_date=${encodeURIComponent(fromDate)}&to_date=${encodeURIComponent(toDate)}&camera_key=${encodeURIComponent(cameraKey)}&label=${encodeURIComponent(label)}&limit=250&t=${Date.now()}`;
    try {
      const response = await fetch(url, {cache:'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (!data.ok) throw new Error(data.error || 'Road report failed');
      render(data);
    } catch (err) {
      console.warn('Road report error:', err);
      setText('roadStatus', 'Unable to load road report');
      const tbody = q('#roadTable tbody');
      if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="empty-row">Road report API is unavailable.</td></tr>';
    }
  }

  q('#roadApply')?.addEventListener('click', loadRoadReport);
  q('#roadFromDate')?.addEventListener('change', loadRoadReport);
  q('#roadToDate')?.addEventListener('change', loadRoadReport);
  q('#roadCamera')?.addEventListener('change', loadRoadReport);
  q('#roadLabel')?.addEventListener('change', loadRoadReport);
  q('#roadToday')?.addEventListener('click', () => {
    const today = new Date().toLocaleDateString('en-CA');
    if (q('#roadFromDate')) q('#roadFromDate').value = today;
    if (q('#roadToDate')) q('#roadToDate').value = today;
    loadRoadReport();
  });

  loadRoadReport();
  setInterval(loadRoadReport, 10000);
})();
