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
permission_warning=false
for device in /dev/sd? /dev/sg*; do
    if [[ -e "$device" && ! -r "$device" ]]; then
        permission_warning=true
    fi
done
if [[ "$permission_warning" == true ]]; then
    echo "[WARNUNG] Kein Leserecht für mindestens ein Laufwerk."
    echo "[WARNUNG] Einmalig ausführen: sudo usermod -aG disk \"$USER\""
    echo "[WARNUNG] Danach ab- und wieder anmelden; dieses Programm nicht mit sudo starten."
fi

# Datenbank löschen, falls das Flag gesetzt wurde
if [ "$CLEAR_DB" = true ]; then
    echo "[INFO] Flag '--clear-db' erkannt. Lösche bestehende Datenbank..."
    rm -f data/inventory.db data/inventory.db-journal data/inventory.db-wal data/inventory.db-shm
    echo "[INFO] Datenbank wurde erfolgreich zurückgesetzt."
fi

# Virtuelle Umgebung / Abhängigkeiten prüfen
if [ ! -x "venv/bin/python" ]; then
    python3 -m venv venv
fi

echo "Installiere Abhängigkeiten..."
venv/bin/python -m pip install -q -r requirements.txt

echo "Starte Webserver auf http://localhost:8000 ..."
venv/bin/python -m uvicorn src.main:app --host 0.0.0.0 --port 8000