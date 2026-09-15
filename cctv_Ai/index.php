<?php
require __DIR__ . '/ui_common.php';
render_page_start('overview', 'Traffic Monitoring Dashboard', 'System overview with quick access to live monitoring, reports, helmet violations and road damage.');
?>

<div class="operator-summary">
    <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-camera-video"></i></span><div><small>ACTIVE CAMERAS</small><strong id="activeCameraCount">0 / <?= count($cameras) ?></strong><p id="cameraNetworkText">Checking camera streams</p></div></article>
    <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-car-front-fill"></i></span><div><small>VEHICLES COUNTED TODAY</small><strong id="todayVehicleCount">0</strong><p>Unique line-crossing events</p></div></article>
    <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-person-x-fill"></i></span><div><small>NO-HELMET TODAY</small><strong id="todayNoHelmetCount">0</strong><p>Confirmed violations</p></div></article>
    <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-cpu"></i></span><div><small>AI STATUS</small><strong id="aiDetectionStatus">STARTING</strong><p id="aiDetectionText">YOLO + ByteTrack</p></div></article>
</div>

<div class="status-strip" aria-label="Advanced detection readiness">
    <span class="status-chip" id="helmetReadiness"><i class="bi bi-person-badge"></i><span>Helmet model: checking</span></span>
    <span class="status-chip" id="plateReadiness"><i class="bi bi-card-text"></i><span>Plate OCR: checking</span></span>
    <span class="status-chip" id="roadDamageReadiness"><i class="bi bi-cone-striped"></i><span>Road damage: checking</span></span>
    <span class="status-chip" id="roadObstructionReadiness"><i class="bi bi-signpost-split"></i><span>Road obstruction: checking</span></span>
</div>

<div class="report-table-grid" style="margin-top:20px">
    <a class="data-card" href="live.php" style="text-decoration:none;color:inherit"><div class="data-card-head"><h3><i class="bi bi-camera-video"></i> Live Monitoring</h3><small>Open camera feeds and AI tracking</small></div></a>
    <a class="data-card" href="reports.php" style="text-decoration:none;color:inherit"><div class="data-card-head"><h3><i class="bi bi-bar-chart-line"></i> Vehicle Reports</h3><small>Day, time and camera-wise counts</small></div></a>
    <a class="data-card" href="violations.php" style="text-decoration:none;color:inherit"><div class="data-card-head"><h3><i class="bi bi-exclamation-triangle"></i> Helmet Violations</h3><small>Confirmed no-helmet evidence</small></div></a>
    <a class="data-card" href="road_damage.php" style="text-decoration:none;color:inherit"><div class="data-card-head"><h3><i class="bi bi-cone-striped"></i> Road Damage</h3><small>Area, camera, damage type and evidence</small></div></a>
</div>

<?php render_page_end(); ?>
