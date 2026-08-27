"""
Ratified answers to open questions from the Care Plan & Treatment Plan architecture doc's
"Open Clinical Validation Items" (section 29) that this build previously left as unstated,
silent assumptions baked into behavior rather than named decisions someone could find,
question, or override.

Each constant below is one such item: the decision made, who made it (the product owner --
"the user", via direct instruction in the session that resolved this file), and the
reasoning. The architecture doc itself is explicit that these are meant to be validated with
institutional stakeholders before a build is frozen ("Do not freeze unvalidated assumptions...
the product can implement the workflow shell, permissions and audit model while leaving
institution-specific clinical rules configurable until formally approved"). Resolving them
here as named, documented, single-source-of-truth flags is exactly that pattern: not a
guess buried in an if-statement, and not something a future institutional deployment is
locked into -- a real deployment can revisit and flip any of these with a one-line change
plus a re-validation of the callers that branch on it.

None of these three concern dosage, dose-thresholds, or chemotherapy safety-check logic --
that remains explicitly out of scope regardless of what gets resolved here.
"""

# ---------------------------------------------------------------------------
# 1. Can an External MDT Specialist sign a recommendation, or only comment/opine?
#    (Architecture doc section 29: "Whether external MDT specialists can sign
#    recommendations or only comment.")
#
# Decision: OPINION/COMMENT ONLY. An external specialist's contribution
# (CCAExternalOpinion, routers/cca_coordination.py's submit_opinion) is recorded as a
# separately-attributable input to the case. It never becomes, replaces, or co-signs the
# institution's own binding MDTDecision -- only a participant of the treating institution's
# own tumour board (via routers/cca.py's record recommendation flow) produces the recommendation
# that actually drives a Treatment Plan.
#
# Reasoning: the architecture doc's own MDT integration section frames external specialists
# as case-scoped, time-limited guests providing outside opinion, not tumour-board members
# with binding authority over another institution's patient -- and the safety-conservative
# reading (per this doc's own "prefer the more conservative interpretation" instruction) is
# that clinical authority over a patient one's own institution didn't examine stays with the
# treating team. This was already the de facto behavior (no sign endpoint was ever built for
# this role); this flag makes that a stated decision instead of an implementation gap.
EXTERNAL_SPECIALIST_CAN_SIGN_RECOMMENDATIONS = False


# ---------------------------------------------------------------------------
# 2. Is the Care Plan one longitudinal object per patient, or one per treatment
#    episode/phase? (Architecture doc section 29: "Whether the Care Plan is one longitudinal
#    object per patient or one per treatment episode/phase.")
#
# Decision: ONE LONGITUDINAL OBJECT PER PATIENT while a care journey is in progress. A
# patient may not have a second CarePlan created while an existing one is still ACTIVE,
# BLOCKED, or ON_HOLD (routers/cca.py's create_care_plan enforces this -- see
# CARE_PLAN_IN_PROGRESS_STATUSES below). Once a CarePlan reaches a terminal state (COMPLETED
# or CANCELLED), a new CarePlan may be created -- representing a genuinely new chapter (e.g.
# a recurrence years later), not a second live copy of the same ongoing journey.
#
# Reasoning: architecture doc section 3 (Architecture Principles) states the rule directly --
# "One patient, one longitudinal record: Care Plan and Treatment Plan are shared clinical
# objects attached to the same patient record. Modules should not keep duplicate copies." A
# patient having two simultaneously-live Care Plans would fragment exactly the "current
# active milestone", "next 3-5 owned tasks", and "blocked/overdue" views the architecture doc
# defines Care Plan around -- there would be no single answer to "what's next for this
# patient" if two were open at once. Amendments to an in-progress plan already go through
# versioning in place (update_care_plan/update_care_plan_status), which is the correct tool
# for "the plan changed"; this decision is specifically about preventing a second,
# concurrent, competing plan object from ever existing for the same patient.
CARE_PLAN_IN_PROGRESS_STATUSES = ("ACTIVE", "BLOCKED", "ON_HOLD")


# ---------------------------------------------------------------------------
# 3. Are systemic therapy treatment orders created inside this OS, or only referenced from
#    an existing oncology/pharmacy system? (Architecture doc section 29: "Whether systemic
#    therapy treatment orders are created inside this OS or only referenced from an existing
#    oncology system.")
#
# Decision: CREATED IN-OS. TreatmentOrder (models_cca.py) is this system's own executable
# instruction record, authored and signed here (routers/cca.py's treatment-orders endpoints),
# not a read-only mirror of an external pharmacy/oncology-information-system order.
#
# Reasoning: this was already the as-built behavior before this decision was formally
# recorded -- TreatmentOrder/TreatmentEvent, the sign/clearance/administration workflow, and
# the Day-Care closed loop are all built and tested against in-OS authorship, and reversing
# that to a read-only external-reference model would mean rebuilding the entire Treatment
# Plan -> Order -> Event chain against an external system's API instead, which no such
# integration exists for in this codebase. Ratifying the existing behavior (rather than
# leaving it an unstated assumption) is the option that doesn't discard already-built,
# already-tested safety-relevant work. This decision does NOT extend to dose-threshold or
# chemotherapy safety-check logic (pharmacy verification remains explicitly out of scope --
# see architecture doc section 29's separate, still-open item on that, and this repo's
# standing instruction never to build it).
TREATMENT_ORDERS_SYSTEM_OF_RECORD = "in_os"  # the only other documented value would be "external_reference", unimplemented
