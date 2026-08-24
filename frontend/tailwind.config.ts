import type { Config } from "tailwindcss";

// Tailwind 3.4 (config-file flavor) over v4: boring and proven, per the
// project's tooling rule.
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: { extend: {} },
  plugins: [],
};
export default config;
