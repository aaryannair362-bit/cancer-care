// Shared vanilla-JS utilities between frontend/ipd.html and frontend/headnurse.html.
// Plain <script src="/js/ipd-shared.js"> include (no build step, no ES modules) -- this file
// must load BEFORE either page's own inline <script> block, since both rely on the globals
// (accessToken/refreshToken) and functions (apiRequest/closeModal/taskTypeBadge) defined here.
const API_BASE = '/api';
let accessToken = localStorage.getItem('access_token');
let refreshToken = localStorage.getItem('refresh_token') || null;

async function apiRequest(endpoint, options = {}) {
    const headers = { 'Content-Type': 'application/json', ...options.headers };
    if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`;
    const res = await fetch(`${API_BASE}${endpoint}`, { ...options, headers });
    if (res.status === 401) {
        const refresh = refreshToken || localStorage.getItem('refresh_token');
        if (refresh) {
            const r = await fetch(`${API_BASE}/auth/refresh`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh_token: refresh })
            });
            if (r.ok) {
                const data = await r.json();
                localStorage.setItem('access_token', data.access_token);
                localStorage.setItem('refresh_token', data.refresh_token);
                accessToken = data.access_token;
                refreshToken = data.refresh_token;
                headers['Authorization'] = `Bearer ${data.access_token}`;
                return fetch(`${API_BASE}${endpoint}`, { ...options, headers });
            }
        }
        localStorage.clear();
        window.location.href = '/index.html';
    }
    return res;
}

function closeModal(id) { document.getElementById(id).style.display = 'none'; }

function taskTypeBadge(t) {
    const icon = { Medication: '💊', Lab: '🧪', Observation: '👁️', General: '📋' }[t.task_type] || '📋';
    const sourceColor = t.source === 'Auto' ? '#2F6F52' : '#667169';
    return `<span title="${t.task_type || 'General'}">${icon}</span> <span style="font-size:10px;color:${sourceColor};border:1px solid ${sourceColor};border-radius:8px;padding:0 6px;">${t.source || 'Manual'}</span>`;
}

// Oncology Review Results PDF item 14: Medication Administration Record (MAR) -- a real
// per-dose timestamp, distinct from a Task's generic completed_at. Shared between ipd.html
// (Nurse) and headnurse.html (HeadNurse), both of which render a `#mar-history-<patientId>`
// placeholder div in their Medication tab for loadMarHistory() to fill.
async function recordMedicationAdministration(patientId, drugName, dose, route) {
    const statusInput = prompt('Status (Given / Missed / Refused / Held):', 'Given');
    if (!statusInput) return;
    const status = ['Given', 'Missed', 'Refused', 'Held'].find(s => s.toLowerCase() === statusInput.trim().toLowerCase());
    if (!status) { alert('Status must be one of: Given, Missed, Refused, Held'); return; }
    const res = await apiRequest('/ipd/medication-administrations', {
        method: 'POST',
        body: JSON.stringify({ patient_id: patientId, drug_name: drugName, dose, route, status })
    });
    if (!res.ok) { const err = await res.json().catch(() => ({})); alert(err.detail || 'Failed to record administration'); return; }
    loadMarHistory(patientId);
}

async function loadMarHistory(patientId) {
    const el = document.getElementById(`mar-history-${patientId}`);
    if (!el) return;
    const res = await apiRequest(`/ipd/medication-administrations?patient_id=${patientId}`);
    if (!res.ok) { el.innerHTML = ''; return; }
    const records = await res.json();
    el.innerHTML = '<h3 style="font-size:13px;color:#667169;margin-bottom:6px;text-transform:uppercase;">🕒 Administration History</h3>' +
        (records.length ? records.map(r => `
            <div style="background:#FBFAF7;border:1px solid #E6E7E2;padding:6px 10px;border-radius:6px;margin-bottom:4px;">
                <strong>${r.drug_name}</strong>${r.dose ? ' - ' + r.dose : ''} - ${r.status}
                <span style="color:#93998F;">(${new Date(r.administered_at).toLocaleString()}${r.administered_by ? ', by nurse #' + r.administered_by : ''})</span>
            </div>
        `).join('') : '<p style="color:#93998F;">No administrations recorded yet.</p>');
}

// Oncology Review Results PDF item 18: Raise Request workflow for lab-related requests --
// thin wrapper over the existing Task model (task_type="Lab", already rendered distinctly by
// taskTypeBadge() above), open to Nurse and HeadNurse alike (unlike the stricter, unrelated
// HeadNurse-only general task-creation flow).
async function raiseLabRequest(patientId) {
    const requestType = prompt('Request type (e.g. Blood Sample, Urine Sample, Other Lab):', 'Blood Sample');
    if (!requestType || !requestType.trim()) return;
    const details = prompt('Details (tests needed, urgency, etc.) — optional:', '') || '';
    const res = await apiRequest('/ipd/lab-requests', {
        method: 'POST',
        body: JSON.stringify({ patient_id: patientId, request_type: requestType.trim(), details: details.trim() })
    });
    if (!res.ok) { const err = await res.json().catch(() => ({})); alert(err.detail || 'Failed to raise request'); return; }
    alert('Lab request raised — see it under Tasks.');
    showPatientDetail(patientId);
}
