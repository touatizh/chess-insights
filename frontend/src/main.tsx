import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./index.css";

const root = document.getElementById("root");
if (!root) throw new Error("Root element #root not found");

createRoot(root).render(
  <StrictMode>
    <div className="pt-24 text-center font-mono text-sm uppercase tracking-[0.14em] text-chalk">
      Chess Insights
    </div>
  </StrictMode>,
);
