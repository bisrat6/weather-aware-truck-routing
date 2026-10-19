import { getThemeToken, riskStyles, type RiskLabel, type RiskShape } from "./constants";
import type { Map as MapLibreMap } from "maplibre-gl";

const CIRCLE_PATH = "M12 3.5a8.5 8.5 0 1 0 0 17a8.5 8.5 0 1 0 0-17";
export const DESTINATION_ICON_NAME = "route-destination-pin";
const SHAPE_PATHS: Record<Exclude<RiskShape, "missing">, string[]> = {
  circle: [CIRCLE_PATH],
  "circle-alert": ["M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z", "M12 8v4", "M12 16h.01"],
  "triangle-alert": ["m10.3 3.86-8.1 14a2 2 0 0 0 1.74 3h16.12a2 2 0 0 0 1.74-3l-8.1-14a2 2 0 0 0-3.4 0Z", "M12 9v4", "M12 17h.01"],
  "shield-alert": ["M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z", "M12 8v4", "M12 16h.01"],
  cross: ["M4 4 20 20", "M20 4 4 20"],
};

function pathsForShape(shape: RiskShape): string[] {
  return shape === "missing" ? [CIRCLE_PATH] : SHAPE_PATHS[shape];
}

export function riskIconName(level: RiskLabel): string {
  return `risk-icon-${level.toLowerCase().replaceAll(" ", "-")}`;
}

export function registerRiskIcons(map: MapLibreMap): void {
  for (const style of Object.values(riskStyles)) {
    const name = riskIconName(style.label);
    const canvas = document.createElement("canvas");
    canvas.width = 48;
    canvas.height = 48;
    const context = canvas.getContext("2d");
    if (!context) continue;
    context.scale(2, 2);
    context.lineCap = "round";
    context.lineJoin = "round";
    context.font = "bold 16px sans-serif";
    context.textAlign = "center";
    context.textBaseline = "middle";
    const color = getComputedStyle(document.documentElement).getPropertyValue(style.color).trim();
    const paths = pathsForShape(style.shape).map((path) => new Path2D(path));
    context.strokeStyle = "#ffffff";
    context.lineWidth = style.shape === "missing" || style.shape === "cross" ? 6 : 5;
    for (const path of paths) context.stroke(path);
    if (style.shape === "circle") {
      context.fillStyle = "#ffffff";
      context.fill(paths[0]);
      context.fillStyle = color;
      context.fill(paths[0]);
    }
    context.strokeStyle = color;
    context.lineWidth = style.shape === "missing" || style.shape === "cross" ? 3 : 2.2;
    for (const path of paths) context.stroke(path);
    if (style.shape === "missing") {
      context.lineWidth = 4;
      context.strokeStyle = "#ffffff";
      context.strokeText("?", 12, 12.5);
      context.fillStyle = color;
      context.fillText("?", 12, 12.5);
    }
    const image = context.getImageData(0, 0, canvas.width, canvas.height);
    if (map.hasImage(name)) {
      map.updateImage(name, image);
    } else {
      map.addImage(name, image, { pixelRatio: 2 });
    }
  }
}

export function registerDestinationIcon(map: MapLibreMap): void {
  const canvas = document.createElement("canvas");
  canvas.width = 48;
  canvas.height = 48;
  const context = canvas.getContext("2d");
  if (!context) return;
  context.scale(2, 2);
  const pin = new Path2D("M20 10c0 5-8 11-8 11s-8-6-8-11a8 8 0 1 1 16 0Z");
  context.fillStyle = getThemeToken("--primary");
  context.strokeStyle = "#ffffff";
  context.lineWidth = 4;
  context.lineJoin = "round";
  context.fill(pin);
  context.stroke(pin);
  context.fillStyle = "#ffffff";
  context.beginPath();
  context.arc(12, 10, 2.6, 0, Math.PI * 2);
  context.fill();
  const image = context.getImageData(0, 0, canvas.width, canvas.height);
  if (map.hasImage(DESTINATION_ICON_NAME)) {
    map.updateImage(DESTINATION_ICON_NAME, image);
  } else {
    map.addImage(DESTINATION_ICON_NAME, image, { pixelRatio: 2 });
  }
}

export function RiskIcon({ level, className }: { level: RiskLabel; className?: string }) {
  const style = riskStyles[level];
  const color = `var(${style.color})`;
  const paths = pathsForShape(style.shape);
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {paths.map((path, index) => (
        <path key={`halo-${index}`} d={path} fill="none" stroke="white" strokeWidth={style.shape === "cross" || style.shape === "missing" ? 5 : 4} strokeLinejoin="round" strokeLinecap="round" />
      ))}
      {style.shape === "missing" ? (
        <>
          <path d={paths[0]} fill="none" stroke={color} strokeWidth="1.8" />
          <text x="12" y="16" textAnchor="middle" fontSize="13" fontWeight="700" fill={color} stroke="white" strokeWidth="2.5" paintOrder="stroke">?</text>
        </>
      ) : style.shape === "cross" ? (
        paths.map((path, index) => <path key={`cross-${index}`} d={path} fill="none" stroke={color} strokeWidth="2.6" strokeLinecap="round" />)
      ) : style.shape === "circle" ? (
        <path d={paths[0]} fill={color} stroke={color} strokeWidth="1.5" />
      ) : (
        paths.map((path, index) => <path key={`icon-${index}`} d={path} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />)
      )}
    </svg>
  );
}
