import { useEffect, useRef, useState } from "react";
import Map, { type MapRef } from "react-map-gl/maplibre";
import type { Feature, FeatureCollection, LineString } from "geojson";
import { GeoJSONSource } from "maplibre-gl";
import { BASEMAP_STYLE, MAP_INITIAL_VIEW, ROUTE_COLORS } from "./constants";
import type { AnalyzedRoute } from "./types";

interface RouteMapProps {
  routes: AnalyzedRoute[];
  recommended: number | null;
}

function routeFeature(route: AnalyzedRoute): Feature<LineString> {
  return {
    type: "Feature",
    properties: { route_index: route.route_index },
    geometry: { type: "LineString", coordinates: route.geometry.coordinates },
  };
}

function asCollection(route: AnalyzedRoute): FeatureCollection<LineString> {
  return { type: "FeatureCollection", features: [routeFeature(route)] };
}

export function RouteMap({ routes, recommended }: RouteMapProps) {
  const mapRef = useRef<MapRef>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const map = mapRef.current?.getMap();
    if (!loaded || !map) return;

    const routeIds = new Set(routes.map((route) => String(route.route_index)));
    for (const route of routes) {
      const sourceId = `route-${route.route_index}`;
      const layerId = `${sourceId}-line`;
      const source = map.getSource(sourceId);
      if (source instanceof GeoJSONSource) {
        source.setData(asCollection(route));
      } else {
        map.addSource(sourceId, { type: "geojson", data: asCollection(route) });
        map.addLayer({
          id: layerId,
          type: "line",
          source: sourceId,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": ROUTE_COLORS.alternatives[route.route_index % ROUTE_COLORS.alternatives.length],
            "line-width": 3,
          },
        });
      }
      map.setPaintProperty(
        layerId,
        "line-color",
        route.route_index === recommended
          ? ROUTE_COLORS.recommended
          : ROUTE_COLORS.alternatives[route.route_index % ROUTE_COLORS.alternatives.length],
      );
      map.setPaintProperty(layerId, "line-width", route.route_index === recommended ? 6 : 3);
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

    const coordinates = routes.flatMap((route) => route.geometry.coordinates);
    if (coordinates.length) {
      let west = Infinity;
      let south = Infinity;
      let east = -Infinity;
      let north = -Infinity;
      for (const [longitude, latitude] of coordinates) {
        west = Math.min(west, longitude);
        east = Math.max(east, longitude);
        south = Math.min(south, latitude);
        north = Math.max(north, latitude);
      }
      map.fitBounds(
        [
          [west, south],
          [east, north],
        ],
        { padding: 40, duration: 0 },
      );
    }
  }, [loaded, recommended, routes]);

  return (
    <Map
      ref={mapRef}
      initialViewState={MAP_INITIAL_VIEW}
      mapStyle={BASEMAP_STYLE}
      attributionControl={{}}
      onLoad={() => setLoaded(true)}
      style={{ width: "100%", height: 520 }}
    />
  );
}
