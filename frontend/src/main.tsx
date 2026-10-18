import React from "react";
import ReactDOM from "react-dom/client";
import "@fontsource-variable/inter";
import "maplibre-gl/dist/maplibre-gl.css";
import App from "./App";
import "./styles/tokens.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
