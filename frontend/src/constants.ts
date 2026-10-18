import type { RiskLevel } from "./types";

export type RiskLabel = RiskLevel | "No data";

export const RISK_LEVELS: { label: RiskLabel; marker: string }[] = [
  { label: "Low", marker: "○" },
  { label: "Moderate", marker: "◇" },
  { label: "High", marker: "△" },
  { label: "Severe", marker: "■" },
  { label: "No Travel", marker: "✕" },
  { label: "No data", marker: "?" },
];

const RISK_TOKEN: Record<RiskLabel, string> = {
  Low: "--risk-low",
  Moderate: "--risk-moderate",
  High: "--risk-high",
  Severe: "--risk-severe",
  "No Travel": "--risk-no-travel",
  "No data": "--risk-no-data",
};

export function riskColorToken(level: RiskLabel): string {
  return RISK_TOKEN[level];
}

export function getRiskColor(level: RiskLabel): string {
  return getComputedStyle(document.documentElement).getPropertyValue(RISK_TOKEN[level]).trim();
}

export function getThemeToken(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function getThemePixels(name: string): number {
  return Number.parseFloat(getThemeToken(name));
}

export const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/positron";
// Swap to "https://tiles.openfreemap.org/styles/liberty" to use the Liberty style.

export const MAP_INITIAL_VIEW = {
  longitude: -98.5,
  latitude: 39.8,
  zoom: 3,
};
