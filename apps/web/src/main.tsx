import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";

import "roboto-fontface/css/roboto/roboto-fontface.css";
import "roboto-fontface/css/roboto-slab/roboto-slab-fontface.css";

import { RootErrorBoundary } from "./app/RootErrorBoundary";
import "./styles/global.css";

const App = lazy(async () =>
  import("./App").then((module) => ({ default: module.App })),
);

const root = document.getElementById("root");
if (!root) {
  throw new Error("Missing #root application mount");
}

createRoot(root).render(
  <StrictMode>
    <RootErrorBoundary>
      <Suspense fallback={<p role="status">Preparing your workspace</p>}>
        <App />
      </Suspense>
    </RootErrorBoundary>
  </StrictMode>,
);
