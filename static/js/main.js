const ws = new WebSocket(`ws://${location.host}/ws`);
const grid = document.getElementById("slots-grid");
const barcodeInput = document.getElementById("barcode-input");
const popup = document.getElementById("scan-popup");
const testModeButton = document.getElementById("test-mode-btn");
const selectionModeButton = document.getElementById("selection-mode-btn");
const selectionPanel = document.getElementById("selection-panel");
const inventoryList = document.getElementById("inventory-list");
const startSelectionScanButton = document.getElementById("start-selection-scan-btn");

let slotsData = {};
let selectionMode = false;
let inventory = [];
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
        infoHtml = `
            <p><strong>SN:</strong> ${slot.info.serial_number || 'N/A'} ${dbBadge}</p>
            <p><strong>Modell:</strong> ${slot.info.model || 'N/A'}</p>
            <p><strong>Pending:</strong> ${slot.info.pending_sectors ?? 0}</p>
            <p><strong>Reallocated:</strong> ${slot.info.reallocated_sectors ?? 0}</p>
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
    inventoryList.innerHTML = inventory.map((hdd) => `
        <label class="inventory-row">
            <input type="checkbox" class="inventory-checkbox" value="${hdd.serial_number}" ${hdd.test_result === "PASSED" ? "checked" : ""}>
            <span><strong>${hdd.serial_number}</strong></span>
            <span>${hdd.model || "UNKNOWN"}</span>
            <span class="inventory-status ${String(hdd.test_result).toLowerCase()}">${hdd.test_result}</span>
            <span>Pending: ${hdd.pending_sectors ?? 0}</span>
            <span>Reallocated: ${hdd.reallocated_sectors ?? 0}</span>
            <span>${hdd.tested_at || "kein Datum"}</span>
        </label>
    `).join("");
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