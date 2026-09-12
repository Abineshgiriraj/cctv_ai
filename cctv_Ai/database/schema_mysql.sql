CREATE DATABASE IF NOT EXISTS cctv_ai CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE cctv_ai;

CREATE TABLE IF NOT EXISTS vehicle_events (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(32) NOT NULL,
    camera_ip VARCHAR(45) NOT NULL,
    track_id BIGINT NOT NULL,
    vehicle_type VARCHAR(32) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    confidence DECIMAL(6,5) NOT NULL DEFAULT 0,
    crossed_at DATETIME NOT NULL,
    event_date DATE NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_vehicle_session_track (session_id, camera_ip, track_id),
    KEY idx_vehicle_event_date (event_date, camera_ip, vehicle_type),
    KEY idx_vehicle_crossed_at (crossed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS daily_vehicle_counts (
    event_date DATE NOT NULL,
    camera_ip VARCHAR(45) NOT NULL,
    vehicle_type VARCHAR(32) NOT NULL,
    direction VARCHAR(16) NOT NULL,
    total_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (event_date, camera_ip, vehicle_type, direction)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS violations (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(32) NOT NULL,
    camera_ip VARCHAR(45) NOT NULL,
    violation_type VARCHAR(64) NOT NULL,
    vehicle_type VARCHAR(32) NULL,
    vehicle_track_id BIGINT NULL,
    person_track_id BIGINT NULL,
    helmet_status VARCHAR(32) NULL,
    plate_number VARCHAR(32) NULL,
    plate_confidence DECIMAL(6,5) NULL,
    detection_confidence DECIMAL(6,5) NOT NULL DEFAULT 0,
    captured_at DATETIME NOT NULL,
    event_date DATE NOT NULL,
    evidence_image LONGBLOB NULL,
    plate_image LONGBLOB NULL,
    metadata_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_violation_date (event_date, camera_ip, violation_type),
    KEY idx_violation_plate (plate_number),
    KEY idx_violation_captured (captured_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS road_events (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(32) NOT NULL,
    camera_ip VARCHAR(45) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    model_label VARCHAR(64) NOT NULL,
    confidence DECIMAL(6,5) NOT NULL DEFAULT 0,
    captured_at DATETIME NOT NULL,
    event_date DATE NOT NULL,
    evidence_image LONGBLOB NULL,
    metadata_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_road_event_date (event_date, camera_ip, event_type),
    KEY idx_road_event_captured (captured_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
