"""Tests für den AMS-Live-Abgleich mit simulierten Home-Assistant-Sensoren (Namen/Attribute wie in der echten Installation)."""
import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

APP_DIR = Path(__file__).resolve().parents[1] / "filament_manager" / "app"


def sensor(entity, device, pos, name, typ, color, tag, empty=False, weight=1000):
    return {
        "entity_id": entity,
        "state": name,
        "attributes": {
            "slot": pos, "empty": empty, "name": name, "type": typ, "color": color + "FF",
            "tag_uid": tag, "tray_weight": weight, "friendly_name": f"{device} Slot {pos}",
        },
    }


Z = "0000000000000000"
# Entity-IDs sind (wie bei dir) NICHT zu den Gerätenamen passend: gruppiert wird über den Anzeigenamen.
STATES = [
    sensor("sensor.p1s_ams_links_1_slot_1", "P1S-AMS links 1", 1, "Bambu PLA Basic", "PLA", "#5B6579", "ECFA670100000100"),
    sensor("sensor.p1s_ams_links_1_slot_2", "P1S-AMS links 1", 2, "Bambu PLA Basic", "PLA", "#5E43B7", "A5FF1B1000000100"),
    sensor("sensor.p1s_ams_links_1_slot_3", "P1S-AMS links 1", 3, "Kexcelled PLA SalbeiGrün", "PLA", "#FFF144", Z, weight=0),
    sensor("sensor.p1s_ams_links_1_slot_4", "P1S-AMS links 1", 4, "Bambu PLA Matte", "PLA", "#000000", "1C9B656900000100"),
    sensor("sensor.p1s_ams_rechts_slot_1", "P1S-AMS links 2", 1, "Bambu PLA Basic", "PLA", "#F4EE2A", "B54B410300000100"),
    sensor("sensor.p1s_01p00c580401181_ams_1_slot_1", "P1S AMS rechts", 1, "Empty", "Empty", "#000000", "", empty=True),
    sensor("sensor.p1s_01p00c580401181_ams_1_slot_2", "P1S AMS rechts", 2, "Bambu PLA Matte", "PLA", "#FFFFFF", "327E616D00000100"),
    {"entity_id": "sensor.other", "state": "1", "attributes": {"friendly_name": "Etwas anderes"}},
]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FMT_PASSWORD", "")
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.syspath_prepend(str(APP_DIR))
    sys.modules.pop("server", None)
    server = importlib.import_module("server")
    monkeypatch.setattr(server, "fetch_ha_states", lambda: STATES)
    return TestClient(server.app)


def put(c, slot, material, color, manufacturer="Bambu", color_name="", weight=800):
    r = c.post("/api/slots", json={"slot": slot, "material": material, "color": color,
                                   "manufacturer": manufacturer, "color_name": color_name, "weight": weight})
    assert r.status_code == 200


def tray(c, slot):
    data = c.get("/api/ams").json()
    return next(t for u in data["units"] for t in u["trays"] if t["slot"] == slot)


def test_units_are_found_by_display_name_and_ordered(client):
    data = client.get("/api/ams").json()
    assert data["available"]
    assert [u["ha_name"] for u in data["units"]] == ["P1S-AMS links 1", "P1S-AMS links 2", "P1S AMS rechts"]
    assert tray(client, "AMS-3-2")["live"]["name"] == "Bambu PLA Matte"   # Gerät "P1S AMS rechts"
    assert tray(client, "AMS-2-1")["live"]["color"] == "#f4ee2a"          # Entity heißt "..._rechts_...", gehört aber zu links 2
    assert tray(client, "AMS-3-1")["status"] == "empty"
    assert tray(client, "AMS-1-3")["live"]["tag_uid"] is None             # Fremdspule: nur Nullen = keine Kennung


def test_matching_spool_offers_to_learn_tag_then_is_ok(client):
    put(client, "AMS-1-1", "PLA Basic", "#5b6579")
    t = tray(client, "AMS-1-1")
    assert t["status"] == "learn" and t["action"]["kind"] == "learn"
    assert client.post("/api/ams/apply", json={"slot": "AMS-1-1", "kind": "learn"}).status_code == 200
    assert tray(client, "AMS-1-1")["status"] == "ok"
    assert client.get("/api/slots").json()["slots"]["AMS-1-1"]["tag_uid"] == "ECFA670100000100"


def test_third_party_spool_without_tag_is_just_ok(client):
    put(client, "AMS-1-3", "PLA", "#fff040", manufacturer="Kexcelled")
    assert tray(client, "AMS-1-3")["status"] == "ok" and tray(client, "AMS-1-3")["action"] is None


def test_mismatch_offers_candidates_and_swaps_places(client):
    # Spule steckt physisch in AMS 1 Fach 2, die App führt sie noch im Regal (A3); Fach 2 führt eine andere Spule
    put(client, "A3", "PLA Basic", "#5e43b7", color_name="Violett")
    put(client, "A4", "PETG", "#5e43b7", color_name="falsches Material")      # kein Kandidat (anderes Material)
    put(client, "AMS-1-2", "PLA Basic", "#123456", color_name="Falsch")
    t = tray(client, "AMS-1-2")
    assert t["status"] == "mismatch" and [c["slot"] for c in t["candidates"]] == ["A3"]
    assert client.post("/api/ams/apply", json={"slot": "AMS-1-2", "kind": "move", "from_slot": "A3"}).status_code == 200
    s = client.get("/api/slots").json()["slots"]
    assert s["AMS-1-2"]["color_name"] == "Violett" and s["AMS-1-2"]["tag_uid"] == "A5FF1B1000000100"
    assert s["A3"]["color_name"] == "Falsch"                                    # getauscht, nichts verloren
    assert tray(client, "AMS-1-2")["status"] == "ok"


def test_known_tag_in_new_place_suggests_move(client):
    put(client, "A5", "PLA Matte", "#000000")
    client.post("/api/slots", json={"slot": "A5", "material": "PLA Matte", "color": "#000000", "tag_uid": "1C9B656900000100"})
    t = tray(client, "AMS-1-4")
    assert t["status"] == "moved" and t["action"] == {"kind": "move", "from_slot": "A5", "label": "A5 → hierher"}
    assert client.post("/api/ams/apply", json={"slot": "AMS-1-4", "kind": "move", "from_slot": "A5"}).status_code == 200
    s = client.get("/api/slots").json()["slots"]
    assert "A5" not in s and s["AMS-1-4"]["tag_uid"] == "1C9B656900000100"


def test_create_from_live_data(client):
    assert client.post("/api/ams/apply", json={"slot": "AMS-1-3", "kind": "create"}).status_code == 200
    s = client.get("/api/slots").json()["slots"]["AMS-1-3"]
    assert (s["manufacturer"], s["material"], s["color_name"], s["color"]) == ("Kexcelled", "PLA", "SalbeiGrün", "#fff144")
    assert client.post("/api/ams/apply", json={"slot": "AMS-1-3", "kind": "create"}).status_code == 409   # nicht überschreiben
    assert client.post("/api/ams/apply", json={"slot": "AMS-3-1", "kind": "create"}).status_code == 409   # Fach leer
    b = client.post("/api/ams/apply", json={"slot": "AMS-3-2", "kind": "create"})
    assert b.status_code == 200 and client.get("/api/slots").json()["slots"]["AMS-3-2"]["material"] == "PLA Matte"


def test_tag_survives_editing_the_spool(client):
    put(client, "AMS-1-1", "PLA Basic", "#5b6579")
    client.post("/api/ams/apply", json={"slot": "AMS-1-1", "kind": "learn"})
    put(client, "AMS-1-1", "PLA Basic", "#5b6579", weight=500)
    assert client.get("/api/slots").json()["slots"]["AMS-1-1"]["tag_uid"] == "ECFA670100000100"


def test_mapping_can_be_changed(client):
    assert client.post("/api/ams/map", json={"unit": 1, "name": "P1S AMS rechts"}).status_code == 200
    assert tray(client, "AMS-1-2")["live"]["name"] == "Bambu PLA Matte"


def test_home_assistant_unreachable(client, monkeypatch):
    server = sys.modules["server"]
    def boom():
        raise server.AmsUnavailable("weg")
    monkeypatch.setattr(server, "fetch_ha_states", boom)
    assert client.get("/api/ams").json() == {"available": False, "reason": "weg", "units": []}
    assert client.post("/api/ams/apply", json={"slot": "AMS-1-1", "kind": "learn"}).status_code == 503


def test_similar_but_different_spools_are_not_reported_as_matching(client):
    # Werte aus der echten Anlage: Farbe/Sorte weichen sichtbar ab, früher stand hier "passt"
    put(client, "AMS-1-4", "PLA Basic", "#d2bc8a", color_name="Beige")           # live ist Matte-Schwarz: Farbe und Sorte weichen ab
    assert tray(client, "AMS-1-4")["status"] == "mismatch"
    put(client, "AMS-3-2", "PLA Basic", "#f6f7f1", color_name="Jade White")      # gleiche Farbe wie live (#FFFFFF), aber Basic statt Matte
    assert tray(client, "AMS-3-2")["status"] == "mismatch"
    put(client, "AMS-3-2", "PLA Matte", "#f6f7f1", color_name="Jade White")      # gleiche Sorte -> passt
    assert tray(client, "AMS-3-2")["status"] in ("ok", "learn")
    put(client, "AMS-3-2", "PLA", "#f6f7f1")                                     # keine Sorte angegeben -> wird nicht bemängelt
    assert tray(client, "AMS-3-2")["status"] in ("ok", "learn")


def test_color_distance_is_perceptual():
    sys.path.insert(0, str(APP_DIR))
    server = importlib.import_module("server")
    d = server.color_distance
    assert d("#8f908a", "#c8a27d") > 20      # grau vs. Latte Brown
    assert d("#0b2989", "#0057b8") > 15      # Marineblau vs. Blau
    assert d("#d3b8a7", "#d2bc8a") > 15      # Rosabeige vs. Gelbbeige
    assert d("#ffffff", "#f6f7f1") < 10      # praktisch gleiches Weiß
    assert d("#c12d1f", "#c12e1f") < 1


def test_basic_and_matte_are_never_treated_as_identical(client):
    # Spule mit bekannter Kennung (Bambu PLA Matte, schwarz), in der App aber als "PLA Basic" geführt
    client.post("/api/ams/apply", json={"slot": "AMS-1-4", "kind": "create"})
    assert tray(client, "AMS-1-4")["status"] == "ok"
    put(client, "AMS-1-4", "PLA Basic", "#000000")            # Kennung bleibt erhalten
    t = tray(client, "AMS-1-4")
    assert t["status"] == "fix" and t["action"] == {"kind": "fixmat", "label": "Sorte → PLA Matte"}
    assert client.post("/api/ams/apply", json={"slot": "AMS-1-4", "kind": "fixmat"}).status_code == 200
    assert client.get("/api/slots").json()["slots"]["AMS-1-4"]["material"] == "PLA Matte"
    assert tray(client, "AMS-1-4")["status"] == "ok"
    # Ohne Kennung: gleiche Farbe, aber Basic ↔ Matte -> weicht ab, auch als Kandidat nie vorgeschlagen
    put(client, "A9", "PLA Basic", "#000000")
    put(client, "AMS-3-2", "PLA Basic", "#ffffff")
    t = tray(client, "AMS-3-2")
    assert t["status"] == "mismatch" and all(c["slot"] != "A9" for c in t["candidates"])


def test_old_spool_goes_to_main_storage_when_slot_is_replaced_or_emptied(client):
    # Fach 3-1 ist laut AMS leer, die App führt noch eine Spule -> Vorschlag "ins Lager", aber nicht in der Sammelübernahme
    put(client, "AMS-3-1", "PLA", "#dcf478", manufacturer="Kexcelled")
    put(client, "H0", "PETG", "#111111")
    t = tray(client, "AMS-3-1")
    assert t["status"] == "gone" and t["action"] == {"kind": "park", "label": "Spule ins Lager", "bulk": False}
    r = client.post("/api/ams/apply", json={"slot": "AMS-3-1", "kind": "park"}).json()
    assert r["parked_to"] == "D0"                                           # angefangene Spulen kommen in Lager D
    s = client.get("/api/slots").json()["slots"]
    assert "AMS-3-1" not in s and s["D0"]["manufacturer"] == "Kexcelled" and "H0" in s
    assert client.post("/api/ams/apply", json={"slot": "AMS-3-1", "kind": "park"}).status_code == 409

    # Neue, unbekannte Spule im belegten Fach: "Neu anlegen" nur mit Auslagerung der alten
    put(client, "AMS-1-3", "PLA", "#123456", manufacturer="Alt", color_name="Alte Spule")
    assert client.post("/api/ams/apply", json={"slot": "AMS-1-3", "kind": "create"}).status_code == 409
    r = client.post("/api/ams/apply", json={"slot": "AMS-1-3", "kind": "create", "park_old": True}).json()
    s = client.get("/api/slots").json()["slots"]
    assert r["parked_to"] == "D1" and s["D1"]["color_name"] == "Alte Spule" and s["AMS-1-3"]["color_name"] == "SalbeiGrün"


def test_falls_back_to_main_storage_when_lager_d_is_full(client):
    for i in range(12):
        put(client, f"D{i}", "PLA", "#222222")
    put(client, "H0", "PLA", "#222222")
    put(client, "AMS-3-1", "PLA", "#dcf478")
    assert client.post("/api/ams/apply", json={"slot": "AMS-3-1", "kind": "park"}).json()["parked_to"] == "H1"
