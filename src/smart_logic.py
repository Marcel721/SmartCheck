import subprocess
import re
import os
from typing import Dict, Any, Tuple
from src.config import MAX_PENDING_SECTORS, MAX_REALLOCATED_SECTORS, MAX_UNCORRECTABLE_SECTORS

def run_cmd(cmd: list, timeout_sec: int = 4) -> str:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
        return (res.stdout or "") + "\n" + (res.stderr or "")
    except Exception as e:
        return str(e)

def run_smartctl_with_fallback(args: list, device_path: str) -> str:
    out_sat = run_cmd(["smartctl", "-d", "sat"] + args + [device_path])
    if any(k in out_sat for k in ["Serial Number", "Current_Pending_Sector", "PASSED", "has begun", "in progress"]):
        return out_sat

    out_std = run_cmd(["smartctl"] + args + [device_path])
    if any(k in out_std for k in ["Serial Number", "Current_Pending_Sector", "PASSED", "has begun", "in progress"]):
        return out_std

    return run_cmd(["smartctl", "-d", "auto"] + args + [device_path])

def parse_attribute(output: str, attr_name: str) -> int:
    match = re.search(rf"{attr_name}.*?\s+(\d+)\s*$", output, re.MULTILINE | re.IGNORECASE)
    return int(match.group(1)) if match else 0

def get_disk_info(device_path: str) -> Dict[str, Any]:
    if not os.path.exists(device_path):
        return {
            "device": device_path,
            "serial_number": "UNKNOWN",
            "model": "UNKNOWN",
            "pending_sectors": 0,
            "reallocated_sectors": 0,
            "uncorrectable_sectors": 0,
            "smart_overall": "FAILED"
        }

    info_out = run_smartctl_with_fallback(["-i"], device_path)
    
    serial_match = re.search(r"Serial Number:\s+(.*)", info_out, re.IGNORECASE)
    if not serial_match:
        return {
            "device": device_path,
            "serial_number": "UNKNOWN",
            "model": "UNKNOWN",
            "pending_sectors": 0,
            "reallocated_sectors": 0,
            "uncorrectable_sectors": 0,
            "smart_overall": "FAILED"
        }

    serial = serial_match.group(1).strip()
    model_match = re.search(r"Device Model:\s+(.*)|Model Number:\s+(.*)", info_out, re.IGNORECASE)
    model = "UNKNOWN"
    if model_match:
        model = (model_match.group(1) or model_match.group(2)).strip()

    attr_out = run_smartctl_with_fallback(["-A"], device_path)
    health_out = run_smartctl_with_fallback(["-H"], device_path)

    pending = parse_attribute(attr_out, "Current_Pending_Sector")
    reallocated = parse_attribute(attr_out, "Reallocated_Sector_Ct")
    uncorrectable = parse_attribute(attr_out, "Offline_Uncorrectable")

    overall_ok = "PASSED" in health_out or "OK" in health_out

    return {
        "device": device_path,
        "serial_number": serial,
        "model": model,
        "pending_sectors": pending,
        "reallocated_sectors": reallocated,
        "uncorrectable_sectors": uncorrectable,
        "smart_overall": "PASSED" if overall_ok else "FAILED"
    }

def is_disk_present(device_path: str) -> bool:
    info = get_disk_info(device_path)
    return info["serial_number"] != "UNKNOWN"

def start_short_test(device_path: str) -> bool:
    out = run_smartctl_with_fallback(["-t", "short"], device_path)
    print(f"[DEBUG] Start Short Test Antwort:\n{out[:200]}...")
    return "has begun" in out.lower() or "testing has started" in out.lower() or "in progress" in out.lower()

def check_test_progress(device_path: str) -> Tuple[int, bool]:
    out = run_smartctl_with_fallback(["-c"], device_path)

    match = re.search(r"(\d+)%\s+(?:of\s+test\s+)?remaining", out, re.IGNORECASE)
    if match:
        remaining_pct = int(match.group(1))
        progress_pct = max(0, min(100, 100 - remaining_pct))
        return progress_pct, False

    if "in progress" in out.lower():
        return 50, False

    return 100, True

def evaluate_health(disk_info: Dict[str, Any]) -> str:
    if disk_info["serial_number"] == "UNKNOWN":
        return "FAILED"
    if disk_info["smart_overall"] != "PASSED":
        return "FAILED"
    if disk_info["pending_sectors"] > MAX_PENDING_SECTORS:
        return "FAILED"
    if disk_info["reallocated_sectors"] > MAX_REALLOCATED_SECTORS:
        return "FAILED"
    if disk_info["uncorrectable_sectors"] > MAX_UNCORRECTABLE_SECTORS:
        return "FAILED"

    return "PASSED"