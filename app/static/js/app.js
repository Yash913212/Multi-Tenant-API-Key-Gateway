/**
 * Multi-Tenant API Key Gateway - Frontend Console JavaScript
 * Interacts with Gateway REST API, handles Chart.js, modals, and gateway simulator.
 */

const STATE = {
  selectedTenantId: 1,
  tenants: [],
  keys: [],
  auditPage: 1,
  auditLimit: 10,
  autoRefresh: true,
  refreshTimer: null,
  chartInstance: null,
  pendingActionKeyId: null,
};

// --- Initialization ---
document.addEventListener("DOMContentLoaded", async () => {
  initChart();
  await loadTenants();
  await refreshDashboard();
  setupEventListeners();

  // Set up periodic dashboard auto-refresh
  STATE.refreshTimer = setInterval(() => {
    if (STATE.autoRefresh) {
      refreshDashboard(true);
    }
  }, 4000);
});

// --- Event Listeners ---
function setupEventListeners() {
  // Tenant select
  document.getElementById("tenantSelect").addEventListener("change", async (e) => {
    STATE.selectedTenantId = parseInt(e.target.value, 10);
    STATE.auditPage = 1;
    await refreshDashboard();
  });

  // Create Key Modal Triggers
  document.getElementById("btnOpenCreateModal").addEventListener("click", () => {
    openModal("createKeyModal");
  });

  document.getElementById("btnSubmitCreateKey").addEventListener("click", handleCreateKey);

  // Rate limit preset chips
  document.querySelectorAll(".rate-preset-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      document.getElementById("createRateLimit").value = e.target.dataset.rate;
    });
  });

  // Action modals
  document.getElementById("btnConfirmRotate").addEventListener("click", handleRotateKey);
  document.getElementById("btnConfirmRevoke").addEventListener("click", handleRevokeKey);

  // Copy Key in reveal modal
  document.getElementById("btnCopyRevealedKey").addEventListener("click", () => {
    const text = document.getElementById("revealedKeyText").innerText;
    copyToClipboard(text, "API Key copied to clipboard!");
  });

  // Simulator
  document.getElementById("btnSimSingle").addEventListener("click", () => runSimulator(1));
  document.getElementById("btnSimBurst").addEventListener("click", () => runSimulator(6));

  // Audit Logs Pagination
  document.getElementById("btnAuditPrev").addEventListener("click", () => {
    if (STATE.auditPage > 1) {
      STATE.auditPage--;
      loadAuditLogs();
    }
  });

  document.getElementById("btnAuditNext").addEventListener("click", () => {
    STATE.auditPage++;
    loadAuditLogs();
  });

  // Auto-refresh toggle
  const refreshToggle = document.getElementById("autoRefreshToggle");
  if (refreshToggle) {
    refreshToggle.addEventListener("change", (e) => {
      STATE.autoRefresh = e.target.checked;
      showToast(STATE.autoRefresh ? "Live auto-refresh enabled" : "Auto-refresh paused", "warning");
    });
  }

  // Close modals on overlay click or close button
  document.querySelectorAll(".modal-overlay").forEach((overlay) => {
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay || e.target.closest(".btn-close") || e.target.closest(".btn-modal-cancel")) {
        closeModal(overlay.id);
      }
    });
  });
}

// --- API Calls ---

async function loadTenants() {
  try {
    const res = await fetch("/api/tenants");
    if (!res.ok) throw new Error("Failed to load tenants");
    const data = await res.json();
    STATE.tenants = data;

    const select = document.getElementById("tenantSelect");
    select.innerHTML = "";
    data.forEach((t) => {
      const opt = document.createElement("option");
      opt.value = t.id;
      opt.textContent = `${t.name} (ID: ${t.id})`;
      if (t.id === STATE.selectedTenantId) opt.selected = true;
      select.appendChild(opt);
    });
  } catch (err) {
    console.error(err);
    showToast("Error loading tenants", "error");
  }
}

async function refreshDashboard(silent = false) {
  await Promise.all([
    loadHealth(),
    loadKeys(),
    loadAuditLogs(),
    loadAnalytics(),
  ]);
  if (!silent) {
    updateSimulatorKeyDropdown();
  }
}

async function loadHealth() {
  try {
    const res = await fetch("/health");
    const data = await res.json();
    const badge = document.getElementById("systemHealthBadge");
    if (data.status === "healthy") {
      badge.innerHTML = `<span class="status-dot"></span> Gateway Active • PostgreSQL & Redis Connected`;
      badge.style.color = "var(--accent-emerald)";
    } else {
      badge.innerHTML = `<span class="status-dot" style="background:var(--accent-rose)"></span> Service Degraded`;
      badge.style.color = "var(--accent-rose)";
    }
  } catch (err) {
    const badge = document.getElementById("systemHealthBadge");
    badge.innerHTML = `<span class="status-dot" style="background:var(--accent-rose)"></span> Offline`;
    badge.style.color = "var(--accent-rose)";
  }
}

async function loadKeys() {
  try {
    const res = await fetch(`/api/tenants/${STATE.selectedTenantId}/keys`);
    if (!res.ok) throw new Error("Failed to fetch keys");
    const data = await res.json();
    STATE.keys = data;
    renderKeysTable(data);
    updateSimulatorKeyDropdown();
  } catch (err) {
    console.error(err);
  }
}

async function loadAuditLogs() {
  try {
    const res = await fetch(`/api/audit-logs?tenantId=${STATE.selectedTenantId}&page=${STATE.auditPage}&limit=${STATE.auditLimit}`);
    if (!res.ok) throw new Error("Failed to fetch audit logs");
    const data = await res.json();
    renderAuditTable(data);
  } catch (err) {
    console.error(err);
  }
}

async function loadAnalytics() {
  try {
    const res = await fetch(`/api/analytics/usage?tenantId=${STATE.selectedTenantId}`);
    if (!res.ok) throw new Error("Failed to fetch analytics");
    const data = await res.json();
    renderAnalytics(data);
  } catch (err) {
    console.error(err);
  }
}

// --- Actions: Create, Rotate, Revoke ---

async function handleCreateKey() {
  const rateLimit = parseInt(document.getElementById("createRateLimit").value, 10) || 100;
  const btn = document.getElementById("btnSubmitCreateKey");
  btn.disabled = true;
  btn.innerText = "Generating...";

  try {
    const res = await fetch(`/api/tenants/${STATE.selectedTenantId}/keys`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rateLimitPerMinute: rateLimit }),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Key creation failed");
    }

    const result = await res.json();
    closeModal("createKeyModal");

    // Reveal the key in reveal modal
    document.getElementById("revealedKeyText").innerText = result.apiKey;
    openModal("keyRevealModal");

    // Put into simulator key input for instant convenience
    document.getElementById("simKeyInput").value = result.apiKey;

    showToast("API Key created successfully!", "success");
    await refreshDashboard();
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerText = "Create Key";
  }
}

function promptRotateKey(keyId, maskedKey) {
  STATE.pendingActionKeyId = keyId;
  document.getElementById("rotateKeyPrompt").innerText = `Rotating key ${maskedKey}. A new key will be generated and this key will expire in 60 seconds.`;
  openModal("rotateConfirmModal");
}

async function handleRotateKey() {
  if (!STATE.pendingActionKeyId) return;
  const keyId = STATE.pendingActionKeyId;
  const btn = document.getElementById("btnConfirmRotate");
  btn.disabled = true;
  btn.innerText = "Rotating...";

  try {
    const res = await fetch(`/api/keys/${keyId}/rotate`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Rotation failed");
    }

    const data = await res.json();
    closeModal("rotateConfirmModal");

    // Reveal new key
    document.getElementById("revealedKeyText").innerText = data.newApiKey;
    openModal("keyRevealModal");

    // Put into simulator
    document.getElementById("simKeyInput").value = data.newApiKey;

    showToast("Key rotated! 60s grace period started.", "success");
    await refreshDashboard();
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerText = "Rotate Key";
    STATE.pendingActionKeyId = null;
  }
}

function promptRevokeKey(keyId, maskedKey) {
  STATE.pendingActionKeyId = keyId;
  document.getElementById("revokeKeyPrompt").innerText = `Are you sure you want to revoke key ${maskedKey}? This key will immediately stop working.`;
  openModal("revokeConfirmModal");
}

async function handleRevokeKey() {
  if (!STATE.pendingActionKeyId) return;
  const keyId = STATE.pendingActionKeyId;
  const btn = document.getElementById("btnConfirmRevoke");
  btn.disabled = true;
  btn.innerText = "Revoking...";

  try {
    const res = await fetch(`/api/keys/${keyId}`, { method: "DELETE" });
    if (!res.ok && res.status !== 204) {
      throw new Error("Revocation failed");
    }

    closeModal("revokeConfirmModal");
    showToast("Key revoked successfully.", "warning");
    await refreshDashboard();
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerText = "Revoke Key";
    STATE.pendingActionKeyId = null;
  }
}

// --- Simulator ---

async function runSimulator(requestCount) {
  const token = document.getElementById("simKeyInput").value.trim();
  const outputEl = document.getElementById("simOutput");

  if (!token) {
    showToast("Please select or enter an API key", "warning");
    return;
  }

  outputEl.textContent = `🚀 Firing ${requestCount} request(s) to GET /api/protected ...\n`;

  for (let i = 1; i <= requestCount; i++) {
    const start = performance.now();
    try {
      const res = await fetch("/api/protected", {
        headers: { Authorization: `Bearer ${token}` },
      });
      const duration = Math.round(performance.now() - start);
      const retryAfter = res.headers.get("Retry-After");
      const body = await res.json();

      let line = `[#${i}] Status: ${res.status} ${res.statusText} (${duration}ms)`;
      if (res.status === 200) {
        line += ` ✅ Quota: ${body.quotaUsed}/${body.rateLimitPerMinute}`;
      } else if (res.status === 429) {
        line += ` 🛑 RATE LIMITED! Retry-After: ${retryAfter || body.retryAfter}s`;
      } else if (res.status === 401) {
        line += ` ❌ 401 Unauthorized: ${body.detail || "Invalid Key"}`;
      }

      outputEl.textContent += line + "\n";
    } catch (err) {
      outputEl.textContent += `[#${i}] Network Error: ${err.message}\n`;
    }
  }

  // Refresh logs and metrics
  setTimeout(() => {
    loadAuditLogs();
    loadAnalytics();
  }, 300);
}

function updateSimulatorKeyDropdown() {
  const select = document.getElementById("simKeySelect");
  if (!select) return;

  const currentVal = select.value;
  select.innerHTML = '<option value="">-- Choose from active tenant keys --</option>';

  STATE.keys.forEach((k) => {
    if (k.isActive) {
      const opt = document.createElement("option");
      opt.value = k.maskedKey;
      opt.dataset.keyId = k.id;
      opt.textContent = `${k.maskedKey} (${k.rateLimitPerMinute} req/m)`;
      select.appendChild(opt);
    }
  });

  select.onchange = (e) => {
    // When selected from masked dropdown, inform user or prompt
    const chosen = STATE.keys.find((k) => k.maskedKey === e.target.value);
    if (chosen) {
      showToast("Note: Plaintext key is secret. Paste full key or issue a new key to test.", "warning");
    }
  };
}

// --- Renders ---

function renderKeysTable(keys) {
  const tbody = document.getElementById("keysTableBody");
  tbody.innerHTML = "";

  if (!keys || keys.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty-state">No API keys found for this tenant. Click "Issue New Key" above.</td></tr>`;
    return;
  }

  const now = new Date();

  keys.forEach((k) => {
    const tr = document.createElement("tr");

    // Status evaluation
    let statusBadge = "";
    let isRotating = false;
    if (!k.isActive) {
      statusBadge = `<span class="badge badge-revoked">Revoked</span>`;
    } else if (k.expiresAt) {
      const expDate = new Date(k.expiresAt);
      const remainingSec = Math.max(0, Math.round((expDate - now) / 1000));
      if (remainingSec > 0) {
        isRotating = true;
        statusBadge = `<span class="badge badge-rotating">Grace Period (${remainingSec}s)</span>`;
      } else {
        statusBadge = `<span class="badge badge-revoked">Expired</span>`;
      }
    } else {
      statusBadge = `<span class="badge badge-active">Active</span>`;
    }

    const createdFormatted = k.createdAt ? new Date(k.createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : "—";

    tr.innerHTML = `
      <td>
        <span class="key-badge">
          ${k.maskedKey}
          <button class="btn-copy-mini" title="Copy masked key" onclick="copyToClipboard('${k.maskedKey}', 'Masked key copied')">
            <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path></svg>
          </button>
        </span>
      </td>
      <td><strong>${k.rateLimitPerMinute}</strong> req/min</td>
      <td>${statusBadge}</td>
      <td style="color:var(--text-muted);font-size:0.8rem">${createdFormatted}</td>
      <td>
        <div class="table-actions">
          <button class="btn btn-outline-amber btn-sm" ${!k.isActive ? "disabled" : ""} onclick="promptRotateKey(${k.id}, '${k.maskedKey}')">
            Rotate
          </button>
          <button class="btn btn-outline-rose btn-sm" ${!k.isActive ? "disabled" : ""} onclick="promptRevokeKey(${k.id}, '${k.maskedKey}')">
            Revoke
          </button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function renderAuditTable(data) {
  const tbody = document.getElementById("auditTableBody");
  tbody.innerHTML = "";

  if (!data.items || data.items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty-state">No audit logs recorded yet. Test requests in the simulator above!</td></tr>`;
    document.getElementById("auditPageInfo").innerText = "Page 1 of 1";
    document.getElementById("btnAuditPrev").disabled = true;
    document.getElementById("btnAuditNext").disabled = true;
    return;
  }

  data.items.forEach((item) => {
    const tr = document.createElement("tr");

    let statusBadge = "";
    if (item.statusCode === 200) {
      statusBadge = `<span class="badge badge-status-200">200 OK</span>`;
    } else if (item.statusCode === 429) {
      statusBadge = `<span class="badge badge-status-429">429 Limited</span>`;
    } else {
      statusBadge = `<span class="badge badge-status-401">${item.statusCode}</span>`;
    }

    const timeFormatted = item.timestamp ? new Date(item.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : "—";

    tr.innerHTML = `
      <td style="color:var(--text-dim);font-family:var(--font-mono);font-size:0.75rem">#${item.id}</td>
      <td><span class="key-badge">${item.maskedKey}</span></td>
      <td><code style="color:#a5b4fc;font-size:0.82rem">${item.endpoint}</code></td>
      <td>${statusBadge}</td>
      <td style="color:var(--text-muted);font-size:0.8rem">${timeFormatted}</td>
    `;
    tbody.appendChild(tr);
  });

  document.getElementById("auditPageInfo").innerText = `Page ${data.page} of ${data.totalPages || 1} (${data.total} logs)`;
  document.getElementById("btnAuditPrev").disabled = data.page <= 1;
  document.getElementById("btnAuditNext").disabled = data.page >= data.totalPages;
}

function renderAnalytics(data) {
  // Metric cards
  document.getElementById("metricTotalReq").innerText = data.totalRequests || 0;
  document.getElementById("metricSuccessReq").innerText = data.successRequests || 0;
  document.getElementById("metricRateLimitedReq").innerText = data.rateLimitedRequests || 0;
  document.getElementById("metricActiveKeys").innerText = data.activeKeysCount || 0;

  // Chart update
  if (STATE.chartInstance && data.timeline) {
    const labels = data.timeline.map((t) => t.bucket);
    const successData = data.timeline.map((t) => t.success);
    const limitedData = data.timeline.map((t) => t.rateLimited);

    // If timeline is empty, show default placeholder buckets
    if (labels.length === 0) {
      const now = new Date();
      const currentMin = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      STATE.chartInstance.data.labels = [currentMin];
      STATE.chartInstance.data.datasets[0].data = [0];
      STATE.chartInstance.data.datasets[1].data = [0];
    } else {
      STATE.chartInstance.data.labels = labels;
      STATE.chartInstance.data.datasets[0].data = successData;
      STATE.chartInstance.data.datasets[1].data = limitedData;
    }
    STATE.chartInstance.update();
  }
}

// --- Chart.js Initialization ---

function initChart() {
  const ctx = document.getElementById("usageChart").getContext("2d");

  const gradientSuccess = ctx.createLinearGradient(0, 0, 0, 200);
  gradientSuccess.addColorStop(0, "rgba(16, 185, 129, 0.35)");
  gradientSuccess.addColorStop(1, "rgba(16, 185, 129, 0.0)");

  const gradientLimited = ctx.createLinearGradient(0, 0, 0, 200);
  gradientLimited.addColorStop(0, "rgba(244, 63, 94, 0.35)");
  gradientLimited.addColorStop(1, "rgba(244, 63, 94, 0.0)");

  STATE.chartInstance = new Chart(ctx, {
    type: "line",
    data: {
      labels: ["--:--"],
      datasets: [
        {
          label: "200 OK (Allowed)",
          data: [0],
          borderColor: "#10b981",
          backgroundColor: gradientSuccess,
          borderWidth: 2,
          tension: 0.35,
          fill: true,
          pointRadius: 3,
          pointHoverRadius: 5,
        },
        {
          label: "429 Rate Limited",
          data: [0],
          borderColor: "#f43f5e",
          backgroundColor: gradientLimited,
          borderWidth: 2,
          tension: 0.35,
          fill: true,
          pointRadius: 3,
          pointHoverRadius: 5,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          labels: {
            color: "#94a3b8",
            font: { family: "'Inter', sans-serif", size: 11 },
          },
        },
        tooltip: {
          backgroundColor: "#111827",
          titleColor: "#f8fafc",
          bodyColor: "#cbd5e1",
          borderColor: "rgba(255,255,255,0.1)",
          borderWidth: 1,
        },
      },
      scales: {
        x: {
          grid: { color: "rgba(255, 255, 255, 0.04)" },
          ticks: { color: "#64748b", font: { size: 10 } },
        },
        y: {
          beginAtZero: true,
          grid: { color: "rgba(255, 255, 255, 0.04)" },
          ticks: {
            color: "#64748b",
            font: { size: 10 },
            stepSize: 1,
          },
        },
      },
    },
  });
}

// --- Utilities & UI Helpers ---

function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add("active");
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove("active");
}

function showToast(message, type = "info") {
  const container = document.getElementById("toastContainer");
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.innerText = message;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    setTimeout(() => toast.remove(), 250);
  }, 3500);
}

function copyToClipboard(text, successMsg = "Copied!") {
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(() => {
      showToast(successMsg, "success");
    });
  } else {
    const input = document.createElement("input");
    input.value = text;
    document.body.appendChild(input);
    input.select();
    document.execCommand("copy");
    document.body.removeChild(input);
    showToast(successMsg, "success");
  }
}
