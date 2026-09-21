(() => {
  const q = (s, r = document) => r.querySelector(s);
  const config = window.CCTV_UI_CONFIG || {};
  const baseUrl = config.baseUrl || 'http://127.0.0.1:5000';
  const cameras = Array.isArray(config.cameras) ? config.cameras : [];
  const cameraMap = new Map(cameras.map(cam => [cam.camera_key, cam]));

  const esc = (v) => String(v ?? '')
    .replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')
    .replaceAll('"','&quot;').replaceAll("'",'&#039;');
  const setText = (id, v) => { const el = q('#' + id); if (el) el.textContent = v; };
  const labelType = (v) => v === 'accident' ? 'Accident' : v === 'road_obstruction' ? 'Road Obstruction' : v;

  function render(data) {
    const rows = data.events || [];
    const summary = data.summary || {};
    setText('incidentTotal', Number(summary.total || 0).toLocaleString('en-IN'));
    setText('incidentAccidents', Number(summary.accident || 0).toLocaleString('en-IN'));
    setText('incidentObstructions', Number(summary.road_obstruction || 0).toLocaleString('en-IN'));
    setText('incidentCameras', Number(summary.cameras || 0).toLocaleString('en-IN'));
    setText('incidentStatus', rows.length ? `${rows.length} event(s) shown` : 'No incidents found');

    const tbody = q('#incidentTable tbody');
    if (tbody) {
      tbody.innerHTML = rows.length ? rows.map(row => {
        const cam = cameraMap.get(row.camera_key) || {};
        const when = row.captured_at ? new Date(row.captured_at.replace(' ', 'T')).toLocaleString('en-IN') : '';
        return `<tr>
          <td>${esc(when)}</td>
          <td><b>${esc(cam.name || row.camera_key)}</b><br><code>${esc(row.camera_key)}</code></td>
          <td>${esc(cam.area || '')}</td>
          <td>${esc(labelType(row.incident_type))}</td>
          <td>${esc(row.severity || '')}</td>
          <td><b>${Math.round(Number(row.confidence || 0) * 100)}%</b></td>
          <td><button class="evidence-btn" type="button" data-incident-id="${Number(row.id)}"><i class="bi bi-image"></i> View</button></td>
        </tr>`;
      }).join('') : '<tr><td colspan="7" class="empty-row">No incidents found for this period.</td></tr>';
    }

    const grid = q('#incidentEvidenceGrid');
    if (grid) {
      grid.innerHTML = rows.slice(0, 12).map(row => {
        const cam = cameraMap.get(row.camera_key) || {};
        return `<article class="violation-card">
          <img src="${baseUrl}/analytics/incident_image/${row.id}?t=${Date.now()}" alt="Incident evidence" loading="lazy">
          <div class="violation-body">
            <b>${esc(labelType(row.incident_type))}</b>
            <div class="violation-meta"><span>${esc(cam.name || row.camera_key)}</span><span>${esc(row.severity || '')}</span></div>
            <div class="violation-meta"><span>${esc(cam.area || '')}</span><span>${Math.round(Number(row.confidence || 0) * 100)}%</span></div>
          </div>
        </article>`;
      }).join('');
    }

    document.querySelectorAll('[data-incident-id]').forEach(btn => {
      btn.addEventListener('click', () => {
        const url = `${baseUrl}/analytics/incident_image/${btn.dataset.incidentId}`;
        if (window.openLightbox) window.openLightbox(url);
        else window.open(url, '_blank');
      });
    });
  }

  async function load() {
    const today = new Date().toLocaleDateString('en-CA');
    const params = new URLSearchParams({
      from_date: q('#incidentFromDate')?.value || today,
      to_date: q('#incidentToDate')?.value || q('#incidentFromDate')?.value || today,
      camera_key: q('#incidentCamera')?.value || '',
      incident_type: q('#incidentType')?.value || '',
      limit: '250',
      t: String(Date.now())
    });

    setText('incidentStatus', 'Loading...');
    try {
      const response = await fetch(`${baseUrl}/analytics/incidents?${params.toString()}`, {cache:'no-store'});
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.detail || data.error || `HTTP ${response.status}`);
      render(data);
    } catch (err) {
      setText('incidentStatus', `Unable to load incidents: ${err.message || err}`);
      const tbody = q('#incidentTable tbody');
      if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="empty-row">Incident API is unavailable.</td></tr>';
    }
  }

  q('#incidentApply')?.addEventListener('click', load);
  q('#incidentFromDate')?.addEventListener('change', load);
  q('#incidentToDate')?.addEventListener('change', load);
  q('#incidentCamera')?.addEventListener('change', load);
  q('#incidentType')?.addEventListener('change', load);
  q('#incidentToday')?.addEventListener('click', () => {
    const today = new Date().toLocaleDateString('en-CA');
    if (q('#incidentFromDate')) q('#incidentFromDate').value = today;
    if (q('#incidentToDate')) q('#incidentToDate').value = today;
    load();
  });

  load();
  setInterval(load, 10000);
})();
