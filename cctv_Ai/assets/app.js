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
        <div><h2>Vehicle Counts Today</h2><p>Persistent crossing counts stored in MySQL. Each tracked vehicle is counted once.</p></div>
        <div><span class="badge live"><i></i> MYSQL ANALYTICS</span></div>
      </div>
      <div class="traffic-count-grid">
        <div class="traffic-count-card"><small>Person</small><b id="countPerson">0</b><span>Today</span></div>
        <div class="traffic-count-card"><small>Motorcycle</small><b id="countMotorcycle">0</b><span>Today</span></div>
        <div class="traffic-count-card"><small>Car</small><b id="countCar">0</b><span>Today</span></div>
        <div class="traffic-count-card"><small>Bus</small><b id="countBus">0</b><span>Today</span></div>
        <div class="traffic-count-card"><small>Truck</small><b id="countTruck">0</b><span>Today</span></div>
        <div class="traffic-count-card"><small>Bicycle</small><b id="countBicycle">0</b><span>Today</span></div>
        <div class="traffic-count-card"><small>Total vehicles</small><b id="countVehicleTotal">0</b><span>All cameras</span></div>
      </div>
      <div class="count-line-note">Counting line: <b id="countLineStatus">Loading…</b> · Database: <b>cctv_ai</b></div>
      <div class="panel-head"><div><h2>Advanced Detection Readiness</h2><p>Custom model files are required for helmet, plate and road-event detection.</p></div></div>
      <div class="advanced-readiness">
        <div id="helmetReadiness"><i class="bi bi-person-badge"></i><span><b>Helmet / No Helmet</b><small>Checking model…</small></span></div>
        <div id="plateReadiness"><i class="bi bi-card-text"></i><span><b>Number Plate + OCR</b><small>Checking model…</small></span></div>
        <div id="roadDamageReadiness"><i class="bi bi-cone-striped"></i><span><b>Road Damage</b><small>Checking model…</small></span></div>
        <div id="roadObstructionReadiness"><i class="bi bi-signpost-split"></i><span><b>Fallen Tree / Obstruction</b><small>Checking model…</small></span></div>
      </div>`;
    integration.parentNode.insertBefore(panel, integration);
  }

  function ensureCameraModal() {
    if (q('#cameraFocusModal')) return;
    const modal = document.createElement('div');
    modal.id = 'cameraFocusModal';
    modal.className = 'camera-focus-modal';
    modal.innerHTML = `
      <div class="camera-focus-shell" role="dialog" aria-modal="true" aria-label="Focused camera view">
        <div class="camera-focus-head"><b id="focusCameraTitle">Camera</b><span id="focusCameraIp"></span><button type="button" id="focusClose" aria-label="Close"><i class="bi bi-x-lg"></i></button></div>
        <div class="camera-focus-body"><img id="focusCameraStream" alt="Focused CCTV stream"></div>
        <div class="camera-focus-foot"><i class="live-dot"></i><b id="focusCameraMode">AI TRACKING LIVE</b><span>Press Esc or click close to return</span></div>
      </div>`;
    document.body.appendChild(modal);
    q('#focusClose')?.addEventListener('click', closeCameraModal);
    modal.addEventListener('click', (event) => { if (event.target === modal) closeCameraModal(); });
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeCameraModal(); });
  }

  function openCameraModal(number) {
    const img = q(`#cameraStream${number}`);
    const card = img?.closest('.camera-card');
    if (!img || !card) return;
    const mode = img.dataset.streamMode || 'ai';
    const url = mode === 'ai' ? img.dataset.aiStreamUrl : img.dataset.rawStreamUrl;
    q('#focusCameraTitle').textContent = `Camera ${number}`;
    q('#focusCameraIp').textContent = card.dataset.cameraIp || '';
    q('#focusCameraMode').textContent = mode === 'ai' ? 'AI TRACKING LIVE' : 'RAW MJPEG LIVE';
    q('#focusCameraStream').src = `${url}?focus=${Date.now()}`;
    q('#cameraFocusModal').classList.add('open');
    document.body.classList.add('modal-open');
  }

  function closeCameraModal() {
    const modal = q('#cameraFocusModal');
    if (!modal) return;
    modal.classList.remove('open');
    document.body.classList.remove('modal-open');
    const img = q('#focusCameraStream');
    if (img) img.removeAttribute('src');
  }

  ensureTrafficUi();
  ensureCameraModal();
  qa('.camera-card').forEach((card) => {
    const number = Number(card.dataset.cameraNumber || 0);
    q('.camera-frame', card)?.addEventListener('click', () => openCameraModal(number));
  });

  function tick() {
    const now = new Date().toLocaleTimeString('en-IN', { hour12: true });
    qa('.camera-clock').forEach((clock) => { clock.textContent = now; });
  }
  tick();
  setInterval(tick, 1000);

  function cameraMode(number) { return q(`#cameraStream${number}`)?.dataset.streamMode || 'ai'; }
  function setText(id, value) { const el = q(`#${id}`); if (el) el.textContent = value; }

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
    const readable = Object.entries(classCounts).filter(([,v]) => Number(v) > 0).map(([n,v]) => `${n.replace(/\b\w/g,c=>c.toUpperCase())}: ${v}`).join(' · ');
    const cumulative = ai?.session_vehicle_counts || {};
    const cumulativeReadable = Object.entries(cumulative).filter(([,v]) => Number(v) > 0).map(([n,v]) => `${n.replace(/\b\w/g,c=>c.toUpperCase())}: ${v}`).join(' · ');
    setText(`classCounts${number}`, `${readable || 'No target objects in current frame'}${cumulativeReadable ? ` | Session counted: ${cumulativeReadable}` : ''}`);
    let aiState = 'Starting';
    if (ai?.last_error) aiState = 'AI Error'; else if (ai?.has_frame) aiState = 'Tracking'; else if (ai?.model_loaded) aiState = 'Waiting for frame';
    setText(`aiState${number}`, aiState);
  }

  function updateTraffic(traffic) {
    const today = traffic?.today || {};
    const totals = today?.totals || {};
    setText('countPerson', Number(totals.person || 0).toLocaleString('en-IN'));
    setText('countMotorcycle', Number(totals.motorcycle || 0).toLocaleString('en-IN'));
    setText('countCar', Number(totals.car || 0).toLocaleString('en-IN'));
    setText('countBus', Number(totals.bus || 0).toLocaleString('en-IN'));
    setText('countTruck', Number(totals.truck || 0).toLocaleString('en-IN'));
    setText('countBicycle', Number(totals.bicycle || 0).toLocaleString('en-IN'));
    setText('countVehicleTotal', Number(today?.grand_total || 0).toLocaleString('en-IN'));
    const ratio = Number(traffic?.count_line_y_ratio);
    setText('countLineStatus', traffic?.counting_enabled ? `${Number.isFinite(ratio) ? Math.round(ratio * 100) : '—'}% frame height · enabled` : 'disabled');
  }

  function setReadiness(id, model) {
    const el = q(`#${id}`); if (!el) return;
    const available = Boolean(model?.available);
    el.classList.toggle('ready', available);
    const small = q('small', el); if (small) small.textContent = available ? 'Model file found · enabled' : 'Custom .pt model required';
  }
  function updateAdvancedModels(models) {
    setReadiness('helmetReadiness', models?.helmet);
    setReadiness('plateReadiness', models?.plate);
    setReadiness('roadDamageReadiness', models?.road_damage);
    setReadiness('roadObstructionReadiness', models?.road_obstruction);
  }

  function setSystemState(liveCount, total, backendReachable, aiLiveCount, aiErrors) {
    const system = q('#systemLive'), count = q('#activeCameraCount'), text = q('#cameraNetworkText'), backend = q('#backendHealth');
    if (count) count.innerHTML = `${liveCount} <em>/ ${total}</em>`;
    if (system) {
      system.classList.remove('offline','pending');
      const label = q('span', system);
      if (!backendReachable) { system.classList.add('offline'); if(label) label.textContent='BACKEND OFFLINE'; if(text) text.textContent='MJPEG server not reachable'; if(backend) backend.textContent='Offline · start start-mjpeg.bat'; }
      else if (liveCount===total && total>0) { if(label) label.textContent='SYSTEM LIVE'; if(text) text.textContent='All configured cameras streaming'; if(backend) backend.textContent=`Online · raw ${liveCount}/${total} · AI ${aiLiveCount}/${total}`; }
      else { system.classList.add('pending'); if(label) label.textContent='PARTIAL STREAM'; if(text) text.textContent=`${liveCount} of ${total} cameras have raw frames`; if(backend) backend.textContent='Online · waiting for one or more camera frames'; }
    }
    const aiStatus=q('#aiDetectionStatus'), aiText=q('#aiDetectionText');
    if(aiStatus){ aiStatus.classList.remove('muted-stat'); if(!backendReachable){aiStatus.textContent='OFFLINE';aiStatus.classList.add('muted-stat');}else if(aiErrors>0){aiStatus.textContent='ERROR';aiStatus.classList.add('muted-stat');}else if(aiLiveCount===total&&total>0){aiStatus.textContent='TRACKING';}else{aiStatus.textContent='STARTING';}}
    if(aiText) aiText.textContent=aiErrors>0?'Check backend console / health endpoint':`YOLOv8 + ByteTrack · ${aiLiveCount}/${total} AI feeds`;
  }

  async function refreshHealth() {
    const total=config.cameraIps.length;
    try {
      const response=await fetch(`${config.healthUrl}?t=${Date.now()}`,{cache:'no-store'}); if(!response.ok) throw new Error('health unavailable');
      const data=await response.json(); let liveCount=0,aiLiveCount=0,aiErrors=0;
      config.cameraIps.forEach((ip,index)=>{
        const number=index+1, st=data?.cameras?.[ip]||{}, ai=st?.ai||{};
        const rawLive=Boolean(st.has_frame&&st.connected), aiLive=Boolean(ai.has_frame&&ai.model_loaded&&!ai.last_error), mode=cameraMode(number), selectedLive=mode==='ai'?aiLive:rawLive;
        if(rawLive) liveCount++; if(aiLive) aiLiveCount++; if(ai.last_error) aiErrors++;
        let label=mode==='ai'?'AI STARTING':'RAW LIVE'; if(mode==='ai'&&ai.last_error) label='AI ERROR'; else if(mode==='ai'&&aiLive) label='AI LIVE'; else if(mode==='raw'&&!rawLive) label='NO FRAME';
        setCameraState(number,selectedLive?'live':(ai.last_error&&mode==='ai'?'offline':'pending'),label,Number(st.frames||0)); updateAiCard(number,ai);
      });
      updateTraffic(data?.traffic||{}); updateAdvancedModels(data?.advanced_models||{}); setSystemState(liveCount,total,true,aiLiveCount,aiErrors);
    } catch (_) {
      config.cameraIps.forEach((_,index)=>{setCameraState(index+1,'offline','BACKEND OFFLINE',NaN);updateAiCard(index+1,{});});
      updateTraffic({}); updateAdvancedModels({}); setSystemState(0,total,false,0,0);
    }
  }

  window.switchCameraMode=(number,mode)=>{
    const img=q(`#cameraStream${number}`); if(!img||!['ai','raw'].includes(mode)||img.dataset.streamMode===mode) return;
    const url=mode==='ai'?img.dataset.aiStreamUrl:img.dataset.rawStreamUrl; img.dataset.streamMode=mode;
    setText(`streamModeLabel${number}`,mode==='ai'?'AI TRACKING LIVE':'RAW MJPEG LIVE'); q(`#aiMode${number}`)?.classList.toggle('active',mode==='ai'); q(`#rawMode${number}`)?.classList.toggle('active',mode==='raw');
    setCameraState(number,'pending',mode==='ai'?'AI STARTING':'CONNECTING',NaN); img.src=`${url}?t=${Date.now()}`; setTimeout(refreshHealth,800);
  };
  window.reconnectCamera=(number)=>{const img=q(`#cameraStream${number}`);if(!img)return;const mode=img.dataset.streamMode||'ai',base=mode==='ai'?img.dataset.aiStreamUrl:img.dataset.rawStreamUrl;setCameraState(number,'pending','RECONNECTING',NaN);img.src=`${base}?t=${Date.now()}`;setTimeout(refreshHealth,1000);};


  async function fetchReportData() {
    const dateInput = q('#reportDate')?.value || '';
    const cameraIp = q('#reportCamera')?.value || '';
    const baseUrl = config.healthUrl ? config.healthUrl.replace('/health', '') : 'http://127.0.0.1:5000';
    const url = `${baseUrl}/analytics/vehicle_counts?date=${encodeURIComponent(dateInput)}&camera_ip=${encodeURIComponent(cameraIp)}&t=${Date.now()}`;
    
    try {
      const res = await fetch(url, { cache: 'no-store' });
      if (!res.ok) throw new Error('Report query failed');
      const data = await res.json();
      
      const summary = data?.summary || {};
      const grandTotal = Number(summary.grand_total || data.grand_total || 0);
      
      setText('rptTotalPersons', Number(summary.persons || 0).toLocaleString('en-IN'));
      setText('rptTotalVehicles', Number(summary.vehicles || 0).toLocaleString('en-IN'));
      setText('rptTotalCars', Number(summary.cars || 0).toLocaleString('en-IN'));
      setText('rptTotalMotorcycles', Number(summary.motorcycles || 0).toLocaleString('en-IN'));
      setText('rptTotalBuses', Number(summary.buses || 0).toLocaleString('en-IN'));
      setText('rptTotalTrucks', Number(summary.trucks || 0).toLocaleString('en-IN'));
      setText('rptTotalOther', Number(summary.other || 0).toLocaleString('en-IN'));
      setText('rptGrandTotal', grandTotal.toLocaleString('en-IN'));
      
      // Populate Camera-Wise Table
      const camTbody = q('#tblCameraBreakdown tbody');
      if (camTbody) {
        const byCamera = data?.by_camera || {};
        let html = '';
        const ipsToRender = cameraIp ? [cameraIp] : config.cameraIps;
        
        ipsToRender.forEach((ip, idx) => {
          const camRow = byCamera[ip] || {};
          const name = `Camera ${config.cameraIps.indexOf(ip) + 1}`;
          const persons = Number(camRow.persons || 0);
          const cars = Number(camRow.cars || 0);
          const motos = Number(camRow.motorcycles || 0);
          const buses = Number(camRow.buses || 0);
          const trucks = Number(camRow.trucks || 0);
          const other = Number(camRow.other || 0);
          const total = Number(camRow.total || camRow.grand_total || (persons + cars + motos + buses + trucks + other));
          
          html += `<tr>
            <td><b>${name}</b></td>
            <td><code>${ip}</code></td>
            <td>${persons.toLocaleString('en-IN')}</td>
            <td>${cars.toLocaleString('en-IN')}</td>
            <td>${motos.toLocaleString('en-IN')}</td>
            <td>${buses.toLocaleString('en-IN')}</td>
            <td>${trucks.toLocaleString('en-IN')}</td>
            <td>${other.toLocaleString('en-IN')}</td>
            <td><strong class="highlight-total">${total.toLocaleString('en-IN')}</strong></td>
          </tr>`;
        });
        
        camTbody.innerHTML = html || '<tr><td colspan="9" class="text-center">No data available for selected filter</td></tr>';
      }
      
      // Populate Class-Wise Summary Table
      const classTbody = q('#tblClassBreakdown tbody');
      if (classTbody) {
        const totals = data?.totals || {};
        const entries = Object.entries(totals).sort((a, b) => Number(b[1]) - Number(a[1]));
        let html = '';
        
        if (entries.length === 0) {
          html = '<tr><td colspan="3" class="text-center">No objects counted yet</td></tr>';
        } else {
          entries.forEach(([clsName, countVal]) => {
            const count = Number(countVal || 0);
            const pct = grandTotal > 0 ? ((count / grandTotal) * 100).toFixed(1) : '0.0';
            const label = clsName.replace(/\b\w/g, c => c.toUpperCase());
            
            html += `<tr>
              <td><b>${label}</b></td>
              <td>${count.toLocaleString('en-IN')}</td>
              <td>
                <div class="progress-bar-cell">
                  <span>${pct}%</span>
                  <div class="progress-bar"><div class="progress-fill" style="width: ${pct}%"></div></div>
                </div>
              </td>
            </tr>`;
          });
        }
        classTbody.innerHTML = html;
      }
    } catch (err) {
      console.warn('Report query failed:', err);
    }
  }

  q('#reportDate')?.addEventListener('change', fetchReportData);
  q('#reportCamera')?.addEventListener('change', fetchReportData);
  q('#btnRefreshReport')?.addEventListener('click', fetchReportData);
  fetchReportData();

  refreshHealth();
  setInterval(refreshHealth,3000);
})();
