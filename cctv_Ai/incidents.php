<?php
require __DIR__ . '/ui_common.php';
$today = date('Y-m-d');
render_page_start('incidents', 'Incident Monitoring', 'Accident and persistent road-obstruction events from active CCTV cameras.');
?>

<div class="operator-summary">
    <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-exclamation-triangle"></i></span><div><small>TOTAL INCIDENTS</small><strong id="incidentTotal">0</strong><p>Selected period</p></div></article>
    <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-car-front"></i></span><div><small>ACCIDENTS</small><strong id="incidentAccidents">0</strong><p>Trajectory-confirmed candidates</p></div></article>
    <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-sign-stop"></i></span><div><small>ROAD OBSTRUCTIONS</small><strong id="incidentObstructions">0</strong><p>Persistent roadway objects</p></div></article>
    <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-camera-video"></i></span><div><small>CAMERAS WITH EVENTS</small><strong id="incidentCameras">0</strong><p>Distinct camera sources</p></div></article>
</div>

<div class="report-message success" id="incidentMethodStatus">
    Accident detection uses vehicle trajectories/proximity/sudden stop. Road obstruction uses persistent fixed-camera foreground; neither requires a separate accident/obstruction .pt file.
</div>

<article class="report-panel">
    <div class="report-filter-grid">
        <div class="report-field"><label for="incidentFromDate">FROM DATE</label><input type="date" id="incidentFromDate" value="<?= e($today) ?>"></div>
        <div class="report-field"><label for="incidentToDate">TO DATE</label><input type="date" id="incidentToDate" value="<?= e($today) ?>"></div>
        <div class="report-field"><label for="incidentCamera">CAMERA / AREA</label><select id="incidentCamera"><option value="">All Cameras</option><?php foreach ($cameras as $camera): ?><option value="<?= e($camera['camera_key']) ?>"><?= e($camera['name'] . ' · ' . $camera['area']) ?></option><?php endforeach; ?></select></div>
        <div class="report-field"><label for="incidentType">INCIDENT TYPE</label><select id="incidentType"><option value="">All Types</option><option value="accident">Accident</option><option value="road_obstruction">Road Obstruction</option></select></div>
        <div class="report-actions"><button type="button" class="secondary" id="incidentToday">Today</button><button type="button" class="primary" id="incidentApply"><i class="bi bi-search"></i> Apply</button></div>
    </div>
</article>

<article class="data-card" style="margin-top:14px">
    <div class="data-card-head"><h3><i class="bi bi-table"></i> Incident Report</h3><small id="incidentStatus">Loading...</small></div>
    <div class="table-scroll">
        <table class="ops-table" id="incidentTable">
            <thead><tr><th>Date & Time</th><th>Camera</th><th>Area</th><th>Type</th><th>Severity</th><th>Confidence</th><th>Evidence</th></tr></thead>
            <tbody><tr><td colspan="7" class="empty-row">Loading incidents...</td></tr></tbody>
        </table>
    </div>
</article>

<section class="section-block">
    <div class="section-heading"><div><h2>Recent Evidence</h2><p>Saved incident captures for review.</p></div></div>
    <div class="violations-grid" id="incidentEvidenceGrid"></div>
</section>

<?php render_page_end(['assets/incidents.js?v=1']); ?>
