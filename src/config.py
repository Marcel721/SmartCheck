from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "inventory.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# SMART Schwellenwerte für Gut / Defekt
MAX_PENDING_SECTORS = 0
MAX_REALLOCATED_SECTORS = 0
MAX_UNCORRECTABLE_SECTORS = 0

# Anzahl der max. darzustellenden Slots auf der Webseite (z. B. 24)
MAX_SLOTS = 24
