<?php
date_default_timezone_set('Asia/Kolkata');

$camera_ips = ['192.168.0.241', '192.168.0.242'];
$env_path = __DIR__ . DIRECTORY_SEPARATOR . '.env';
if (is_readable($env_path)) {
    foreach (file($env_path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
        $line = trim($line);
        if ($line === '' || $line[0] === '#' || strpos($line, '=') === false) continue;
        [$key, $value] = explode('=', $line, 2);
        if (trim($key) === 'CAMERA_IPS') {
            $parsed = array_values(array_filter(array_map('trim', explode(',', $value))));
            if ($parsed) $camera_ips = $parsed;
        }
    }
}

$stream_base = 'http://127.0.0.1:5000';
$cameras = [];
foreach ($camera_ips as $index => $ip) {
    $n = $index + 1;
    $cameras[] = [
        'number' => $n,
        'code' => 'CAM ' . $n,
        'name' => 'Camera ' . $n,
        'ip' => $ip,
        'raw_url' => $stream_base . '/video_feed/' . rawurlencode($ip),
        'ai_url' => $stream_base . '/tracked_feed/' . rawurlencode($ip),
    ];
}

function e(string $value): string { return htmlspecialchars($value, ENT_QUOTES, 'UTF-8'); }
$today = date('Y-m-d');
$asset_version = '20260915-report2';
?>
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>CivicVision AI - Traffic Monitoring</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link rel="stylesheet" href="assets/app.css?v=<?= e($asset_version) ?>">
    <link rel="stylesheet" href="assets/ops.css?v=<?= e($asset_version) ?>">
</head>
<body>
<aside class="sidebar">
    <div class="brand">
        <span class="brand-mark"><i class="bi bi-bounding-box-circles"></i></span>
        <div><b>CIVICVISION</b><small>TRAFFIC AI</small></div>
    </div>
    <nav>
        <a class="active" href="#overview"><i class="bi bi-speedometer2"></i><span>Overview</span></a>
        <a href="#live-monitoring"><i class="bi bi-camera-video"></i><span>Live Monitoring</span></a>
        <a href="#reports"><i class="bi bi-bar-chart-line"></i><span>Reports</span></a>
        <a href="#violations"><i class="bi bi-exclamation-triangle"></i><span>Violations</span></a>
    </nav>
    <div class="sidebar-foot">
        <div class="secure"><i class="bi bi-database-check"></i><div><b>MySQL Analytics</b><small>Vehicle crossings stored continuously</small></div></div>
    </div>
</aside>

<main id="overview">
    <header class="topbar">
        <button class="menu" id="menu" type="button" aria-label="Toggle navigation"><i class="bi bi-list"></i></button>
        <div>
            <p class="eyebrow">TIRUPPUR CITY MUNICIPAL CORPORATION</p>
            <h1>Traffic Monitoring Dashboard</h1>
        </div>
        <div class="top-actions">
            <div class="live-pill pending" id="systemLive"><i></i><span>CHECKING CAMERAS</span></div>
            <div class="avatar">CV</div>
        </div>
    </header>

    <section class="content">
        <div class="hero-row">
            <div><p class="muted">Live camera monitoring, vehicle crossing counts, helmet violations and time-based reports.</p></div>
            <div class="date-chip"><i class="bi bi-calendar3"></i><?= e(date('D, d M Y · h:i A')) ?></div>
        </div>

        <div class="operator-summary">
            <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-camera-video"></i></span><div><small>ACTIVE CAMERAS</small><strong id="activeCameraCount">0 / <?= count($cameras) ?></strong><p id="cameraNetworkText">Checking camera streams</p></div></article>
            <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-car-front-fill"></i></span><div><small>VEHICLES COUNTED TODAY</small><strong id="todayVehicleCount">0</strong><p>Unique line-crossing events in MySQL</p></div></article>
            <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-person-x-fill"></i></span><div><small>NO-HELMET TODAY</small><strong id="todayNoHelmetCount">0</strong><p>Confirmed violation records</p></div></article>
            <article class="operator-kpi"><span class="kpi-icon"><i class="bi bi-cpu"></i></span><div><small>AI STATUS</small><strong id="aiDetectionStatus">STARTING</strong><p id="aiDetectionText">YOLO + ByteTrack</p></div></article>
        </div>

        <div class="status-strip" aria-label="Advanced detection readiness">
            <span class="status-chip" id="helmetReadiness"><i class="bi bi-person-badge"></i><span>Helmet model: checking</span></span>
            <span class="status-chip" id="plateReadiness"><i class="bi bi-card-text"></i><span>Plate OCR: checking</span></span>
            <span class="status-chip" id="roadDamageReadiness"><i class="bi bi-cone-striped"></i><span>Road damage: checking</span></span>
            <span class="status-chip" id="roadObstructionReadiness"><i class="bi bi-signpost-split"></i><span>Road obstruction: checking</span></span>
        </div>

        <section class="section-block" id="live-monitoring">
            <div class="section-heading"><div><h2>Live Monitoring</h2><p>Click any camera to open a large view. AI Tracking is the default stream.</p></div></div>
            <div class="operator-camera-grid">
                <?php foreach ($cameras as $camera): ?>
                    <article class="operator-camera-card" data-camera-ip="<?= e($camera['ip']) ?>" data-camera-number="<?= (int)$camera['number'] ?>">
                        <div class="operator-camera-head"><div class="operator-camera-title"><span class="cam-dot"></span><div><h3><?= e($camera['name']) ?></h3><p><?= e($camera['ip']) ?></p></div></div><span class="camera-state-text" id="cameraState<?= (int)$camera['number'] ?>">CONNECTING</span></div>
                        <div class="operator-camera-frame camera-frame">
                            <img id="cameraStream<?= (int)$camera['number'] ?>" src="<?= e($camera['ai_url']) ?>" data-ai-stream-url="<?= e($camera['ai_url']) ?>" data-raw-stream-url="<?= e($camera['raw_url']) ?>" data-stream-mode="ai" alt="CCTV stream <?= e($camera['name']) ?>">
                            <div class="stream-message" id="streamMessage<?= (int)$camera['number'] ?>"><i class="bi bi-camera-video"></i><b>WAITING FOR STREAM</b><span>Starting AI feed...</span></div>
                            <div class="camera-overlay-top"><span><?= e($camera['code']) ?></span><span class="camera-clock"></span></div>
                        </div>
                        <div class="operator-camera-toolbar"><div class="operator-mode-switch"><button type="button" class="active" id="aiMode<?= (int)$camera['number'] ?>" onclick="switchCameraMode(<?= (int)$camera['number'] ?>,'ai')"><i class="bi bi-bounding-box"></i> AI</button><button type="button" id="rawMode<?= (int)$camera['number'] ?>" onclick="switchCameraMode(<?= (int)$camera['number'] ?>,'raw')"><i class="bi bi-camera-video"></i> Raw</button></div><button type="button" class="camera-action-btn" onclick="reconnectCamera(<?= (int)$camera['number'] ?>)"><i class="bi bi-arrow-clockwise"></i> Reconnect</button></div>
                        <div class="camera-useful-stats"><div><small>VISIBLE VEHICLES</small><b id="vehicleCount<?= (int)$camera['number'] ?>">0</b></div><div><small>VISIBLE PERSONS</small><b id="personCount<?= (int)$camera['number'] ?>">0</b></div><div><small>COUNTED TODAY</small><b id="cameraTodayCount<?= (int)$camera['number'] ?>">0</b></div><div><small>AI LATENCY</small><b id="inferenceMs<?= (int)$camera['number'] ?>">—</b></div></div>
                    </article>
                <?php endforeach; ?>
            </div>
        </section>

        <section class="section-block" id="reports">
            <div class="section-heading"><div><h2>Vehicle Count Reports</h2><p>Filter by day, date range, exact time range and camera. Values are read directly from MySQL crossing records.</p></div></div>
            <article class="report-panel">
                <div class="report-filter-grid">
                    <div class="report-field"><label for="reportFromDate">FROM DATE</label><input type="date" id="reportFromDate" value="<?= e($today) ?>"></div>
                    <div class="report-field"><label for="reportToDate">TO DATE</label><input type="date" id="reportToDate" value="<?= e($today) ?>"></div>
                    <div class="report-field"><label for="reportFromTime">FROM TIME</label><input type="time" id="reportFromTime" value="00:00"></div>
                    <div class="report-field"><label for="reportToTime">TO TIME</label><input type="time" id="reportToTime" value="23:59"></div>
                    <div class="report-field"><label for="reportCamera">CAMERA</label><select id="reportCamera"><option value="">All Cameras</option><?php foreach ($cameras as $camera): ?><option value="<?= e($camera['ip']) ?>"><?= e($camera['name']) ?> · <?= e($camera['ip']) ?></option><?php endforeach; ?></select></div>
                    <div class="report-actions"><button type="button" class="secondary" id="btnTodayReport">Today</button><button type="button" class="primary" id="btnRefreshReport"><i class="bi bi-search"></i> Apply</button></div>
                </div>

                <div class="report-kpis">
                    <div class="report-kpi total"><small>TOTAL VEHICLES</small><strong id="rptTotalVehicles">0</strong></div>
                    <div class="report-kpi"><small>MOTORCYCLES</small><strong id="rptTotalMotorcycles">0</strong></div>
                    <div class="report-kpi"><small>CARS</small><strong id="rptTotalCars">0</strong></div>
                    <div class="report-kpi"><small>BUSES</small><strong id="rptTotalBuses">0</strong></div>
                    <div class="report-kpi"><small>TRUCKS</small><strong id="rptTotalTrucks">0</strong></div>
                    <div class="report-kpi"><small>BICYCLES</small><strong id="rptTotalBicycles">0</strong></div>
                </div>

                <div class="report-message" id="reportMessage">Loading vehicle count report...</div>
                <div class="report-table-grid">
                    <div class="data-card"><div class="data-card-head"><h3><i class="bi bi-calendar3"></i> Day-wise Count</h3><small>One row per day</small></div><div class="table-scroll"><table class="ops-table" id="tblDailyReport"><thead><tr><th>Date</th><th>Motorcycles</th><th>Cars</th><th>Buses</th><th>Trucks</th><th>Bicycles</th><th>Total Vehicles</th></tr></thead><tbody><tr><td colspan="7" class="empty-row">Loading...</td></tr></tbody></table></div></div>
                    <div class="data-card"><div class="data-card-head"><h3><i class="bi bi-clock-history"></i> Time-wise Count</h3><small>Hourly totals inside selected period</small></div><div class="table-scroll"><table class="ops-table" id="tblHourlyReport"><thead><tr><th>Date</th><th>Time</th><th>Motorcycles</th><th>Cars</th><th>Buses</th><th>Trucks</th><th>Bicycles</th><th>Total Vehicles</th></tr></thead><tbody><tr><td colspan="8" class="empty-row">Loading...</td></tr></tbody></table></div></div>
                    <div class="data-card"><div class="data-card-head"><h3><i class="bi bi-camera-video"></i> Camera-wise Count</h3><small>Stored crossings by camera</small></div><div class="table-scroll"><table class="ops-table" id="tblCameraReport"><thead><tr><th>Camera</th><th>IP</th><th>Motorcycles</th><th>Cars</th><th>Buses</th><th>Trucks</th><th>Bicycles</th><th>Total Vehicles</th></tr></thead><tbody><tr><td colspan="8" class="empty-row">Loading...</td></tr></tbody></table></div></div>
                </div>
            </article>
        </section>

        <section class="section-block" id="violations">
            <div class="section-heading"><div><h2>Recent No-Helmet Violations</h2><p>Confirmed records with evidence; number plate is shown when OCR succeeds.</p></div><div><button type="button" class="camera-action-btn" id="btnRefreshViolations"><i class="bi bi-arrow-clockwise"></i> Refresh</button></div></div>
            <div class="report-message" id="violationStatus">Loading recent violations...</div>
            <div class="violations-grid" id="violationsGrid"></div>
        </section>
    </section>

    <footer>© <?= date('Y') ?> CivicVision AI · Municipal Video Intelligence Platform</footer>
</main>

<script>
window.CCTV_UI_CONFIG = {
    healthUrl: <?= json_encode($stream_base . '/health', JSON_UNESCAPED_SLASHES) ?>,
    baseUrl: <?= json_encode($stream_base, JSON_UNESCAPED_SLASHES) ?>,
    reportApiUrl: 'report_api.php',
    cameraIps: <?= json_encode(array_values($camera_ips), JSON_UNESCAPED_SLASHES) ?>
};
</script>
<script src="assets/app.js?v=<?= e($asset_version) ?>"></script>
</body>
</html>
