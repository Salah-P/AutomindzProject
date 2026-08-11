import { defineConfig } from "@trigger.dev/sdk/v3";
import { syncEnvVars } from "@trigger.dev/build/extensions/core";
import { pythonExtension } from "@trigger.dev/python/extension";
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";

/**
 * Root-level config so Trigger.dev's GitHub App (default: look at repo root)
 * can clone + build without custom "config path" / "install command" settings.
 *
 * Local alternative: `cd trigger && npm run dev` still uses `trigger/trigger.config.ts`.
 */

function loadDotEnv(): Record<string, string> {
  const candidates = [".env", ".env.production", "trigger/.env"];
  const out: Record<string, string> = {};
  for (const file of candidates) {
    const full = path.resolve(file);
    if (!existsSync(full)) continue;
    for (const line of readFileSync(full, "utf8").split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eq = trimmed.indexOf("=");
      if (eq === -1) continue;
      const key = trimmed.slice(0, eq).trim();
      let value = trimmed.slice(eq + 1).trim();
      if (
        (value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))
      ) {
        value = value.slice(1, -1);
      }
      out[key] = value;
    }
  }
  return out;
}

export default defineConfig({
  project: "proj_dvfflvatqctdhlkzxcbx",
  runtime: "node",
  logLevel: "info",
  maxDuration: 180,
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
  dirs: ["./trigger/src"],
  build: {
    extensions: [
      pythonExtension({
        requirementsFile: "./trigger/requirements.txt",
        scripts: ["./trigger/scraper/**/*.py"],
      }),
      syncEnvVars(async () => {
        const env = loadDotEnv();
        return [
          {
            name: "SUPABASE_URL",
            value: env.SUPABASE_URL || process.env.SUPABASE_URL || "",
            isSecret: false,
          },
          {
            name: "SUPABASE_SECRET_KEY",
            value:
              env.SUPABASE_SECRET_KEY || process.env.SUPABASE_SECRET_KEY || "",
            isSecret: true,
          },
          {
            name: "SCRAPER_REQUEST_DELAY",
            value:
              env.SCRAPER_REQUEST_DELAY ||
              process.env.SCRAPER_REQUEST_DELAY ||
              "0.25",
            isSecret: false,
          },
        ].filter((v) => Boolean(v.value));
      }),
    ],
  },
});
