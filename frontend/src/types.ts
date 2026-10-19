export type RiskLevel = "Low" | "Moderate" | "High" | "Severe" | "No Travel";

export interface Coordinate {
  lat: number;
  lon: number;
}

export interface GeoJSONLine {
  coordinates: [longitude: number, latitude: number][];
}

export interface CheckpointWeather {
  wind_mph: number;
  rain_in_hr: number;
  snow_in_hr: number;
}

export interface Checkpoint {
  index: number;
  lat: number;
  lon: number;
  dist_mi: number;
  eta_utc: string;
  weather: CheckpointWeather | null;
  risk_level: RiskLevel | null;
  risk_level_minus_2h: RiskLevel | null;
  risk_level_plus_2h: RiskLevel | null;
  unstable: boolean | null;
  reasons: string[];
  owned_mi: number;
  weather_missing: boolean;
  missing_reason: string | null;
}

export interface RouteSummary {
  miles_by_level: Record<RiskLevel, number>;
  missing_weather_miles: number;
  max_level: RiskLevel | null;
  average_risk: number | null;
  duration_s: number;
  eta_method: string;
  missing_weather_count: number;
  unstable_checkpoints: number;
  unknown_stability_checkpoints: number;
}

export interface AnalyzedRoute {
  route_index: number;
  origin_label: string;
  source: "provider" | "generated";
  geometry: GeoJSONLine;
  distance_mi: number;
  duration_s: number;
  eta_method: string;
  checkpoints: Checkpoint[];
  summary: RouteSummary;
}

export interface AnalyzeResponse {
  routes: AnalyzedRoute[];
  ranking: number[];
  recommended: number;
  recommendation_reason: string;
  notices: string[];
}

export interface GeocodeResult {
  label: string;
  lat: number;
  lon: number;
}

export interface GeocodeResponse {
  results: GeocodeResult[];
}

export interface AnalyzeRequest {
  origin: Coordinate;
  destination: Coordinate;
  departure_utc: string;
  load_lb: number;
  interval_miles?: 10 | 25 | 50;
}

export interface HeatmapRequest {
  geometry: GeoJSONLine;
  departure_utc: string;
  load_lb: number;
}

export interface HeatmapPoint {
  lat: number;
  lon: number;
  risk: (0 | 1 | 2 | 3 | 4 | null)[];
}

export interface HeatmapResponse {
  hours: string[];
  points: HeatmapPoint[];
}
