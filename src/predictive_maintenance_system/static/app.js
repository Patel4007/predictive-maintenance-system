const state = {
  snapshot: null,
  source: null,
};

const numberFmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
const moneyFmt = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

function number(value, suffix = "") {
  return `${numberFmt.format(value ?? 0)}${suffix}`;
}

function currency(value) {
  return moneyFmt.format(value ?? 0);
}

function toneClass(value) {
  const key = String(value || "").toLowerCase();
  if (["critical", "high", "failed", "immediate"].includes(key)) {
    return "pill--critical";
  }
  if (["medium", "warning", "degraded", "urgent", "planned"].includes(key)) {
    return "pill--warning";
  }
  if (["low", "info", "running", "monitor"].includes(key)) {
    return "pill--good";
  }
  return "pill--neutral";
}

async function postJson(url, payload = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || "Request failed");
  }
  return body;
}

function setFeedback(id, message, isError = false) {
  const node = document.getElementById(id);
  if (!node) {
    return;
  }
  node.textContent = message;
  node.style.color = isError ? "var(--red)" : "var(--muted)";
}

function render(snapshot) {
  state.snapshot = snapshot;
  document.getElementById("fleet-name").textContent = snapshot.fleet_name;
  document.getElementById("heartbeat").textContent = new Date(snapshot.generated_at).toLocaleTimeString();
  document.getElementById("site-meta").textContent = `${snapshot.site_name} · ${snapshot.fleet_name} · ${snapshot.metrics.asset_count} simulated assets`;

  renderFocus(snapshot);
  renderKpis(snapshot);
  renderSchedule(snapshot);
  renderScenarios(snapshot);
  renderAssets(snapshot);
  renderCharts(snapshot);
  renderInsights(snapshot);
}

function renderFocus(snapshot) {
  const asset = snapshot.assets[0];
  const order = snapshot.maintenance_schedule.find((item) => item.asset_id === asset?.asset_id) || snapshot.maintenance_schedule[0];

  if (!asset) {
    document.getElementById("focus-title").textContent = "No assets available";
    document.getElementById("focus-detail").textContent = "The model has no telemetry to rank yet.";
    document.getElementById("focus-action").textContent = "Waiting for machine history.";
    return;
  }

  document.getElementById("focus-chip").textContent = `${asset.risk_level} risk`;
  document.getElementById("focus-chip").className = `pill ${toneClass(asset.risk_level)}`;
  document.getElementById("focus-priority").textContent = order ? order.priority_label : "Monitor";
  document.getElementById("focus-priority").className = `pill ${toneClass(order?.priority_label || "monitor")}`;
  document.getElementById("focus-title").textContent = `${asset.name} is the lead intervention candidate`;
  document.getElementById("focus-detail").textContent = `${asset.location} · ${asset.asset_type} · ${asset.state} · confidence ${number(asset.confidence * 100, "%")}`;
  document.getElementById("focus-action").textContent = order ? order.rationale : asset.recommended_action;
  document.getElementById("focus-mode").textContent = asset.predicted_failure_mode;
  document.getElementById("focus-risk").textContent = `${number(asset.probability_24h * 100, "%")} / ${number(asset.probability_7d * 100, "%")}`;
  document.getElementById("focus-rul").textContent = `${number(asset.remaining_useful_life_hours)} h`;
  document.getElementById("focus-savings").textContent = order ? currency(order.estimated_cost_avoided_usd) : currency(snapshot.metrics.estimated_cost_avoided_usd);
  document.getElementById("focus-window").textContent = order ? new Date(order.scheduled_start).toLocaleString() : "Continue monitoring";
}

function renderKpis(snapshot) {
  const metrics = snapshot.metrics;
  const cards = [
    {
      label: "Fleet Health",
      value: `${number(metrics.average_health_score, "%")}`,
      detail: `${metrics.running_assets} running · ${metrics.degraded_assets} degraded · ${metrics.failed_assets} failed`,
      accent: "score--mint",
    },
    {
      label: "7d Failure Load",
      value: `${number(metrics.average_probability_7d * 100, "%")}`,
      detail: `${number(metrics.predicted_failures_7d)} forecast failures across the next week`,
      accent: "score--amber",
    },
    {
      label: "Urgent Work Orders",
      value: `${metrics.urgent_work_orders}`,
      detail: `${number(metrics.scheduled_maintenance_hours)} crew-hours scheduled`,
      accent: "score--coral",
    },
    {
      label: "High-Risk Assets",
      value: `${metrics.high_risk_assets}`,
      detail: "Assets currently above the high-risk threshold",
      accent: "score--ink",
    },
    {
      label: "Downtime Avoided",
      value: `${number(metrics.estimated_downtime_avoided_hours)} h`,
      detail: `${currency(metrics.estimated_cost_avoided_usd)} of production value protected`,
      accent: "score--mint",
    },
    {
      label: "Portfolio Size",
      value: `${metrics.asset_count}`,
      detail: "Simulated machines under live predictive monitoring",
      accent: "score--amber",
    },
  ];

  document.getElementById("kpi-grid").innerHTML = cards
    .map(
      (card, index) => `
        <article class="score ${card.accent} ${index === 0 ? "score--hero" : ""}">
          <div class="score__label">${card.label}</div>
          <div class="score__value">${card.value}</div>
          <div class="score__detail">${card.detail}</div>
        </article>
      `
    )
    .join("");
}

function renderSchedule(snapshot) {
  const container = document.getElementById("schedule-list");
  if (!snapshot.maintenance_schedule.length) {
    container.innerHTML = `
      <article class="work-order work-order--empty">
        <div class="work-order__eyebrow">Queue is clear</div>
        <h3>No intervention is required right now</h3>
        <p class="work-order__text">The simulator is not recommending a maintenance stop in the current operating window.</p>
      </article>
    `;
    return;
  }

  container.innerHTML = snapshot.maintenance_schedule
    .slice(0, 5)
    .map(
      (item) => `
        <article class="work-order">
          <div class="work-order__header">
            <div>
              <div class="work-order__eyebrow">${item.task_id} · ${item.asset_name}</div>
              <h3>${item.action}</h3>
            </div>
            <span class="pill ${toneClass(item.priority_label)}">${item.priority_label}</span>
          </div>
          <p class="work-order__text">${item.rationale}</p>
          <div class="work-order__meta">
            <span>Start ${new Date(item.scheduled_start).toLocaleString()}</span>
            <span>Due ${new Date(item.due_by).toLocaleString()}</span>
          </div>
          <div class="work-order__meta">
            <span>${number(item.duration_hours)} h service</span>
            <span>${number(item.estimated_downtime_avoided_hours)} h avoided</span>
            <span>${currency(item.estimated_cost_avoided_usd)}</span>
          </div>
          <button data-service="${item.asset_id}" class="button button--primary">Execute Maintenance</button>
        </article>
      `
    )
    .join("");

  container.querySelectorAll("button[data-service]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        const body = await postJson(`/api/maintenance/execute/${button.dataset.service}`);
        setFeedback("stream-feedback", body.detail);
      } catch (error) {
        setFeedback("stream-feedback", error.message, true);
      }
    });
  });
}

function renderScenarios(snapshot) {
  const container = document.getElementById("scenario-list");
  container.innerHTML = snapshot.scenarios
    .map(
      (scenario) => `
        <article class="scenario-card">
          <div class="scenario-card__title">${scenario.name}</div>
          <p>${scenario.description}</p>
          <div class="scenario-card__impact">${scenario.impact}</div>
          <button data-scenario="${scenario.scenario_id}" class="button button--secondary">Run Scenario</button>
        </article>
      `
    )
    .join("");

  container.querySelectorAll("button[data-scenario]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        const body = await postJson(`/api/scenarios/${button.dataset.scenario}`, { duration_hours: 16 });
        setFeedback("scenario-feedback", body.detail);
      } catch (error) {
        setFeedback("scenario-feedback", error.message, true);
      }
    });
  });
}

function renderAssets(snapshot) {
  const container = document.getElementById("asset-grid");
  container.innerHTML = snapshot.assets
    .map(
      (asset) => `
        <article class="asset-card asset-card--${asset.risk_level}">
          <div class="asset-card__header">
            <div>
              <div class="asset-card__meta">${asset.asset_id} · ${asset.location}</div>
              <h3>${asset.name}</h3>
              <p class="asset-card__subhead">${asset.predicted_failure_mode} · ${asset.asset_type}</p>
            </div>
            <div class="asset-card__pills">
              <span class="pill ${toneClass(asset.state)}">${asset.state}</span>
              <span class="pill ${toneClass(asset.risk_level)}">${asset.risk_level}</span>
            </div>
          </div>

          <p class="asset-card__action">${asset.recommended_action}</p>

          <div class="asset-card__metrics">
            <div class="metric-chip"><span>Health</span><strong>${number(asset.health_score, "%")}</strong></div>
            <div class="metric-chip"><span>RUL</span><strong>${number(asset.remaining_useful_life_hours)} h</strong></div>
            <div class="metric-chip"><span>24h Risk</span><strong>${number(asset.probability_24h * 100, "%")}</strong></div>
            <div class="metric-chip"><span>7d Risk</span><strong>${number(asset.probability_7d * 100, "%")}</strong></div>
            <div class="metric-chip"><span>Vibration</span><strong>${number(asset.vibration_mm_s)}</strong></div>
            <div class="metric-chip"><span>Temperature</span><strong>${number(asset.temperature_c, "°C")}</strong></div>
            <div class="metric-chip"><span>Pressure</span><strong>${number(asset.pressure_bar, " bar")}</strong></div>
            <div class="metric-chip"><span>Lubricant</span><strong>${number(asset.lubricant_pct, "%")}</strong></div>
          </div>

          <div class="asset-card__drivers">
            ${asset.risk_drivers.map((driver) => `<div class="driver-note">${driver}</div>`).join("")}
          </div>

          <div class="asset-card__signals">
            <div class="mini-panel">
              <div class="mini-panel__label">Health</div>
              ${sparkline(asset.recent_health, "var(--mint)")}
            </div>
            <div class="mini-panel">
              <div class="mini-panel__label">Risk</div>
              ${sparkline(asset.recent_risk, "var(--amber)")}
            </div>
            <div class="mini-panel">
              <div class="mini-panel__label">Vibration</div>
              ${sparkline(asset.recent_vibration, "var(--teal)")}
            </div>
          </div>
        </article>
      `
    )
    .join("");
}

function renderCharts(snapshot) {
  const history = snapshot.history;
  const charts = [
    ["Average Health", `${number(snapshot.metrics.average_health_score, "%")}`, history.map((item) => item.average_health_score), "var(--mint)"],
    ["Average 7d Risk", `${number(snapshot.metrics.average_probability_7d * 100, "%")}`, history.map((item) => item.average_probability_7d * 100), "var(--amber)"],
    ["High-Risk Count", `${snapshot.metrics.high_risk_assets}`, history.map((item) => item.high_risk_assets), "var(--coral)"],
    ["Downtime Avoided", `${number(snapshot.metrics.estimated_downtime_avoided_hours)} h`, history.map((item) => item.estimated_downtime_avoided_hours), "var(--teal)"],
  ];

  document.getElementById("chart-grid").innerHTML = charts
    .map(
      ([label, value, values, color]) => `
        <article class="signal-card">
          <div class="signal-card__label">${label}</div>
          <div class="signal-card__value">${value}</div>
          ${sparkline(values, color)}
        </article>
      `
    )
    .join("");
}

function renderInsights(snapshot) {
  const container = document.getElementById("insight-list");
  container.innerHTML = snapshot.insights
    .map(
      (insight) => `
        <article class="insight-card">
          <div class="insight-card__header">
            <div class="insight-card__title">${insight.title}</div>
            <span class="pill ${toneClass(insight.severity)}">${insight.severity}</span>
          </div>
          <p class="insight-card__text">${insight.detail}</p>
          <div class="insight-card__meta">${insight.source} · ${new Date(insight.timestamp).toLocaleTimeString()}</div>
        </article>
      `
    )
    .join("");
}

function sparkline(values, stroke) {
  const points = (values || []).map((value) => Number(value || 0));
  if (!points.length) {
    return `<svg viewBox="0 0 220 88"></svg>`;
  }
  const max = Math.max(...points);
  const min = Math.min(...points);
  const range = max - min || 1;
  const path = points
    .map((value, index) => {
      const x = (index / Math.max(points.length - 1, 1)) * 220;
      const y = 70 - ((value - min) / range) * 50;
      return `${x},${y}`;
    })
    .join(" ");

  return `
    <svg viewBox="0 0 220 88" preserveAspectRatio="none">
      <polyline fill="none" stroke="${stroke}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" points="${path}"></polyline>
      <line x1="0" y1="70" x2="220" y2="70" stroke="rgba(20, 31, 38, 0.16)" stroke-width="1"></line>
    </svg>
  `;
}

async function loadDashboard() {
  const response = await fetch("/api/dashboard");
  const snapshot = await response.json();
  render(snapshot);
}

function connectStream() {
  const source = new EventSource("/api/events");
  state.source = source;
  source.onopen = () => {
    const node = document.getElementById("stream-status");
    node.textContent = "Connected";
    node.className = "pill pill--good";
    setFeedback("stream-feedback", "Streaming live telemetry updates.");
  };
  source.onmessage = (event) => {
    render(JSON.parse(event.data));
  };
  source.onerror = () => {
    const node = document.getElementById("stream-status");
    node.textContent = "Reconnecting";
    node.className = "pill pill--warning";
    setFeedback("stream-feedback", "Telemetry connection dropped. Retrying automatically.");
  };
}

loadDashboard()
  .then(connectStream)
  .catch((error) => {
    const node = document.getElementById("stream-status");
    node.textContent = "Error";
    node.className = "pill pill--critical";
    setFeedback("stream-feedback", error.message, true);
  });
