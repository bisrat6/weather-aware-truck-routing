import type { RiskLevel } from "./types";

export type RiskLabel = RiskLevel | "No data";
export type RiskShape = "circle-check" | "circle-alert" | "triangle-alert" | "shield-alert" | "cross" | "missing";

export const riskStyles: Record<RiskLabel, { label: RiskLabel; color: string; shape: RiskShape }> = {
  Low: { label: "Low", color: "--risk-low", shape: "circle-check" },
  Moderate: { label: "Moderate", color: "--risk-moderate", shape: "circle-alert" },
  High: { label: "High", color: "--risk-high", shape: "triangle-alert" },
  Severe: { label: "Severe", color: "--risk-severe", shape: "shield-alert" },
  "No Travel": { label: "No Travel", color: "--risk-no-travel", shape: "cross" },
  "No data": { label: "No data", color: "--risk-no-data", shape: "missing" },
};


export const RISK_LEVELS = Object.values(riskStyles);

export function riskColorToken(level: RiskLabel): string {
  return riskStyles[level].color;
}

export function getRiskColor(level: RiskLabel): string {
  return getComputedStyle(document.documentElement).getPropertyValue(riskStyles[level].color).trim();
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
