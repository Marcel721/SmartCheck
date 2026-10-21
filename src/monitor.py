import asyncio
import pyudev
import traceback
from typing import Callable, Awaitable

class DiskMonitor:
    def __init__(self, on_disk_added: Callable[[str], Awaitable[None]], on_disk_removed: Callable[[str], Awaitable[None]]):
        self.on_disk_added = on_disk_added
        self.on_disk_removed = on_disk_removed
        self.context = pyudev.Context()
        self.monitor = pyudev.Monitor.from_netlink(self.context)
        # Filter auf alle Block-Devices (Platten, Partitionen) lockern
        self.monitor.filter_by(subsystem='block')
        self.loop = None

    async def start(self):
        self.loop = asyncio.get_running_loop()
        observer = pyudev.MonitorObserver(self.monitor, callback=self._udev_event)
        observer.start()
        print("\n==================================================")
        print("[UDEV MONITOR] Maximaler Event-Logger gestartet!")
        print("==================================================\n")

    def _udev_event(self, device):
        try:
            dev_node = device.device_node
            action = device.action
            dev_type = device.get('DEVTYPE', 'unknown')

            # Logge absolut JEDES Block-Event von Linux
            print(f"[UDEV RAW] Action='{action}' | Node='{dev_node}' | DevType='{dev_type}'")

            if not dev_node or "loop" in dev_node or "ram" in dev_node:
                return

            # Wir filtern nur Partitionen raus (z.B. sda1), wollen aber ganze Disks (sda)
            if dev_type == 'partition':
                print(f"  └─► [INFO] Ignoriere Partition {dev_node}")
                return

            if self.loop and self.loop.is_running():
                if action in ('add', 'change', 'online'):
                    print(f"  └─► [TRIGGER ADD/CHANGE] Starte Auswertung für {dev_node}...")
                    asyncio.run_coroutine_threadsafe(self.on_disk_added(dev_node), self.loop)
                elif action in ('remove', 'offline'):
                    print(f"  └─► [INFO] Entfernung wird vom Poller geprüft: {dev_node}")
        except Exception as e:
            print(f"[UDEV ERROR] Fehler im Event-Callback: {e}")
            traceback.print_exc()