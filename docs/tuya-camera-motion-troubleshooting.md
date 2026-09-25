# Tuya-IPC-Kamera: Bewegungsalarme zum Laufen bringen

Ausführliche Schritt-für-Schritt-Beschreibung, wie am 2026-09-25 die
Bewegungserkennung einer Tuya-IPC-Kamera (Outdoor Battery Solar PTZ Camera)
in sensir zum Laufen gebracht wurde. Alle anderen Tuya-Gerätearten (Sensoren,
Steckdosen, Schalter) liefen zu diesem Zeitpunkt bereits einwandfrei über
Pulsar — **nur** Kameras brauchten die hier beschriebenen Zusatzschritte.

Kurzfassung steht auch in [`sensir.md`, Abschnitt 4.2](../sensir.md#42-tuya--smartlife-cloud-tuyapy)
("Bekannte Falle" / "Pulsar liefert ohne Messaging Rule gar nichts" /
"IPC-Kamera-Bewegungsalarme") — dieses Dokument hier ist die ausführliche
Version mit jedem einzelnen Klick, für den Fall, dass es nochmal reproduziert
werden muss (neues Tuya-Projekt, andere Kamera, o. ä.).

## Symptom

- Alle anderen Sensoren (Shelly, Tuya-Steckdosen/-Sensoren) funktionierten.
- `GET /api/households/{id}/status` und die Web-Oberfläche zeigten für die
  Kamera nie ein Ereignis, obwohl die SmartLife-App Bewegungen korrekt
  meldete.
- `GET /v1.0/devices/{camera_id}/status` (das normale Status-Polling)
  antwortete mit `{"code": 2003, "msg": "function not support"}` — die
  Kamera liefert **keine** normalen Datenpunkte (DPs) wie ein Bewegungsmelder.

## Ursache 1: keine Messaging Rule für die Production-Umgebung

Der größte, am längsten unentdeckte Fehler: **ohne eine aktive Messaging
Rule kommt über Pulsar überhaupt keine Nachricht an — für kein Gerät, nicht
nur für die Kamera.** Das war lange nicht offensichtlich, weil die
Pulsar-Websocket-Verbindung selbst klaglos als "connected" gemeldet wurde
(`app/ingest/tuya.py` loggt `Tuya-Pulsar-Stream gestartet` /
`Websocket connected`) — der Verbindungsaufbau funktioniert unabhängig
davon, ob überhaupt etwas durchgelassen wird.

**Fix:**

1. [iot.tuya.com](https://iot.tuya.com) → links **Cloud** → im Untermenü
   **Cloud Project → Project Management** → das Projekt öffnen (hier:
   „SensIR").
2. Im Projekt oben auf den Tab **Message Service**.
3. Dort zwei Tabs: **Messaging Rules** und **Subscription Management**.
   Unter **Messaging Rules** gibt es zwei *unabhängige* Umgebungen:
   **Production Environment** und **Test Environment**. Unser Code nutzt
   `TuyaCloudPulsarTopic.PROD` (`app/ingest/tuya.py`) — die Regel muss also
   in **Production Environment** stehen, nicht (nur) im Test-Tab.
4. Stand vorher: „No messaging rules apply to the production environment,
   no messages will be pushed." — d. h. buchstäblich keine einzige
   Nachricht wurde je durchgelassen.
5. **Create Messaging Rules** klicken.
6. Bedingung: `BizCode (Message Type)` `In` → im Dropdown auswählen:
   - `devicePropertyMessage` (Device Property Message) — normale
     Gerätestatus-Änderungen
   - `deviceEventMessage` (Device Event Message)
   - `event_notify` (Event Notify)

   (Die Dropdown-Liste enthält weit mehr Einträge, z. B. `deviceOnline`,
   `deviceOffline`, `deviceTransfer`, `deviceFreeze`, `deviceUnFreeze`,
   `deviceActionResponseMessage` — für sensir reichen die drei oben.)
7. **Release Rule** klicken.
8. Nach dem Veröffentlichen erscheint links ein Schalter, der die Regel
   aktiviert/deaktiviert — Stand direkt nach dem Erstellen war er **aus**
   ("The rules for the production environment are disabled, so all
   messages will not be pushed."). Schalter anklicken, bis der Text
   wechselt zu: "The rules for the production environment are in effect,
   and messages are filtered out based on the rules. The corresponding
   message topic is: clientId/out/event."
9. Backend neu starten (`docker compose restart backend`), damit die
   Pulsar-Verbindung neu aufgebaut wird.

Ab hier kamen bereits die ersten echten Pulsar-Nachrichten für normale
Geräte an (siehe Ursache 3 für deren Format).

## Ursache 2: Kamera braucht eine eigene Service-Freischaltung

Selbst mit aktiver Messaging Rule kam für die Kamera nichts an, solange der
folgende Service nicht abonniert war:

1. Im Projekt → **Cloud → Cloud Services** (linkes Menü, **nicht** "Service
   API" — das ist ein anderer, hier nicht zielführender Menüpunkt).
2. In der zweiten Filterzeile ("Categories") auf **Video Services** klicken.
3. Dort erscheinen mehrere Services: *Video Cloud Storage*, *IoT Video Live
   Stream*, *Video Shop Inspection*, *Body Key Point Recognition* — **keiner
   davon ist der richtige**. Stattdessen im Suchfeld nach **„alarm"** suchen
   (Kategorie-Filter auf „All" zurücksetzen) → **„Camera Service"** taucht
   auf, Beschreibung: *"Provide real-time camera playback, PTZ control,
   arming and disarming, snapshots, alarm events and AI event reporting."*
4. **View Details** → **Free Trial** ($0.00) → Projekt „SensIR" als
   autorisiertes Projekt auswählen → abschließen.

Die im Service gelisteten REST-APIs (Camera Face Management, Snap Pictures,
PTZ Control, Session Management) haben mit Bewegungsalarmen direkt nichts zu
tun — der Effekt der Freischaltung ist, dass die Kamera überhaupt erst
Ereignisnachrichten in die (in Ursache 1 eingerichtete) Message Queue
einspeist.

## Ursache 3: anderes Nachrichtenformat + anderer DP-Code

Mit Regel + Service-Freischaltung kamen live folgende Pulsar-Nachrichten an
(mitgeschnitten über ein temporäres `logger.info(...)` in `_on_pulsar()`,
siehe Commits `dccedb8` / `f33c262`):

Normales Tuya-Gerät (Beispiel: SD-Karten-Status einer anderen Kamera):

```json
{
  "bizCode": "devicePropertyMessage",
  "bizData": {
    "devId": "bffa3e207713088390nflw",
    "dataId": "00065C52D82D65553A5512716A140A0C",
    "productId": "rogprwflblumx2co",
    "properties": [
      {"code": "sd_storge", "dpId": 109, "time": 1790360744191, "value": "124790784|86859776|37931008"}
    ]
  },
  "ts": 1790360744191
}
```

Bewegungsalarm der Test-Kamera:

```json
{
  "bizCode": "devicePropertyMessage",
  "bizData": {
    "devId": "bf2794ae02e44431dfinq1",
    "dataId": "00065C52DC74B28295BBF8616A1F0033",
    "productId": "xjuuufndz8qezkud",
    "properties": [
      {"code": "movement_detect_pic", "dpId": 115, "time": 1790360815972,
       "value": "eyJ2IjoiMy4wIiwiYnVja2V0IjoidHktZXUtc3RvcmFnZTMwLXBpYyIsImZpbGVzIjpbWyIvYzQ5YzFhLTY4MTIxNjEzLXliZ2RkMjJiYjI2NjhmYTM2NzBjL2RldGVjdC8xNzkwMzYwODE4LmpwZWciLCIxMWE5OTk4Njg4ODZhODFhIl1dfQ=="}
    ]
  },
  "ts": 1790360815972
}
```

Zwei Abweichungen von dem, was `app/ingest/tuya.py` ursprünglich erwartete
(altes `{"data": {"devId", "status": [{"code","value"}]}}`-Format aus der
`tuya-connector-python`-Beispieldoku):

1. **Umschlag heißt `bizData`, nicht `data`**, und die Liste heißt
   **`properties`, nicht `status`** (Elemente haben zusätzlich `dpId`/`time`,
   aber `code`/`value` bedeuten dasselbe).
2. Der Bewegungs-DP-Code ist **`movement_detect_pic`**, nicht `pir` o. ä.
   `value` ist ein **base64-kodierter JSON-Blob**
   (`{"v":"3.0","bucket":"ty-eu-storage30-pic","files":[["/<pfad>.jpeg","<hash>"]]}`)
   mit dem Pfad zum Schnappschuss in Tuyas Cloud-Speicher — für die reine
   Bewegungserkennung irrelevant, nur das **Eintreffen** des Codes zählt.

**Fix im Code** (`app/ingest/tuya.py`, `_on_pulsar()`): liest jetzt sowohl
`bizData` als auch `data`, sowohl `properties` als auch `status`, und
normalisiert beide auf dieselbe `{code, value}`-Form. `app/ingest/normalize.py`
bekam einen neuen `_TUYA_MOTION_EVENT`-Codepfad für `movement_detect_pic`
(ohne die sonst übliche `_truthy()`-Prüfung, weil der Wert kein An/Aus-Flag
ist).

## Verifikation

Testkamera testweise als Sensor angelegt
(`POST /api/sensors {"household_id":1,"kind":"tuya","external_id":"bf2794ae02e44431dfinq1",...}`),
zweimal vor der Kamera bewegt, `GET /api/households/1/events?limit=5` zeigte
beide Ereignisse korrekt als `kind: motion`. Test-Sensor danach wieder
gelöscht (gehörte nicht zum Haushalt).

## Kurzversion für neue Kameras

Falls eine weitere Tuya-IPC-Kamera angebunden werden soll und alles oben
Beschriebene im Projekt schon eingerichtet ist, sind **keine** weiteren
Schritte nötig — Messaging Rule und Camera-Service-Abo gelten fürs ganze
Projekt, nicht pro Gerät. Einfach normal als Sensor anlegen:

```bash
curl -X POST localhost:8000/api/sensors -H 'Content-Type: application/json' \
  -d '{"household_id":1,"name":"Neue Kamera","kind":"tuya","external_id":"<device-id>","config":{}}'
```
