#!/bin/sh
# Startskript. Als Home-Assistant-Add-on werden die Einstellungen aus /data/options.json gelesen,
# sonst gelten die Umgebungsvariablen (FMT_USER, FMT_PASSWORD, DATA_DIR, PORT).
set -e

if [ -f /data/options.json ]; then
  eval "$(python3 - <<'PY'
import json, shlex
o = json.load(open("/data/options.json"))
print("export FMT_USER=" + shlex.quote(str(o.get("username") or "fmt")))
print("export FMT_PASSWORD=" + shlex.quote(str(o.get("password") or "")))
PY
)"
  export PORT=8099
  # Der Zugang über die Home-Assistant-Seitenleiste (Ingress) ist bereits durch HA angemeldet
  export FMT_TRUSTED_IPS=172.30.32.2
  # Vorhandene Datenbank beim allerersten Start übernehmen (Datei nach /share/filament_import.db legen)
  export FILAMENT_IMPORT_DB=/share/filament_import.db
fi

cd /opt/filament
exec python3 -m uvicorn server:app --host 0.0.0.0 --port "${PORT:-8080}"
