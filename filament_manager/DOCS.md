# Filament Manager

Filamentlager mit Regal-Ansicht, QR-Labels, Barcode-Scan und Statistik.

## Benutzung

Nach dem Start erscheint **Filament** in der Seitenleiste. Der Zugriff darüber ist durch Home Assistant geschützt.

## Direkter Zugriff und Handy-App

Für den **Kamera-Scan** und die **Installation als App** braucht der Browser eine HTTPS-Adresse. Innerhalb der
Home-Assistant-Seitenleiste ist die Kamera nicht verfügbar.

1. Port `8099` im Reiter „Netzwerk" des Add-ons freigeben.
2. **Passwort setzen** (Reiter „Konfiguration"), bevor der Port freigegeben wird.
3. Per HTTPS aufrufen (z. B. über einen Reverse-Proxy wie Caddy oder Nginx Proxy Manager) und die Seite als App installieren.

## Bestehende Datenbank übernehmen

`filament.db` des alten Systems als `filament_import.db` in den Ordner `/share` legen und das Add-on starten.
Das passiert nur beim allerersten Start, vorhandene Daten werden nie überschrieben. Alternativ: CSV Export/Import in der App.

## Sensoren in Home Assistant

Die Schnittstelle `http://<Host>:8099/api/summary` liefert Spulenzahl, Gesamtgewicht und knappe Spulen
(`?low=150` = Grenze in Gramm). Vorlagen: Ordner `homeassistant` im Repository.

## Daten

Alles liegt im Add-on-Speicher (`/data/filament.db`) und ist in den Home-Assistant-Sicherungen enthalten.
