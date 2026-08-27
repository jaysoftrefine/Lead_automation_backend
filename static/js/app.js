/**
 * LeadPulse AI - Dashboard Application Logic
 */

document.addEventListener("DOMContentLoaded", () => {
  // Initialize Lucide Icons
  if (window.lucide) {
    lucide.createIcons();
  }

  // --- API Base URL ---
  const API_BASE = window.location.origin.includes(":8000") ? "" : "http://127.0.0.1:8000";

  // --- State ---
  let pipelinePollingInterval = null;
  let currentLeads = [];

  // --- DOM Elements ---
  const tabButtons = document.querySelectorAll(".tab-btn");
  const tabPanels = document.querySelectorAll(".tab-panel");
  const btnRefreshAll = document.getElementById("btn-refresh-all");

  // Status Elements
  const mongoStatusPill = document.getElementById("mongo-status-pill");
  const activeLlmLabel = document.getElementById("active-llm-label");
  const activeTavilyLabel = document.getElementById("active-tavily-label");

  // Stat Elements
  const statTotalLeads = document.getElementById("stat-total-leads");
  const statTotalContacts = document.getElementById("stat-total-contacts");
  const statTotalRaw = document.getElementById("stat-total-raw");
  const statAvgScore = document.getElementById("stat-avg-score");
  const leadsCountBadge = document.getElementById("leads-count-badge");
  const pipelineRunningBadge = document.getElementById("pipeline-running-badge");

  // Pipeline Form Elements
  const pipelineForm = document.getElementById("pipeline-form");
  const btnStartPipeline = document.getElementById("btn-start-pipeline");
  const btnStopPipeline = document.getElementById("btn-stop-pipeline");
  const liveIndicator = document.getElementById("live-indicator");
  const liveIndicatorText = document.getElementById("live-indicator-text");
  const progressBox = document.getElementById("progress-box");
  const progressJobLabel = document.getElementById("progress-job-label");
  const progressCountLabel = document.getElementById("progress-count-label");
  const progressFill = document.getElementById("progress-fill");
  const terminalLogs = document.getElementById("terminal-logs");
  const terminalPlaceholder = document.getElementById("terminal-placeholder");
  const terminalWindow = document.getElementById("terminal-window");
  const btnClearLogs = document.getElementById("btn-clear-logs");
  const lastUpdatedText = document.getElementById("last-updated-text");

  // Leads Tab Elements
  const leadSearchInput = document.getElementById("lead-search-input");
  const filterCompanySize = document.getElementById("filter-company-size");
  const filterSite = document.getElementById("filter-site");
  const filterStatus = document.getElementById("filter-status");
  const filterMinScore = document.getElementById("filter-min-score");
  const btnExportCsv = document.getElementById("btn-export-csv");
  const btnRefreshLeads = document.getElementById("btn-refresh-leads");
  const leadsListContainer = document.getElementById("leads-list-container");
  const leadsLoadingSpinner = document.getElementById("leads-loading-spinner");
  const leadsEmptyState = document.getElementById("leads-empty-state");

  // Instant Lab Elements
  const instantForm = document.getElementById("instant-research-form");
  const btnRunLab = document.getElementById("btn-run-lab");
  const labResultPlaceholder = document.getElementById("lab-result-placeholder");
  const labLoading = document.getElementById("lab-loading");
  const labResultContent = document.getElementById("lab-result-content");

  // Modal Elements
  const leadModal = document.getElementById("lead-modal");
  const btnCloseModal = document.getElementById("btn-close-modal");
  const modalBodyContent = document.getElementById("modal-body-content");
  const modalTitle = document.getElementById("modal-title");
  const modalCompanyBadge = document.getElementById("modal-company-badge");

  // --- Toast Notifications ---
  function showToast(message, type = "success") {
    const container = document.getElementById("toast-container");
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.innerHTML = `
      <i data-lucide="${type === 'success' ? 'check-circle' : 'alert-triangle'}"></i>
      <span>${escapeHtml(message)}</span>
    `;
    container.appendChild(toast);
    if (window.lucide) lucide.createIcons();
    setTimeout(() => {
      toast.remove();
    }, 4000);
  }

  function escapeHtml(text) {
    if (!text) return "";
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // --- Tab Navigation ---
  tabButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      const targetTabId = btn.getAttribute("data-tab");
      tabButtons.forEach(b => b.classList.remove("active"));
      tabPanels.forEach(p => p.classList.remove("active"));

      btn.classList.add("active");
      const targetPanel = document.getElementById(targetTabId);
      if (targetPanel) targetPanel.classList.add("active");

      if (targetTabId === "tab-leads") {
        fetchLeads();
      }
    });
  });

  // --- Fetch System Stats ---
  async function fetchStats() {
    try {
      const res = await fetch(`${API_BASE}/api/stats`);
      if (!res.ok) throw new Error("Failed to fetch stats");
      const data = await res.json();

      // Update Mongo Status Pill
      if (data.db_connected) {
        mongoStatusPill.className = "status-pill online";
        mongoStatusPill.innerHTML = `<span class="status-dot"></span><span class="status-text">MongoDB: Connected (${data.database_name})</span>`;
      } else {
        mongoStatusPill.className = "status-pill offline";
        mongoStatusPill.innerHTML = `<span class="status-dot"></span><span class="status-text">MongoDB: Disconnected</span>`;
      }

      // Update LLM & Tavily Pills
      activeLlmLabel.innerText = `LLM: ${data.default_provider.toUpperCase()}`;
      activeTavilyLabel.innerText = data.tavily_configured ? "Tavily: Configured" : "Tavily: Missing Key";

      // Update Counts
      statTotalLeads.innerText = data.leads_count || 0;
      statTotalContacts.innerText = data.total_contacts_discovered || 0;
      statTotalRaw.innerText = data.raw_jobs_count || 0;
      statAvgScore.innerText = data.avg_relevance_score ? data.avg_relevance_score.toFixed(1) : "0.0";
      leadsCountBadge.innerText = data.leads_count || 0;

      // Running Badge
      if (data.is_pipeline_running) {
        pipelineRunningBadge.style.display = "inline-block";
      } else {
        pipelineRunningBadge.style.display = "none";
      }
    } catch (err) {
      console.error("Error fetching stats:", err);
      mongoStatusPill.className = "status-pill offline";
      mongoStatusPill.innerHTML = `<span class="status-dot"></span><span class="status-text">MongoDB: Offline</span>`;
    }
  }

  // --- Pipeline Controls ---
  async function startPipeline(e) {
    e.preventDefault();

    const searchTerm = document.getElementById("search-term").value.trim();
    const location = document.getElementById("search-location").value.trim();
    const companySize = document.getElementById("company-size") ? document.getElementById("company-size").value : "small";
    const jobType = document.getElementById("job-type") ? document.getElementById("job-type").value : "all";
    const hoursOld = document.getElementById("hours-old") ? parseInt(document.getElementById("hours-old").value, 10) : 72;
    const skipExisting = document.getElementById("skip-existing") ? document.getElementById("skip-existing").checked : true;
    const limit = parseInt(document.getElementById("search-limit").value, 10);
    const minScore = parseInt(document.getElementById("min-score").value, 10);
    const provider = document.getElementById("pipeline-provider").value;

    const checkedSites = Array.from(document.querySelectorAll("input[name='sites']:checked")).map(el => el.value);

    if (!searchTerm) {
      showToast("Please enter a target job title or keyword.", "error");
      return;
    }

    if (checkedSites.length === 0) {
      showToast("Please select at least one job platform.", "error");
      return;
    }

    btnStartPipeline.disabled = true;
    btnStartPipeline.innerHTML = `<div class="spinner" style="width: 16px; height: 16px; border-width: 2px;"></div> Starting...`;

    // Clear previous logs & reset progress bar immediately
    terminalLogs.innerHTML = "";
    terminalPlaceholder.style.display = "none";
    progressBox.style.display = "block";
    progressFill.style.width = "5%";
    progressCountLabel.innerText = "0 / ?";
    progressJobLabel.innerText = `Connecting to ${checkedSites.join(", ")} and scraping '${searchTerm}' (Target: ${companySize.toUpperCase()} [Max 50], ${jobType.toUpperCase()}, ${hoursOld > 0 ? (hoursOld + 'h') : 'All dates'})...`;

    try {
      const res = await fetch(`${API_BASE}/api/pipeline/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          search_term: searchTerm,
          location: location,
          company_size: companySize,
          job_type: jobType,
          hours_old: hoursOld,
          skip_existing: skipExisting,
          sites: checkedSites,
          limit: limit,
          min_score: minScore,
          provider: provider,
        }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Failed to start pipeline");
      }

      showToast(`Pipeline launched for "${searchTerm}"!`, "success");
      setPipelineUIState(true);
      startPipelinePolling();
    } catch (err) {
      showToast(err.message, "error");
      btnStartPipeline.disabled = false;
      btnStartPipeline.innerHTML = `<i data-lucide="play"></i><span>Start Autonomous Lead Pipeline</span>`;
      if (window.lucide) lucide.createIcons();
    }
  }

  async function stopPipeline() {
    btnStopPipeline.disabled = true;
    try {
      const res = await fetch(`${API_BASE}/api/pipeline/stop`, { method: "POST" });
      const data = await res.json();
      showToast(data.message, "success");
    } catch (err) {
      showToast("Failed to request stop", "error");
    }
  }

  function setPipelineUIState(isRunning) {
    const pulseDot = liveIndicator.querySelector(".pulse-dot");
    if (isRunning) {
      btnStartPipeline.style.display = "none";
      btnStopPipeline.style.display = "inline-flex";
      btnStopPipeline.disabled = false;
      progressBox.style.display = "block";
      pulseDot.classList.add("running");
      liveIndicatorText.innerText = "Running";
      pipelineRunningBadge.style.display = "inline-block";
    } else {
      btnStartPipeline.style.display = "inline-flex";
      btnStartPipeline.disabled = false;
      btnStartPipeline.innerHTML = `<i data-lucide="play"></i><span>Start Autonomous Lead Pipeline</span>`;
      btnStopPipeline.style.display = "none";
      pulseDot.classList.remove("running");
      liveIndicatorText.innerText = "Idle";
      pipelineRunningBadge.style.display = "none";
      if (window.lucide) lucide.createIcons();
    }
  }

  function startPipelinePolling() {
    if (pipelinePollingInterval) clearInterval(pipelinePollingInterval);
    pipelinePollingInterval = setInterval(pollPipelineStatus, 1500);
    pollPipelineStatus();
  }

  async function pollPipelineStatus() {
    try {
      const res = await fetch(`${API_BASE}/api/pipeline/status`);
      if (!res.ok) return;
      const data = await res.json();

      lastUpdatedText.innerText = `Updated: ${new Date().toLocaleTimeString()}`;

      // Update Progress UI
      if (data.is_running) {
        setPipelineUIState(true);
        const total = data.total_count || 1;
        const current = data.processed_count || 0;
        const pct = Math.min(100, Math.round((current / total) * 100));

        progressFill.style.width = `${pct}%`;
        progressCountLabel.innerText = `${current} / ${data.total_count || '?'}`;

        if (data.status === "scraping") {
          progressJobLabel.innerText = `Scraping job listings from selected platforms...`;
        } else if (data.status === "enriching" && data.current_company) {
          progressJobLabel.innerText = `Analyzing: ${data.current_job_title} @ ${data.current_company}`;
        }
      } else {
        if (data.status === "completed" || data.status === "error") {
          setPipelineUIState(false);
          clearInterval(pipelinePollingInterval);
          pipelinePollingInterval = null;
          progressJobLabel.innerText = data.status === "completed" ? "Pipeline complete! Ready for next search." : `Pipeline halted: ${data.error_message || 'Error'}`;
          fetchStats(); // Update counters
        }
      }

      // Render Logs
      if (data.logs && data.logs.length > 0) {
        terminalPlaceholder.style.display = "none";
        terminalLogs.innerHTML = data.logs
          .map(
            log => `
            <div class="log-entry">
              <span class="log-time">[${log.time}]</span>
              <span class="log-msg ${log.level}">${escapeHtml(log.message)}</span>
            </div>
          `
          )
          .join("");
        terminalWindow.scrollTop = terminalWindow.scrollHeight;
      }
    } catch (err) {
      console.error("Pipeline poll error:", err);
    }
  }

  // --- Leads Explorer & Fetch ---
  async function fetchLeads() {
    leadsLoadingSpinner.style.display = "flex";
    leadsEmptyState.style.display = "none";
    leadsListContainer.innerHTML = "";

    const search = leadSearchInput.value.trim();
    const companySize = filterCompanySize ? filterCompanySize.value : "all";
    const filterJobType = document.getElementById("filter-job-type");
    const jobType = filterJobType ? filterJobType.value : "all";
    const filterDatePosted = document.getElementById("filter-date-posted");
    const dateVal = filterDatePosted ? filterDatePosted.value : "all";
    const site = filterSite.value;
    const status = filterStatus.value;
    const minScore = filterMinScore.value;

    const params = new URLSearchParams();
    if (search) params.append("search", search);
    if (companySize && companySize !== "all") params.append("company_size", companySize);
    if (jobType && jobType !== "all") params.append("job_type", jobType);
    if (dateVal && dateVal !== "all") params.append("hours_old", dateVal);
    if (site && site !== "all") params.append("site", site);
    if (status && status !== "all") params.append("status", status);
    if (minScore > 0) params.append("min_score", minScore);

    // Update export CSV link
    if (btnExportCsv) {
      btnExportCsv.href = `${API_BASE}/api/export/csv?${params.toString()}`;
    }

    try {
      const res = await fetch(`${API_BASE}/api/leads?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to fetch leads");
      const data = await res.json();
      currentLeads = data.leads || [];

      leadsLoadingSpinner.style.display = "none";

      if (currentLeads.length === 0) {
        leadsEmptyState.style.display = "flex";
        return;
      }

      renderLeads(currentLeads);
    } catch (err) {
      leadsLoadingSpinner.style.display = "none";
      showToast(err.message, "error");
    }
  }

  function renderLeads(leads) {
    leadsListContainer.innerHTML = leads.map(lead => {
      const score = lead.relevance_score || 0;
      let scoreClass = "score-low";
      if (score >= 70) scoreClass = "score-high";
      else if (score >= 40) scoreClass = "score-med";

      const contacts = lead.contacts || [];
      const domainDisplay = lead.company_domain ? `<a href="https://${lead.company_domain}" target="_blank" class="domain-link"><i data-lucide="external-link" style="width: 12px; height: 12px; display: inline;"></i> ${escapeHtml(lead.company_domain)}</a>` : "";

      const contactsHtml = contacts.length > 0 
        ? contacts.slice(0, 3).map(c => {
            const roleLower = (c.role || "").toLowerCase();
            let roleBadge = "";
            if (roleLower.includes("founder") || roleLower.includes("owner")) roleBadge = "👑 ";
            else if (roleLower.includes("ceo")) roleBadge = "👑 ";
            else if (roleLower.includes("cto")) roleBadge = "⚡ ";
            else if (roleLower.includes("coo")) roleBadge = "💼 ";
            else if (roleLower.includes("director")) roleBadge = "🎯 ";
            else if (roleLower.includes("product") || roleLower.includes("pm")) roleBadge = "🚀 ";

            return `
            <div class="contact-person-card">
              <div class="contact-info-col">
                <span class="contact-name-role">${escapeHtml(c.name || "Executive Decision Maker")}</span>
                <span class="contact-role-sub">${roleBadge}<strong style="color:#e2e8f0;">${escapeHtml(c.role || "Founder / Executive")}</strong></span>
                ${c.email ? `
                  <div style="display:flex; align-items:center; gap:6px; margin-top:2px;">
                    <span style="font-size:0.75rem; color:var(--accent-cyan); font-weight:500;">✉ ${escapeHtml(c.email)}</span>
                    ${c.is_verified ? `<span class="badge-verified-email" title="${escapeHtml(c.verification_details || 'MX & SMTP Validated')}">✓ Verified</span>` : ''}
                  </div>` : ''}
              </div>
              <div class="contact-actions">
                ${c.email ? `<button class="btn-copy-email" onclick="window.copyToClipboard('${c.email}', this)"><i data-lucide="copy" style="width:12px;height:12px;display:inline;"></i> Copy</button>` : ''}
                ${c.linkedin_url ? `<a href="${c.linkedin_url}" target="_blank" class="domain-link" title="Open LinkedIn Profile"><i data-lucide="linkedin" style="width:14px;height:14px;display:inline;"></i></a>` : ''}
              </div>
            </div>
          `;
          }).join("")
        : `<span class="text-muted" style="font-size: 0.75rem;">No direct leadership contacts found. Official company domain available.</span>`;

      const techTags = (lead.key_technologies || []).slice(0, 5).map(t => `<span class="tech-tag">${escapeHtml(t)}</span>`).join("");

      return `
        <div class="lead-card">
          <div class="lead-card-header">
            <div class="lead-company-box">
              <span class="company-name">${escapeHtml(lead.company)}</span>
              ${domainDisplay}
            </div>
            <span class="lead-score-pill ${scoreClass}">${score}/100 Score</span>
          </div>

          <div class="lead-job-title">
            <a href="${escapeHtml(lead.job_url)}" target="_blank" class="job-title-link" title="Open Job Posting on ${escapeHtml(lead.site.toUpperCase())}">
              <span>${escapeHtml(lead.title)}</span>
              <i data-lucide="external-link" style="width:13px;height:13px;opacity:0.85;"></i>
            </a>
          </div>

          <div class="lead-meta-row">
            <span class="lead-meta-pill" style="color: var(--accent-emerald); background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.2);"><i data-lucide="building-2" style="width:12px;height:12px;"></i> ${escapeHtml(lead.company_size || "11-50 employees")}</span>
            <span class="lead-meta-pill" style="color: #f59e0b; background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.2);"><i data-lucide="briefcase" style="width:12px;height:12px;"></i> ${escapeHtml(lead.job_type || "Contract")}</span>
            <a href="${escapeHtml(lead.job_url)}" target="_blank" class="lead-meta-pill platform-link-pill" title="View Original Post on ${escapeHtml(lead.site.toUpperCase())}">
              <i data-lucide="${lead.site.toLowerCase() === 'linkedin' ? 'linkedin' : 'globe'}" style="width:12px;height:12px;"></i>
              ${escapeHtml(lead.site.toUpperCase())} Post
            </a>
            <span class="lead-meta-pill"><i data-lucide="map-pin" style="width:12px;height:12px;"></i> ${escapeHtml(lead.location || "Remote")}</span>
            ${lead.hiring_urgency ? `<span class="lead-meta-pill text-amber"><i data-lucide="clock" style="width:12px;height:12px;"></i> ${escapeHtml(lead.hiring_urgency)} Urgency</span>` : ''}
          </div>

          <!-- Contacts Box -->
          <div class="lead-contacts-section">
            <div class="contacts-header">
              <i data-lucide="users" style="width: 14px; height: 14px;"></i>
              <span>Key Leadership & Decision Makers (${contacts.length})</span>
            </div>
            ${contactsHtml}
          </div>

          <!-- Post / Job Description Excerpt -->
          <div class="lead-post-snippet">
            <div class="snippet-label">
              <i data-lucide="file-text" style="width:12px;height:12px;"></i>
              <span>Original Post Excerpt</span>
            </div>
            <p class="post-text-content">${escapeHtml(lead.job_description || lead.lead_summary || lead.company_summary || 'No post text preview available.')}</p>
          </div>

          ${techTags ? `<div class="tech-tags-list">${techTags}</div>` : ''}

          <div class="lead-card-footer">
            <span class="text-muted">Status: <strong class="text-highlight">${escapeHtml(lead.status || 'new')}</strong></span>
            <div class="lead-card-actions">
              <a href="${escapeHtml(lead.job_url)}" target="_blank" class="btn btn-secondary btn-sm btn-job-post-link" title="Open Job on ${escapeHtml(lead.site.toUpperCase())}">
                <i data-lucide="${lead.site.toLowerCase() === 'linkedin' ? 'linkedin' : 'external-link'}" style="width: 13px; height: 13px;"></i>
                <span>${lead.site.toLowerCase() === 'linkedin' ? 'LinkedIn Post' : (escapeHtml(lead.site.toUpperCase()) + ' Post')}</span>
              </a>
              <button class="btn btn-secondary btn-sm" onclick="window.openLeadModal('${encodeURIComponent(lead.job_url)}')">
                <i data-lucide="eye" style="width: 14px; height: 14px;"></i>
                <span>View Details</span>
              </button>
            </div>
          </div>
        </div>
      `;
    }).join("");

    if (window.lucide) lucide.createIcons();
  }

  // --- Lead Details Modal ---
  window.openLeadModal = function(encodedUrl) {
    const targetUrl = decodeURIComponent(encodedUrl);
    const lead = currentLeads.find(l => l.job_url === targetUrl);
    if (!lead) return;

    modalCompanyBadge.innerText = lead.site.toUpperCase();
    modalTitle.innerText = `${lead.title} @ ${lead.company}`;

    const contactsList = (lead.contacts || []).map(c => `
      <div class="contact-person-card" style="margin-bottom: 0.5rem;">
        <div class="contact-info-col">
          <strong style="color:#fff;">${escapeHtml(c.name || 'Decision Maker')}</strong>
          <span style="color:var(--text-muted); font-size: 0.75rem;">${escapeHtml(c.role || 'Role')} • Confidence: ${c.confidence_score || 50}%</span>
          ${c.email ? `<div style="display:flex; align-items:center; gap:8px; margin-top:2px;">
            <span style="color:var(--accent-cyan); font-size: 0.8rem;">✉ ${escapeHtml(c.email)}</span>
            ${c.is_verified ? `<span class="badge-verified-email" title="${escapeHtml(c.verification_details || 'MX & SMTP Validated')}">✓ SMTP & MX Verified</span>` : ''}
          </div>` : ''}
          ${c.phone ? `<span style="color:#a78bfa; font-size: 0.8rem;">☎ ${escapeHtml(c.phone)}</span>` : ''}
          ${c.verification_details ? `<span style="color:#94a3b8; font-size: 0.7rem; font-style:italic;">${escapeHtml(c.verification_details)}</span>` : ''}
        </div>
        ${c.linkedin_url ? `<a href="${c.linkedin_url}" target="_blank" class="btn btn-secondary btn-sm">LinkedIn Profile</a>` : ''}
      </div>
    `).join("");

    const queriesList = (lead.search_queries_used || []).map(q => `<li style="font-size:0.8rem; color:var(--text-secondary);">${escapeHtml(q)}</li>`).join("");

    modalBodyContent.innerHTML = `
      <!-- Prominent Source Job Post URL Banner -->
      <div class="modal-source-banner">
        <div class="source-info">
          <span class="source-label">
            <i data-lucide="${lead.site.toLowerCase() === 'linkedin' ? 'linkedin' : 'globe'}" style="width:14px;height:14px;color:#38bdf8;"></i>
            <span>Origin: <strong>${escapeHtml(lead.site.toUpperCase())} Job Posting</strong></span>
          </span>
          <a href="${escapeHtml(lead.job_url)}" target="_blank" class="source-url-text" title="Open ${escapeHtml(lead.job_url)}">
            ${escapeHtml(lead.job_url)}
          </a>
        </div>
        <div class="source-btns">
          <button class="btn btn-secondary btn-sm" onclick="window.copyToClipboard('${escapeHtml(lead.job_url)}', this)">
            <i data-lucide="copy" style="width:12px;height:12px;"></i> Copy URL
          </button>
          <a href="${escapeHtml(lead.job_url)}" target="_blank" class="btn btn-primary btn-sm">
            <i data-lucide="external-link" style="width:13px;height:13px;"></i> Open ${lead.site.toLowerCase() === 'linkedin' ? 'LinkedIn' : lead.site.toUpperCase()} Post
          </a>
        </div>
      </div>

      <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 0.5rem;">
        <span class="lead-meta-pill" style="color: var(--accent-emerald); background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.2);"><i data-lucide="building-2" style="width:12px;height:12px;"></i> Size: ${escapeHtml(lead.company_size || '11-50 employees')}</span>
        <span class="lead-meta-pill" style="color: #f59e0b; background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.2);"><i data-lucide="briefcase" style="width:12px;height:12px;"></i> ${escapeHtml(lead.job_type || 'Contract')}</span>
        <span class="lead-meta-pill"><i data-lucide="map-pin" style="width:12px;height:12px;"></i> ${escapeHtml(lead.location || 'Remote')}</span>
        <span class="lead-meta-pill"><i data-lucide="globe" style="width:12px;height:12px;"></i> ${escapeHtml(lead.site.toUpperCase())}</span>
        ${lead.hiring_urgency ? `<span class="lead-meta-pill text-amber"><i data-lucide="clock" style="width:12px;height:12px;"></i> ${escapeHtml(lead.hiring_urgency)} Urgency</span>` : ''}
      </div>

      <div>
        <h4 style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 0.4rem;">OPPORTUNITY SUMMARY</h4>
        <p style="font-size: 0.9rem; color: #e2e8f0;">${escapeHtml(lead.lead_summary || lead.company_summary || 'No summary available.')}</p>
      </div>

      <div>
        <h4 style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 0.4rem;">FOUNDERS & EXECUTIVE DECISION MAKERS (CEO, CTO, DIRECTORS, PM)</h4>
        ${contactsList || '<p class="text-muted">No individual leadership contacts found.</p>'}
      </div>

      ${queriesList ? `
      <div>
        <h4 style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 0.4rem;">TAVILY SEARCH QUERIES EXECUTED</h4>
        <ul style="padding-left: 1.25rem;">${queriesList}</ul>
      </div>` : ''}

      <div>
        <h4 style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 0.4rem;">ORIGINAL LINKEDIN JOB POST / DESCRIPTION</h4>
        <div class="job-description-box">${escapeHtml(lead.job_description || lead.lead_summary || 'No original post description recorded.')}</div>
      </div>

      <div>
        <h4 style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 0.4rem;">AGENT THINKING & RESEARCH TRAIL</h4>
        <div class="reasoning-box">${escapeHtml(lead.agent_thinking_process || 'Thinking process was recorded into final synthesis.')}</div>
      </div>

      <div style="display:flex; justify-content:space-between; align-items:center; border-top:1px solid var(--border-subtle); padding-top:1rem;">
        <div style="display:flex; align-items:center; gap:0.5rem;">
          <span style="font-size:0.8rem; color:var(--text-muted);">Change Status:</span>
          <select class="custom-select" style="padding:0.35rem 0.75rem;" onchange="window.updateStatus('${encodeURIComponent(lead.job_url)}', this.value)">
            <option value="new" ${lead.status === 'new' ? 'selected' : ''}>New</option>
            <option value="contacted" ${lead.status === 'contacted' ? 'selected' : ''}>Contacted</option>
            <option value="qualified" ${lead.status === 'qualified' ? 'selected' : ''}>Qualified</option>
            <option value="rejected" ${lead.status === 'rejected' ? 'selected' : ''}>Rejected</option>
            <option value="archived" ${lead.status === 'archived' ? 'selected' : ''}>Archived</option>
          </select>
        </div>
        <a href="${lead.job_url}" target="_blank" class="btn btn-primary btn-sm">
          <i data-lucide="external-link" style="width:14px;height:14px;"></i> Original Job Post
        </a>
      </div>
    `;

    leadModal.style.display = "flex";
    if (window.lucide) lucide.createIcons();
  };

  window.updateStatus = async function(encodedUrl, newStatus) {
    const jobUrl = decodeURIComponent(encodedUrl);
    try {
      const res = await fetch(`${API_BASE}/api/leads/update-status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_url: jobUrl, status: newStatus }),
      });
      if (res.ok) {
        showToast(`Lead status updated to ${newStatus}`, "success");
        fetchLeads();
      }
    } catch (err) {
      showToast("Failed to update status", "error");
    }
  };

  window.copyToClipboard = function(text, btnElement) {
    navigator.clipboard.writeText(text).then(() => {
      const originalText = btnElement.innerHTML;
      btnElement.innerText = "✓ Copied!";
      setTimeout(() => {
        btnElement.innerHTML = originalText;
        if (window.lucide) lucide.createIcons();
      }, 2000);
      showToast(`Copied ${text} to clipboard!`, "success");
    });
  };

  // Close Modal
  btnCloseModal.addEventListener("click", () => {
    leadModal.style.display = "none";
  });
  window.addEventListener("click", (e) => {
    if (e.target === leadModal) {
      leadModal.style.display = "none";
    }
  });

  // --- Direct LinkedIn Post & Single Job Scraper ---
  instantForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = document.getElementById("lab-url") ? document.getElementById("lab-url").value.trim() : "";
    const company = document.getElementById("lab-company") ? document.getElementById("lab-company").value.trim() : "";
    const title = document.getElementById("lab-title") ? document.getElementById("lab-title").value.trim() : "";
    const size = document.getElementById("lab-size") ? document.getElementById("lab-size").value : "small";
    const jobType = document.getElementById("lab-type") ? document.getElementById("lab-type").value : "contract";
    const desc = document.getElementById("lab-description") ? document.getElementById("lab-description").value.trim() : "";
    const saveToDb = document.getElementById("lab-save-db") ? document.getElementById("lab-save-db").checked : true;

    if (!url && !company && !desc) {
      showToast("Please provide a LinkedIn URL, company name, or post text.", "error");
      return;
    }

    labResultPlaceholder.style.display = "none";
    labLoading.style.display = "flex";
    labResultContent.style.display = "none";
    btnRunLab.disabled = true;
    btnRunLab.innerHTML = `<div class="spinner" style="width: 16px; height: 16px; border-width: 2px;"></div> Scrapiing & Researching...`;

    try {
      const res = await fetch(`${API_BASE}/api/scrape/linkedin-post`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: url || null,
          raw_text: desc || null,
          company: company || null,
          title: title || null,
          target_company_size: size,
          target_job_type: jobType,
          save_to_db: saveToDb,
        }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Agent research on LinkedIn post failed");

      labLoading.style.display = "none";
      labResultContent.style.display = "block";

      const lead = data.lead;
      const contacts = lead.contacts || [];

      labResultContent.innerHTML = `
        <div style="background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: var(--radius-md); padding: 1rem; margin-bottom: 1rem;">
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem; margin-bottom:0.4rem;">
            <h3 style="color: #34d399; font-size: 1rem; margin: 0;">✓ Scraped: ${escapeHtml(lead.company)} - ${escapeHtml(lead.title)}</h3>
            <span class="lead-score-pill score-high">${lead.relevance_score}/100 Score</span>
          </div>
          <p style="font-size: 0.85rem; color: #cbd5e1; margin-bottom: 0.5rem;">${escapeHtml(lead.lead_summary || lead.company_summary || '')}</p>
          <div style="display: flex; gap: 6px; flex-wrap: wrap;">
            <span class="lead-meta-pill"><i data-lucide="building-2"></i> ${escapeHtml(lead.company_size || 'Small')}</span>
            <span class="lead-meta-pill"><i data-lucide="briefcase"></i> ${escapeHtml(lead.job_type || 'Contract')}</span>
            ${lead.job_url ? `<a href="${escapeHtml(lead.job_url)}" target="_blank" class="lead-meta-pill platform-link-pill"><i data-lucide="external-link"></i> Original Post Link</a>` : ''}
          </div>
        </div>

        <div style="margin-bottom: 1rem;">
          <h4 style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 0.5rem;">KEY LEADERSHIP & VERIFIED EMAILS (${contacts.length})</h4>
          ${contacts.map(c => `
            <div class="contact-person-card" style="margin-bottom: 0.4rem;">
              <div>
                <strong>${escapeHtml(c.name || 'Leadership Contact')}</strong> - <span style="color:#e2e8f0;">${escapeHtml(c.role || 'Executive')}</span>
                ${c.email ? `
                  <div style="display:flex; align-items:center; gap:6px; margin-top:2px;">
                    <span style="color:var(--accent-cyan); font-size: 0.8rem; font-weight:500;">✉ ${escapeHtml(c.email)}</span>
                    ${c.is_verified ? `<span class="badge-verified-email">✓ Verified</span>` : ''}
                  </div>` : ''}
              </div>
              <div style="display:flex; gap:6px;">
                ${c.email ? `<button class="btn-copy-email" onclick="window.copyToClipboard('${c.email}', this)"><i data-lucide="copy" style="width:12px;height:12px;"></i> Copy</button>` : ''}
                ${c.linkedin_url ? `<a href="${c.linkedin_url}" target="_blank" class="btn btn-secondary btn-sm" title="View LinkedIn Profile">LinkedIn</a>` : ''}
              </div>
            </div>
          `).join('') || '<p class="text-muted">No individual direct contacts found. Domain: ' + escapeHtml(lead.company_domain || 'N/A') + '</p>'}
        </div>

        <div>
          <h4 style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 0.5rem;">AGENT THINKING PROCESS & SEARCH EVIDENCE</h4>
          <div class="reasoning-box">${escapeHtml(lead.agent_thinking_process || 'Agent reasoning completed.')}</div>
        </div>
      `;

      if (saveToDb) {
        showToast(`Saved ${lead.company} to Leads Explorer!`, "success");
        fetchStats();
        fetchLeads();
      }
    } catch (err) {
      labLoading.style.display = "none";
      showToast(err.message, "error");
    } finally {
      btnRunLab.disabled = false;
      btnRunLab.innerHTML = `<i data-lucide="sparkles"></i><span>Scrape & Enrich LinkedIn Post</span>`;
      if (window.lucide) lucide.createIcons();
    }
  });

  // --- Event Listeners ---
  pipelineForm.addEventListener("submit", startPipeline);
  btnStopPipeline.addEventListener("click", stopPipeline);
  btnClearLogs.addEventListener("click", () => {
    terminalLogs.innerHTML = "";
    terminalPlaceholder.style.display = "flex";
  });
  btnRefreshAll.addEventListener("click", () => {
    fetchStats();
    fetchLeads();
    showToast("Dashboard refreshed", "success");
  });
  btnRefreshLeads.addEventListener("click", fetchLeads);
  const btnClearDbUi = document.getElementById("btn-clear-db-ui");
  if (btnClearDbUi) {
    btnClearDbUi.addEventListener("click", async () => {
      if (!confirm("Are you sure you want to clear all leads from MongoDB? This will allow you to re-scrape from scratch.")) return;
      try {
        const res = await fetch(`${API_BASE}/api/database/clear`, { method: "POST" });
        const data = await res.json();
        showToast(data.message || "Database cleared successfully!", "success");
        fetchStats();
        fetchLeads();
      } catch (err) {
        showToast("Failed to clear database: " + err.message, "error");
      }
    });
  }
  leadSearchInput.addEventListener("input", debounce(fetchLeads, 400));
  if (filterCompanySize) filterCompanySize.addEventListener("change", fetchLeads);
  const filterJobTypeEl = document.getElementById("filter-job-type");
  if (filterJobTypeEl) filterJobTypeEl.addEventListener("change", fetchLeads);
  const filterDatePostedEl = document.getElementById("filter-date-posted");
  if (filterDatePostedEl) filterDatePostedEl.addEventListener("change", fetchLeads);
  filterSite.addEventListener("change", fetchLeads);
  filterStatus.addEventListener("change", fetchLeads);
  filterMinScore.addEventListener("input", debounce(fetchLeads, 400));

  function debounce(fn, delay) {
    let timer = null;
    return function (...args) {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), delay);
    };
  }

  // --- Initial Load ---
  fetchStats();
  fetchLeads();
  pollPipelineStatus(); // Resume polling if a task was already in progress
});
