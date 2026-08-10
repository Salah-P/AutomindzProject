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
      maxAttempts: 3,
      minTimeoutInMs: 1000,
      maxTimeoutInMs: 10000,
      factor: 2,
      randomize: true,
    },
  },
  // Pick up trigger/src/scrapeJobsTask.ts
  dirs: ["./src"],
  build: {
    // Needed for Trigger.dev cloud deploy so the runner has Python + our scraper.
    // Local `trigger.dev dev` uses your machine's Python via child_process / PYTHON_BIN.
    extensions: [
      pythonExtension({
        // Stdlib-only scraper today; file kept for future deps.
        requirementsFile: "../scraper/requirements.txt",
        scripts: ["../scraper/**/*.py"],
        // Windows local binary override (optional):
        // devPythonBinaryPath: "python",
      }),
    ],
  },
});
