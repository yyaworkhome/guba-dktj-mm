import { createApp } from "vue";
import App from "./App.vue";
import "./style.css";
createApp(App).mount("#app");
// Cache only the Web/PWA app shell; native bundles already live on the device.
if (
  import.meta.env.PROD &&
  "serviceWorker" in navigator &&
  /^https?:$/.test(location.protocol) &&
  !("Capacitor" in window) &&
  !("__TAURI_INTERNALS__" in window)
) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* App remains usable without offline support. */
    });
  });
}
