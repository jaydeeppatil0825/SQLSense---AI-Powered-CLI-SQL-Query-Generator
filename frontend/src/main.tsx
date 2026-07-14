import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { AppProviders } from "./app/providers/AppProviders";
import { router } from "./app/router";
import "./styles/index.css";

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <AppProviders router={router} />
  </StrictMode>,
);
