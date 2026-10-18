import type { RiskLevel } from "./types";

export type RiskLabel = RiskLevel | "No data";

export const DESIGN = {
  color: {
    canvas: "#edf1f3",
    panel: "#ffffff",
    surface: "#ffffff",
    ink: "#182b35",
    muted: "#536874",
    quiet: "#687d87",
    line: "#dce4e8",
    lineStrong: "#c4d1d7",
    accent: "#176b9a",
    accentSoft: "#e9f3f8",
    accentDark: "#105375",
    focus: "#075d92",
    alert: "#9a3d25",
    alertSoft: "#fff0e9",
    warning: "#785400",
    warningSoft: "#fbf1d9",
    shadow: "#19372e24",
    mapMarkerHalo: "#fbfcfa",
    riskLow: "#176b58",
    riskModerate: "#8a6100",
    riskHigh: "#b64b1e",
    riskSevere: "#a22d46",
    riskNoTravel: "#642344",
    riskNoData: "#5e6e77",
    routeHalo: "#fbfcfa",
  },
  space: {
    0: "0px",
    "0_5": "2px",
    "0_75": "3px",
    1: "4px",
    2: "8px",
    3: "12px",
    4: "16px",
    5: "20px",
    6: "24px",
    8: "32px",
    10: "40px",
    12: "48px",
  },
  type: {
    family: '"Inter", "Segoe UI", sans-serif',
    sizeXs: "11px",
    sizeSm: "12px",
    sizeBase: "14px",
    sizeMd: "16px",
    sizeLg: "19px",
    sizeXl: "25px",
    sizeDisplay: "30px",
    lineTight: "1.15",
    lineBase: "1.45",
    lineRelaxed: "1.65",
    weightRegular: "400",
    weightMedium: "550",
    weightSemibold: "650",
    weightBold: "750",
    tracking: "-0.025em",
    trackingWide: "0.09em",
  },
  radius: {
    sm: "6px",
    md: "10px",
    lg: "14px",
    xl: "19px",
    pill: "999px",
  },
  border: {
    hairline: "1px",
    strong: "2px",
  },
  motion: {
    quick: "140ms",
    standard: "220ms",
    easing: "cubic-bezier(0.2, 0.7, 0.2, 1)",
  },
  size: {
    dot: "8px",
    spinner: "20px",
    progress: "3px",
    panel: "390px",
    touch: "44px",
    marker: "18px",
    control: "44px",
  },
  shadow: {
    panel: "0 16px 46px #19372e24",
    float: "0 4px 18px #19372e20",
  },
} as const;

const RISK_COLOR_KEYS: Record<RiskLabel, keyof typeof DESIGN.color> = {
  Low: "riskLow",
  Moderate: "riskModerate",
  High: "riskHigh",
  Severe: "riskSevere",
  "No Travel": "riskNoTravel",
  "No data": "riskNoData",
};

export const RISK_LEVELS: { label: RiskLabel; marker: string }[] = [
  { label: "Low", marker: "○" },
  { label: "Moderate", marker: "◇" },
  { label: "High", marker: "△" },
  { label: "Severe", marker: "■" },
  { label: "No Travel", marker: "✕" },
  { label: "No data", marker: "?" },
];

export const RISK_COLORS = Object.fromEntries(
  Object.entries(RISK_COLOR_KEYS).map(([label, key]) => [label, DESIGN.color[key]]),
) as Record<RiskLabel, string>;

export function applyDesignTokens(): void {
  const root = document.documentElement;
  Object.entries(DESIGN.color).forEach(([name, value]) => root.style.setProperty(`--color-${name}`, value));
  Object.entries(DESIGN.space).forEach(([name, value]) => root.style.setProperty(`--space-${name}`, value));
  Object.entries(DESIGN.type).forEach(([name, value]) => root.style.setProperty(`--type-${name}`, value));
  Object.entries(DESIGN.radius).forEach(([name, value]) => root.style.setProperty(`--radius-${name}`, value));
  Object.entries(DESIGN.border).forEach(([name, value]) => root.style.setProperty(`--border-${name}`, value));
  Object.entries(DESIGN.motion).forEach(([name, value]) => root.style.setProperty(`--motion-${name}`, value));
  Object.entries(DESIGN.size).forEach(([name, value]) => root.style.setProperty(`--size-${name}`, value));
  Object.entries(DESIGN.shadow).forEach(([name, value]) => root.style.setProperty(`--shadow-${name}`, value));
}

export const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";

export const MAP_INITIAL_VIEW = {
  longitude: -98.5,
  latitude: 39.8,
  zoom: 3,
};

export const MAP_FIT_PADDING = 52;
