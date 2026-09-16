<?php
require __DIR__ . '/ui_common.php';
render_page_start('live', 'Live Monitoring', 'Live AI and raw camera streams with per-camera status and counts.');
?>
<section class="section-block" id="live-monitoring">
    <div class="operator-camera-grid">
        <?php foreach ($cameras as $index => $camera): $num = $index + 1; ?>
            <article class="operator-camera-card" data-camera-key="<?= e($camera['camera_key']) ?>" data-camera-number="<?= $num ?>">
                <div class="operator-camera-head">
                    <div class="operator-camera-title"><span class="cam-dot"></span><div><h3><?= e($camera['name']) ?></h3><p><?= e($camera['area']) ?> · <?= e($camera['ip']) ?></p></div></div>
                    <span class="camera-state-text" id="cameraState<?= $num ?>">CONNECTING</span>
                </div>
                <div class="operator-camera-frame camera-frame">
                    <img id="cameraStream<?= $num ?>" src="<?= e($camera['ai_url']) ?>" data-ai-stream-url="<?= e($camera['ai_url']) ?>" data-raw-stream-url="<?= e($camera['raw_url']) ?>" data-stream-mode="ai" alt="<?= e($camera['name']) ?>">
                    <div class="stream-message" id="streamMessage<?= $num ?>"><i class="bi bi-camera-video"></i><b>WAITING FOR STREAM</b><span>Starting AI feed...</span></div>
                    <div class="camera-overlay-top"><span>CH<?= e($camera['channel']) ?></span><span class="camera-clock"></span></div>
                </div>
                <div class="operator-camera-toolbar">
                    <div class="operator-mode-switch"><button type="button" class="active" id="aiMode<?= $num ?>" onclick="switchCameraMode(<?= $num ?>,'ai')"><i class="bi bi-bounding-box"></i> AI</button><button type="button" id="rawMode<?= $num ?>" onclick="switchCameraMode(<?= $num ?>,'raw')"><i class="bi bi-camera-video"></i> Raw</button></div>
                    <button type="button" class="camera-action-btn" onclick="reconnectCamera(<?= $num ?>)"><i class="bi bi-arrow-clockwise"></i> Reconnect</button>
                </div>
                <div class="camera-useful-stats">
                    <div><small>VISIBLE VEHICLES</small><b id="vehicleCount<?= $num ?>">0</b></div>
                    <div><small>VISIBLE PERSONS</small><b id="personCount<?= $num ?>">0</b></div>
                    <div><small>COUNTED TODAY</small><b id="cameraTodayCount<?= $num ?>">0</b></div>
                    <div><small>AI LATENCY</small><b id="inferenceMs<?= $num ?>">—</b></div>
                </div>
            </article>
        <?php endforeach; ?>
    </div>
</section>
<?php render_page_end(); ?>
