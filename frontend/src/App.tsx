import { useEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent, type KeyboardEvent } from "react";
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
import { RiskIcon } from "./RiskIcon";
import type { AnalyzeResponse, AnalyzedRoute, Checkpoint, HeatmapResponse, RiskLevel } from "./types";
import "./App.css";

type IntervalMiles = 10 | 25 | 50;
type ResultsTab = "routes" | "checkpoints";
type HeatmapContext = { departureUtc: string; loadLb: number };
type CachedHeatmap = { response: HeatmapResponse; requestBytes: number; responseBytes: number };

const OPEN_METEO_URL = "https://open-meteo.com/";
const HEATMAP_RISK_LEVELS: RiskLevel[] = ["Low", "Moderate", "High", "Severe", "No Travel"];

function stormDemoEnabled(): boolean {
  return import.meta.env.DEV && new URLSearchParams(window.location.search).get("demo") === "storm";
}

function makeStormDemo(response: HeatmapResponse): HeatmapResponse {
  const missingIndexes = new Set([0.11, 0.52, 0.88].map((part) => Math.round((response.points.length - 1) * part)));
  return {
    ...response,
    points: response.points.map((point, pointIndex) => ({
      ...point,
      risk: response.hours.map((_, hour) => {
        if (missingIndexes.has(pointIndex)) return null;
        const progress = hour / Math.max(1, response.hours.length - 1);
        const stormStrength = Math.sin(progress * Math.PI);
        const routePosition = response.points.length < 2 ? 0.5 : pointIndex / (response.points.length - 1);
        const distanceFromCenter = Math.abs(routePosition - 0.5);
        const stormWidth = 0.015 + stormStrength * 0.16;
        if (stormStrength > 0.12 && distanceFromCenter <= stormWidth * 0.2) return 3;
        if (stormStrength > 0.08 && distanceFromCenter <= stormWidth * 0.55) return 2;
        if (stormStrength > 0.05 && distanceFromCenter <= stormWidth) return 1;
        return 0;
      }),
    })),
  };
}

function localTimezoneLabel(value: string): string {
  const date = new Date(value);
  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const hours = String(Math.floor(Math.abs(offset) / 60)).padStart(2, "0");
  const minutes = String(Math.abs(offset) % 60).padStart(2, "0");
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return `${timezone} (UTC${sign}${hours}:${minutes})`;
}

function localHeatmapTime(value: string, departureValue: string): string {
  const date = new Date(value);
  const hourOffset = Math.round((date.getTime() - Date.parse(departureValue)) / 3_600_000);
  const weekday = new Intl.DateTimeFormat(undefined, { weekday: "short" }).format(date);
  const localTime = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
  const utcTime = new Intl.DateTimeFormat("en-GB", {
    timeZone: "UTC", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(date);
  return `${weekday} ${localTime} (+${hourOffset} h) · ${utcTime} UTC`;
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
  demo,
}: {
  response: HeatmapResponse | null;
  routeIndex: number | null;
  loading: boolean;
  unavailable: boolean;
  hour: number;
  onHourChange: (hour: number) => void;
  enabled: boolean;
  onEnabledChange: (enabled: boolean) => void;
  demo: boolean;
}) {
  const hourValue = response?.hours[hour] ?? response?.hours[0] ?? "";
  const timeLabel = hourValue
    ? localHeatmapTime(hourValue, response?.hours[0] ?? hourValue)
    : "Choose a route to load forecast hours";
  const timezone = hourValue ? localTimezoneLabel(hourValue) : Intl.DateTimeFormat().resolvedOptions().timeZone;
  const counts = { Low: 0, Moderate: 0, High: 0, Severe: 0, "No Travel": 0, Missing: 0 };
  for (const point of response?.points ?? []) {
    const value = point.risk[hour] ?? null;
    if (value === null) counts.Missing += 1;
    else counts[HEATMAP_RISK_LEVELS[value]] += 1;
  }
  const worst = [...HEATMAP_RISK_LEVELS].reverse().find((level) => counts[level] > 0);
  const allLow = Boolean(response?.points.length) && counts.Low === response!.points.length;
  const hourStatus = allLow
    ? "No weather hazards along this route at this hour"
    : `At this hour: ${response?.points.length ?? 0} points, worst level ${worst ?? "No data"} · Low ${counts.Low} · Moderate ${counts.Moderate} · High ${counts.High} · Severe ${counts.Severe} · No Travel ${counts["No Travel"]} · missing ${counts.Missing}`;

  return (
    <section className="panel-section heatmap-section" aria-labelledby="heatmap-title">
      <div className="section-title-row">
        <h2 id="heatmap-title" className="section-title">
          {routeIndex === null ? "Weather forecast" : `Forecast along Route ${routeIndex + 1}`}
        </h2>
        {import.meta.env.DEV && demo && <span className="heatmap-demo-badge">DEMO DATA</span>}
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
      <div className="heatmap-range-ticks" aria-hidden="true">
        {[25, 50, 75, 100].map((position) => <i key={position} style={{ left: `${position}%` }} />)}
      </div>
      <div className="heatmap-range-ends" aria-hidden="true"><span>Departure</span><span>+48 h</span></div>
      {loading && <p className="heatmap-status" role="status">Loading forecast…</p>}
      {unavailable && <p className="heatmap-unavailable" role="status">Forecast overlay unavailable for this route</p>}
      {response && <p className="heatmap-hour-status" aria-live="polite">{hourStatus}</p>}
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
  const hours = Math.floor(minutes / 60);
  const remainingMinutes = minutes % 60;
  return hours ? `${hours} h ${remainingMinutes} min` : `${remainingMinutes} min`;
}

function formatMiles(miles: number): string {
  return `${Math.round(miles).toLocaleString()} mi`;
}

function formatCheckpointCount(count: number): string {
  return `${count.toLocaleString()} checkpoint${count === 1 ? "" : "s"}`;
}

function signedDelta(value: number): string {
  return `${value > 0 ? "+" : ""}${value.toLocaleString()}`;
}

function getRiskLabel(level: RiskLevel | null): RiskLabel {
  return level ?? "No data";
}

function riskStyle(level: RiskLabel): CSSProperties {
  return { "--risk-color": `var(${riskColorToken(level)})` } as CSSProperties;
}

function RiskBadge({ level }: { level: RiskLabel }) {
  return (
    <span className="risk-badge" style={riskStyle(level)}>
      <RiskIcon level={level} className="risk-icon risk-badge__symbol" />
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

  function clampLoadOnBlur() {
    const parsed = Number(value);
    const bounded = Number.isFinite(parsed) ? Math.min(100_000, Math.max(0, Math.trunc(parsed))) : 0;
    onChange(String(bounded));
  }

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
        onChange={(event) => onChange(event.target.value.replace(/^0+(?=\d)/, ""))}
        onBlur={clampLoadOnBlur}
      />
      <div className="load-meter" aria-hidden="true">
        <div className="load-meter__track"><span style={{ width: `${visualPercent}%` }} /></div>
        <i className="load-meter__tick load-meter__tick--30" />
        <i className="load-meter__tick load-meter__tick--40" />
        <span className="load-meter__limit">80k</span>
      </div>
      <div className="load-meter__labels" aria-hidden="true">
        <span>0</span><span>30k</span><span>40k</span><span>80k lb</span>
      </div>
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
  recommendationReason,
  recommendedRoute,
  nextRankedRoute,
  onSelect,
}: {
  route: AnalyzedRoute;
  recommended: boolean;
  selected: boolean;
  recommendationReason: string;
  recommendedRoute: AnalyzedRoute;
  nextRankedRoute: AnalyzedRoute | undefined;
  onSelect: () => void;
}) {
  const level = getRiskLabel(route.summary.max_level);
  const maxRiskCheckpoint = route.summary.max_level && route.summary.max_level !== "Low"
    ? route.checkpoints.find((checkpoint) => checkpoint.risk_level === route.summary.max_level && checkpoint.reasons.length > 0)
    : undefined;
  let explanation = recommendationReason;
  if (recommended && nextRankedRoute && /\b(shorter|longer)[.!?]*$/i.test(recommendationReason.trim())) {
    explanation = `${recommendationReason.trim().replace(/[.!?]+$/, "")} than Route ${nextRankedRoute.route_index + 1}`;
  } else if (!recommended) {
    const minutesDelta = Math.round(route.duration_s / 60) - Math.round(recommendedRoute.duration_s / 60);
    const milesDelta = Math.round(route.distance_mi) - Math.round(recommendedRoute.distance_mi);
    const durationComparison = minutesDelta === 0
      ? "same duration"
      : `${Math.abs(minutesDelta).toLocaleString()} min ${minutesDelta > 0 ? "longer" : "shorter"}`;
    const comparison = `${durationComparison}, ${signedDelta(milesDelta)} mi vs Route ${recommendedRoute.route_index + 1}`;
    const severeDelta = Math.round(route.summary.miles_by_level.Severe) - Math.round(recommendedRoute.summary.miles_by_level.Severe);
    const highDelta = Math.round(route.summary.miles_by_level.High) - Math.round(recommendedRoute.summary.miles_by_level.High);
    const higherRiskMiles = [
      severeDelta > 0 ? `+${severeDelta.toLocaleString()} Severe mi` : "",
      highDelta > 0 ? `+${highDelta.toLocaleString()} High mi` : "",
    ].filter(Boolean);
    explanation = [comparison, ...higherRiskMiles, maxRiskCheckpoint?.reasons[0] ?? ""].filter(Boolean).join(" · ");
  }

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
            <span className="route-card__missing">Weather missing · {formatMiles(route.summary.missing_weather_miles)} · {formatCheckpointCount(route.summary.missing_weather_count)}</span>
          )}
        </span>
        {explanation && <span className="route-card__reason">{explanation}</span>}
        <span className="route-card__footer">
          <span>
            {route.summary.unstable_checkpoints > 0
              ? `${formatCheckpointCount(route.summary.unstable_checkpoints)} may change risk if timing shifts by 2 h`
              : route.summary.unknown_stability_checkpoints > 0
                ? `Stability unknown for ${formatCheckpointCount(route.summary.unknown_stability_checkpoints)}`
                : "Stable within a ±2 h shift"}
            {route.summary.unknown_stability_checkpoints > 0 && route.summary.unstable_checkpoints > 0
              ? ` · ${formatCheckpointCount(route.summary.unknown_stability_checkpoints)} unknown`
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
  const demoHeatmap = stormDemoEnabled();
  const displayedHeatmapResponse = useMemo(
    () => heatmapResponse && demoHeatmap ? makeStormDemo(heatmapResponse) : heatmapResponse,
    [heatmapResponse, demoHeatmap],
  );

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
  const recommendedRoute = routes.find((route) => route.route_index === data?.recommended);
  const recommendedPosition = routes.findIndex((route) => route.route_index === data?.recommended);
  const nextRankedRoute = recommendedPosition >= 0 ? routes[recommendedPosition + 1] : undefined;
  const levelsDemoEnabled = import.meta.env.DEV && new URLSearchParams(window.location.search).get("demo") === "levels";

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
          heatmapResponse={displayedHeatmapResponse}
          heatmapHour={heatmapHour}
          heatmapEnabled={heatmapEnabled}
          heatmapRequestedAt={heatmapRequestedAt}
          heatmapRequestBytes={heatmapRequestBytes}
          heatmapResponseBytes={heatmapResponseBytes}
        />
        <div className="risk-legend" aria-label="Risk level legend">
          <span className="risk-legend__title">Checkpoint risk</span>
          {levelsDemoEnabled && (
            <span className="heatmap-demo-badge">DEMO DATA</span>
          )}
          {RISK_LEVELS.filter(({ label }) => label !== "No data" || heatmapEnabled || levelsDemoEnabled).map(({ label }) => (
            <span className="risk-legend__item" key={label} style={riskStyle(label)}>
              <RiskIcon level={label} className="risk-icon risk-legend__icon" />{label}
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
                        recommendationReason={data.recommendation_reason}
                        recommendedRoute={recommendedRoute ?? route}
                        nextRankedRoute={nextRankedRoute}
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
            response={displayedHeatmapResponse}
            routeIndex={activeRoute?.route_index ?? null}
            loading={heatmapLoading}
            unavailable={heatmapUnavailable}
            hour={heatmapHour}
            onHourChange={setHeatmapHour}
            enabled={heatmapEnabled}
            onEnabledChange={setHeatmapEnabled}
            demo={demoHeatmap}
          />
        </div>
      </aside>
    </main>
  );
}
