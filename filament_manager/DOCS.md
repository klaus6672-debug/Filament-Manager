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

## AMS Live-Abgleich (Bambu Lab)

Ist in Home Assistant die Bambu-Lab-Integration eingerichtet, zeigt Seite 1 unter den drei AMS den **Live-Abgleich**:
Pro AMS-Fach steht, was der Drucker meldet (Material, Farbe), daneben die Spule, die die App in `AMS-n-m` führt.

- Das Add-on liest die Sensoren der Fächer über Home Assistant. Es ist keine eigene Verbindung zum Drucker nötig.
- Über die Auswahl neben „AMS 1/2/3" legst du fest, welcher AMS aus Home Assistant zu welchem AMS der App gehört (wird gespeichert).
- **Kennung merken:** Passt die Spule, speichert die App die RFID-Kennung der Bambu-Spule.
- **Steckt jetzt hier:** Eine Spule mit bekannter Kennung wurde in ein anderes Fach gesteckt. Ein Klick verschiebt sie (ist das Fach belegt, tauschen beide Spulen die Plätze).
- **Weicht ab / Nicht in der App:** Die App schlägt passende Spulen aus Regal oder Lager vor (gleiches Material, ähnliche Farbe) oder legt die Spule aus den Live-Daten neu an.
- **Farbname aus dem Code:** Der Drucker liefert den Hex-Code der Spule. Daraus erzeugt die App den Farbnamen: zuerst so, wie du dieselbe Farbe (gleicher Hersteller, gleiche Sorte) schon in deinen Spulen benennst, sonst nach den offiziellen Bambu-Tabellen (z. B. `#5B6579` = Blue Gray) für PLA Basic und PLA Matte. Ist der Code noch nicht in der Tabelle, steht „Farbname fehlt" und die Spule wird **manuell angelegt** (Formular wird vorausgefüllt). Weicht der Hex-Code einer App-Spule sichtbar vom AMS ab (auch bei Spulen mit gemerkter Kennung), setzt „Farbe übernehmen" den Wert aus dem AMS.
- **Ausgebaute Spulen** legt die App automatisch ins **Lager D** (12 Plätze für angefangene Spulen), bei vollem Lager D ins Hauptlager.
- **Vorschläge übernehmen** führt alle eindeutigen Vorschläge nach Rückfrage aus. Es wird nie etwas ohne deinen Klick geändert.
- Spulen ohne RFID (Fremdhersteller) werden über Material und Farbe verglichen. Das Restgewicht übernimmt die App nicht, die P1S-AMS melden es nicht.

## Sensoren in Home Assistant

Die Schnittstelle `http://<Host>:8099/api/summary` liefert Spulenzahl, Gesamtgewicht und knappe Spulen
(`?low=150` = Grenze in Gramm). Vorlagen: Ordner `homeassistant` im Repository.

## Daten

Alles liegt im Add-on-Speicher (`/data/filament.db`) und ist in den Home-Assistant-Sicherungen enthalten.
