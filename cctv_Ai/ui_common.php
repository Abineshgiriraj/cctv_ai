<?php
date_default_timezone_set('Asia/Kolkata');

function normalize_env_value(string $value): string {
    $value = trim($value);
    $length = strlen($value);
    if ($length >= 2) {
        $first = $value[0];
        $last = $value[$length - 1];
        if (($first === '"' && $last === '"') || ($first === "'" && $last === "'")) {
            $value = substr($value, 1, -1);
        }
    }
    return trim($value);
}

function load_env_file(string $path): array {
    $env = [];
    if (!is_readable($path)) return $env;
    foreach (file($path, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
        $line = trim($line);
        if ($line === '' || $line[0] === '#' || strpos($line, '=') === false) continue;
        [$key, $value] = explode('=', $line, 2);
        $env[trim($key)] = normalize_env_value($value);
    }
    return $env;
}

$ui_env = load_env_file(__DIR__ . DIRECTORY_SEPARATOR . '.env');
$stream_base = rtrim($ui_env['BACKEND_URL'] ?? 'http://127.0.0.1:5000', '/');

$cameras = [];
$config_str = $ui_env['CAMERA_CONFIG'] ?? '';
if ($config_str !== '') {
    $parts = array_filter(array_map('trim', explode(',', $config_str)));
    foreach ($parts as $part) {
        $items = array_map('trim', explode('|', $part));
        if (count($items) >= 4) {
            $ip = trim($items[0], " \t\n\r\0\x0B\"'");
            $ch = max(1, (int)$items[1]);
            $name = trim($items[2], " \t\n\r\0\x0B\"'");
            $area = trim($items[3], " \t\n\r\0\x0B\"'");
            if ($ip === '') continue;
            $key = $ip . '_ch' . $ch;
            $cameras[] = [
                'camera_key' => $key,
                'ip' => $ip,
                'channel' => $ch,
                'name' => $name,
                'area' => $area,
                'raw_url' => $stream_base . '/video_feed/' . rawurlencode($key),
                'ai_url' => $stream_base . '/tracked_feed/' . rawurlencode($key),
            ];
        }
    }
} else {
    $camera_ips = array_values(array_filter(array_map('trim', explode(',', $ui_env['CAMERA_IPS'] ?? '192.168.0.241,192.168.0.242'))));
    $camera_areas = array_map('trim', explode(',', $ui_env['CAMERA_AREAS'] ?? ''));
    foreach ($camera_ips as $index => $ip) {
        $n = $index + 1;
        $area = $camera_areas[$index] ?? '';
        if ($area === '') $area = 'Camera ' . $n . ' Area';
        $key = $ip . '_ch1';
        $cameras[] = [
            'camera_key' => $key,
            'ip' => $ip,
            'channel' => 1,
            'name' => 'Camera ' . $n,
            'area' => $area,
            'raw_url' => $stream_base . '/video_feed/' . rawurlencode($key),
            'ai_url' => $stream_base . '/tracked_feed/' . rawurlencode($key),
        ];
    }
}

$title_cache_path = __DIR__ . DIRECTORY_SEPARATOR . 'data' . DIRECTORY_SEPARATOR . 'nvr_channel_titles.json';
if (is_readable($title_cache_path)) {
    $decoded = json_decode((string)file_get_contents($title_cache_path), true);
    if (is_array($decoded)) {
        foreach ($cameras as &$camera) {
            $cached = $decoded[$camera['camera_key']] ?? null;
            if (!is_array($cached)) continue;
            $title = trim((string)($cached['camera_name'] ?? ''));
            if ($title === '') continue;
            $camera['name'] = $title;
            if ($camera['area'] === '' || stripos($camera['area'], 'Recorder ') === 0) {
                $camera['area'] = trim((string)($cached['area_name'] ?? $title)) ?: $title;
            }
        }
        unset($camera);
    }
}

function e(string $value): string { return htmlspecialchars($value, ENT_QUOTES, 'UTF-8'); }

function render_page_start(string $active, string $title, string $subtitle = ''): void {
    $items = [
        'overview' => ['index.php', 'bi-speedometer2', 'Overview'],
        'live' => ['live.php', 'bi-camera-video', 'Live Monitoring'],
        'reports' => ['reports.php', 'bi-bar-chart-line', 'Vehicle Reports'],
        'violations' => ['violations.php', 'bi-exclamation-triangle', 'Helmet Violations'],
        'road' => ['road_damage.php', 'bi-cone-striped', 'Road Damage'],
        'incidents' => ['incidents.php', 'bi-exclamation-octagon', 'Incidents'],
    ];
    ?>
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>CivicVision AI - <?= e($title) ?></title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
    <link rel="stylesheet" href="assets/app.css">
    <link rel="stylesheet" href="assets/ops.css?v=8">
    <link rel="stylesheet" href="assets/road.css">
</head>
<body>
<aside class="sidebar">
    <div class="brand">
        <span class="brand-mark"><i class="bi bi-bounding-box-circles"></i></span>
        <div><b>CIVICVISION</b><small>TRAFFIC AI</small></div>
    </div>
    <nav>
        <?php foreach ($items as $key => [$href, $icon, $label]): ?>
            <a class="<?= $active === $key ? 'active' : '' ?>" href="<?= e($href) ?>"><i class="bi <?= e($icon) ?>"></i><span><?= e($label) ?></span></a>
        <?php endforeach; ?>
    </nav>
    <div class="sidebar-foot">
        <div class="secure"><i class="bi bi-database-check"></i><div><b>MySQL Analytics</b><small>Detection events stored continuously</small></div></div>
    </div>
</aside>
<main>
    <header class="topbar">
        <button class="menu" id="menu" type="button" aria-label="Toggle navigation"><i class="bi bi-list"></i></button>
        <div>
            <p class="eyebrow">TIRUPPUR CITY MUNICIPAL CORPORATION</p>
            <h1><?= e($title) ?></h1>
        </div>
        <div class="top-actions">
            <div class="live-pill pending" id="systemLive"><i></i><span>CHECKING CAMERAS</span></div>
            <div class="avatar">CV</div>
        </div>
    </header>
    <section class="content">
        <div class="hero-row">
            <div><p class="muted"><?= e($subtitle) ?></p></div>
            <div class="date-chip"><i class="bi bi-calendar3"></i><?= e(date('D, d M Y · h:i A')) ?></div>
        </div>
    <?php
}

function render_page_end(array $extraScripts = []): void {
    global $cameras, $stream_base;
    ?>
    </section>
    <footer>© <?= date('Y') ?> CivicVision AI · Municipal Video Intelligence Platform</footer>
</main>
<script>
window.CCTV_UI_CONFIG = {
    healthUrl: <?= json_encode($stream_base . '/health', JSON_UNESCAPED_SLASHES) ?>,
    baseUrl: <?= json_encode($stream_base, JSON_UNESCAPED_SLASHES) ?>,
    cameras: <?= json_encode($cameras, JSON_UNESCAPED_SLASHES) ?>
};
</script>
<script src="assets/app_fixed.js?v=8"></script>
<?php foreach ($extraScripts as $src): ?>
<script src="<?= e($src) ?>"></script>
<?php endforeach; ?>
</body>
</html>
<?php
}
