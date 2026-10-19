/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** WebSocket URL of the AURA backend, e.g. "wss://host/ws". */
  readonly VITE_WS_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
