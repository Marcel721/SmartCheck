#!/bin/bash

CLEAR_DB=false

# Argumente / Flags auswerten
for arg in "$@"
do
    case $arg in
        --clear-db|-c|--reset)
            CLEAR_DB=true
            shift
            ;;
    esac
done

echo "=== Starte HDD Triage Station ==="

# Datenbank löschen, falls das Flag gesetzt wurde
if [ "$CLEAR_DB" = true ]; then
    echo "[INFO] Flag '--clear-db' erkannt. Lösche bestehende Datenbank..."
    rm -f data/inventory.db data/inventory.db-journal data/inventory.db-wal data/inventory.db-shm
    echo "[INFO] Datenbank wurde erfolgreich zurückgesetzt."
fi

# Virtuelle Umgebung / Abhängigkeiten prüfen
if [ -d "venv" ]; then
    source venv/bin/activate
else
    python3 -m venv venv
    source venv/bin/activate
fi

echo "Installiere Abhängigkeiten..."
pip install -q -r requirements.txt

echo "Starte Webserver auf http://localhost:8000 ..."
python3 -m uvicorn src.main:app --host 0.0.0.0 --port 8000