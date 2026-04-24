import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { initPwa } from "./pwa";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

// Fire after render so the SW registration doesn't block initial
// paint. Registration itself is async; this is only the synchronous
// kickoff call.
initPwa();
