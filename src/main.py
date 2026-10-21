import asyncio
import glob
import os
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Any, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.config import BASE_DIR, MAX_SLOTS
from src.database import init_db, save_or_update_hdd, get_all_hdds, get_hdd_by_serial
from src import smart_logic
from src.monitor import DiskMonitor

app = FastAPI(title="HDD Triage Station")

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.active_connections.discard(connection)

manager = ConnectionManager()

slots_state: Dict[int, Dict[str, Any]] = {
    i: {
        "slot_id": i, "status": "EMPTY", "device": None, "info": None,
        "progress": 0, "from_db": False, "test_started_at": None,
        "test_duration_seconds": None, "test_generation": 0
    }
    for i in range(1, MAX_SLOTS + 1)
}
device_locks = defaultdict(asyncio.Lock)
manually_ejected_devices: Dict[str, str] = {}

def empty_slot(slot_id: int) -> Dict[str, Any]:
    return {
        "slot_id": slot_id, "status": "EMPTY", "device": None, "info": None,
        "progress": 0, "from_db": False, "test_started_at": None,
        "test_duration_seconds": None, "test_generation": 0
    }

def start_test_state(slot: Dict[str, Any]) -> None:
    slot["test_started_at"] = datetime.now(timezone.utc).isoformat()
    slot["test_duration_seconds"] = 0
    slot["test_generation"] = slot.get("test_generation", 0) + 1

def assign_slot(dev_node: str) -> int:
    for s_id, s_data in slots_state.items():
        if s_data["device"] == dev_node:
            return s_id
    for s_id, s_data in slots_state.items():
        if s_data["status"] == "EMPTY":
            return s_id
    return 1

def get_slot_by_device(dev_node: str) -> int:
    for s_id, s_data in slots_state.items():
        if s_data["device"] == dev_node:
            return s_id
    return None

async def handle_disk_added(dev_node: str, preferred_slot_id: int = None):
    try:
        async with device_locks[dev_node]:
            info = None
            serial = "UNKNOWN"
            for attempt in range(3):
                if not os.path.exists(dev_node):
                    return
                info = smart_logic.get_disk_info(dev_node)
                serial = info.get("serial_number", "UNKNOWN")
                if serial != "UNKNOWN":
                    break
                if attempt < 2:
                    await asyncio.sleep(0.4)

            if not os.path.exists(dev_node):
                return
            if serial == "UNKNOWN":
                print(f"[BACKEND INFO] SMART-Erkennung für {dev_node} nicht verfügbar; ignoriere Ereignis.")
                return

            if manually_ejected_devices.get(dev_node) == serial:
                return
            manually_ejected_devices.pop(dev_node, None)

            existing_slot_id = get_slot_by_device(dev_node)
            if existing_slot_id and slots_state[existing_slot_id].get("info", {}).get("serial_number") == serial:
                return

            slot_id = (
                preferred_slot_id
                if preferred_slot_id in slots_state and slots_state[preferred_slot_id]["status"] == "EMPTY"
                else assign_slot(dev_node)
            )
            current_slot = slots_state[slot_id]
            current_serial = current_slot.get("info", {}).get("serial_number") if current_slot.get("info") else None
            if current_serial and current_serial != serial:
                print(f"[BACKEND] Medienwechsel in Slot {slot_id}: {current_serial} -> {serial}")
                slots_state[slot_id] = empty_slot(slot_id)

            db_entry = get_hdd_by_serial(serial)

            if db_entry:
                print(f"[BACKEND DB-HIT] Festplatte SN '{serial}' existiert bereits im Inventar! Status: {db_entry['test_result']}")
                slots_state[slot_id] = {
                    "slot_id": slot_id,
                    "device": dev_node,
                    "status": db_entry["test_result"],
                    "progress": 100,
                    "info": {
                        **info,
                        "pending_sectors": db_entry.get("pending_sectors", info["pending_sectors"]),
                        "reallocated_sectors": db_entry.get("reallocated_sectors", info["reallocated_sectors"]),
                        "tested_at": db_entry.get("tested_at")
                    },
                    "from_db": True,
                    "test_started_at": None,
                    "test_duration_seconds": db_entry.get("test_duration_seconds"),
                    "test_generation": current_slot.get("test_generation", 0) + 1
                }
                await manager.broadcast({"type": "SLOT_UPDATE", "slot": slots_state[slot_id]})
                return

            print(f"[BACKEND NEW] Neue Festplatte auf {dev_node} erkannt (SN: {serial}). Starte kurzen SMART-Test...")
            slots_state[slot_id] = {
                "slot_id": slot_id,
                "device": dev_node,
                "status": "TESTING",
                "progress": 0,
                "info": info,
                "from_db": False,
                "test_started_at": datetime.now(timezone.utc).isoformat(),
                "test_duration_seconds": 0,
                "test_generation": current_slot.get("test_generation", 0) + 1
            }

            await manager.broadcast({"type": "SLOT_UPDATE", "slot": slots_state[slot_id]})
            smart_logic.start_short_test(dev_node)
            asyncio.create_task(poll_test_progress(
                dev_node, slot_id, slots_state[slot_id]["test_generation"]
            ))

    except Exception as e:
        print(f"[ERROR handle_disk_added]: {e}")
        traceback.print_exc()

async def check_disk_for_slot(slot_id: int):
    if slot_id not in slots_state or slots_state[slot_id]["status"] != "EMPTY":
        return

    for dev_node in sorted(glob.glob("/dev/sd[a-z]")):
        if get_slot_by_device(dev_node):
            continue
        info = smart_logic.get_disk_info(dev_node)
        if info.get("serial_number") != "UNKNOWN":
            await handle_disk_added(dev_node, preferred_slot_id=slot_id)
            return

    print(f"[BACKEND INFO] Keine Festplatte für Slot {slot_id} gefunden.")

async def handle_disk_removed(dev_node: str):
    try:
        await asyncio.sleep(1.5)
        if os.path.exists(dev_node):
            print(f"[BACKEND INFO] Ignoriere veraltetes REMOVE-Event für {dev_node}; Device ist wieder vorhanden.")
            return

        slot_id = get_slot_by_device(dev_node)
        if slot_id:
            slots_state[slot_id] = empty_slot(slot_id)
            await manager.broadcast({"type": "SLOT_UPDATE", "slot": slots_state[slot_id]})
            print(f"[BACKEND] Slot {slot_id} freigegeben (EMPTY).")
    except Exception as e:
        print(f"[ERROR handle_disk_removed]: {e}")
        traceback.print_exc()

async def start_manual_retest(slot_id: int):
    try:
        if slot_id not in slots_state or not slots_state[slot_id]["device"]:
            return

        dev_node = slots_state[slot_id]["device"]
        if not smart_logic.is_disk_present(dev_node):
            return

        print(f"[BACKEND RETEST] Manuell angeforderter Re-Test für Slot {slot_id} ({dev_node})...")
        info = smart_logic.get_disk_info(dev_node)

        slots_state[slot_id]["status"] = "TESTING"
        slots_state[slot_id]["progress"] = 0
        slots_state[slot_id]["info"] = info
        slots_state[slot_id]["from_db"] = False
        start_test_state(slots_state[slot_id])

        await manager.broadcast({"type": "SLOT_UPDATE", "slot": slots_state[slot_id]})
        smart_logic.start_short_test(dev_node)
        asyncio.create_task(poll_test_progress(
            dev_node, slot_id, slots_state[slot_id]["test_generation"]
        ))
    except Exception as e:
        print(f"[ERROR start_manual_retest]: {e}")
        traceback.print_exc()

async def poll_test_progress(dev_node: str, slot_id: int, generation: int):
    while True:
        try:
            await asyncio.sleep(4)

            if not smart_logic.is_disk_present(dev_node):
                if not os.path.exists(dev_node):
                    print(f"[BACKEND INFO] Festplatte {dev_node} herausgezogen. Gebe Slot {slot_id} frei.")
                    await handle_disk_removed(dev_node)
                    break
                print(f"[BACKEND INFO] SMART-Abfrage für {dev_node} vorübergehend nicht verfügbar.")
                continue

            if (
                slots_state[slot_id]["device"] != dev_node
                or slots_state[slot_id]["status"] != "TESTING"
                or slots_state[slot_id].get("test_generation") != generation
            ):
                break

            progress, finished = smart_logic.check_test_progress(dev_node)
            slots_state[slot_id]["progress"] = progress
            started_at = slots_state[slot_id].get("test_started_at")
            if started_at:
                started = datetime.fromisoformat(started_at)
                slots_state[slot_id]["test_duration_seconds"] = max(
                    0, (datetime.now(timezone.utc) - started).total_seconds()
                )

            if finished:
                final_info = smart_logic.get_disk_info(dev_node)
                final_result = smart_logic.evaluate_health(final_info)

                slots_state[slot_id]["status"] = final_result
                slots_state[slot_id]["info"] = final_info

                tested_at = save_or_update_hdd({
                    **final_info,
                    "test_result": final_result,
                    "slot_id": slot_id,
                    "test_duration_seconds": slots_state[slot_id]["test_duration_seconds"]
                })
                slots_state[slot_id]["info"]["tested_at"] = tested_at
                await manager.broadcast({"type": "SLOT_UPDATE", "slot": slots_state[slot_id]})
                break
            else:
                await manager.broadcast({"type": "SLOT_UPDATE", "slot": slots_state[slot_id]})

        except Exception as e:
            print(f"[ERROR poll_test_progress]: {e}")
            traceback.print_exc()
            break

async def background_poll_loop():
    """
    Hintergrund-Poller: Prüft alle 2.5 Sekunden aktiv die USB-Ports auf neue / entfernte Festplatten.
    Behebt stumme USB-Docks (Sharkoon & Co.).
    """
    print("[POLLER] Polling-Loop gestartet.")
    while True:
        await asyncio.sleep(2.5)
        try:
            devices = sorted(glob.glob("/dev/sd[a-z]"))
            for dev_node in list(manually_ejected_devices):
                if dev_node not in devices and not os.path.exists(dev_node):
                    manually_ejected_devices.pop(dev_node, None)

            for dev_node in devices:
                info = smart_logic.get_disk_info(dev_node)
                serial = info.get("serial_number", "UNKNOWN")
                slot_id = get_slot_by_device(dev_node)

                if serial != "UNKNOWN":
                    # Eine Festplatte steckt im Dock
                    if not slot_id:
                        await handle_disk_added(dev_node)
                    else:
                        # War der Slot belegt und die Disk wurde ausgetauscht?
                        curr_slot = slots_state[slot_id]
                        curr_sn = curr_slot.get("info", {}).get("serial_number") if curr_slot.get("info") else None
                        if curr_sn and curr_sn != serial:
                            print(f"[POLLER] Medienwechsel auf {dev_node}: {curr_sn} -> {serial}")
                            slots_state[slot_id] = empty_slot(slot_id)
                            await manager.broadcast({"type": "SLOT_UPDATE", "slot": slots_state[slot_id]})
                            await handle_disk_added(dev_node, preferred_slot_id=slot_id)
                else:
                    # Keine Festplatte antwortet an diesem Port
                    if slot_id and slots_state[slot_id]["status"] != "EMPTY":
                        if not os.path.exists(dev_node):
                            print(f"[POLLER] Device-Node {dev_node} entfernt -> Leere Slot {slot_id}")
                            await handle_disk_removed(dev_node)
                        else:
                            print(f"[POLLER] SMART-Abfrage für {dev_node} momentan nicht verfügbar; Slot bleibt belegt.")

            present_devices = set(devices)
            for slot_id, slot in list(slots_state.items()):
                dev_node = slot.get("device")
                if (
                    dev_node
                    and slot["status"] != "EMPTY"
                    and dev_node not in present_devices
                    and not os.path.exists(dev_node)
                ):
                    print(f"[POLLER] Device-Node {dev_node} nicht mehr vorhanden -> Leere Slot {slot_id}")
                    await handle_disk_removed(dev_node)
        except Exception as e:
            print(f"[POLLER EXCEPTION]: {e}")

@app.on_event("startup")
async def startup_event():
    init_db()
    monitor = DiskMonitor(handle_disk_added, handle_disk_removed)
    await monitor.start()
    asyncio.create_task(background_poll_loop())

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={"slots": slots_state}
    )

@app.get("/api/inventory")
async def api_inventory():
    return get_all_hdds()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        await websocket.send_json({"type": "INIT_STATE", "slots": slots_state})
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "BARCODE_SCAN":
                serial = data.get("serial", "").strip()
                result = get_hdd_by_serial(serial)
                await websocket.send_json({"type": "SCAN_RESULT", "serial": serial, "data": result})
            elif data.get("type") == "EJECT_SLOT":
                slot_id = int(data.get("slot_id"))
                if slot_id in slots_state:
                    dev_node = slots_state[slot_id].get("device")
                    serial = slots_state[slot_id].get("info", {}).get("serial_number")
                    if dev_node and serial:
                        manually_ejected_devices[dev_node] = serial
                    slots_state[slot_id] = empty_slot(slot_id)
                    await manager.broadcast({"type": "SLOT_UPDATE", "slot": slots_state[slot_id]})
            elif data.get("type") == "RETEST_SLOT":
                slot_id = int(data.get("slot_id"))
                asyncio.create_task(start_manual_retest(slot_id))
            elif data.get("type") == "CHECK_SLOT":
                slot_id = int(data.get("slot_id"))
                asyncio.create_task(check_disk_for_slot(slot_id))
    except WebSocketDisconnect:
        manager.disconnect(websocket)