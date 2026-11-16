import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ChatWidget } from "./ChatWidget";
import { resolveConfig } from "./config";
import styles from "./styles.css?inline";

// Must be read synchronously while the script runs; it is null afterwards.
const scriptTag =
  (document.currentScript as HTMLScriptElement | null) ??
  document.querySelector<HTMLScriptElement>('script[src*="widget.js"]');

/** Create a host div, attach a Shadow DOM, inject scoped CSS and mount React. */
function mount(): void {
  const host = document.createElement("div");
  host.id = "nais-chat-host";
  document.body.appendChild(host);

  const shadow = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = styles;
  const container = document.createElement("div");
  shadow.append(style, container);

  createRoot(container).render(
    <StrictMode>
      <ChatWidget config={resolveConfig(scriptTag)} />
    </StrictMode>,
  );
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount);
} else {
  mount();
}
