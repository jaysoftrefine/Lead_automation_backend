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
      } else if (targetTabId === "tab-eu-startups") {
        loadEUStats();
        loadEUOptions();
        loadEUStartups(1);
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

    const isRemote = document.getElementById("is-remote-only") ? document.getElementById("is-remote-only").checked : true;

    try {
      const res = await fetch(`${API_BASE}/api/pipeline/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          search_term: searchTerm,
          location: location,
          is_remote: isRemote,
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
        const totalGoal = data.total_count || 10;
        const savedSoFar = data.processed_count || (data.metrics ? data.metrics.saved_to_db : 0);
        const pct = Math.min(100, Math.round((savedSoFar / totalGoal) * 100));

        progressFill.style.width = `${pct}%`;
        progressCountLabel.innerText = `${savedSoFar} / ${totalGoal} Qualified Leads`;

        if (data.status === "scraping") {
          progressJobLabel.innerText = `Scraping candidates across platforms to find ${totalGoal} qualified leads...`;
        } else if (data.status === "enriching" && data.current_company) {
          progressJobLabel.innerText = `[${savedSoFar}/${totalGoal} Found] Qualifying: ${data.current_job_title} @ ${data.current_company}`;
        }
      } else {
        if (data.status === "completed" || data.status === "error") {
          setPipelineUIState(false);
          clearInterval(pipelinePollingInterval);
          pipelinePollingInterval = null;

          if (data.status === "completed") {
            const savedTotal = (data.metrics && data.metrics.saved_to_db !== undefined) ? data.metrics.saved_to_db : (data.processed_count || 0);
            const goal = data.total_count || savedTotal || 10;
            progressFill.style.width = "100%";
            progressFill.style.background = "linear-gradient(90deg, #10b981, #06b6d4)";
            progressCountLabel.innerText = `${savedTotal} / ${goal} Qualified Leads`;
            progressJobLabel.innerHTML = `✅ <strong>Goal Reached!</strong> Discovered ${savedTotal} qualified leads. <a href="#" onclick="document.querySelector('[data-tab=tab-leads]').click(); return false;" style="color:#38bdf8; text-decoration:underline; font-weight:700; margin-left:8px;">View in Leads Explorer (${savedTotal} saved) &rarr;</a>`;
          } else {
            progressJobLabel.innerText = `Pipeline halted: ${data.error_message || 'Error occurred'}`;
          }

          fetchStats(); // Update counters
          fetchLeads(); // Refresh leads in database
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

  // --- Instant Lead & Company Research Lab ---
  instantForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const company = document.getElementById("lab-company") ? document.getElementById("lab-company").value.trim() : "";
    const title = document.getElementById("lab-title") ? document.getElementById("lab-title").value.trim() : "";
    const location = document.getElementById("lab-location") ? document.getElementById("lab-location").value.trim() : "Remote";
    const url = document.getElementById("lab-url") ? document.getElementById("lab-url").value.trim() : "";
    const size = document.getElementById("lab-size") ? document.getElementById("lab-size").value : "small";
    const jobType = document.getElementById("lab-type") ? document.getElementById("lab-type").value : "all";
    const desc = document.getElementById("lab-description") ? document.getElementById("lab-description").value.trim() : "";
    const saveToDb = document.getElementById("lab-save-db") ? document.getElementById("lab-save-db").checked : true;

    if (!company || !title) {
      showToast("Please provide both Company Name and Job Title.", "error");
      return;
    }

    labResultPlaceholder.style.display = "none";
    labLoading.style.display = "flex";
    labResultContent.style.display = "none";
    btnRunLab.disabled = true;
    btnRunLab.innerHTML = `<div class="spinner" style="width: 16px; height: 16px; border-width: 2px;"></div> Researching & Enriching...`;

    try {
      const res = await fetch(`${API_BASE}/api/pipeline/test-enrichment`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          company: company,
          title: title,
          location: location || "Remote",
          job_url: url || null,
          job_description: desc || null,
          target_company_size: size,
          target_job_type: jobType,
          save_to_db: saveToDb,
        }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Agent research on company failed");

      labLoading.style.display = "none";
      labResultContent.style.display = "block";

      const lead = data.lead;
      const contacts = lead.contacts || [];

      labResultContent.innerHTML = `
        <div style="background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.3); border-radius: var(--radius-md); padding: 1rem; margin-bottom: 1rem;">
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem; margin-bottom:0.4rem;">
            <h3 style="color: #34d399; font-size: 1rem; margin: 0;">✓ Enriched: ${escapeHtml(lead.company)} - ${escapeHtml(lead.title)}</h3>
            <span class="lead-score-pill score-high">${lead.relevance_score}/100 Score</span>
          </div>
          <p style="font-size: 0.85rem; color: #cbd5e1; margin-bottom: 0.5rem;">${escapeHtml(lead.lead_summary || lead.company_summary || '')}</p>
          <div style="display: flex; gap: 6px; flex-wrap: wrap;">
            <span class="lead-meta-pill"><i data-lucide="building-2"></i> ${escapeHtml(lead.company_size || 'Small')}</span>
            <span class="lead-meta-pill"><i data-lucide="briefcase"></i> ${escapeHtml(lead.job_type || 'Contract')}</span>
            ${lead.job_url ? `<a href="${escapeHtml(lead.job_url)}" target="_blank" class="lead-meta-pill platform-link-pill"><i data-lucide="external-link"></i> Link</a>` : ''}
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
                ${c.linkedin_url ? `<a href="${c.linkedin_url}" target="_blank" class="btn btn-secondary btn-sm" title="View Profile">LinkedIn Profile</a>` : ''}
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
      btnRunLab.innerHTML = `<i data-lucide="sparkles"></i><span>Run Autonomous Agent Research</span>`;
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

  // ============================================================
  // EU STARTUPS EXPLORER & INTELLIGENCE
  // ============================================================
  const euClock = document.getElementById("eu-clock");
  const euDate = document.getElementById("eu-date");
  const euStatTotal = document.getElementById("eu-stat-total");
  const euStatCountries = document.getElementById("eu-stat-countries");
  const euStatCategories = document.getElementById("eu-stat-categories");
  const euStatPeople = document.getElementById("eu-stat-people");
  const euStatEmails = document.getElementById("eu-stat-emails");
  const euStartupsBadge = document.getElementById("eu-startups-badge");

  const euSearch = document.getElementById("eu-search");
  const euCountry = document.getElementById("eu-country");
  const euState = document.getElementById("eu-state");
  const euCity = document.getElementById("eu-city");
  const euCategory = document.getElementById("eu-category");
  const euRole = document.getElementById("eu-role");
  const euFoundedMin = document.getElementById("eu-founded-min");
  const euFoundedMax = document.getElementById("eu-founded-max");
  const euHasWebsite = document.getElementById("eu-has-website");
  const euHasEmail = document.getElementById("eu-has-email");
  const euSort = document.getElementById("eu-sort");
  const euPerPage = document.getElementById("eu-per-page");
  const euBtnReset = document.getElementById("eu-btn-reset");
  const euBtnApply = document.getElementById("eu-btn-apply");

  const euTableBody = document.getElementById("eu-table-body");
  const euPagination = document.getElementById("eu-pagination");
  const euLoadingSpinner = document.getElementById("eu-loading-spinner");
  const euEmptyState = document.getElementById("eu-empty-state");
  const euResultInfo = document.getElementById("eu-result-info");

  let euCurrentPage = 1;
  let euOptionsLoaded = false;

  function updateEUClock() {
    if (!euClock || !euDate) return;
    const now = new Date();
    euClock.textContent = now.toLocaleTimeString();
    euDate.textContent = now.toLocaleDateString(undefined, {
      weekday: "short",
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  }
  setInterval(updateEUClock, 1000);
  updateEUClock();

  function formatEUScrapedDate(dateStr) {
    if (!dateStr) return "—";
    try {
      const d = new Date(dateStr.includes("T") ? dateStr : dateStr.replace(" ", "T"));
      if (isNaN(d.getTime())) return dateStr;
      return d.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
      }) + ", " + d.toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (e) {
      return dateStr;
    }
  }

  async function loadEUStats() {
    try {
      const res = await fetch(`${API_BASE}/api/eu-startups/stats`);
      if (!res.ok) throw new Error("Failed to fetch EU Startups stats");
      const d = await res.json();
      if (euStatTotal) euStatTotal.textContent = (d.total || 0).toLocaleString();
      if (euStatCountries) euStatCountries.textContent = (d.countries || 0).toLocaleString();
      if (euStatCategories) euStatCategories.textContent = (d.categories || 0).toLocaleString();
      if (euStatPeople) euStatPeople.textContent = (d.people || 0).toLocaleString();
      if (euStatEmails) euStatEmails.textContent = (d.emails || 0).toLocaleString();
      if (euStartupsBadge) euStartupsBadge.textContent = (d.total || 0).toLocaleString();
    } catch (err) {
      console.warn("Could not load EU Startups stats:", err);
    }
  }

  async function loadEUOptions() {
    if (euOptionsLoaded) return;
    try {
      const res = await fetch(`${API_BASE}/api/eu-startups/options`);
      if (!res.ok) throw new Error("Failed to fetch EU Startups filter options");
      const d = await res.json();

      fillEUSelect(euCountry, d.countries || [], "All Countries");
      fillEUSelect(euState, d.states || [], "All States");
      fillEUSelect(euCity, d.cities || [], "All Cities");
      fillEUSelect(euCategory, d.categories || [], "All Categories");
      fillEUSelect(euRole, d.roles || [], "All Roles");

      euOptionsLoaded = true;
    } catch (err) {
      console.warn("Could not load EU Startups options:", err);
    }
  }

  function fillEUSelect(selectEl, values, defaultLabel) {
    if (!selectEl) return;
    const currentVal = selectEl.value;
    let html = `<option value="">${escapeHtml(defaultLabel)}</option>`;
    values.forEach(v => {
      html += `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`;
    });
    selectEl.innerHTML = html;
    if (currentVal) selectEl.value = currentVal;
  }

  function getEUParams(page = 1) {
    const [sort, direction] = (euSort ? euSort.value : "updated_at|desc").split("|");
    const perPage = euPerPage ? parseInt(euPerPage.value, 10) || 25 : 25;
    return new URLSearchParams({
      page: page,
      per_page: perPage,
      search: (euSearch ? euSearch.value : "").trim(),
      country: euCountry ? euCountry.value : "",
      state: euState ? euState.value : "",
      city: euCity ? euCity.value : "",
      category: euCategory ? euCategory.value : "",
      role: euRole ? euRole.value : "",
      founded_min: euFoundedMin ? euFoundedMin.value.trim() : "",
      founded_max: euFoundedMax ? euFoundedMax.value.trim() : "",
      has_website: euHasWebsite ? euHasWebsite.value : "",
      has_email: euHasEmail ? euHasEmail.value : "",
      sort: sort || "updated_at",
      direction: direction || "desc",
    });
  }

  window.loadEUStartups = async function(page = 1) {
    euCurrentPage = page;
    if (euLoadingSpinner) euLoadingSpinner.style.display = "flex";
    if (euEmptyState) euEmptyState.style.display = "none";
    if (euTableBody) euTableBody.innerHTML = "";

    try {
      const res = await fetch(`${API_BASE}/api/eu-startups/startups?` + getEUParams(page));
      if (!res.ok) throw new Error("Failed to load startups");
      const d = await res.json();

      if (euResultInfo) {
        euResultInfo.textContent = `${(d.total || 0).toLocaleString()} result${d.total === 1 ? "" : "s"} • Page ${d.page} of ${Math.max(d.pages || 1, 1)}`;
      }

      if (!d.data || d.data.length === 0) {
        if (euEmptyState) euEmptyState.style.display = "block";
        if (euPagination) euPagination.innerHTML = "";
      } else {
        renderEURows(d.data);
        renderEUPagination(d.page, d.pages);
      }
    } catch (err) {
      if (euTableBody) {
        euTableBody.innerHTML = `<tr><td colspan="9" style="text-align:center; padding: 40px; color: #fb7185;">Error loading startups: ${escapeHtml(err.message)}</td></tr>`;
      }
    } finally {
      if (euLoadingSpinner) euLoadingSpinner.style.display = "none";
      if (window.lucide) lucide.createIcons();
    }
  };

  function renderEURows(rows) {
    if (!euTableBody) return;
    euTableBody.innerHTML = rows.map(s => {
      const peopleList = (s.people || []).map(p => {
        const isPersonal = p.linkedin && p.linkedin.includes("/in/");
        return `
        <div class="eu-person-block">
          <div class="eu-person-title">${escapeHtml(p.name || "Public Contact")}</div>
          ${p.role ? `<div class="eu-person-subrole">${escapeHtml(p.role)}</div>` : ""}
          ${p.email ? `<div class="eu-person-mail">✉ ${escapeHtml(p.email)}</div>` : ""}
          ${isPersonal ? `<a href="${escapeHtml(p.linkedin)}" target="_blank" rel="noopener noreferrer" class="eu-person-linkedin"><i data-lucide="linkedin" style="width:12px;height:12px;"></i> Founder LinkedIn</a>` : ""}
        </div>
      `;}).join("");

      const tagsList = s.tags ? s.tags.split(",").map(t => t.trim()).filter(Boolean).map(t => `
        <span class="eu-tag-pill">${escapeHtml(t)}</span>
      `).join(" ") : "—";

      return `
        <tr>
          <td>
            <div class="eu-company-name">
              ${s.website
                ? `<a href="${escapeHtml(s.website)}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.company_name || "Unnamed Startup")}</a>`
                : escapeHtml(s.company_name || "Unnamed Startup")}
            </div>
            <div class="eu-company-desc">
              ${escapeHtml((s.description || "").slice(0, 160))}${(s.description && s.description.length > 160) ? "..." : ""}
            </div>
          </td>
          <td>
            <div>${escapeHtml(s.city || "—")}</div>
            ${s.state ? `<div style="font-size:0.75rem; color:var(--text-muted);">${escapeHtml(s.state)}</div>` : ""}
            <span class="eu-location-badge">${escapeHtml(s.country || "Europe")}</span>
          </td>
          <td>
            ${s.category ? `<span class="eu-cat-badge">${escapeHtml(s.category)}</span>` : "—"}
          </td>
          <td>
            <div style="font-family:var(--font-mono); font-weight:700; color:#38bdf8; font-size:0.86rem; display:flex; align-items:center; gap:4px;">
              <i data-lucide="calendar" style="width:12px;height:12px;color:var(--accent-cyan);"></i>
              <span>${escapeHtml(s.founded_year ? String(s.founded_year) : "—")}</span>
            </div>
          </td>
          <td>
            ${s.created_at ? `
            <div style="font-size:0.78rem; color:var(--text-muted); display:flex; align-items:center; gap:3px; white-space:nowrap;" title="Scraped &amp; Added at ${escapeHtml(s.created_at)}">
              <i data-lucide="clock" style="width:10px;height:10px;color:var(--text-dim);"></i>
              <span>${formatEUScrapedDate(s.created_at)}</span>
            </div>` : `<span style="color:var(--text-dim);">—</span>`}
          </td>
          <td class="eu-tag-text">
            ${tagsList}
          </td>
          <td style="min-width: 170px;">
            ${peopleList || `<span style="color:var(--text-muted); font-size:0.78rem;">No contacts found</span>`}
          </td>
          <td>
            <div class="eu-count-badge">${s.email_count || 0} emails</div>
            <div style="font-size:0.75rem; color:var(--text-muted);">${s.people_count || 0} people</div>
          </td>
          <td>
            ${s.eu_startups_url ? `<a href="${escapeHtml(s.eu_startups_url)}" target="_blank" rel="noopener noreferrer" class="eu-link-action"><i data-lucide="external-link" style="width:12px;height:12px;"></i> EU-Startups</a><br>` : ""}
            ${s.website ? `<a href="${escapeHtml(s.website)}" target="_blank" rel="noopener noreferrer" class="eu-link-action"><i data-lucide="globe" style="width:12px;height:12px;"></i> Website</a><br>` : ""}
            ${s.company_linkedin ? `<a href="${escapeHtml(s.company_linkedin)}" target="_blank" rel="noopener noreferrer" class="eu-link-action"><i data-lucide="linkedin" style="width:12px;height:12px;"></i> Company LinkedIn</a>` : ""}
          </td>
        </tr>
      `;
    }).join("");
  }

  function renderEUPagination(page, pages) {
    if (!euPagination) return;
    if (pages <= 1) {
      euPagination.innerHTML = "";
      return;
    }

    let html = "";
    const start = Math.max(1, page - 2);
    const end = Math.min(pages, page + 2);

    if (page > 1) {
      html += `<button class="eu-page-btn" onclick="window.loadEUStartups(${page - 1})" title="Previous Page">‹</button>`;
    }
    if (start > 1) {
      html += `<button class="eu-page-btn" onclick="window.loadEUStartups(1)">1</button>`;
    }
    if (start > 2) {
      html += `<span class="eu-page-ellipsis">…</span>`;
    }

    for (let i = start; i <= end; i++) {
      html += `<button class="eu-page-btn ${i === page ? "active" : ""}" onclick="window.loadEUStartups(${i})">${i}</button>`;
    }

    if (end < pages - 1) {
      html += `<span class="eu-page-ellipsis">…</span>`;
    }
    if (end < pages) {
      html += `<button class="eu-page-btn" onclick="window.loadEUStartups(${pages})">${pages}</button>`;
    }
    if (page < pages) {
      html += `<button class="eu-page-btn" onclick="window.loadEUStartups(${page + 1})" title="Next Page">›</button>`;
    }

    euPagination.innerHTML = html;
  }

  function resetEUFilters() {
    if (euSearch) euSearch.value = "";
    if (euFoundedMin) euFoundedMin.value = "";
    if (euFoundedMax) euFoundedMax.value = "";
    if (euCountry) euCountry.value = "";
    if (euState) euState.value = "";
    if (euCity) euCity.value = "";
    if (euCategory) euCategory.value = "";
    if (euRole) euRole.value = "";
    if (euHasWebsite) euHasWebsite.value = "";
    if (euHasEmail) euHasEmail.value = "";
    if (euSort) euSort.value = "updated_at|desc";
    window.loadEUStartups(1);
  }

  const euBtnSearchInline = document.getElementById("eu-btn-search-inline");
  const euBtnDiscoverOpen = document.getElementById("eu-btn-discover-open");
  const euDiscoverModal = document.getElementById("eu-discover-modal");
  const btnCloseDiscoverModal = document.getElementById("btn-close-discover-modal");
  const btnCancelDiscover = document.getElementById("btn-cancel-discover");
  const btnStartDiscover = document.getElementById("btn-start-discover");
  const discoverTopic = document.getElementById("discover-topic");
  const discoverCountry = document.getElementById("discover-country");
  const discoverLimit = document.getElementById("discover-limit");
  const discoverProgressBox = document.getElementById("discover-progress-box");
  const discoverStatusText = document.getElementById("discover-status-text");

  if (euBtnApply) euBtnApply.addEventListener("click", () => window.loadEUStartups(1));
  if (euBtnReset) euBtnReset.addEventListener("click", resetEUFilters);
  if (euBtnSearchInline) {
    euBtnSearchInline.addEventListener("click", () => window.loadEUStartups(1));
  }

  let searchDebounceTimer = null;
  if (euSearch) {
    euSearch.addEventListener("input", () => {
      clearTimeout(searchDebounceTimer);
      searchDebounceTimer = setTimeout(() => {
        window.loadEUStartups(1);
      }, 300);
    });
    euSearch.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        clearTimeout(searchDebounceTimer);
        window.loadEUStartups(1);
      }
    });
  }

  if (euSort) euSort.addEventListener("change", () => window.loadEUStartups(1));
  if (euPerPage) euPerPage.addEventListener("change", () => window.loadEUStartups(1));
  if (euCountry) euCountry.addEventListener("change", () => window.loadEUStartups(1));
  if (euState) euState.addEventListener("change", () => window.loadEUStartups(1));
  if (euCity) euCity.addEventListener("change", () => window.loadEUStartups(1));
  if (euCategory) euCategory.addEventListener("change", () => window.loadEUStartups(1));
  if (euRole) euRole.addEventListener("change", () => window.loadEUStartups(1));
  if (euHasWebsite) euHasWebsite.addEventListener("change", () => window.loadEUStartups(1));
  if (euHasEmail) euHasEmail.addEventListener("change", () => window.loadEUStartups(1));

  // Discover Modal Handling
  window.openDiscoverModal = function() {
    if (!euDiscoverModal) return;
    euDiscoverModal.style.display = "flex";
    euDiscoverModal.classList.add("show");
    if (discoverProgressBox) discoverProgressBox.style.display = "none";
    if (btnStartDiscover) {
      btnStartDiscover.disabled = false;
      btnStartDiscover.innerHTML = `<i data-lucide="sparkles"></i> <span>Start Lead Discovery & Enrichment</span>`;
      if (window.lucide) lucide.createIcons();
    }
  };

  window.closeDiscoverModal = function() {
    if (!euDiscoverModal) return;
    euDiscoverModal.style.display = "none";
    euDiscoverModal.classList.remove("show");
  };

  if (euBtnDiscoverOpen) {
    euBtnDiscoverOpen.addEventListener("click", window.openDiscoverModal);
  }

  if (btnCloseDiscoverModal) btnCloseDiscoverModal.addEventListener("click", window.closeDiscoverModal);
  if (btnCancelDiscover) btnCancelDiscover.addEventListener("click", window.closeDiscoverModal);
  window.addEventListener("click", (e) => {
    if (e.target === euDiscoverModal) {
      window.closeDiscoverModal();
    }
  });

  // Multi-Select Topic Chips
  function updateDiscoverTopicFromChips() {
    const activeChips = Array.from(document.querySelectorAll(".topic-chip.active"));
    if (discoverTopic) {
      if (activeChips.length > 0) {
        const topics = activeChips.map(c => c.getAttribute("data-topic") || c.textContent.trim());
        discoverTopic.value = topics.join(", ");
      }
    }
  }

  document.querySelectorAll(".topic-chip").forEach(chip => {
    chip.addEventListener("click", () => {
      chip.classList.toggle("active");
      updateDiscoverTopicFromChips();
    });
  });

  if (btnStartDiscover) {
    btnStartDiscover.addEventListener("click", async () => {
      const topic = (discoverTopic ? discoverTopic.value : "").trim();
      const country = discoverCountry ? discoverCountry.value : "";
      const limit = discoverLimit ? parseInt(discoverLimit.value, 10) || 5 : 5;

      if (discoverProgressBox) discoverProgressBox.style.display = "block";
      if (discoverStatusText) discoverStatusText.textContent = `Searching web for European '${topic || "Tech"}' startups & auto-enriching founders...`;
      
      btnStartDiscover.disabled = true;
      btnStartDiscover.innerHTML = `<div class="spinner" style="width:16px;height:16px;border-width:2px;margin-right:6px;"></div> <span>Discovering Leads...</span>`;

      try {
        const params = new URLSearchParams({ topic, country, limit });
        const res = await fetch(`${API_BASE}/api/eu-startups/discover?${params.toString()}`, {
          method: "POST",
        });

        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.detail || "Failed to discover startups");
        }

        const data = await res.json();
        showToast("success", `Discovered and auto-enriched ${data.discovered_count || 0} qualified European leads!`);
        
        // Refresh stats & table
        await loadEUStats();
        await window.loadEUStartups(1);
        closeDiscoverModal();
      } catch (err) {
        showToast("error", `Discovery error: ${err.message}`);
      } finally {
        if (discoverProgressBox) discoverProgressBox.style.display = "none";
        btnStartDiscover.disabled = false;
        btnStartDiscover.innerHTML = `<i data-lucide="sparkles"></i> <span>Start Lead Discovery & Enrichment</span>`;
        if (window.lucide) lucide.createIcons();
      }
    });
  }

  // --- Initial Load ---
  fetchStats();
  fetchLeads();
  loadEUStats();
  pollPipelineStatus(); // Resume polling if a task was already in progress

  // Check if directed directly to EU Startups
  if (
    window.location.pathname.includes("eu-startups") ||
    window.location.pathname.includes("startups") ||
    window.location.hash === "#eu-startups"
  ) {
    const euTabBtn = document.querySelector('[data-tab="tab-eu-startups"]');
    if (euTabBtn) euTabBtn.click();
  }

  // ============================================================
  // EMAIL CAMPAIGNS MODULE
  // ============================================================

  const EMAIL_API = `${API_BASE}/api/email`;
  let emailTemplates = [];
  let activeCampaignId = null;
  let campaignPollInterval = null;

  // --- Inner pill navigation ---
  document.querySelectorAll(".email-pill[data-epanel]").forEach(pill => {
    pill.addEventListener("click", () => {
      const target = pill.getAttribute("data-epanel");
      if (target === "ep-smtp") { openSmtpModal(); return; }
      document.querySelectorAll(".email-pill[data-epanel]").forEach(p => p.classList.remove("active"));
      document.querySelectorAll(".email-panel").forEach(p => p.classList.remove("active"));
      pill.classList.add("active");
      const panel = document.getElementById(target);
      if (panel) panel.classList.add("active");
      if (target === "ep-history") loadCampaignHistory();
    });
  });

  // Load email tab on switch
  tabButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      if (btn.getAttribute("data-tab") === "tab-email") {
        loadVariableChips();
        loadTemplates();
        loadTemplatesIntoSelect();
      }
    });
  });

  // --- Variable Chips ---
  async function loadVariableChips() {
    const container = document.getElementById("var-chips-container");
    if (!container || container.children.length > 0) return;
    try {
      const res = await fetch(`${EMAIL_API}/templates/variables`);
      const d = await res.json();
      container.innerHTML = (d.data || []).map(v => `
        <button type="button" class="var-chip" title="${escapeHtml(v.description)}"
          onclick="insertVariable('${v.variable.replace(/'/g,"\\'")}')">
          ${escapeHtml(v.variable)}
        </button>
      `).join("");
      if (window.lucide) lucide.createIcons();
    } catch (e) { console.warn("Failed to load variable chips", e); }
  }

  window.insertVariable = function(variable) {
    const bodyEl = document.getElementById("tpl-body");
    const subjectEl = document.getElementById("tpl-subject");
    // Insert into whichever was last focused, default body
    const target = document.activeElement === subjectEl ? subjectEl : bodyEl;
    const start = target.selectionStart;
    const end = target.selectionEnd;
    const val = target.value;
    target.value = val.slice(0, start) + variable + val.slice(end);
    target.selectionStart = target.selectionEnd = start + variable.length;
    target.focus();
  };

  // --- Templates CRUD ---
  async function loadTemplates() {
    try {
      const res = await fetch(`${EMAIL_API}/templates`);
      const d = await res.json();
      emailTemplates = d.data || [];
      renderTemplatesGrid(emailTemplates);
      updateCampaignsBadge(emailTemplates.length);
    } catch (e) { console.warn("Failed to load templates", e); }
  }

  async function loadTemplatesIntoSelect() {
    try {
      const res = await fetch(`${EMAIL_API}/templates`);
      const d = await res.json();
      const sel = document.getElementById("camp-template");
      if (!sel) return;
      const current = sel.value;
      sel.innerHTML = `<option value="">— Select a template —</option>` +
        (d.data || []).map(t =>
          `<option value="${t.id}" ${t.id === current ? "selected" : ""}>${escapeHtml(t.name)}</option>`
        ).join("");
    } catch (e) {}
  }

  function renderTemplatesGrid(templates) {
    const grid = document.getElementById("templates-grid");
    const empty = document.getElementById("templates-empty");
    if (!grid) return;
    if (!templates || templates.length === 0) {
      grid.innerHTML = "";
      if (empty) empty.style.display = "flex";
      return;
    }
    if (empty) empty.style.display = "none";
    grid.innerHTML = templates.map(t => `
      <div class="template-card">
        <div class="template-card-name">${escapeHtml(t.name)}</div>
        <div class="template-card-subject">✉ ${escapeHtml(t.subject)}</div>
        <div class="template-card-meta">Updated ${formatDateShort(t.updated_at)}</div>
        ${t.tags ? `<div style="font-size:0.72rem;color:var(--accent-cyan);">🏷 ${escapeHtml(t.tags)}</div>` : ""}
        <div class="template-card-actions">
          <button class="btn btn-sm btn-secondary" onclick="editTemplate('${t.id}')">
            <i data-lucide="edit-3" style="width:12px;height:12px;"></i> Edit
          </button>
          <button class="btn btn-sm btn-secondary" onclick="previewTemplate('${t.id}')" style="color:#38bdf8;">
            <i data-lucide="eye" style="width:12px;height:12px;"></i> Preview
          </button>
          <button class="btn btn-sm" onclick="deleteTemplate('${t.id}', '${escapeHtml(t.name).replace(/'/g,"\\'")}', this)"
            style="color:#fb7185;background:rgba(244,63,94,0.1);border:1px solid rgba(244,63,94,0.25);">
            <i data-lucide="trash-2" style="width:12px;height:12px;"></i>
          </button>
        </div>
      </div>
    `).join("");
    if (window.lucide) lucide.createIcons();
  }

  function updateCampaignsBadge(count) {
    const badge = document.getElementById("email-campaigns-badge");
    if (badge) badge.textContent = count;
  }

  function formatDateShort(dt) {
    if (!dt) return "—";
    try {
      const d = new Date(dt.includes("T") ? dt : dt.replace(" ", "T"));
      return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    } catch(e) { return dt; }
  }

  // Save template (create or update)
  const btnTplSave = document.getElementById("btn-tpl-save");
  if (btnTplSave) {
    btnTplSave.addEventListener("click", async () => {
      const name = document.getElementById("tpl-name").value.trim();
      const subject = document.getElementById("tpl-subject").value.trim();
      const body = document.getElementById("tpl-body").value.trim();
      const editingId = document.getElementById("tpl-editing-id").value;
      if (!name || !subject || !body) {
        showToast("Please fill in Name, Subject, and Body.", "error"); return;
      }
      try {
        const url = editingId ? `${EMAIL_API}/templates/${editingId}` : `${EMAIL_API}/templates`;
        const method = editingId ? "PUT" : "POST";
        const res = await fetch(url, {
          method, headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, subject, body }),
        });
        const d = await res.json();
        if (!res.ok) throw new Error(d.detail || "Save failed");
        showToast(editingId ? "Template updated!" : "Template saved!", "success");
        clearTemplateEditor();
        loadTemplates();
        loadTemplatesIntoSelect();
      } catch (e) { showToast(e.message, "error"); }
    });
  }

  const btnTplClear = document.getElementById("btn-tpl-clear");
  if (btnTplClear) btnTplClear.addEventListener("click", clearTemplateEditor);

  function clearTemplateEditor() {
    document.getElementById("tpl-name").value = "";
    document.getElementById("tpl-subject").value = "";
    document.getElementById("tpl-body").value = "";
    document.getElementById("tpl-editing-id").value = "";
    document.getElementById("tpl-editor-title").textContent = "New Template";
  }

  window.editTemplate = function(id) {
    const t = emailTemplates.find(x => x.id === id);
    if (!t) return;
    document.getElementById("tpl-name").value = t.name;
    document.getElementById("tpl-subject").value = t.subject;
    document.getElementById("tpl-body").value = t.body;
    document.getElementById("tpl-editing-id").value = t.id;
    document.getElementById("tpl-editor-title").textContent = "Edit Template";
    document.getElementById("tpl-name").scrollIntoView({ behavior: "smooth", block: "center" });
  };

  window.deleteTemplate = async function(id, name, btn) {
    if (!confirm(`Delete template "${name}"?`)) return;
    try {
      const res = await fetch(`${EMAIL_API}/templates/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Delete failed");
      showToast("Template deleted.", "success");
      loadTemplates();
      loadTemplatesIntoSelect();
    } catch (e) { showToast(e.message, "error"); }
  };

  window.previewTemplate = async function(id) {
    try {
      const res = await fetch(`${EMAIL_API}/templates/${id}/preview`, { method: "POST" });
      const d = await res.json();
      document.getElementById("preview-subject").textContent = d.data.rendered_subject;
      document.getElementById("preview-body").innerHTML = d.data.rendered_body;
      // Switch to preview panel if on editor
      document.querySelector(".email-panel.active").scrollIntoView({ behavior:"smooth" });
    } catch (e) { showToast("Preview failed: " + e.message, "error"); }
  };

  const btnTplPreview = document.getElementById("btn-tpl-preview");
  if (btnTplPreview) {
    btnTplPreview.addEventListener("click", async () => {
      const subject = document.getElementById("tpl-subject").value;
      const body = document.getElementById("tpl-body").value;
      if (!subject && !body) { showToast("Write something first!", "error"); return; }
      try {
        const res = await fetch(`${EMAIL_API}/templates/preview-raw`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ subject, body }),
        });
        const d = await res.json();
        document.getElementById("preview-subject").textContent = d.data.rendered_subject || subject;
        document.getElementById("preview-body").innerHTML = d.data.rendered_body || body;
      } catch (e) { showToast("Preview error: " + e.message, "error"); }
    });
  }

  const btnRefreshTemplates = document.getElementById("btn-refresh-templates");
  if (btnRefreshTemplates) btnRefreshTemplates.addEventListener("click", loadTemplates);

  // --- SMTP Modal ---
  const smtpModal = document.getElementById("smtp-modal");
  const btnOpenSmtp = document.getElementById("btn-open-smtp");
  const btnCloseSmtpModal = document.getElementById("btn-close-smtp-modal");

  function openSmtpModal() {
    smtpModal.style.display = "flex";
    loadSmtpConfig();
    if (window.lucide) lucide.createIcons();
  }

  async function loadSmtpConfig() {
    try {
      const res = await fetch(`${EMAIL_API}/smtp/config`);
      const d = await res.json();
      const cfg = d.data || {};
      document.getElementById("smtp-host").value = cfg.smtp_host || "";
      document.getElementById("smtp-port").value = cfg.smtp_port || 587;
      document.getElementById("smtp-user").value = cfg.smtp_user || "";
      document.getElementById("smtp-pass").value = "";
      document.getElementById("smtp-from-name").value = cfg.from_name || "LeadPulse AI";
      document.getElementById("smtp-use-ssl").checked = !!cfg.use_ssl;
      document.getElementById("smtp-use-tls").checked = !!cfg.use_tls;
    } catch(e) {}
  }

  if (btnOpenSmtp) btnOpenSmtp.addEventListener("click", openSmtpModal);
  if (btnCloseSmtpModal) btnCloseSmtpModal.addEventListener("click", () => smtpModal.style.display = "none");
  window.addEventListener("click", e => { if (e.target === smtpModal) smtpModal.style.display = "none"; });

  const btnSmtpSave = document.getElementById("btn-smtp-save");
  if (btnSmtpSave) {
    btnSmtpSave.addEventListener("click", async () => {
      const cfg = getSmtpFormValues();
      try {
        const res = await fetch(`${EMAIL_API}/smtp/config`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(cfg),
        });
        const d = await res.json();
        showToast(d.message || "SMTP saved!", "success");
        smtpModal.style.display = "none";
      } catch(e) { showToast("Save failed: " + e.message, "error"); }
    });
  }

  const btnSmtpTest = document.getElementById("btn-smtp-test");
  if (btnSmtpTest) {
    btnSmtpTest.addEventListener("click", async () => {
      const resultEl = document.getElementById("smtp-test-result");
      resultEl.textContent = "Testing connection...";
      resultEl.className = "";
      const cfg = getSmtpFormValues();
      try {
        const res = await fetch(`${EMAIL_API}/smtp/test`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(cfg),
        });
        const d = await res.json();
        resultEl.textContent = d.message;
        resultEl.className = d.connected ? "smtp-result-ok" : "smtp-result-err";
      } catch(e) {
        resultEl.textContent = "Error: " + e.message;
        resultEl.className = "smtp-result-err";
      }
    });
  }

  function getSmtpFormValues() {
    return {
      smtp_host: document.getElementById("smtp-host").value.trim(),
      smtp_port: parseInt(document.getElementById("smtp-port").value) || 587,
      smtp_user: document.getElementById("smtp-user").value.trim(),
      smtp_pass: document.getElementById("smtp-pass").value,
      from_name: document.getElementById("smtp-from-name").value.trim() || "LeadPulse AI",
      use_ssl: document.getElementById("smtp-use-ssl").checked,
      use_tls: document.getElementById("smtp-use-tls").checked,
    };
  }

  // --- Manual emails toggle ---
  document.querySelectorAll("input[name='camp-source']").forEach(cb => {
    cb.addEventListener("change", () => {
      const manualGroup = document.getElementById("manual-emails-group");
      const manualCb = document.querySelector("input[name='camp-source'][value='manual']");
      if (manualGroup && manualCb) {
        manualGroup.style.display = manualCb.checked ? "block" : "none";
      }
    });
  });

  // --- Recipient Estimate ---
  const btnEstimate = document.getElementById("btn-estimate");
  if (btnEstimate) {
    btnEstimate.addEventListener("click", async () => {
      const sources = Array.from(document.querySelectorAll("input[name='camp-source']:checked")).map(x => x.value);
      const country = document.getElementById("camp-country").value.trim();
      const category = document.getElementById("camp-category").value.trim();
      const manualEmails = document.getElementById("camp-manual-emails").value;
      const params = new URLSearchParams({
        sources: sources.join(","),
        country, category, manual_emails: manualEmails,
      });
      try {
        const res = await fetch(`${EMAIL_API}/campaigns/estimate?${params}`);
        const d = await res.json();
        const count = d.data?.estimated_recipients || 0;
        document.getElementById("recipient-estimate-text").textContent = `~${count} recipient${count !== 1 ? "s" : ""} will receive this campaign`;
      } catch(e) { showToast("Estimate failed: " + e.message, "error"); }
    });
  }

  // --- Test Send ---
  const btnSendTest = document.getElementById("btn-send-test");
  if (btnSendTest) {
    btnSendTest.addEventListener("click", async () => {
      const toEmail = document.getElementById("camp-test-email").value.trim();
      const templateId = document.getElementById("camp-template").value;
      if (!toEmail) { showToast("Enter a test email address.", "error"); return; }
      if (!templateId) { showToast("Select a template first.", "error"); return; }
      btnSendTest.disabled = true;
      btnSendTest.textContent = "Sending...";
      try {
        const res = await fetch(`${EMAIL_API}/send-test`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ to_email: toEmail, template_id: templateId }),
        });
        const d = await res.json();
        if (d.status === "success") showToast(d.message, "success");
        else showToast(d.message, "error");
      } catch(e) { showToast("Test send failed: " + e.message, "error"); }
      finally {
        btnSendTest.disabled = false;
        btnSendTest.innerHTML = `<i data-lucide="send"></i> Test Send`;
        if (window.lucide) lucide.createIcons();
      }
    });
  }

  // --- Launch Campaign ---
  const btnLaunchCampaign = document.getElementById("btn-launch-campaign");
  if (btnLaunchCampaign) {
    btnLaunchCampaign.addEventListener("click", async () => {
      const name = document.getElementById("camp-name").value.trim();
      const templateId = document.getElementById("camp-template").value;
      const sources = Array.from(document.querySelectorAll("input[name='camp-source']:checked")).map(x => x.value);
      const country = document.getElementById("camp-country").value.trim();
      const category = document.getElementById("camp-category").value.trim();
      const manualEmailsRaw = document.getElementById("camp-manual-emails").value;
      const manualEmails = manualEmailsRaw ? manualEmailsRaw.split(/[\n,]/).map(e => e.trim()).filter(e => e.includes("@")) : [];
      const delay = parseFloat(document.getElementById("camp-delay").value) || 0.8;

      if (!name) { showToast("Please enter a campaign name.", "error"); return; }
      if (!templateId) { showToast("Please select an email template.", "error"); return; }
      if (sources.length === 0) { showToast("Select at least one audience source.", "error"); return; }

      btnLaunchCampaign.disabled = true;
      btnLaunchCampaign.innerHTML = `<div class="spinner" style="width:16px;height:16px;border-width:2px;"></div> Launching...`;

      try {
        const res = await fetch(`${EMAIL_API}/campaigns`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name, template_id: templateId,
            audience_sources: sources,
            audience_filters: { country, category },
            manual_emails: manualEmails,
            delay_seconds: delay,
          }),
        });
        const d = await res.json();
        if (!res.ok) throw new Error(d.detail || "Launch failed");
        showToast(d.message, "success");
        activeCampaignId = d.data.campaign_id;
        startCampaignPolling(activeCampaignId, d.data.total_recipients);
      } catch(e) {
        showToast(e.message, "error");
      } finally {
        btnLaunchCampaign.disabled = false;
        btnLaunchCampaign.innerHTML = `<i data-lucide="rocket"></i><span>Launch Bulk Campaign</span>`;
        if (window.lucide) lucide.createIcons();
      }
    });
  }

  function startCampaignPolling(campaignId, total) {
    const progressWrap = document.getElementById("camp-progress-wrap");
    const idlePlaceholder = document.getElementById("camp-idle-placeholder");
    const liveText = document.getElementById("camp-live-text");
    const pulseDot = document.querySelector("#camp-live-indicator .pulse-dot");

    if (progressWrap) progressWrap.style.display = "block";
    if (idlePlaceholder) idlePlaceholder.style.display = "none";
    if (liveText) liveText.textContent = "Running";
    if (pulseDot) pulseDot.classList.add("running");
    document.getElementById("camp-stat-total").textContent = total;

    if (campaignPollInterval) clearInterval(campaignPollInterval);
    campaignPollInterval = setInterval(() => pollCampaign(campaignId, total), 2000);
    pollCampaign(campaignId, total);
  }

  async function pollCampaign(campaignId, total) {
    try {
      const res = await fetch(`${EMAIL_API}/campaigns/${campaignId}`);
      const d = await res.json();
      const c = d.data;
      const sent = c.sent || 0;
      const failed = c.failed_count || 0;
      const t = c.total || total || 1;
      const pct = Math.min(100, Math.round((sent + failed) / t * 100));

      document.getElementById("camp-progress-fill").style.width = `${pct}%`;
      document.getElementById("camp-progress-count").textContent = `${sent + failed} / ${t}`;
      document.getElementById("camp-progress-label").textContent = c.status === "completed"
        ? `✅ Campaign complete! ${sent} sent, ${failed} failed.`
        : `Sending emails... ${sent} sent, ${failed} failed`;
      document.getElementById("camp-stat-sent").textContent = sent;
      document.getElementById("camp-stat-failed").textContent = failed;
      document.getElementById("camp-stat-total").textContent = t;

      if (c.status === "completed" || c.status === "failed") {
        clearInterval(campaignPollInterval);
        campaignPollInterval = null;
        const liveText = document.getElementById("camp-live-text");
        const pulseDot = document.querySelector("#camp-live-indicator .pulse-dot");
        if (liveText) liveText.textContent = c.status === "completed" ? "Done" : "Failed";
        if (pulseDot) pulseDot.classList.remove("running");
        if (c.status === "completed") {
          document.getElementById("camp-progress-fill").style.background = "linear-gradient(90deg,#10b981,#06b6d4)";
        }
      }
    } catch(e) {}
  }

  // --- Campaign History ---
  async function loadCampaignHistory() {
    const tbody = document.getElementById("campaigns-table-body");
    const emptyRow = document.getElementById("campaigns-empty-row");
    if (!tbody) return;
    try {
      const res = await fetch(`${EMAIL_API}/campaigns`);
      const d = await res.json();
      const campaigns = d.data || [];
      if (campaigns.length === 0) {
        if (emptyRow) emptyRow.style.display = "";
        return;
      }
      if (emptyRow) emptyRow.style.display = "none";
      tbody.innerHTML = campaigns.map(c => {
        const statusCls = c.status || "pending";
        const statusLabel = {
          pending: "Pending", queued: "Queued", running: "Running",
          completed: "Done", failed: "Failed"
        }[statusCls] || c.status;
        return `
          <tr>
            <td><strong style="color:#fff;">${escapeHtml(c.name)}</strong></td>
            <td style="color:var(--text-muted);">${escapeHtml(c.template_name || "—")}</td>
            <td><span class="camp-status-badge ${statusCls}">${statusLabel}</span></td>
            <td style="color:#10b981;font-weight:700;">${c.sent || 0}</td>
            <td style="color:#fb7185;font-weight:700;">${c.failed_count || 0}</td>
            <td style="color:var(--text-muted);">${c.total || 0}</td>
            <td style="font-size:0.78rem;color:var(--text-muted);">${formatDateShort(c.created_at)}</td>
            <td>
              <button class="btn btn-sm btn-secondary" onclick="viewCampaignLogs('${c.id}', '${escapeHtml(c.name).replace(/'/g,"\\'")}')">
                <i data-lucide="list" style="width:12px;height:12px;"></i> Logs
              </button>
            </td>
          </tr>
        `;
      }).join("");
      if (window.lucide) lucide.createIcons();
    } catch(e) { console.warn("Failed to load campaign history", e); }
  }

  const btnRefreshCampaigns = document.getElementById("btn-refresh-campaigns");
  if (btnRefreshCampaigns) btnRefreshCampaigns.addEventListener("click", loadCampaignHistory);

  // --- Campaign Logs Modal ---
  const campLogsModal = document.getElementById("camp-logs-modal");
  const btnCloseLogsModal = document.getElementById("btn-close-logs-modal");
  if (btnCloseLogsModal) btnCloseLogsModal.addEventListener("click", () => campLogsModal.style.display = "none");
  window.addEventListener("click", e => { if (e.target === campLogsModal) campLogsModal.style.display = "none"; });

  window.viewCampaignLogs = async function(campaignId, campaignName) {
    document.getElementById("logs-modal-title").textContent = `Logs: ${campaignName}`;
    document.getElementById("logs-modal-body").innerHTML = `<div class="loading-state"><div class="spinner"></div><span>Loading logs...</span></div>`;
    campLogsModal.style.display = "flex";
    try {
      const res = await fetch(`${EMAIL_API}/campaigns/${campaignId}/logs?per_page=200`);
      const d = await res.json();
      const logs = d.data || [];
      if (logs.length === 0) {
        document.getElementById("logs-modal-body").innerHTML = `<p style="color:var(--text-muted);text-align:center;padding:2rem;">No logs yet for this campaign.</p>`;
        return;
      }
      const sentCount = logs.filter(l => l.status === "sent").length;
      const failedCount = logs.filter(l => l.status === "failed").length;
      document.getElementById("logs-modal-body").innerHTML = `
        <div style="display:flex;gap:1rem;margin-bottom:1rem;flex-wrap:wrap;">
          <div class="camp-stat-pill success"><span>${sentCount}</span> Sent</div>
          <div class="camp-stat-pill error"><span>${failedCount}</span> Failed</div>
          <div class="camp-stat-pill info"><span>${logs.length}</span> Total</div>
        </div>
        <div style="overflow-x:auto;">
          <table class="logs-table">
            <thead>
              <tr>
                <th>Recipient</th>
                <th>Email</th>
                <th>Company</th>
                <th>Status</th>
                <th>Error</th>
                <th>Sent At</th>
              </tr>
            </thead>
            <tbody>
              ${logs.map(l => `
                <tr>
                  <td>${escapeHtml(l.recipient_name || "—")}</td>
                  <td style="font-family:var(--font-mono);font-size:0.75rem;">${escapeHtml(l.recipient_email || "—")}</td>
                  <td>${escapeHtml(l.company_name || "—")}</td>
                  <td><span class="log-status-${l.status}">${l.status === "sent" ? "✓ Sent" : "✗ Failed"}</span></td>
                  <td style="font-size:0.72rem;color:#fb7185;">${escapeHtml(l.error_message || "")}</td>
                  <td style="font-size:0.72rem;white-space:nowrap;">${formatDateShort(l.sent_at)}</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      `;
      if (window.lucide) lucide.createIcons();
    } catch(e) {
      document.getElementById("logs-modal-body").innerHTML = `<p style="color:#fb7185;">Error loading logs: ${escapeHtml(e.message)}</p>`;
    }
  };

});
