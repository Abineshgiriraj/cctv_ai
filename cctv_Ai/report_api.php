<?php

declare(strict_types=1);
date_default_timezone_set('Asia/Kolkata');
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');

function fail_json(string $message, int $status = 500, array $extra = []): never {
    http_response_code($status);
    echo json_encode(array_merge(['ok' => false, 'error' => $message], $extra), JSON_UNESCAPED_SLASHES);
    exit;
}

function read_env_file(string $path): array {
    $env = [];
    if (!is_readable($path)) return $env;
    foreach (file($path, FILE_IGNORE_NEW_LINES) ?: [] as $line) {
        $line = trim($line);
        if ($line === '' || str_starts_with($line, '#') || !str_contains($line, '=')) continue;
        [$key, $value] = explode('=', $line, 2);
        $key = trim($key);
        $value = trim($value);
        if ($value !== '' && (($value[0] === '"' && substr($value, -1) === '"') || ($value[0] === "'" && substr($value, -1) === "'"))) {
            $value = substr($value, 1, -1);
        }
        $env[$key] = $value;
    }
    return $env;
}

function empty_bucket(): array {
    return [
        'person' => 0,
        'motorcycle' => 0,
        'car' => 0,
        'bus' => 0,
        'truck' => 0,
        'bicycle' => 0,
        'other' => 0,
        'vehicles' => 0,
        'total' => 0,
        'grand_total' => 0,
    ];
}

function add_type(array &$bucket, string $type, int $count): void {
    $type = strtolower(trim($type));
    if ($type === 'person') {
        $bucket['person'] += $count;
        $bucket['grand_total'] += $count;
        return;
    }
    if (array_key_exists($type, $bucket) && !in_array($type, ['vehicles', 'total', 'grand_total', 'other'], true)) {
        $bucket[$type] += $count;
    } else {
        $bucket['other'] += $count;
    }
    $bucket['vehicles'] += $count;
    $bucket['total'] += $count;
    $bucket['grand_total'] += $count;
}

function bind_params(mysqli_stmt $stmt, string $types, array &$params): void {
    if ($types === '') return;
    $refs = [];
    $refs[] = $types;
    foreach ($params as $key => &$value) $refs[] = &$value;
    if (!call_user_func_array([$stmt, 'bind_param'], $refs)) {
        throw new RuntimeException('Unable to bind report query parameters');
    }
}

function query_rows(mysqli $db, string $sql, string $types = '', array $params = []): array {
    $stmt = $db->prepare($sql);
    if (!$stmt) throw new RuntimeException($db->error ?: 'Unable to prepare report query');
    try {
        bind_params($stmt, $types, $params);
        if (!$stmt->execute()) throw new RuntimeException($stmt->error ?: 'Unable to execute report query');
        $result = $stmt->get_result();
        if (!$result) return [];
        return $result->fetch_all(MYSQLI_ASSOC);
    } finally {
        $stmt->close();
    }
}

$env = read_env_file(__DIR__ . DIRECTORY_SEPARATOR . '.env');
$dbHost = $env['DB_HOST'] ?? '127.0.0.1';
$dbPort = (int)($env['DB_PORT'] ?? 3306);
$dbName = $env['DB_NAME'] ?? 'cctv_ai';
$dbUser = $env['DB_USER'] ?? 'root';
$dbPass = $env['DB_PASSWORD'] ?? '';

mysqli_report(MYSQLI_REPORT_OFF);
$db = @new mysqli($dbHost, $dbUser, $dbPass, $dbName, $dbPort);
if ($db->connect_errno) fail_json('MySQL connection failed: ' . $db->connect_error, 500);
$db->set_charset('utf8mb4');

$today = date('Y-m-d');
$fromDate = trim((string)($_GET['from_date'] ?? $today));
$toDate = trim((string)($_GET['to_date'] ?? $fromDate));
$fromTime = trim((string)($_GET['from_time'] ?? '00:00'));
$toTime = trim((string)($_GET['to_time'] ?? '23:59'));
$cameraIp = trim((string)($_GET['camera_ip'] ?? ''));

if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $fromDate) || !preg_match('/^\d{4}-\d{2}-\d{2}$/', $toDate)) {
    fail_json('Invalid report date', 400);
}
if (!preg_match('/^\d{2}:\d{2}$/', $fromTime) || !preg_match('/^\d{2}:\d{2}$/', $toTime)) {
    fail_json('Invalid report time', 400);
}

$start = DateTime::createFromFormat('Y-m-d H:i', "$fromDate $fromTime");
$end = DateTime::createFromFormat('Y-m-d H:i', "$toDate $toTime");
if (!$start || !$end) fail_json('Invalid report date/time', 400);
$end->setTime((int)$end->format('H'), (int)$end->format('i'), 59);
if ($end < $start) fail_json('Report end must be after report start', 400);
if ((int)$start->diff($end)->format('%a') > 366) fail_json('Report range cannot exceed 366 days', 400);

$startSql = $start->format('Y-m-d H:i:s');
$endSql = $end->format('Y-m-d H:i:s');
$where = 'crossed_at >= ? AND crossed_at <= ?';
$types = 'ss';
$params = [$startSql, $endSql];
if ($cameraIp !== '') {
    $where .= ' AND camera_ip = ?';
    $types .= 's';
    $params[] = $cameraIp;
}

try {
    $tableCheck = query_rows($db, "SHOW TABLES LIKE 'vehicle_events'");
    if (!$tableCheck) fail_json('vehicle_events table is missing. Restart the CCTV backend once to create analytics tables.', 500);

    $summaryRows = query_rows($db,
        "SELECT vehicle_type, COUNT(*) total FROM vehicle_events WHERE $where GROUP BY vehicle_type",
        $types, $params
    );
    $dailyRows = query_rows($db,
        "SELECT DATE(crossed_at) bucket_date, vehicle_type, COUNT(*) total FROM vehicle_events WHERE $where GROUP BY DATE(crossed_at), vehicle_type ORDER BY bucket_date",
        $types, $params
    );
    $hourlyRows = query_rows($db,
        "SELECT DATE(crossed_at) bucket_date, HOUR(crossed_at) bucket_hour, vehicle_type, COUNT(*) total FROM vehicle_events WHERE $where GROUP BY DATE(crossed_at), HOUR(crossed_at), vehicle_type ORDER BY bucket_date, bucket_hour",
        $types, $params
    );
    $cameraRows = query_rows($db,
        "SELECT camera_ip, vehicle_type, COUNT(*) total FROM vehicle_events WHERE $where GROUP BY camera_ip, vehicle_type ORDER BY camera_ip, vehicle_type",
        $types, $params
    );

    $summary = empty_bucket();
    foreach ($summaryRows as $row) add_type($summary, (string)$row['vehicle_type'], (int)$row['total']);

    $dailyMap = [];
    foreach ($dailyRows as $row) {
        $key = (string)$row['bucket_date'];
        if (!isset($dailyMap[$key])) $dailyMap[$key] = empty_bucket();
        add_type($dailyMap[$key], (string)$row['vehicle_type'], (int)$row['total']);
    }
    $daily = [];
    foreach ($dailyMap as $date => $bucket) $daily[] = array_merge(['date' => $date], $bucket);

    $hourlyMap = [];
    foreach ($hourlyRows as $row) {
        $date = (string)$row['bucket_date'];
        $hour = (int)$row['bucket_hour'];
        $key = $date . '|' . sprintf('%02d', $hour);
        if (!isset($hourlyMap[$key])) $hourlyMap[$key] = ['date' => $date, 'hour_num' => $hour, 'bucket' => empty_bucket()];
        add_type($hourlyMap[$key]['bucket'], (string)$row['vehicle_type'], (int)$row['total']);
    }
    $hourly = [];
    foreach ($hourlyMap as $item) {
        $h = (int)$item['hour_num'];
        $hourly[] = array_merge([
            'date' => $item['date'],
            'hour' => sprintf('%02d:00 - %02d:59', $h, $h),
        ], $item['bucket']);
    }

    $cameraMap = [];
    foreach ($cameraRows as $row) {
        $key = (string)$row['camera_ip'];
        if (!isset($cameraMap[$key])) $cameraMap[$key] = empty_bucket();
        add_type($cameraMap[$key], (string)$row['vehicle_type'], (int)$row['total']);
    }
    $byCamera = [];
    foreach ($cameraMap as $ip => $bucket) $byCamera[] = array_merge(['camera_ip' => $ip], $bucket);

    $eventCount = array_sum(array_map(static fn($r) => (int)$r['total'], $summaryRows));
    $source = 'vehicle_events';
    $note = null;

    // Compatibility fallback for installations that already have daily aggregate
    // counts but no historical event rows from an older build. Exact hourly data
    // cannot be reconstructed from an aggregate-only table.
    if ($eventCount === 0 && $fromTime === '00:00' && $toTime === '23:59') {
        $dailyTable = query_rows($db, "SHOW TABLES LIKE 'daily_vehicle_counts'");
        if ($dailyTable) {
            $aggWhere = 'event_date >= ? AND event_date <= ?';
            $aggTypes = 'ss';
            $aggParams = [$fromDate, $toDate];
            if ($cameraIp !== '') {
                $aggWhere .= ' AND camera_ip = ?';
                $aggTypes .= 's';
                $aggParams[] = $cameraIp;
            }
            $aggRows = query_rows($db,
                "SELECT event_date, camera_ip, vehicle_type, SUM(total_count) total FROM daily_vehicle_counts WHERE $aggWhere GROUP BY event_date, camera_ip, vehicle_type ORDER BY event_date, camera_ip",
                $aggTypes, $aggParams
            );
            if ($aggRows) {
                $summary = empty_bucket();
                $dailyMap = [];
                $cameraMap = [];
                foreach ($aggRows as $row) {
                    $date = (string)$row['event_date'];
                    $ip = (string)$row['camera_ip'];
                    $count = (int)$row['total'];
                    $type = (string)$row['vehicle_type'];
                    add_type($summary, $type, $count);
                    if (!isset($dailyMap[$date])) $dailyMap[$date] = empty_bucket();
                    add_type($dailyMap[$date], $type, $count);
                    if (!isset($cameraMap[$ip])) $cameraMap[$ip] = empty_bucket();
                    add_type($cameraMap[$ip], $type, $count);
                }
                $daily = [];
                foreach ($dailyMap as $date => $bucket) $daily[] = array_merge(['date' => $date], $bucket);
                $byCamera = [];
                foreach ($cameraMap as $ip => $bucket) $byCamera[] = array_merge(['camera_ip' => $ip], $bucket);
                $hourly = [];
                $source = 'daily_vehicle_counts';
                $note = 'Historical totals were recovered from daily aggregates. Hour-wise rows become available for new counts stored in vehicle_events.';
            }
        }
    }

    echo json_encode([
        'ok' => true,
        'from' => $start->format('Y-m-d H:i'),
        'to' => $end->format('Y-m-d H:i'),
        'camera_ip' => $cameraIp !== '' ? $cameraIp : null,
        'summary' => $summary,
        'daily' => $daily,
        'hourly' => $hourly,
        'by_camera' => $byCamera,
        'source' => $source,
        'note' => $note,
    ], JSON_UNESCAPED_SLASHES);
} catch (Throwable $e) {
    fail_json('Report query failed: ' . $e->getMessage(), 500);
} finally {
    $db->close();
}
