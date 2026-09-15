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
?>
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>CivicVision AI Command Center</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link rel="stylesheet" href="assets/app.css">
</head>
<body>
<aside class="sidebar">
    <div class="brand">
        <span class="brand-mark"><i class="bi bi-bounding-box-circles"></i></span>
        <div><b>CIVICVISION</b><small>AI COMMAND CENTER</small></div>
    </div>
    <nav>
        <a class="active" href="#overview"><i class="bi bi-grid-1x2"></i><span>Overview</span></a>
        <a href="#live-cameras"><i class="bi bi-camera-video"></i><span>Camera Grid</span></a>
        <a href="#stream-status"><i class="bi bi-broadcast"></i><span>Stream Status</span></a>
        <a href="#detections"><i class="bi bi-activity"></i><span>Detections</span></a>
        <a href="#reports"><i class="bi bi-bar-chart"></i><span>Reports</span></a>
    </nav>
    <div class="sidebar-foot">
        <div class="secure"><i class="bi bi-shield-check"></i><div><b>Credentials protected</b><small>Loaded server-side from .env</small></div></div>
    </div>
</aside>

<main id="overview">
    <header class="topbar">
        <button class="menu" id="menu" type="button" aria-label="Toggle navigation"><i class="bi bi-list"></i></button>
        <div><p class="eyebrow">TIRUPPUR CITY MUNICIPAL CORPORATION</p><h1>Operations Overview</h1></div>
        <div class="top-actions">
            <div class="live-pill" id="systemLive"><i></i><span>CHECKING STREAMS</span></div>
            <button class="icon-btn" type="button" aria-label="Streaming status"><i class="bi bi-broadcast-pin"></i></button>
            <div class="avatar">CV</div>
        </div>
    </header>

    <section class="content">
        <div class="hero-row">
            <div><p class="muted">Real-time MJPEG monitoring with YOLOv8 + ByteTrack person and vehicle tracking.</p></div>
            <div class="date-chip"><i class="bi bi-calendar3"></i><?= e(date('D, d M Y · h:i A')) ?></div>
        </div>

        <div class="stats">
            <article>
                <span class="stat-icon cyan"><i class="bi bi-camera-video"></i></span>
                <div><small>ACTIVE CAMERAS</small><strong id="activeCameraCount">0 <em>/ <?= count($cameras) ?></em></strong><p><i class="dot good"></i> <span id="cameraNetworkText">Checking backend</span></p></div>
            </article>
            <article>
                <span class="stat-icon purple"><i class="bi bi-broadcast"></i></span>
                <div><small>STREAM DELIVERY</small><strong class="compact-stat">MJPEG</strong><p>Original raw endpoint preserved</p></div>
            </article>
            <article>
                <span class="stat-icon amber"><i class="bi bi-cpu"></i></span>
                <div><small>AI DETECTION</small><strong class="compact-stat" id="aiDetectionStatus">STARTING</strong><p id="aiDetectionText">YOLOv8n + ByteTrack</p></div>
            </article>
            <article>
                <span class="stat-icon green"><i class="bi bi-shield-lock"></i></span>
                <div><small>CAMERA CREDENTIALS</small><strong class="compact-stat">.ENV</strong><p>Not exposed to browser JavaScript</p></div>
            </article>
        </div>

        <div class="dashboard-grid dashboard-grid-live" id="live-cameras">
            <article class="panel live-panel dual-live-panel">
                <div class="panel-head">
                    <div><h2>Live Camera Grid</h2><p>AI Tracking is default; RAW Live remains available as a safe fallback.</p></div>
                    <div><span class="badge live"><i></i> LIVE MONITORING</span></div>
                </div>

                <div class="live-camera-grid">
                    <?php foreach ($cameras as $camera): ?>
                        <section class="camera-card" data-camera-ip="<?= e($camera['ip']) ?>" data-camera-number="<?= (int)$camera['number'] ?>">
                            <div class="camera-card-head">
                                <div>
                                    <span class="camera-label"><?= e($camera['code']) ?></span>
                                    <h3><?= e($camera['name']) ?></h3>
                                    <p><?= e($camera['ip']) ?></p>
                                </div>
                                <span class="camera-state pending" id="cameraState<?= (int)$camera['number'] ?>"><i></i><span>AI STARTING</span></span>
                            </div>

                            <div class="camera-frame live-mjpeg-frame">
                                <img
                                    id="cameraStream<?= (int)$camera['number'] ?>"
                                    class="live-stream"
                                    src="<?= e($camera['ai_url']) ?>"
                                    data-ai-stream-url="<?= e($camera['ai_url']) ?>"
                                    data-raw-stream-url="<?= e($camera['raw_url']) ?>"
                                    data-stream-mode="ai"
                                    alt="AI CCTV stream from <?= e($camera['ip']) ?>"
                                >
                                <div class="stream-message" id="streamMessage<?= (int)$camera['number'] ?>">
                                    <i class="bi bi-cpu"></i>
                                    <b>WAITING FOR AI FRAMES</b>
                                    <span>YOLOv8 + ByteTrack is loading</span>
                                </div>
                                <div class="camera-top"><span><?= e($camera['code']) ?></span><span class="camera-clock"></span></div>
                                <div class="focus-corners"></div>
                                <div class="camera-bottom"><span><i class="bi bi-circle-fill"></i> <b id="streamModeLabel<?= (int)$camera['number'] ?>">AI TRACKING LIVE</b></span><span><?= e($camera['ip']) ?></span></div>
                            </div>

                            <div class="camera-tools camera-tools-ai">
                                <div class="mode-switch" role="group" aria-label="Stream mode">
                                    <button type="button" class="mode-btn active" id="aiMode<?= (int)$camera['number'] ?>" onclick="switchCameraMode(<?= (int)$camera['number'] ?>, 'ai')"><i class="bi bi-bounding-box"></i> AI Tracking</button>
                                    <button type="button" class="mode-btn" id="rawMode<?= (int)$camera['number'] ?>" onclick="switchCameraMode(<?= (int)$camera['number'] ?>, 'raw')"><i class="bi bi-camera-video"></i> Raw Live</button>
                                </div>
                                <button type="button" onclick="reconnectCamera(<?= (int)$camera['number'] ?>)"><i class="bi bi-arrow-clockwise"></i> Reconnect</button>
                            </div>

                            <div class="camera-ai-stats">
                                <span><small>PERSONS</small><b id="personCount<?= (int)$camera['number'] ?>">0</b></span>
                                <span><small>VEHICLES</small><b id="vehicleCount<?= (int)$camera['number'] ?>">0</b></span>
                                <span><small>INFER FPS</small><b id="aiFps<?= (int)$camera['number'] ?>">—</b></span>
                                <span><small>RAW FRAMES</small><b id="frameCount<?= (int)$camera['number'] ?>">—</b></span>
                            </div>
                            <article class="panel reports-panel" id="reports">
            <div class="panel-head">
                <div>
                    <h2>Traffic & Object Analytics Reports</h2>
                    <p>Historical object counts stored in MySQL database. Filter by date or camera.</p>
                </div>
                <div class="report-controls">
                    <div class="filter-group">
                        <label for="reportDate"><i class="bi bi-calendar-event"></i> Date:</label>
                        <input type="date" id="reportDate" class="report-input" value="<?= e(date('Y-m-d')) ?>">
                    </div>
                    <div class="filter-group">
                        <label for="reportCamera"><i class="bi bi-camera-video"></i> Camera:</label>
                        <select id="reportCamera" class="report-select">
                            <option value="">All Cameras</option>
                            <?php foreach ($cameras as $camera): ?>
                                <option value="<?= e($camera['ip']) ?>"><?= e($camera['name']) ?> (<?= e($camera['ip']) ?>)</option>
                            <?php endforeach; ?>
                        </select>
                    </div>
                    <button type="button" id="btnRefreshReport" class="btn-refresh">
                        <i class="bi bi-arrow-clockwise"></i> Refresh Report
                    </button>
                </div>
            </div>

            <div class="reports-stats-grid">
                <div class="report-card card-person">
                    <div class="card-icon"><i class="bi bi-person-fill"></i></div>
                    <div class="card-info"><small>TOTAL PERSONS</small><strong id="rptTotalPersons">0</strong></div>
                </div>
                <div class="report-card card-vehicle">
                    <div class="card-icon"><i class="bi bi-truck-front-fill"></i></div>
                    <div class="card-info"><small>TOTAL VEHICLES</small><strong id="rptTotalVehicles">0</strong></div>
                </div>
                <div class="report-card card-car">
                    <div class="card-icon"><i class="bi bi-car-front-fill"></i></div>
                    <div class="card-info"><small>TOTAL CARS</small><strong id="rptTotalCars">0</strong></div>
                </div>
                <div class="report-card card-motorcycle">
                    <div class="card-icon"><i class="bi bi-bicycle"></i></div>
                    <div class="card-info"><small>TOTAL MOTORCYCLES</small><strong id="rptTotalMotorcycles">0</strong></div>
                </div>
                <div class="report-card card-bus">
                    <div class="card-icon"><i class="bi bi-bus-front-fill"></i></div>
                    <div class="card-info"><small>TOTAL BUSES</small><strong id="rptTotalBuses">0</strong></div>
                </div>
                <div class="report-card card-truck">
                    <div class="card-icon"><i class="bi bi-truck"></i></div>
                    <div class="card-info"><small>TOTAL TRUCKS</small><strong id="rptTotalTrucks">0</strong></div>
                </div>
                <div class="report-card card-other">
                    <div class="card-icon"><i class="bi bi-box-seam-fill"></i></div>
                    <div class="card-info"><small>OTHER OBJECTS</small><strong id="rptTotalOther">0</strong></div>
                </div>
                <div class="report-card card-grand">
                    <div class="card-icon"><i class="bi bi-calculator-fill"></i></div>
                    <div class="card-info"><small>GRAND TOTAL</small><strong id="rptGrandTotal">0</strong></div>
                </div>
            </div>

            <div class="reports-tables-grid">
                <div class="table-card">
                    <div class="table-head">
                        <h3><i class="bi bi-camera-video"></i> Camera-Wise Breakdown</h3>
                    </div>
                    <div class="table-wrapper">
                        <table class="report-table" id="tblCameraBreakdown">
                            <thead>
                                <tr>
                                    <th>Camera</th>
                                    <th>IP Address</th>
                                    <th>Persons</th>
                                    <th>Cars</th>
                                    <th>Motorcycles</th>
                                    <th>Buses</th>
                                    <th>Trucks</th>
                                    <th>Other</th>
                                    <th>Total Objects</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr><td colspan="9" class="text-center">Loading camera report...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <div class="table-card">
                    <div class="table-head">
                        <h3><i class="bi bi-pie-chart"></i> Class-Wise Summary</h3>
                    </div>
                    <div class="table-wrapper">
                        <table class="report-table" id="tblClassBreakdown">
                            <thead>
                                <tr>
                                    <th>Object Class</th>
                                    <th>Total Count</th>
                                    <th>Percentage</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr><td colspan="3" class="text-center">Loading class summary...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </article>
    </section>
                    <?php endforeach; ?>
                </div>
            </article>

            <aside class="panel stream-architecture" id="stream-status">
                <div class="panel-head"><div><h2>Streaming Architecture</h2><p>One RTSP capture per camera; AI reuses captured frames</p></div></div>
                <div class="architecture-list">
                    <div><span>1</span><i class="bi bi-camera-video"></i><p><b>IP Cameras</b><small><?= e(implode(' · ', $camera_ips)) ?></small></p></div>
                    <div><span>2</span><i class="bi bi-diagram-3"></i><p><b>RTSP over TCP</b><small>Credentials loaded only by Python</small></p></div>
                    <div><span>3</span><i class="bi bi-filetype-py"></i><p><b>OpenCV Capture</b><small>One working capture thread per camera</small></p></div>
                    <div><span>4</span><i class="bi bi-cpu"></i><p><b>YOLOv8 + ByteTrack</b><small>Person, bicycle, car, motorcycle, bus, truck</small></p></div>
                    <div><span>5</span><i class="bi bi-bounding-box"></i><p><b>Tracked JPEG Frames</b><small>Boxes, confidence, tracking ID and motion trail</small></p></div>
                    <div><span>6</span><i class="bi bi-browser-chrome"></i><p><b>Browser MJPEG</b><small>/tracked_feed/&lt;camera_ip&gt;</small></p></div>
                </div>
                <div class="backend-health"><span>Backend health</span><b id="backendHealth">Checking http://127.0.0.1:5000/health</b></div>
            </aside>
        </div>

        <article class="panel detection-panel" id="detections">
            <div class="panel-head">
                <div><h2>Live AI Detections</h2><p>Current objects visible in each camera frame. Counts are not cumulative traffic totals.</p></div>
                <div><span class="badge live"><i></i> YOLO + BYTETRACK</span></div>
            </div>
            <div class="detection-grid">
                <?php foreach ($cameras as $camera): ?>
                    <section class="detection-card">
                        <div class="detection-card-head"><div><span><?= e($camera['code']) ?></span><b><?= e($camera['ip']) ?></b></div><em id="aiState<?= (int)$camera['number'] ?>">Starting</em></div>
                        <div class="detection-metrics">
                            <div><i class="bi bi-person-bounding-box"></i><span><small>Persons</small><b id="detPerson<?= (int)$camera['number'] ?>">0</b></span></div>
                            <div><i class="bi bi-car-front"></i><span><small>Vehicles</small><b id="detVehicle<?= (int)$camera['number'] ?>">0</b></span></div>
                            <div><i class="bi bi-stopwatch"></i><span><small>Inference</small><b id="inferenceMs<?= (int)$camera['number'] ?>">—</b></span></div>
                        </div>
                        <p class="class-counts" id="classCounts<?= (int)$camera['number'] ?>">Waiting for detections…</p>
                        <article class="panel reports-panel" id="reports">
            <div class="panel-head">
                <div>
                    <h2>Traffic & Object Analytics Reports</h2>
                    <p>Historical object counts stored in MySQL database. Filter by date or camera.</p>
                </div>
                <div class="report-controls">
                    <div class="filter-group">
                        <label for="reportDate"><i class="bi bi-calendar-event"></i> Date:</label>
                        <input type="date" id="reportDate" class="report-input" value="<?= e(date('Y-m-d')) ?>">
                    </div>
                    <div class="filter-group">
                        <label for="reportCamera"><i class="bi bi-camera-video"></i> Camera:</label>
                        <select id="reportCamera" class="report-select">
                            <option value="">All Cameras</option>
                            <?php foreach ($cameras as $camera): ?>
                                <option value="<?= e($camera['ip']) ?>"><?= e($camera['name']) ?> (<?= e($camera['ip']) ?>)</option>
                            <?php endforeach; ?>
                        </select>
                    </div>
                    <button type="button" id="btnRefreshReport" class="btn-refresh">
                        <i class="bi bi-arrow-clockwise"></i> Refresh Report
                    </button>
                </div>
            </div>

            <div class="reports-stats-grid">
                <div class="report-card card-person">
                    <div class="card-icon"><i class="bi bi-person-fill"></i></div>
                    <div class="card-info"><small>TOTAL PERSONS</small><strong id="rptTotalPersons">0</strong></div>
                </div>
                <div class="report-card card-vehicle">
                    <div class="card-icon"><i class="bi bi-truck-front-fill"></i></div>
                    <div class="card-info"><small>TOTAL VEHICLES</small><strong id="rptTotalVehicles">0</strong></div>
                </div>
                <div class="report-card card-car">
                    <div class="card-icon"><i class="bi bi-car-front-fill"></i></div>
                    <div class="card-info"><small>TOTAL CARS</small><strong id="rptTotalCars">0</strong></div>
                </div>
                <div class="report-card card-motorcycle">
                    <div class="card-icon"><i class="bi bi-bicycle"></i></div>
                    <div class="card-info"><small>TOTAL MOTORCYCLES</small><strong id="rptTotalMotorcycles">0</strong></div>
                </div>
                <div class="report-card card-bus">
                    <div class="card-icon"><i class="bi bi-bus-front-fill"></i></div>
                    <div class="card-info"><small>TOTAL BUSES</small><strong id="rptTotalBuses">0</strong></div>
                </div>
                <div class="report-card card-truck">
                    <div class="card-icon"><i class="bi bi-truck"></i></div>
                    <div class="card-info"><small>TOTAL TRUCKS</small><strong id="rptTotalTrucks">0</strong></div>
                </div>
                <div class="report-card card-other">
                    <div class="card-icon"><i class="bi bi-box-seam-fill"></i></div>
                    <div class="card-info"><small>OTHER OBJECTS</small><strong id="rptTotalOther">0</strong></div>
                </div>
                <div class="report-card card-grand">
                    <div class="card-icon"><i class="bi bi-calculator-fill"></i></div>
                    <div class="card-info"><small>GRAND TOTAL</small><strong id="rptGrandTotal">0</strong></div>
                </div>
            </div>

            <div class="reports-tables-grid">
                <div class="table-card">
                    <div class="table-head">
                        <h3><i class="bi bi-camera-video"></i> Camera-Wise Breakdown</h3>
                    </div>
                    <div class="table-wrapper">
                        <table class="report-table" id="tblCameraBreakdown">
                            <thead>
                                <tr>
                                    <th>Camera</th>
                                    <th>IP Address</th>
                                    <th>Persons</th>
                                    <th>Cars</th>
                                    <th>Motorcycles</th>
                                    <th>Buses</th>
                                    <th>Trucks</th>
                                    <th>Other</th>
                                    <th>Total Objects</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr><td colspan="9" class="text-center">Loading camera report...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <div class="table-card">
                    <div class="table-head">
                        <h3><i class="bi bi-pie-chart"></i> Class-Wise Summary</h3>
                    </div>
                    <div class="table-wrapper">
                        <table class="report-table" id="tblClassBreakdown">
                            <thead>
                                <tr>
                                    <th>Object Class</th>
                                    <th>Total Count</th>
                                    <th>Percentage</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr><td colspan="3" class="text-center">Loading class summary...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </article>
    </section>
                <?php endforeach; ?>
            </div>
        </article>

        <article class="panel integration-panel">
            <div class="panel-head"><div><h2>Integration Status</h2><p>Existing camera connection preserved; AI added after capture.</p></div></div>
            <div class="migration-grid">
                <div><i class="bi bi-check-circle"></i><span><b>Raw MJPEG preserved</b><small>/video_feed endpoints still work exactly as fallback streams</small></span></div>
                <div><i class="bi bi-check-circle"></i><span><b>No second RTSP connection</b><small>YOLO consumes the same OpenCV frames already captured by stream_server.py</small></span></div>
                <div><i class="bi bi-check-circle"></i><span><b>Independent camera trackers</b><small>Each camera has separate ByteTrack state and tracking IDs</small></span></div>
                <div><i class="bi bi-shield-check"></i><span><b>Credentials stay server-side</b><small>Frontend contains camera IPs only; no username or password</small></span></div>
            </div>
        </article>
        <article class="panel reports-panel" id="reports">
            <div class="panel-head">
                <div>
                    <h2>Traffic & Object Analytics Reports</h2>
                    <p>Historical object counts stored in MySQL database. Filter by date or camera.</p>
                </div>
                <div class="report-controls">
                    <div class="filter-group">
                        <label for="reportDate"><i class="bi bi-calendar-event"></i> Date:</label>
                        <input type="date" id="reportDate" class="report-input" value="<?= e(date('Y-m-d')) ?>">
                    </div>
                    <div class="filter-group">
                        <label for="reportCamera"><i class="bi bi-camera-video"></i> Camera:</label>
                        <select id="reportCamera" class="report-select">
                            <option value="">All Cameras</option>
                            <?php foreach ($cameras as $camera): ?>
                                <option value="<?= e($camera['ip']) ?>"><?= e($camera['name']) ?> (<?= e($camera['ip']) ?>)</option>
                            <?php endforeach; ?>
                        </select>
                    </div>
                    <button type="button" id="btnRefreshReport" class="btn-refresh">
                        <i class="bi bi-arrow-clockwise"></i> Refresh Report
                    </button>
                </div>
            </div>

            <div class="reports-stats-grid">
                <div class="report-card card-person">
                    <div class="card-icon"><i class="bi bi-person-fill"></i></div>
                    <div class="card-info"><small>TOTAL PERSONS</small><strong id="rptTotalPersons">0</strong></div>
                </div>
                <div class="report-card card-vehicle">
                    <div class="card-icon"><i class="bi bi-truck-front-fill"></i></div>
                    <div class="card-info"><small>TOTAL VEHICLES</small><strong id="rptTotalVehicles">0</strong></div>
                </div>
                <div class="report-card card-car">
                    <div class="card-icon"><i class="bi bi-car-front-fill"></i></div>
                    <div class="card-info"><small>TOTAL CARS</small><strong id="rptTotalCars">0</strong></div>
                </div>
                <div class="report-card card-motorcycle">
                    <div class="card-icon"><i class="bi bi-bicycle"></i></div>
                    <div class="card-info"><small>TOTAL MOTORCYCLES</small><strong id="rptTotalMotorcycles">0</strong></div>
                </div>
                <div class="report-card card-bus">
                    <div class="card-icon"><i class="bi bi-bus-front-fill"></i></div>
                    <div class="card-info"><small>TOTAL BUSES</small><strong id="rptTotalBuses">0</strong></div>
                </div>
                <div class="report-card card-truck">
                    <div class="card-icon"><i class="bi bi-truck"></i></div>
                    <div class="card-info"><small>TOTAL TRUCKS</small><strong id="rptTotalTrucks">0</strong></div>
                </div>
                <div class="report-card card-other">
                    <div class="card-icon"><i class="bi bi-box-seam-fill"></i></div>
                    <div class="card-info"><small>OTHER OBJECTS</small><strong id="rptTotalOther">0</strong></div>
                </div>
                <div class="report-card card-grand">
                    <div class="card-icon"><i class="bi bi-calculator-fill"></i></div>
                    <div class="card-info"><small>GRAND TOTAL</small><strong id="rptGrandTotal">0</strong></div>
                </div>
            </div>

            <div class="reports-tables-grid">
                <div class="table-card">
                    <div class="table-head">
                        <h3><i class="bi bi-camera-video"></i> Camera-Wise Breakdown</h3>
                    </div>
                    <div class="table-wrapper">
                        <table class="report-table" id="tblCameraBreakdown">
                            <thead>
                                <tr>
                                    <th>Camera</th>
                                    <th>IP Address</th>
                                    <th>Persons</th>
                                    <th>Cars</th>
                                    <th>Motorcycles</th>
                                    <th>Buses</th>
                                    <th>Trucks</th>
                                    <th>Other</th>
                                    <th>Total Objects</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr><td colspan="9" class="text-center">Loading camera report...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <div class="table-card">
                    <div class="table-head">
                        <h3><i class="bi bi-pie-chart"></i> Class-Wise Summary</h3>
                    </div>
                    <div class="table-wrapper">
                        <table class="report-table" id="tblClassBreakdown">
                            <thead>
                                <tr>
                                    <th>Object Class</th>
                                    <th>Total Count</th>
                                    <th>Percentage</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr><td colspan="3" class="text-center">Loading class summary...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </article>
    </section>

    <footer>© <?= date('Y') ?> CivicVision AI · Municipal Video Intelligence Platform</footer>
</main>

<script>
window.CCTV_UI_CONFIG = {
    healthUrl: <?= json_encode($stream_base . '/health', JSON_UNESCAPED_SLASHES) ?>,
    cameraIps: <?= json_encode(array_values($camera_ips), JSON_UNESCAPED_SLASHES) ?>
};
</script>
<script src="assets/app.js"></script>
</body>
</html>
