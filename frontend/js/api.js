/*
 * Shared frontend infrastructure for every Aivana HMS page: authenticated fetch wrapper
 * (with one-shot access-token refresh on 401), role-based top navigation, and small render
 * helpers. Loaded by every page via <script src="/static/js/api.js"></script> before the
 * page's own inline script runs.
 */
const API_BASE = '/api';

const ROLE_HOME = {
  Admin: '/admin.html',
  Doctor: '/opd.html',
  HeadNurse: '/headnurse.html',
  Nurse: '/ipd.html',
  NursingStation: '/frontdesk.html',
  Pharmacist: '/pharmacy.html',
  InventoryManager: '/inventory.html',
  Billing: '/billing.html',
  TPA: '/tpa.html',
  // CCA Oncology OS personas — fully migrated to dedicated light-theme role pages
  CCAFrontDesk: '/frontdesk.html',
  CCANurseNavigator: '/nurse_navigator.html',
  CCAMedicalOncologist: '/medical_oncologist.html',
  CCASurgicalOncologist: '/surgical_oncologist.html',
  CCARadiationOncologist: '/radiation_oncologist.html',
  CCARadiologist: '/radiologist.html',
  CCARadiologyCoordinator: '/radiology_coordinator.html',
  CCAPathologist: '/pathologist.html',
  CCALabPhlebotomy: '/laboratory.html',
  CCAInfusionNurse: '/infusion_nurse.html',
  CCAMDTCoordinator: '/mdt_coordinator.html',
  CCAPatientLiaison: '/patient_liaison.html',
  CCAFinancialCounsellor: '/patient_financial_services.html',
  // No reference screenshots exist for this role (see plan) -- stays on the old dark-theme SPA,
  // which already has a distinct read-only "Assigned Cases" view for it, rather than pointing
  // at mdt_coordinator.html (whose Auth.requirePage doesn't allow this role and would redirect-loop).
  CCAExternalMDTSpecialist: '/cca_os.html',
};

const NAV_ITEMS = [
  { key: 'frontdesk', label: 'Front Desk', href: '/frontdesk.html', roles: ['Admin', 'NursingStation', 'Doctor', 'CCAFrontDesk'] },
  { key: 'opd', label: 'OPD Scribe', href: '/opd.html', roles: ['Doctor'] },
  { key: 'medical_oncologist', label: 'Medical Oncologist', href: '/medical_oncologist.html', roles: ['Admin', 'CCAMedicalOncologist'] },
  { key: 'surgical_oncologist', label: 'Surgical Oncologist', href: '/surgical_oncologist.html', roles: ['Admin', 'CCASurgicalOncologist'] },
  { key: 'radiation_oncologist', label: 'Radiation Oncology', href: '/radiation_oncologist.html', roles: ['Admin', 'CCARadiationOncologist'] },
  { key: 'radiologist', label: 'Radiologist', href: '/radiologist.html', roles: ['Admin', 'CCARadiologist'] },
  { key: 'radiology_coordinator', label: 'Radiology Coordinator', href: '/radiology_coordinator.html', roles: ['Admin', 'CCARadiologyCoordinator'] },
  { key: 'pathologist', label: 'Pathologist', href: '/pathologist.html', roles: ['Admin', 'CCAPathologist'] },
  { key: 'laboratory', label: 'Laboratory', href: '/laboratory.html', roles: ['Admin', 'CCALabPhlebotomy'] },
  { key: 'infusion_nurse', label: 'Infusion Nurse', href: '/infusion_nurse.html', roles: ['Admin', 'CCAInfusionNurse'] },
  { key: 'mdt_coordinator', label: 'MDT Coordinator', href: '/mdt_coordinator.html', roles: ['Admin', 'CCAMDTCoordinator'] },
  { key: 'nurse_navigator', label: 'Nurse Navigator', href: '/nurse_navigator.html', roles: ['Admin', 'CCANurseNavigator'] },
  { key: 'patient_liaison', label: 'Patient Liaison', href: '/patient_liaison.html', roles: ['Admin', 'CCAPatientLiaison'] },
  { key: 'patient_financial_services', label: 'Financial Services', href: '/patient_financial_services.html', roles: ['Admin', 'CCAFinancialCounsellor'] },
  { key: 'ipd', label: 'Ward', href: '/ipd.html', roles: ['Doctor', 'Nurse', 'NursingStation', 'HeadNurse'] },
  { key: 'headnurse', label: 'Ward Oversight', href: '/headnurse.html', roles: ['HeadNurse'] },
  { key: 'pharmacy', label: 'Pharmacy', href: '/pharmacy.html', roles: ['Pharmacist', 'Admin'] },
  { key: 'inventory', label: 'Inventory', href: '/inventory.html', roles: ['Pharmacist', 'Admin', 'InventoryManager'] },
  { key: 'billing', label: 'Billing', href: '/billing.html', roles: ['Billing', 'Admin'] },
  { key: 'tpa', label: 'Patient Search', href: '/tpa.html', roles: ['TPA'] },
  { key: 'admin', label: 'Admin', href: '/admin.html', roles: ['Admin'] },
  { key: 'cca', label: '🎗️ Oncology OS', href: '/cca_os.html', roles: ['Doctor', 'Admin', 'HeadNurse', 'Nurse'] },
];

const Auth = {
  getAccessToken() { return localStorage.getItem('access_token'); },
  getRefreshToken() { return localStorage.getItem('refresh_token'); },
  getUser() {
    try { return JSON.parse(localStorage.getItem('hms_user') || 'null'); }
    catch (e) { return null; }
  },
  setSession(accessToken, refreshToken, user) {
    localStorage.setItem('access_token', accessToken);
    localStorage.setItem('refresh_token', refreshToken);
    if (user) localStorage.setItem('hms_user', JSON.stringify(user));
  },
  setTokens(accessToken, refreshToken) {
    localStorage.setItem('access_token', accessToken);
    localStorage.setItem('refresh_token', refreshToken);
  },
  clear() {
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('hms_user');
  },
  homeFor(role) { return ROLE_HOME[role] || '/index.html'; },
  logout() { Auth.clear(); window.location.href = '/index.html'; },
  /** Call at the very top of a page's script. Redirects away if not logged in or not permitted. */
  requirePage(allowedRoles) {
    const user = Auth.getUser();
    const token = Auth.getAccessToken();
    if (!user || !token) {
      window.location.href = '/index.html';
      throw new Error('redirecting to login');
    }
    if (allowedRoles && !allowedRoles.includes(user.role)) {
      window.location.href = Auth.homeFor(user.role);
      throw new Error('redirecting: role not permitted on this page');
    }
    return user;
  },
};

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

let _refreshInFlight = null;

async function _refreshAccessToken() {
  if (_refreshInFlight) return _refreshInFlight;
  _refreshInFlight = (async () => {
    const refreshToken = Auth.getRefreshToken();
    if (!refreshToken) throw new ApiError(401, 'No refresh token');
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) throw new ApiError(res.status, 'Session expired');
    const data = await res.json();
    Auth.setTokens(data.access_token, data.refresh_token);
    return data.access_token;
  })();
  try {
    return await _refreshInFlight;
  } finally {
    _refreshInFlight = null;
  }
}

/**
 * Core request helper. `path` is relative to API_BASE (e.g. "/patients/search?q=foo").
 * Retries exactly once after a successful token refresh on a 401; otherwise throws ApiError
 * with the FastAPI HTTPException `detail` string as its message.
 */
async function apiRequest(method, path, body, { retry = true } = {}) {
  const token = Auth.getAccessToken();
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (res.status === 401 && retry && Auth.getRefreshToken()) {
    try {
      await _refreshAccessToken();
      return apiRequest(method, path, body, { retry: false });
    } catch (e) {
      Auth.clear();
      window.location.href = '/index.html';
      throw new ApiError(401, 'Session expired, please log in again');
    }
  }

  let data = null;
  const text = await res.text();
  if (text) {
    try { data = JSON.parse(text); } catch (e) { data = null; }
  }

  if (!res.ok) {
    const detail = data && data.detail ? data.detail : `Request failed (${res.status})`;
    throw new ApiError(res.status, typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

const Api = {
  get: (path) => apiRequest('GET', path),
  post: (path, body) => apiRequest('POST', path, body ?? {}),
  patch: (path, body) => apiRequest('PATCH', path, body ?? {}),
  put: (path, body) => apiRequest('PUT', path, body ?? {}),
  delete: (path) => apiRequest('DELETE', path),
  async upload(path, formData) {
    const send = async (retry = true) => {
      const res = await fetch(`${API_BASE}${path}`, { method: 'POST', headers: { Authorization: `Bearer ${Auth.getAccessToken()}` }, body: formData });
      if (res.status === 401 && retry && Auth.getRefreshToken()) { await _refreshAccessToken(); return send(false); }
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new ApiError(res.status, data?.detail || `Upload failed (${res.status})`);
      return data;
    };
    return send();
  },
  async blob(path) {
    const send = async (retry = true) => {
      const res = await fetch(`${API_BASE}${path}`, { headers: { Authorization: `Bearer ${Auth.getAccessToken()}` } });
      if (res.status === 401 && retry && Auth.getRefreshToken()) { await _refreshAccessToken(); return send(false); }
      if (!res.ok) { const data = await res.json().catch(() => null); throw new ApiError(res.status, data?.detail || 'Could not open document'); }
      return res.blob();
    };
    return send();
  },
};

// ---------------------------------------------------------------------------
// Small render/format helpers shared by every page
// ---------------------------------------------------------------------------

function escapeHtml(value) {
  if (value === null || value === undefined) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function fmtMoney(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '-';
  return `₹${Number(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtDateTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function fmtDate(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function qs(sel, root) { return (root || document).querySelector(sel); }
function qsa(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

let _toastTimer = null;
function toast(message, type = 'info') {
  let el = document.getElementById('hms-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'hms-toast';
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.className = `hms-toast hms-toast--${type} hms-toast--visible`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.classList.remove('hms-toast--visible'); }, 4000);
}

function apiErrorMessage(err) {
  return err instanceof ApiError ? err.message : (err && err.message) || 'Something went wrong';
}

function renderNav(activeKey) {
  const container = document.getElementById('topnav');
  if (!container) return;
  const user = Auth.getUser();
  if (!user) return;
  const items = NAV_ITEMS.filter((item) => item.roles.includes(user.role));
  const links = items.map((item) => (
    `<a class="nav-link${item.key === activeKey ? ' nav-link--active' : ''}" href="${item.href}">${item.label}</a>`
  )).join('');
  container.innerHTML = `
    <div class="nav-brand">
      <span class="nav-brand__mark">Aivana</span><span class="nav-brand__sub">HMS</span>
    </div>
    <nav class="nav-links">${links}</nav>
    <div class="nav-user">
      <span class="nav-user__email">${escapeHtml(user.email)}</span>
      <span class="badge badge--role">${escapeHtml(user.role)}</span>
      <button class="btn btn--ghost btn--small" id="nav-logout-btn" type="button">Log out</button>
    </div>
  `;
  document.getElementById('nav-logout-btn').addEventListener('click', Auth.logout);
}

function openModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('modal--open');
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove('modal--open');
}
