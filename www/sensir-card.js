const SENSIR_CARD_VERSION = "1.1.0";

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
 *   base_url: http://192.168.42.132:8000   # erforderlich
 *   title: SensIR                          # optional
 *   refresh_seconds: 60                    # optional
 *   household_ids: [1, 2]                  # optional, sonst alle Haushalte
 *
 * Klick auf eine Haushalts-Kachel klappt Sensoren + Kontakte direkt in der
 * Karte auf (kein Verlassen von Home Assistant nötig); ein Link am Ende
 * öffnet bei Bedarf die vollständige sensir-Seite (Zeitfenster bearbeiten,
 * Testnachricht senden, Sensoren anlegen, …) in einem neuen Tab.
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
    return { base_url: "http://192.168.42.132:8000", title: "SensIR" };
  }

  getCardSize() {
    return 4;
  }

  setConfig(config) {
    if (!config.base_url) {
      throw new Error("sensir-card: base_url fehlt (z. B. http://192.168.42.132:8000)");
    }
    this._config = {
      title: "SensIR",
      refresh_seconds: 60,
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
    try {
      const hhResp = await fetch(`${base}/api/households`);
      if (!hhResp.ok) throw new Error(`Haushalte: HTTP ${hhResp.status}`);
      let households = await hhResp.json();
      if (Array.isArray(this._config.household_ids) && this._config.household_ids.length) {
        households = households.filter((h) => this._config.household_ids.includes(h.id));
      }

      const statuses = await Promise.all(
        households.map(async (h) => {
          try {
            const r = await fetch(`${base}/api/households/${h.id}/status`);
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return await r.json();
          } catch (err) {
            return { household_id: h.id, household_name: h.name, error: true };
          }
        })
      );

      // Details (Sensoren + Kontakte) nur für aufgeklappte Haushalte holen,
      // nicht bei jedem Poll für alle - spart unnötige Requests.
      const details = {};
      await Promise.all(
        [...this._expandedIds].map(async (id) => {
          details[id] = await this._fetchDetail(base, id);
        })
      );

      if (generation !== this._renderGeneration) return; // Karte inzwischen neu konfiguriert/entfernt
      this._render(statuses, details);
    } catch (err) {
      if (generation !== this._renderGeneration) return;
      this._renderError(err);
    }
  }

  async _fetchDetail(base, householdId) {
    try {
      const [sensorsResp, contactsResp] = await Promise.all([
        fetch(`${base}/api/sensors?household_id=${householdId}`),
        fetch(`${base}/api/households/${householdId}/contacts`),
      ]);
      return {
        sensors: sensorsResp.ok ? await sensorsResp.json() : [],
        contacts: contactsResp.ok ? await contactsResp.json() : [],
      };
    } catch (err) {
      return { error: true };
    }
  }

  _statusMeta(s) {
    if (s.error) return { color: "#757575", label: "Nicht erreichbar" };
    if (!s.household_active) return { color: "#757575", label: "Überwachung pausiert" };
    if (s.status === "positive") return { color: "#2e7d32", label: "Alles in Ordnung" };
    if (s.status === "negative") return { color: "#c62828", label: "Bitte melden – keine Aktivität!" };
    return { color: "#757575", label: "Noch keine Auswertung" };
  }

  _render(statuses, details) {
    const base = this._config.base_url;
    const cardsHtml = statuses
      .map((s) => {
        const meta = this._statusMeta(s);
        const expanded = this._expandedIds.has(s.household_id);
        const lastEvent = s.last_event_at
          ? new Date(s.last_event_at).toLocaleString("de-DE", {
              day: "2-digit",
              month: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            })
          : "Keine Aktivität registriert";

        return `
          <div class="hh-card${expanded ? " expanded" : ""}" style="border-left-color:${meta.color}"
               data-id="${s.household_id}">
            <div class="hh-head" data-toggle="${s.household_id}">
              <div>
                <div class="hh-name">${this._esc(s.household_name)}</div>
                <div class="hh-status">${meta.label}</div>
                <div class="hh-meta">${s.error ? "" : "Letzte Aktivität: " + this._esc(lastEvent)}</div>
              </div>
              <div class="hh-chevron">${expanded ? "▾" : "▸"}</div>
            </div>
            ${expanded ? this._renderDetail(base, s.household_id, details[s.household_id]) : ""}
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

  _renderDetail(base, householdId, detail) {
    if (!detail) return `<div class="hh-detail"><p class="loading">Lädt …</p></div>`;
    if (detail.error) return `<div class="hh-detail"><p class="error">Details nicht erreichbar.</p></div>`;

    const sensorsHtml = detail.sensors.length
      ? detail.sensors
          .map(
            (sn) =>
              `<li>${this._esc(sn.name)} <span class="dim">— ${this._esc(sn.kind)}</span>${
                sn.is_active ? "" : ' <span class="dim">(inaktiv)</span>'
              }</li>`
          )
          .join("")
      : '<li class="dim">Keine Sensoren</li>';

    const contactsHtml = detail.contacts.length
      ? detail.contacts
          .map((c) => `<li>${this._esc(c.name)}${c.telegram_chat_id ? " — Telegram" : ""}</li>`)
          .join("")
      : '<li class="dim">Keine Kontakte</li>';

    return `
      <div class="hh-detail">
        <div class="hh-detail-col">
          <div class="hh-detail-title">Sensoren</div>
          <ul>${sensorsHtml}</ul>
        </div>
        <div class="hh-detail-col">
          <div class="hh-detail-title">Kontakte</div>
          <ul>${contactsHtml}</ul>
        </div>
        <a class="hh-detail-link" href="${base}/households/${householdId}" target="_blank" rel="noopener">
          Vollständige Seite öffnen (Zeitfenster, Testnachricht, Sensor hinzufügen …) ↗
        </a>
      </div>`;
  }

  _attachHandlers() {
    this.shadowRoot.querySelectorAll("[data-toggle]").forEach((el) => {
      el.addEventListener("click", () => {
        const id = Number(el.dataset.toggle);
        if (this._expandedIds.has(id)) {
          this._expandedIds.delete(id);
        } else {
          this._expandedIds.add(id);
        }
        this._fetchAndRender();
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
        gap: 8px;
        padding: 12px;
        cursor: pointer;
      }
      .hh-name { font-weight: 700; font-size: 1.05em; margin-bottom: 4px; color: var(--primary-text-color, #212121); }
      .hh-status { font-size: 0.9em; margin-bottom: 4px; color: var(--primary-text-color, #212121); }
      .hh-meta { font-size: 0.8em; color: var(--secondary-text-color, #757575); }
      .hh-chevron { color: var(--secondary-text-color, #757575); font-size: 1.1em; line-height: 1; }
      .hh-detail {
        border-top: 1px solid var(--divider-color, #e0e0e0);
        padding: 10px 12px 12px;
        display: flex;
        flex-wrap: wrap;
        gap: 16px;
        font-size: 0.85em;
      }
      .hh-detail-col { min-width: 120px; flex: 1; }
      .hh-detail-title { font-weight: 700; margin-bottom: 4px; color: var(--primary-text-color, #212121); }
      .hh-detail ul { list-style: none; margin: 0; padding: 0; color: var(--primary-text-color, #212121); }
      .hh-detail li { padding: 1px 0; }
      .dim { color: var(--secondary-text-color, #757575); }
      .hh-detail-link {
        display: block;
        width: 100%;
        margin-top: 8px;
        color: var(--primary-color, #e20074);
        text-decoration: none;
        font-size: 0.85em;
      }
      .hh-detail-link:hover { text-decoration: underline; }
      .loading, .empty, .error { padding: 4px 0; color: var(--secondary-text-color, #757575); }
    `;
  }
}

customElements.define("sensir-card", SensirCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "sensir-card",
  name: "SensIR",
  description: "Ampel-Übersicht der sensir-Haushalte (Status, Sensoren, Kontakte)",
});

console.info(`%c SENSIR-CARD %c v${SENSIR_CARD_VERSION} `, "color: #fff; background: #e20074; font-weight: 700;", "color: #e20074; background: #fff; font-weight: 700;");
