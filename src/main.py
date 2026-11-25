import asyncio
import glob
import os
import re
import subprocess
import time
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Any, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.config import BASE_DIR, MAX_SLOTS
from src.database import (
    init_db,
    save_or_update_hdd,
    get_all_hdds,
    get_hdd_by_serial,
    update_wipe_status,
)
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
ignored_startup_devices: Set[str] = set()
startup_ignore_until = 0.0
wipe_processes: Dict[int, Dict[str, Any]] = {}

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

def start_wipe_process(slot_id: int, dev_node: str, serial: str) -> None:
    if not re.fullmatch(r"/dev/sd[a-z]", dev_node):
        wipe_processes[slot_id] = {"progress": 0, "status": "FAILED", "message": "Ungültiges Laufwerksziel."}
        return

    size_result = subprocess.run(
        ["blockdev", "--getsize64", dev_node],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    total_bytes = int(size_result.stdout.strip()) if size_result.stdout.strip().isdigit() else 0
    process = subprocess.Popen(
        ["dd", "if=/dev/zero", f"of={dev_node}", "bs=16M", "status=progress", "conv=fsync"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    wipe_processes[slot_id] = {
        "process": process,
        "progress": 0,
        "status": "WIPING",
        "written_bytes": 0,
        "total_bytes": total_bytes,
        "serial": serial,
        "message": "Vollständiges Löschen läuft.",
    }

    def watch_output():
        if process.stderr:
            chunk_buffer = ""
            while True:
                chunk = process.stderr.read(4096)
                if not chunk:
                    break
                chunk_buffer += chunk
                matches = re.findall(r"(\d+)\s+bytes", chunk_buffer)
                if matches:
                    written = int(matches[-1])
                    wipe_processes[slot_id]["written_bytes"] = written
                    if total_bytes:
                        wipe_processes[slot_id]["progress"] = max(0, min(100, round(written * 100 / total_bytes)))

        return_code = process.wait()
        if return_code == 0:
            wipe_processes[slot_id].update({
                "progress": 100,
                "status": "WIPED",
                "message": "Datenträger vollständig mit Nullbytes überschrieben.",
            })
        else:
            wipe_processes[slot_id].update({
                "status": "WIPE_FAILED",
                "message": f"Löschen fehlgeschlagen (Exit-Code {return_code}).",
            })

    import threading
    threading.Thread(target=watch_output, daemon=True).start()

async def monitor_wipe(slot_id: int, dev_node: str):
    while True:
        await asyncio.sleep(1)
        state = wipe_processes.get(slot_id)
        if not state:
            return
        slots_state[slot_id]["progress"] = state["progress"]
        slots_state[slot_id]["status"] = state["status"]
        slots_state[slot_id]["wipe_message"] = state["message"]
        await manager.broadcast({"type": "SLOT_UPDATE", "slot": slots_state[slot_id]})
        if state["status"] in ("WIPED", "WIPE_FAILED"):
            await asyncio.to_thread(update_wipe_status, state["serial"], state["status"])
            wipe_processes.pop(slot_id, None)
            return

def set_short_test_result(info: Dict[str, Any], started: bool) -> None:
    info.setdefault("test_results", {})["short_test"] = (
        "STARTED" if started else "NOT_SUPPORTED"
    )

def assign_slot(dev_node: str) -> int:
    for s_id, s_data in slots_state.items():
        if s_data["device"] == dev_node:
            return s_id
    for s_id, s_data in slots_state.items():
        if s_data["status"] == "EMPTY":
            return s_id
    return None

def get_slot_by_device(dev_node: str) -> int:
    for s_id, s_data in slots_state.items():
        if s_data["device"] == dev_node:
            return s_id
    return None

async def handle_disk_added(dev_node: str, preferred_slot_id: int = None):
    try:
        if time.monotonic() < startup_ignore_until:
            ignored_startup_devices.add(dev_node)
            print(f"[STARTUP] Ignoriere während der ersten 5 Sekunden: {dev_node}")
            return
        if dev_node in ignored_startup_devices:
            return
        async with device_locks[dev_node]:
            info = None
            serial = "UNKNOWN"
            for attempt in range(3):
                if not os.path.exists(dev_node):
                    return
                info = await asyncio.to_thread(smart_logic.get_disk_info, dev_node)
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
            if slot_id is None:
                print(f"[BACKEND INFO] Ignoriere {dev_node}: alle {MAX_SLOTS} Slots sind belegt.")
                return
            current_slot = slots_state[slot_id]
            current_serial = current_slot.get("info", {}).get("serial_number") if current_slot.get("info") else None
            if current_serial and current_serial != serial:
                print(f"[BACKEND] Medienwechsel in Slot {slot_id}: {current_serial} -> {serial}")
                slots_state[slot_id] = empty_slot(slot_id)

            db_entry = await asyncio.to_thread(get_hdd_by_serial, serial)

            if db_entry:
                print(f"[BACKEND DB-HIT] Festplatte SN '{serial}' existiert bereits im Inventar! Status: {db_entry['test_result']}")
                stored_fields = (
                    "temperature_c",
                    "trip_temperature_c",
                    "power_on_time",
                    "grown_defects",
                    "uncorrected_read_errors",
                    "quality_warnings",
                    "wipe_status",
                    "wipe_finished_at",
                )
                if db_entry.get("smart_report"):
                    info["smart_report"] = db_entry["smart_report"]
                for field in stored_fields:
                    if db_entry.get(field) is not None:
                        info[field] = db_entry[field]
                if db_entry.get("self_test_result"):
                    info.setdefault("test_results", {})["short_test"] = db_entry["self_test_result"]
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

            short_test_started = await asyncio.to_thread(smart_logic.start_short_test, dev_node)
            set_short_test_result(info, short_test_started)
            await manager.broadcast({"type": "SLOT_UPDATE", "slot": slots_state[slot_id]})
            asyncio.create_task(poll_test_progress(
                dev_node, slot_id, slots_state[slot_id]["test_generation"]
            ))

    except Exception as e:
        print(f"[ERROR handle_disk_added]: {e}")
        traceback.print_exc()

async def check_disk_for_slot(slot_id: int):
    if slot_id not in slots_state or slots_state[slot_id]["status"] != "EMPTY":
        await manager.broadcast({
            "type": "CHECK_RESULT",
            "slot_id": slot_id,
            "status": "slot_occupied",
            "message": f"Slot {slot_id} ist bereits belegt."
        })
        return

    for dev_node in sorted(glob.glob("/dev/sd[a-z]")):
        if dev_node in ignored_startup_devices:
            continue
        if get_slot_by_device(dev_node):
            continue
        manually_ejected_devices.pop(dev_node, None)
        info = None
        for attempt in range(3):
            if not os.path.exists(dev_node):
                break
            info = await asyncio.to_thread(smart_logic.get_disk_info, dev_node)
            if info.get("serial_number") != "UNKNOWN":
                break
            if attempt < 2:
                await asyncio.sleep(0.5)

        if info and info.get("serial_number") != "UNKNOWN":
            serial = info["serial_number"]
            db_entry = await asyncio.to_thread(get_hdd_by_serial, serial)
            if db_entry:
                message = f"Festplatte {serial} wurde gefunden. Bereits getestet: {db_entry['test_result']}."
                status = "already_tested"
            else:
                message = f"Festplatte {serial} wurde gefunden. Neuer Test wird gestartet."
                status = "new_test"
            await manager.broadcast({
                "type": "CHECK_RESULT",
                "slot_id": slot_id,
                "status": status,
                "message": message
            })
            await handle_disk_added(dev_node, preferred_slot_id=slot_id)
            return

    candidates = sorted(glob.glob("/dev/sd[a-z]"))
    message = (
        f"Keine auslesbare Festplatte für Slot {slot_id} gefunden."
        if candidates
        else f"Keine Festplatte für Slot {slot_id} gefunden."
    )
    print(f"[BACKEND INFO] {message}")
    await manager.broadcast({
        "type": "CHECK_RESULT",
        "slot_id": slot_id,
        "status": "not_found",
        "message": message
    })

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
        if not await asyncio.to_thread(smart_logic.is_disk_present, dev_node):
            return

        print(f"[BACKEND RETEST] Manuell angeforderter Re-Test für Slot {slot_id} ({dev_node})...")
        info = await asyncio.to_thread(smart_logic.get_disk_info, dev_node)

        slots_state[slot_id]["status"] = "TESTING"
        slots_state[slot_id]["progress"] = 0
        slots_state[slot_id]["info"] = info
        slots_state[slot_id]["from_db"] = False
        start_test_state(slots_state[slot_id])

        short_test_started = await asyncio.to_thread(smart_logic.start_short_test, dev_node)
        set_short_test_result(info, short_test_started)
        await manager.broadcast({"type": "SLOT_UPDATE", "slot": slots_state[slot_id]})
        asyncio.create_task(poll_test_progress(
            dev_node, slot_id, slots_state[slot_id]["test_generation"]
        ))
    except Exception as e:
        print(f"[ERROR start_manual_retest]: {e}")
        traceback.print_exc()

async def poll_test_progress(dev_node: str, slot_id: int, generation: int):
    while True:
        try:
            await asyncio.sleep(2)

            if not await asyncio.to_thread(smart_logic.is_disk_present, dev_node):
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

            progress, finished = await asyncio.to_thread(smart_logic.check_test_progress, dev_node)
            slots_state[slot_id]["progress"] = progress
            started_at = slots_state[slot_id].get("test_started_at")
            if started_at:
                started = datetime.fromisoformat(started_at)
                slots_state[slot_id]["test_duration_seconds"] = max(
                    0, (datetime.now(timezone.utc) - started).total_seconds()
                )

            if finished:
                short_test_result = slots_state[slot_id]["info"].get(
                    "test_results", {}
                ).get("short_test", "NOT_RUN")
                final_info = await asyncio.to_thread(smart_logic.get_disk_info, dev_node)
                final_info.update(await asyncio.to_thread(smart_logic.get_extended_disk_info, dev_node))
                final_info["smart_report"] = await asyncio.to_thread(smart_logic.get_smart_report, dev_node)
                test_results = final_info.setdefault("test_results", {})
                test_results["short_test"] = (
                    await asyncio.to_thread(smart_logic.get_self_test_result, dev_node)
                    if short_test_result == "STARTED"
                    else short_test_result
                )
                if test_results["short_test"] == "NOT_VERIFIABLE":
                    test_results["reason"] = (
                        f"{test_results.get('reason', '')}; "
                        "SAS-Selbsttest wurde beendet, aber das Ergebnis wird nicht protokolliert"
                    ).lstrip("; ")
                elif test_results["short_test"] == "NOT_SUPPORTED":
                    test_results["reason"] = (
                        f"{test_results.get('reason', '')}; "
                        "SAS-Short-Selbsttest vom Gerät nicht unterstützt"
                    ).lstrip("; ")
                final_result = smart_logic.evaluate_health(final_info)

                slots_state[slot_id]["status"] = final_result
                slots_state[slot_id]["info"] = final_info

                tested_at = await asyncio.to_thread(save_or_update_hdd, {
                    **final_info,
                    "test_result": final_result,
                    "self_test_result": test_results["short_test"],
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
            ignored_startup_devices.intersection_update(devices)
            for dev_node in list(manually_ejected_devices):
                if dev_node not in devices and not os.path.exists(dev_node):
                    manually_ejected_devices.pop(dev_node, None)

            for dev_node in devices:
                if dev_node in ignored_startup_devices:
                    continue
                info = await asyncio.to_thread(smart_logic.get_disk_info, dev_node)
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
    global startup_ignore_until
    init_db()
    startup_ignore_until = time.monotonic() + 5
    ignored_startup_devices.update(glob.glob("/dev/sd[a-z]"))
    print(
        "[STARTUP] Ignoriere alle während der ersten 5 Sekunden sichtbaren "
        f"Festplatten: {', '.join(sorted(ignored_startup_devices)) or 'keine'}"
    )
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
    return await asyncio.to_thread(get_all_hdds)

@app.get("/api/report/{serial}")
async def api_smart_report(serial: str):
    record = await asyncio.to_thread(get_hdd_by_serial, serial)
    if not record:
        return {"smart_report": None}
    return {"smart_report": record.get("smart_report")}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    selection_mode_active = False
    selected_serials: set[str] = set()
    await manager.connect(websocket)
    try:
        await websocket.send_json({"type": "INIT_STATE", "slots": slots_state})
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "BARCODE_SCAN":
                serial = data.get("serial", "").strip()
                lookup_serial = serial[:-1] if serial.endswith(".") else serial
                result = await asyncio.to_thread(get_hdd_by_serial, serial)
                if not result:
                    result = await asyncio.to_thread(get_hdd_by_serial, lookup_serial)
                matched_serial = result["serial_number"] if result else lookup_serial
                await websocket.send_json({
                    "type": "SCAN_RESULT",
                    "serial": serial,
                    "matched_serial": matched_serial,
                    "data": result,
                    "selection_allowed": (
                        selection_mode_active
                        and matched_serial in selected_serials
                        and result is not None
                        and result.get("test_result") == "PASSED"
                    )
                })
            elif data.get("type") == "START_SELECTION_SCAN":
                selected_serials = {
                    str(serial).strip()
                    for serial in data.get("serials", [])
                    if str(serial).strip()
                }
                selection_mode_active = True
                await websocket.send_json({
                    "type": "SELECTION_SCAN_STARTED",
                    "count": len(selected_serials)
                })
            elif data.get("type") == "STOP_SELECTION_SCAN":
                selection_mode_active = False
                selected_serials = set()
            elif data.get("type") == "EJECT_SLOT":
                slot_id = int(data.get("slot_id"))
                if slots_state.get(slot_id, {}).get("status") == "WIPING":
                    await websocket.send_json({
                        "type": "WIPE_RESULT",
                        "slot_id": slot_id,
                        "status": "blocked",
                        "message": "Der laufende Löschvorgang kann nicht freigegeben werden.",
                    })
                    continue
                if slot_id in slots_state:
                    dev_node = slots_state[slot_id].get("device")
                    serial = slots_state[slot_id].get("info", {}).get("serial_number")
                    if dev_node and serial:
                        manually_ejected_devices[dev_node] = serial
                    slots_state[slot_id] = empty_slot(slot_id)
                    await manager.broadcast({"type": "SLOT_UPDATE", "slot": slots_state[slot_id]})
            elif data.get("type") == "RETEST_SLOT":
                slot_id = int(data.get("slot_id"))
                if slots_state.get(slot_id, {}).get("status") == "WIPING":
                    continue
                asyncio.create_task(start_manual_retest(slot_id))
            elif data.get("type") == "WIPE_SLOT":
                slot_id = int(data.get("slot_id"))
                slot = slots_state.get(slot_id)
                if not slot or slot["status"] == "WIPING":
                    continue
                dev_node = slot.get("device")
                serial = slot.get("info", {}).get("serial_number")
                if not dev_node or not serial:
                    continue
                if data.get("confirmed") is not True and data.get("confirmed_serial") != serial:
                    await websocket.send_json({
                        "type": "WIPE_RESULT",
                        "slot_id": slot_id,
                        "status": "cancelled",
                        "message": "Seriennummer stimmt nicht überein. Löschen wurde abgebrochen.",
                    })
                    continue
                slot["status"] = "WIPING"
                slot["progress"] = 0
                slot["wipe_message"] = "Vollständiges Löschen läuft."
                await asyncio.to_thread(update_wipe_status, serial, "WIPING")
                await manager.broadcast({"type": "SLOT_UPDATE", "slot": slot})
                await asyncio.to_thread(start_wipe_process, slot_id, dev_node, serial)
                asyncio.create_task(monitor_wipe(slot_id, dev_node))
            elif data.get("type") == "CHECK_SLOT":
                slot_id = int(data.get("slot_id"))
                asyncio.create_task(check_disk_for_slot(slot_id))
    except WebSocketDisconnect:
        manager.disconnect(websocket)