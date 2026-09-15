<?php
require __DIR__ . '/ui_common.php';
render_page_start('violations', 'Helmet Violations', 'Confirmed no-helmet violations with captured evidence and plate details when available.');
?>
<section class="section-block" id="violations">
    <div class="section-heading">
        <div><h2>Recent No-Helmet Violations</h2><p>Confirmed records only.</p></div>
        <div><button type="button" class="camera-action-btn" id="btnRefreshViolations"><i class="bi bi-arrow-clockwise"></i> Refresh</button></div>
    </div>
    <div class="report-message" id="violationStatus">Loading recent violations...</div>
    <div class="violations-grid" id="violationsGrid"></div>
</section>
<?php render_page_end(); ?>
