import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import Map, {
  NavigationControl,
  Popup,
  type MapLayerMouseEvent,
  type MapRef,
} from "react-map-gl/maplibre";
import { GeoJSONSource } from "maplibre-gl";
import type { Feature, FeatureCollection, LineString, Point } from "geojson";
import {
  BASEMAP_STYLE,
  DESIGN,
  MAP_FIT_PADDING,
  MAP_INITIAL_VIEW,
  RISK_COLORS,
  RISK_LEVELS,
  type RiskLabel,
} from "./constants";
import type { AnalyzedRoute, Checkpoint, RiskLevel } from "./types";

const CHECKPOINT_SOURCE = "active-route-checkpoints";
const CHECKPOINT_LAYER = "checkpoint-risk-markers";

interface RouteMapProps {
  routes: AnalyzedRoute[];
  recommended: number | null;
  selectedRoute: AnalyzedRoute | null;
}

function riskLabel(level: RiskLevel | null): RiskLabel {
  return level ?? "No data";
}

function markerShape(label: RiskLabel): string {
  return RISK_LEVELS.find((level) => level.label === label)!.marker;
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

function checkpointCollection(route: AnalyzedRoute | null): FeatureCollection<Point> {
  return {
    type: "FeatureCollection",
    features: (route?.checkpoints ?? []).map((checkpoint) => {
      const level = riskLabel(checkpoint.risk_level);
      return {
        type: "Feature" as const,
        id: checkpoint.index,
        properties: {
          checkpoint_index: checkpoint.index,
          risk_label: level,
          marker: markerShape(level),
          marker_color: RISK_COLORS[level],
        },
        geometry: { type: "Point" as const, coordinates: [checkpoint.lon, checkpoint.lat] },
      };
    }),
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
        <span className="eyebrow">Checkpoint {checkpoint.index + 1}</span>
        <span className="risk-label" style={{ "--risk-color": RISK_COLORS[level] } as CSSProperties}>
          <span className="risk-mark" aria-hidden="true">{markerShape(level)}</span> {level}
        </span>
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

export function RouteMap({ routes, recommended, selectedRoute }: RouteMapProps) {
  const mapRef = useRef<MapRef>(null);
  const [loaded, setLoaded] = useState(false);
  const [popupCheckpoint, setPopupCheckpoint] = useState<Checkpoint | null>(null);

  useEffect(() => {
    setPopupCheckpoint(null);
  }, [selectedRoute?.route_index]);

  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!loaded || !map) return;

    const routeIds = new Set(routes.map((route) => String(route.route_index)));
    for (const route of routes) {
      const sourceId = `route-${route.route_index}`;
      const layerId = `${sourceId}-line`;
      const collection = routeCollection(route);
      const source = map.getSource(sourceId);
      if (source instanceof GeoJSONSource) {
        source.setData(collection);
      } else {
        map.addSource(sourceId, { type: "geojson", data: collection });
        map.addLayer({
          id: layerId,
          type: "line",
          source: sourceId,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": RISK_COLORS[riskLabel(route.summary.max_level)],
            "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 5, 3],
            "line-opacity": ["case", ["boolean", ["feature-state", "selected"], false], 0.96, 0.24],
            "line-blur": ["case", ["boolean", ["feature-state", "selected"], false], 0, 0.35],
          },
        });
      }
      map.setPaintProperty(layerId, "line-color", RISK_COLORS[riskLabel(route.summary.max_level)]);
    }

    for (const layer of map.getStyle().layers ?? []) {
      if (layer.id.startsWith("route-") && layer.id.endsWith("-line")) {
        const routeIndex = layer.id.slice("route-".length, -"-line".length);
        if (!routeIds.has(routeIndex)) {
          map.removeLayer(layer.id);
          if (map.getSource(`route-${routeIndex}`)) map.removeSource(`route-${routeIndex}`);
        }
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
        layout: {
          "text-field": ["get", "marker"],
          "text-font": ["Noto Sans Regular"],
          "text-size": Number.parseInt(DESIGN.type.sizeLg, 10),
          "text-allow-overlap": true,
          "text-ignore-placement": true,
          "text-anchor": "center",
        },
        paint: {
          "text-color": ["get", "marker_color"],
          "text-halo-color": DESIGN.color.mapMarkerHalo,
          "text-halo-width": 2,
        },
      });
    }
    map.moveLayer(CHECKPOINT_LAYER);

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
        { padding: MAP_FIT_PADDING, duration: 0 },
      );
    }
  }, [loaded, routes]);

  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!loaded || !map) return;
    for (const route of routes) {
      const source = `route-${route.route_index}`;
      const layer = `${source}-line`;
      if (!map.getSource(source) || !map.getLayer(layer)) continue;
      map.setFeatureState(
        { source, id: route.route_index },
        { selected: route.route_index === selectedRoute?.route_index },
      );
    }
    if (selectedRoute && map.getLayer(`route-${selectedRoute.route_index}-line`)) {
      map.moveLayer(`route-${selectedRoute.route_index}-line`, CHECKPOINT_LAYER);
    }
  }, [loaded, recommended, routes, selectedRoute]);

  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!loaded || !map) return;
    const source = map.getSource(CHECKPOINT_SOURCE);
    if (source instanceof GeoJSONSource) source.setData(checkpointCollection(selectedRoute));
  }, [loaded, selectedRoute]);

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
        attributionControl={{}}
        interactiveLayerIds={[CHECKPOINT_LAYER]}
        onClick={handleMapClick}
        onLoad={() => setLoaded(true)}
        style={{ width: "100%", height: "100%" }}
      >
        <NavigationControl position="top-right" showCompass={false} />
        {popupCheckpoint && (
          <CheckpointPopup checkpoint={popupCheckpoint} onClose={() => setPopupCheckpoint(null)} />
        )}
      </Map>
    </div>
  );
}
