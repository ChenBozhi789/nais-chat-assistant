/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API base URL, set at build time. Overridable with data-api-url on the script tag. */
  readonly VITE_API_URL?: string;
  /** Fallback links shown when the API fails. */
  readonly VITE_FAQ_URL?: string;
  readonly VITE_BOOKING_URL?: string;
  readonly VITE_CONTACT_URL?: string;
}
