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
async function renderCaseSummaryPanel(containerId, patientId, options = {}) {
    _ccaSummaryEnsureStyles();
    const container = document.getElementById(containerId);
    if (!container) return;
    const hideMedications = !!options.hideMedications;

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
                <div class="cca-sum-tile"><div class="cca-sum-tile-label">Documents on file</div><div class="cca-sum-tile-value">${ov.document_count ?? '—'}</div></div>
                <div class="cca-sum-tile"><div class="cca-sum-tile-label">Verified facts</div><div class="cca-sum-tile-value">${ov.verified_fact_count ?? '—'}</div></div>
                <div class="cca-sum-tile"><div class="cca-sum-tile-label">Encounters</div><div class="cca-sum-tile-value">${ov.encounter_count ?? '—'}</div></div>
                <div class="cca-sum-tile"><div class="cca-sum-tile-label">Last visit</div><div class="cca-sum-tile-value" style="font-size:13px;">${ov.last_visit ? fmtDateTime(ov.last_visit) : '—'}</div></div>
            </div>
            ${p.id_proof_number ? `<div class="cca-sum-row-sub">Identity on file: ${escapeHtml(p.id_proof_type || 'ID')} ${escapeHtml(p.id_proof_number)} · ${escapeHtml(p.id_proof_verification_status || 'Pending')}</div>` : ''}
        </div>`;

    const documentsSection = `
        <div class="section-card">
            <div class="section-title" style="margin-bottom:12px;">Ingested Documents</div>
            ${(summary.documents || []).length ? summary.documents.map(d => `
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

    const factEntries = Object.entries(summary.clinical_facts || {}).filter(([type]) => !hideMedications || type !== 'MEDICATION');
    const factsSection = `
        <div class="section-card">
            <div class="section-title" style="margin-bottom:12px;">${hideMedications ? 'Diagnoses &amp; Extracted Findings' : 'Diagnoses, Medications &amp; Extracted Findings'}</div>
            ${factEntries.length ? factEntries.map(([type, facts]) => `
                <div class="cca-sum-fact-group">
                    <div class="cca-sum-fact-group-title">${escapeHtml(CCA_SUMMARY_FACT_LABELS[type] || type)}</div>
                    <div class="cca-sum-chip-row">
                        ${facts.map(f => `<span class="cca-sum-chip${f.status !== 'VERIFIED' ? ' is-proposed' : ''}" title="${f.status === 'VERIFIED' ? 'Clinician-verified' : 'AI-drafted — not yet verified'}">${escapeHtml(f.value)}</span>`).join('')}
                    </div>
                </div>
            `).join('') : '<p class="cca-sum-empty">No diagnoses or findings extracted yet.</p>'}
        </div>`;

    const ORDER_STATUS_BADGE = { RAISED: 'badge-amber', SCHEDULED: 'badge-amber', IN_PROGRESS: 'badge-amber', RESULTED: 'badge-green', ACKNOWLEDGED: 'badge-green', CLOSED: 'badge-blue', CANCELLED: 'badge-rose' };
    const ordersSection = `
        <div class="section-card">
            <div class="section-title" style="margin-bottom:12px;">Investigations Ordered (Lab / Radiology / Pathology)</div>
            ${(summary.orders || []).length ? summary.orders.map(o => `
                <div class="cca-sum-row">
                    <div class="cca-sum-row-main">
                        <div class="cca-sum-row-title">${escapeHtml(o.item_name)} <span style="font-weight:400; color:var(--ink-500);">· ${escapeHtml(o.order_type || '')}</span></div>
                        <div class="cca-sum-row-sub">${o.ordered_at ? fmtDateTime(o.ordered_at) : '—'}${o.requested_by ? ' · ' + escapeHtml(o.requested_by) : ''}${o.priority && o.priority !== 'ROUTINE' ? ' · ' + escapeHtml(o.priority) : ''}</div>
                        ${o.clinical_indication ? `<div class="cca-sum-row-sub" style="margin-top:4px;">Indication: ${escapeHtml(o.clinical_indication)}</div>` : ''}
                    </div>
                    <span class="badge ${ORDER_STATUS_BADGE[o.status] || 'badge-amber'}">${escapeHtml(o.status || 'Unknown')}</span>
                </div>
            `).join('') : '<p class="cca-sum-empty">No investigations ordered yet.</p>'}
        </div>`;

    const RESULT_STATUS_BADGE = { NEW: 'badge-amber', PENDING_REVIEW: 'badge-amber', ACKNOWLEDGED: 'badge-green', ACTIONED: 'badge-green' };
    const resultsSection = `
        <div class="section-card">
            <div class="section-title" style="margin-bottom:12px;">Results</div>
            ${(summary.results || []).length ? summary.results.map(r => `
                <div class="cca-sum-row">
                    <div class="cca-sum-row-main">
                        <div class="cca-sum-row-title">${escapeHtml(r.title)} <span style="font-weight:400; color:var(--ink-500);">· ${escapeHtml(r.result_type || '')}</span></div>
                        <div class="cca-sum-row-sub">${r.resulted_at ? fmtDateTime(r.resulted_at) : '—'}</div>
                        ${r.excerpt ? `<div class="cca-sum-row-sub" style="margin-top:4px;">${escapeHtml(r.excerpt)}</div>` : ''}
                    </div>
                    <div style="display:flex; flex-direction:column; align-items:flex-end; gap:6px;">
                        ${r.is_critical ? '<span class="badge badge-rose">🔴 Critical</span>' : ''}
                        <span class="badge ${RESULT_STATUS_BADGE[r.status] || 'badge-amber'}">${escapeHtml(r.status || 'Unknown')}</span>
                    </div>
                </div>
            `).join('') : '<p class="cca-sum-empty">No results on record yet.</p>'}
        </div>`;

    const encountersSection = `
        <div class="section-card">
            <div class="section-title" style="margin-bottom:12px;">Consultation / Encounter History</div>
            ${(summary.encounters || []).length ? summary.encounters.map(e => `
                <div class="cca-sum-timeline-item">
                    <div class="cca-sum-timeline-when">${e.started_at ? fmtDateTime(e.started_at) : '—'} · ${escapeHtml(e.specialty || '')}${e.clinician ? ' · ' + escapeHtml(e.clinician) : ''}</div>
                    <div class="cca-sum-timeline-title">${escapeHtml(e.diagnosis || e.chief_complaint || 'No diagnosis recorded')}</div>
                    ${e.advice ? `<div class="cca-sum-timeline-desc">Plan: ${escapeHtml(e.advice)}</div>` : ''}
                    ${!hideMedications && e.medications && e.medications.length ? `<div class="cca-sum-timeline-desc">Medications noted: ${e.medications.map(m => {
                        if (!m || typeof m !== 'object') return escapeHtml(String(m));
                        const name = m.drugName || m.name || 'Unnamed';
                        const posology = [m.dose, m.frequency, m.route, m.duration].filter(Boolean).join(', ');
                        return escapeHtml(posology ? `${name} (${posology})` : name);
                    }).join('; ')}</div>` : ''}
                </div>
            `).join('') : '<p class="cca-sum-empty">No prior consultations on record.</p>'}
        </div>`;

    const journeySection = `
        <div class="section-card">
            <div class="section-title" style="margin-bottom:12px;">Journey Timeline</div>
            ${(summary.journey || []).length ? summary.journey.map(ev => `
                <div class="cca-sum-timeline-item">
                    <div class="cca-sum-timeline-when">${ev.timestamp ? fmtDateTime(ev.timestamp) : '—'}${ev.actor ? ' · ' + escapeHtml(ev.actor) : ''}</div>
                    <div class="cca-sum-timeline-title">${escapeHtml(ev.title)}</div>
                    ${ev.description ? `<div class="cca-sum-timeline-desc">${escapeHtml(ev.description)}</div>` : ''}
                </div>
            `).join('') : '<p class="cca-sum-empty">No journey events recorded yet.</p>'}
        </div>`;

    container.innerHTML = header + documentsSection + factsSection + ordersSection + resultsSection + encountersSection + journeySection +
        `<p style="font-size:11.5px; color:var(--ink-500); margin-top:-8px;">${escapeHtml(summary.disclaimer)}</p>`;
}

/**
 * Financial Counsellor's restricted view of case-summary: the backend's is_cca_financial_counsellor
 * branch (GET /cca/patients/{id}/case-summary) deliberately returns a different, narrower shape
 * than the full clinical one above -- order/plan status only, per spec (DA-13: "Financial
 * Counsellor -- Status field only") and rbac_projection.py's FINANCE tier (modality/cycle counts,
 * not full clinical notes). renderCaseSummaryPanel assumes the full shape and renders empty
 * sections against this one, so this is a dedicated renderer for the shape Finance actually gets.
 */
const FIN_ORDER_STATUS_BADGE = { RAISED: 'badge-amber', SCHEDULED: 'badge-amber', IN_PROGRESS: 'badge-amber', RESULTED: 'badge-green', ACKNOWLEDGED: 'badge-green', CLOSED: 'badge-blue', CANCELLED: 'badge-rose' };

async function renderFinancialCaseSummary(containerId, patientId) {
    _ccaSummaryEnsureStyles();
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!patientId) { container.innerHTML = ''; return; }

    container.innerHTML = `<div class="section-card"><p class="cca-sum-empty">Loading patient summary…</p></div>`;

    let summary;
    try {
        summary = await Api.get(`/cca/patients/${patientId}/case-summary`);
    } catch (err) {
        container.innerHTML = `<div class="section-card"><p style="color:var(--rose-badge-text); font-size:13px;">${escapeHtml(apiErrorMessage(err))}</p></div>`;
        return;
    }

    const p = summary.patient;
    const ov = summary.overview;
    const orders = (summary.financial_projection && summary.financial_projection.orders) || [];
    const plans = (summary.financial_projection && summary.financial_projection.active_plans) || [];

    const header = `
        <div class="section-card">
            <div class="section-head">
                <div>
                    <div class="section-title">${escapeHtml(p.name)} <span style="font-weight:400; color:var(--ink-500);">· ${escapeHtml(p.mrn)}</span></div>
                    <div class="section-sub">${escapeHtml(String(p.age ?? '—'))}y · ${escapeHtml(p.sex || '—')} · ${escapeHtml(p.journey_state || '—')}</div>
                </div>
                ${ov.is_returning_patient ? '<span class="badge badge-blue">Returning patient</span>' : '<span class="badge badge-amber">No prior visits on record</span>'}
            </div>
            <div class="cca-sum-grid">
                <div class="cca-sum-tile"><div class="cca-sum-tile-label">Orders on record</div><div class="cca-sum-tile-value">${ov.order_count ?? '—'}</div></div>
            </div>
        </div>`;

    const ordersSection = `
        <div class="section-card">
            <div class="section-title" style="margin-bottom:12px;">Investigation Orders (status only)</div>
            ${orders.length ? orders.map(o => `
                <div class="cca-sum-row">
                    <div class="cca-sum-row-main">
                        <div class="cca-sum-row-title">${escapeHtml(o.item_name)} <span style="font-weight:400; color:var(--ink-500);">· ${escapeHtml(o.order_type || '')}</span></div>
                    </div>
                    <span class="badge ${FIN_ORDER_STATUS_BADGE[o.status] || 'badge-amber'}">${escapeHtml(o.status || 'Unknown')}</span>
                </div>
            `).join('') : '<p class="cca-sum-empty">No investigation orders on record.</p>'}
        </div>`;

    const plansSection = `
        <div class="section-card">
            <div class="section-title" style="margin-bottom:12px;">Treatment Plans (modality/cycle status only)</div>
            ${plans.length ? plans.map(tp => `
                <div class="cca-sum-row">
                    <div class="cca-sum-row-main">
                        <div class="cca-sum-row-title">${escapeHtml(tp.modality || 'Treatment plan')}${tp.protocol_name ? ' · ' + escapeHtml(tp.protocol_name) : ''}</div>
                        <div class="cca-sum-row-sub">${tp.planned_sessions ? `Planned sessions: ${escapeHtml(String(tp.planned_sessions))}` : ''}${tp.start_date ? ' · Start ' + fmtDateTime(tp.start_date) : ''}</div>
                    </div>
                    <span class="badge ${TP_SUMMARY_BADGE[tp.status] || 'badge-blue'}">${escapeHtml(tp.status || 'Unknown')}</span>
                </div>
            `).join('') : '<p class="cca-sum-empty">No treatment plans on record.</p>'}
        </div>`;

    container.innerHTML = header + ordersSection + plansSection +
        `<p style="font-size:11.5px; color:var(--ink-500); margin-top:-8px;">${escapeHtml(summary.disclaimer || 'Financial projection view: includes billing, modality counts, and operational status only.')}</p>`;
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
const CP_SUMMARY_BADGE = { DRAFT: 'badge-amber', PROPOSED: 'badge-amber', ACTIVE: 'badge-green', BLOCKED: 'badge-rose', ON_HOLD: 'badge-amber', COMPLETED: 'badge-blue', CANCELLED: 'badge-rose' };
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

/*
 * NEXUS Clinical Reasoning Brief -- the 13 (+1 Must-Not-Miss) source-linked sections from
 * synthesize_nexus_brief (architecture doc Sec 20). Previously only the retired cca_os.html
 * single-page app rendered this; every live role page's NEXUS tab showed static demo content
 * instead. Ported from cca-app.js's loadNexusBrief() and generalized (shared containerId/
 * patientId args, section keys read generically) so every role page's NEXUS tab can call it.
 */
async function renderNexusBrief(containerId, patientId) {
    _ccaSummaryEnsureStyles();
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!patientId) { container.innerHTML = ''; return; }
    container.innerHTML = `<div class="section-card"><p class="cca-sum-empty">Loading NEXUS clinical brief…</p></div>`;
    try {
        const data = await Api.get(`/cca/patients/${patientId}/clinical-brief`);
        const sections = data.sections || {};
        const keys = Object.keys(sections).sort();
        if (!keys.length) {
            container.innerHTML = `<div class="section-card"><p class="cca-sum-empty">No clinical brief available yet.</p></div>`;
            return;
        }
        container.innerHTML = keys.map((key) => {
            const sec = sections[key];
            const num = (key.match(/^\d+/) || ['?'])[0];
            return `
                <div class="section-card" style="margin-bottom:10px;">
                    <div class="section-title" style="font-size:13px; display:flex; gap:8px; align-items:baseline;">
                        <span style="font-family:monospace; color:var(--ink-500); font-size:11px;">${escapeHtml(num)}</span>
                        ${escapeHtml(sec.title || key)}
                    </div>
                    <p style="font-size:12.5px; color:var(--ink-700); line-height:1.6; margin-top:6px;">${escapeHtml(sec.content || '')}</p>
                </div>
            `;
        }).join('') + `<p style="font-size:11px; color:var(--ink-500);">Clinical uncertainty: ${escapeHtml(data.clinical_uncertainty || '—')} · Generated ${data.generated_at ? new Date(data.generated_at).toLocaleString() : '—'}</p>`;
    } catch (err) {
        container.innerHTML = `<div class="section-card"><p style="color:var(--rose-badge-text); font-size:13px;">${escapeHtml(apiErrorMessage(err))}</p></div>`;
    }
}

/*
 * AI Search -- global, source-cited, scope-governed retrieval (Spec Section 30). Shared by
 * every role page that has the #ai-search-input / #ai-search-results search-pill markup, so
 * there is one implementation of the scope selector, result rendering and propose-task flow
 * instead of one per (already copy-pasted) page. Retrieval and summarization only: the only
 * way a result becomes durable is an explicit "Propose as Care Plan Task" action (architecture
 * doc: no silent AI writes).
 *
 * Depends on page globals: CCA_API (or falls back to '/cca'), currentPatientId, and (for
 * "View Source" tab-jumping) an optional page-level showTab(tabName) function.
 */
const CCA_SEARCH_SCOPES = [
    { value: 'THIS_PATIENT', label: 'This Patient' },
    { value: 'HOSPITAL_RECORDS', label: 'Hospital Records' },
    { value: 'CLINICAL_KNOWLEDGE', label: 'Clinical Knowledge' },
];
let lastAiSearchResults = [];
let currentAiSearchScope = 'THIS_PATIENT';

function _ccaSearchEnsureScopeControl() {
    const input = document.getElementById('ai-search-input');
    if (!input || document.getElementById('ai-search-scope')) return;
    const select = document.createElement('select');
    select.id = 'ai-search-scope';
    select.title = 'Search scope';
    select.style.cssText = 'flex:0 0 auto; font-size:11.5px; color:var(--ink-600); background:transparent; border:none; border-right:1px solid var(--line); padding:0 8px 0 4px; margin-right:4px; cursor:pointer;';
    select.innerHTML = CCA_SEARCH_SCOPES.map(s => `<option value="${s.value}">${escapeHtml(s.label)}</option>`).join('');
    select.addEventListener('change', () => {
        currentAiSearchScope = select.value;
        if (input.value.trim().length >= 2) runAiSearch();
    });
    input.parentElement.insertBefore(select, input);
}

async function runAiSearch() {
    _ccaSearchEnsureScopeControl();
    const resultsEl = document.getElementById('ai-search-results');
    const input = document.getElementById('ai-search-input');
    const q = input.value.trim();
    const apiBase = (typeof CCA_API !== 'undefined' && CCA_API) ? CCA_API : '/cca';
    if (currentAiSearchScope !== 'HOSPITAL_RECORDS' && currentAiSearchScope !== 'CLINICAL_KNOWLEDGE' && !currentPatientId) {
        resultsEl.style.display = 'block';
        resultsEl.innerHTML = '<div style="padding:12px; font-size:12.5px; color:var(--ink-500);">Open a patient first to search their record.</div>';
        return;
    }
    if (q.length < 2) { resultsEl.style.display = 'none'; return; }
    resultsEl.style.display = 'block';
    resultsEl.innerHTML = '<div style="padding:12px; font-size:12.5px; color:var(--ink-500);">Searching…</div>';
    try {
        const patientForSearch = currentPatientId || 0;
        const data = await Api.get(`${apiBase}/patients/${patientForSearch}/search?query=${encodeURIComponent(q)}&scope=${encodeURIComponent(currentAiSearchScope)}`);
        lastAiSearchResults = data.results || [];
        if (!lastAiSearchResults.length) {
            resultsEl.innerHTML = '<div style="padding:12px; font-size:12.5px; color:var(--ink-500);">No matches in this scope.</div>';
            return;
        }
        resultsEl.innerHTML = lastAiSearchResults.map((r, i) => {
            const heading = r.snippet !== undefined ? r.snippet : (r.title || '');
            const body = r.content ? r.content : '';
            const metaBits = [];
            if (r.source_author) metaBits.push(escapeHtml(r.source_author));
            else if (r.citation) metaBits.push(escapeHtml(r.citation));
            if (r.source_date) metaBits.push(new Date(r.source_date).toLocaleDateString());
            if (r.confirmation_state) metaBits.push(escapeHtml(r.confirmation_state));
            return `
                <div style="padding:10px 12px; border-bottom:1px solid var(--line-subtle);">
                    <div style="font-size:10.5px; font-weight:700; color:var(--ink-500); text-transform:uppercase; letter-spacing:0.4px;">${escapeHtml((r.type || '').replace(/_/g, ' '))}</div>
                    ${heading ? `<div style="font-size:13px; color:var(--ink-900); margin:3px 0;">${escapeHtml(heading)}</div>` : ''}
                    ${body ? `<div style="font-size:12px; color:var(--ink-700); margin:3px 0;">${escapeHtml(body)}</div>` : ''}
                    <div style="font-size:11.5px; color:var(--ink-500);">${metaBits.length ? metaBits.join(' · ') : 'Unknown source'}</div>
                    <div style="display:flex; gap:6px; margin-top:6px;">
                        ${r.view_source ? `<button class="nexus-mini-btn" onclick="viewAiSearchSource(${i})">View Source</button>` : ''}
                        ${r.id != null ? `<button class="nexus-mini-btn primary" onclick="proposeAiSearchTask(${i})">Propose as Care Plan Task</button>` : ''}
                    </div>
                </div>
            `;
        }).join('');
    } catch (err) {
        resultsEl.innerHTML = `<div style="padding:12px; font-size:12.5px; color:var(--rose-badge-text);">${escapeHtml(apiErrorMessage(err))}</div>`;
    }
}

const CCA_SEARCH_RESULT_TAB = { CLINICAL_FACT: 'history', MDT_DECISION: 'mdt', TREATMENT_PLAN: 'treatmentplan', STAGING_RECORD: 'staging', JOURNEY_EVENT: 'history' };

function viewAiSearchSource(index) {
    const r = lastAiSearchResults[index];
    document.getElementById('ai-search-results').style.display = 'none';
    const tab = CCA_SEARCH_RESULT_TAB[r.type];
    if (tab && typeof showTab === 'function' && document.getElementById(`nav-${tab}`)) { showTab(tab); return; }
    toast(`Source: ${r.view_source}`, 'info');
}

async function proposeAiSearchTask(index) {
    const r = lastAiSearchResults[index];
    const apiBase = (typeof CCA_API !== 'undefined' && CCA_API) ? CCA_API : '/cca';
    const description = prompt('Task description for this patient task (reviewed and edited by you before saving):', r.snippet || '');
    if (!description) return;
    try {
        await Api.post(`${apiBase}/patients/${currentPatientId}/search/propose-task`, {
            description, source_reference: { type: r.type, id: r.id },
        });
        toast('Task proposed from AI Search', 'success');
        document.getElementById('ai-search-results').style.display = 'none';
        document.getElementById('ai-search-input').value = '';
    } catch (err) { toast(apiErrorMessage(err), 'error'); }
}

document.addEventListener('click', (e) => {
    const container = document.querySelector('.search-pill-container');
    const resultsEl = document.getElementById('ai-search-results');
    if (container && resultsEl && !container.contains(e.target)) {
        resultsEl.style.display = 'none';
    }
});
