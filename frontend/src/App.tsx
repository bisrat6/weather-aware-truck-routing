import { useEffect, useState, type FormEvent } from "react";
import { analyze, ApiClientError } from "./api";
import { PlacePicker, type Place } from "./PlacePicker";
import { RouteMap } from "./RouteMap";
import type { AnalyzeResponse } from "./types";

function defaultDeparture(): string {
  const date = new Date(Date.now() + 2 * 60 * 60 * 1000);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function formatDuration(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return hours ? `${hours} h ${remainder} min` : `${remainder} min`;
}

export default function App() {
  const [origin, setOrigin] = useState<Place | null>(null);
  const [destination, setDestination] = useState<Place | null>(null);
  const [departure, setDeparture] = useState(defaultDeparture);
  const [loadLb, setLoadLb] = useState("0");
  const [data, setData] = useState<AnalyzeResponse | null>(null);
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
    setLoading(true);
    setWaking(false);
    setError(null);
    setData(null);
    try {
      if (!origin || !destination) return;
      const result = await analyze({
        origin: { lat: origin.lat, lon: origin.lon },
        destination: { lat: destination.lat, lon: destination.lon },
        departure_utc: new Date(departure).toISOString(),
        load_lb: Number(loadLb),
        interval_miles: 25,
      });
      setData(result);
    } catch (cause) {
      if (
        cause instanceof ApiClientError &&
        ["no_route_found", "route_too_far", "route_not_supported"].includes(cause.code ?? "")
      ) {
        setError(cause.message || "No driving route found between these places.");
      } else {
        setError(cause instanceof Error ? cause.message : "The request failed.");
      }
    } finally {
      setLoading(false);
      setWaking(false);
    }
  }

  return (
    <main>
      <h1>Weather-aware truck routing</h1>
      <form onSubmit={submit}>
        <p>
          <PlacePicker id="origin" label="Origin" value={origin} onChange={setOrigin} />
        </p>
        <p>
          <PlacePicker
            id="destination"
            label="Destination"
            value={destination}
            onChange={setDestination}
          />
        </p>
        <p>
          <label>
            Departure local time{" "}
            <input
              required
              type="datetime-local"
              value={departure}
              onChange={(event) => setDeparture(event.target.value)}
            />
          </label>
        </p>
        <p>
          <label>
            Load (lb, 0–100,000){" "}
            <input
              required
              type="number"
              min="0"
              max="100000"
              step="1"
              value={loadLb}
              onChange={(event) => setLoadLb(event.target.value)}
            />
          </label>
        </p>
        <button type="submit" disabled={loading || !origin || !destination}>
          {loading ? "Analyzing…" : "Analyze routes"}
        </button>
      </form>

      {waking && <p role="status">The server may be waking up. This can take a little while.</p>}
      {error && <p role="alert">{error}</p>}

      {data && (
        <section>
          <h2>Results</h2>
          <p>Routes available: {data.routes.length}</p>
          {data.notices.map((notice, index) => <p key={`${index}-${notice}`}>Notice: {notice}</p>)}
          <p>
            Recommended route index: {data.recommended}.{" "}
            {data.recommendation_reason}
          </p>
          {data.routes.map((route) => (
            <p key={route.route_index}>
              Route {route.route_index + 1}: {route.distance_mi.toFixed(1)} miles;{" "}
              {formatDuration(route.duration_s)}; max level {route.summary.max_level ?? "Unknown"};{" "}
              {route.summary.unstable_checkpoints} unstable checkpoints;{" "}
              {route.summary.unknown_stability_checkpoints} unknown stability checkpoints
            </p>
          ))}
        </section>
      )}

      <section aria-label="Routes map">
        <RouteMap routes={data?.routes ?? []} recommended={data?.recommended ?? null} />
      </section>
    </main>
  );
}
