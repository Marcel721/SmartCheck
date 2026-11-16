# SmartCheck

SmartCheck is a local web application for testing and selecting hard
drives. It reads SMART information, runs a short SMART self-test, stores the
result in a local SQLite inventory, and provides a barcode-based workflow for
selecting drives that are safe to use.

The application is designed for a test station with direct internal SATA/SAS
connections, a USB/SATA dock, or another way of connecting drives to a Linux
computer. It runs locally and is normally
opened at <http://127.0.0.1:8000/>.

## What it is used for

The application supports two workflows:

1. **Testing drives**
   - Detect a connected disk.
   - Read its model, serial number, SMART health, and important sector counts.
   - Run a short SMART self-test.
   - Classify the drive as `PASSED` or `FAILED` based on variables defined in `src/config.py`.
   - Store the result in the local inventory.

2. **Selecting tested drives**
   - Show all drives previously stored in the inventory.
   - Preselect drives whose test result is `PASSED`.
   - Manually include or exclude drives with checkboxes.
   - Scan a barcode and immediately show a large green or red decision:
     - **PLATTE NEHMEN**: the drive is selected and passed its test.
     - **PLATTE NICHT NEHMEN**: the drive is not selected, is not in the
       inventory, or did not pass.

Barcode values with a trailing period are supported. For example,
`W4Z1EPVJ.` is also checked as `W4Z1EPVJ`.

## Requirements

- Linux
- Python 3.10 or newer
- `smartctl` from the `smartmontools` package
- Permission to read the connected block devices, such as `/dev/sda`
- Direct SATA/SAS connections or a USB/SATA dock with SMART passthrough
- A USB barcode scanner that behaves like a keyboard

On Debian or Ubuntu, install the system package with:

```bash
sudo apt update
sudo apt install smartmontools libcap2-bin python3 python3-venv
```

The Python dependencies are listed in
[requirements.txt](./requirements.txt) and are installed by `run.sh`.

Before starting the application, confirm that Linux sees the installed drives:

```bash
lsblk -d -o NAME,PATH,TYPE,TRAN,MODEL,SERIAL,SIZE
sudo smartctl -d scsi -i /dev/sda
```

Use the whole-disk device names shown by `lsblk`, such as `/dev/sda`; do not
use partition names such as `/dev/sda1`.

## Starting the application

From the project directory, run:

```bash
./run.sh
```

The script:

1. Optionally resets the database when called with `--clear-db`.
2. Creates or activates the local `venv` virtual environment.
3. Installs the Python requirements.
4. Starts Uvicorn on `0.0.0.0:8000`.

Open the application in a browser:

```text
http://127.0.0.1:8000/
```

If `smartctl` reports `Permission denied` for a device, grant the application
user access to raw disk devices:

```bash
sudo usermod -aG disk "$USER"
```

Log out and back in before restarting the application. Do not put the password
in the script and do not run the application with `sudo` for regular use.

Some Linux installations allow SMART reads for the `disk` group but still
block self-test commands with `Operation not permitted`. Grant the installed
`smartctl` binary the raw-I/O capability once:

```bash
sudo setcap cap_sys_rawio+ep "$(command -v smartctl)"
getcap "$(command -v smartctl)"
```

The second command should show `cap_sys_rawio+ep`. After this, the application
can start SAS self-tests as the normal user. Reapply the command after a
smartmontools package upgrade if the capability is removed.

```bash
sudo usermod -aG disk "$USER"
```

The `disk` group grants broad raw-disk access. Use a dedicated service account
or a udev rule instead if that access is too broad for the workstation.

Stop the server with `Ctrl+C`.

## Testing a drive

### Automatic detection

In **Testen** mode:

1. Start the application with the computer's existing disks connected.
2. During the first five seconds, every visible whole-disk device is recorded
   as a startup disk and intentionally ignored.
3. Connect a drive after the five-second startup window.
4. Wait for Linux to create a whole-disk device such as `/dev/sda`.
5. The application reads the drive information and starts a short SMART test.
6. Watch the slot card for the current status and SMART values.
7. When the test is complete, the result is stored in the inventory.

For SAS disks, the slot shows the self-test progress from `smartctl` every two
seconds. After completion, the self-test result is stored as `PASSED`, `FAILED`,
or `NOT_VERIFIABLE` when the controller does not expose a result log.


The application listens for Linux block-device events and also polls for
whole-disk `/dev/sdX` devices. It ignores partitions and NVMe devices. If a
drive does not appear in `lsblk`, the application cannot test it. In that case,
check the bay, cable, controller, and the output of:

```bash
lsblk
```

### Manual slot check

If automatic detection does not assign the drive to a slot:

1. Insert the drive.
2. In an empty slot, click **Festplatte prüfen**.
3. The application scans the available `/dev/sdX` devices.
4. If the drive is found, it is assigned to that slot.
5. A known drive shows its existing inventory result; a new drive starts a
   short test.

### Changing drives

The safest workflow with a dock is:

1. Wait until the current test is finished.
2. Click **Slot freigeben** if you want to clear the slot immediately.
3. Physically remove the drive.
4. Wait until it disappears from `lsblk`.
5. Insert the next drive.
6. Wait for the new device to appear in `lsblk`.
7. Use automatic detection or click **Festplatte prüfen**.

Disks seen during the five-second startup window remain ignored. To test one of
them, stop and restart the application with that disk removed, then connect it
after the startup window has finished.

**Slot freigeben does not physically eject a disk.** It only clears the
displayed slot and suppresses rediscovery of the same still-connected drive.
The dock may still require a power cycle between drives if it does not
reinitialize the USB/SATA connection correctly.

## Selecting drives for use

1. Click **Platten auswählen** in the header.
2. Review the inventory table.
3. Drives with `PASSED` are selected automatically.
4. Check or uncheck drives as needed.
5. Click **Scanner für Auswahl starten**.
6. A large overlay appears with **PLATTEN SCANNEN**.
7. Scan a drive barcode.
8. The overlay turns green or red:
   - Green means the drive may be used.
   - Red means the drive must not be used.
9. Click **Auswahl ändern** to close the scan overlay and edit the selection.

The barcode scanner should be configured to send an Enter key after each
barcode. The application also accepts a final `.` and retries the lookup
without that period.

## Inventory and database

The inventory is stored in:

```text
data/inventory.db
```

Each inventory record contains the serial number, model, SMART sector
counts, SMART health, test result, slot information, and test timestamp.

To reset the inventory deliberately:

```bash
./run.sh --clear-db
```

This permanently deletes the local inventory database before starting the
server. Do not use this option if the stored test history is still needed.

The inventory can also be read as JSON from:

```text
http://127.0.0.1:8000/api/inventory
```

## Project structure

```text
hdd-triage/
├── run.sh                 # Setup and application startup script
├── requirements.txt        # Python dependencies
├── data/
│   └── inventory.db       # SQLite inventory created at runtime
├── src/
│   ├── main.py            # FastAPI app, WebSocket actions, slots, polling
│   ├── monitor.py         # Linux udev block-device event monitor
│   ├── smart_logic.py     # smartctl calls, parsing, and health evaluation
│   ├── database.py        # SQLite schema and inventory operations
│   └── config.py          # Paths, SMART thresholds, and slot count
├── templates/
│   └── index.html         # Initial HTML page and slot layout
└── static/
    ├── js/main.js         # WebSocket client, rendering, modes, barcode UI
    └── css/style.css      # Page layout, buttons, cards, and scan overlay
```

## How the parts work together

### Backend

`src/main.py` starts the FastAPI application and maintains the in-memory state
of the configured slots. It provides:

- the web page at `/`
- the inventory API at `/api/inventory`
- the WebSocket endpoint at `/ws`
- disk insertion and removal handling
- automatic polling for `/dev/sdX` devices
- manual slot checks, retests, and slot release

### Device monitoring

`src/monitor.py` listens to Linux udev block-device events. Partition events
are ignored because the application tests complete disks, not partitions.
The background poller in `src/main.py` provides a second detection path for
docks whose udev events are incomplete or arrive in an unusual order.

### SMART and tests

`src/smart_logic.py` calls `smartctl`, including fallback device modes for
USB/SATA adapters. It extracts the serial number, model, health result, and
sector attributes. It also starts and checks short SMART self-tests.

The configured thresholds are in `src/config.py`. A drive is considered
healthy only when its SMART health passes and pending, reallocated, and
uncorrectable sector counts are within those thresholds. For SAS drives, the
short self-test must pass, grown defects and uncorrected read errors must be
zero, and the measured temperature must not exceed 55 C. SAS link errors and
non-medium errors are displayed as warnings because they may indicate a cable,
backplane, or HBA problem rather than a disk-medium failure.

### Database

`src/database.py` creates and migrates the SQLite `hdds` table and saves the
final result after a test. The serial number is the primary key, so testing
the same drive again updates its existing inventory record.

### Frontend

`templates/index.html` provides the initial page. `static/js/main.js` opens a
WebSocket and updates slot cards immediately when the backend broadcasts a
change. It also handles the Testen/Platten auswählen modes, barcode scans,
inventory checkboxes, and the green/red scan overlay.

## Troubleshooting

### The drive is not detected

First verify that Linux sees the drive:

```bash
lsblk
ls /dev/sd*
```

If no new device appears, the issue is below the application level. Check the
dock, power, USB cable, USB port, and drive.

### SMART permission errors

If the log contains:

```text
Permission denied
```

verify the permissions of the device and the user running the server. For a
temporary diagnostic run, query a disk directly with:

```bash
sudo smartctl -d scsi -a /dev/sda
```

### The dock needs a power cycle

Some USB/SATA docks keep the old device state after a drive is removed. If a
new drive does not appear in `lsblk`, power-cycle the dock, insert the new
drive, and verify `lsblk` before using the application again.

### The browser shows old JavaScript

Reload the page with:

```text
Ctrl+Shift+R
```

The page uses versioned JavaScript URLs so that normal browser caching does
not hide frontend changes.
