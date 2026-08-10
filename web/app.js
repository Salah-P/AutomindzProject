const API_BASE = localStorage.getItem("API_BASE") || "";

const form = document.getElementById("search-form");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");
const titleInput = document.getElementById("job-title");

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

function shortDescription(text, maxLen = 220) {
  const cleaned = String(text || "").replace(/\s+/g, " ").trim();
  if (cleaned.length <= maxLen) return cleaned;
  return `${cleaned.slice(0, maxLen - 1)}…`;
}

function renderJobs(jobs) {
  resultsEl.innerHTML = "";
  if (!jobs?.length) {
    resultsEl.innerHTML = "<p class='meta'>No matching jobs found.</p>";
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

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const jobTitle = titleInput.value.trim();
  const button = form.querySelector("button");
  button.disabled = true;
  setStatus("Scraping WeWorkRemotely… this can take a few seconds.");
  resultsEl.innerHTML = "";

  try {
    const url = new URL("/v1/get-jobs", API_BASE || window.location.origin);
    url.searchParams.set("job_title", jobTitle);
    const res = await fetch(url);
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = body.detail || res.statusText || "Request failed";
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    setStatus(`Found ${body.count} job(s) for “${jobTitle}”.`);
    renderJobs(body.jobs);
  } catch (err) {
    setStatus(String(err.message || err), true);
  } finally {
    button.disabled = false;
  }
});
