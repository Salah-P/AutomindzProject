import { defineConfig } from "@trigger.dev/sdk/v3";
import { pythonExtension } from "@trigger.dev/python/extension";

export default defineConfig({
  project: "proj_dvfflvatqctdhlkzxcbx",
  runtime: "node",
  logLevel: "info",
  maxDuration: 600,
  retries: {
    enabledInDev: true,
    default: {
      maxAttempts: 2,
      minTimeoutInMs: 1000,
      maxTimeoutInMs: 10000,
      factor: 2,
      randomize: true,
    },
  },
  dirs: ["./src"],
  build: {
    extensions: [
      pythonExtension({
        requirementsFile: "./requirements.txt",
        scripts: ["./scraper/**/*.py"],
      }),
    ],
  },
});
