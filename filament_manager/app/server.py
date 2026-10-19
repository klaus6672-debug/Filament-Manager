"""Filament Manager – Server (FastAPI + SQLite).

Liefert die App (static/) aus und stellt eine kleine JSON-API bereit:

    GET    /api/slots          alle belegten Slots
    POST   /api/slots          Slot anlegen/überschreiben
    POST   /api/move           Spule in leeren Slot verschieben
    DELETE /api/slots/{slot}   Slot leeren
    GET    /api/summary        Kennzahlen für Home Assistant (?low=100 = Grenze für "knapp" in Gramm)
    GET    /api/ams            Live-Abgleich: AMS-Fächer aus Home Assistant gegen die Slots AMS-n-m
    POST   /api/ams/apply      Abgleich-Vorschlag übernehmen (Kennung merken / verschieben / neu anlegen)
    POST   /api/ams/map        Zuordnung "AMS n der App" -> "AMS in Home Assistant" speichern
    GET    /api/health         Lebenszeichen (immer ohne Passwort erreichbar)

Konfiguration über Umgebungsvariablen:
    DATA_DIR            Ordner mit filament.db            (Standard: ./data)
    FILAMENT_IMPORT_DB  Pfad zu einer vorhandenen filament.db, die beim allerersten Start übernommen wird
    FMT_PASSWORD        Optional: schützt App und API per Benutzername/Passwort (HTTP Basic)
    FMT_USER            Benutzername (Standard: fmt)
    HA_URL, HA_TOKEN    Nur außerhalb des HA-Add-ons: Adresse und Langzeit-Zugriffstoken von Home Assistant
                        (im Add-on wird stattdessen automatisch der Supervisor-Zugang genutzt)
    FMT_TRUSTED_IPS     Kommagetrennte Absender-IPs, die ohne Passwort durchgelassen werden
                        (Home-Assistant-Add-on: der Ingress-Zugang des Supervisors, 172.30.32.2)
"""
import base64
import hmac
import json
import os
import re
import shutil
import sqlite3
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "filament.db"

AUTH_USER = os.environ.get("FMT_USER", "fmt")
AUTH_PASSWORD = os.environ.get("FMT_PASSWORD", "")
TRUSTED_IPS = {ip.strip() for ip in os.environ.get("FMT_TRUSTED_IPS", "").split(",") if ip.strip()}

# Diese Pfade brauchen kein Passwort: sie enthalten keine Daten, werden aber vom Browser
# für die Installation der App ohne Anmeldedaten abgerufen.
PUBLIC_PATHS = {"/api/health", "/manifest.webmanifest", "/sw.js"}
PUBLIC_PREFIXES = ("/icons/",)


# ---------------------------------------------------------------- Datenbank

def ensure_column(cursor, table_name: str, column_name: str, column_def: str):
    cursor.execute(f"PRAGMA table_info({table_name})")
    if column_name not in [row[1] for row in cursor.fetchall()]:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    import_path = os.environ.get("FILAMENT_IMPORT_DB", "")
    if import_path and not DB_PATH.exists() and Path(import_path).is_file():
        shutil.copyfile(import_path, DB_PATH)
        print(f"Vorhandene Datenbank übernommen: {import_path}")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS filaments(
            slot TEXT PRIMARY KEY,
            material TEXT,
            color TEXT,
            color_name TEXT,
            manufacturer TEXT,
            weight INTEGER
        )
        """
    )
    ensure_column(c, "filaments", "color_name", "TEXT")
    ensure_column(c, "filaments", "tag_uid", "TEXT")  # RFID-Kennung (Bambu), wird vom AMS-Abgleich gelernt
    c.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_all() -> dict:
    with db() as conn:
        rows = conn.execute("SELECT * FROM filaments").fetchall()
    return {r["slot"]: dict(r) for r in rows}


# ---------------------------------------------------------------- Modelle

class SlotIn(BaseModel):
    slot: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    material: str = Field(min_length=1, max_length=40)
    color: str = Field(default="#888888", max_length=32)
    color_name: str = Field(default="", max_length=60)
    manufacturer: str = Field(default="", max_length=60)
    weight: int = Field(default=0, ge=0, le=100000)
    tag_uid: str | None = Field(default=None, max_length=40)


class MoveIn(BaseModel):
    from_slot: str = Field(min_length=1, max_length=32)
    to_slot: str = Field(min_length=1, max_length=32)


# ---------------------------------------------------------------- AMS-Live-Abgleich (Home Assistant)

HA_STATES_TIMEOUT = 6
ZERO_TAG = re.compile(r"^0*$")
SLOT_NAME = re.compile(r"^(.*\S)\s+Slot\s+(\d+)$")
AMS_SLOT_RE = re.compile(r"^AMS-(\d+)-(\d+)$")
AMS_UNITS = (1, 2, 3)
VARIANTS = {"basic", "matte", "silk", "hf", "tough", "cf", "gf", "plus", "+", "lite", "metal", "sparkle", "marble", "galaxy"}
COLOR_OK = 10      # Farbabstand (CIE-Lab ΔE), bis zu dem Farben als "gleich" gelten (Grau ↔ Braun ≈ 26, Blau ↔ Marine ≈ 21)
COLOR_CAND = 30    # Farbabstand, bis zu dem eine Spule als Kandidat vorgeschlagen wird


class AmsUnavailable(Exception):
    pass


def ha_endpoint():
    """Adresse und Token für den Zugriff auf Home Assistant (Add-on: Supervisor, sonst HA_URL/HA_TOKEN)."""
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if token:
        return "http://supervisor/core/api/states", token
    url, token = os.environ.get("HA_URL", "").rstrip("/"), os.environ.get("HA_TOKEN", "")
    if url and token:
        return url + "/api/states", token
    raise AmsUnavailable(
        "Kein Zugang zu Home Assistant. Im Add-on ist er automatisch vorhanden; "
        "bei Docker bitte HA_URL und HA_TOKEN setzen."
    )


def fetch_ha_states():
    url, token = ha_endpoint()
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=HA_STATES_TIMEOUT) as resp:
            return json.load(resp)
    except Exception as e:  # Netzwerk, Token, JSON …
        raise AmsUnavailable(f"Home Assistant nicht erreichbar ({type(e).__name__}).") from e


def norm_hex(value):
    """'#RRGGBBAA' / '#RRGGBB' -> '#rrggbb' (oder None)."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?", (value or "").strip())
    return "#" + m.group(1).lower() if m else None


def _lab(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    r, g, b = ((v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4) for v in (r, g, b))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def color_distance(a, b):
    """Wahrgenommener Farbabstand (CIE-Lab ΔE76). 0 = gleich, ~2 kaum sichtbar, >20 deutlich andere Farbe."""
    a, b = norm_hex(a), norm_hex(b)
    if not a or not b:
        return 999.0
    return sum((x - y) ** 2 for x, y in zip(_lab(a), _lab(b))) ** 0.5


def base_type(material):
    return (material or "").strip().split(" ")[0].upper()


def collect_units(states):
    """Fächer aller AMS-Einheiten aus den HA-Sensoren (erkannt an den Attributen slot/tag_uid/empty)."""
    units = {}
    for st in states:
        attrs = st.get("attributes") or {}
        if not str(st.get("entity_id", "")).startswith("sensor.") or not {"slot", "tag_uid", "empty"} <= attrs.keys():
            continue
        m = SLOT_NAME.match(str(attrs.get("friendly_name", "")))
        if not m:
            continue
        name, pos = m.group(1), int(attrs.get("slot") or m.group(2))
        tag = str(attrs.get("tag_uid") or "")
        units.setdefault(name, {})[pos] = {
            "pos": pos,
            "empty": bool(attrs.get("empty")),
            "type": str(attrs.get("type") or ""),
            "name": str(attrs.get("name") or ""),
            "color": norm_hex(attrs.get("color")),
            "tag_uid": None if ZERO_TAG.match(tag) else tag.upper(),
            "tray_weight": int(attrs.get("tray_weight") or 0),
        }
    return units


def unit_order(names):
    return sorted(names, key=lambda n: re.sub(r"[^a-z0-9]+", " ", n.lower()).split())


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def ams_mapping(conn, names):
    """AMS n der App -> Name der AMS in Home Assistant (gespeichert, sonst nach Namen sortiert)."""
    try:
        saved = json.loads(get_setting(conn, "ams_map", "{}"))
    except ValueError:
        saved = {}
    order = unit_order(names)
    mapping = {}
    for i, unit in enumerate(AMS_UNITS):
        want = saved.get(str(unit))
        mapping[unit] = want if want in names else (order[i] if i < len(order) else None)
    return mapping


def describe_live(tray):
    """Aus einem Live-Fach Hersteller/Material/Farbname für einen neuen Eintrag ableiten."""
    tokens = tray["name"].split()
    typ = tray["type"].upper()
    idx = next((i for i, t in enumerate(tokens) if t.upper() == typ), None)
    if idx is None:
        return {"manufacturer": "", "material": tray["type"] or "PLA", "color_name": ""}
    variants = [t for t in tokens[idx + 1:] if t.lower() in VARIANTS]
    rest = [t for t in tokens[idx + 1:] if t.lower() not in VARIANTS]
    return {
        "manufacturer": " ".join(tokens[:idx]),
        "material": " ".join([tray["type"], *variants]),
        "color_name": " ".join(rest),
    }


def variants_of(text):
    return {t.lower() for t in (text or "").replace("+", " + ").split() if t.lower() in VARIANTS}


def same_material(material, tray):
    """Gleicher Grundtyp (PLA, PETG …) und – wenn beide eine Sorte nennen – gleiche Sorte (Basic ≠ Matte)."""
    if base_type(material) != tray["type"].upper():
        return False
    mine, live = variants_of(material), variants_of(tray["name"])
    return not mine or not live or bool(mine & live)


def compatible(spool, tray):
    return (
        bool(spool)
        and same_material(spool["material"], tray)
        and color_distance(spool["color"], tray["color"]) <= COLOR_OK
    )


def build_ams(states, conn):
    units = collect_units(states)
    mapping = ams_mapping(conn, list(units))
    spools = {r["slot"]: dict(r) for r in conn.execute("SELECT * FROM filaments").fetchall()}
    by_tag = {s["tag_uid"].upper(): s for s in spools.values() if s.get("tag_uid")}
    # Spulen außerhalb des AMS, die als Kandidaten für ein Fach in Frage kommen
    outside = [s for s in spools.values() if not AMS_SLOT_RE.match(s["slot"])]

    result = []
    for unit in AMS_UNITS:
        ha_name = mapping.get(unit)
        trays = []
        for pos in range(1, 5):
            slot = f"AMS-{unit}-{pos}"
            live = units.get(ha_name, {}).get(pos) if ha_name else None
            spool = spools.get(slot)
            entry = {
                "slot": slot,
                "pos": pos,
                "live": live,
                "app": (
                    {k: spool.get(k) for k in ("material", "color", "color_name", "manufacturer", "weight", "tag_uid")}
                    if spool else None
                ),
                "status": "unknown",
                "action": None,
                "candidates": [],
            }
            trays.append(entry)
            if live is None:
                continue
            if live["empty"]:
                entry["status"] = "gone" if spool else "empty"
                continue
            tag_owner = by_tag.get(live["tag_uid"]) if live["tag_uid"] else None
            if tag_owner and tag_owner["slot"] == slot:
                if same_material(tag_owner["material"], live):
                    entry["status"] = "ok"
                else:  # Es ist dieselbe Spule (Kennung), aber die App führt eine andere Sorte, z. B. Basic statt Matte
                    entry["status"] = "fix"
                    entry["action"] = {"kind": "fixmat", "label": f"Sorte → {describe_live(live)['material']}"}
                continue
            if tag_owner:  # Spule ist bekannt, steckt aber laut App woanders
                entry["status"] = "moved"
                entry["action"] = {"kind": "move", "from_slot": tag_owner["slot"], "label": f"{tag_owner['slot']} → hierher"}
                continue
            if compatible(spool, live):
                if live["tag_uid"] and not (spool.get("tag_uid") or "").strip():
                    entry["status"] = "learn"
                    entry["action"] = {"kind": "learn", "label": "Kennung merken"}
                else:
                    entry["status"] = "ok"
                continue
            entry["status"] = "mismatch" if spool else "missing"
            cands = []
            for s in outside:
                if s.get("tag_uid") or not same_material(s["material"], live):
                    continue
                d = color_distance(s["color"], live["color"])
                if d <= COLOR_CAND:
                    cands.append((d, s))
            cands.sort(key=lambda x: (x[0], x[1]["slot"]))
            entry["candidates"] = [
                {
                    "slot": s["slot"],
                    "label": " · ".join(x for x in (s["slot"], s["manufacturer"], s["material"], s["color_name"]) if x),
                    "color": s["color"],
                }
                for _, s in cands[:6]
            ]
        result.append({"unit": unit, "ha_name": ha_name, "trays": trays})
    return {"available": True, "units": result, "ha_units": unit_order(list(units))}


# ---------------------------------------------------------------- App

init_db()
app = FastAPI(title="Filament Manager", docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    fields = ", ".join(".".join(str(p) for p in e["loc"][1:]) for e in exc.errors())
    return JSONResponse({"error": f"Ungültige Eingabe ({fields})"}, status_code=422)


def _authorized(request: Request) -> bool:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return False
    try:
        user, _, password = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except Exception:
        return False
    return hmac.compare_digest(user.encode(), AUTH_USER.encode()) and hmac.compare_digest(
        password.encode(), AUTH_PASSWORD.encode()
    )


@app.middleware("http")
async def guard_and_headers(request: Request, call_next):
    path = request.url.path
    public = path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)
    trusted = bool(request.client and request.client.host in TRUSTED_IPS)
    if AUTH_PASSWORD and not public and not trusted and not _authorized(request):
        return Response(
            "Anmeldung erforderlich",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Filament Manager", charset="UTF-8"'},
        )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif path in ("/", "/index.html", "/sw.js", "/manifest.webmanifest"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---------------------------------------------------------------- API

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/slots")
def list_slots():
    slots = get_all()
    return {
        "slots": slots,
        "count": len(slots),
        "total_weight": sum(int(s["weight"] or 0) for s in slots.values()),
    }


@app.post("/api/slots")
def upsert_slot(item: SlotIn):
    with db() as conn:
        existing = conn.execute("SELECT color_name, tag_uid FROM filaments WHERE slot=?", (item.slot,)).fetchone()
        # Ein vorhandener Farbname bleibt erhalten, wenn keiner mitgeschickt wird
        color_name = item.color_name.strip() or (existing["color_name"] if existing and existing["color_name"] else "")
        # Die gelernte RFID-Kennung bleibt beim Bearbeiten der Spule erhalten
        tag_uid = item.tag_uid if item.tag_uid is not None else (existing["tag_uid"] if existing else None)
        conn.execute(
            """
            INSERT OR REPLACE INTO filaments(slot, material, color, color_name, manufacturer, weight, tag_uid)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (item.slot, item.material, item.color, color_name, item.manufacturer, item.weight, tag_uid),
        )
    return {"ok": True}


@app.post("/api/move")
def move_slot(move: MoveIn):
    with db() as conn:
        if not conn.execute("SELECT slot FROM filaments WHERE slot=?", (move.from_slot,)).fetchone():
            return JSONResponse({"error": "Quelle nicht gefunden"}, status_code=404)
        if conn.execute("SELECT slot FROM filaments WHERE slot=?", (move.to_slot,)).fetchone():
            return JSONResponse({"error": "Zielslot ist belegt"}, status_code=400)
        conn.execute("UPDATE filaments SET slot=? WHERE slot=?", (move.to_slot, move.from_slot))
    return {"ok": True}


@app.delete("/api/slots/{slot}")
def delete_slot(slot: str):
    with db() as conn:
        conn.execute("DELETE FROM filaments WHERE slot=?", (slot,))
    return {"ok": True}


@app.get("/api/summary")
def summary(low: int = 100):
    """Kennzahlen, z. B. für Home-Assistant-Sensoren. `low` = Gewicht in Gramm, ab dem eine Spule als knapp gilt."""
    slots = get_all()
    by_material, by_manufacturer = {}, {}
    low_stock = []
    for s in slots.values():
        grams = int(s["weight"] or 0)
        by_material[s["material"]] = by_material.get(s["material"], 0) + grams
        by_manufacturer[s["manufacturer"] or "Unbekannt"] = by_manufacturer.get(s["manufacturer"] or "Unbekannt", 0) + grams
        if grams <= low:
            low_stock.append(
                {
                    "slot": s["slot"],
                    "material": s["material"],
                    "color": s.get("color_name") or s["color"],
                    "manufacturer": s["manufacturer"],
                    "weight": grams,
                }
            )
    low_stock.sort(key=lambda x: (x["weight"], x["slot"]))
    return {
        "spool_count": len(slots),
        "total_weight_g": sum(by_material.values()),
        "low_threshold_g": low,
        "low_stock_count": len(low_stock),
        "low_stock": low_stock[:30],
        "by_material": dict(sorted(by_material.items(), key=lambda kv: -kv[1])),
        "by_manufacturer": dict(sorted(by_manufacturer.items(), key=lambda kv: -kv[1])),
    }


class AmsApplyIn(BaseModel):
    slot: str = Field(pattern=r"^AMS-[1-3]-[1-4]$")
    kind: str = Field(pattern=r"^(learn|move|create|fixmat)$")
    from_slot: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,32}$")


class AmsMapIn(BaseModel):
    unit: int = Field(ge=1, le=3)
    name: str = Field(min_length=1, max_length=120)


def _live_tray(slot, conn):
    states = fetch_ha_states()
    units = collect_units(states)
    unit, pos = (int(x) for x in AMS_SLOT_RE.match(slot).groups())
    name = ams_mapping(conn, list(units)).get(unit)
    return units.get(name, {}).get(pos)


def _clear_tag(conn, tag, keep_slot):
    conn.execute("UPDATE filaments SET tag_uid=NULL WHERE tag_uid=? AND slot<>?", (tag, keep_slot))


@app.get("/api/ams")
def ams_status():
    try:
        states = fetch_ha_states()
    except AmsUnavailable as e:
        return {"available": False, "reason": str(e), "units": []}
    with db() as conn:
        return build_ams(states, conn)


@app.post("/api/ams/apply")
def ams_apply(item: AmsApplyIn):
    try:
        with db() as conn:
            live = _live_tray(item.slot, conn)
            if not live or live["empty"]:
                return JSONResponse({"error": "Das Fach ist laut AMS leer oder nicht verbunden."}, status_code=409)
            target = conn.execute("SELECT * FROM filaments WHERE slot=?", (item.slot,)).fetchone()

            if item.kind == "learn":
                if not target or not live["tag_uid"]:
                    return JSONResponse({"error": "Nichts zu merken."}, status_code=400)
                _clear_tag(conn, live["tag_uid"], item.slot)
                conn.execute("UPDATE filaments SET tag_uid=? WHERE slot=?", (live["tag_uid"], item.slot))

            elif item.kind == "fixmat":
                if not target:
                    return JSONResponse({"error": "Der Slot ist leer."}, status_code=400)
                conn.execute("UPDATE filaments SET material=? WHERE slot=?", (describe_live(live)["material"], item.slot))

            elif item.kind == "move":
                src_slot = item.from_slot
                src = conn.execute("SELECT * FROM filaments WHERE slot=?", (src_slot or "",)).fetchone()
                if not src or src_slot == item.slot:
                    return JSONResponse({"error": "Quell-Spule nicht gefunden."}, status_code=404)
                # Belegt? Dann tauschen die beiden Spulen die Plätze (Spule aus dem Regal gegen die im AMS).
                tmp = f"__swap_{os.getpid()}"
                if target:
                    conn.execute("UPDATE filaments SET slot=? WHERE slot=?", (tmp, item.slot))
                conn.execute("UPDATE filaments SET slot=? WHERE slot=?", (item.slot, src_slot))
                if target:
                    conn.execute("UPDATE filaments SET slot=? WHERE slot=?", (src_slot, tmp))
                if live["tag_uid"]:
                    _clear_tag(conn, live["tag_uid"], item.slot)
                    conn.execute("UPDATE filaments SET tag_uid=? WHERE slot=?", (live["tag_uid"], item.slot))

            else:  # create
                if target:
                    return JSONResponse({"error": "Der Slot ist bereits belegt."}, status_code=409)
                info = describe_live(live)
                if live["tag_uid"]:
                    _clear_tag(conn, live["tag_uid"], item.slot)
                conn.execute(
                    "INSERT INTO filaments(slot, material, color, color_name, manufacturer, weight, tag_uid) VALUES (?,?,?,?,?,?,?)",
                    (
                        item.slot,
                        info["material"],
                        live["color"] or "#888888",
                        info["color_name"],
                        info["manufacturer"],
                        live["tray_weight"] or 1000,
                        live["tag_uid"],
                    ),
                )
    except AmsUnavailable as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    return {"ok": True}


@app.post("/api/ams/map")
def ams_map(item: AmsMapIn):
    with db() as conn:
        try:
            saved = json.loads(get_setting(conn, "ams_map", "{}"))
        except ValueError:
            saved = {}
        saved[str(item.unit)] = item.name
        conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('ams_map', ?)", (json.dumps(saved),))
    return {"ok": True}


# App-Dateien (index.html, Manifest, Service Worker, Icons, Bibliotheken). Muss nach den API-Routen stehen.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
