import { logger, task } from "@trigger.dev/sdk/v3";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

export type ScrapeJobsPayload = {
  job_title: string;
};

export type ScrapedJob = {
  job_url: string;
  job_title: string;
  company_name: string;
  job_description: string;
};

function resolveRepoRoot(): string {
  if (process.env.AUTOMINDZ_ROOT && existsSync(path.join(process.env.AUTOMINDZ_ROOT, "scraper", "run.py"))) {
    return path.resolve(process.env.AUTOMINDZ_ROOT);
  }

  // Prefer walking from cwd: `npx trigger.dev dev` runs with cwd = trigger/
  let dir = process.cwd();
  for (let i = 0; i < 8; i++) {
    if (existsSync(path.join(dir, "scraper", "run.py"))) {
      return dir;
    }
    const parent = path.resolve(dir, "..");
    if (parent === dir) break;
    dir = parent;
  }

  throw new Error(
    "Could not locate scraper/run.py. Set AUTOMINDZ_ROOT to the monorepo root.",
  );
}

function pythonCommand(): string {
  // Prefer explicit override; otherwise python3 (Linux/macOS / cloud), python on Windows.
  if (process.env.PYTHON_BIN) return process.env.PYTHON_BIN;
  return process.platform === "win32" ? "python" : "python3";
}

function runScraper(jobTitle: string): Promise<ScrapedJob[]> {
  const repoRoot = resolveRepoRoot();
  const script = path.join(repoRoot, "scraper", "run.py");
  const cmd = pythonCommand();
  const args = [script, jobTitle];

  logger.info("Spawning scraper", { cmd, args, cwd: repoRoot, scriptExists: existsSync(script) });

  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, {
      cwd: repoRoot,
      env: {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (err) => {
      reject(new Error(`Failed to start ${cmd}: ${err.message}`));
    });
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`scraper exited ${code}: ${stderr || stdout}`));
        return;
      }
      try {
        const parsed = JSON.parse(stdout) as ScrapedJob[];
        if (!Array.isArray(parsed)) {
          reject(new Error("scraper stdout was not a JSON array"));
          return;
        }
        resolve(parsed);
      } catch (err) {
        reject(
          new Error(
            `failed to parse scraper JSON: ${String(err)}\nstdout=${stdout.slice(0, 500)}`,
          ),
        );
      }
    });
  });
}

export const scrapeJobsTask = task({
  id: "scrape-jobs",
  maxDuration: 600,
  run: async (payload: ScrapeJobsPayload) => {
    const jobTitle = (payload.job_title || "").trim();
    if (!jobTitle) {
      throw new Error("job_title is required");
    }

    logger.info("scrape-jobs starting", { jobTitle });
    const jobs = await runScraper(jobTitle);
    logger.info("scrape-jobs finished", { count: jobs.length });
    return jobs;
  },
});
