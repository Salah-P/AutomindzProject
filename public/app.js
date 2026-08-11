// Always same-origin in the browser unless API_BASE is an explicit non-localhost override
// used only for local static hosting against a remote API.
function resolveApiBase() {
  const origin = window.location.origin;
  const host = window.location.hostname;
  const onVercel = host.endsWith(".vercel.app");
  const stored = localStorage.getItem("API_BASE");

  // Production / preview on Vercel must never call a stale API_BASE (common cause of NOT_FOUND).
  if (onVercel) {
    if (stored) localStorage.removeItem("API_BASE");
    return origin;
  }

  if (!stored) return origin;
  try {
    const u = new URL(stored);
    if (u.hostname === "127.0.0.1" || u.hostname === "localhost") {
      return stored.replace(/\/$/, "");
    }
    return stored.replace(/\/$/, "");
  } catch {
    localStorage.removeItem("API_BASE");
    return origin;
  }
}

const API_BASE = resolveApiBase();

const form = document.getElementById("search-form");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");
const titleInput = document.getElementById("job-title");

let pollTimer = null;

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

function shortDescription(text, maxLen = 220) {
  const cleaned = String(text || "").replace(/\s+/g, " ").trim();
  if (cleaned.length <= maxLen) return cleaned;
  return `${cleaned.slice(0, maxLen - 1)}…`;
}

function renderJobs(jobs, { emptyMessage } = {}) {
  resultsEl.innerHTML = "";
  if (!jobs?.length) {
    const message =
      emptyMessage || "No matching jobs found for this search.";
    resultsEl.innerHTML = `<p class="meta">${escapeHtml(message)}</p>`;
    return;
  }

  for (const job of jobs) {
    const article = document.createElement("article");
    article.className = "job";
    article.innerHTML = `
      <h2>${escapeHtml(job.job_title)}</h2>
      <p class="meta">
        ${escapeHtml(job.company_name)}
        ·
        <a href="${escapeAttr(job.job_url)}" target="_blank" rel="noopener">View listing</a>
      </p>
      <p>${escapeHtml(shortDescription(job.job_description))}</p>
    `;
    resultsEl.appendChild(article);
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("'", "&#39;");
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function fetchJobs(jobTitle, { refresh = false, poll = false } = {}) {
  // Prefer /v1; fall back to /api/v1 if a proxy only exposes the /api prefix.
  const paths = ["/v1/get-jobs", "/api/v1/get-jobs"];
  let lastError = null;

  for (const path of paths) {
    const url = new URL(path, API_BASE);
    url.searchParams.set("job_title", jobTitle);
    if (refresh) url.searchParams.set("refresh", "true");
    if (poll) url.searchParams.set("poll", "true");

    try {
      const res = await fetch(url);
      const text = await res.text();
      let body = {};
      try {
        body = text ? JSON.parse(text) : {};
      } catch {
        body = { detail: text.slice(0, 300) };
      }

      if (res.status === 404 && path === paths[0]) {
        lastError = new Error(`HTTP 404 at ${url.pathname}`);
        continue;
      }

      if (!res.ok) {
        const detail = body.detail || res.statusText || "Request failed";
        let msg = typeof detail === "string" ? detail : JSON.stringify(detail);
        if (
          res.status === 404 &&
          (String(msg).includes("NOT_FOUND") || String(msg).includes("page could not be found"))
        ) {
          msg =
            "API route not found on Vercel (NOT_FOUND). Hard-refresh the page; if it persists the FastAPI deploy may be wrong.";
        }
        throw new Error(`HTTP ${res.status}: ${msg}`);
      }
      return body;
    } catch (err) {
      lastError = err;
      if (path === paths[0] && String(err.message || err).includes("404")) {
        continue;
      }
      throw err;
    }
  }

  throw lastError || new Error("Request failed");
}

function startPolling(jobTitle, { keepJobs = false } = {}) {
  stopPolling();
  // Cap how long the UI waits for a scrape (Trigger task also has maxDuration).
  const pollEveryMs = 3000;
  const maxWaitMs = 90_000;
  const maxTicks = Math.ceil(maxWaitMs / pollEveryMs);
  let ticks = 0;
  pollTimer = setInterval(async () => {
    ticks += 1;
    try {
      const data = await fetchJobs(jobTitle, { poll: true });
      if (data.status === "ready" && data.jobs?.length) {
        setStatus(`Found ${data.count} job(s) for “${jobTitle}”.`);
        renderJobs(data.jobs);
        stopPolling();
        return;
      }
      if (data.status === "ready" && !data.jobs?.length) {
        setStatus(`No matching jobs for “${jobTitle}”.`);
        renderJobs([], {
          emptyMessage: "No matching jobs found for this search.",
        });
        stopPolling();
        return;
      }
      if (ticks >= maxTicks) {
        setStatus(
          "Still no results after 90s — the scrape may still be running, try searching again shortly.",
          true,
        );
        if (!keepJobs) {
          renderJobs([], {
            emptyMessage: "Still searching — try again in a moment if nothing shows up.",
          });
        }
        stopPolling();
        return;
      }
      setStatus(`Scraping via Trigger.dev… (${ticks * 3}s)`);
      if (!keepJobs) {
        renderJobs([], {
          emptyMessage: "Still searching — this can take a short while.",
        });
      }
    } catch (err) {
      setStatus(String(err.message || err), true);
      stopPolling();
    }
  }, pollEveryMs);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  stopPolling();

  const jobTitle = titleInput.value.trim();
  const button = form.querySelector("button");
  button.disabled = true;
  setStatus("Looking up jobs…");
  resultsEl.innerHTML = "";

  try {
    // Cache first (API auto-refreshes when TTL expired).
    let data = await fetchJobs(jobTitle, { refresh: false });

    if (data.status === "ready" && data.jobs?.length) {
      setStatus(`Found ${data.count} job(s) for “${jobTitle}” (cache).`);
      renderJobs(data.jobs);
      return;
    }

    // Status scraping: API already kicked Trigger (miss or stale TTL).
    // Only force refresh when the first call did not start a run.
    if (data.status !== "scraping") {
      data = await fetchJobs(jobTitle, { refresh: true });
      if (data.status === "ready" && data.jobs?.length) {
        setStatus(`Found ${data.count} job(s) for “${jobTitle}”.`);
        renderJobs(data.jobs);
        return;
      }
    }

    setStatus(data.message || "Scrape started. Waiting for results…");
    if (data.jobs?.length) {
      // Stale cache refresh: show previous results while new scrape runs.
      renderJobs(data.jobs);
      startPolling(jobTitle, { keepJobs: true });
    } else {
      renderJobs([], {
        emptyMessage: "Still searching — this can take a short while.",
      });
      startPolling(jobTitle);
    }
  } catch (err) {
    setStatus(String(err.message || err), true);
  } finally {
    button.disabled = false;
  }
});
