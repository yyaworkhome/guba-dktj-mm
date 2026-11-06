import type { CapacitorConfig } from "@capacitor/cli";
const config: CapacitorConfig = {
  appId: "com.sentimentlab.app",
  appName: "散户情绪量化",
  webDir: "dist",
  android: { backgroundColor: "#0b111b", allowMixedContent: false },
};
export default config;
