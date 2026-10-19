"""API-Tests. Ausführen:  pip install -r filament_manager/app/requirements.txt pytest httpx && pytest"""
import base64
import importlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

APP_DIR = Path(__file__).resolve().parents[1] / "filament_manager" / "app"


def make_legacy_db(path: Path):
    """Datenbank im Format der ersten Version (ohne Spalte color_name, mit altem Nebentabelle)."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE filament(id INTEGER PRIMARY KEY AUTOINCREMENT, material TEXT, color TEXT, weight INTEGER)")
    conn.execute("CREATE TABLE filaments(slot TEXT PRIMARY KEY, material TEXT, color TEXT, manufacturer TEXT, weight INTEGER)")
    rows = [("A0", "ABS", "#111111", "Sunlu", 917), ("A1", "PLA Basic", "#8e9089", "Bambu", 58), ("H0", "PETG", "#3aa6ff", "Bambu", 1000)]
    conn.executemany("INSERT INTO filaments VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return rows


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    def factory(password="", trusted=""):
        legacy = tmp_path / "legacy.db"
        rows = make_legacy_db(legacy)
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("FILAMENT_IMPORT_DB", str(legacy))
        monkeypatch.setenv("FMT_PASSWORD", password)
        monkeypatch.setenv("FMT_TRUSTED_IPS", trusted)
        monkeypatch.syspath_prepend(str(APP_DIR))
        sys.modules.pop("server", None)
        server = importlib.import_module("server")
        return TestClient(server.app), rows

    return factory


def test_legacy_database_is_imported_and_upgraded(make_client):
    c, rows = make_client()
    data = c.get("/api/slots").json()
    assert data["count"] == 3 and data["total_weight"] == sum(r[4] for r in rows)
    assert data["slots"]["A0"]["manufacturer"] == "Sunlu" and data["slots"]["A0"]["color_name"] is None  # Spalte nachgerüstet


def test_import_does_not_overwrite_existing_database(make_client, tmp_path):
    c, _ = make_client()
    c.delete("/api/slots/A0")
    sys.modules.pop("server")
    server = importlib.import_module("server")  # Neustart bei vorhandener DB und gesetztem Import-Pfad
    assert "A0" not in TestClient(server.app).get("/api/slots").json()["slots"]


def test_crud_and_color_name_is_kept(make_client):
    c, _ = make_client()
    body = {"slot": "H99", "material": "PETG", "color": "#ff0000", "color_name": "Rot", "manufacturer": "X", "weight": 800}
    assert c.post("/api/slots", json=body).json() == {"ok": True}
    c.post("/api/slots", json={**body, "color_name": "", "weight": 700})
    s = c.get("/api/slots").json()["slots"]["H99"]
    assert s["color_name"] == "Rot" and s["weight"] == 700
    assert c.delete("/api/slots/H99").json() == {"ok": True}
    assert "H99" not in c.get("/api/slots").json()["slots"]


def test_move_rules(make_client):
    c, _ = make_client()
    assert c.post("/api/move", json={"from_slot": "ZZ1", "to_slot": "ZZ2"}).status_code == 404
    r = c.post("/api/move", json={"from_slot": "A0", "to_slot": "A1"})
    assert r.status_code == 400 and r.json()["error"] == "Zielslot ist belegt"
    assert c.post("/api/move", json={"from_slot": "A0", "to_slot": "H99"}).json() == {"ok": True}
    slots = c.get("/api/slots").json()["slots"]
    assert "A0" not in slots and slots["H99"]["material"] == "ABS"


def test_validation(make_client):
    c, _ = make_client()
    assert c.post("/api/slots", json={"slot": "bad slot!", "material": "PLA"}).status_code == 422
    assert c.post("/api/slots", json={"slot": "H1", "material": "PLA", "weight": -5}).status_code == 422
    assert "error" in c.post("/api/slots", json={"slot": "H1"}).json()


def test_summary_for_home_assistant(make_client):
    c, _ = make_client()
    s = c.get("/api/summary?low=100").json()
    assert s["spool_count"] == 3 and s["total_weight_g"] == 917 + 58 + 1000
    assert s["low_stock_count"] == 1 and s["low_stock"][0]["slot"] == "A1"
    assert sum(s["by_material"].values()) == s["total_weight_g"]


def test_app_files_are_served(make_client):
    c, _ = make_client()
    page = c.get("/")
    assert page.status_code == 200 and 'id="slotForm"' in page.text and "{{" not in page.text
    for path, kind in [("/manifest.webmanifest", "json"), ("/sw.js", "javascript"), ("/icons/icon-192.png", "png"),
                       ("/vendor/qrcode.min.js", "javascript"), ("/vendor/jspdf.umd.min.js", "javascript"),
                       ("/vendor/html5-qrcode.min.js", "javascript")]:
        r = c.get(path)
        assert r.status_code == 200 and kind in r.headers["content-type"], path
    assert c.get("/api/slots").headers["cache-control"] == "no-store"


def test_password_protection(make_client):
    c, _ = make_client("geheim123")
    assert c.get("/api/health").status_code == 200
    assert c.get("/manifest.webmanifest").status_code == 200 and c.get("/sw.js").status_code == 200
    assert c.get("/api/slots").status_code == 401 and c.get("/").status_code == 401
    assert c.delete("/api/slots/A0").status_code == 401
    good = {"Authorization": "Basic " + base64.b64encode(b"fmt:geheim123").decode()}
    bad = {"Authorization": "Basic " + base64.b64encode(b"fmt:falsch").decode()}
    assert c.get("/api/slots", headers=good).status_code == 200
    assert c.get("/api/slots", headers=bad).status_code == 401


def test_trusted_ip_skips_password(make_client):
    c, _ = make_client("geheim123", trusted="testclient")  # TestClient meldet sich als Host "testclient"
    assert c.get("/api/slots").status_code == 200


def test_path_traversal_blocked(make_client):
    c, _ = make_client()
    for path in ["/../server.py", "/%2e%2e/server.py", "/..%2fserver.py"]:
        assert "FastAPI" not in c.get(path).text, path


def test_old_get_delete_route_is_gone(make_client):
    c, _ = make_client()
    assert c.get("/delete/A0").status_code == 404 and "A0" in c.get("/api/slots").json()["slots"]
