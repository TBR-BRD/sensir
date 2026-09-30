# Haushalte verwalten: Onboarding, Sensoren bearbeiten, Haushalt löschen

Ausführliche Beschreibung der Web-Oberfläche zum Einrichten und Pflegen
von Haushalten (Stand 2026-09-30). Kurzfassung steht auch in
[`sensir.md`](../sensir.md) unter "Onboarding-Assistent für neue Haushalte"
und "Sensoren pflegen & Haushalt löschen" — dieses Dokument hier zeigt den
kompletten Klickpfad inkl. aller Sonderfälle.

Vorher ging das Anlegen eines Haushalts nur per `curl`/API
(`POST /api/households`), und Sensoren/Haushalte ließen sich nach dem
ersten Einrichten nur per API ändern oder löschen. Beides ist jetzt auch
über die normale Web-Oberfläche möglich.

## 1. Neuen Haushalt anlegen (Onboarding-Assistent)

Einstieg: Dashboard (`/`) → Button **"+ Neuen Haushalt anlegen"** →
`/onboarding`.

### Schritt 1: Haushalt

- Formular mit **Name** (Pflichtfeld, z. B. "Familie Meyer") und
  **Zeitzone** (vorausgefüllt mit `Europe/Berlin`, IANA-Zeitzonenname).
- "Weiter zu Sensoren →" legt den Haushalt sofort per `POST /onboarding`
  an (`Household`-Zeile existiert ab hier schon in der DB, auch wenn der
  Assistent danach abgebrochen wird — das ist gewollt, der Haushalt lässt
  sich jederzeit über die normale Haushaltsseite weiter konfigurieren).

### Schritt 2: Sensoren

- Zeigt alle **unzugeordneten** Tuya-/Shelly-Cloud-Geräte des gemeinsamen
  Cloud-Kontos (`TUYA_APP_ACCOUNT_UID` / `SHELLY_AUTH_KEY` aus `.env`,
  global für alle Haushalte) als Tabelle mit Checkbox + editierbarem
  Anzeigenamen. "Unzugeordnet" heißt: kein bestehender `Sensor` mit
  derselben `(kind, external_id)`-Kombination — ein Gerät, das schon einem
  anderen Haushalt zugeordnet wurde, taucht hier nicht mehr auf.
- Drei mögliche Zustände pro Quelle (Tuya/Shelly getrennt):
  - **Nicht konfiguriert**: `TUYA_ENABLED`/`SHELLY_ENABLED` steht nicht auf
    `true` in `.env` → Hinweistext statt leerer Tabelle.
  - **Fehler beim Abruf**: z. B. abgelaufenes Tuya-Trial-Abo oder
    Netzwerkproblem → die Fehlermeldung der Cloud-API wird direkt
    angezeigt (rot hinterlegt), die Seite stürzt nicht ab.
  - **Leer**: Quelle ist konfiguriert und erreichbar, aber es gibt gerade
    keine unzugeordneten Geräte → Hinweistext.
- **IR-Bridge** (Tasmota) lässt sich nicht automatisch erkennen (kein
  Cloud-API-Listing dafür) — eigenes Mini-Formular mit Name und
  MQTT-Topic, leer lassen zum Überspringen.
- "Weiter zu Kontakt →" legt alle angekreuzten Geräte als `Sensor`-Zeilen
  an (`POST /onboarding/{id}/sensors`) und geht zu Schritt 3. Link
  "Sensoren überspringen" geht direkt zu Schritt 3, ohne etwas anzulegen —
  Sensoren lassen sich jederzeit später nachtragen (siehe Abschnitt 3).
- **Race Condition**: Wird ein angekreuztes Gerät zwischen dem Laden der
  Seite und dem Absenden des Formulars anderswo zugeordnet (z. B. zwei
  Browser-Tabs gleichzeitig), fängt der Server das per
  `IntegrityError` ab und lädt dieselbe Seite neu — das Gerät fehlt dann
  einfach in der aktualisierten Liste, kein Fehler für den Nutzer sichtbar.

### Schritt 3: Kontakt

- Formular für einen Kontakt: Name, Telegram-Chat-ID, Telefon, Priorität
  (0 = wird zuerst benachrichtigt). Mehrere Kontakte lassen sich
  nacheinander anlegen, die Seite bleibt nach jedem Absenden auf Schritt 3.
- Jeder angelegte Kontakt mit `telegram_chat_id` bekommt sofort einen
  Button **"Testnachricht senden"** — schickt eine Telegram-Nachricht und
  protokolliert sie in `alert_log`, unabhängig von Zeitfenstern. Wichtig
  zum sofortigen Verifizieren der `chat_id` direkt beim Einrichten, statt
  erst beim ersten echten Alarm zu merken, dass sie falsch war.
- Hinweistext: das Standard-Zeitfenster 08:00–22:00 Uhr (alle Sensoren,
  `min_actions` per ML-Modell oder Fallback) gilt automatisch, ohne dass
  hier etwas eingestellt werden muss — siehe
  [`sensir.md`, Abschnitt 5](../sensir.md#5-zeitfenster-manuell-vs-ki) für
  Details zum Zeitfenster-System.
- "Fertig – zur Haushaltsseite →" beendet den Assistenten und führt zur
  normalen Haushaltsseite (`/households/{id}`), auf der sich ab jetzt
  alles (Sensoren, Kontakte, Zeitfenster, Pause/Löschen) weiter bearbeiten
  lässt.

Der Assistent ist rein additiv: er ersetzt keine der bestehenden Routen,
sondern führt nur beim ersten Einrichten linear durch dieselben Schritte,
die vorher einzeln über die Haushaltsseite bzw. `curl` gemacht werden
mussten.

## 2. Sensor bearbeiten (Name, Aktiv/Inaktiv)

Auf der Haushaltsseite, Abschnitt "Sensoren": jede Zeile der Tabelle hat
ein Textfeld für den Namen und eine Checkbox "aktiv". Änderung an einer
Zeile vornehmen, dann **"Speichern"** in derselben Zeile klicken
(`POST /households/{id}/sensors/{sensor_id}/edit`).

- Umbenennen ändert nur `Sensor.name` — der Dashboard/Event-Anzeigename.
  Hat keinen Einfluss auf die Zuordnung zum Cloud-Gerät (`external_id`
  bleibt unverändert).
- Die Checkbox steuert `Sensor.is_active`. Ein inaktiver Sensor liefert
  weiterhin technisch Ereignisse, wenn die Cloud-Quelle sendet (Ingest
  filtert nicht nach `is_active`) — die Auswirkung ist aktuell rein
  informativ in der UI; eine tatsächliche Exklusion aus der
  Zeitfenster-Zählung erreicht man stattdessen über `confirmation_only`
  im `Sensor.config`-JSON (siehe unten, aktuell nur per API setzbar).
- **Technischer Hinweis** (falls das Markup mal angepasst werden muss):
  die Eingabefelder liegen in normalen Tabellenzellen, das zugehörige
  `<form>`-Element aber *außerhalb* der Tabelle (ein `<form>` darf laut
  HTML-Spezifikation keine Tabellenzellen-Grenzen überspannen — Browser
  "foster parenten" es sonst aus der Tabelle heraus und die Formularwerte
  gehen verloren). Verknüpft wird über das `form="edit-sensor-<id>"`-
  Attribut an jedem Input/Button, das versteckte `<form id="edit-sensor-
  <id>">` steht direkt nach der Tabelle.

## 3. Sensor hinzufügen (zu einem bestehenden Haushalt)

Button **"+ Sensor hinzufügen"** unter der Sensor-Tabelle führt zu
`/households/{id}/sensors/add` — exakt derselbe Tuya-/Shelly-Geräte-Picker
wie in Schritt 2 des Onboarding-Assistenten (gleiche Discovery-Logik,
gleiches Markup als gemeinsames Jinja-Include `_sensor_picker.html`,
siehe Abschnitt 5). Ausgewählte Geräte werden direkt diesem Haushalt
zugeordnet, danach geht es zurück zur Haushaltsseite (nicht zu einem
weiteren Assistenten-Schritt).

## 4. Sensor entfernen

Button **"Entfernen"** pro Sensor-Zeile
(`POST /households/{id}/sensors/{sensor_id}/delete`), mit
Bestätigungsdialog (`confirm()` im Browser, beschreibt in der Meldung,
dass nur die Zuordnung entfernt wird).

- Löscht die `Sensor`-Zeile selbst. Bereits empfangene Ereignisse
  (`events`-Tabelle) bleiben erhalten — sie referenzieren den Sensor über
  `sensor_id`, das Löschen des Sensors löscht *nicht* rückwirkend die
  Historie (kein Cascade in diese Richtung). Für den CSV-Export
  (`GET /api/households/{id}/export`) bedeutet das: alte Ereignisse
  dieses Sensors tauchen weiterhin auf, nur ohne dass ihnen noch ein
  aktiver Sensor zugeordnet ist.
- Ein entfernter Sensor zählt danach in keinem Zeitfenster mehr mit — auch
  nicht in Fenstern, die vorher explizit auf ihn gescoped waren
  (`ObservationWindow.sensor_id`). Ein solches Fenster bleibt bestehen,
  liefert aber ab sofort keine Ereignisse mehr und wird dauerhaft negativ
  auswerten, bis es manuell gelöscht oder umgehängt wird (aktuell nur per
  direktem DB-Zugriff oder `DELETE`/`PATCH` über die API möglich, kein
  UI-Button für Zeitfenster-Löschung).
- Der Cloud-Anbieter (Tuya/SmartLife-App bzw. Shelly-App) bekommt davon
  nichts mit — das Gerät bleibt dort unverändert bestehen und weiter mit
  dem Cloud-Konto verbunden. Es taucht danach wieder als "unzugeordnet"
  im Geräte-Picker (Abschnitt 1/3) auf und kann erneut (auch einem
  anderen Haushalt) zugeordnet werden.

## 5. Haushalt löschen

Button **"Haushalt löschen"** oben auf der Haushaltsseite, mit
Bestätigungsdialog, der den Haushaltsnamen nennt
(`POST /households/{id}/delete`).

- Löscht den Haushalt **und per DB-Cascade alles, was an ihn hängt**:
  Sensoren, Ereignisse, Kontakte, Beobachtungsfenster, Aktivitäts-Checks,
  Alarm-Historie. Das entspricht `DELETE /api/households/{id}` (dieselbe
  Cascade, nur über die API statt die Web-Oberfläche ausgelöst).
- **Unwiderruflich** — es gibt keinen Papierkorb. Für den häufigeren Fall
  ("Person ist vorübergehend im Krankenhaus, Beobachtung soll ruhen, aber
  alle Daten/Konfiguration sollen erhalten bleiben") ist stattdessen der
  Button **"Überwachung pausieren"** gedacht (`is_active=false`) — siehe
  [`sensir.md`](../sensir.md) Abschnitt "`Household` — Multi-Haushalt &
  Pause". Löschen ist nur für den endgültigen Fall (Haushalt wird gar
  nicht mehr beobachtet, z. B. Testeinrichtung wieder entfernen).
- Nach dem Löschen: Redirect zum Dashboard (`/`). Ein direkter Aufruf der
  alten Haushaltsseite (`/households/{id}`, z. B. über ein altes
  Lesezeichen oder die Home-Assistant-Lovelace-Karte) liefert danach
  sauber `404 Not Found` — vorher (vor diesem Feature) endete das in einem
  `500 Internal Server Error`, weil `household_detail` nicht auf
  `household is None` prüfte; wurde beim Testen des Löschflows entdeckt
  und mitgefixt.

## 6. Technische Umsetzung (für spätere Änderungen)

- `app/web/routes.py`:
  - `_discover_unassigned_devices(db)` — gemeinsame Discovery-Logik
    (Tuya/Shelly `list_cloud_devices()`, gefiltert um bereits vergebene
    `(kind, external_id)`-Paare), genutzt von Onboarding-Schritt 2 *und*
    der "Sensor hinzufügen"-Seite.
  - `_add_sensors_from_form(db, household_id, form)` — gemeinsame
    Anlege-Logik aus einem `_sensor_picker.html`-Formular, ebenfalls von
    beiden Stellen genutzt. Committet selbst; `IntegrityError` wird
    unverändert weitergereicht, damit der jeweilige Aufrufer entscheidet,
    wohin bei einer Race Condition zurückgeleitet wird.
  - Neue Routen: `GET/POST /households/{id}/sensors/add`,
    `POST /households/{id}/sensors/{id}/edit`,
    `POST /households/{id}/sensors/{id}/delete`,
    `POST /households/{id}/delete`.
- `app/web/templates/_sensor_picker.html` — das Tuya-/Shelly-/IR-Bridge-
  Auswahl-Markup als Jinja-`{% include %}`, eingebunden sowohl in
  `onboarding_sensors.html` als auch in `household_add_sensors.html`
  (jeweils innerhalb eines umschließenden `<form>` mit unterschiedlichem
  `action`-Ziel).
- Getestet per FastAPI-`TestClient` gegen eine dateibasierte SQLite-DB
  (kompletter Durchlauf: Haushalt anlegen → Sensor hinzufügen → umbenennen
  → deaktivieren/reaktivieren → entfernen → Haushalt löschen → 404 auf der
  alten URL), zusätzlich zur bestehenden `pytest`-Suite (`backend/tests/`,
  12 Tests, unverändert grün).
