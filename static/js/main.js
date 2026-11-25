const ws = new WebSocket(`ws://${location.host}/ws`);
const grid = document.getElementById("slots-grid");
const barcodeInput = document.getElementById("barcode-input");
const popup = document.getElementById("scan-popup");
const testModeButton = document.getElementById("test-mode-btn");
const selectionModeButton = document.getElementById("selection-mode-btn");
const selectionPanel = document.getElementById("selection-panel");
const inventoryList = document.getElementById("inventory-list");
const startSelectionScanButton = document.getElementById("start-selection-scan-btn");
const inventoryRuntimeSortButton = document.getElementById("inventory-runtime-sort-btn");

let slotsData = {};
let selectionMode = false;
let inventory = [];
let inventoryRuntimeSort = "desc";
let selectionScanActive = false;
let scanPopupTimer = null;

ws.onmessage = (event) => {
    try {
        const msg = JSON.parse(event.data);
        if (msg.type === "INIT_STATE") {
            slotsData = msg.slots;
            renderAllSlots();
        } else if (msg.type === "SLOT_UPDATE") {
            slotsData[msg.slot.slot_id] = msg.slot;
            renderSlot(msg.slot.slot_id);
        } else if (msg.type === "SCAN_RESULT") {
            showScanResult(msg);
        } else if (msg.type === "CHECK_RESULT") {
            showCheckResult(msg);
        } else if (msg.type === "WIPE_RESULT") {
            showCheckResult(msg);
        }
    } catch (err) {
        console.error("Fehler beim Verarbeiten der WebSocket-Nachricht:", err);
    }
};

function renderAllSlots() {
    if (!grid) return;
    grid.innerHTML = "";
    Object.keys(slotsData).forEach(slotId => {
        const card = document.createElement("div");
        card.id = `slot-${slotId}`;
        grid.appendChild(card);
        renderSlot(slotId);
    });
}

function renderSlot(slotId) {
    const slot = slotsData[slotId];
    if (!slot) return;

    const card = document.getElementById(`slot-${slotId}`);
    if (!card) return;

    card.className = `slot-card ${slot.status.toLowerCase()}`;

    let infoHtml = "<p>Keine Festplatte</p>";
    if (slot.info) {
        let dbBadge = slot.from_db ? `<span class="db-tag">📁 Aus Inventar</span>` : "";
        let testTime = slot.info.tested_at ? `<p class="test-time"><strong>Getestet am:</strong> ${slot.info.tested_at}</p>` : "";
        let testResults = "";
        if (slot.info.test_results) {
            const results = slot.info.test_results;
            testResults = `
                <div class="test-results">
                    <p><strong>Entscheidung: ${slot.status}</strong></p>
                    ${results.short_test && results.short_test !== "NOT_RUN" ? `<p>Selftest: ${results.short_test}</p>` : ""}
                </div>
            `;
        }
        infoHtml = `
            ${slot.info.serial_number ? `<p><strong>SN:</strong> ${slot.info.serial_number} ${dbBadge}</p>` : ""}
            ${slot.info.model && slot.info.model !== "UNKNOWN" ? `<p><strong>Modell:</strong> ${slot.info.model}</p>` : ""}
            <div class="key-indicators">
                <p><strong>SMART Health:</strong> ${slot.info.smart_overall}</p>
                ${slot.info.test_results?.short_test && slot.info.test_results.short_test !== "NOT_RUN" ? `<p><strong>Selftest:</strong> ${slot.info.test_results.short_test}</p>` : ""}
                ${slot.info.temperature_c != null ? `<p><strong>Temperatur:</strong> ${slot.info.temperature_c} °C</p>` : ""}
                ${slot.info.grown_defects != null ? `<p><strong>Grown defects:</strong> ${slot.info.grown_defects}</p>` : ""}
                ${slot.info.uncorrected_read_errors != null ? `<p><strong>Unkorrigierte Lesefehler:</strong> ${slot.info.uncorrected_read_errors}</p>` : ""}
            </div>
            ${testResults}
            ${testTime}
        `;
    }

    let progressHtml = "";
    if (slot.status === "TESTING") {
        progressHtml = `
            <div class="progress-bar-bg">
                <div class="progress-bar-fill" style="width: ${slot.progress}%"></div>
            </div>
            <p style="font-size:0.8rem; text-align:right; margin-top:4px;">Test: ${slot.progress}%</p>
        `;
    } else if (slot.status === "WIPING") {
        progressHtml = `
            <div class="progress-bar-bg"><div class="progress-bar-fill wipe-progress" style="width: ${slot.progress || 0}%"></div></div>
            <p style="font-size:0.8rem;text-align:right;margin-top:4px;">Vollständig löschen: ${slot.progress || 0}%</p>
        `;
    } else if (slot.wipe_message) {
        progressHtml = `<p class="wipe-message">${escapeHtml(slot.wipe_message)}</p>`;
    }

    let actionBtnsHtml = "";
    if (slot.status === "EMPTY") {
        actionBtnsHtml = `
            <div class="btn-group">
                <button class="action-btn check-btn" onclick="checkSlot(${slotId})">🔍 Festplatte prüfen</button>
            </div>
        `;
    } else {
        actionBtnsHtml = `
            <div class="btn-group">
                <button class="action-btn retest-btn" onclick="retestSlot(${slotId})" ${slot.status === "TESTING" ? "disabled" : ""}>🔄 Erneuten Test starten</button>
                <button class="action-btn report-btn" onclick="showSmartReport(${slotId})">📄 SMART-Bericht</button>
                <button class="action-btn wipe-btn" onclick="wipeSlot(${slotId})" ${slot.status === "WIPING" ? "disabled" : ""}>🗑 Datenträger löschen</button>
                <button class="action-btn eject-btn" onclick="ejectSlot(${slotId})">⏏ Slot freigeben</button>
            </div>
        `;
    }

    card.innerHTML = `
        <div class="slot-header">
            <span>Slot ${slotId}</span>
            <span class="badge ${slot.status.toLowerCase()}">${slot.status}</span>
        </div>
        <div class="hdd-info">${infoHtml}</div>
        ${progressHtml}
        ${actionBtnsHtml}
    `;
}

function ejectSlot(slotId) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "EJECT_SLOT", slot_id: slotId }));
    }
}

function retestSlot(slotId) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "RETEST_SLOT", slot_id: slotId }));
    }
}

function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
}

async function showSmartReport(slotId) {
    const slot = slotsData[slotId];
    const serial = slot?.info?.serial_number;
    let report = slot?.info?.smart_report;

    if (!report && serial) report = await fetchSmartReport(serial);
    openSmartReport(`SMART-Bericht - Slot ${slotId}`, report);
}

async function fetchSmartReport(serial) {
    try {
        const response = await fetch(`/api/report/${encodeURIComponent(serial)}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        return data.smart_report;
    } catch (error) {
        console.error("SMART-Bericht konnte nicht geladen werden:", error);
        return null;
    }
}

function openSmartReport(title, report) {
    if (!popup) return;
    if (!report) {
        popup.className = "popup scan-ready smart-report-popup";
        popup.innerHTML = `
            <div class="smart-report-header">
                <h2>SMART-Bericht</h2>
                <button class="modal-close-btn" type="button" aria-label="Schließen" onclick="closeSmartReport()">×</button>
            </div>
            <p>Für diese Platte wurde noch kein vollständiger Bericht gespeichert.</p>
            <button class="popup-close-btn" type="button" onclick="closeSmartReport()">Schließen</button>
        `;
        return;
    }

    popup.className = "popup scan-ready smart-report-popup";
    popup.innerHTML = `
        <div class="smart-report-header">
            <h2>${escapeHtml(title)}</h2>
            <button class="modal-close-btn" type="button" aria-label="Schließen" onclick="closeSmartReport()">×</button>
        </div>
        <pre class="smart-report-text">${escapeHtml(report)}</pre>
        <button class="popup-close-btn" type="button" onclick="closeSmartReport()">Schließen</button>
    `;
}

async function showInventorySmartReport(serial) {
    const record = inventory.find((hdd) => hdd.serial_number === serial);
    const report = record?.smart_report || await fetchSmartReport(serial);
    openSmartReport(`SMART-Bericht - ${serial}`, report);
}

function closeSmartReport() {
    popup.className = "popup hidden";
    barcodeInput.focus();
}

document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && popup && !popup.classList.contains("hidden")) {
        closeSmartReport();
    }
});

function wipeSlot(slotId) {
    const slot = slotsData[slotId];
    const serial = slot?.info?.serial_number;
    if (!serial) return;
    if (!popup) return;

    popup.className = "popup scan-ready wipe-confirm-popup";
    popup.innerHTML = `
        <h2>Datenträger vollständig löschen?</h2>
        <p><strong>Slot:</strong> ${slotId}</p>
        <p><strong>Seriennummer:</strong> ${escapeHtml(serial)}</p>
        <p>Alle Daten werden unwiederbringlich mit Nullbytes überschrieben.</p>
        <div class="popup-actions">
            <button class="popup-close-btn wipe-cancel" type="button" onclick="closeSmartReport()">Abbrechen</button>
            <button class="popup-close-btn wipe-confirm" type="button" onclick="confirmWipe(${slotId})">Löschen bestätigen</button>
        </div>
    `;
}

function confirmWipe(slotId) {
    popup.className = "popup hidden";
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "WIPE_SLOT", slot_id: slotId, confirmed: true }));
    }
}

function checkSlot(slotId) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "CHECK_SLOT", slot_id: slotId }));
    }
}

function showCheckResult(result) {
    if (!popup) return;
    popup.classList.remove("hidden");
    popup.innerHTML = `<p>${result.message}</p>`;
    setTimeout(() => popup.classList.add("hidden"), 5000);
}

function setSelectionMode(enabled) {
    selectionMode = enabled;
    selectionPanel.classList.toggle("hidden", !enabled);
    grid.classList.toggle("hidden", enabled);
    testModeButton.classList.toggle("active", !enabled);
    selectionModeButton.classList.toggle("active", enabled);
    barcodeInput.placeholder = enabled ? "Platte für Auswahl scannen..." : "Barcode scannen...";
    if (enabled) {
        loadInventory();
    } else if (ws.readyState === WebSocket.OPEN) {
        selectionScanActive = false;
        ws.send(JSON.stringify({ type: "STOP_SELECTION_SCAN" }));
    }
}

async function loadInventory() {
    try {
        const response = await fetch("/api/inventory");
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        inventory = await response.json();
        renderInventory();
    } catch (error) {
        console.error("Inventar konnte nicht geladen werden:", error);
        inventoryList.innerHTML = "<p>Inventar konnte nicht geladen werden.</p>";
    }
}

function renderInventory() {
    if (!inventory.length) {
        inventoryList.innerHTML = "<p>Noch keine getesteten Festplatten vorhanden.</p>";
        return;
    }
    const hasExistingSelection = Boolean(inventoryList.querySelector(".inventory-checkbox"));
    const selectedSerials = new Set(
        Array.from(inventoryList.querySelectorAll(".inventory-checkbox:checked"))
            .map((checkbox) => checkbox.value)
    );
    const sortedInventory = [...inventory].sort((first, second) => {
        const firstMinutes = parsePowerOnMinutes(first.power_on_time);
        const secondMinutes = parsePowerOnMinutes(second.power_on_time);
        if (firstMinutes == null && secondMinutes == null) return 0;
        if (firstMinutes == null) return 1;
        if (secondMinutes == null) return -1;
        return inventoryRuntimeSort === "asc"
            ? firstMinutes - secondMinutes
            : secondMinutes - firstMinutes;
    });

    inventoryList.innerHTML = sortedInventory.map((hdd, index) => `
        <div class="inventory-row">
            <input id="inventory-select-${index}" type="checkbox" class="inventory-checkbox" value="${escapeHtml(hdd.serial_number)}" ${hasExistingSelection ? (selectedSerials.has(hdd.serial_number) ? "checked" : "") : (hdd.test_result === "PASSED" ? "checked" : "")}>
            <label class="inventory-serial" for="inventory-select-${index}"><strong>${escapeHtml(hdd.serial_number)}</strong></label>
            <span>${hdd.model || "UNKNOWN"}</span>
            <span class="inventory-status ${String(hdd.test_result).toLowerCase()}">${hdd.test_result}</span>
            ${hdd.smart_overall ? `<span>SMART: ${hdd.smart_overall}</span>` : ""}
            ${hdd.self_test_result && hdd.self_test_result !== "UNKNOWN" ? `<span>Selbsttest: ${hdd.self_test_result}</span>` : ""}
            ${hdd.temperature_c != null ? `<span>Temperatur: ${hdd.temperature_c} °C</span>` : ""}
            ${formatPowerOnYears(hdd.power_on_time) ? `<span>Laufzeit: ${formatPowerOnYears(hdd.power_on_time)} Jahre</span>` : ""}
            ${hdd.grown_defects != null ? `<span>Grown defects: ${hdd.grown_defects}</span>` : ""}
            ${hdd.uncorrected_read_errors != null ? `<span>Unkorrigierte Lesefehler: ${hdd.uncorrected_read_errors}</span>` : ""}
            <span>${hdd.tested_at || "kein Datum"}</span>
            <button type="button" class="inventory-report-btn" data-serial="${escapeHtml(hdd.serial_number)}">SMART-Bericht</button>
        </div>
    `).join("");

    inventoryList.querySelectorAll(".inventory-report-btn").forEach((button) => {
        button.addEventListener("click", () => showInventorySmartReport(button.dataset.serial));
    });
}

function parsePowerOnMinutes(powerOnTime) {
    const match = /^(\d+):(\d{1,2})$/.exec(powerOnTime || "");
    if (!match) return null;
    return Number(match[1]) * 60 + Number(match[2]);
}

function formatPowerOnYears(powerOnTime) {
    const match = /^(\d+):(\d{1,2})$/.exec(powerOnTime || "");
    if (!match) return "";
    const totalHours = Number(match[1]) + Number(match[2]) / 60;
    return (totalHours / (24 * 365.25)).toFixed(1);
}

if (inventoryRuntimeSortButton) {
    inventoryRuntimeSortButton.addEventListener("click", () => {
        inventoryRuntimeSort = inventoryRuntimeSort === "desc" ? "asc" : "desc";
        inventoryRuntimeSortButton.textContent = inventoryRuntimeSort === "desc"
            ? "Laufzeit: absteigend ↓"
            : "Laufzeit: aufsteigend ↑";
        renderInventory();
    });
}

function startSelectionScan() {
    const serials = Array.from(document.querySelectorAll(".inventory-checkbox:checked"))
        .map((checkbox) => checkbox.value);
    if (!serials.length) {
        showCheckResult({ message: "Bitte mindestens eine Festplatte auswählen." });
        return;
    }
    if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "START_SELECTION_SCAN", serials }));
        selectionScanActive = true;
        showSelectionReady(serials.length);
    }
}

function showSelectionReady(count) {
    if (!popup) return;
    clearTimeout(scanPopupTimer);
    popup.className = "popup scan-ready";
    popup.innerHTML = `
        <div class="scan-popup-icon">▣</div>
        <h2>PLATTEN SCANNEN</h2>
        <p>${count} Platte(n) ausgewählt</p>
        <p>Barcode scannen...</p>
        <button class="popup-close-btn" type="button" onclick="closeSelectionScan()">Auswahl ändern</button>
    `;
}

function closeSelectionScan() {
    clearTimeout(scanPopupTimer);
    selectionScanActive = false;
    if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "STOP_SELECTION_SCAN" }));
    }
    popup.className = "popup hidden";
    barcodeInput.focus();
}

if (barcodeInput) {
    barcodeInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") {
            const val = barcodeInput.value.trim();
            if (val && ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                    type: "BARCODE_SCAN",
                    serial: val,
                    selection_mode: selectionMode
                }));
                barcodeInput.value = "";
            }
        }
    });
    document.addEventListener("click", () => barcodeInput.focus());
}

function showScanResult(res) {
    if (!popup) return;
    popup.classList.remove("hidden");
    if (selectionMode && selectionScanActive) {
        const allowed = res.selection_allowed === true;
        clearTimeout(scanPopupTimer);
        popup.className = `popup scan-result ${allowed ? "scan-approved" : "scan-rejected"}`;
        popup.innerHTML = `
            <div class="scan-popup-icon">${allowed ? "✓" : "✕"}</div>
            <h2>${allowed ? "PLATTE NEHMEN" : "PLATTE NICHT NEHMEN"}</h2>
            <p><strong>SN:</strong> ${res.serial}</p>
            <p>${res.matched_serial && res.matched_serial !== res.serial ? `<strong>Erkannt als:</strong> ${res.matched_serial}<br>` : ""}${allowed ? "Diese Platte ist ausgewählt und PASSED." : "Diese Platte ist nicht ausgewählt oder nicht im Inventar."}</p>
            <button class="popup-close-btn" type="button" onclick="closeSelectionScan()">Auswahl ändern</button>
        `;
        scanPopupTimer = setTimeout(showSelectionReady, 5000, selectedCount());
        return;
    }
    popup.className = "popup";
    if (res.data) {
        const h = res.data;
        const color = h.test_result === "PASSED" ? "#10b981" : "#ef4444";
        popup.innerHTML = `
            <h3 style="color:${color}">${h.test_result}</h3>
            <p><strong>SN:</strong> ${h.serial_number}</p>
            <p><strong>Modell:</strong> ${h.model}</p>
            <p><strong>Datum:</strong> ${h.tested_at || 'k.A.'}</p>
        `;
    } else {
        popup.innerHTML = `<p style="color:#ef4444">SN <strong>${res.serial}</strong> nicht gefunden!</p>`;
    }
    setTimeout(() => popup.classList.add("hidden"), 4000);
}

function selectedCount() {
    return document.querySelectorAll(".inventory-checkbox:checked").length;
}

testModeButton.addEventListener("click", () => setSelectionMode(false));
selectionModeButton.addEventListener("click", () => setSelectionMode(true));
startSelectionScanButton.addEventListener("click", startSelectionScan);