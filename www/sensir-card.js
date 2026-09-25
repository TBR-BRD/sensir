const SENSIR_CARD_VERSION = "1.3.0";

/**
 * Custom Lovelace-Karte für sensir (https://github.com/TBR-BRD/sensir).
 *
 * Fragt die sensir-REST-API direkt aus dem Browser ab (kein Umweg über
 * Home-Assistant-Entities/eine eigene Integration nötig) - deshalb muss
 * der sensir-Server CORS für den Origin dieser HA-Instanz erlauben
 * (CORS_ALLOW_ORIGINS in .env, siehe sensir.md Abschnitt "Home Assistant
 * Lovelace-Karte").
 *
 * Konfiguration (Lovelace YAML):
 *   type: custom:sensir-card
 *   base_url: http://192.168.1.100:8000   # erforderlich
 *   title: SensIR                          # optional
 *   refresh_seconds: 60                    # optional
 *   event_limit: 10                        # optional, Standard 10
 *   household_ids: [1, 2]                  # optional, sonst alle Haushalte
 *
 * Jede Haushalts-Kachel startet zugeklappt (nur Status). Ein kleiner Pfeil
 * klappt die letzten `event_limit` Ereignisse auf/zu, ohne die Seite zu
 * verlassen. Klick auf den Haushaltsnamen öffnet die vollständige sensir-
 * Seite (Zeitfenster bearbeiten, Testnachricht senden, Sensor anlegen, …)
 * in einem neuen Tab.
 */
class SensirCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._timer = null;
    this._renderGeneration = 0;
    this._expandedIds = new Set();
  }

  static getStubConfig() {
    return { base_url: "http://192.168.1.100:8000", title: "SensIR" };
  }

  getCardSize() {
    return 4;
  }

  setConfig(config) {
    if (!config.base_url) {
      throw new Error("sensir-card: base_url fehlt (z. B. http://192.168.1.100:8000)");
    }
    this._config = {
      title: "SensIR",
      refresh_seconds: 60,
      event_limit: 10,
      household_ids: null,
      ...config,
    };
    this._config.base_url = this._config.base_url.replace(/\/+$/, "");
    this._scheduleFetch();
  }

  // Die Karte liest keinen HA-Entity-State - Pflicht-Setter für Lovelace,
  // bleibt bewusst leer.
  set hass(_hass) {}

  connectedCallback() {
    this._scheduleFetch();
  }

  disconnectedCallback() {
    this._renderGeneration++;
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
  }

  _scheduleFetch() {
    if (!this.isConnected || !this._config.base_url) return;
    this._fetchAndRender();
    if (this._timer) clearInterval(this._timer);
    const seconds = Number(this._config.refresh_seconds) || 60;
    this._timer = setInterval(() => this._fetchAndRender(), seconds * 1000);
  }

  async _fetchAndRender() {
    const generation = ++this._renderGeneration;
    const base = this._config.base_url;
    const limit = Number(this._config.event_limit) || 10;
    try {
      const hhResp = await fetch(`${base}/api/households`);
      if (!hhResp.ok) throw new Error(`Haushalte: HTTP ${hhResp.status}`);
      let households = await hhResp.json();
      if (Array.isArray(this._config.household_ids) && this._config.household_ids.length) {
        households = households.filter((h) => this._config.household_ids.includes(h.id));
      }

      const statuses = await Promise.all(
        households.map(async (h) => {
          const [statusResp, eventsResp] = await Promise.allSettled([
            fetch(`${base}/api/households/${h.id}/status`),
            fetch(`${base}/api/households/${h.id}/events?limit=${limit}`),
          ]);
          const status =
            statusResp.status === "fulfilled" && statusResp.value.ok
              ? await statusResp.value.json()
              : { household_id: h.id, household_name: h.name, error: true };
          status.events =
            eventsResp.status === "fulfilled" && eventsResp.value.ok
              ? await eventsResp.value.json()
              : [];
          return status;
        })
      );

      if (generation !== this._renderGeneration) return; // Karte inzwischen neu konfiguriert/entfernt
      this._render(statuses);
    } catch (err) {
      if (generation !== this._renderGeneration) return;
      this._renderError(err);
    }
  }

  _statusMeta(s) {
    if (s.error) return { color: "#757575", label: "Nicht erreichbar" };
    if (!s.household_active) return { color: "#757575", label: "Überwachung pausiert" };
    if (s.status === "positive") return { color: "#2e7d32", label: "Alles in Ordnung" };
    if (s.status === "negative") return { color: "#c62828", label: "Bitte melden – keine Aktivität!" };
    return { color: "#757575", label: "Noch keine Auswertung" };
  }

  _formatTime(iso) {
    return new Date(iso).toLocaleString("de-DE", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  _render(statuses) {
    this._lastStatuses = statuses;
    const base = this._config.base_url;
    const cardsHtml = statuses
      .map((s) => {
        const meta = this._statusMeta(s);
        const expanded = this._expandedIds.has(s.household_id);
        const lastEvent = s.last_event_at ? this._formatTime(s.last_event_at) : "Keine Aktivität registriert";

        const eventsHtml = (s.events || []).length
          ? s.events
              .map(
                (e) =>
                  `<li>${this._esc(this._formatTime(e.received_at))} — ${this._esc(e.sensor_name)}
                   <span class="dim">(${this._esc(e.kind)})</span></li>`
              )
              .join("")
          : '<li class="dim">Keine Ereignisse</li>';

        return `
          <div class="hh-card" style="border-left-color:${meta.color}">
            <div class="hh-head">
              <a class="hh-link" href="${base}/households/${s.household_id}" target="_blank" rel="noopener">
                <div class="hh-name">${this._esc(s.household_name)}</div>
                <div class="hh-status">${meta.label}</div>
                <div class="hh-meta">${s.error ? "" : "Letzte Aktivität: " + this._esc(lastEvent)}</div>
              </a>
              <button class="hh-toggle" data-toggle="${s.household_id}" aria-label="Ereignisse ein-/ausblenden">
                ${expanded ? "▾" : "▸"}
              </button>
            </div>
            ${expanded ? `<ul class="hh-events">${eventsHtml}</ul>` : ""}
          </div>`;
      })
      .join("");

    this.shadowRoot.innerHTML = `
      <style>${this._css()}</style>
      <ha-card header="${this._esc(this._config.title)}">
        <div class="grid">${cardsHtml || '<p class="empty">Keine Haushalte angelegt.</p>'}</div>
      </ha-card>`;

    this._attachHandlers();
  }

  _attachHandlers() {
    this.shadowRoot.querySelectorAll("[data-toggle]").forEach((el) => {
      el.addEventListener("click", (ev) => {
        ev.preventDefault();
        const id = Number(el.dataset.toggle);
        if (this._expandedIds.has(id)) {
          this._expandedIds.delete(id);
        } else {
          this._expandedIds.add(id);
        }
        this._render(this._lastStatuses || []);
      });
    });
  }

  _renderError(err) {
    this.shadowRoot.innerHTML = `
      <style>${this._css()}</style>
      <ha-card header="${this._esc(this._config.title)}">
        <div class="error">⚠️ ${this._esc(err.message || String(err))}</div>
      </ha-card>`;
  }

  _esc(value) {
    const div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  _css() {
    return `
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
        gap: 12px;
        padding: 16px;
        align-items: start;
      }
      .hh-card {
        background: var(--card-background-color, #fff);
        border: 1px solid var(--divider-color, #e0e0e0);
        border-left: 6px solid #757575;
        border-radius: 8px;
        overflow: hidden;
      }
      .hh-head {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 4px;
        padding: 12px;
      }
      .hh-link { flex: 1; min-width: 0; text-decoration: none; color: inherit; }
      .hh-name { font-weight: 700; font-size: 1.05em; margin-bottom: 4px; color: var(--primary-text-color, #212121); }
      .hh-status { font-size: 0.9em; margin-bottom: 4px; color: var(--primary-text-color, #212121); }
      .hh-meta { font-size: 0.8em; color: var(--secondary-text-color, #757575); }
      .hh-toggle {
        flex-shrink: 0;
        background: none;
        border: none;
        cursor: pointer;
        color: var(--secondary-text-color, #757575);
        font-size: 1.1em;
        line-height: 1;
        padding: 2px 4px;
      }
      .hh-toggle:hover { color: var(--primary-text-color, #212121); }
      .hh-events {
        list-style: none;
        margin: 0;
        padding: 8px 12px 12px;
        border-top: 1px solid var(--divider-color, #e0e0e0);
        font-size: 0.8em;
        color: var(--primary-text-color, #212121);
      }
      .hh-events li { padding: 1px 0; }
      .dim { color: var(--secondary-text-color, #757575); }
      .empty, .error { padding: 16px; color: var(--secondary-text-color, #757575); }
    `;
  }
}

customElements.define("sensir-card", SensirCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "sensir-card",
  name: "SensIR",
  description: "Ampel-Übersicht der sensir-Haushalte mit auf-/zuklappbaren letzten Ereignissen",
});

console.info(`%c SENSIR-CARD %c v${SENSIR_CARD_VERSION} `, "color: #fff; background: #e20074; font-weight: 700;", "color: #e20074; background: #fff; font-weight: 700;");
