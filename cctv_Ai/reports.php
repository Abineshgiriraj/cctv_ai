<?php
require __DIR__ . '/ui_common.php';
$today = date('Y-m-d');
render_page_start('reports', 'Vehicle Count Reports', 'Day-wise, time-wise and camera-wise vehicle crossing reports.');
?>
<article class="report-panel">
    <div class="report-filter-grid">
        <div class="report-field"><label for="reportFromDate">FROM DATE</label><input type="date" id="reportFromDate" value="<?= e($today) ?>"></div>
        <div class="report-field"><label for="reportToDate">TO DATE</label><input type="date" id="reportToDate" value="<?= e($today) ?>"></div>
        <div class="report-field"><label for="reportFromTime">FROM TIME</label><input type="time" id="reportFromTime" value="00:00"></div>
        <div class="report-field"><label for="reportToTime">TO TIME</label><input type="time" id="reportToTime" value="23:59"></div>
        <div class="report-field"><label for="reportCamera">CAMERA</label><select id="reportCamera"><option value="">All Cameras</option><?php foreach ($cameras as $camera): ?><option value="<?= e($camera['camera_key']) ?>"><?= e($camera['name'] . ' · ' . $camera['area']) ?></option><?php endforeach; ?></select></div>
        <div class="report-actions"><button type="button" class="secondary" id="btnTodayReport">Today</button><button type="button" class="primary" id="btnRefreshReport"><i class="bi bi-search"></i> Apply</button></div>
    </div>
    <div class="report-kpis">
        <div class="report-kpi person"><small>PERSONS</small><strong id="rptTotalPersons">0</strong></div>
        <div class="report-kpi total"><small>TOTAL VEHICLES</small><strong id="rptTotalVehicles">0</strong></div>
        <div class="report-kpi"><small>MOTORCYCLES</small><strong id="rptTotalMotorcycles">0</strong></div>
        <div class="report-kpi"><small>CARS</small><strong id="rptTotalCars">0</strong></div>
        <div class="report-kpi"><small>BUSES</small><strong id="rptTotalBuses">0</strong></div>
        <div class="report-kpi"><small>TRUCKS</small><strong id="rptTotalTrucks">0</strong></div>
        <div class="report-kpi"><small>BICYCLES</small><strong id="rptTotalBicycles">0</strong></div>
        <div class="report-kpi"><small>OTHER</small><strong id="rptTotalOther">0</strong></div>
        <div class="report-kpi grand"><small>GRAND TOTAL</small><strong id="rptGrandTotal">0</strong></div>
    </div>
    <div class="report-message" id="reportMessage">Loading vehicle count report...</div>
    <div class="report-table-grid">
        <div class="data-card"><div class="data-card-head"><h3><i class="bi bi-calendar3"></i> Day-wise Count</h3><small>One row per day</small></div><div class="table-scroll"><table class="ops-table" id="tblDailyReport"><thead><tr><th>Date</th><th>Persons</th><th>Cars</th><th>Motorcycles</th><th>Buses</th><th>Trucks</th><th>Bicycles</th><th>Total</th></tr></thead><tbody><tr><td colspan="8" class="empty-row">Loading...</td></tr></tbody></table></div></div>
        <div class="data-card"><div class="data-card-head"><h3><i class="bi bi-clock-history"></i> Time-wise Count</h3><small>Hourly totals</small></div><div class="table-scroll"><table class="ops-table" id="tblHourlyReport"><thead><tr><th>Date</th><th>Time</th><th>Persons</th><th>Cars</th><th>Motorcycles</th><th>Buses</th><th>Trucks</th><th>Bicycles</th><th>Total</th></tr></thead><tbody><tr><td colspan="9" class="empty-row">Loading...</td></tr></tbody></table></div></div>
        <div class="data-card"><div class="data-card-head"><h3><i class="bi bi-camera-video"></i> Camera-wise Count</h3><small>Stored crossings by camera</small></div><div class="table-scroll"><table class="ops-table" id="tblCameraReport"><thead><tr><th>Camera</th><th>IP</th><th>Persons</th><th>Cars</th><th>Motorcycles</th><th>Buses</th><th>Trucks</th><th>Bicycles</th><th>Total</th></tr></thead><tbody><tr><td colspan="9" class="empty-row">Loading...</td></tr></tbody></table></div></div>
    </div>
</article>
<?php render_page_end(); ?>
