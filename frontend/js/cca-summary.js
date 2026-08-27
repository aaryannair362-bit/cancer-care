/*
 * Shared "Patient History & Documents" summary panel for the CCA Oncology OS.
 *
 * Renders GET /api/cca/patients/{id}/case-summary -- every document uploaded for a patient
 * (front desk or otherwise), the clinical facts extracted/verified from them, the visit-by-visit
 * encounter history, orders/results on record, and the journey timeline. Used identically by the
 * Medical/Surgical/Radiation Oncologist pages and the Financial Counsellor page so there is one
 * implementation of this view instead of one per (already copy-pasted) page.
 *
 * Usage: <script src="/static/js/api.js"></script> then <script src="/static/js/cca-summary.js"></script>,
 * then call `renderCaseSummaryPanel('some-container-id', patientId)`.
 * Depends on globals from api.js: Api, escapeHtml, fmtDateTime, apiErrorMessage, toast.
 */

const CCA_SUMMARY_FACT_LABELS = {
    PRIMARY_SITE: 'Primary site', LATERALITY: 'Laterality', HISTOLOGY: 'Histology / diagnosis',
    GRADE: 'Grade', T_EVIDENCE: 'T stage evidence', N_EVIDENCE: 'N stage evidence',
    M_EVIDENCE: 'M stage evidence', BIOMARKER_RESULT: 'Biomarker result', LAB_RESULT: 'Lab result',
    IMAGING_FINDING: 'Imaging finding', ECOG: 'ECOG', COMORBIDITY: 'Comorbidity',
    MEDICATION: 'Medication', ALLERGY: 'Allergy',
};

function _ccaSummaryEnsureStyles() {
    if (document.getElementById('cca-summary-styles')) return;
    const style = document.createElement('style');
    style.id = 'cca-summary-styles';
    style.textContent = `
        .cca-sum-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px,1fr)); gap:10px; margin-bottom:16px; }
        .cca-sum-tile { background:var(--bg-card-subtle); border:1px solid var(--line); border-radius:var(--radius-md); padding:12px 14px; }
        .cca-sum-tile-label { font-size:11px; color:var(--ink-500); text-transform:uppercase; margin-bottom:4px; }
        .cca-sum-tile-value { font-size:18px; font-weight:700; color:var(--ink-900); }
        .cca-sum-row { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; padding:10px 0; border-bottom:1px solid var(--line-subtle); }
        .cca-sum-row:last-child { border-bottom:none; }
        .cca-sum-row-main { min-width:0; flex:1; }
        .cca-sum-row-title { font-size:13.5px; font-weight:600; color:var(--ink-900); }
        .cca-sum-row-sub { font-size:12px; color:var(--ink-500); margin-top:2px; }
        .cca-sum-fact-group { margin-bottom:12px; }
        .cca-sum-fact-group-title { font-size:12px; font-weight:700; color:var(--ink-700); text-transform:uppercase; letter-spacing:0.4px; margin-bottom:6px; }
        .cca-sum-chip-row { display:flex; flex-wrap:wrap; gap:6px; }
        .cca-sum-chip { display:inline-flex; align-items:center; gap:5px; background:#F8FAFC; border:1px solid var(--line); border-radius:999px; padding:4px 10px; font-size:12px; color:var(--ink-800); }
        .cca-sum-chip.is-proposed { border-style:dashed; color:var(--ink-600); }
        .cca-sum-empty { font-size:13px; color:var(--ink-500); padding:8px 0; }
        .cca-sum-timeline-item { border-left:2px solid var(--line); padding:2px 0 12px 14px; margin-left:4px; position:relative; }
        .cca-sum-timeline-item::before { content:''; position:absolute; left:-5px; top:4px; width:8px; height:8px; border-radius:50%; background:var(--brand-primary); }
        .cca-sum-timeline-when { font-size:11px; color:var(--ink-500); }
        .cca-sum-timeline-title { font-size:13px; font-weight:600; color:var(--ink-900); margin-top:2px; }
        .cca-sum-timeline-desc { font-size:12.5px; color:var(--ink-600); margin-top:2px; }
    `;
    document.head.appendChild(style);
}

function _ccaSummaryBadge(status) {
    const cls = status === 'VERIFIED' || status === 'Completed' || status === 'RESULTED' ? 'badge-green'
        : status === 'OCR_FAILED' ? 'badge-rose' : 'badge-amber';
    return `<span class="badge ${cls}">${escapeHtml(status || 'Unknown')}</span>`;
}

/**
 * Explains, in plain language, why a document contributed nothing to the diagnoses/medications
 * section below -- so "the summary shows nothing for this file" reads as an understood state,
 * not a silent failure. Checked in priority order: a document can't be OCR_FAILED and also have
 * a classification, and there's no point noting "0 facts" separately from "couldn't classify"
 * when they have the same root cause (unreadable text).
 */
function _ccaSummaryDocCondition(d) {
    if (d.status === 'OCR_FAILED') {
        return '⚠ OCR could not read this file — re-scan or upload a clearer, text-based copy.';
    }
    if (d.classification === 'UNCLASSIFIED') {
        return '⚠ Could not identify what kind of document this is — the extracted text was unclear or in an unsupported language. Review the original manually.';
    }
    if (!d.fact_count) {
        return 'No diagnoses, medications, or findings extracted from this document yet.';
    }
    return null;
}

async function _ccaSummaryOpenDocument(patientId, documentId, filename) {
    try {
        const blob = await Api.blob(`/cca/patients/${patientId}/documents/${documentId}/file`);
        const url = URL.createObjectURL(blob);
        window.open(url, '_blank');
        setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (err) {
        toast(`Could not open ${filename}: ${apiErrorMessage(err)}`, 'error');
    }
}

/**
 * Fetch and render the case-summary panel for `patientId` into #`containerId`.
 * Safe to call repeatedly (e.g. every time a tab is opened) -- it always re-fetches fresh data.
 */
async function renderCaseSummaryPanel(containerId, patientId) {
    _ccaSummaryEnsureStyles();
    const container = document.getElementById(containerId);
    if (!container) return;

    if (!patientId) {
        container.innerHTML = `<div class="section-card"><p class="cca-sum-empty">Select a patient first.</p></div>`;
        return;
    }

    container.innerHTML = `<div class="section-card"><p class="cca-sum-empty">Loading patient history…</p></div>`;

    let summary;
    try {
        summary = await Api.get(`/cca/patients/${patientId}/case-summary`);
    } catch (err) {
        container.innerHTML = `<div class="section-card"><p style="color:var(--rose-badge-text); font-size:13px;">${escapeHtml(apiErrorMessage(err))}</p></div>`;
        return;
    }

    const p = summary.patient;
    const ov = summary.overview;

    const header = `
        <div class="section-card">
            <div class="section-head">
                <div>
                    <div class="section-title">${escapeHtml(p.name)} <span style="font-weight:400; color:var(--ink-500);">· ${escapeHtml(p.mrn)}</span></div>
                    <div class="section-sub">${escapeHtml(String(p.age ?? '—'))}y · ${escapeHtml(p.sex || '—')} · ${escapeHtml(p.journey_state || '—')}</div>
                </div>
                ${ov.is_returning_patient ? '<span class="badge badge-blue">Returning patient — prior history below</span>' : '<span class="badge badge-amber">No prior visits on record</span>'}
            </div>
            <div class="cca-sum-grid">
                <div class="cca-sum-tile"><div class="cca-sum-tile-label">Documents on file</div><div class="cca-sum-tile-value">${ov.document_count}</div></div>
                <div class="cca-sum-tile"><div class="cca-sum-tile-label">Verified facts</div><div class="cca-sum-tile-value">${ov.verified_fact_count}</div></div>
                <div class="cca-sum-tile"><div class="cca-sum-tile-label">Encounters</div><div class="cca-sum-tile-value">${ov.encounter_count}</div></div>
                <div class="cca-sum-tile"><div class="cca-sum-tile-label">Last visit</div><div class="cca-sum-tile-value" style="font-size:13px;">${ov.last_visit ? fmtDateTime(ov.last_visit) : '—'}</div></div>
            </div>
            ${p.id_proof_number ? `<div class="cca-sum-row-sub">Identity on file: ${escapeHtml(p.id_proof_type || 'ID')} ${escapeHtml(p.id_proof_number)} · ${escapeHtml(p.id_proof_verification_status || 'Pending')}</div>` : ''}
        </div>`;

    const documentsSection = `
        <div class="section-card">
            <div class="section-title" style="margin-bottom:12px;">Ingested Documents</div>
            ${summary.documents.length ? summary.documents.map(d => `
                <div class="cca-sum-row">
                    <div class="cca-sum-row-main">
                        <div class="cca-sum-row-title">${escapeHtml(d.filename)}</div>
                        <div class="cca-sum-row-sub">${escapeHtml(d.classification === 'UNCLASSIFIED' ? 'Not automatically classified' : (d.classification || 'Unclassified'))} · uploaded ${d.uploaded_at ? fmtDateTime(d.uploaded_at) : '—'}${d.uploaded_by ? ' · ' + escapeHtml(d.uploaded_by) : ''}</div>
                        ${d.excerpt ? `<div class="cca-sum-row-sub" style="margin-top:4px;">${escapeHtml(d.excerpt)}…</div>` : ''}
                        ${(() => { const note = _ccaSummaryDocCondition(d); return note ? `<div class="cca-sum-row-sub" style="margin-top:4px; color:var(--amber-badge-text);">${escapeHtml(note)}</div>` : ''; })()}
                    </div>
                    <div style="display:flex; flex-direction:column; align-items:flex-end; gap:6px;">
                        ${_ccaSummaryBadge(d.status)}
                        ${d.file_url ? `<button type="button" class="btn-outline" style="padding:4px 10px; font-size:11px;" onclick="_ccaSummaryOpenDocument(${Number(patientId)}, ${Number(d.id)}, '${escapeHtml(d.filename).replace(/'/g, "\\'")}')">📄 View</button>` : ''}
                    </div>
                </div>
            `).join('') : '<p class="cca-sum-empty">No documents uploaded yet for this patient.</p>'}
        </div>`;

    const factEntries = Object.entries(summary.clinical_facts || {});
    const factsSection = `
        <div class="section-card">
            <div class="section-title" style="margin-bottom:12px;">Diagnoses, Medications &amp; Extracted Findings</div>
            ${factEntries.length ? factEntries.map(([type, facts]) => `
                <div class="cca-sum-fact-group">
                    <div class="cca-sum-fact-group-title">${escapeHtml(CCA_SUMMARY_FACT_LABELS[type] || type)}</div>
                    <div class="cca-sum-chip-row">
                        ${facts.map(f => `<span class="cca-sum-chip${f.status !== 'VERIFIED' ? ' is-proposed' : ''}" title="${f.status === 'VERIFIED' ? 'Clinician-verified' : 'AI-drafted — not yet verified'}">${escapeHtml(f.value)}</span>`).join('')}
                    </div>
                </div>
            `).join('') : '<p class="cca-sum-empty">No diagnoses, medications, or findings extracted yet.</p>'}
        </div>`;

    const encountersSection = `
        <div class="section-card">
            <div class="section-title" style="margin-bottom:12px;">Consultation / Encounter History</div>
            ${summary.encounters.length ? summary.encounters.map(e => `
                <div class="cca-sum-timeline-item">
                    <div class="cca-sum-timeline-when">${e.started_at ? fmtDateTime(e.started_at) : '—'} · ${escapeHtml(e.specialty || '')}${e.clinician ? ' · ' + escapeHtml(e.clinician) : ''}</div>
                    <div class="cca-sum-timeline-title">${escapeHtml(e.diagnosis || e.chief_complaint || 'No diagnosis recorded')}</div>
                    ${e.advice ? `<div class="cca-sum-timeline-desc">Plan: ${escapeHtml(e.advice)}</div>` : ''}
                    ${e.medications && e.medications.length ? `<div class="cca-sum-timeline-desc">Medications noted: ${e.medications.map(m => escapeHtml(m.drugName || m.name || JSON.stringify(m))).join(', ')}</div>` : ''}
                </div>
            `).join('') : '<p class="cca-sum-empty">No prior consultations on record.</p>'}
        </div>`;

    const journeySection = `
        <div class="section-card">
            <div class="section-title" style="margin-bottom:12px;">Journey Timeline</div>
            ${summary.journey.length ? summary.journey.map(ev => `
                <div class="cca-sum-timeline-item">
                    <div class="cca-sum-timeline-when">${ev.timestamp ? fmtDateTime(ev.timestamp) : '—'}${ev.actor ? ' · ' + escapeHtml(ev.actor) : ''}</div>
                    <div class="cca-sum-timeline-title">${escapeHtml(ev.title)}</div>
                    ${ev.description ? `<div class="cca-sum-timeline-desc">${escapeHtml(ev.description)}</div>` : ''}
                </div>
            `).join('') : '<p class="cca-sum-empty">No journey events recorded yet.</p>'}
        </div>`;

    container.innerHTML = header + documentsSection + factsSection + encountersSection + journeySection +
        `<p style="font-size:11.5px; color:var(--ink-500); margin-top:-8px;">${escapeHtml(summary.disclaimer)}</p>`;
}

/*
 * Patient Summary -- the compact, doctor-facing "what's the current clinical strategy and
 * what's next" view (architecture doc section 14). Two SEPARATE cards, never collapsed into
 * one paragraph: "Active Treatment Plan" (pulled from the signed Treatment Plan) and
 * "Care Plan / Next Steps" (pulled from the Care Plan's open tasks). Meant to be embedded
 * where a doctor already is -- the Consultation view's patient banner -- rather than becoming
 * its own disconnected nav item (architecture doc: "Patient Summary opens from
 * Patients/Consultation rather than being a disconnected duplicate module").
 *
 * Usage: same setup as renderCaseSummaryPanel; call `renderPatientSummaryCards('container-id', patientId)`.
 */
const TP_SUMMARY_BADGE = { DRAFT: 'badge-amber', PROPOSED: 'badge-amber', ACTIVE: 'badge-green', ON_HOLD: 'badge-amber', COMPLETED: 'badge-blue', SUPERSEDED: 'badge-blue', CANCELLED: 'badge-rose' };
const CP_SUMMARY_BADGE = { ACTIVE: 'badge-green', BLOCKED: 'badge-rose', ON_HOLD: 'badge-amber', COMPLETED: 'badge-blue', CANCELLED: 'badge-rose' };
const TASK_SUMMARY_BADGE = { OPEN: 'badge-amber', ACKNOWLEDGED: 'badge-blue', BLOCKED: 'badge-rose', ESCALATED: 'badge-rose', RESOLVED: 'badge-green' };

async function renderPatientSummaryCards(containerId, patientId) {
    _ccaSummaryEnsureStyles();
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!patientId) { container.innerHTML = ''; return; }

    container.innerHTML = `<div class="section-card"><p class="cca-sum-empty">Loading patient summary…</p></div>`;

    let plans = [], carePlan = null, tasks = [];
    try {
        const [planData, careData, taskData] = await Promise.all([
            Api.get(`/cca/patients/${patientId}/treatment-plans`).catch(() => ({ treatment_plans: [] })),
            Api.get(`/cca/care-plans/current?patient_id=${patientId}`).catch(() => ({ care_plan: null })),
            Api.get(`/cca/patients/${patientId}/tasks`).catch(() => ({ tasks: [] })),
        ]);
        plans = planData.treatment_plans || [];
        carePlan = careData.care_plan || null;
        tasks = taskData.tasks || [];
    } catch (err) {
        container.innerHTML = `<div class="section-card"><p style="color:var(--rose-badge-text); font-size:13px;">${escapeHtml(apiErrorMessage(err))}</p></div>`;
        return;
    }

    const activePlan = plans.find(p => p.status === 'ACTIVE') || plans.find(p => p.status === 'ON_HOLD') || null;

    const treatmentCard = `
        <div class="section-card" style="margin-bottom:0;">
            <div class="section-head">
                <div class="section-title" style="font-size:14px;">Active Treatment Plan</div>
                ${activePlan ? `<span class="badge ${TP_SUMMARY_BADGE[activePlan.status] || 'badge-blue'}">${escapeHtml(activePlan.status)}</span>` : ''}
            </div>
            ${activePlan ? `
                <div class="cca-sum-row-title">${escapeHtml(activePlan.modality)} · ${escapeHtml(activePlan.intent || '—')}</div>
                <div class="cca-sum-row-sub" style="margin-top:2px;">${escapeHtml(activePlan.protocol_name || 'Protocol not yet recorded')}</div>
                <div class="cca-sum-row-sub" style="margin-top:6px;">Cycle ${activePlan.completed_sessions || 0} of ${activePlan.planned_sessions || '—'} · Signed by ${escapeHtml(activePlan.signer_email || '—')}</div>
                ${activePlan.guideline_review_required ? `<div class="cca-sum-row-sub" style="margin-top:6px; color:var(--amber-badge-text);">⚠ Guideline review required: ${escapeHtml(activePlan.guideline_review_reason || 'newer guideline version published')}</div>` : ''}
            ` : `<p class="cca-sum-empty">No active Treatment Plan on record yet.</p>`}
        </div>`;

    const openTasks = (tasks || []).filter(t => t.status !== 'RESOLVED').slice(0, 5);
    const careCard = `
        <div class="section-card" style="margin-bottom:0;">
            <div class="section-head">
                <div class="section-title" style="font-size:14px;">Care Plan / Next Steps</div>
                ${carePlan ? `<span class="badge ${CP_SUMMARY_BADGE[carePlan.status] || 'badge-blue'}">${escapeHtml(carePlan.status)}</span>` : ''}
            </div>
            ${carePlan ? `<div class="cca-sum-row-sub" style="margin-bottom:8px;">Intent: ${escapeHtml(carePlan.intent || '—')} · v${carePlan.version_no}</div>` : `<p class="cca-sum-empty" style="margin-bottom:8px;">No Care Plan on record yet.</p>`}
            ${openTasks.length ? openTasks.map(t => `
                <div class="cca-sum-row" style="padding:6px 0;">
                    <div class="cca-sum-row-main">
                        <div class="cca-sum-row-title" style="font-size:12.5px;">${escapeHtml(t.patient_visible_note || t.description)}</div>
                        <div class="cca-sum-row-sub">${escapeHtml(t.owner_name || 'Unassigned')} · due ${t.due_date ? fmtDateTime(t.due_date) : '—'}</div>
                    </div>
                    <span class="badge ${TASK_SUMMARY_BADGE[t.status] || 'badge-blue'}" style="font-size:10px;">${escapeHtml(t.status)}</span>
                </div>
            `).join('') : '<p class="cca-sum-empty">No open tasks.</p>'}
        </div>`;

    container.innerHTML = `<div class="grid-2" style="align-items:start;">${treatmentCard}${careCard}</div>`;
}
