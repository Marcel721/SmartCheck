import subprocess
import re
import os
from typing import Dict, Any, Tuple
from src.config import (
    MAX_GROWN_DEFECTS,
    MAX_PENDING_SECTORS,
    MAX_REALLOCATED_SECTORS,
    MAX_TEMPERATURE_C,
    MAX_UNCORRECTABLE_SECTORS,
    MAX_UNCORRECTED_READ_ERRORS,
)

def run_cmd(cmd: list, timeout_sec: int = 4) -> str:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
        return (res.stdout or "") + "\n" + (res.stderr or "")
    except Exception as e:
        return str(e)

def run_smartctl_with_fallback(args: list, device_path: str) -> str:
    markers = (
        "serial number",
        "device model",
        "model number",
        "product:",
        "current_pending_sector",
        "smart health status",
        "self-test execution status",
        "percent of test remaining",
        "completed without error",
        "passed",
        "has begun",
        "in progress",
    )
    outputs = []
    for device_type in ("scsi", "sat", None, "auto"):
        command = ["smartctl"]
        if device_type:
            command += ["-d", device_type]
        output = run_cmd(command + args + [device_path])
        outputs.append(output)
        if any(marker in output.lower() for marker in markers):
            return output

    return outputs[-1]

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
    model_match = re.search(
        r"Device Model:\s+(.*)|Model Number:\s+(.*)|Product:\s+(.*)",
        info_out,
        re.IGNORECASE,
    )
    model = "UNKNOWN"
    if model_match:
        model = (model_match.group(1) or model_match.group(2) or model_match.group(3)).strip()

    attr_out = run_smartctl_with_fallback(["-A"], device_path)
    health_out = run_smartctl_with_fallback(["-H"], device_path)

    attr_lower = attr_out.lower()
    health_lower = health_out.lower()
    attributes_supported = (
        "smart support is: unavailable" not in attr_lower
        and any(
            name.lower() in attr_lower
            for name in (
                "current_pending_sector",
                "reallocated_sector_ct",
                "offline_uncorrectable",
            )
        )
    )
    pending = parse_attribute(attr_out, "Current_Pending_Sector") if attributes_supported else None
    reallocated = parse_attribute(attr_out, "Reallocated_Sector_Ct") if attributes_supported else None
    uncorrectable = parse_attribute(attr_out, "Offline_Uncorrectable") if attributes_supported else None
    overall_ok = "passed" in health_lower or "smart health status: ok" in health_lower

    return {
        "device": device_path,
        "serial_number": serial,
        "model": model,
        "pending_sectors": pending,
        "reallocated_sectors": reallocated,
        "uncorrectable_sectors": uncorrectable,
        "smart_overall": "PASSED" if overall_ok else "FAILED",
        "test_results": {
            "identity": "PASSED",
            "attributes": "PASSED" if attributes_supported else "NOT_SUPPORTED",
            "health": "PASSED" if overall_ok else "FAILED",
            "short_test": "NOT_RUN",
            "reason": (
                "Keine ATA-SMART-Attributtabelle; SAS-Diagnosedaten werden nach dem Test mit -x gelesen"
                if not attributes_supported
                else "SMART-Attribute erfolgreich gelesen"
            ),
        }
    }

def is_disk_present(device_path: str) -> bool:
    info = get_disk_info(device_path)
    return info["serial_number"] != "UNKNOWN"

def start_short_test(device_path: str) -> bool:
    out = run_cmd(["smartctl", "-t", "short", device_path])
    print(f"[DEBUG] Start Short Test Antwort:\n{out[:200]}...")
    output_lower = out.lower()
    if "operation not supported" in output_lower or "failed" in output_lower:
        return False
    return "has begun" in output_lower or "testing has started" in output_lower or "in progress" in output_lower

def check_test_progress(device_path: str) -> Tuple[int, bool]:
    out = run_cmd(["smartctl", "-a", device_path])

    match = re.search(r"(\d+)%\s+(?:of\s+test\s+)?remaining", out, re.IGNORECASE)
    if match:
        remaining_pct = int(match.group(1))
        progress_pct = max(0, min(100, 100 - remaining_pct))
        return progress_pct, False

    if "in progress" in out.lower():
        return 50, False

    return 100, True

def parse_sas_extended_info(output: str) -> Dict[str, Any]:
    def integer(pattern: str) -> Any:
        match = re.search(pattern, output, re.IGNORECASE | re.MULTILINE)
        return int(match.group(1)) if match else None

    power_on_match = re.search(
        r"Accumulated power on time, hours:minutes\s+(\d+:\d+)",
        output,
        re.IGNORECASE,
    )
    long_test_match = re.search(
        r"Long \(extended\) Self-test duration:\s*(.*?)\s*$",
        output,
        re.IGNORECASE | re.MULTILINE,
    )

    temperature_c = integer(r"Current Drive Temperature:\s*(\d+)\s*C")
    grown_defects = integer(r"Elements in grown defect list:\s*(\d+)")
    uncorrected_read_errors = integer(r"^read:.*?(\d+)\s*$")
    non_medium_errors = integer(r"Non-medium error count:\s*(\d+)")
    invalid_dword_count = integer(r"Invalid DWORD count\s*=\s*(\d+)")
    running_disparity_errors = integer(r"Running disparity error count\s*=\s*(\d+)")
    loss_of_sync_errors = integer(r"Loss of DWORD synchronization count\s*=\s*(\d+)")
    warnings = []
    if non_medium_errors:
        warnings.append(f"Non-medium errors: {non_medium_errors}")
    if any(value for value in (invalid_dword_count, running_disparity_errors, loss_of_sync_errors)):
        warnings.append("SAS-Linkfehler vorhanden; Verbindung prüfen")
    if temperature_c is not None and temperature_c > MAX_TEMPERATURE_C:
        warnings.append(f"Temperatur über {MAX_TEMPERATURE_C} °C")

    return {
        "temperature_c": temperature_c,
        "trip_temperature_c": integer(r"Drive Trip Temperature:\s*(\d+)\s*C"),
        "power_on_time": power_on_match.group(1) if power_on_match else None,
        "grown_defects": grown_defects,
        "uncorrected_read_errors": uncorrected_read_errors,
        "non_medium_errors": non_medium_errors,
        "invalid_dword_count": invalid_dword_count,
        "running_disparity_errors": running_disparity_errors,
        "loss_of_sync_errors": loss_of_sync_errors,
        "long_self_test_duration": long_test_match.group(1).strip() if long_test_match else None,
        "quality_warnings": warnings,
    }

def get_extended_disk_info(device_path: str) -> Dict[str, Any]:
    return parse_sas_extended_info(run_cmd(["smartctl", "-x", device_path]))

def parse_self_test_result(output: str) -> str:
    latest_test = re.search(r"^\s*#\s*1\s+.*$", output, re.IGNORECASE | re.MULTILINE)
    if latest_test:
        status = latest_test.group(0).lower()
        if "completed" in status and not re.search(r"failed|error", status):
            return "PASSED"
        if re.search(r"failed|error", status):
            return "FAILED"
    return "NOT_VERIFIABLE"

def get_self_test_result(device_path: str) -> str:
    return parse_self_test_result(run_cmd(["smartctl", "-x", device_path]))

def evaluate_health(disk_info: Dict[str, Any]) -> str:
    if disk_info["serial_number"] == "UNKNOWN":
        return "FAILED"
    if disk_info["smart_overall"] != "PASSED":
        return "FAILED"
    if disk_info.get("test_results", {}).get("short_test") != "PASSED":
        return "FAILED"
    if disk_info["pending_sectors"] is not None and disk_info["pending_sectors"] > MAX_PENDING_SECTORS:
        return "FAILED"
    if disk_info["reallocated_sectors"] is not None and disk_info["reallocated_sectors"] > MAX_REALLOCATED_SECTORS:
        return "FAILED"
    if disk_info["uncorrectable_sectors"] is not None and disk_info["uncorrectable_sectors"] > MAX_UNCORRECTABLE_SECTORS:
        return "FAILED"

    sas_data_present = any(
        disk_info.get(field) is not None
        for field in ("grown_defects", "uncorrected_read_errors", "non_medium_errors")
    )
    if sas_data_present:
        if disk_info.get("grown_defects") is None or disk_info["grown_defects"] > MAX_GROWN_DEFECTS:
            return "FAILED"
        if (
            disk_info.get("uncorrected_read_errors") is None
            or disk_info["uncorrected_read_errors"] > MAX_UNCORRECTED_READ_ERRORS
        ):
            return "FAILED"
        if disk_info.get("temperature_c") is not None and disk_info["temperature_c"] > MAX_TEMPERATURE_C:
            return "FAILED"

    return "PASSED"