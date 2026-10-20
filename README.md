# Filament Manager

Filamentlager mit Regal-Ansicht (bis zu vier frei einstellbare Lager A–D mit 6, 12 oder 18 Fächern, bis zu 3× AMS, Hauptlager mit 120 Plätzen), QR-Labels als PDF, Barcode-/QR-Scan mit der Kamera,
Statistik und CSV-Import/-Export. Läuft als **installierbare App auf Handy und PC** (PWA), als **Docker-Container** und
als **Home-Assistant-Add-on**. Die Daten liegen in einer SQLite-Datei (`filament.db`) auf deinem eigenen Rechner.

**Aufbau anpassen:** Unter „⚙ Lager einrichten" lassen sich die Lager A–D einzeln ein- und ausblenden, auf 6, 12 oder 18 Fächer
stellen und umbenennen (z. B. „Regal links", „Schrank", „Angefangene Spulen"). Dazu die Anzahl der angezeigten AMS (0 bis 3).
Belegte Fächer werden nie versteckt: Die App verweigert das Ausblenden oder Verkleinern, solange dort Spulen liegen.

```
filament-manager/
├── filament_manager/        Anwendung + Home-Assistant-Add-on (gleicher Code für beides)
│   ├── app/                 Server (FastAPI) und Oberfläche (static/)
│   ├── config.yaml          Add-on-Beschreibung
│   ├── Dockerfile, run.sh
│   └── DOCS.md              Anleitung, die Home Assistant im Add-on anzeigt
├── repository.yaml          macht das Repository zu einem Add-on-Repository
├── docker-compose.yml       Server + HTTPS (Caddy) für den Mini-PC
├── homeassistant/           Sensoren, Benachrichtigung und Dashboard-Karten für Home Assistant
└── tests/                   automatische Tests
```

## 1. Schnellstart: Mini-PC mit Docker (Windows 11)

1. Ordner auf den Mini-PC kopieren, z. B. nach `C:\filament-manager`.
2. `.env.example` nach `.env` kopieren und anpassen:
   `FMT_PASSWORD` (eigenes Passwort), `FMT_HOST` (IP des Mini-PCs, feste IP im Router vergeben),
   `FILAMENT_DATA` (Ordner für die Datenbank, z. B. `D:/Filament`; mit Schrägstrichen und exakt dem echten Ordnernamen, sonst legt Docker einen neuen leeren Ordner an).
3. **Vorhandene Daten übernehmen:** deine bisherige `filament.db` in den Ordner `FILAMENT_DATA` kopieren.
   Das Format ist unverändert, alle Spulen sind danach sofort da. (Ohne Datei startet die App leer.)
4. In PowerShell im Ordner: `docker compose up -d --build`
5. Falls das Handy keine Verbindung bekommt: Ports in der Windows-Firewall freigeben. Die PowerShell muss dafür
   **als Administrator** laufen (Startmenü → PowerShell → Rechtsklick → Als Administrator ausführen):
   `New-NetFirewallRule -DisplayName "Filament Manager" -Direction Inbound -Protocol TCP -LocalPort 8080,8443 -Action Allow -Profile Any`
6. **Zertifikat fürs Handy** (einmalig, nötig für Kamera-Scan und App-Installation, beides verlangt HTTPS):
   `docker cp filament-https:/data/caddy/pki/authorities/local/root.crt .\filament-root.crt`
   Die Datei aufs Handy kopieren und als CA-Zertifikat installieren
   (Android: Einstellungen → Sicherheit → Verschlüsselung & Anmeldedaten → Zertifikat installieren → CA-Zertifikat;
   iPhone: Datei öffnen → Profil installieren → Einstellungen → Allgemein → Info → Zertifikatsvertrauenseinstellungen).
   Am PC die Datei per Doppelklick in „Vertrauenswürdige Stammzertifizierungsstellen" (Lokaler Computer) importieren.
7. Im Browser `https://<FMT_HOST>:8443` öffnen (in die **Adressleiste** tippen, nicht ins Google-Suchfeld, sonst wird gesucht statt verbunden) und Benutzer `fmt` + Passwort eingeben.

Die Container starten nach Updates und Neustarts von selbst wieder (`restart: unless-stopped`).

## 2. Als App installieren (Handy und PC)

Die Oberfläche ist eine PWA. Nach dem Öffnen von `https://<FMT_HOST>:8443`:

| Gerät | Installation |
|---|---|
| Android (Chrome) | Menü ⋮ → „App installieren" |
| iPhone/iPad (Safari) | Teilen → „Zum Home-Bildschirm" |
| Windows/Mac/Linux (Chrome, Edge) | Installations-Symbol in der Adressleiste |

Die App bekommt ein eigenes Icon und ein eigenes Fenster. **Ohne Verbindung** zum Server startet sie trotzdem und zeigt den
zuletzt geladenen Bestand (oben „Offline – Stand …"). Ändern, Verschieben und Löschen geht nur mit Verbindung.

## 3. Home Assistant

**Home Assistant als Docker-Container (Test-Installation, „Container"-Variante):** Dort gibt es keine Add-ons. Nimm die
Anbindung über Sensoren: `homeassistant/filament_sensors.yaml` (Anleitung am Dateianfang) liefert
*Spulen*, *Gesamtgewicht*, *knappe Spulen* (mit Liste), einen Sensor „Filament nachkaufen" und eine Benachrichtigung.
`homeassistant/dashboard_card.yaml` zeigt Kennzahlen oder die komplette App im Dashboard.

**Home Assistant OS / Supervised (mit Add-on-Store):** Dieses Repository ist ein Add-on-Repository.

1. Repository auf GitHub hochladen (Adresse: `https://github.com/klaus6672-debug/Filament-Manager`; bei anderem Benutzernamen in `repository.yaml` und `filament_manager/config.yaml` anpassen).
2. Home Assistant → Einstellungen → Add-ons → Add-on-Store → ⋮ → Repositories → GitHub-Adresse eintragen.
3. „Filament Manager" installieren und starten. Es erscheint ein Eintrag „Filament" in der Seitenleiste (Ingress,
   Anmeldung übernimmt Home Assistant).
4. Bestehende Datenbank übernehmen: Datei als `filament_import.db` in den Ordner `/share` legen (z. B. mit dem
   Samba-Add-on) und das Add-on einmal starten. Alternativ: im alten System „CSV Export", im neuen „CSV Import".
5. **AMS-Live-Abgleich (Bambu Lab):** läuft im Add-on automatisch, wenn die Bambu-Integration in Home Assistant eingerichtet ist (siehe `filament_manager/DOCS.md`). Bei Docker ohne Add-on `HA_URL` und `HA_TOKEN` (Langzeit-Zugriffstoken) in `.env` setzen.
6. Für den **Kamera-Scan und die App-Installation am Handy** den Port 8099 im Add-on freigeben und per HTTPS aufrufen,
   denn innerhalb der Home-Assistant-Seitenleiste ist die Kamera nicht verfügbar.

## 4. Was gegenüber dem alten Projekt anders ist

- **Eigenständige App statt Server-Seite:** Die Seite wird nicht mehr vom Server mit Daten „gebacken", sondern lädt sie über
  eine JSON-API. Dadurch funktionieren Installation, Offline-Start und Home-Assistant-Ingress.
- **Bibliotheken liegen lokal** (`static/vendor`) statt auf drei fremden CDN-Servern. Die App läuft komplett ohne Internet.
- **Löschen per `DELETE`** statt über einen Link (`GET /delete/…`). Vorher hätte schon das Vorladen eines Links oder ein
  Klick auf eine fremde Seite eine Spule löschen können.
- **Optionaler Passwortschutz** (`FMT_PASSWORD`), vorher war alles ohne Anmeldung offen.
- **Fehler behoben:** In `suggestColorName` stand ein verirrtes `torq`, das bei jedem Aufruf einen Fehler auslöste;
  die doppelt vorhandene Funktion `applyDefaultWeightForSlot` ist zusammengeführt.
- **CSV enthält jetzt den Farbnamen** (Spalte `color_name`). Bisher gingen die Namen bei Export und Import verloren.
  Alte CSV-Dateien ohne diese Spalte lassen sich weiterhin importieren.
- **Nicht mehr nötig und entfernt:** `venv`, `filament_backup.tar.gz`, `cert.pem`/`key.pem` (HTTPS übernimmt Caddy),
  nicht erreichbare Vorlagen `filament.html` und `label.html`.

> **Hinweis zu `key.pem`:** Der private Schlüssel lag in der hochgeladenen ZIP-Datei. Wenn diese Datei je irgendwo
> veröffentlicht wurde, nicht weiterverwenden. Die `.gitignore` hier verhindert, dass Schlüssel und `*.db` im Repository landen.

## 5. Sicherung

Es genügt, `filament.db` zu kopieren (Ordner `FILAMENT_DATA`), bei laufendem Betrieb am besten kurz vorher den Container
stoppen. Das Add-on wird von den Home-Assistant-Sicherungen mit erfasst. Zusätzlich gibt es den CSV-Export in der App.

## 6. Entwicklung und Tests

```
pip install -r requirements-dev.txt
pytest                                   # API-Tests
cd filament_manager/app && DATA_DIR=./data python server.py   # Entwicklungsserver auf http://localhost:8080
```

Bei Änderungen an der Oberfläche in `static/sw.js` die Zahl `VERSION` erhöhen, damit installierte Apps den Cache erneuern.

## Grenzen

- Mehrere Personen gleichzeitig: Pro Slot gilt die zuletzt gespeicherte Änderung, Konflikte werden nicht gemeldet.
- Offline wird nur gelesen, nicht geschrieben.
