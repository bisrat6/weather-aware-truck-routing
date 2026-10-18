import { useEffect, useRef, useState, type CSSProperties, type FormEvent, type KeyboardEvent } from "react";
import {
  CalendarClock,
  ChevronDown,
  ChevronUp,
  Clock3,
  CloudSun,
  Ellipsis,
  Info,
  Route as RouteIcon,
  Weight,
} from "lucide-react";
import { analyze, heatmap } from "./api";
import { PlacePicker, type Place } from "./PlacePicker";
import { RouteMap } from "./RouteMap";
import { RISK_LEVELS, riskColorToken, type RiskLabel } from "./constants";
import type { AnalyzeResponse, AnalyzedRoute, Checkpoint, HeatmapResponse, RiskLevel } from "./types";
import "./App.css";

type IntervalMiles = 10 | 25 | 50;
type ResultsTab = "routes" | "checkpoints";
type HeatmapContext = { departureUtc: string; loadLb: number };
type CachedHeatmap = { response: HeatmapResponse; requestBytes: number; responseBytes: number };

const OPEN_METEO_URL = "https://open-meteo.com/";

function localTimezoneLabel(value: string): string {
  const date = new Date(value);
  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const hours = String(Math.floor(Math.abs(offset) / 60)).padStart(2, "0");
  const minutes = String(Math.abs(offset) % 60).padStart(2, "0");
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return `${timezone} (UTC${sign}${hours}:${minutes})`;
}

function localHeatmapTime(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function HeatmapPanel({
  response,
  routeIndex,
  loading,
  unavailable,
  hour,
  onHourChange,
  enabled,
  onEnabledChange,
}: {
  response: HeatmapResponse | null;
  routeIndex: number | null;
  loading: boolean;
  unavailable: boolean;
  hour: number;
  onHourChange: (hour: number) => void;
  enabled: boolean;
  onEnabledChange: (enabled: boolean) => void;
}) {
  const hourValue = response?.hours[hour] ?? response?.hours[0] ?? "";
  const timeLabel = hourValue ? localHeatmapTime(hourValue) : "Choose a route to load forecast hours";
  const timezone = hourValue ? localTimezoneLabel(hourValue) : Intl.DateTimeFormat().resolvedOptions().timeZone;

  return (
    <section className="panel-section heatmap-section" aria-labelledby="heatmap-title">
      <div className="section-title-row">
        <h2 id="heatmap-title" className="section-title">
          {routeIndex === null ? "Weather forecast" : `Forecast along Route ${routeIndex + 1}`}
        </h2>
        <label className="heatmap-toggle">
          <input type="checkbox" checked={enabled} onChange={(event) => onEnabledChange(event.target.checked)} />
          <span>Show</span>
        </label>
      </div>
      <label className="field-label" htmlFor="forecast-hour">Forecast hour · {timezone}</label>
      <output className="heatmap-time" htmlFor="forecast-hour">{timeLabel}</output>
      <input
        id="forecast-hour"
        className="heatmap-slider"
        type="range"
        min={0}
        max={Math.max(0, (response?.hours.length ?? 49) - 1)}
        step={1}
        value={hour}
        disabled={!response}
        aria-valuetext={hourValue ? `${timeLabel}, ${timezone}` : timeLabel}
        onChange={(event) => onHourChange(Number(event.target.value))}
      />
      {loading && <p className="heatmap-status" role="status">Loading forecast…</p>}
      {unavailable && <p className="heatmap-unavailable" role="status">Forecast overlay unavailable for this route</p>}
      <div className="heatmap-legend" aria-label="Heatmap risk legend">
        {RISK_LEVELS.filter(({ label }) => label !== "No data").map(({ label, marker }) => (
          <span className="heatmap-legend__item" key={label} style={riskStyle(label)}>
            <i aria-hidden="true">{marker}</i>{label}
          </span>
        ))}
        <span className="heatmap-legend__item" style={riskStyle("No data")}>
          <i aria-hidden="true">?</i>No forecast data
        </span>
      </div>
      <p className="heatmap-credit">Weather data by <a href={OPEN_METEO_URL} target="_blank" rel="noreferrer">Open-Meteo</a></p>
    </section>
  );
}

function defaultDeparture(): string {
  const date = new Date(Date.now() + 2 * 60 * 60 * 1000);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function formatDuration(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}`;
}

function formatMiles(miles: number): string {
  return `${miles.toFixed(0)} mi`;
}

function getRiskLabel(level: RiskLevel | null): RiskLabel {
  return level ?? "No data";
}

function riskStyle(level: RiskLabel): CSSProperties {
  return { "--risk-color": `var(${riskColorToken(level)})` } as CSSProperties;
}

function RiskBadge({ level }: { level: RiskLabel }) {
  const marker = RISK_LEVELS.find((entry) => entry.label === level)?.marker ?? "?";
  return (
    <span className="risk-badge" style={riskStyle(level)}>
      <span className="risk-badge__symbol" aria-hidden="true">{marker}</span>
      <span>{level}</span>
    </span>
  );
}

function routeTitle(route: AnalyzedRoute): string {
  return `Route ${route.route_index + 1}`;
}

function rankedRoutes(data: AnalyzeResponse): AnalyzedRoute[] {
  return data.ranking.flatMap((index) => {
    const route = data.routes.find((candidate) => candidate.route_index === index);
    return route ? [route] : [];
  });
}

function SegmentedControl({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly { value: string; label: string }[];
  value: string;
  onChange: (value: string) => void;
}) {
  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const current = options.findIndex((option) => option.value === value);
    const next = event.key === "Home"
      ? 0
      : event.key === "End"
        ? options.length - 1
        : (current + (event.key === "ArrowRight" ? 1 : options.length - 1)) % options.length;
    onChange(options[next].value);
    document.getElementById(`${label.replaceAll(" ", "-")}-${options[next].value}`)?.focus();
  }

  return (
    <div className="segmented-field">
      <span className="field-label">{label}</span>
      <div className="segmented-control" role="radiogroup" aria-label={label}>
        {options.map((option) => (
          <button
            key={option.value}
            id={`${label.replaceAll(" ", "-")}-${option.value}`}
            type="button"
            role="radio"
            aria-checked={value === option.value}
            tabIndex={value === option.value ? 0 : -1}
            className={value === option.value ? "is-active" : ""}
            onClick={() => onChange(option.value)}
            onKeyDown={handleKeyDown}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function LoadBar({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const numericValue = Number(value) || 0;
  const visualPercent = Math.max(0, Math.min(numericValue, 80_000)) / 80_000 * 100;
  const note = numericValue > 40_000
    ? "High wind can raise risk above 40,000 lb"
    : numericValue > 30_000
      ? "Stricter wind rules above 30,000 lb"
      : "Load is included in wind risk scoring";

  return (
    <section className="panel-section load-section" aria-labelledby="load-title">
      <div className="section-title-row">
        <h2 id="load-title">Load Info</h2>
        <details className="info-note">
          <summary aria-label="About load thresholds"><Info size={16} aria-hidden="true" /></summary>
          <span>Load weight can escalate wind risk based on the project thresholds.</span>
        </details>
      </div>
      <label className="field-label load-label" htmlFor="load-lb"><Weight size={15} />Cargo weight · lb</label>
      <input
        id="load-lb"
        className="field-input"
        required
        type="number"
        min="0"
        max="100000"
        step="1"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      <div className="load-meter" aria-hidden="true">
        <div className="load-meter__track"><span style={{ width: `${visualPercent}%` }} /></div>
        <i className="load-meter__tick load-meter__tick--30" />
        <i className="load-meter__tick load-meter__tick--40" />
        <span className="load-meter__limit">80k</span>
      </div>
      <div className="load-meter__labels" aria-hidden="true"><span>0</span><span>30k</span><span>40k</span><span>80k lb</span></div>
      <div className={`load-advisory${numericValue > 30_000 ? " load-advisory--active" : ""}`}>
        <strong>{numericValue.toLocaleString()} lb</strong>
        <span>{note}</span>
      </div>
    </section>
  );
}

function RouteCard({
  route,
  recommended,
  selected,
  reason,
  onSelect,
}: {
  route: AnalyzedRoute;
  recommended: boolean;
  selected: boolean;
  reason: string;
  onSelect: () => void;
}) {
  const level = getRiskLabel(route.summary.max_level);
  const routeReason = route.checkpoints.find((checkpoint) => checkpoint.reasons.length > 0)?.reasons[0];
  const explanation = recommended ? reason : routeReason;

  return (
    <li className="timeline-item">
      <span className={`timeline-index${recommended ? " timeline-index--recommended" : ""}`} aria-hidden="true">
        {route.route_index + 1}
      </span>
      <button
        type="button"
        className={`route-card${selected ? " route-card--selected" : ""}`}
        aria-pressed={selected}
        onClick={onSelect}
      >
        <span className="route-card__heading">
          <span className="route-card__name">{routeTitle(route)}</span>
          {recommended && <span className="recommended-pill">Recommended</span>}
          <RiskBadge level={level} />
        </span>
        <span className="route-card__provider">{route.origin_label}</span>
        <span className="route-card__metrics">
          <span><RouteIcon size={15} />{formatMiles(route.distance_mi)}</span>
          <i aria-hidden="true" />
          <span><Clock3 size={15} />{formatDuration(route.duration_s)}</span>
        </span>
        <span className="route-card__risk-miles">
          <span><b className="risk-dot risk-dot--severe" />Severe <strong>{formatMiles(route.summary.miles_by_level.Severe)}</strong></span>
          <span><b className="risk-dot risk-dot--high" />High <strong>{formatMiles(route.summary.miles_by_level.High)}</strong></span>
          {route.summary.missing_weather_count > 0 && (
            <span className="route-card__missing">Weather missing · {formatMiles(route.summary.missing_weather_miles)} · {route.summary.missing_weather_count} checkpoints</span>
          )}
        </span>
        {explanation && <span className="route-card__reason">{explanation}</span>}
        <span className="route-card__footer">
          <span>
            {route.summary.unstable_checkpoints > 0
              ? `${route.summary.unstable_checkpoints} checkpoints may change ±2 h`
              : route.summary.unknown_stability_checkpoints > 0
                ? `Stability unknown for ${route.summary.unknown_stability_checkpoints}`
                : "Stable within a ±2 h shift"}
            {route.summary.unknown_stability_checkpoints > 0 && route.summary.unstable_checkpoints > 0
              ? ` · ${route.summary.unknown_stability_checkpoints} unknown`
              : ""}
          </span>
          <Ellipsis size={16} aria-hidden="true" />
        </span>
      </button>
    </li>
  );
}

function CheckpointList({ checkpoints }: { checkpoints: Checkpoint[] }) {
  const visibleCheckpoints = checkpoints.slice(0, 80);
  return (
    <div className="checkpoint-list" aria-label="Route checkpoints">
      {visibleCheckpoints.map((checkpoint) => (
        <div className="checkpoint-row" key={checkpoint.index}>
          <strong className="checkpoint-row__index">{checkpoint.index + 1}</strong>
          <span className="checkpoint-row__distance">{formatMiles(checkpoint.dist_mi)}</span>
          <time className="checkpoint-row__eta" dateTime={checkpoint.eta_utc}>
            {new Date(checkpoint.eta_utc).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" })}
          </time>
          <RiskBadge level={getRiskLabel(checkpoint.risk_level)} />
        </div>
      ))}
      {checkpoints.length > visibleCheckpoints.length && (
        <p className="checkpoint-list__cap">Showing first {visibleCheckpoints.length} of {checkpoints.length} checkpoints.</p>
      )}
    </div>
  );
}

export default function App() {
  const heatmapController = useRef<AbortController | null>(null);
  const heatmapCache = useRef(new Map<number, CachedHeatmap>());
  const [origin, setOrigin] = useState<Place | null>(null);
  const [destination, setDestination] = useState<Place | null>(null);
  const [departure, setDeparture] = useState(defaultDeparture);
  const [loadLb, setLoadLb] = useState("0");
  const [intervalMiles, setIntervalMiles] = useState<IntervalMiles>(25);
  const [data, setData] = useState<AnalyzeResponse | null>(null);
  const [selectedRoute, setSelectedRoute] = useState<number | null>(null);
  const [tab, setTab] = useState<ResultsTab>("routes");
  const [loading, setLoading] = useState(false);
  const [waking, setWaking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [heatmapContext, setHeatmapContext] = useState<HeatmapContext | null>(null);
  const [heatmapResponse, setHeatmapResponse] = useState<HeatmapResponse | null>(null);
  const [heatmapLoading, setHeatmapLoading] = useState(false);
  const [heatmapUnavailable, setHeatmapUnavailable] = useState(false);
  const [heatmapHour, setHeatmapHour] = useState(0);
  const [heatmapEnabled, setHeatmapEnabled] = useState(true);
  const [heatmapRequestedAt, setHeatmapRequestedAt] = useState<number | null>(null);
  const [heatmapRequestBytes, setHeatmapRequestBytes] = useState<number | null>(null);
  const [heatmapResponseBytes, setHeatmapResponseBytes] = useState<number | null>(null);

  useEffect(() => {
    if (!loading) return;
    const timer = window.setTimeout(() => setWaking(true), 3000);
    return () => window.clearTimeout(timer);
  }, [loading]);

  const activeRoute = data?.routes.find((route) => route.route_index === selectedRoute)
    ?? data?.routes.find((route) => route.route_index === data.recommended)
    ?? null;

  useEffect(() => {
    if (!activeRoute || !heatmapContext) return;
    const cached = heatmapCache.current.get(activeRoute.route_index);
    if (cached) {
      setHeatmapResponse(cached.response);
      setHeatmapRequestBytes(cached.requestBytes);
      setHeatmapResponseBytes(cached.responseBytes);
      setHeatmapRequestedAt(null);
      setHeatmapLoading(false);
      setHeatmapUnavailable(false);
      setHeatmapHour(0);
      return;
    }

    const controller = new AbortController();
    heatmapController.current = controller;
    const requestedAt = performance.now();
    const requestBody = {
      geometry: activeRoute.geometry,
      departure_utc: heatmapContext.departureUtc,
      load_lb: heatmapContext.loadLb,
    };
    const requestBytes = new TextEncoder().encode(JSON.stringify(requestBody)).length;
    setHeatmapRequestedAt(requestedAt);
    setHeatmapRequestBytes(requestBytes);
    setHeatmapResponseBytes(null);
    setHeatmapResponse(null);
    setHeatmapLoading(true);
    setHeatmapUnavailable(false);
    setHeatmapHour(0);

    let responseBytes = 0;
    void heatmap(requestBody, controller.signal, (bodyText) => {
      responseBytes = new TextEncoder().encode(bodyText).length;
    }).then((response) => {
      if (controller.signal.aborted) return;
      heatmapCache.current.set(activeRoute.route_index, { response, requestBytes, responseBytes });
      setHeatmapResponse(response);
      setHeatmapResponseBytes(responseBytes);
      setHeatmapLoading(false);
    }).catch(() => {
      if (controller.signal.aborted) return;
      setHeatmapUnavailable(true);
      setHeatmapLoading(false);
    });

    return () => {
      controller.abort();
      if (heatmapController.current === controller) heatmapController.current = null;
    };
  }, [activeRoute, heatmapContext]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!origin || !destination) return;
    heatmapController.current?.abort();
    heatmapController.current = null;
    setHeatmapContext(null);
    setHeatmapResponse(null);
    setHeatmapLoading(false);
    setHeatmapUnavailable(false);
    setHeatmapRequestedAt(null);
    setHeatmapRequestBytes(null);
    setHeatmapResponseBytes(null);
    heatmapCache.current.clear();
    setHeatmapHour(0);
    setLoading(true);
    setWaking(false);
    setError(null);
    setData(null);
    try {
      const departureUtc = new Date(departure).toISOString();
      const result = await analyze({
        origin: { lat: origin.lat, lon: origin.lon },
        destination: { lat: destination.lat, lon: destination.lon },
        departure_utc: departureUtc,
        load_lb: Number(loadLb),
        interval_miles: intervalMiles,
      });
      setData(result);
      setSelectedRoute(result.recommended);
      setTab("routes");
      setHeatmapContext({ departureUtc, loadLb: Number(loadLb) });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The request failed.");
    } finally {
      setLoading(false);
      setWaking(false);
    }
  }

  const routes = data ? rankedRoutes(data) : [];

  function handleTabKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const nextTab = tab === "routes" ? "checkpoints" : "routes";
    setTab(nextTab);
    document.getElementById(`${nextTab}-tab`)?.focus();
  }

  return (
    <main className="workspace">
      <section className="map-stage" aria-label="Route map">
        <RouteMap
          routes={data?.routes ?? []}
          recommended={data?.recommended ?? null}
          selectedRoute={activeRoute}
          heatmapResponse={heatmapResponse}
          heatmapHour={heatmapHour}
          heatmapEnabled={heatmapEnabled}
          heatmapRequestedAt={heatmapRequestedAt}
          heatmapRequestBytes={heatmapRequestBytes}
          heatmapResponseBytes={heatmapResponseBytes}
        />
        <div className="risk-legend" aria-label="Risk level legend">
          <span className="risk-legend__title">Checkpoint risk</span>
          {RISK_LEVELS.map(({ label, marker }) => (
            <span className="risk-legend__item" key={label} style={riskStyle(label)}>
              <i aria-hidden="true">{marker}</i>{label}
            </span>
          ))}
        </div>
      </section>

      <aside className={`trip-panel${collapsed ? " trip-panel--collapsed" : ""}`} aria-label="Trip planning and route results">
        <button
          className="panel-handle"
          type="button"
          aria-expanded={!collapsed}
          aria-label={collapsed ? "Expand trip panel" : "Collapse trip panel"}
          onClick={() => setCollapsed((value) => !value)}
        >
          {collapsed ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
        </button>
        <div className="trip-panel__content">
          <header className="panel-header">
            <div className="app-icon">
              <img src="/worldwise-icon.png" alt="World Wise" />
            </div>
            <div className="panel-header__text">
              <h1>Weather-Aware Routing</h1>
              <p>Safest route for your load</p>
            </div>
          </header>

          <form onSubmit={submit}>
            <section className="panel-section" aria-labelledby="trip-title">
              <h2 id="trip-title" className="section-title">Trip</h2>
              <PlacePicker id="origin" label="Origin" value={origin} onChange={setOrigin} />
              <PlacePicker id="destination" label="Destination" value={destination} onChange={setDestination} />
              <label className="form-field">
                <span className="field-label"><CalendarClock size={15} />Departure</span>
                <input className="field-input" required type="datetime-local" value={departure} onChange={(event) => setDeparture(event.target.value)} />
              </label>
              <SegmentedControl
                label="Checkpoint interval"
                options={[{ value: "10", label: "10 mi" }, { value: "25", label: "25 mi" }, { value: "50", label: "50 mi" }]}
                value={String(intervalMiles)}
                onChange={(value) => setIntervalMiles(Number(value) as IntervalMiles)}
              />
              <button className="analyze-button" type="submit" disabled={loading || !origin || !destination}>
                {loading ? "Analyzing routes…" : "Analyze routes"}
                <RouteIcon size={16} aria-hidden="true" />
              </button>
            </section>

            <LoadBar value={loadLb} onChange={setLoadLb} />
          </form>

          <section className="panel-section results-section" aria-labelledby="routes-title">
            <div className="section-title-row">
              <h2 id="routes-title" className="section-title">Routes</h2>
              {data && <span className="route-count">{data.routes.length} available</span>}
            </div>
            {data && (
              <div className="segmented-control results-tabs" role="tablist" aria-label="Route details">
                <button id="routes-tab" type="button" role="tab" aria-controls="route-tabpanel" aria-selected={tab === "routes"} tabIndex={tab === "routes" ? 0 : -1} className={tab === "routes" ? "is-active" : ""} onClick={() => setTab("routes")} onKeyDown={handleTabKeyDown}>Routes</button>
                <button id="checkpoints-tab" type="button" role="tab" aria-controls="route-tabpanel" aria-selected={tab === "checkpoints"} tabIndex={tab === "checkpoints" ? 0 : -1} className={tab === "checkpoints" ? "is-active" : ""} onClick={() => setTab("checkpoints")} onKeyDown={handleTabKeyDown}>Checkpoints</button>
              </div>
            )}
            {data?.notices.length ? <div className="notice-banner" role="note">{data.notices.map((notice) => <p key={notice}>{notice}</p>)}</div> : null}
            {error && <p className="error-banner" role="alert">{error}</p>}
            {loading && (
              <div className="loading-state" role="status" aria-live="polite">
                <span className="loading-state__spinner" aria-hidden="true" />
                <span>{waking ? "Waking the server, this can take up to a minute." : "Checking route and forecast conditions…"}</span>
                <div className="loading-skeleton"><i /><i /><i /></div>
              </div>
            )}
            {!loading && !error && !data && (
              <div className="empty-state"><CloudSun size={20} /><span>Choose two places to see route conditions.</span></div>
            )}
            {!loading && data && (
              <div id="route-tabpanel" role="tabpanel" aria-labelledby={`${tab}-tab`} tabIndex={0}>
                {tab === "routes" ? (routes.length ? (
                  <ol className="route-timeline">
                    {routes.map((route) => (
                      <RouteCard
                        key={route.route_index}
                        route={route}
                        recommended={route.route_index === data.recommended}
                        selected={route.route_index === (selectedRoute ?? data.recommended)}
                        reason={data.recommendation_reason}
                        onSelect={() => setSelectedRoute(route.route_index)}
                      />
                    ))}
                  </ol>
                ) : <div className="empty-state">No route options returned.</div>) : (
                  activeRoute ? <CheckpointList checkpoints={activeRoute.checkpoints} /> : <div className="empty-state">Select a route to inspect its checkpoints.</div>
                )}
              </div>
            )}
          </section>
          <HeatmapPanel
            response={heatmapResponse}
            routeIndex={activeRoute?.route_index ?? null}
            loading={heatmapLoading}
            unavailable={heatmapUnavailable}
            hour={heatmapHour}
            onHourChange={setHeatmapHour}
            enabled={heatmapEnabled}
            onEnabledChange={setHeatmapEnabled}
          />
        </div>
      </aside>
    </main>
  );
}
