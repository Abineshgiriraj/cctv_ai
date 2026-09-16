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

function env_truthy(array $env, string $key, bool $default = false): bool {
    if (!array_key_exists($key, $env)) return $default;
    return in_array(strtolower(trim((string)$env[$key])), ['1', 'true', 'yes', 'on'], true);
}

function make_camera_row(string $ip, int $channel, string $name, string $area, string $stream_base): ?array {
    $ip = trim($ip, " \t\n\r\0\x0B\"'");
    if ($ip === '') return null;
    $channel = max(1, $channel);
    $name = trim($name, " \t\n\r\0\x0B\"'");
    $area = trim($area, " \t\n\r\0\x0B\"'");
    $key = $ip . '_ch' . $channel;
    return [
        'camera_key' => $key,
        'ip' => $ip,
        'channel' => $channel,
        'name' => $name !== '' ? $name : ('Camera ' . $ip),
        'area' => $area !== '' ? $area : 'Unknown Area',
        'raw_url' => $stream_base . '/video_feed/' . rawurlencode($key),
        'ai_url' => $stream_base . '/tracked_feed/' . rawurlencode($key),
    ];
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
            $camera = make_camera_row($items[0], (int)$items[1], $items[2], $items[3], $stream_base);
            if ($camera) $cameras[] = $camera;
        }
    }
} else {
    $camera_ips = array_values(array_filter(array_map('trim', explode(',', $ui_env['CAMERA_IPS'] ?? '192.168.0.241,192.168.0.242'))));
    $camera_areas = array_map('trim', explode(',', $ui_env['CAMERA_AREAS'] ?? ''));
    foreach ($camera_ips as $index => $ip) {
        $n = $index + 1;
        $area = $camera_areas[$index] ?? '';
        if ($area === '') $area = 'Camera ' . $n . ' Area';
        $camera = make_camera_row($ip, 1, 'Camera ' . $n, $area, $stream_base);
        if ($camera) $cameras[] = $camera;
    }
}

// Merge the non-secret inventory file into the local camera list. The .env entry
// wins when the same IP+channel exists in both sources.
if (env_truthy($ui_env, 'CAMERA_INVENTORY_ENABLED', true)) {
    $inventory_file = trim($ui_env['CAMERA_INVENTORY_FILE'] ?? 'camera_inventory.json');
    if ($inventory_file === '') $inventory_file = 'camera_inventory.json';
    if (!preg_match('/^[A-Za-z]:[\\\\\/]/', $inventory_file) && !str_starts_with($inventory_file, '/')) {
        $inventory_file = __DIR__ . DIRECTORY_SEPARATOR . $inventory_file;
    }
    if (is_readable($inventory_file)) {
        $decoded = json_decode((string)file_get_contents($inventory_file), true);
        if (is_array($decoded)) {
            $seen = [];
            foreach ($cameras as $camera) $seen[$camera['camera_key']] = true;
            foreach ($decoded as $row) {
                if (!is_array($row)) continue;
                $camera = make_camera_row(
                    (string)($row['camera_ip'] ?? ''),
                    (int)($row['channel_no'] ?? 1),
                    (string)($row['camera_name'] ?? ''),
                    (string)($row['area_name'] ?? ''),
                    $stream_base
                );
                if (!$camera || isset($seen[$camera['camera_key']])) continue;
                $cameras[] = $camera;
                $seen[$camera['camera_key']] = true;
            }
        }
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
        'incidents' => ['incidents.php', 'bi-car-front-fill', 'Incidents'],
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
    <link rel="stylesheet" href="assets/ops.css">
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
<script src="assets/app_fixed.js?v=3"></script>
<?php foreach ($extraScripts as $src): ?>
<script src="<?= e($src) ?>"></script>
<?php endforeach; ?>
</body>
</html>
<?php
}
