import { logger, task } from "@trigger.dev/sdk/v3";
import { createClient } from "@supabase/supabase-js";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

export type ScrapePayload = {
  search_query: string;
  limit?: number | null;
};

export type ScrapedJob = {
  job_url: string;
  job_title: string;
  company_name: string;
  job_description: string;
  search_query: string;
};

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// trigger/src/trigger → repo root
const REPO_ROOT = path.resolve(__dirname, "../../..");

function runScraper(searchQuery: string, limit?: number | null): Promise<ScrapedJob[]> {
  const script = path.join(REPO_ROOT, "scraper", "wwr.py");
  const args = [script, "--search-query", searchQuery, "--json"];
  if (limit != null) {
    args.push("--limit", String(limit));
  }

  return new Promise((resolve, reject) => {
    const child = spawn("python", args, {
      cwd: REPO_ROOT,
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) {
        reject(new Error(`scraper exited ${code}: ${stderr || stdout}`));
        return;
      }
      try {
        const parsed = JSON.parse(stdout) as ScrapedJob[];
        resolve(parsed);
      } catch (err) {
        reject(new Error(`failed to parse scraper JSON: ${String(err)}\n${stdout}`));
      }
    });
  });
}

export const scrapeWeworkremotely = task({
  id: "scrape-weworkremotely",
  maxDuration: 600,
  run: async (payload: ScrapePayload) => {
    const { search_query, limit } = payload;
    logger.info("Starting WeWorkRemotely scrape", { search_query, limit });

    const jobs = await runScraper(search_query, limit);
    logger.info("Scraper finished", { count: jobs.length });

    const url = process.env.SUPABASE_URL;
    const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
    if (!url || !key) {
      throw new Error("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required");
    }

    const supabase = createClient(url, key);
    // Upsert on job_url so re-scrapes refresh title/description without duplicates
    const { error, count } = await supabase.from("jobs").upsert(
      jobs.map((j) => ({
        job_url: j.job_url,
        job_title: j.job_title,
        company_name: j.company_name,
        job_description: j.job_description,
        search_query: j.search_query,
      })),
      { onConflict: "job_url", count: "exact" },
    );

    if (error) {
      throw new Error(`Supabase upsert failed: ${error.message}`);
    }

    logger.info("Upserted jobs", { count: count ?? jobs.length });
    return { search_query, upserted: count ?? jobs.length };
  },
});
