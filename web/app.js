const API_BASE = localStorage.getItem("API_BASE") || "http://127.0.0.1:8000";

const form = document.getElementById("search-form");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");
const queryInput = document.getElementById("search-query");
const forceRefreshInput = document.getElementById("force-refresh");

let pollTimer = null;

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

function renderJobs(jobs) {
  resultsEl.innerHTML = "";
  if (!jobs?.length) {
    resultsEl.innerHTML = "<p class='meta'>No jobs yet.</p>";
    return;
  }

  for (const job of jobs) {
    const article = document.createElement("article");
    article.className = "job";
    article.innerHTML = `
      <h2>${escapeHtml(job.job_title)}</h2>
      <p class="meta">${escapeHtml(job.company_name)} · <a href="${escapeAttr(
        job.job_url,
      )}" target="_blank" rel="noopener">Open listing</a></p>
      <p>${escapeHtml(job.job_description)}</p>
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

async function fetchJobs({ searchQuery, forceRefresh, method }) {
  if (method === "GET") {
    const url = new URL("/v1/get-jobs", API_BASE);
    url.searchParams.set("search_query", searchQuery);
    const res = await fetch(url);
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  }

  const res = await fetch(new URL("/v1/get-jobs", API_BASE), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      search_query: searchQuery,
      force_refresh: forceRefresh,
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function startPolling(searchQuery) {
  stopPolling();
  pollTimer = setInterval(async () => {
    try {
      const data = await fetchJobs({
        searchQuery,
        forceRefresh: false,
        method: "GET",
      });
      if (data.jobs?.length) {
        setStatus(`Ready — ${data.jobs.length} job(s) for “${searchQuery}”.`);
        renderJobs(data.jobs);
        stopPolling();
      } else {
        setStatus("Still scraping… polling every 3s.");
      }
    } catch (err) {
      setStatus(String(err.message || err), true);
      stopPolling();
    }
  }, 3000);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  stopPolling();

  const searchQuery = queryInput.value.trim();
  const forceRefresh = forceRefreshInput.checked;
  const button = form.querySelector("button");
  button.disabled = true;
  setStatus("Requesting jobs…");
  resultsEl.innerHTML = "";

  try {
    const data = await fetchJobs({
      searchQuery,
      forceRefresh,
      method: "POST",
    });
    renderJobs(data.jobs);

    if (data.status === "ready") {
      setStatus(`Ready — ${data.jobs.length} job(s) for “${searchQuery}”.`);
    } else {
      setStatus(
        `${data.message || "Scrape triggered."}${data.run_id ? ` Run: ${data.run_id}` : ""}`,
      );
      startPolling(searchQuery);
    }
  } catch (err) {
    setStatus(String(err.message || err), true);
  } finally {
    button.disabled = false;
  }
});
