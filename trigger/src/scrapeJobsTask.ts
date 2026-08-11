import { logger, task } from "@trigger.dev/sdk/v3";
import { python } from "@trigger.dev/python";
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

function resolveRepoRoot(): string | null {
  if (
    process.env.AUTOMINDZ_ROOT &&
    existsSync(path.join(process.env.AUTOMINDZ_ROOT, "scraper", "run.py"))
  ) {
    return path.resolve(process.env.AUTOMINDZ_ROOT);
  }

  let dir = process.cwd();
  for (let i = 0; i < 8; i++) {
    if (existsSync(path.join(dir, "scraper", "run.py"))) {
      return dir;
    }
    const parent = path.resolve(dir, "..");
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}

function pythonCommand(): string {
  if (process.env.PYTHON_BIN) return process.env.PYTHON_BIN;
  return process.platform === "win32" ? "python" : "python3";
}

async function runScraperViaPythonExtension(jobTitle: string): Promise<ScrapedJob[]> {
  const result = await python.runScript("scraper/run.py", [jobTitle]);
  if (result.stderr) {
    logger.warn("scraper stderr", { stderr: result.stderr.slice(0, 1000) });
  }
  const parsed = JSON.parse(result.stdout) as ScrapedJob[];
  if (!Array.isArray(parsed)) {
    throw new Error("scraper stdout was not a JSON array");
  }
  return parsed;
}

function runScraperViaSpawn(jobTitle: string, repoRoot: string): Promise<ScrapedJob[]> {
  const script = path.join(repoRoot, "scraper", "run.py");
  const cmd = pythonCommand();
  const args = [script, jobTitle];

  logger.info("Spawning scraper", { cmd, args, cwd: repoRoot });

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

async function runScraper(jobTitle: string): Promise<ScrapedJob[]> {
  try {
    return await runScraperViaPythonExtension(jobTitle);
  } catch (err) {
    logger.warn("python.runScript failed; trying local spawn", {
      error: String(err),
    });
    const repoRoot = resolveRepoRoot();
    if (!repoRoot) {
      throw err instanceof Error ? err : new Error(String(err));
    }
    return runScraperViaSpawn(jobTitle, repoRoot);
  }
}

async function upsertJobs(jobs: ScrapedJob[], searchQuery: string): Promise<number> {
  // Use PostgREST directly — supabase-js pulls in realtime/WebSocket which breaks on Trigger runners.
  const baseUrl = process.env.SUPABASE_URL;
  const key =
    process.env.SUPABASE_SECRET_KEY || process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!baseUrl || !key) {
    throw new Error(
      "SUPABASE_URL and SUPABASE_SECRET_KEY must be set on the Trigger.dev environment",
    );
  }
  if (!jobs.length) return 0;

  const rows = jobs.map((j) => ({
    job_url: j.job_url,
    job_title: j.job_title,
    company_name: j.company_name,
    job_description: j.job_description,
    search_query: searchQuery,
    scraped_at: new Date().toISOString(),
  }));

  const resp = await fetch(`${baseUrl.replace(/\/$/, "")}/rest/v1/jobs?on_conflict=job_url`, {
    method: "POST",
    headers: {
      apikey: key,
      Authorization: `Bearer ${key}`,
      "Content-Type": "application/json",
      Prefer: "resolution=merge-duplicates,return=representation",
    },
    body: JSON.stringify(rows),
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Supabase upsert failed (${resp.status}): ${text}`);
  }

  const data = (await resp.json()) as unknown;
  return Array.isArray(data) ? data.length : rows.length;
}

export const scrapeJobsTask = task({
  id: "scrape-jobs",
  // Hard cap per run — WWR RSS + a few detail pages should finish well under this.
  maxDuration: 180,
  retry: {
    maxAttempts: 2,
  },
  run: async (payload: ScrapeJobsPayload) => {
    const jobTitle = (payload.job_title || "").trim();
    if (!jobTitle) {
      throw new Error("job_title is required");
    }

    logger.info("scrape-jobs starting", { jobTitle });
    const jobs = await runScraper(jobTitle);
    const upserted = await upsertJobs(jobs, jobTitle);
    logger.info("scrape-jobs finished", { count: jobs.length, upserted });
    return { jobs, upserted };
  },
});
