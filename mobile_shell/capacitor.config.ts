import type { CapacitorConfig } from "@capacitor/cli";

const streamlitUrl = process.env.STREAMLIT_URL ?? "http://192.168.1.100:8501";

const config: CapacitorConfig = {
  appId: "com.fundestimator.mobile",
  appName: "Fund Estimator",
  webDir: "www",
  server: {
    url: streamlitUrl,
    cleartext: streamlitUrl.startsWith("http://")
  }
};

export default config;
