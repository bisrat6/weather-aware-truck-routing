import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import Map, {
  NavigationControl,
  Popup,
  type MapLayerMouseEvent,
  type MapRef,
} from "react-map-gl/maplibre";
import { GeoJSONSource, type Map as MapLibreMap } from "maplibre-gl";
import { Compass } from "lucide-react";
import type { ExpressionSpecification } from "@maplibre/maplibre-gl-style-spec";
import type { Feature, FeatureCollection, LineString, Point } from "geojson";
import {
  BASEMAP_STYLE,
  getRiskColor,
  getThemeToken,
  MAP_INITIAL_VIEW,
  riskStyles,
  riskColorToken,
  type RiskLabel,
} from "./constants";
import type { AnalyzedRoute, Checkpoint, HeatmapResponse, RiskLevel } from "./types";
import { registerRiskIcons, riskIconName, RiskIcon } from "./RiskIcon";

const CHECKPOINT_SOURCE = "active-route-checkpoints";
const CHECKPOINT_LAYER = "checkpoint-risk-markers";
const CHECKPOINT_LOW_LAYER = "checkpoint-risk-low-markers";
const ENDPOINT_SOURCE = "active-route-endpoints";
const ORIGIN_LAYER = "active-route-origin";
const DESTINATION_LAYER = "active-route-destination";
const HEATMAP_SOURCE = "forecast-risk-points";
const HEATMAP_LAYER = "forecast-risk-density";
const HEATMAP_CIRCLE_LAYER = "forecast-risk-circles";
const HEATMAP_LOW_CIRCLE_LAYER = "forecast-risk-low-circles";
const HEATMAP_MISSING_LAYER = "forecast-risk-missing";
const HEATMAP_LABELS: RiskLabel[] = ["Low", "Moderate", "High", "Severe", "No Travel"];

interface HeatmapProperties {
  risk_values: (number | null)[];
  risk_level: number | null;
  heat_weight: number;
  weather_missing: boolean;
  marker_image: string;
  near_checkpoint: boolean;
}

type HeatmapFeature = Feature<Point, HeatmapProperties>;
type HeatmapCollection = FeatureCollection<Point, HeatmapProperties>;

const EMPTY_HEATMAP: HeatmapCollection = { type: "FeatureCollection", features: [] };

function heatmapCollection(response: HeatmapResponse): HeatmapCollection {
  return {
    type: "FeatureCollection",
    features: response.points.map((point, index): HeatmapFeature => {
      const risk = point.risk[0] ?? null;
      return {
        type: "Feature",
        id: index,
        properties: {
          risk_values: point.risk,
          risk_level: risk,
          heat_weight: risk ?? 0,
          weather_missing: risk === null,
          marker_image: riskIconName(risk === null ? "No data" : HEATMAP_LABELS[risk]),
          near_checkpoint: false,
        },
        geometry: { type: "Point", coordinates: [point.lon, point.lat] },
      };
    }),
  };
}

function performanceEnabled(): boolean {
  return import.meta.env.DEV && new URLSearchParams(window.location.search).get("perf") === "1";
}

interface RouteMapProps {
  routes: AnalyzedRoute[];
  recommended: number | null;
  selectedRoute: AnalyzedRoute | null;
  heatmapResponse: HeatmapResponse | null;
  heatmapHour: number;
  heatmapEnabled: boolean;
  heatmapRequestedAt: number | null;
  heatmapRequestBytes: number | null;
  heatmapResponseBytes: number | null;
}

function riskLabel(level: RiskLevel | null): RiskLabel {
  return level ?? "No data";
}

function CheckpointRiskBadge({ level }: { level: RiskLabel }) {
  return (
    <span className="risk-badge" style={{ "--risk-color": `var(${riskColorToken(level)})` } as CSSProperties}>
      <RiskIcon level={level} className="risk-icon risk-badge__symbol" />{level}
    </span>
  );
}

function routeFeature(route: AnalyzedRoute): Feature<LineString> {
  return {
    type: "Feature",
    id: route.route_index,
    properties: { route_index: route.route_index },
    geometry: { type: "LineString", coordinates: route.geometry.coordinates },
  };
}

function routeCollection(route: AnalyzedRoute): FeatureCollection<LineString> {
  return { type: "FeatureCollection", features: [routeFeature(route)] };
}

function routeRiskGradient(route: AnalyzedRoute): ExpressionSpecification {
  const checkpoints = route.checkpoints;
  const first = checkpoints[0];
  const totalMiles = checkpoints.at(-1)?.dist_mi || route.distance_mi;
  const expression: unknown[] = [
    "step",
    ["line-progress"],
    getRiskColor(riskLabel(first?.risk_level ?? route.summary.max_level)),
  ];
  let previousProgress = 0;
  for (let index = 1; index < checkpoints.length; index += 1) {
    const previous = checkpoints[index - 1];
    const checkpoint = checkpoints[index];
    const progress = ((previous.dist_mi + checkpoint.dist_mi) / 2) / totalMiles;
    if (!Number.isFinite(progress) || progress <= previousProgress || progress > 1) continue;
    expression.push(progress, getRiskColor(riskLabel(checkpoint.risk_level)));
    previousProgress = progress;
  }
  return expression as unknown as ExpressionSpecification;
}

const DEMO_MARKER_LEVELS = Object.keys(riskStyles) as RiskLabel[];

function levelDemoEnabled(): boolean {
  return import.meta.env.DEV && new URLSearchParams(window.location.search).get("demo") === "levels";
}

function checkpointCollection(route: AnalyzedRoute | null, demoLevels = false): FeatureCollection<Point> {
  return {
    type: "FeatureCollection",
    features: (route?.checkpoints ?? []).map((checkpoint, index) => {
      const level = demoLevels
        ? DEMO_MARKER_LEVELS[index % DEMO_MARKER_LEVELS.length]
        : riskLabel(checkpoint.risk_level);
      return {
        type: "Feature" as const,
        id: checkpoint.index,
        properties: {
          checkpoint_index: checkpoint.index,
          risk_label: level,
          marker_image: riskIconName(level),
          demo_marker: demoLevels,
        },
        geometry: { type: "Point" as const, coordinates: [checkpoint.lon, checkpoint.lat] },
      };
    }),
  };
}

function endpointCollection(route: AnalyzedRoute | null): FeatureCollection<Point> {
  const coordinates = route?.geometry.coordinates ?? [];
  if (coordinates.length < 2) return { type: "FeatureCollection", features: [] };
  const [origin, destination] = [coordinates[0], coordinates[coordinates.length - 1]];
  return {
    type: "FeatureCollection",
    features: [
      { type: "Feature", properties: { kind: "origin" }, geometry: { type: "Point", coordinates: origin } },
      { type: "Feature", properties: { kind: "destination" }, geometry: { type: "Point", coordinates: destination } },
    ],
  };
}

function updateHeatmapFeatureState(
  map: MapLibreMap,
  data: HeatmapCollection,
  hour: number,
  route: AnalyzedRoute | null,
): void {
  const checkpointPixels = (route?.checkpoints ?? []).map((checkpoint) => map.project([checkpoint.lon, checkpoint.lat]));
  for (const feature of data.features) {
    const level = feature.properties.risk_values[hour] ?? null;
    const pixel = map.project(feature.geometry.coordinates as [number, number]);
    feature.properties.risk_level = level;
    feature.properties.heat_weight = level ?? 0;
    feature.properties.weather_missing = level === null;
    feature.properties.marker_image = riskIconName(level === null ? "No data" : HEATMAP_LABELS[level]);
    feature.properties.near_checkpoint = checkpointPixels.some(
      (checkpointPixel) => Math.hypot(pixel.x - checkpointPixel.x, pixel.y - checkpointPixel.y) <= 5,
    );
  }
}

function localEta(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function utcEta(value: string): string {
  return `${new Date(value).toISOString().replace("T", " ").replace(".000Z", " UTC")}`;
}

function CheckpointPopup({
  checkpoint,
  onClose,
}: {
  checkpoint: Checkpoint;
  onClose: () => void;
}) {
  const level = riskLabel(checkpoint.risk_level);
  return (
    <Popup
      latitude={checkpoint.lat}
      longitude={checkpoint.lon}
      anchor="bottom"
      closeOnClick={false}
      onClose={onClose}
      className="checkpoint-popup"
    >
      <div className="checkpoint-popup__heading">
        <strong>Checkpoint {checkpoint.index + 1}</strong>
        <CheckpointRiskBadge level={level} />
      </div>
      <dl className="checkpoint-popup__times">
        <div><dt>Local ETA</dt><dd>{localEta(checkpoint.eta_utc)}</dd></div>
        <div><dt>UTC</dt><dd>{utcEta(checkpoint.eta_utc)}</dd></div>
      </dl>
      {checkpoint.weather ? (
        <dl className="checkpoint-popup__weather">
          <div><dt>Wind</dt><dd>{checkpoint.weather.wind_mph.toFixed(1)} mph</dd></div>
          <div><dt>Rain</dt><dd>{checkpoint.weather.rain_in_hr.toFixed(2)} in/hr</dd></div>
          <div><dt>Snow</dt><dd>{checkpoint.weather.snow_in_hr.toFixed(2)} in/hr</dd></div>
        </dl>
      ) : (
        <p className="checkpoint-popup__missing">
          No weather data{checkpoint.missing_reason ? ` · ${checkpoint.missing_reason}` : ""}
        </p>
      )}
      <div className="checkpoint-popup__shifts">
        <span>−2 h <strong>{checkpoint.risk_level_minus_2h ?? "No data"}</strong></span>
        <span>+2 h <strong>{checkpoint.risk_level_plus_2h ?? "No data"}</strong></span>
      </div>
      {checkpoint.unstable === true ? (
        <p className="checkpoint-popup__stability checkpoint-popup__stability--change">
          May change if timing shifts by 2 h
        </p>
      ) : checkpoint.unstable === null ? (
        <p className="checkpoint-popup__stability">Stability unknown</p>
      ) : (
        <p className="checkpoint-popup__stability">Stable for a 2 h shift</p>
      )}
      {checkpoint.reasons.length > 0 && (
        <div className="checkpoint-popup__reasons">
          <h3>Risk reasons</h3>
          <ul>{checkpoint.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
        </div>
      )}
    </Popup>
  );
}

export function RouteMap({
  routes,
  recommended,
  selectedRoute,
  heatmapResponse,
  heatmapHour,
  heatmapEnabled,
  heatmapRequestedAt,
  heatmapRequestBytes,
  heatmapResponseBytes,
}: RouteMapProps) {
  const mapRef = useRef<MapRef>(null);
  const heatmapMeasureId = useRef(0);
  const perfLoggedResponses = useRef(new WeakSet<HeatmapResponse>());
  const [loaded, setLoaded] = useState(false);
  const [styleRevision, setStyleRevision] = useState(0);
  const [popupCheckpoint, setPopupCheckpoint] = useState<Checkpoint | null>(null);
  const [perfSliderMs, setPerfSliderMs] = useState<number | null>(null);
  const [perfFirstRenderMs, setPerfFirstRenderMs] = useState<number | null>(null);
  const perfEnabled = performanceEnabled();
  const heatmapData = useMemo(
    () => heatmapResponse ? heatmapCollection(heatmapResponse) : null,
    [heatmapResponse],
  );

  useEffect(() => {
    setPopupCheckpoint(null);
  }, [selectedRoute?.route_index]);

  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!loaded || !map) return;
    const onStyleLoad = () => {
      registerRiskIcons(map);
      setStyleRevision((revision) => revision + 1);
    };
    map.on("style.load", onStyleLoad);
    return () => { map.off("style.load", onStyleLoad); };
  }, [loaded]);

  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!loaded || !map) return;
    registerRiskIcons(map);

    const routeIds = new Set(routes.map((route) => String(route.route_index)));
    for (const route of routes) {
      const sourceId = `route-${route.route_index}`;
      const layerId = `${sourceId}-line`;
      const collection = routeCollection(route);
      const source = map.getSource(sourceId);
      if (source instanceof GeoJSONSource) {
        source.setData(collection);
      } else {
        map.addSource(sourceId, { type: "geojson", data: collection, lineMetrics: true });
        map.addLayer({
          id: `${sourceId}-halo`,
          type: "line",
          source: sourceId,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": getThemeToken("--surface"),
            "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 10, 0],
            "line-opacity": ["case", ["boolean", ["feature-state", "selected"], false], 1, 0],
          },
        });
        map.addLayer({
          id: layerId,
          type: "line",
          source: sourceId,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": getRiskColor(riskLabel(route.summary.max_level)),
            "line-gradient": routeRiskGradient(route),
            "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 6, 3],
            "line-opacity": ["case", ["boolean", ["feature-state", "selected"], false], 1, 0.6],
          },
        });
      }
      map.setPaintProperty(layerId, "line-color", getRiskColor(riskLabel(route.summary.max_level)));
      map.setPaintProperty(layerId, "line-gradient", routeRiskGradient(route));
    }

    for (const layer of map.getStyle().layers ?? []) {
      if (!layer.id.startsWith("route-")) continue;
      const routeIndex = layer.id.slice("route-".length).replace(/-(?:halo|line)$/, "");
      if (!routeIds.has(routeIndex)) map.removeLayer(layer.id);
    }
    for (const sourceId of Object.keys(map.getStyle().sources)) {
      if (sourceId.startsWith("route-") && !routeIds.has(sourceId.slice("route-".length))) {
        map.removeSource(sourceId);
      }
    }

    if (!map.getSource(CHECKPOINT_SOURCE)) {
      map.addSource(CHECKPOINT_SOURCE, {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
    }
    if (!map.getLayer(CHECKPOINT_LAYER)) {
      map.addLayer({
        id: CHECKPOINT_LAYER,
        type: "symbol",
        source: CHECKPOINT_SOURCE,
        minzoom: 4,
        filter: ["any", ["!=", ["get", "risk_label"], "Low"], ["==", ["get", "demo_marker"], true]],
        layout: {
          "icon-image": ["get", "marker_image"],
          "icon-size": ["match", ["get", "risk_label"], "Low", 0.42, ["interpolate", ["linear"], ["zoom"], 3, 0.35, 6, 0.67]],
          "icon-allow-overlap": true,
          "icon-ignore-placement": true,
        },
      });
    }
    if (!map.getLayer(CHECKPOINT_LOW_LAYER)) {
      map.addLayer({
        id: CHECKPOINT_LOW_LAYER,
        type: "symbol",
        source: CHECKPOINT_SOURCE,
        minzoom: 6,
        filter: ["all", ["==", ["get", "risk_label"], "Low"], ["!=", ["get", "demo_marker"], true]],
        layout: {
          "icon-image": ["get", "marker_image"],
          "icon-size": 0.42,
          "icon-allow-overlap": true,
          "icon-ignore-placement": true,
        },
      });
    }

    if (!map.getSource(ENDPOINT_SOURCE)) {
      map.addSource(ENDPOINT_SOURCE, { type: "geojson", data: endpointCollection(null) });
    }
    if (!map.getLayer(ORIGIN_LAYER)) {
      map.addLayer({
        id: ORIGIN_LAYER,
        type: "circle",
        source: ENDPOINT_SOURCE,
        filter: ["==", ["get", "kind"], "origin"],
        paint: {
          "circle-radius": 8,
          "circle-color": getThemeToken("--surface"),
          "circle-stroke-color": getThemeToken("--primary"),
          "circle-stroke-width": 3,
        },
      });
    }
    if (!map.getLayer(DESTINATION_LAYER)) {
      map.addLayer({
        id: DESTINATION_LAYER,
        type: "symbol",
        source: ENDPOINT_SOURCE,
        filter: ["==", ["get", "kind"], "destination"],
        layout: { "text-field": "▼", "text-font": ["Noto Sans Regular"], "text-size": 18, "text-anchor": "bottom", "text-allow-overlap": true },
        paint: { "text-color": getThemeToken("--primary"), "text-halo-color": getThemeToken("--surface"), "text-halo-width": 2 },
      });
    }
    map.moveLayer(CHECKPOINT_LAYER);
    map.moveLayer(CHECKPOINT_LOW_LAYER);
    map.moveLayer(ORIGIN_LAYER);
    map.moveLayer(DESTINATION_LAYER);

    let west = Infinity;
    let south = Infinity;
    let east = -Infinity;
    let north = -Infinity;
    let hasCoordinates = false;
    for (const route of routes) {
      for (const [longitude, latitude] of route.geometry.coordinates) {
        hasCoordinates = true;
        west = Math.min(west, longitude);
        east = Math.max(east, longitude);
        south = Math.min(south, latitude);
        north = Math.max(north, latitude);
      }
    }
    if (hasCoordinates) {
      map.fitBounds(
        [[west, south], [east, north]],
        {
          padding: window.innerWidth < 768
            ? { top: 20, right: 20, bottom: window.innerHeight * 0.62, left: 20 }
            : { top: 76, right: 24, bottom: 100, left: 380 },
          duration: 0,
        },
      );
    }
  }, [loaded, routes, styleRevision]);

  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!loaded || !map) return;

    const layers = map.getStyle().layers ?? [];
    const firstRouteLayer = layers.find((layer) => layer.id.startsWith("route-") && layer.id.endsWith("-halo"))?.id;
    const existingHeatmapSource = map.getSource(HEATMAP_SOURCE);
    if (!existingHeatmapSource) {
      map.addSource(HEATMAP_SOURCE, { type: "geojson", data: heatmapData ?? EMPTY_HEATMAP });
    } else if (!heatmapData && existingHeatmapSource instanceof GeoJSONSource) {
      existingHeatmapSource.setData(EMPTY_HEATMAP);
    }
    if (!map.getLayer(HEATMAP_LAYER)) {
      map.addLayer({
        id: HEATMAP_LAYER,
        type: "heatmap",
        source: HEATMAP_SOURCE,
        maxzoom: 9,
        paint: {
          "heatmap-weight": [
            "case",
            ["boolean", ["get", "weather_missing"], false], 0,
            ["interpolate", ["linear"], ["get", "heat_weight"],
              0, 0, 1, 0.3, 2, 0.6, 3, 0.85, 4, 1],
          ],
          "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 3, 1, 6, 1, 9, 1],
          "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 3, 22, 6, 15, 9, 8],
          "heatmap-opacity": ["interpolate", ["linear"], ["zoom"], 0, 0.85, 8, 0.9],
          "heatmap-color": [
            "interpolate", ["linear"], ["heatmap-density"],
            0, "rgba(0,0,0,0)",
            0.3, "#e3b323",
            0.6, getRiskColor("High"),
            0.85, getRiskColor("Severe"),
            1, getRiskColor("No Travel"),
          ],
        } as unknown as Record<string, unknown>,
      }, firstRouteLayer);
    }
    if (!map.getLayer(HEATMAP_CIRCLE_LAYER)) {
      map.addLayer({
        id: HEATMAP_CIRCLE_LAYER,
        type: "symbol",
        source: HEATMAP_SOURCE,
        minzoom: 3,
        filter: ["all", ["==", ["get", "weather_missing"], false], [">", ["get", "risk_level"], 0], ["==", ["get", "near_checkpoint"], false]],
        layout: {
          "icon-image": ["get", "marker_image"],
          "icon-size": 0.67,
          "icon-allow-overlap": true,
          "icon-ignore-placement": true,
        },
      }, firstRouteLayer);
    }
    if (!map.getLayer(HEATMAP_LOW_CIRCLE_LAYER)) {
      map.addLayer({
        id: HEATMAP_LOW_CIRCLE_LAYER,
        type: "circle",
        source: HEATMAP_SOURCE,
        minzoom: 7,
        filter: ["all", ["==", ["get", "weather_missing"], false], ["==", ["get", "risk_level"], 0], ["==", ["get", "near_checkpoint"], false]],
        paint: {
          "circle-radius": 2.5,
          "circle-color": getRiskColor("No data"),
          "circle-opacity": 0.85,
        },
      }, firstRouteLayer);
    }
    if (!map.getLayer(HEATMAP_MISSING_LAYER)) {
      map.addLayer({
        id: HEATMAP_MISSING_LAYER,
        type: "symbol",
        source: HEATMAP_SOURCE,
        minzoom: 3,
        filter: ["all", ["==", ["get", "weather_missing"], true], ["==", ["get", "near_checkpoint"], false]],
        layout: {
          "icon-image": ["get", "marker_image"],
          "icon-size": 0.67,
          "icon-allow-overlap": true,
          "icon-ignore-placement": true,
        },
      }, firstRouteLayer);
    }
    for (const layerId of [HEATMAP_LAYER, HEATMAP_CIRCLE_LAYER, HEATMAP_LOW_CIRCLE_LAYER, HEATMAP_MISSING_LAYER]) {
      map.setLayoutProperty(layerId, "visibility", heatmapEnabled ? "visible" : "none");
    }
  }, [loaded, heatmapData, heatmapEnabled, styleRevision]);

  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!loaded || !map || !heatmapData) return;
    let frame = 0;
    frame = window.requestAnimationFrame(() => {
      const source = map.getSource(HEATMAP_SOURCE);
      if (!(source instanceof GeoJSONSource)) return;
      updateHeatmapFeatureState(map, heatmapData, heatmapHour, selectedRoute);
      if (perfEnabled) {
        const id = ++heatmapMeasureId.current;
        const startMark = `heatmap-setdata-start-${id}`;
        const endMark = `heatmap-setdata-end-${id}`;
        const measureName = `heatmap-slider-setdata-${id}`;
        performance.mark(startMark);
        source.setData(heatmapData);
        performance.mark(endMark);
        performance.measure(measureName, startMark, endMark);
        const duration = performance.getEntriesByName(measureName, "measure").at(-1)?.duration ?? 0;
        setPerfSliderMs(duration);
        console.info(`[heatmap perf] slider setData: ${duration.toFixed(2)} ms`);
        performance.clearMarks(startMark);
        performance.clearMarks(endMark);
        performance.clearMeasures(measureName);
      } else {
        source.setData(heatmapData);
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [loaded, heatmapData, heatmapHour, perfEnabled, selectedRoute, styleRevision]);

  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!loaded || !map || !heatmapData) return;
    let frame = 0;
    const refreshProximity = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        const source = map.getSource(HEATMAP_SOURCE);
        if (!(source instanceof GeoJSONSource)) return;
        updateHeatmapFeatureState(map, heatmapData, heatmapHour, selectedRoute);
        source.setData(heatmapData);
      });
    };
    map.on("zoomend", refreshProximity);
    map.on("rotateend", refreshProximity);
    map.on("pitchend", refreshProximity);
    refreshProximity();
    return () => {
      window.cancelAnimationFrame(frame);
      map.off("zoomend", refreshProximity);
      map.off("rotateend", refreshProximity);
      map.off("pitchend", refreshProximity);
    };
  }, [loaded, heatmapData, heatmapHour, selectedRoute, styleRevision]);

  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!perfEnabled || !loaded || !map) return;
    if (!heatmapResponse || !heatmapData) {
      if (heatmapRequestedAt !== null) setPerfFirstRenderMs(null);
      return;
    }
    if (perfLoggedResponses.current.has(heatmapResponse)) return;
    perfLoggedResponses.current.add(heatmapResponse);
    setPerfFirstRenderMs(null);
    if (heatmapRequestBytes !== null) {
      console.info(`[heatmap perf] request body: ${(heatmapRequestBytes / 1024).toFixed(1)} KB`);
    }
    if (heatmapResponseBytes !== null) {
      console.info(`[heatmap perf] response body: ${(heatmapResponseBytes / 1024).toFixed(1)} KB`);
    }
    if (heatmapRequestedAt === null) return;
    const onIdle = () => {
      const duration = performance.now() - heatmapRequestedAt;
      setPerfFirstRenderMs(duration);
      console.info(`[heatmap perf] request to first map render: ${duration.toFixed(1)} ms`);
    };
    map.once("idle", onIdle);
    return () => { map.off("idle", onIdle); };
  }, [perfEnabled, loaded, heatmapResponse, heatmapData, heatmapRequestedAt, heatmapRequestBytes, heatmapResponseBytes]);

  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!loaded || !map) return;
    for (const route of routes) {
      const source = `route-${route.route_index}`;
      const layer = `${source}-line`;
      if (!map.getSource(source) || !map.getLayer(layer)) continue;
      map.setFeatureState(
        { source, id: route.route_index },
        { selected: route.route_index === (selectedRoute?.route_index ?? recommended) },
      );
    }
    if (selectedRoute && map.getLayer(`route-${selectedRoute.route_index}-line`)) {
      const lineId = `route-${selectedRoute.route_index}-line`;
      const haloId = `route-${selectedRoute.route_index}-halo`;
      map.moveLayer(lineId, CHECKPOINT_LAYER);
      map.moveLayer(haloId, lineId);
    }
  }, [loaded, recommended, routes, selectedRoute, styleRevision]);

  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!loaded || !map) return;
    const source = map.getSource(CHECKPOINT_SOURCE);
    if (source instanceof GeoJSONSource) source.setData(checkpointCollection(selectedRoute, levelDemoEnabled()));
    const endpointSource = map.getSource(ENDPOINT_SOURCE);
    if (endpointSource instanceof GeoJSONSource) endpointSource.setData(endpointCollection(selectedRoute));
  }, [loaded, selectedRoute, styleRevision]);

  const handleMapClick = useCallback(
    (event: MapLayerMouseEvent) => {
      const feature = event.features?.[0];
      if (!feature || !selectedRoute) return;
      const checkpointIndex = Number(feature.properties?.checkpoint_index);
      const checkpoint = selectedRoute.checkpoints.find((item) => item.index === checkpointIndex);
      if (checkpoint) setPopupCheckpoint(checkpoint);
    },
    [selectedRoute],
  );

  return (
    <div className="map-canvas">
      <Map
        ref={mapRef}
        initialViewState={MAP_INITIAL_VIEW}
        mapStyle={BASEMAP_STYLE}
        attributionControl={{ compact: false, customAttribution: "Weather data: Open-Meteo" }}
        interactiveLayerIds={[CHECKPOINT_LAYER, CHECKPOINT_LOW_LAYER]}
        onClick={handleMapClick}
        onLoad={() => setLoaded(true)}
        style={{ width: "100%", height: "100%" }}
      >
        <NavigationControl position="bottom-right" showCompass={false} />
        {popupCheckpoint && (
          <CheckpointPopup checkpoint={popupCheckpoint} onClose={() => setPopupCheckpoint(null)} />
        )}
      </Map>
      <button
        className="map-compass-reset"
        type="button"
        aria-label="Reset map orientation to north"
        title="Reset north"
        onClick={() => mapRef.current?.getMap().easeTo({ bearing: 0, pitch: 0, duration: 300 })}
      >
        <Compass size={18} aria-hidden="true" />
      </button>
      {perfEnabled && (
        <aside className="heatmap-perf" aria-label="Heatmap performance measurements">
          <strong>Heatmap performance</strong>
          <span>Slider setData: {perfSliderMs === null ? "—" : `${perfSliderMs.toFixed(2)} ms`}</span>
          <span>Request body: {heatmapRequestBytes === null ? "—" : `${(heatmapRequestBytes / 1024).toFixed(1)} KB`}</span>
          <span>Response body: {heatmapResponseBytes === null ? "—" : `${(heatmapResponseBytes / 1024).toFixed(1)} KB`}</span>
          <span>Request to first render: {perfFirstRenderMs === null ? "—" : `${perfFirstRenderMs.toFixed(1)} ms`}</span>
        </aside>
      )}
    </div>
  );
}
