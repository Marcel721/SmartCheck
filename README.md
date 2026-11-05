# SmartCheck

SmartCheck is a local web application for testing and selecting hard
drives. It reads SMART information, runs a short SMART self-test, stores the
result in a local SQLite inventory, and provides a barcode-based workflow for
selecting drives that are safe to use.

The application is designed for a test station with a USB/SATA dock or another
way of connecting drives to a Linux computer. It runs locally and is normally
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
- A USB/SATA dock or another supported disk connection
- A USB barcode scanner that behaves like a keyboard

On Debian or Ubuntu, install the system package with:

```bash
sudo apt update
sudo apt install smartmontools python3 python3-venv
```

The Python dependencies are listed in
[requirements.txt](./requirements.txt) and are installed by `run.sh`.

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

If `smartctl` reports `Permission denied` for a device, the current user does
not have sufficient access to the disk. For a quick test, stop the server and
start it with:

```bash
sudo ./run.sh
```

For regular use, it is preferable to configure the required device
permissions for the user instead of running the whole application as root.
After changing group membership, log out and in again before restarting the
application.

Stop the server with `Ctrl+C`.

## Testing a drive

### Automatic detection

In **Testen** mode:

1. Connect a drive to the dock.
2. Wait for Linux to create a block device such as `/dev/sda`.
3. The application reads the drive information and starts a short SMART test.
4. Watch the slot card for the current status and SMART values.
5. When the test is complete, the result is stored in the inventory.

The application listens for Linux block-device events and also polls for
devices. Some USB/SATA docks take time to reinitialize after a drive change.
If the dock does not expose a new device in `lsblk`, the application cannot
test it. In that case, check the dock power, USB connection, and the output of:

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
uncorrectable sector counts are within those thresholds.

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
temporary diagnostic run:

```bash
sudo ./run.sh
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
