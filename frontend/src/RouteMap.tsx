import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, type CSSProperties } from "react";
import Map, {
  NavigationControl,
  Popup,
  type MapLayerMouseEvent,
  type MapRef,
} from "react-map-gl/maplibre";
import { GeoJSONSource } from "maplibre-gl";
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
import {
  CHECKPOINT_ICON_NAME,
  DESTINATION_ICON_NAME,
  registerCheckpointIcon,
  registerDestinationIcon,
  registerRiskIcons,
  riskIconName,
  RiskIcon,
} from "./RiskIcon";


const CHECKPOINT_SOURCE = "active-route-checkpoints";
const CHECKPOINT_LAYER = "checkpoint-risk-markers";
const CHECKPOINT_LOW_LAYER = "checkpoint-risk-low-markers";
const ENDPOINT_SOURCE = "active-route-endpoints";
const ORIGIN_LAYER = "active-route-origin";
const DESTINATION_LAYER = "active-route-destination";
const HEATMAP_SOURCE = "forecast-risk-points";
const HEATMAP_LAYER = "forecast-risk-density";
const HEATMAP_CIRCLE_LAYER = "forecast-risk-circles";
const HEATMAP_SYMBOL_LAYER = "forecast-risk-symbols";

function heatmapIconExpression(hour: number): ExpressionSpecification {
  return [
    "case",
    ["==", ["at", hour, ["get", "risk_values"]], 4], riskIconName("No Travel"),
    ["==", ["at", hour, ["get", "risk_values"]], 3], riskIconName("Severe"),
    ["==", ["at", hour, ["get", "risk_values"]], 2], riskIconName("High"),
    ["==", ["at", hour, ["get", "risk_values"]], 1], riskIconName("Moderate"),
    riskIconName("Low"),
  ] as unknown as ExpressionSpecification;
}

function heatmapSymbolFilter(hour: number): ExpressionSpecification {
  return [
    "all",
    ["!=", ["at", hour, ["get", "risk_values"]], null],
    [">", ["at", hour, ["get", "risk_values"]], 0],
  ] as unknown as ExpressionSpecification;
}

interface HeatmapProperties {
  risk_values: (number | null)[];
}

type HeatmapFeature = Feature<Point, HeatmapProperties>;
type HeatmapCollection = FeatureCollection<Point, HeatmapProperties>;

const EMPTY_HEATMAP: HeatmapCollection = { type: "FeatureCollection", features: [] };

function heatmapCollection(response: HeatmapResponse): HeatmapCollection {
  return {
    type: "FeatureCollection",
    features: response.points.map((point, index): HeatmapFeature => ({
      type: "Feature",
      id: index,
      properties: {
        risk_values: point.risk,
      },
      geometry: { type: "Point", coordinates: [point.lon, point.lat] },
    })),
  };
}

function heatmapWeightExpression(hour: number): ExpressionSpecification {
  return [
    "case",
    ["==", ["at", hour, ["get", "risk_values"]], null], 0,
    ["==", ["at", hour, ["get", "risk_values"]], 0], 0.02,
    ["==", ["at", hour, ["get", "risk_values"]], 1], 0.45,
    ["==", ["at", hour, ["get", "risk_values"]], 2], 0.75,
    ["==", ["at", hour, ["get", "risk_values"]], 3], 0.95,
    ["==", ["at", hour, ["get", "risk_values"]], 4], 1.0,
    0,
  ] as unknown as ExpressionSpecification;
}

function heatmapColorExpression(hour: number): ExpressionSpecification {
  return [
    "case",
    ["==", ["at", hour, ["get", "risk_values"]], null], getRiskColor("No data"),
    ["==", ["at", hour, ["get", "risk_values"]], 0], getRiskColor("Low"),
    ["==", ["at", hour, ["get", "risk_values"]], 1], getRiskColor("Moderate"),
    ["==", ["at", hour, ["get", "risk_values"]], 2], getRiskColor("High"),
    ["==", ["at", hour, ["get", "risk_values"]], 3], getRiskColor("Severe"),
    ["==", ["at", hour, ["get", "risk_values"]], 4], getRiskColor("No Travel"),
    getRiskColor("No data"),
  ] as unknown as ExpressionSpecification;
}

function heatmapRadiusExpression(hour: number): ExpressionSpecification {
  return [
    "interpolate",
    ["linear"],
    ["zoom"],
    4,
    [
      "case",
      ["==", ["at", hour, ["get", "risk_values"]], 4], 8.0,
      ["==", ["at", hour, ["get", "risk_values"]], 3], 7.0,
      ["==", ["at", hour, ["get", "risk_values"]], 2], 6.0,
      ["==", ["at", hour, ["get", "risk_values"]], 1], 4.8,
      2.5,
    ],
    7,
    [
      "case",
      ["==", ["at", hour, ["get", "risk_values"]], 4], 12.0,
      ["==", ["at", hour, ["get", "risk_values"]], 3], 10.5,
      ["==", ["at", hour, ["get", "risk_values"]], 2], 9.0,
      ["==", ["at", hour, ["get", "risk_values"]], 1], 7.0,
      3.8,
    ],
    12,
    [
      "case",
      ["==", ["at", hour, ["get", "risk_values"]], 4], 16.0,
      ["==", ["at", hour, ["get", "risk_values"]], 3], 14.5,
      ["==", ["at", hour, ["get", "risk_values"]], 2], 12.5,
      ["==", ["at", hour, ["get", "risk_values"]], 1], 10.0,
      5.5,
    ],
  ] as unknown as ExpressionSpecification;
}

function heatmapStrokeWidthExpression(hour: number): ExpressionSpecification {
  return [
    "case",
    ["==", ["at", hour, ["get", "risk_values"]], null], 1.0,
    ["==", ["at", hour, ["get", "risk_values"]], 0], 1.0,
    2.2,
  ] as unknown as ExpressionSpecification;
}


function performanceEnabled(): boolean {
  return import.meta.env.DEV && new URLSearchParams(window.location.search).get("perf") === "1";
}

export interface RouteMapHandle {
  setHeatmapHour: (hour: number) => void;
  runBenchmark: (moves?: number) => void;
}

export interface RouteMapProps {
  routes: AnalyzedRoute[];
  recommended: number | null;
  selectedRoute: AnalyzedRoute | null;
  heatmapResponse: HeatmapResponse | null;
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
          marker_image: level === "Low" ? CHECKPOINT_ICON_NAME : riskIconName(level),
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

export const RouteMap = forwardRef<RouteMapHandle, RouteMapProps>(function RouteMap(
  {
    routes,
    recommended,
    selectedRoute,
    heatmapResponse,
    heatmapEnabled,
    heatmapRequestedAt,
    heatmapRequestBytes,
    heatmapResponseBytes,
  },
  ref,
) {
  const mapRef = useRef<MapRef>(null);
  const perfLoggedResponses = useRef(new WeakSet<HeatmapResponse>());
  const sliderSamples = useRef<number[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [styleRevision, setStyleRevision] = useState(0);
  const [popupCheckpoint, setPopupCheckpoint] = useState<Checkpoint | null>(null);
  const [perfSliderMs, setPerfSliderMs] = useState<number | null>(null);
  const [perfFirstRenderMs, setPerfFirstRenderMs] = useState<number | null>(null);
  const perfEnabled = performanceEnabled();
  const heatmapData = useMemo(
    () => (heatmapResponse ? heatmapCollection(heatmapResponse) : null),
    [heatmapResponse],
  );

  const recordSliderTiming = useCallback((durationMs: number) => {
    sliderSamples.current.push(durationMs);
    if (sliderSamples.current.length >= 100) {
      const window100 = sliderSamples.current.slice(-100);
      const avg = window100.reduce((acc, v) => acc + v, 0) / window100.length;
      const worst = Math.max(...window100);
      console.info(
        `[heatmap perf] Average: ${avg.toFixed(3)} ms, Worst: ${worst.toFixed(3)} ms over 100 slider moves`,
      );
      sliderSamples.current = [];
    }
  }, []);

  const applyHeatmapHour = useCallback(
    (hour: number) => {
      const map = mapRef.current?.getMap();
      if (!map || !map.isStyleLoaded()) return;

      const t0 = performance.now();
      if (map.getLayer(HEATMAP_LAYER)) {
        map.setPaintProperty(HEATMAP_LAYER, "heatmap-weight", heatmapWeightExpression(hour));
      }
      if (map.getLayer(HEATMAP_CIRCLE_LAYER)) {
        map.setPaintProperty(HEATMAP_CIRCLE_LAYER, "circle-color", heatmapColorExpression(hour));
        map.setPaintProperty(HEATMAP_CIRCLE_LAYER, "circle-radius", heatmapRadiusExpression(hour));
        map.setPaintProperty(HEATMAP_CIRCLE_LAYER, "circle-stroke-width", heatmapStrokeWidthExpression(hour));
      }
      if (map.getLayer(HEATMAP_SYMBOL_LAYER)) {
        map.setLayoutProperty(HEATMAP_SYMBOL_LAYER, "icon-image", heatmapIconExpression(hour));
        map.setFilter(HEATMAP_SYMBOL_LAYER, heatmapSymbolFilter(hour));
      }
      const elapsed = performance.now() - t0;
      recordSliderTiming(elapsed);
      if (perfEnabled) {
        setPerfSliderMs(elapsed);
      }
    },
    [perfEnabled, recordSliderTiming],
  );

  useImperativeHandle(
    ref,
    () => ({
      setHeatmapHour(hour: number) {
        applyHeatmapHour(hour);
      },
      runBenchmark(moves = 100) {
        console.info(`[heatmap perf] Running ${moves} slider updates benchmark...`);
        for (let i = 0; i < moves; i++) {
          applyHeatmapHour(i % 49);
        }
      },
    }),
    [applyHeatmapHour],
  );

  useEffect(() => {
    setPopupCheckpoint(null);
  }, [selectedRoute?.route_index]);

  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!loaded || !map) return;
    const onStyleLoad = () => {
      registerRiskIcons(map);
      registerCheckpointIcon(map);
      registerDestinationIcon(map);
      setStyleRevision((revision) => revision + 1);
    };
    map.on("style.load", onStyleLoad);
    return () => { map.off("style.load", onStyleLoad); };
  }, [loaded]);

  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!loaded || !map) return;
    registerRiskIcons(map);
    registerCheckpointIcon(map);
    registerDestinationIcon(map);

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
          // MapLibre only permits camera expressions like `zoom` at the top
          // level of a layout property; Low checkpoints use their own layer.
          "icon-size": ["interpolate", ["linear"], ["zoom"], 4, 0.58, 6, 0.72],
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
        minzoom: 4,
        filter: ["all", ["==", ["get", "risk_label"], "Low"], ["!=", ["get", "demo_marker"], true]],
        layout: {
          "icon-image": CHECKPOINT_ICON_NAME,
          "icon-size": ["interpolate", ["linear"], ["zoom"], 4, 0.52, 6, 0.68, 10, 0.85],
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
        layout: {
          "icon-image": DESTINATION_ICON_NAME,
          "icon-size": 1,
          "icon-anchor": "bottom",
          "icon-allow-overlap": true,
          "icon-ignore-placement": true,
        },
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
    } else if (existingHeatmapSource instanceof GeoJSONSource) {
      existingHeatmapSource.setData(heatmapData ?? EMPTY_HEATMAP);
    }

    if (!map.getLayer(HEATMAP_LAYER)) {
      map.addLayer(
        {
          id: HEATMAP_LAYER,
          type: "heatmap",
          source: HEATMAP_SOURCE,
          minzoom: 3,
          maxzoom: 8,
          paint: {
            "heatmap-weight": heatmapWeightExpression(0),
            "heatmap-intensity": ["interpolate", ["linear"], ["zoom"], 3, 1.25, 4, 1.3, 8, 1.1],
            "heatmap-radius": ["interpolate", ["linear"], ["zoom"], 3, 24, 6, 20, 8, 16],
            "heatmap-opacity": 0.85,
            "heatmap-color": [
              "interpolate", ["linear"], ["heatmap-density"],
              0, "rgba(0,0,0,0)",
              0.2, getRiskColor("Low"),
              0.4, getRiskColor("Moderate"),
              0.6, getRiskColor("High"),
              0.8, getRiskColor("Severe"),
              1, getRiskColor("No Travel"),
            ],
          } as unknown as Record<string, unknown>,
        },
        firstRouteLayer,
      );
    }

    if (!map.getLayer(HEATMAP_CIRCLE_LAYER)) {
      map.addLayer(
        {
          id: HEATMAP_CIRCLE_LAYER,
          type: "circle",
          source: HEATMAP_SOURCE,
          minzoom: 4,
          paint: {
            "circle-radius": heatmapRadiusExpression(0),
            "circle-color": heatmapColorExpression(0),
            "circle-stroke-color": getThemeToken("--surface"),
            "circle-stroke-width": heatmapStrokeWidthExpression(0),
            "circle-opacity": 0.95,
          },
        },
        firstRouteLayer,
      );
    }

    if (!map.getLayer(HEATMAP_SYMBOL_LAYER)) {
      map.addLayer(
        {
          id: HEATMAP_SYMBOL_LAYER,
          type: "symbol",
          source: HEATMAP_SOURCE,
          minzoom: 4,
          filter: heatmapSymbolFilter(0),
          layout: {
            "icon-image": heatmapIconExpression(0),
            "icon-size": ["interpolate", ["linear"], ["zoom"], 4, 0.58, 7, 0.75, 11, 0.95],
            "icon-allow-overlap": true,
            "icon-ignore-placement": true,
          },
        },
        firstRouteLayer,
      );
    }

    for (const obsolete of ["forecast-risk-low-circles", "forecast-risk-missing"]) {
      if (map.getLayer(obsolete)) map.removeLayer(obsolete);
    }

    for (const layerId of [HEATMAP_LAYER, HEATMAP_CIRCLE_LAYER, HEATMAP_SYMBOL_LAYER]) {
      if (map.getLayer(layerId)) {
        map.setLayoutProperty(layerId, "visibility", heatmapEnabled ? "visible" : "none");
      }
    }

    if (map.getLayer(HEATMAP_CIRCLE_LAYER) && map.getLayer(CHECKPOINT_LAYER)) {
      map.moveLayer(HEATMAP_CIRCLE_LAYER, CHECKPOINT_LAYER);
    }
    if (map.getLayer(HEATMAP_SYMBOL_LAYER) && map.getLayer(CHECKPOINT_LAYER)) {
      map.moveLayer(HEATMAP_SYMBOL_LAYER, CHECKPOINT_LAYER);
    }

    applyHeatmapHour(0);
  }, [loaded, heatmapData, heatmapEnabled, styleRevision, applyHeatmapHour]);

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
      map.moveLayer(lineId, map.getLayer(HEATMAP_CIRCLE_LAYER) ? HEATMAP_CIRCLE_LAYER : CHECKPOINT_LAYER);
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
          <span>Slider paint update: {perfSliderMs === null ? "—" : `${perfSliderMs.toFixed(3)} ms`}</span>
          <span>Request body: {heatmapRequestBytes === null ? "—" : `${(heatmapRequestBytes / 1024).toFixed(1)} KB`}</span>
          <span>Response body: {heatmapResponseBytes === null ? "—" : `${(heatmapResponseBytes / 1024).toFixed(1)} KB`}</span>
          <span>Request to first render: {perfFirstRenderMs === null ? "—" : `${perfFirstRenderMs.toFixed(1)} ms`}</span>
        </aside>
      )}
    </div>
  );
});
