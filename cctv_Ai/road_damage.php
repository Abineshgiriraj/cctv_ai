<?php
require __DIR__ . '/ui_common.php';
$today = date('Y-m-d');
render_page_start('road', 'Road Damage Monitoring', 'Detected road defects with camera, area, confidence, priority and evidence.');
?>

<div class="operator-summary road-summary">
    <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-cone-striped"></i></span><div><small>TOTAL DETECTIONS</small><strong id="roadTotal">0</strong><p>Selected period</p></div></article>
    <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-exclamation-octagon"></i></span><div><small>HIGH PRIORITY</small><strong id="roadHigh">0</strong><p>Pothole / severe pattern</p></div></article>
    <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-dash-circle"></i></span><div><small>MEDIUM PRIORITY</small><strong id="roadMedium">0</strong><p>Crack detections</p></div></article>
    <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-camera-video"></i></span><div><small>CAMERAS WITH EVENTS</small><strong id="roadCameras">0</strong><p>Distinct camera sources</p></div></article>
</div>

<article class="report-panel road-filter-panel">
    <div class="report-filter-grid">
        <div class="report-field"><label for="roadFromDate">FROM DATE</label><input type="date" id="roadFromDate" value="<?= e($today) ?>"></div>
        <div class="report-field"><label for="roadToDate">TO DATE</label><input type="date" id="roadToDate" value="<?= e($today) ?>"></div>
        <div class="report-field"><label for="roadCamera">CAMERA / AREA</label><select id="roadCamera"><option value="">All Cameras</option><?php foreach ($cameras as $camera): ?><option value="<?= e($camera['camera_key']) ?>"><?= e($camera['name'] . ' · ' . $camera['area']) ?></option><?php endforeach; ?></select></div>
        <div class="report-field"><label for="roadLabel">DAMAGE TYPE</label><select id="roadLabel"><option value="">All Types</option><option value="Pothole">Pothole</option><option value="Alligator Crack">Alligator Crack</option><option value="Longitudinal Crack">Longitudinal Crack</option><option value="Transverse Crack">Transverse Crack</option><option value="Other">Other</option></select></div>
        <div class="report-actions"><button type="button" class="secondary" id="roadToday">Today</button><button type="button" class="primary" id="roadApply"><i class="bi bi-search"></i> Apply</button></div>
    </div>
</article>

<div class="report-message" id="roadModelStatus">Checking road-damage detection model...</div>
<div class="road-note"><i class="bi bi-info-circle"></i> Damage level is a rule-based operational priority derived from the detected class. Model confidence is shown separately and is not a physical engineering severity measurement.</div>

<div class="road-layout">
    <article class="data-card road-table-card">
        <div class="data-card-head"><h3><i class="bi bi-table"></i> Road Damage Report</h3><small id="roadStatus">Loading...</small></div>
        <div class="table-scroll">
            <table class="ops-table road-table" id="roadTable">
                <thead><tr><th>Date & Time</th><th>Camera</th><th>Area</th><th>Damage Type</th><th>Damage Level</th><th>AI Confidence</th><th>Evidence</th></tr></thead>
                <tbody><tr><td colspan="7" class="empty-row">Loading road events...</td></tr></tbody>
            </table>
        </div>
    </article>

    <section>
        <div class="section-heading"><div><h2>Recent Evidence</h2><p>Latest saved road-damage captures.</p></div></div>
        <div class="road-evidence-grid" id="roadEvidenceGrid"></div>
    </section>
</div>

<?php render_page_end(['assets/road.js?v=2']); ?>
