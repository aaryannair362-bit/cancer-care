/**
 * AIvana CCA Cancer Care AI OS — Client Application Logic
 * Implements full 22-screen oncology state machine, 2-click provenance,
 * Human-in-the-loop verification, and demo controller scenarios.
 */

// Relative to api.js's Api.* helpers, which prepend '/api' -- so this resolves to /api/cca.
// Named distinctly from api.js's own top-level `API_BASE` const to avoid a duplicate-declaration
// SyntaxError (both scripts share the same global scope on cca_os.html).
const CCA_API_BASE = '/cca';
// Every CCA persona (see auth.py's CCA_ROLES) plus the original general HMS roles this OS
// launched under, kept for backward compatibility with already-provisioned demo accounts.
const CCA_ALL_ROLES = [
    'Doctor', 'Admin', 'HeadNurse', 'Nurse',
    'CCAFrontDesk', 'CCANurseNavigator', 'CCAMedicalOncologist', 'CCASurgicalOncologist',
    'CCARadiationOncologist', 'CCARadiologist', 'CCARadiologyCoordinator', 'CCAPathologist',
    'CCALabPhlebotomy', 'CCAInfusionNurse', 'CCAMDTCoordinator', 'CCAExternalMDTSpecialist',
    'CCAPatientLiaison', 'CCAFinancialCounsellor',
];
const ccaCurrentUser = Auth.requirePage(CCA_ALL_ROLES);
let currentPatientId = 1;
let currentPatientData = null;
let currentDocuments = [];
let currentFacts = [];
let currentContradictions = [];

// ---------------------------------------------------------------------------
// Role-aware sidebar (role-screen-spec "Final Sidebar" lists). Roles not listed here
// (Doctor/Admin/HeadNurse/Nurse -- the pre-existing generic logins this OS launched under)
// fall back to ALL_NAV_ITEMS, matching the original single-view behavior exactly.
// ---------------------------------------------------------------------------
const ALL_NAV_ITEMS = [
    { group: 'Command & Intake', items: [
        { tab: 'command_centre', icon: '🏥', label: 'Command Centre' },
        { tab: 'intake', icon: '🚶‍♂️', label: 'Nurse Intake & BSA' },
        { tab: 'opd', icon: '👨‍⚕️', label: 'Doctor OPD Consult' },
    ]},
    { group: 'Evidence & Verification', items: [
        { tab: 'documents', icon: '📁', label: 'Ingested Documents' },
        { tab: 'verification', icon: '🔍', label: 'Fact Verification' },
        { tab: 'results_inbox', icon: '📑', label: 'Results & Orders' },
    ]},
    { group: 'Clinical Intelligence', items: [
        { tab: 'summary', icon: '🔮', label: 'Patient 360 Summary' },
        { tab: 'staging', icon: '🎯', label: 'AJCC Staging Workspace' },
        { tab: 'nccn', icon: '📜', label: 'NCCN Guideline Context' },
        { tab: 'nexus', icon: '⚡', label: 'NEXUS Clinical Brief' },
        { tab: 'mdt', icon: '👥', label: 'Tumor Board (MDT)' },
    ]},
    { group: 'Delivery & Surveillance', items: [
        { tab: 'careplan', icon: '🛡️', label: 'Live Care Plan' },
        { tab: 'treatment_day', icon: '💉', label: 'Treatment Clearance' },
        { tab: 'response', icon: '📈', label: 'Response (RECIST 1.1)' },
        { tab: 'journey', icon: '🗺️', label: 'Timeline Journey' },
    ]},
];

const ROLE_NAV_ITEMS = {
    CCAFrontDesk: [
        { group: 'Command & Intake', items: [
            { tab: 'command_centre', icon: '🏥', label: 'Patients' },
            { tab: 'documents', icon: '📁', label: 'Registration / Referral Upload' },
        ]},
    ],
    CCANurseNavigator: [
        { group: 'Command & Intake', items: [
            { tab: 'command_centre', icon: '🏥', label: 'Patients' },
            { tab: 'intake', icon: '🚶‍♂️', label: 'Nurse Intake & BSA' },
        ]},
        { group: 'Clinical Intelligence', items: [{ tab: 'nexus', icon: '⚡', label: 'NEXUS' }] },
    ],
    CCAMedicalOncologist: [
        { group: 'Command & Intake', items: [
            { tab: 'command_centre', icon: '🏥', label: 'Patients' },
            { tab: 'opd', icon: '👨‍⚕️', label: 'Consultation' },
        ]},
        { group: 'Clinical Intelligence', items: [
            { tab: 'careplan', icon: '🛡️', label: 'Treatment Plan' },
            { tab: 'nexus', icon: '⚡', label: 'NEXUS' },
            { tab: 'nccn', icon: '📜', label: 'Guideline Pathway' },
            { tab: 'staging', icon: '🎯', label: 'Staging' },
            { tab: 'mdt', icon: '👥', label: 'MDT / Tumour Board' },
        ]},
    ],
    CCASurgicalOncologist: [
        { group: 'Command & Intake', items: [
            { tab: 'command_centre', icon: '🏥', label: 'Patients' },
            { tab: 'opd', icon: '👨‍⚕️', label: 'Consultation' },
        ]},
        { group: 'Clinical Intelligence', items: [
            { tab: 'careplan', icon: '🛡️', label: 'Surgical Plan' },
            { tab: 'nexus', icon: '⚡', label: 'NEXUS' },
            { tab: 'nccn', icon: '📜', label: 'Guideline Pathway' },
            { tab: 'staging', icon: '🎯', label: 'Staging' },
            { tab: 'mdt', icon: '👥', label: 'MDT / Tumour Board' },
        ]},
    ],
    CCARadiationOncologist: [
        { group: 'Command & Intake', items: [
            { tab: 'command_centre', icon: '🏥', label: 'Patients' },
            { tab: 'opd', icon: '👨‍⚕️', label: 'Consultation' },
        ]},
        { group: 'Clinical Intelligence', items: [
            { tab: 'careplan', icon: '🛡️', label: 'Radiation Plan' },
            { tab: 'nexus', icon: '⚡', label: 'NEXUS' },
            { tab: 'nccn', icon: '📜', label: 'Guideline Pathway' },
            { tab: 'staging', icon: '🎯', label: 'Staging' },
            { tab: 'mdt', icon: '👥', label: 'MDT / Tumour Board' },
        ]},
    ],
    CCARadiologist: [
        { group: 'Command & Intake', items: [{ tab: 'command_centre', icon: '🏥', label: 'Patients' }] },
        { group: 'Diagnostics', items: [
            { tab: 'imaging', icon: '🩻', label: 'Imaging Worklist' },
        ]},
        { group: 'Clinical Intelligence', items: [
            { tab: 'nexus', icon: '⚡', label: 'NEXUS' },
            { tab: 'mdt', icon: '👥', label: 'MDT / Tumour Board' },
        ]},
    ],
    CCARadiologyCoordinator: [
        { group: 'Command & Intake', items: [{ tab: 'command_centre', icon: '🏥', label: 'Patients' }] },
        { group: 'Diagnostics', items: [{ tab: 'imaging', icon: '🩻', label: 'Imaging Coordination' }] },
    ],
    CCAPathologist: [
        { group: 'Command & Intake', items: [{ tab: 'command_centre', icon: '🏥', label: 'Patients' }] },
        { group: 'Diagnostics', items: [
            { tab: 'pathology', icon: '🔬', label: 'Pathology Worklist' },
            { tab: 'molecular', icon: '🧬', label: 'Molecular Diagnostics' },
        ]},
        { group: 'Clinical Intelligence', items: [
            { tab: 'nexus', icon: '⚡', label: 'NEXUS' },
            { tab: 'mdt', icon: '👥', label: 'MDT / Tumour Board' },
        ]},
    ],
    CCALabPhlebotomy: [
        { group: 'Command & Intake', items: [{ tab: 'command_centre', icon: '🏥', label: 'Patients' }] },
        { group: 'Diagnostics', items: [{ tab: 'lab', icon: '🧪', label: 'Lab Worklist' }] },
    ],
    CCAInfusionNurse: [
        { group: 'Command & Intake', items: [{ tab: 'command_centre', icon: '🏥', label: 'Patients' }] },
        { group: 'Delivery & Surveillance', items: [{ tab: 'treatment_day', icon: '💉', label: 'Treatment Day / Infusion' }] },
        { group: 'Clinical Intelligence', items: [{ tab: 'nexus', icon: '⚡', label: 'NEXUS' }] },
    ],
    CCAMDTCoordinator: [
        { group: 'Command & Intake', items: [{ tab: 'command_centre', icon: '🏥', label: 'Patients' }] },
        { group: 'Clinical Intelligence', items: [
            { tab: 'mdt_coordinator', icon: '👥', label: 'MDT / Tumour Board' },
            { tab: 'nexus', icon: '⚡', label: 'NEXUS' },
        ]},
    ],
    CCAExternalMDTSpecialist: [
        { group: 'Clinical Intelligence', items: [
            { tab: 'external_mdt', icon: '👥', label: 'Assigned Cases' },
        ]},
    ],
    CCAPatientLiaison: [
        { group: 'Command & Intake', items: [{ tab: 'command_centre', icon: '🏥', label: 'Patients' }] },
        { group: 'Coordination', items: [
            { tab: 'coordination', icon: '🧭', label: 'Care Coordination' },
            { tab: 'financial', icon: '💳', label: 'Financial Counselling' },
        ]},
        { group: 'Clinical Intelligence', items: [{ tab: 'nexus', icon: '⚡', label: 'NEXUS' }] },
    ],
    CCAFinancialCounsellor: [
        { group: 'Command & Intake', items: [{ tab: 'command_centre', icon: '🏥', label: 'Patients' }] },
        { group: 'Coordination', items: [
            { tab: 'financial', icon: '💳', label: 'Financial Counselling' },
            { tab: 'financial', icon: '🧮', label: 'Estimates & Clearance' },
        ]},
    ],
};

function renderSidebarForRole() {
    const role = ccaCurrentUser.role;
    const sections = ROLE_NAV_ITEMS[role] || ALL_NAV_ITEMS;
    const container = document.getElementById('sidebar-nav-content');
    container.innerHTML = sections.map(section => `
        <div class="nav-group-title">${escapeHtml(section.group)}</div>
        ${section.items.map(item => `
            <a class="nav-item" onclick="switchTab('${item.tab}')">
                <span><span class="nav-icon">${item.icon}</span> ${escapeHtml(item.label)}</span>
            </a>
        `).join('')}
    `).join('');
    // Admin gets the Operations Dashboard on top of whatever generic access they already have.
    if (role === 'Admin') {
        container.innerHTML += `
            <div class="nav-group-title">Operations</div>
            <a class="nav-item" onclick="switchTab('admin_ops')"><span><span class="nav-icon">📊</span> Operations Dashboard</span></a>
        `;
    }
    document.getElementById('sidebar-user-line').textContent = `Logged in as: ${ccaCurrentUser.email} (${role})`;
    // Demo controls (header buttons + floating HUD) mutate/reset shared demo data -- backend
    // now enforces Admin-only (routers/cca.py's /demo/* endpoints); hide them for everyone else
    // rather than showing controls that will just 403.
    const isAdminUser = role === 'Admin';
    document.querySelectorAll('.demo-only-control').forEach(el => { el.style.display = isAdminUser ? '' : 'none'; });
    // Land on this role's first tab instead of Command Centre when the generic view isn't relevant.
    const firstTab = sections[0]?.items[0]?.tab;
    if (firstTab && firstTab !== 'command_centre') switchTab(firstTab);
}

// 1. App Initialization
document.addEventListener('DOMContentLoaded', async () => {
    renderSidebarForRole();
    await loadPatient(1);
    await loadCensusQueue();
    // Regression fix: this used to call a `loadDocumentsList()` that was never defined anywhere
    // in the file -- an unhandled ReferenceError here silently aborted every load call after
    // it, so staging/guideline/NEXUS/journey never populated on first page load. Renders both
    // the Documents screen's list and the Verification workspace's document picker.
    await loadDocumentsList();
    await loadContextualSummary('initial_consult');
    await loadStagingWorkspace();
    await loadGuidelineContext();
    await loadNexusBrief();
    await loadJourneyTimeline();
});

// 2. Navigation Tab Switcher
function switchTab(tabId) {
    document.querySelectorAll('.screen-view').forEach(view => view.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));

    const targetView = document.getElementById(`view-${tabId}`);
    if (targetView) targetView.classList.add('active');

    // Highlight matching sidebar nav item
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(item => {
        if (item.getAttribute('onclick') && item.getAttribute('onclick').includes(tabId)) {
            item.classList.add('active');
        }
    });

    // Refresh tab-specific data
    if (tabId === 'verification') loadVerificationWorkspace();
    if (tabId === 'staging') loadStagingWorkspace();
    if (tabId === 'nccn') loadGuidelineContext();
    if (tabId === 'nexus') loadNexusBrief();
    if (tabId === 'journey') loadJourneyTimeline();
    if (tabId === 'command_centre') loadCensusQueue();
    if (tabId === 'documents') loadDocumentsList();
    if (tabId === 'imaging') loadImagingWorklist();
    if (tabId === 'pathology') loadPathologyWorklist();
    if (tabId === 'molecular') loadMolecularTests();
    if (tabId === 'lab') loadLabWorklist();
    if (tabId === 'mdt_coordinator') loadMdtCoordinatorQueue();
    if (tabId === 'external_mdt') loadAssignedCases();
    if (tabId === 'financial') loadFinancialQueue();
    if (tabId === 'coordination') loadCoordinationQueue();
    if (tabId === 'admin_ops') loadOperationsDashboard();
}

// 3. Load Patient & Refresh Persistent Header
async function loadPatient(patientId) {
    try {
        currentPatientId = patientId;
        const data = await Api.get(`${CCA_API_BASE}/patients/${patientId}`);
        currentPatientData = data;

        const p = data.patient;
        const h = data.header;
        
        document.getElementById('hdr-name').textContent = p.name;
        document.getElementById('hdr-mrn').textContent = p.mrn;
        document.getElementById('hdr-age-sex').textContent = `${p.age}y / ${p.sex}`;
        document.getElementById('hdr-oncologist').textContent = p.primary_oncologist || 'Dr. Sarah Varma';
        document.getElementById('hdr-journey-state').textContent = p.journey_state;
        if (p.photo_url) document.getElementById('hdr-avatar').src = p.photo_url;
        
        if (h.bsa) document.getElementById('hdr-bsa').textContent = h.bsa;
        if (h.ecog !== null) document.getElementById('hdr-ecog').textContent = h.ecog;
        
        // Update Staging Pill
        const stagingPill = document.getElementById('pill-staging-engine');
        const stagingText = document.getElementById('pill-staging-text');
        const stagingStatus = h.engine_pills.staging.status;
        stagingText.textContent = stagingStatus.replace('_', ' ');
        stagingPill.className = `engine-pill ${stagingStatus === 'CLINICIAN_CONFIRMED' ? 'confirmed' : stagingStatus === 'READY_FOR_STAGING' ? 'ready' : 'incomplete'}`;
        
        // Update Guideline Pill
        const guidelinePill = document.getElementById('pill-guideline-engine');
        const guidelineText = document.getElementById('pill-guideline-text');
        const guidelineStatus = h.engine_pills.guideline.status;
        guidelineText.textContent = guidelineStatus.replace('_', ' ');
        guidelinePill.className = `engine-pill ${guidelineStatus === 'READY' ? 'ready' : 'danger'}`;
        
        // Contradiction Pill
        const ctrPill = document.getElementById('hdr-contradiction-pill');
        if (h.open_contradictions_count > 0) {
            ctrPill.style.display = 'inline-flex';
            ctrPill.textContent = `⚠️ ${h.open_contradictions_count} Open Laterality Conflict`;
        } else {
            ctrPill.style.display = 'none';
        }
        
    } catch (err) {
        console.error("Failed to load patient:", err);
    }
}

// 4. Command Centre Census Queue (SCR-02)
async function loadCensusQueue() {
    try {
        const data = await Api.get(`${CCA_API_BASE}/patients`);
        const tbody = document.getElementById('census-table-body');
        tbody.innerHTML = '';

        data.results.forEach(p => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong style="color:var(--brand-primary);">${escapeHtml(p.mrn)}</strong></td>
                <td><strong>${escapeHtml(p.name)}</strong></td>
                <td>${escapeHtml(p.age)}y / ${escapeHtml(p.sex)}</td>
                <td><span class="badge-pill ${p.journey_state === 'InTreatment' ? 'badge-curative' : 'badge-stage'}">${escapeHtml(p.journey_state)}</span></td>
                <td>${escapeHtml(p.primary_oncologist || 'Oncology Care Team')}</td>
                <td><span class="badge-pill ${p.staging_state === 'CLINICIAN_CONFIRMED' ? 'badge-curative' : 'badge-warning'}">${escapeHtml(p.staging_state)}</span></td>
                <td><button class="btn-cca btn-outline" style="font-size:11px;padding:3px 8px;" onclick="loadPatient(${Number(p.id)})">Open Chart</button></td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        console.error("Error loading census:", err);
    }
}

// 4b. Ingested Documents screen -- upload + list (Front Desk registration / referral upload)
async function loadDocumentsList() {
    try {
        const data = await Api.get(`${CCA_API_BASE}/documents?patient_id=${currentPatientId}`);
        const tbody = document.getElementById('documents-list-body');
        if (tbody) {
            tbody.innerHTML = data.documents.map(d => `
                <tr>
                    <td>${escapeHtml(d.filename)}</td>
                    <td><span class="badge-pill badge-stage">${escapeHtml(d.classification)}</span></td>
                    <td>${d.confidence != null ? Math.round(d.confidence * 100) + '%' : '-'}</td>
                    <td>${Number(d.fact_count)}</td>
                    <td>${Number(d.verified_count)}</td>
                    <td>${escapeHtml(d.uploaded_at ? new Date(d.uploaded_at).toLocaleString() : '-')}</td>
                </tr>
            `).join('') || '<tr><td colspan="6">No documents uploaded yet.</td></tr>';
        }
    } catch (err) {
        console.error("Error loading documents:", err);
    }
}

async function submitDocumentUpload() {
    const input = document.getElementById('doc-upload-input');
    const statusEl = document.getElementById('doc-upload-status');
    if (!input.files.length) { toast('Choose a file first', 'error'); return; }
    const formData = new FormData();
    formData.append('file', input.files[0]);
    statusEl.textContent = 'Uploading and extracting...';
    try {
        const data = await Api.upload(`${CCA_API_BASE}/documents?patient_id=${currentPatientId}`, formData);
        statusEl.textContent = `✅ Classified as ${data.document.classification}. ${data.facts_drafted} candidate fact(s) drafted for verification.`;
        input.value = '';
        await loadDocumentsList();
    } catch (err) {
        statusEl.textContent = `❌ ${apiErrorMessage(err)}`;
    }
}

// 5. Verification Workspace (SCR-07 / WOW 2)
async function loadVerificationWorkspace() {
    try {
        const docsData = await Api.get(`${CCA_API_BASE}/documents?patient_id=${currentPatientId}`);
        currentDocuments = docsData.documents;

        const docSelect = document.getElementById('doc-picker-select');
        docSelect.innerHTML = '';
        currentDocuments.forEach(d => {
            const opt = document.createElement('option');
            opt.value = d.id;
            opt.textContent = `${d.filename} (${d.classification})`;
            docSelect.appendChild(opt);
        });

        if (currentDocuments.length > 0) {
            await loadDocExtractions(currentDocuments[0].id);
        }

        // Load contradictions
        const ctrData = await Api.get(`${CCA_API_BASE}/patients/${currentPatientId}/contradictions`);
        currentContradictions = ctrData.contradictions;

        const bannerContainer = document.getElementById('contradiction-banner-container');
        bannerContainer.innerHTML = '';

        const openCtrs = currentContradictions.filter(c => c.status === 'OPEN');
        if (openCtrs.length > 0) {
            openCtrs.forEach(c => {
                const banner = document.createElement('div');
                banner.className = 'workspace-card';
                banner.style.border = '1px solid #f43f5e';
                banner.style.background = 'rgba(244, 63, 94, 0.1)';
                banner.innerHTML = `
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <div>
                            <span class="badge-pill" style="background:#f43f5e;color:#fff;margin-right:8px;">⚠️ ${escapeHtml(c.rule_id)} OPEN CONTRADICTION</span>
                            <strong style="color:#f87171;">Laterality Discrepancy Detected Across Ingested Documents</strong>
                            <p style="font-size:12px;color:var(--text-secondary);margin-top:6px;">${escapeHtml(c.description)}</p>
                        </div>
                        <button class="btn-cca btn-primary" onclick="openResolveContradictionModal(${Number(c.id)})">⚖️ Clinician Disposition</button>
                    </div>
                `;
                bannerContainer.appendChild(banner);
            });
        }
    } catch (err) {
        console.error("Error loading verification workspace:", err);
    }
}

async function loadDocExtractions(docId) {
    try {
        const data = await Api.get(`${CCA_API_BASE}/extractions/${docId}`);

        document.getElementById('doc-ocr-display').textContent = data.document.ocr_text || "No OCR text extracted.";
        document.getElementById('fact-count-tag').textContent = `${data.facts.length} Candidate Facts`;

        const factsContainer = document.getElementById('facts-list-container');
        factsContainer.innerHTML = '';

        data.facts.forEach(f => {
            const card = document.createElement('div');
            card.style.background = 'var(--bg-card)';
            card.style.border = f.is_conflicted ? '1px solid #f43f5e' : '1px solid var(--border-subtle)';
            card.style.borderRadius = '8px';
            card.style.padding = '12px';

            // f.value/f.verbatim originate from OCR'd/AI-extracted document text -- never trust
            // it as markup. escapeHtml() every field pulled from a patient document or AI output.
            card.innerHTML = `
                <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                    <div>
                        <span class="badge-pill" style="background:rgba(255,255,255,0.06);font-size:10px;">${escapeHtml(f.type)}</span>
                        <div style="font-size:13px;font-weight:600;margin-top:4px;color:var(--text-primary);">${escapeHtml(f.value)}</div>
                        <div style="font-size:11px;color:var(--text-muted);margin-top:2px;">Verbatim: "<em>${escapeHtml(f.verbatim)}</em>"</div>
                    </div>
                    <div>
                        <span class="badge-pill ${f.status === 'VERIFIED' ? 'badge-curative' : f.status === 'REJECTED' ? 'badge-warning' : 'badge-stage'}">${escapeHtml(f.status)}</span>
                    </div>
                </div>
                <div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px;padding-top:8px;border-top:1px solid rgba(255,255,255,0.04);">
                    <span class="provenance-link" onclick="openProvenanceDrawer(${Number(f.id)})">🔍 View Source (p.${Number(f.page)})</span>
                    <div style="display:flex;gap:6px;">
                        ${f.status === 'PROPOSED' && !f.is_conflicted ? `
                            <button class="btn-cca btn-outline" style="font-size:10px;padding:2px 8px;" onclick="acceptFact(${Number(f.id)})">Accept</button>
                            <button class="btn-cca btn-outline" style="font-size:10px;padding:2px 8px;color:#f87171;" onclick="rejectFact(${Number(f.id)})">Reject</button>
                        ` : ''}
                    </div>
                </div>
            `;
            factsContainer.appendChild(card);
        });
    } catch (err) {
        console.error("Error loading document extractions:", err);
    }
}

async function acceptFact(factId) {
    try {
        await Api.post(`${CCA_API_BASE}/verification/${factId}/accept`);
        const currentDocId = document.getElementById('doc-picker-select').value;
        await loadDocExtractions(currentDocId);
        await loadPatient(currentPatientId);
    } catch (err) {
        console.error("Error accepting fact:", err);
        toast(apiErrorMessage(err), 'error');
    }
}

// Referenced by the "Reject" button in loadDocExtractions but was never implemented -- the
// backend endpoint (POST /verification/{fact_id}/reject) already existed with no caller.
async function rejectFact(factId) {
    try {
        await Api.post(`${CCA_API_BASE}/verification/${factId}/reject`, { reason: 'Clinician rejection' });
        const currentDocId = document.getElementById('doc-picker-select').value;
        await loadDocExtractions(currentDocId);
        await loadPatient(currentPatientId);
    } catch (err) {
        console.error("Error rejecting fact:", err);
        toast(apiErrorMessage(err), 'error');
    }
}

async function bulkAcceptFacts() {
    try {
        const docId = document.getElementById('doc-picker-select').value;
        const data = await Api.get(`${CCA_API_BASE}/extractions/${docId}`);
        const proposedIds = data.facts.filter(f => f.status === 'PROPOSED').map(f => f.id);

        await Api.post(`${CCA_API_BASE}/verification/bulk-accept`, { fact_ids: proposedIds });
        await loadDocExtractions(docId);
        await loadPatient(currentPatientId);
    } catch (err) {
        console.error("Error bulk accepting facts:", err);
        toast(apiErrorMessage(err), 'error');
    }
}

// 6. Patient 360 Summary (SCR-10 / WOW 1)
async function loadContextualSummary(context = 'initial_consult') {
    try {
        const data = await Api.get(`${CCA_API_BASE}/patients/${currentPatientId}/summary?context=${encodeURIComponent(context)}`);
        const grid = document.getElementById('summary-blocks-grid');
        grid.innerHTML = '';

        data.blocks.forEach(b => {
            const card = document.createElement('div');
            card.className = 'workspace-card';
            card.innerHTML = `
                <div class="card-header">
                    <div class="card-title">${escapeHtml(b.title)}</div>
                    <span class="badge-pill ${b.tier === 1 ? 'badge-curative' : 'badge-stage'}">Tier ${Number(b.tier)}</span>
                </div>
                <div style="font-size:14px;font-weight:600;color:${b.absenceState === 'CONTRADICTED' ? '#fb7185' : 'var(--text-primary)'};line-height:1.5;">
                    ${escapeHtml(b.value)}
                </div>
                <div style="margin-top:12px;display:flex;justify-content:space-between;align-items:center;">
                    <span class="absence-token ${b.absenceState === 'CONTRADICTED' ? 'absence-contradicted' : b.absenceState === 'NOT_RECORDED' ? 'absence-not-recorded' : 'absence-unknown'}">${escapeHtml(b.absenceState)}</span>
                    ${b.provenance ? `<span class="provenance-link" onclick="openProvenanceDrawer(${Number(b.provenance.fact_id)})">📄 Source Fact #${Number(b.provenance.fact_id)}</span>` : ''}
                </div>
            `;
            grid.appendChild(card);
        });
    } catch (err) {
        console.error("Error loading contextual summary:", err);
    }
}

// 7. Staging Workspace (SCR-17 / WOW 3)
async function loadStagingWorkspace() {
    try {
        const data = await Api.get(`${CCA_API_BASE}/patients/${currentPatientId}/staging`);
        const readiness = data.readiness;
        
        const banner = document.getElementById('staging-readiness-banner');
        const actionSlot = document.getElementById('staging-action-button-slot');
        
        if (readiness.state === 'CLINICIAN_CONFIRMED') {
            banner.innerHTML = `
                <div style="background:rgba(16,185,129,0.15);border:1px solid #10b981;padding:16px;border-radius:8px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <div>
                            <span class="badge-pill badge-curative">✅ CLINICIAN CONFIRMED STAGE</span>
                            <h3 style="font-size:18px;color:#34d399;margin-top:4px;">${escapeHtml(readiness.confirmed_record.stage_value)}</h3>
                            <div style="font-size:12px;color:var(--text-secondary);margin-top:2px;">Confirmed by ${escapeHtml(readiness.confirmed_record.confirmed_by)}</div>
                        </div>
                        <span class="badge-pill badge-curative">NCCN Ready</span>
                    </div>
                </div>
            `;
            actionSlot.innerHTML = `<button class="btn-cca btn-outline" onclick="openStagingConfirmModal()">Re-Confirm / Amend Stage</button>`;
        } else if (readiness.state === 'READY_FOR_STAGING') {
            banner.innerHTML = `
                <div style="background:rgba(6,182,212,0.15);border:1px solid #06b6d4;padding:16px;border-radius:8px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <div>
                            <span class="badge-pill badge-curative">🎯 READY FOR CLINICIAN STAGING</span>
                            <p style="font-size:13px;color:var(--text-primary);margin-top:4px;">All evidence inputs (T2, N0, M0, Histology Grade 2) verified and complete. Ready for doctor confirmation.</p>
                        </div>
                        <button class="btn-cca btn-primary" onclick="openStagingConfirmModal()">✍️ Sign-Off AJCC Stage</button>
                    </div>
                </div>
            `;
            actionSlot.innerHTML = `<button class="btn-cca btn-primary" onclick="openStagingConfirmModal()">✍️ Confirm Stage</button>`;
        } else {
            banner.innerHTML = `
                <div style="background:rgba(245,158,11,0.15);border:1px solid #f59e0b;padding:16px;border-radius:8px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <div>
                            <span class="badge-pill badge-warning">⏳ EVIDENCE INCOMPLETE (MISSING M-EVIDENCE)</span>
                            <p style="font-size:13px;color:var(--text-primary);margin-top:4px;">Missing distant staging imaging (CECT Chest/Abdomen). Cannot stage autonomously (Safety Rule G-5).</p>
                        </div>
                        <button class="btn-cca btn-primary" onclick="triggerSimulateResult()">🔬 Ingest CT Result (cM0)</button>
                    </div>
                </div>
            `;
            actionSlot.innerHTML = `<button class="btn-cca btn-outline" disabled>Awaiting M0</button>`;
        }
    } catch (err) {
        console.error("Error loading staging workspace:", err);
    }
}

// 8. NCCN Guideline Context (SCR-19 / WOW 4)
async function loadGuidelineContext() {
    const container = document.getElementById('nccn-content-container');
    let data;
    try {
        data = await Api.get(`${CCA_API_BASE}/patients/${currentPatientId}/guidelines/context`);
    } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
            container.innerHTML = `
                <div class="workspace-card" style="border:1px solid var(--border-subtle);text-align:center;padding:40px;">
                    <div style="font-size:36px;margin-bottom:12px;">🔒</div>
                    <h3 style="font-size:16px;color:var(--text-primary);">NCCN Guideline Context Gated</h3>
                    <p style="font-size:13px;color:var(--text-secondary);max-width:500px;margin:8px auto 16px;">
                        Under AI Safety Rule G-5, clinical guideline recommendation pathways remain locked until AJCC stage is formally confirmed by the treating oncologist.
                    </p>
                    <button class="btn-cca btn-primary" onclick="switchTab('staging')">Go to Staging Workspace</button>
                </div>
            `;
        } else {
            console.error("Error loading guideline context:", err);
        }
        return;
    }

    try {
        container.innerHTML = `
            <div class="workspace-card" style="border-left: 4px solid var(--brand-primary); margin-bottom: 20px;">
                <div class="card-header">
                    <div class="card-title">Matched Pathway Node: ${escapeHtml(data.pathway_node)}</div>
                    <span class="badge-pill badge-curative">${escapeHtml(data.guideline_version)}</span>
                </div>
                <div class="grid-3" style="margin-bottom: 16px;">
                    ${data.variables_matched.map(v => `
                        <div style="background:var(--bg-card);padding:10px;border-radius:6px;">
                            <div style="font-size:10px;color:var(--text-muted);">${escapeHtml(v.variable)}</div>
                            <div style="font-size:12px;font-weight:600;margin-top:2px;">${escapeHtml(v.value)}</div>
                        </div>
                    `).join('')}
                </div>
                <div style="display:flex;flex-direction:column;gap:12px;">
                    ${data.pathway_options.map(opt => `
                        <div style="background:var(--bg-card);padding:16px;border-radius:8px;border:1px solid var(--border-subtle);">
                            <div style="font-size:14px;font-weight:700;color:var(--brand-primary);">${escapeHtml(opt.sequence)}</div>
                            <div style="font-size:13px;color:var(--text-primary);margin-top:4px;"><strong>Protocol:</strong> ${escapeHtml(opt.recommendation)}</div>
                            <div style="font-size:12px;color:var(--text-secondary);margin-top:2px;"><em>Rationale:</em> ${escapeHtml(opt.clinical_intent)}</div>
                        </div>
                    `).join('')}
                </div>
                <div style="margin-top:16px;font-size:11px;color:var(--text-muted);border-top:1px solid rgba(255,255,255,0.06);padding-top:10px;">
                    ⚠️ ${escapeHtml(data.disclaimer)}
                </div>
            </div>
        `;
    } catch (err) {
        console.error("Error rendering guideline context:", err);
    }
}

// 9. NEXUS 13-Section Clinical Brief (SCR-20/21 / WOW 5)
async function loadNexusBrief() {
    try {
        const data = await Api.get(`${CCA_API_BASE}/patients/${currentPatientId}/clinical-brief`);
        const container = document.getElementById('nexus-brief-container');
        container.innerHTML = '';

        if (!data.sections) return;

        Object.keys(data.sections).forEach((key, idx) => {
            const sec = data.sections[key];
            const card = document.createElement('div');
            card.className = 'workspace-card';
            card.style.marginBottom = '0';
            card.innerHTML = `
                <div class="card-header">
                    <div class="card-title">
                        <span class="badge-pill badge-stage">${idx + 1}</span>
                        ${escapeHtml(sec.title)}
                    </div>
                </div>
                <div style="font-size:13px;color:var(--text-primary);line-height:1.6;">
                    ${escapeHtml(sec.content)}
                </div>
            `;
            container.appendChild(card);
        });
    } catch (err) {
        console.error("Error loading NEXUS brief:", err);
    }
}

// 10. Timeline Journey (SCR-42)
async function loadJourneyTimeline() {
    try {
        const data = await Api.get(`${CCA_API_BASE}/patients/${currentPatientId}/journey`);
        const container = document.getElementById('journey-timeline-container');
        container.innerHTML = '';

        document.getElementById('hdr-journey-count').textContent = `${data.journey_events.length} Events`;

        data.journey_events.reverse().forEach(e => {
            const item = document.createElement('div');
            item.className = 'workspace-card';
            item.style.marginBottom = '0';
            item.style.borderLeft = `3px solid ${e.event_category === 'STAGING' ? '#10b981' : e.event_category === 'MDT' ? '#6366f1' : 'var(--brand-primary)'}`;
            item.innerHTML = `
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <span class="badge-pill badge-stage" style="font-size:10px;">${escapeHtml(e.event_category)}</span>
                        <strong style="font-size:14px;margin-left:8px;color:var(--text-primary);">${escapeHtml(e.event_title)}</strong>
                    </div>
                    <span style="font-size:11px;color:var(--text-muted);">${escapeHtml(new Date(e.timestamp).toLocaleTimeString())}</span>
                </div>
                <p style="font-size:12px;color:var(--text-secondary);margin-top:6px;">${escapeHtml(e.description)}</p>
                <div style="font-size:11px;color:var(--text-muted);margin-top:6px;">Actor: <strong>${escapeHtml(e.actor_name)}</strong> (${escapeHtml(e.actor_role)})</div>
            `;
            container.appendChild(item);
        });
    } catch (err) {
        console.error("Error loading journey:", err);
    }
}

// 11. 2-Click View Source Provenance Drawer (DSC-03 / WOW 2)
async function openProvenanceDrawer(factId) {
    try {
        const data = await Api.get(`${CCA_API_BASE}/clinical-facts/${factId}/provenance`);

        const drawer = document.getElementById('provenance-drawer');
        const slot = document.getElementById('provenance-details-slot');

        slot.innerHTML = `
            <div style="background:var(--bg-card);padding:16px;border-radius:8px;margin-bottom:16px;">
                <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;">Fact Type</div>
                <div style="font-size:14px;font-weight:700;color:var(--brand-primary);margin-top:2px;">${escapeHtml(data.fact_type)}</div>
                <div style="font-size:13px;font-weight:600;margin-top:6px;">"${escapeHtml(data.value)}"</div>
            </div>

            <div style="background:#040711;padding:16px;border-radius:8px;border:1px solid var(--border-subtle);margin-bottom:16px;">
                <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;margin-bottom:6px;">Verbatim Document Span</div>
                <p style="font-family:'JetBrains Mono',monospace;font-size:12px;color:#38bdf8;line-height:1.5;">
                    "${escapeHtml(data.verbatim_span)}"
                </p>
                <div style="margin-top:8px;font-size:11px;color:var(--text-secondary);">
                    Confidence: <strong>${escapeHtml((data.confidence * 100).toFixed(0))}%</strong> • Page: <strong>${Number(data.page_number)}</strong>
                </div>
            </div>

            <div style="background:var(--bg-card);padding:16px;border-radius:8px;margin-bottom:16px;">
                <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;">Origin Document</div>
                <div style="font-size:13px;font-weight:600;margin-top:2px;">📄 ${escapeHtml(data.document.filename)}</div>
                <div style="font-size:11px;color:var(--text-secondary);margin-top:2px;">Classification: ${escapeHtml(data.document.classification)}</div>
            </div>

            <div style="background:var(--bg-card);padding:16px;border-radius:8px;">
                <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;">Verification Audit Log</div>
                <div style="font-size:12px;color:var(--text-primary);margin-top:4px;">Status: <strong>${escapeHtml(data.verification_history.status)}</strong></div>
                <div style="font-size:11px;color:var(--text-secondary);margin-top:2px;">Verified by: ${escapeHtml(data.verification_history.verified_by || 'Pending Doctor Verification')}</div>
            </div>
        `;

        drawer.classList.add('open');
    } catch (err) {
        console.error("Error opening provenance drawer:", err);
    }
}

function closeProvenanceDrawer() {
    document.getElementById('provenance-drawer').classList.remove('open');
}

// 12. Modal Handlers
function openModal(htmlContent) {
    const modalBox = document.getElementById('app-modal-box');
    modalBox.innerHTML = htmlContent;
    document.getElementById('app-modal-overlay').classList.add('active');
}

function closeModal() {
    document.getElementById('app-modal-overlay').classList.remove('active');
}

// Modal: Resolve Contradiction CTR-01
function openResolveContradictionModal(ctrId) {
    openModal(`
        <div class="card-header">
            <div class="card-title" style="color:#fb7185;">⚖️ Clinician Contradiction Disposition</div>
            <button onclick="closeModal()" style="background:transparent;border:none;color:var(--text-secondary);font-size:20px;cursor:pointer;">&times;</button>
        </div>
        <p style="font-size:13px;color:var(--text-secondary);line-height:1.5;margin-bottom:16px;">
            Referral letter notes "Left Breast", whereas USG Breast and Surgical Core Biopsy report "Right Breast 10 o'clock mass". Select clinician disposition:
        </p>
        <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:20px;">
            <label style="background:var(--bg-card);padding:12px;border-radius:8px;display:flex;align-items:center;gap:10px;cursor:pointer;">
                <input type="radio" name="disposition" value="CONFIRMED_RIGHT_LATERALITY" checked />
                <div>
                    <strong style="font-size:13px;color:#34d399;">Confirm Right Laterality (Pathology Confirmed)</strong>
                    <div style="font-size:11px;color:var(--text-secondary);">Core needle biopsy and ultrasound confirm Right breast 10 o'clock mass. Referral left noted as error.</div>
                </div>
            </label>
            <label style="background:var(--bg-card);padding:12px;border-radius:8px;display:flex;align-items:center;gap:10px;cursor:pointer;">
                <input type="radio" name="disposition" value="REQUEST_REPEAT_EXAM" />
                <div>
                    <strong style="font-size:13px;">Request Repeat Physical Examination</strong>
                </div>
            </label>
        </div>
        <div style="display:flex;justify-content:flex-end;gap:10px;">
            <button class="btn-cca btn-outline" onclick="closeModal()">Cancel</button>
            <button class="btn-cca btn-primary" onclick="submitResolveContradiction(${ctrId})">Confirm Disposition</button>
        </div>
    `);
}

async function submitResolveContradiction(ctrId) {
    try {
        await Api.post(`${CCA_API_BASE}/contradictions/${ctrId}/disposition`, {
            disposition: 'CONFIRMED_RIGHT_LATERALITY',
            note: 'Confirmed Right breast laterality from surgical pathology report. Left referral noted as clerical error.'
        });
        closeModal();
        await loadPatient(currentPatientId);
        await loadVerificationWorkspace();
        await loadJourneyTimeline();
    } catch (err) {
        console.error("Error resolving contradiction:", err);
        toast(apiErrorMessage(err), 'error');
    }
}

// Modal: Clinician Staging Confirmation (staging.confirm)
function openStagingConfirmModal() {
    openModal(`
        <div class="card-header">
            <div class="card-title">✍️ Clinician AJCC Stage Confirmation Gate</div>
            <button onclick="closeModal()" style="background:transparent;border:none;color:var(--text-secondary);font-size:20px;cursor:pointer;">&times;</button>
        </div>
        <div style="display:flex;flex-direction:column;gap:12px;margin-bottom:20px;">
            <div>
                <label style="font-size:11px;color:var(--text-muted);text-transform:uppercase;">Confirmed Stage Designation</label>
                <input id="stg-stage-val" type="text" value="cT2 cN0 cM0 - Stage IIA" style="width:100%;padding:10px;background:var(--bg-card);border:1px solid var(--border-bright);border-radius:6px;color:var(--text-primary);font-size:14px;font-weight:600;margin-top:4px;" />
            </div>
            <div class="grid-3">
                <div>
                    <label style="font-size:11px;color:var(--text-muted);">T-Category</label>
                    <input id="stg-t" type="text" value="cT2" style="width:100%;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);" />
                </div>
                <div>
                    <label style="font-size:11px;color:var(--text-muted);">N-Category</label>
                    <input id="stg-n" type="text" value="cN0" style="width:100%;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);" />
                </div>
                <div>
                    <label style="font-size:11px;color:var(--text-muted);">M-Category</label>
                    <input id="stg-m" type="text" value="cM0" style="width:100%;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);" />
                </div>
            </div>
            <div>
                <label style="font-size:11px;color:var(--text-muted);text-transform:uppercase;">Clinical Staging Note / Basis</label>
                <textarea id="stg-reason" style="width:100%;height:70px;padding:10px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);font-size:12px;margin-top:4px;">CECT Chest + Abdomen confirms no distant metastasis. Primary lesion 2.8 cm, axilla clinically clear.</textarea>
            </div>
        </div>
        <div style="display:flex;justify-content:flex-end;gap:10px;">
            <button class="btn-cca btn-outline" onclick="closeModal()">Cancel</button>
            <button class="btn-cca btn-emerald" onclick="submitStagingConfirm()">✍️ Formally Sign-Off Stage</button>
        </div>
    `);
}

async function submitStagingConfirm() {
    try {
        const stageVal = document.getElementById('stg-stage-val').value;
        const tVal = document.getElementById('stg-t').value;
        const nVal = document.getElementById('stg-n').value;
        const mVal = document.getElementById('stg-m').value;
        const reason = document.getElementById('stg-reason').value;

        await Api.post(`${CCA_API_BASE}/patients/${currentPatientId}/staging/confirm`, {
            stage_value: stageVal,
            classification_prefix: 'c',
            t_stage: tVal,
            n_stage: nVal,
            m_stage: mVal,
            stage_group: 'Stage IIA',
            change_reason: reason
        });
        closeModal();
        await loadPatient(currentPatientId);
        await loadStagingWorkspace();
        await loadGuidelineContext();
        await loadJourneyTimeline();
    } catch (err) {
        console.error("Error confirming stage:", err);
        toast(apiErrorMessage(err), 'error');
    }
}

// Modal: 1-Click MDT Submission (WOW 6)
function openMDTSubmitModal() {
    openModal(`
        <div class="card-header">
            <div class="card-title">👥 1-Click Send Case Package to Tumor Board</div>
            <button onclick="closeModal()" style="background:transparent;border:none;color:var(--text-secondary);font-size:20px;cursor:pointer;">&times;</button>
        </div>
        <div style="display:flex;flex-direction:column;gap:12px;margin-bottom:20px;">
            <div>
                <label style="font-size:11px;color:var(--text-muted);text-transform:uppercase;">Select Tumor Board</label>
                <select id="mdt-board-select" style="width:100%;padding:10px;background:var(--bg-card);border:1px solid var(--border-bright);border-radius:6px;color:var(--text-primary);margin-top:4px;">
                    <option>Breast Oncology Multidisciplinary Tumor Board (Thursday Session)</option>
                    <option>Comprehensive Solid Tumor Board</option>
                </select>
            </div>
            <div>
                <label style="font-size:11px;color:var(--text-muted);text-transform:uppercase;">Clinical Discussion Question</label>
                <textarea id="mdt-question-input" style="width:100%;height:80px;padding:10px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);font-size:13px;margin-top:4px;">Review neoadjuvant Dose-dense AC-T chemotherapy vs upfront breast conserving surgery for 58F with cT2 cN0 cM0 (Stage IIA) HR+/HER2- invasive ductal carcinoma.</textarea>
            </div>
        </div>
        <div style="display:flex;justify-content:flex-end;gap:10px;">
            <button class="btn-cca btn-outline" onclick="closeModal()">Cancel</button>
            <button class="btn-cca btn-primary" onclick="submitMDTCase()">🚀 Submit Case Package</button>
        </div>
    `);
}

async function submitMDTCase() {
    try {
        const question = document.getElementById('mdt-question-input').value;
        const board = document.getElementById('mdt-board-select').value;

        await Api.post(`${CCA_API_BASE}/mdt/cases`, {
            patient_id: currentPatientId,
            question: question,
            tumor_board: board
        });
        closeModal();
        switchTab('mdt');
        await loadJourneyTimeline();
    } catch (err) {
        console.error("Error submitting MDT case:", err);
        toast(apiErrorMessage(err), 'error');
    }
}

// 13. Demo Controller Scenarios (SCR-27 / HUD)
// Demo/reset|simulate-result|advance-clock are Admin-only on the backend (see routers/cca.py) --
// a non-admin gets a clear toast instead of a silently-swallowed failure.
async function triggerSimulateResult() {
    try {
        await Api.post(`${CCA_API_BASE}/demo/simulate-result?patient_id=${currentPatientId}`);
        await loadPatient(currentPatientId);
        await loadStagingWorkspace();
        await loadJourneyTimeline();
        switchTab('staging');
    } catch (err) {
        console.error("Error simulating CT result:", err);
        toast(apiErrorMessage(err), 'error');
    }
}

async function triggerFastForward(targetDay) {
    try {
        await Api.post(`${CCA_API_BASE}/demo/advance-clock`, { target_day: targetDay, patient_id: currentPatientId });
        await loadPatient(currentPatientId);
        await loadJourneyTimeline();
        if (targetDay === 'D+7') switchTab('treatment_day');
        if (targetDay === 'D+21') switchTab('response');
    } catch (err) {
        console.error("Error fast forwarding clock:", err);
        toast(apiErrorMessage(err), 'error');
    }
}

async function resetDemo() {
    try {
        await Api.post(`${CCA_API_BASE}/demo/reset`);
        await loadPatient(1);
        await loadCensusQueue();
        await loadVerificationWorkspace();
        await loadStagingWorkspace();
        await loadGuidelineContext();
        await loadNexusBrief();
        await loadJourneyTimeline();
        switchTab('command_centre');
    } catch (err) {
        console.error("Error resetting demo:", err);
        toast(apiErrorMessage(err), 'error');
    }
}

async function recordMDTConsensus() {
    try {
        await Api.post(`${CCA_API_BASE}/mdt/cases/1/recommendation`, {
            recommendation: "Upfront Neoadjuvant Chemotherapy with Dose-dense AC-T followed by Breast Conserving Surgery + SLNB."
        });
        await loadJourneyTimeline();
        switchTab('careplan');
    } catch (err) {
        console.error("Error recording MDT consensus:", err);
        toast(apiErrorMessage(err), 'error');
    }
}

async function recordResponse() {
    try {
        await Api.post(`${CCA_API_BASE}/response-assessments`, {
            patient_id: currentPatientId,
            response_category: 'PR'
        });
        await loadJourneyTimeline();
        alert("RECIST 1.1 Partial Response (PR) confirmed: -57.1% tumor diameter reduction.");
    } catch (err) {
        console.error("Error recording response:", err);
        toast(apiErrorMessage(err), 'error');
    }
}

// Requirement #43: Anonymous Patient Presentation Toggle for MDT Board
function toggleMDTAnonymization(isAnon) {
    const nameElem = document.getElementById('hdr-name');
    const mrnElem = document.getElementById('hdr-mrn');
    const avatarElem = document.getElementById('hdr-avatar');
    
    if (isAnon) {
        if (nameElem) nameElem.dataset.realName = nameElem.textContent;
        if (mrnElem) mrnElem.dataset.realMrn = mrnElem.textContent;
        if (nameElem) nameElem.textContent = '[ANONYMOUS PATIENT #4417]';
        if (mrnElem) mrnElem.textContent = 'CCA-HIDDEN-***';
        if (avatarElem) avatarElem.src = 'https://images.unsplash.com/photo-1535713875002-d1d0cf377fde?w=150&auto=format&fit=crop&q=80';
    } else {
        if (nameElem && nameElem.dataset.realName) nameElem.textContent = nameElem.dataset.realName;
        if (mrnElem && mrnElem.dataset.realMrn) mrnElem.textContent = mrnElem.dataset.realMrn;
        if (avatarElem) avatarElem.src = 'https://images.unsplash.com/photo-1544005313-94ddf0286df2?w=150&auto=format&fit=crop&q=80';
    }
}

// Requirement #42: External Consultant Signed Access Link Generator
function copyMDTExternalLink() {
    const expUrl = `${window.location.origin}/cca_os.html?token=exp_mdt_${Date.now()}&role=external_consultant`;
    navigator.clipboard.writeText(expUrl);
    alert(`External Consultant Link Copied!\nExpiring Signed URL:\n${expUrl}\n\nCan be shared with external tumor board specialists (no login required).`);
}

// ---------------------------------------------------------------------------
// 14. Imaging Worklist (Radiologist / Radiology Coordinator)
// ---------------------------------------------------------------------------

async function loadImagingWorklist() {
    try {
        const data = await Api.get(`${CCA_API_BASE}/imaging/worklist`);
        const tbody = document.getElementById('imaging-worklist-body');
        tbody.innerHTML = data.worklist.map(o => `
            <tr>
                <td>${escapeHtml(o.patient_name)}</td>
                <td>${escapeHtml(o.patient_mrn)}</td>
                <td>${escapeHtml(o.item_name)}</td>
                <td>${escapeHtml(o.priority)}</td>
                <td><span class="badge-pill badge-stage">${escapeHtml(o.status)}</span></td>
                <td>${o.scheduled_at ? escapeHtml(new Date(o.scheduled_at).toLocaleString()) : '-'}</td>
                <td>${escapeHtml(o.preparation_status)}</td>
                <td><button class="btn-cca btn-outline" style="font-size:11px;padding:3px 8px;" onclick="openImagingOrderModal(${Number(o.id)})">Open</button></td>
            </tr>
        `).join('') || '<tr><td colspan="8">No imaging orders.</td></tr>';
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function openImagingOrderModal(orderId) {
    try {
        const data = await Api.get(`${CCA_API_BASE}/imaging/orders/${orderId}`);
        const o = data.order;
        const draft = data.results.find(r => r.report_status === 'Draft');
        openModal(`
            <div class="card-header">
                <div class="card-title">🩻 ${escapeHtml(o.item_name)}</div>
                <button onclick="closeModal()" style="background:transparent;border:none;color:var(--text-secondary);font-size:20px;cursor:pointer;">&times;</button>
            </div>
            <p style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;">Indication: ${escapeHtml(o.clinical_indication)}${data.prior_study_available ? ' &middot; Prior study available' : ''}</p>
            <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:16px;">
                <label style="font-size:11px;color:var(--text-muted);">Scheduled at (ISO)</label>
                <input id="img-sched-at" type="datetime-local" value="${o.scheduled_at ? o.scheduled_at.slice(0,16) : ''}" style="padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);" />
                <label style="font-size:11px;color:var(--text-muted);">Location</label>
                <input id="img-location" type="text" value="${escapeHtml(o.location || '')}" style="padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);" />
                <button class="btn-cca btn-outline" onclick="submitImagingSchedule(${orderId})">📅 Schedule</button>
                <select id="img-prep-status" style="padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);">
                    ${['NotRequired','Pending','Completed','NeedsReview'].map(s => `<option ${o.preparation_status === s ? 'selected' : ''}>${s}</option>`).join('')}
                </select>
                <button class="btn-cca btn-outline" onclick="submitImagingPreparation(${orderId})">✅ Update Preparation</button>
            </div>
            <div style="border-top:1px solid var(--border-subtle);padding-top:12px;">
                <label style="font-size:11px;color:var(--text-muted);">Findings</label>
                <textarea id="img-findings" style="width:100%;height:60px;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-top:4px;">${escapeHtml(draft ? draft.findings_text || '' : '')}</textarea>
                <label style="font-size:11px;color:var(--text-muted);">Impression</label>
                <textarea id="img-impression" style="width:100%;height:40px;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-top:4px;">${escapeHtml(draft ? draft.impression || '' : '')}</textarea>
                <div style="display:flex;gap:8px;margin-top:10px;">
                    <button class="btn-cca btn-outline" onclick="submitImagingReport(${orderId})">💾 Save Draft</button>
                    ${draft ? `<button class="btn-cca btn-emerald" onclick="submitImagingFinalize(${draft.id})">✍️ Finalize Report</button>` : ''}
                </div>
            </div>
        `);
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function submitImagingSchedule(orderId) {
    try {
        const scheduledAt = document.getElementById('img-sched-at').value;
        if (!scheduledAt) { toast('Scheduled date/time required', 'error'); return; }
        await Api.post(`${CCA_API_BASE}/imaging/orders/${orderId}/schedule`, {
            scheduled_at: scheduledAt, location: document.getElementById('img-location').value,
        });
        toast('Imaging order scheduled', 'success');
        closeModal(); loadImagingWorklist();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function submitImagingPreparation(orderId) {
    try {
        await Api.patch(`${CCA_API_BASE}/imaging/orders/${orderId}/preparation`, {
            preparation_status: document.getElementById('img-prep-status').value,
        });
        toast('Preparation status updated', 'success');
        closeModal(); loadImagingWorklist();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function submitImagingReport(orderId) {
    try {
        await Api.post(`${CCA_API_BASE}/imaging/orders/${orderId}/report`, {
            findings_text: document.getElementById('img-findings').value,
            impression: document.getElementById('img-impression').value,
        });
        toast('Report draft saved', 'success');
        closeModal(); loadImagingWorklist();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function submitImagingFinalize(resultId) {
    try {
        await Api.post(`${CCA_API_BASE}/imaging/results/${resultId}/finalize`);
        toast('Report finalized', 'success');
        closeModal(); loadImagingWorklist();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

// ---------------------------------------------------------------------------
// 15. Pathology Worklist (Pathologist)
// ---------------------------------------------------------------------------

async function loadPathologyWorklist() {
    try {
        const data = await Api.get(`${CCA_API_BASE}/pathology/worklist`);
        const tbody = document.getElementById('pathology-worklist-body');
        tbody.innerHTML = data.worklist.map(o => `
            <tr>
                <td>${escapeHtml(o.patient_name)}</td>
                <td>${escapeHtml(o.patient_mrn)}</td>
                <td>${escapeHtml(o.item_name)}</td>
                <td>${escapeHtml(o.priority)}</td>
                <td><span class="badge-pill badge-stage">${escapeHtml(o.status)}</span></td>
                <td><button class="btn-cca btn-outline" style="font-size:11px;padding:3px 8px;" onclick="openPathologyOrderModal(${Number(o.id)})">Open</button></td>
            </tr>
        `).join('') || '<tr><td colspan="6">No pathology orders.</td></tr>';
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function openPathologyOrderModal(orderId) {
    try {
        const data = await Api.get(`${CCA_API_BASE}/pathology/orders/${orderId}`);
        const o = data.order;
        const draft = data.results.find(r => r.report_status === 'Draft');
        openModal(`
            <div class="card-header">
                <div class="card-title">🔬 ${escapeHtml(o.item_name)}</div>
                <button onclick="closeModal()" style="background:transparent;border:none;color:var(--text-secondary);font-size:20px;cursor:pointer;">&times;</button>
            </div>
            <p style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;">Indication: ${escapeHtml(o.clinical_indication)}</p>
            <label style="font-size:11px;color:var(--text-muted);">Findings / Diagnosis</label>
            <textarea id="path-findings" style="width:100%;height:80px;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-top:4px;">${escapeHtml(draft ? draft.findings_text || '' : '')}</textarea>
            <div style="display:flex;gap:8px;margin-top:10px;">
                <button class="btn-cca btn-outline" onclick="submitPathologyReport(${orderId})">💾 Save Draft</button>
                ${draft ? `<button class="btn-cca btn-emerald" onclick="submitPathologyFinalize(${draft.id})">✍️ Finalize Report</button>` : ''}
            </div>
        `);
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function submitPathologyReport(orderId) {
    try {
        await Api.post(`${CCA_API_BASE}/pathology/orders/${orderId}/report`, {
            findings_text: document.getElementById('path-findings').value,
        });
        toast('Report draft saved', 'success');
        closeModal(); loadPathologyWorklist();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function submitPathologyFinalize(resultId) {
    try {
        await Api.post(`${CCA_API_BASE}/pathology/results/${resultId}/finalize`);
        toast('Report finalized', 'success');
        closeModal(); loadPathologyWorklist();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

// ---------------------------------------------------------------------------
// 16. Molecular Diagnostics
// ---------------------------------------------------------------------------

async function loadMolecularTests() {
    try {
        const data = await Api.get(`${CCA_API_BASE}/molecular/tests?patient_id=${currentPatientId}`);
        const tbody = document.getElementById('molecular-tests-body');
        tbody.innerHTML = data.tests.map(t => `
            <tr>
                <td>${escapeHtml(t.marker_name)}</td>
                <td>${escapeHtml(t.method || '-')}</td>
                <td>${escapeHtml(t.result_as_reported)}</td>
                <td><span class="badge-pill badge-stage">${escapeHtml(t.status)}</span></td>
                <td>${escapeHtml(t.confirmatory_required || '-')}</td>
                <td>${t.status === 'PENDING' ? `<button class="btn-cca btn-outline" style="font-size:11px;padding:3px 8px;" onclick="openRecordMolecularResultModal(${Number(t.id)})">Record Result</button>` : ''}</td>
            </tr>
        `).join('') || '<tr><td colspan="6">No molecular tests ordered.</td></tr>';
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

function openOrderMolecularTestModal() {
    openModal(`
        <div class="card-header">
            <div class="card-title">+ Order Molecular Test</div>
            <button onclick="closeModal()" style="background:transparent;border:none;color:var(--text-secondary);font-size:20px;cursor:pointer;">&times;</button>
        </div>
        <label style="font-size:11px;color:var(--text-muted);">Marker</label>
        <input id="mol-marker" type="text" placeholder="e.g. PD-L1" style="width:100%;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-top:4px;margin-bottom:12px;" />
        <button class="btn-cca btn-primary" onclick="submitOrderMolecularTest()">Order Test</button>
    `);
}

async function submitOrderMolecularTest() {
    try {
        const marker = document.getElementById('mol-marker').value;
        if (!marker) { toast('Marker name required', 'error'); return; }
        await Api.post(`${CCA_API_BASE}/molecular/tests`, { patient_id: currentPatientId, marker_name: marker });
        toast('Molecular test ordered', 'success');
        closeModal(); loadMolecularTests();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

function openRecordMolecularResultModal(testId) {
    openModal(`
        <div class="card-header">
            <div class="card-title">Record Molecular Result</div>
            <button onclick="closeModal()" style="background:transparent;border:none;color:var(--text-secondary);font-size:20px;cursor:pointer;">&times;</button>
        </div>
        <label style="font-size:11px;color:var(--text-muted);">Result</label>
        <input id="mol-result" type="text" style="width:100%;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-top:4px;margin-bottom:12px;" />
        <label style="font-size:11px;color:var(--text-muted);">Confirmatory testing required?</label>
        <select id="mol-confirmatory" style="width:100%;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-top:4px;margin-bottom:12px;">
            <option value="no">No</option><option value="yes">Yes</option><option value="pending">Pending</option>
        </select>
        <button class="btn-cca btn-primary" onclick="submitMolecularResult(${testId})">Save Result</button>
    `);
}

async function submitMolecularResult(testId) {
    try {
        const result = document.getElementById('mol-result').value;
        if (!result) { toast('Result required', 'error'); return; }
        await Api.patch(`${CCA_API_BASE}/molecular/tests/${testId}`, {
            result_as_reported: result, confirmatory_required: document.getElementById('mol-confirmatory').value,
        });
        toast('Result recorded', 'success');
        closeModal(); loadMolecularTests();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

// ---------------------------------------------------------------------------
// 17. Lab / Phlebotomy Worklist
// ---------------------------------------------------------------------------

async function loadLabWorklist() {
    try {
        const data = await Api.get(`${CCA_API_BASE}/lab/worklist`);
        const tbody = document.getElementById('lab-worklist-body');
        tbody.innerHTML = data.worklist.map(o => `
            <tr>
                <td>${escapeHtml(o.patient_name)}</td>
                <td>${escapeHtml(o.patient_mrn)}</td>
                <td>${escapeHtml(o.item_name)}</td>
                <td>${escapeHtml(o.priority)}</td>
                <td><span class="badge-pill badge-stage">${escapeHtml(o.status)}</span></td>
                <td>${o.collected_at ? '✅' : '-'}</td>
                <td>
                    ${!o.collected_at ? `<button class="btn-cca btn-outline" style="font-size:11px;padding:3px 8px;" onclick="submitLabCollect(${Number(o.id)})">Collect</button>
                    <button class="btn-cca btn-outline" style="font-size:11px;padding:3px 8px;color:#f87171;" onclick="openLabRejectModal(${Number(o.id)})">Reject</button>` :
                    (o.status !== 'RESULTED' ? `<button class="btn-cca btn-outline" style="font-size:11px;padding:3px 8px;" onclick="openLabResultModal(${Number(o.id)})">Enter Result</button>` : '')}
                </td>
            </tr>
        `).join('') || '<tr><td colspan="7">No lab orders.</td></tr>';
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function submitLabCollect(orderId) {
    try {
        await Api.post(`${CCA_API_BASE}/lab/orders/${orderId}/collect`, {});
        toast('Specimen collected', 'success');
        loadLabWorklist();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

function openLabRejectModal(orderId) {
    openModal(`
        <div class="card-header">
            <div class="card-title">Reject Specimen</div>
            <button onclick="closeModal()" style="background:transparent;border:none;color:var(--text-secondary);font-size:20px;cursor:pointer;">&times;</button>
        </div>
        <label style="font-size:11px;color:var(--text-muted);">Reason</label>
        <input id="lab-reject-reason" type="text" placeholder="e.g. Hemolysed sample" style="width:100%;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-top:4px;margin-bottom:12px;" />
        <button class="btn-cca btn-primary" onclick="submitLabReject(${orderId})">Mark Recollection Required</button>
    `);
}

async function submitLabReject(orderId) {
    try {
        const reason = document.getElementById('lab-reject-reason').value;
        if (!reason) { toast('Reason required', 'error'); return; }
        await Api.post(`${CCA_API_BASE}/lab/orders/${orderId}/reject`, { reason });
        toast('Specimen marked for recollection', 'success');
        closeModal(); loadLabWorklist();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

function openLabResultModal(orderId) {
    openModal(`
        <div class="card-header">
            <div class="card-title">Enter Lab Result</div>
            <button onclick="closeModal()" style="background:transparent;border:none;color:var(--text-secondary);font-size:20px;cursor:pointer;">&times;</button>
        </div>
        <label style="font-size:11px;color:var(--text-muted);">Findings</label>
        <textarea id="lab-findings" style="width:100%;height:60px;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-top:4px;margin-bottom:12px;"></textarea>
        <label style="font-size:12px;color:var(--text-secondary);display:flex;align-items:center;gap:6px;margin-bottom:12px;">
            <input type="checkbox" id="lab-is-critical" /> Critical result
        </label>
        <button class="btn-cca btn-primary" onclick="submitLabResult(${orderId})">Save Result</button>
    `);
}

async function submitLabResult(orderId) {
    try {
        const findings = document.getElementById('lab-findings').value;
        if (!findings) { toast('Findings required', 'error'); return; }
        await Api.post(`${CCA_API_BASE}/lab/orders/${orderId}/result`, {
            findings_text: findings, is_critical: document.getElementById('lab-is-critical').checked,
        });
        toast('Result recorded', 'success');
        closeModal(); loadLabWorklist();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

// ---------------------------------------------------------------------------
// 18. MDT Coordinator
// ---------------------------------------------------------------------------

async function loadMdtCoordinatorQueue() {
    try {
        const data = await Api.get(`${CCA_API_BASE}/mdt/referral-queue`);
        const tbody = document.getElementById('mdt-coordinator-body');
        tbody.innerHTML = data.queue.map(c => `
            <tr>
                <td>${escapeHtml(c.patient_name)}</td>
                <td>${escapeHtml(c.patient_mrn)}</td>
                <td>${escapeHtml((c.question || '').slice(0, 60))}</td>
                <td><span class="badge-pill badge-stage">${escapeHtml(c.status)}</span></td>
                <td>${escapeHtml(c.board_date || 'Unscheduled')}</td>
                <td>${escapeHtml(c.readiness.overall)}</td>
                <td><button class="btn-cca btn-outline" style="font-size:11px;padding:3px 8px;" onclick="openMdtCoordinatorCaseModal(${Number(c.id)})">Manage</button></td>
            </tr>
        `).join('') || '<tr><td colspan="7">No MDT referrals.</td></tr>';
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function openMdtCoordinatorCaseModal(caseId) {
    try {
        const participants = await Api.get(`${CCA_API_BASE}/mdt/cases/${caseId}/participants`);
        openModal(`
            <div class="card-header">
                <div class="card-title">👥 Schedule MDT Case</div>
                <button onclick="closeModal()" style="background:transparent;border:none;color:var(--text-secondary);font-size:20px;cursor:pointer;">&times;</button>
            </div>
            <div class="grid-2" style="margin-bottom:12px;">
                <div><label style="font-size:11px;color:var(--text-muted);">Board Date</label><input id="mdtc-date" type="date" style="width:100%;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);" /></div>
                <div><label style="font-size:11px;color:var(--text-muted);">Start Time</label><input id="mdtc-time" type="text" placeholder="14:00" style="width:100%;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);" /></div>
            </div>
            <label style="font-size:11px;color:var(--text-muted);">Meeting Type</label>
            <select id="mdtc-type" style="width:100%;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-top:4px;margin-bottom:12px;">
                <option>InPerson</option><option>Virtual</option><option>Hybrid</option>
            </select>
            <button class="btn-cca btn-outline" onclick="submitMdtSchedule(${caseId})">📅 Save Schedule</button>
            <div style="border-top:1px solid var(--border-subtle);margin-top:16px;padding-top:12px;">
                <div class="card-title" style="font-size:13px;margin-bottom:8px;">Participants</div>
                <div style="display:flex;flex-direction:column;gap:6px;margin-bottom:10px;">
                    ${participants.participants.map(p => `<div style="font-size:12px;">${escapeHtml(p.specialist_name)} (${escapeHtml(p.specialist_role)}) — ${escapeHtml(p.invitation_status)}</div>`).join('') || '<div style="font-size:12px;color:var(--text-muted);">None added yet.</div>'}
                </div>
                <input id="mdtc-participant-name" type="text" placeholder="Specialist name" style="width:100%;padding:6px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-bottom:6px;" />
                <input id="mdtc-participant-role" type="text" placeholder="Role, e.g. Radiologist" style="width:100%;padding:6px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-bottom:8px;" />
                <button class="btn-cca btn-outline" onclick="submitAddParticipant(${caseId})">+ Add Participant</button>
            </div>
        `);
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function submitMdtSchedule(caseId) {
    try {
        const board_date = document.getElementById('mdtc-date').value;
        await Api.patch(`${CCA_API_BASE}/mdt/cases/${caseId}/schedule`, {
            board_date: board_date || undefined, start_time: document.getElementById('mdtc-time').value,
            meeting_type: document.getElementById('mdtc-type').value,
        });
        toast('MDT case scheduled', 'success');
        closeModal(); loadMdtCoordinatorQueue();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function submitAddParticipant(caseId) {
    try {
        const name = document.getElementById('mdtc-participant-name').value;
        const role = document.getElementById('mdtc-participant-role').value;
        if (!name || !role) { toast('Name and role required', 'error'); return; }
        await Api.post(`${CCA_API_BASE}/mdt/cases/${caseId}/participants`, { specialist_name: name, specialist_role: role });
        toast('Participant added', 'success');
        openMdtCoordinatorCaseModal(caseId);
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

// ---------------------------------------------------------------------------
// 19. External MDT Specialist: Assigned Cases
// ---------------------------------------------------------------------------

async function loadAssignedCases() {
    try {
        const data = await Api.get(`${CCA_API_BASE}/mdt/assigned-cases`);
        const container = document.getElementById('assigned-cases-container');
        if (!data.assigned_cases.length) {
            container.innerHTML = '<div class="workspace-card">No cases have been shared with your account yet.</div>';
            return;
        }
        container.innerHTML = data.assigned_cases.map(c => `
            <div class="workspace-card">
                <div class="card-header"><div class="card-title">${escapeHtml(c.question)}</div><span class="badge-pill badge-stage">${escapeHtml(c.status)}</span></div>
                <p style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;">Tumor Board: ${escapeHtml(c.tumor_board)}${c.board_date ? ` &middot; ${escapeHtml(c.board_date)}` : ''}</p>
                <button class="btn-cca btn-primary" onclick="openSubmitOpinionModal(${Number(c.id)})">✍️ Submit Opinion</button>
            </div>
        `).join('');
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

function openSubmitOpinionModal(caseId) {
    openModal(`
        <div class="card-header">
            <div class="card-title">Submit External Opinion</div>
            <button onclick="closeModal()" style="background:transparent;border:none;color:var(--text-secondary);font-size:20px;cursor:pointer;">&times;</button>
        </div>
        <label style="font-size:11px;color:var(--text-muted);">Recommendation</label>
        <textarea id="ext-recommendation" style="width:100%;height:60px;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-top:4px;margin-bottom:10px;"></textarea>
        <label style="font-size:11px;color:var(--text-muted);">Rationale</label>
        <textarea id="ext-rationale" style="width:100%;height:50px;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-top:4px;margin-bottom:10px;"></textarea>
        <label style="font-size:11px;color:var(--text-muted);">Certainty</label>
        <select id="ext-certainty" style="width:100%;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-top:4px;margin-bottom:12px;">
            <option>High</option><option>Moderate</option><option>Low</option>
        </select>
        <button class="btn-cca btn-primary" onclick="submitOpinion(${caseId})">Submit Opinion</button>
    `);
}

async function submitOpinion(caseId) {
    try {
        const recommendation = document.getElementById('ext-recommendation').value;
        if (!recommendation) { toast('Recommendation required', 'error'); return; }
        await Api.post(`${CCA_API_BASE}/mdt/cases/${caseId}/opinions`, {
            recommendation, rationale: document.getElementById('ext-rationale').value,
            certainty: document.getElementById('ext-certainty').value,
        });
        toast('Opinion submitted', 'success');
        closeModal(); loadAssignedCases();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

// ---------------------------------------------------------------------------
// 20. Financial Counselling & Estimates
// ---------------------------------------------------------------------------

async function loadFinancialQueue() {
    try {
        const data = await Api.get(`${CCA_API_BASE}/financial/queue`);
        const tbody = document.getElementById('financial-queue-body');
        tbody.innerHTML = data.queue.map(f => `
            <tr>
                <td>${escapeHtml(f.patient_name)}</td>
                <td>${escapeHtml(f.patient_mrn)}</td>
                <td>${escapeHtml(f.counselling_status)}</td>
                <td>${escapeHtml(f.estimate_status)}</td>
                <td>${escapeHtml(f.payer_route || '-')}</td>
                <td><span class="badge-pill badge-stage">${escapeHtml(f.financial_clearance_status)}</span></td>
                <td><button class="btn-cca btn-outline" style="font-size:11px;padding:3px 8px;" onclick="openFinancialCaseModal(${Number(f.id)})">Manage</button></td>
            </tr>
        `).join('') || '<tr><td colspan="7">No financial cases.</td></tr>';
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

function openCreateFinancialCaseModal() {
    openModal(`
        <div class="card-header">
            <div class="card-title">Refer Patient for Financial Counselling</div>
            <button onclick="closeModal()" style="background:transparent;border:none;color:var(--text-secondary);font-size:20px;cursor:pointer;">&times;</button>
        </div>
        <p style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;">Refers the currently loaded patient (${escapeHtml(currentPatientData ? currentPatientData.patient.name : '')}).</p>
        <button class="btn-cca btn-primary" onclick="submitCreateFinancialCase()">Create Case</button>
    `);
}

async function submitCreateFinancialCase() {
    try {
        await Api.post(`${CCA_API_BASE}/financial/cases`, { patient_id: currentPatientId });
        toast('Financial case created', 'success');
        closeModal(); loadFinancialQueue();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function openFinancialCaseModal(caseId) {
    try {
        const data = await Api.get(`${CCA_API_BASE}/financial/cases/${caseId}`);
        const f = data.case;
        openModal(`
            <div class="card-header">
                <div class="card-title">💳 Financial Case</div>
                <button onclick="closeModal()" style="background:transparent;border:none;color:var(--text-secondary);font-size:20px;cursor:pointer;">&times;</button>
            </div>
            <label style="font-size:11px;color:var(--text-muted);">Counselling Notes</label>
            <textarea id="fin-notes" style="width:100%;height:50px;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-top:4px;margin-bottom:8px;">${escapeHtml(f.counselling_notes || '')}</textarea>
            <select id="fin-status" style="width:100%;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-bottom:8px;">
                ${['Pending','InProgress','Completed','FollowUpRequired'].map(s => `<option ${f.counselling_status === s ? 'selected' : ''}>${s}</option>`).join('')}
            </select>
            <button class="btn-cca btn-outline" onclick="submitFinancialCounselling(${caseId})">💾 Save Counselling</button>
            <div style="border-top:1px solid var(--border-subtle);margin-top:14px;padding-top:12px;">
                <label style="font-size:11px;color:var(--text-muted);">Payer Route</label>
                <select id="fin-payer" style="width:100%;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-top:4px;margin-bottom:8px;">
                    ${['SelfPay','PrivateInsurance','CorporateTPA','GovernmentScheme','Assistance'].map(s => `<option ${f.payer_route === s ? 'selected' : ''}>${s}</option>`).join('')}
                </select>
                <button class="btn-cca btn-outline" onclick="submitFinancialInsurance(${caseId})">💾 Save Payer Route</button>
            </div>
            <div style="border-top:1px solid var(--border-subtle);margin-top:14px;padding-top:12px;">
                <label style="font-size:11px;color:var(--text-muted);">Financial Clearance</label>
                <select id="fin-clearance" style="width:100%;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-top:4px;margin-bottom:8px;">
                    ${['NotStarted','PendingDocuments','InsuranceApprovalPending','PatientContributionPending','PartiallyCleared','Cleared','NotCleared','Deferred'].map(s => `<option ${f.financial_clearance_status === s ? 'selected' : ''}>${s}</option>`).join('')}
                </select>
                <button class="btn-cca btn-emerald" onclick="submitFinancialClearance(${caseId})">✅ Save Clearance</button>
            </div>
        `);
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function submitFinancialCounselling(caseId) {
    try {
        await Api.patch(`${CCA_API_BASE}/financial/cases/${caseId}/counselling`, {
            counselling_status: document.getElementById('fin-status').value,
            counselling_notes: document.getElementById('fin-notes').value,
        });
        toast('Counselling updated', 'success');
        closeModal(); loadFinancialQueue();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function submitFinancialInsurance(caseId) {
    try {
        await Api.patch(`${CCA_API_BASE}/financial/cases/${caseId}/insurance`, { payer_route: document.getElementById('fin-payer').value });
        toast('Payer route updated', 'success');
        closeModal(); loadFinancialQueue();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function submitFinancialClearance(caseId) {
    try {
        await Api.patch(`${CCA_API_BASE}/financial/cases/${caseId}/clearance`, { financial_clearance_status: document.getElementById('fin-clearance').value });
        toast('Clearance status updated', 'success');
        closeModal(); loadFinancialQueue();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

// ---------------------------------------------------------------------------
// 21. Care Coordination (Patient Liaison)
// ---------------------------------------------------------------------------

async function loadCoordinationQueue() {
    try {
        const data = await Api.get(`${CCA_API_BASE}/coordination/queue`);
        const tbody = document.getElementById('coordination-queue-body');
        tbody.innerHTML = data.queue.map(c => `
            <tr>
                <td>${escapeHtml(c.patient_name)}</td>
                <td>${escapeHtml(c.patient_mrn)}</td>
                <td><span class="badge-pill badge-stage">${escapeHtml(c.communication_status)}</span></td>
                <td>${(c.barriers || []).length}</td>
                <td>${escapeHtml(c.next_action || '-')}</td>
                <td><button class="btn-cca btn-outline" style="font-size:11px;padding:3px 8px;" onclick="openCoordinationCaseModal(${Number(c.id)})">Manage</button></td>
            </tr>
        `).join('') || '<tr><td colspan="6">No coordination cases.</td></tr>';
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

function openCreateCoordinationCaseModal() {
    openModal(`
        <div class="card-header">
            <div class="card-title">Open Coordination Case</div>
            <button onclick="closeModal()" style="background:transparent;border:none;color:var(--text-secondary);font-size:20px;cursor:pointer;">&times;</button>
        </div>
        <p style="font-size:12px;color:var(--text-secondary);margin-bottom:12px;">Opens a coordination case for the currently loaded patient.</p>
        <button class="btn-cca btn-primary" onclick="submitCreateCoordinationCase()">Create Case</button>
    `);
}

async function submitCreateCoordinationCase() {
    try {
        await Api.post(`${CCA_API_BASE}/coordination/cases`, { patient_id: currentPatientId });
        toast('Coordination case created', 'success');
        closeModal(); loadCoordinationQueue();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function openCoordinationCaseModal(caseId) {
    try {
        const data = await Api.get(`${CCA_API_BASE}/coordination/cases/${caseId}`);
        const c = data.case, m = data.care_milestones;
        openModal(`
            <div class="card-header">
                <div class="card-title">🧭 Care Coordination</div>
                <button onclick="closeModal()" style="background:transparent;border:none;color:var(--text-secondary);font-size:20px;cursor:pointer;">&times;</button>
            </div>
            <div class="card-title" style="font-size:13px;margin-bottom:6px;">Care Milestones</div>
            <div style="font-size:12px;color:var(--text-secondary);margin-bottom:14px;display:flex;flex-direction:column;gap:2px;">
                <span>Nurse intake: ${m.nurse_intake_completed ? '✅' : '⏳'}</span>
                <span>Consultation: ${m.oncology_consultation_completed ? '✅' : '⏳'}</span>
                <span>Investigations: ${escapeHtml(m.investigations_status)}</span>
                <span>MDT: ${escapeHtml(m.mdt_status)}</span>
                <span>Treatment plan: ${m.treatment_plan_available ? '✅' : '⏳'}</span>
                <span>Financial counselling: ${escapeHtml(m.financial_counselling_status)}</span>
            </div>
            <select id="coord-contact-status" style="width:100%;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-bottom:8px;">
                ${['NotContacted','ContactAttempted','Reached','UnableToReach','CallbackRequired'].map(s => `<option ${c.communication_status === s ? 'selected' : ''}>${s}</option>`).join('')}
            </select>
            <button class="btn-cca btn-outline" onclick="submitCoordinationContact(${caseId})">💾 Save Contact Status</button>
            <div style="border-top:1px solid var(--border-subtle);margin-top:14px;padding-top:12px;">
                <div class="card-title" style="font-size:13px;margin-bottom:8px;">Barriers</div>
                <div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">${(c.barriers || []).map(b => `${escapeHtml(b.type)}: ${escapeHtml(b.notes || '')} (${escapeHtml(b.status)})`).join('<br>') || 'None recorded.'}</div>
                <input id="coord-barrier-type" type="text" placeholder="Barrier type, e.g. TransportationIssue" style="width:100%;padding:6px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-bottom:6px;" />
                <button class="btn-cca btn-outline" onclick="submitAddBarrier(${caseId})">+ Add Barrier</button>
            </div>
            <div style="border-top:1px solid var(--border-subtle);margin-top:14px;padding-top:12px;">
                <label style="font-size:11px;color:var(--text-muted);">Next Action</label>
                <input id="coord-next-action" type="text" value="${escapeHtml(c.next_action || '')}" style="width:100%;padding:8px;background:var(--bg-card);border:1px solid var(--border-subtle);border-radius:6px;color:var(--text-primary);margin-top:4px;margin-bottom:8px;" />
                <button class="btn-cca btn-emerald" onclick="submitCoordinationNextAction(${caseId})">💾 Save Next Action</button>
            </div>
        `);
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function submitCoordinationContact(caseId) {
    try {
        await Api.patch(`${CCA_API_BASE}/coordination/cases/${caseId}/contact`, { communication_status: document.getElementById('coord-contact-status').value });
        toast('Contact status updated', 'success');
        closeModal(); loadCoordinationQueue();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function submitAddBarrier(caseId) {
    try {
        const type = document.getElementById('coord-barrier-type').value;
        if (!type) { toast('Barrier type required', 'error'); return; }
        await Api.post(`${CCA_API_BASE}/coordination/cases/${caseId}/barriers`, { type });
        toast('Barrier recorded', 'success');
        openCoordinationCaseModal(caseId);
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

async function submitCoordinationNextAction(caseId) {
    try {
        await Api.patch(`${CCA_API_BASE}/coordination/cases/${caseId}/next-action`, {
            next_action: document.getElementById('coord-next-action').value, next_action_status: 'InProgress',
        });
        toast('Next action updated', 'success');
        closeModal(); loadCoordinationQueue();
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

// ---------------------------------------------------------------------------
// 22. Admin Operations Dashboard
// ---------------------------------------------------------------------------

async function loadOperationsDashboard() {
    try {
        const data = await Api.get(`${CCA_API_BASE}/admin/operations-dashboard`);
        const metrics = [
            ['Total Patients', data.patients_total], ['Nurse Intake Pending', data.nurse_intake_pending],
            ['MDT Cases Pending', data.mdt_cases_pending], ['Radiology Pending', data.radiology_pending],
            ['Pathology Pending', data.pathology_pending], ['Lab Pending', data.lab_pending],
            ['Financial Clearance Pending', data.financial_clearance_pending], ['Coordination Overdue', data.coordination_overdue_tasks],
        ];
        document.getElementById('admin-ops-metrics').innerHTML = metrics.map(([label, value]) => `
            <div class="workspace-card" style="margin-bottom:0;">
                <div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;">${escapeHtml(label)}</div>
                <div style="font-size:28px;font-weight:700;color:var(--brand-primary);margin-top:4px;">${Number(value)}</div>
            </div>
        `).join('');

        const audit = await Api.get(`${CCA_API_BASE}/admin/audit?limit=50`);
        document.getElementById('admin-audit-body').innerHTML = audit.events.map(e => `
            <tr>
                <td>${escapeHtml(e.timestamp ? new Date(e.timestamp).toLocaleString() : '-')}</td>
                <td>${escapeHtml(e.patient_name)}</td>
                <td>${escapeHtml(e.actor_name)} (${escapeHtml(e.actor_role || '')})</td>
                <td>${escapeHtml(e.event_category)}</td>
                <td>${escapeHtml(e.event_title)}</td>
            </tr>
        `).join('') || '<tr><td colspan="5">No audit events.</td></tr>';
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

