from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "inventory.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# SMART Schwellenwerte für Gut / Defekt
MAX_PENDING_SECTORS = 0
MAX_REALLOCATED_SECTORS = 0
MAX_UNCORRECTABLE_SECTORS = 0
MAX_TEMPERATURE_C = 55
MAX_GROWN_DEFECTS = 0
MAX_UNCORRECTED_READ_ERRORS = 0

# Anzahl der darzustellenden Slots auf der Webseite
MAX_SLOTS = 8
