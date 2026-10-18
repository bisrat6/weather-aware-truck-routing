import { useEffect, useState, type CSSProperties, type FormEvent } from "react";
import { analyze } from "./api";
import { PlacePicker, type Place } from "./PlacePicker";
import { RouteMap } from "./RouteMap";
import { RISK_COLORS, RISK_LEVELS, type RiskLabel } from "./constants";
import type { AnalyzeResponse, AnalyzedRoute, RiskLevel } from "./types";
import "./App.css";

function defaultDeparture(): string {
  const date = new Date(Date.now() + 2 * 60 * 60 * 1000);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function formatDuration(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}`;
}

function getRiskLabel(level: RiskLevel | null): RiskLabel {
  return level ?? "No data";
}

function RiskMark({ level }: { level: RiskLabel }) {
  const details = RISK_LEVELS.find((entry) => entry.label === level)!;
  return (
    <span className="risk-mark" style={{ color: RISK_COLORS[level] }} aria-hidden="true">
      {details.marker}
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

function RouteEntry({
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
  const risk = getRiskLabel(route.summary.max_level);
  const levels = RISK_LEVELS.filter(
    (entry): entry is { label: RiskLevel; marker: string } => entry.label !== "No data",
  );

  return (
    <li className="route-list__item">
      <button
        type="button"
        className={`route-entry${selected ? " route-entry--selected" : ""}${recommended ? " route-entry--recommended" : ""}`}
        aria-pressed={selected}
        onClick={onSelect}
      >
        <span className="route-entry__topline">
          <span>
            <span className="route-entry__title">{routeTitle(route)}</span>
            {recommended && <span className="recommended-tag">Recommended</span>}
          </span>
          <span className="risk-label" style={{ "--risk-color": RISK_COLORS[risk] } as CSSProperties}>
            <RiskMark level={risk} /> {risk}
          </span>
        </span>
        <span className="route-entry__metrics">
          <span><strong>{route.distance_mi.toFixed(0)}</strong><small>mi</small></span>
          <span><strong>{formatDuration(route.duration_s)}</strong><small>h:mm</small></span>
          <span><strong>{route.summary.unstable_checkpoints}</strong><small>may shift</small></span>
        </span>
        <span className="route-entry__origin">{route.origin_label}</span>
        <span className="risk-breakdown" aria-label="Miles by risk level">
          {levels.map(({ label }) => (
            <span key={label} className="risk-breakdown__item">
              <RiskMark level={label} />
              <span>{label}</span>
              <strong>{route.summary.miles_by_level[label].toFixed(0)}</strong>
            </span>
          ))}
          <span className="risk-breakdown__item">
            <RiskMark level="No data" />
            <span>No data</span>
            <strong>{route.summary.missing_weather_miles.toFixed(0)}</strong>
          </span>
        </span>
        <span className="route-entry__meta">
          <span>ETA by {route.eta_method}</span>
          {route.summary.missing_weather_count > 0 && (
            <span className="route-entry__missing">
              Weather missing · {route.summary.missing_weather_miles.toFixed(1)} mi across{" "}
              {route.summary.missing_weather_count} checkpoints
            </span>
          )}
          <span>{route.summary.unstable_checkpoints} checkpoints may change risk if timing shifts by 2 h</span>
          {route.summary.unknown_stability_checkpoints > 0 && (
            <span>Stability unknown for {route.summary.unknown_stability_checkpoints}</span>
          )}
        </span>
        {recommended && <span className="recommendation-reason">{reason}</span>}
      </button>
    </li>
  );
}

export default function App() {
  const [origin, setOrigin] = useState<Place | null>(null);
  const [destination, setDestination] = useState<Place | null>(null);
  const [departure, setDeparture] = useState(defaultDeparture);
  const [loadLb, setLoadLb] = useState("0");
  const [data, setData] = useState<AnalyzeResponse | null>(null);
  const [selectedRoute, setSelectedRoute] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [waking, setWaking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!loading) return;
    const timer = window.setTimeout(() => setWaking(true), 3000);
    return () => window.clearTimeout(timer);
  }, [loading]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!origin || !destination) return;
    setLoading(true);
    setWaking(false);
    setError(null);
    setData(null);
    try {
      const result = await analyze({
        origin: { lat: origin.lat, lon: origin.lon },
        destination: { lat: destination.lat, lon: destination.lon },
        departure_utc: new Date(departure).toISOString(),
        load_lb: Number(loadLb),
        interval_miles: 25,
      });
      setData(result);
      setSelectedRoute(result.recommended);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The request failed.");
    } finally {
      setLoading(false);
      setWaking(false);
    }
  }

  const activeRoute = data?.routes.find((route) => route.route_index === selectedRoute)
    ?? data?.routes.find((route) => route.route_index === data.recommended)
    ?? null;

  return (
    <main className="workspace">
      <aside className="trip-panel" aria-label="Trip planning and route results">
        <header className="panel-header">
          <p className="eyebrow">Road weather · Route planning</p>
          <h1>Trip conditions</h1>
          <p className="panel-header__lede">Compare road risk before the wheels roll.</p>
        </header>

        <form className="trip-form" onSubmit={submit}>
          <div className="section-heading">
            <h2>Plan a trip</h2>
            <span>01 / 02</span>
          </div>
          <PlacePicker id="origin" label="Origin" value={origin} onChange={setOrigin} />
          <PlacePicker
            id="destination"
            label="Destination"
            value={destination}
            onChange={setDestination}
          />
          <div className="form-row">
            <label className="form-field">
              <span className="field-label">Departure</span>
              <input
                className="field-input"
                required
                type="datetime-local"
                value={departure}
                onChange={(event) => setDeparture(event.target.value)}
              />
            </label>
            <label className="form-field">
              <span className="field-label">Load · lb</span>
              <input
                className="field-input"
                required
                type="number"
                min="0"
                max="100000"
                step="1"
                value={loadLb}
                onChange={(event) => setLoadLb(event.target.value)}
              />
            </label>
          </div>
          <button className="analyze-button" type="submit" disabled={loading || !origin || !destination}>
            {loading ? "Reading forecast…" : "Analyze trip"}
            {!loading && <span aria-hidden="true">↗</span>}
          </button>
        </form>

        {(loading || error || data) && <div className="panel-rule" />}
        {loading && (
          <div className="loading-state" role="status" aria-live="polite">
            <span className="loading-state__spinner" aria-hidden="true" />
            <div>
              <strong>Scoring route options</strong>
              <p>{waking ? "Waking the server, this can take up to a minute." : "Checking route and forecast conditions…"}</p>
            </div>
            <div className="loading-state__track"><span /></div>
          </div>
        )}
        {error && <p className="error-banner" role="alert">{error}</p>}

        {data ? (
          <section className="results-section" aria-labelledby="results-title">
            <div className="section-heading results-heading">
              <div>
                <p className="eyebrow">02 / 02 · Options</p>
                <h2 id="results-title">Route comparison</h2>
              </div>
              <span className="route-count">{data.routes.length} routes</span>
            </div>
            {data.notices.length > 0 && (
              <div className="notice-banner" role="note">
                {data.notices.map((notice) => <p key={notice}>{notice}</p>)}
              </div>
            )}
            {data.routes.length ? (
              <ol className="route-list">
                {rankedRoutes(data).map((route) => (
                  <RouteEntry
                    key={route.route_index}
                    route={route}
                    recommended={route.route_index === data.recommended}
                    selected={route.route_index === (selectedRoute ?? data.recommended)}
                    reason={data.recommendation_reason}
                    onSelect={() => setSelectedRoute(route.route_index)}
                  />
                ))}
              </ol>
            ) : (
              <div className="empty-state">
                <h3>No route options returned</h3>
                <p>Try adjusting the locations or departure time.</p>
              </div>
            )}
          </section>
        ) : !loading && !error ? (
          <div className="empty-state empty-state--initial">
            <span className="empty-state__index">READY WHEN YOU ARE</span>
            <h2>Your route, with weather in view.</h2>
            <p>Choose a place from each search list to compare forecast risk along the drive.</p>
          </div>
        ) : null}
      </aside>

      <section className="map-stage" aria-label="Route map">
        <div className="map-heading">
          <span className="map-heading__status" />
          <span>{activeRoute ? `${routeTitle(activeRoute)} · ${getRiskLabel(activeRoute.summary.max_level)} conditions` : "Route overview"}</span>
        </div>
        <RouteMap
          routes={data?.routes ?? []}
          recommended={data?.recommended ?? null}
          selectedRoute={activeRoute}
        />
        <div className="risk-legend" aria-label="Risk level legend">
          <p className="risk-legend__title">Checkpoint risk</p>
          <div className="risk-legend__items">
            {RISK_LEVELS.map(({ label }) => (
              <span className="risk-legend__item" key={label}>
                <RiskMark level={label} /> {label}
              </span>
            ))}
          </div>
        </div>
        <div className="map-future-slot" aria-hidden="true" />
      </section>
    </main>
  );
}
