const ws = new WebSocket(`ws://${location.host}/ws`);
const grid = document.getElementById("slots-grid");
const barcodeInput = document.getElementById("barcode-input");
const popup = document.getElementById("scan-popup");

let slotsData = {};

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

if (barcodeInput) {
    barcodeInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") {
            const val = barcodeInput.value.trim();
            if (val && ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "BARCODE_SCAN", serial: val }));
                barcodeInput.value = "";
            }
        }
    });
    document.addEventListener("click", () => barcodeInput.focus());
}

function showScanResult(res) {
    if (!popup) return;
    popup.classList.remove("hidden");
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