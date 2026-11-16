/** Runtime configuration resolved once when the script loads. */

export interface FallbackLink {
  label: string;
  url: string;
}

export interface WidgetConfig {
  apiBase: string;
  fallbackLinks: FallbackLink[];
  /**
   * Open the window and read the greeting on page load.
   * data-auto-open: "session" (default, once per browser session), "always", or "false".
   */
  autoOpen: "session" | "always" | "off";
}

/**
 * API base URL, in priority order:
 *   1. data-api-url on the <script> tag (per-page override)
 *   2. VITE_API_URL (build time)
 *   3. the origin the script itself was loaded from (the backend serves /widget.js)
 */
export function resolveApiBase(script: HTMLScriptElement | null): string {
  const fromAttribute = script?.dataset.apiUrl;
  const fromBuild = import.meta.env.VITE_API_URL;
  const fromScript = script?.src ? new URL(script.src).origin : "";
  return (fromAttribute || fromBuild || fromScript).replace(/\/+$/, "");
}

/** Public NAIS pages; override at build time with VITE_*_URL. */
export function resolveFallbackLinks(): FallbackLink[] {
  return [
    {
      label: "FAQs",
      url: import.meta.env.VITE_FAQ_URL || "https://aisandbox.org.nz/",
    },
    {
      label: "Book a session",
      url:
        import.meta.env.VITE_BOOKING_URL ||
        "https://aisandbox.org.nz/book-a-session/",
    },
    {
      label: "Contact us",
      url: import.meta.env.VITE_CONTACT_URL || "https://aisandbox.org.nz/about-us/",
    },
  ];
}

function resolveAutoOpen(script: HTMLScriptElement | null): WidgetConfig["autoOpen"] {
  const value = script?.dataset.autoOpen;
  if (value === "false") return "off";
  return value === "always" ? "always" : "session";
}

export function resolveConfig(script: HTMLScriptElement | null): WidgetConfig {
  return {
    apiBase: resolveApiBase(script),
    fallbackLinks: resolveFallbackLinks(),
    autoOpen: resolveAutoOpen(script),
  };
}
