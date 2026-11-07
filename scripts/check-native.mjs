import { loadEnv } from "vite";
const configured =
  process.env.VITE_API_URL ||
  loadEnv("production", process.cwd(), "").VITE_API_URL;
let url;
try {
  url = new URL(configured);
} catch {
  console.error(
    "Set VITE_API_URL to your deployed HTTPS API origin before building a native release.",
  );
  process.exit(1);
}
if (
  url.protocol !== "https:" ||
  url.username ||
  url.password ||
  url.search ||
  url.hash ||
  url.pathname !== "/" ||
  ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
) {
  console.error(
    "Native releases require a public HTTPS origin without credentials, a path, or query parameters.",
  );
  process.exit(1);
}
try {
  const response = await fetch(`${url.origin}/api/health`, {
    signal: AbortSignal.timeout(15000),
  });
  const health = await response.json();
  if (!response.ok || health.status !== "ok" || !health.version)
    throw new Error("invalid health response");
} catch {
  console.error(
    "The configured API failed its HTTPS health check. Deploy and verify the backend first.",
  );
  process.exit(1);
}
console.log("Native API destination and health check passed.");
