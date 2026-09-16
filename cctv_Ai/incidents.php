<?php
require __DIR__ . '/ui_common.php';
$today = date('Y-m-d');
render_page_start('incidents', 'Incident Monitoring', 'Accident and road-obstruction events with camera, area, severity, confidence and evidence.');
?>

<div class="operator-summary road-summary">
    <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-exclamation-diamond"></i></span><div><small>TOTAL INCIDENTS</small><strong id="incidentTotal">0</strong><p>Selected period</p></div></article>
    <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-car-front-fill"></i></span><div><small>ACCIDENTS</small><strong id="incidentAccidents">0</strong><p>Temporal collision candidates</p></div></article>
    <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-sign-stop-fill"></i></span><div><small>ROAD OBSTRUCTIONS</small><strong id="incidentObstructions">0</strong><p>Tree / branch / debris / blockage</p></div></article>
    <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-exclamation-octagon"></i></span><div><small>HIGH SEVERITY</small><strong id="incidentHigh">0</strong><p>Requires priority review</p></div></article>
</div>

<article class="report-panel road-filter-panel">
    <div class="report-filter-grid">
        <div class="report-field"><label for="incidentFromDate">FROM DATE</label><input type="date" id="incidentFromDate" value="<?= e($today) ?>"></div>
        <div class="report-field"><label for="incidentToDate">TO DATE</label><input type="date" id="incidentToDate" value="<?= e($today) ?>"></div>
        <div class="report-field"><label for="incidentCamera">CAMERA / AREA</label><select id="incidentCamera"><option value="">All Cameras</option><?php foreach ($cameras as $camera): ?><option value="<?= e($camera['camera_key']) ?>"><?= e($camera['name'] . ' · ' . $camera['area']) ?></option><?php endforeach; ?></select></div>
        <div class="report-field"><label for="incidentType">INCIDENT TYPE</label><select id="incidentType"><option value="">All Types</option><option value="accident">Accident</option><option value="road_obstruction">Road Obstruction</option></select></div>
        <div class="report-actions"><button type="button" class="secondary" id="incidentToday">Today</button><button type="button" class="primary" id="incidentApply"><i class="bi bi-search"></i> Apply</button></div>
    </div>
</article>

<div class="road-note"><i class="bi bi-info-circle"></i> Accident detection is rule-based from tracked vehicle motion/proximity. Road-obstruction fallback detects newly persistent roadway objects. Both should be reviewed with the saved evidence image before operational action.</div>

<div class="road-layout">
    <article class="data-card road-table-card">
        <div class="data-card-head"><h3><i class="bi bi-table"></i> Incident Report</h3><small id="incidentStatus">Loading...</small></div>
        <div class="table-scroll">
            <table class="ops-table road-table" id="incidentTable">
                <thead><tr><th>Date & Time</th><th>Camera</th><th>Area</th><th>Incident</th><th>Severity</th><th>Confidence</th><th>Evidence</th></tr></thead>
                <tbody><tr><td colspan="7" class="empty-row">Loading incidents...</td></tr></tbody>
            </table>
        </div>
    </article>

    <section>
        <div class="section-heading"><div><h2>Recent Evidence</h2><p>Latest saved accident and obstruction captures.</p></div></div>
        <div class="road-evidence-grid" id="incidentEvidenceGrid"></div>
    </section>
</div>

<?php render_page_end(['assets/incidents.js?v=1']); ?>
