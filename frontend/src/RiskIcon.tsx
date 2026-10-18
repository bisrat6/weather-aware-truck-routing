import { riskStyles, type RiskLabel, type RiskShape } from "./constants";
import type { Map as MapLibreMap } from "maplibre-gl";

const CIRCLE_PATH = "M12 3.5a8.5 8.5 0 1 0 0 17a8.5 8.5 0 1 0 0-17";
const SHAPE_PATHS: Record<Exclude<RiskShape, "missing">, string> = {
  circle: CIRCLE_PATH,
  diamond: "M12 2.5 21.5 12 12 21.5 2.5 12Z",
  triangle: "M12 2.5 22 21H2Z",
  square: "M3 3h18v18H3Z",
  cross: "M4 4 20 20M20 4 4 20",
};

export function riskIconName(level: RiskLabel): string {
  return `risk-icon-${level.toLowerCase().replaceAll(" ", "-")}`;
}

export function registerRiskIcons(map: MapLibreMap): void {
  for (const style of Object.values(riskStyles)) {
    const name = riskIconName(style.label);
    if (map.hasImage(name)) continue;
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
    const path = new Path2D(style.shape === "missing" ? CIRCLE_PATH : SHAPE_PATHS[style.shape]);
    context.strokeStyle = "#ffffff";
    context.lineWidth = style.shape === "missing" || style.shape === "cross" ? 6 : 5;
    context.stroke(path);
    if (style.shape !== "missing" && style.shape !== "cross") {
      context.fillStyle = "#ffffff";
      context.fill(path);
      context.fillStyle = color;
      context.fill(path);
    }
    context.strokeStyle = color;
    context.lineWidth = style.shape === "missing" || style.shape === "cross" ? 3 : 2;
    context.stroke(path);
    if (style.shape === "missing") {
      context.lineWidth = 4;
      context.strokeStyle = "#ffffff";
      context.strokeText("?", 12, 12.5);
      context.fillStyle = color;
      context.fillText("?", 12, 12.5);
    }
    map.addImage(name, context.getImageData(0, 0, canvas.width, canvas.height), { pixelRatio: 2 });
  }
}

export function RiskIcon({ level, className }: { level: RiskLabel; className?: string }) {
  const style = riskStyles[level];
  const color = `var(${style.color})`;
  const path = SHAPE_PATHS[style.shape === "missing" ? "circle" : style.shape];
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d={path} fill="none" stroke="white" strokeWidth={style.shape === "cross" || style.shape === "missing" ? 5 : 4} strokeLinejoin="round" strokeLinecap="round" />
      {style.shape === "missing" ? (
        <>
          <path d={path} fill="none" stroke={color} strokeWidth="1.8" />
          <text x="12" y="16" textAnchor="middle" fontSize="13" fontWeight="700" fill={color} stroke="white" strokeWidth="2.5" paintOrder="stroke">?</text>
        </>
      ) : style.shape === "cross" ? (
        <path d={path} fill="none" stroke={color} strokeWidth="2.6" strokeLinecap="round" />
      ) : (
        <path d={path} fill={color} stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
      )}
    </svg>
  );
}
