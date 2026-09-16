(() => {
  const q = (s, r = document) => r.querySelector(s);
  const cfg = window.CCTV_UI_CONFIG || {};
  const baseUrl = cfg.baseUrl || 'http://127.0.0.1:5000';
  const cameras = Array.isArray(cfg.cameras) ? cfg.cameras : [];

  const esc = (v) => String(v ?? '')
    .replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')
    .replaceAll('"','&quot;').replaceAll("'",'&#039;');
  const fmt = (v) => Number(v || 0).toLocaleString('en-IN');
  const setText = (id, value) => { const el = q(`#${id}`); if (el) el.textContent = value; };
  const cameraFor = (key) => cameras.find(c => c.camera_key === key) || null;
  const incidentLabel = (type) => type === 'accident' ? 'Accident' : 'Road Obstruction';

  function imageUrl(row) {
    return row.image_source === 'road'
      ? `${baseUrl}/analytics/road_event_image/${row.id}`
      : `${baseUrl}/analytics/incident_image/${row.id}`;
  }

  function render(data) {
    const rows = data.events || [];
    const summary = data.summary || {};
    setText('incidentTotal', fmt(summary.total));
    setText('incidentAccidents', fmt(summary.accidents));
    setText('incidentObstructions', fmt(summary.road_obstructions));
    setText('incidentHigh', fmt(summary.high));
    setText('incidentStatus', `${rows.length} event(s) shown`);

    const tbody = q('#incidentTable tbody');
    if (tbody) {
      tbody.innerHTML = rows.length ? rows.map(row => {
        const cam = cameraFor(row.camera_key);
        const when = row.captured_at ? new Date(row.captured_at.replace(' ', 'T')).toLocaleString('en-IN') : '';
        const severity = String(row.severity || 'Medium').toLowerCase();
        return `<tr>
          <td>${esc(when)}</td>
          <td><b>${esc(cam?.name || row.camera_key)}</b><br><code>${esc(row.camera_key || '')}</code></td>
          <td>${esc(cam?.area || row.metadata?.area_name || 'Unknown Area')}</td>
          <td><b>${esc(incidentLabel(row.incident_type))}</b></td>
          <td><span class="damage-badge ${esc(severity)}">${esc(row.severity || 'Medium')}</span></td>
          <td><b>${Math.round(Number(row.confidence || 0) * 100)}%</b></td>
          <td><button class="evidence-btn" type="button" data-evidence-url="${esc(imageUrl(row))}"><i class="bi bi-image"></i> View</button></td>
        </tr>`;
      }).join('') : '<tr><td colspan="7" class="empty-row">No incidents found for this period.</td></tr>';
    }

    const grid = q('#incidentEvidenceGrid');
    if (grid) {
      grid.innerHTML = rows.slice(0, 12).map(row => {
        const cam = cameraFor(row.camera_key);
        const source = row.metadata?.source === 'persistent_scene_change' ? 'Scene change' :
          (row.metadata?.source === 'road_obstruction_model' ? 'Obstruction model' : 'Tracking');
        return `<article class="road-evidence-card">
          <img src="${imageUrl(row)}?t=${Date.now()}" alt="Incident evidence" loading="lazy">
          <div class="road-evidence-body">
            <div><b>${esc(incidentLabel(row.incident_type))}</b><span class="damage-badge ${String(row.severity || 'Medium').toLowerCase()}">${esc(row.severity || 'Medium')}</span></div>
            <p>${esc(cam?.name || row.camera_key)} · ${esc(cam?.area || row.metadata?.area_name || 'Unknown Area')}</p>
            <small>${esc(source)} · confidence ${Math.round(Number(row.confidence || 0) * 100)}%</small>
          </div>
        </article>`;
      }).join('');
    }

    document.querySelectorAll('[data-evidence-url]').forEach(btn => {
      btn.addEventListener('click', () => {
        const url = btn.dataset.evidenceUrl;
        if (window.openLightbox) window.openLightbox(url);
        else window.open(url, '_blank');
      });
    });
  }

  async function loadIncidents() {
    const today = new Date().toLocaleDateString('en-CA');
    const fromDate = q('#incidentFromDate')?.value || today;
    const toDate = q('#incidentToDate')?.value || fromDate;
    const cameraKey = q('#incidentCamera')?.value || '';
    const incidentType = q('#incidentType')?.value || '';
    setText('incidentStatus', 'Loading...');
    const params = new URLSearchParams({
      from_date: fromDate,
      to_date: toDate,
      camera_key: cameraKey,
      incident_type: incidentType,
      limit: '300',
      t: String(Date.now())
    });
    try {
      const response = await fetch(`${baseUrl}/analytics/incident_report?${params.toString()}`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (!data.ok) throw new Error(data.error || 'Incident report failed');
      render(data);
    } catch (err) {
      console.warn('Incident report error:', err);
      setText('incidentStatus', 'Unable to load incident report');
      const tbody = q('#incidentTable tbody');
      if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="empty-row">Incident report API is unavailable.</td></tr>';
    }
  }

  q('#incidentApply')?.addEventListener('click', loadIncidents);
  q('#incidentFromDate')?.addEventListener('change', loadIncidents);
  q('#incidentToDate')?.addEventListener('change', loadIncidents);
  q('#incidentCamera')?.addEventListener('change', loadIncidents);
  q('#incidentType')?.addEventListener('change', loadIncidents);
  q('#incidentToday')?.addEventListener('click', () => {
    const today = new Date().toLocaleDateString('en-CA');
    if (q('#incidentFromDate')) q('#incidentFromDate').value = today;
    if (q('#incidentToDate')) q('#incidentToDate').value = today;
    loadIncidents();
  });

  loadIncidents();
  setInterval(loadIncidents, 10000);
})();
