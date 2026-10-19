"""Filament Manager – Server (FastAPI + SQLite).

Liefert die App (static/) aus und stellt eine kleine JSON-API bereit:

    GET    /api/slots          alle belegten Slots
    POST   /api/slots          Slot anlegen/überschreiben
    POST   /api/move           Spule in leeren Slot verschieben
    DELETE /api/slots/{slot}   Slot leeren
    GET    /api/summary        Kennzahlen für Home Assistant (?low=100 = Grenze für "knapp" in Gramm)
    GET    /api/health         Lebenszeichen (immer ohne Passwort erreichbar)

Konfiguration über Umgebungsvariablen:
    DATA_DIR            Ordner mit filament.db            (Standard: ./data)
    FILAMENT_IMPORT_DB  Pfad zu einer vorhandenen filament.db, die beim allerersten Start übernommen wird
    FMT_PASSWORD        Optional: schützt App und API per Benutzername/Passwort (HTTP Basic)
    FMT_USER            Benutzername (Standard: fmt)
    FMT_TRUSTED_IPS     Kommagetrennte Absender-IPs, die ohne Passwort durchgelassen werden
                        (Home-Assistant-Add-on: der Ingress-Zugang des Supervisors, 172.30.32.2)
"""
import base64
import hmac
import os
import shutil
import sqlite3
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


class MoveIn(BaseModel):
    from_slot: str = Field(min_length=1, max_length=32)
    to_slot: str = Field(min_length=1, max_length=32)


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
        existing = conn.execute("SELECT color_name FROM filaments WHERE slot=?", (item.slot,)).fetchone()
        # Ein vorhandener Farbname bleibt erhalten, wenn keiner mitgeschickt wird
        color_name = item.color_name.strip() or (existing["color_name"] if existing and existing["color_name"] else "")
        conn.execute(
            """
            INSERT OR REPLACE INTO filaments(slot, material, color, color_name, manufacturer, weight)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (item.slot, item.material, item.color, color_name, item.manufacturer, item.weight),
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


# App-Dateien (index.html, Manifest, Service Worker, Icons, Bibliotheken). Muss nach den API-Routen stehen.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
