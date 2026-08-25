# Aivana Oncology OS — UI Style Rules

## 1. Visual Direction
- Clinical minimalism + restricted neumorphism + soft morphism + selective glassmorphism.
- Feel: clinical, premium, calm, intelligent, precise, trustworthy.
- Avoid: generic SaaS, consumer-health, futuristic/decorative, gaming-like, excessive rounded cards, excessive animation.
- Clinical readability always outranks visual effects.
- No arbitrary colors, gradients, fonts, shadows or radii.
- Visual effects are semantic, not decorative.

## 2. Typography
- Satoshi: brand and hierarchy — hero headings, page titles, section headings; weights 500–700.
- Inter: clinical/UI — body, labels, forms, tables, clinical data, metadata; weights 400–600.

## 3. Color System
### Brand
- Soft brand: #DDE7F0
- Main brand: #6F8FAF
- Brand strong: #8FA8BF
- Charcoal: #1C1C1C
- Highlight: #B7C7D6

### Surfaces
- App background: #F1F5F9
- Surface: #FFFFFF
- Elevated: #E7EBF1
- Secondary panel: #D2D8DF
- Clinical document: #DDE7F0

### Text / borders
- Supporting text: #334155
- Metadata: #64748B
- Disabled: #94A3B8
- Border: #E2E8F0
- Divider: #EDF1F4
- Emphasized border: #CBD5E1

### Semantic
- Success: #3F8F62; BG #E1F2E8; text #286443
- Warning: #C58A28; BG #FFF1D6; text #8A5D13
- Critical: #C94F4F; BG #FBE4E4; text #963838
- Information: #4C82B8; BG #E3EFFA; text #315F8D

### AI
- AI generated: #6F8FAF
- AI highlight: #DDE7F0
- AI panel: #F1F5F9
- AI emphasis: #8FA8BF

### Clinical chart colors
- Primary: #7F9DB3
- Current/emphasized: #52758A
- Secondary: #91A8BA
- Trend: #B8CCD8
- Target/reference: #C49B5C
- Abnormal/caution: #C8756E
- Neutral: #4D5D69

## 4. Surfaces & Depth
- Default UI is clean and flat.
- Restricted neumorphism: small controls, recording controls, selected controls, AI processing/focused interactions only. Never dense tables, pathology, lab values or large forms.
- Soft morphism: use depth to separate workspace hierarchy; avoid nested card stacks; shadows are broad, soft and low contrast.
- Glassmorphism: only temporary/contextual surfaces such as command palette, document preview overlays and AI review panels.
- Never put critical clinical information on translucent glass.
- If removing an effect does not reduce comprehension/usability, remove it.

## 5. Components
- Primary button: #6F8FAF + white text.
- Secondary button: #DDE7F0 + #334155.
- Selected navigation: #6F8FAF + white.
- Standard card: white + #E2E8F0 border.
- AI card: #F1F5F9 + #DDE7F0 accent.
- Input: #F8FAFC + #E2E8F0 border; focus #8FA8BF.
- Use one clear primary action per local context.
- Do not wrap every field in a card.
- Dense clinical tables prioritize scanability; no glass/heavy shadows.

## 6. Clinical UI
- Patient identity/context should be immediately understandable.
- Diagnosis, stage, TNM, biomarkers, medications, pathology and critical results outrank decorative UI.
- Critical states must be unmistakable.
- Never rely on color alone; pair with text/icon/position.
- Do not animate urgent clinical information.

## 7. AI UI
- AI is an assistant, not an autonomous clinical decision-maker.
- Preferred sequence: AI generated → human review → edit/correction → confirmation → final.
- AI-generated content must be distinguishable from human-verified/final information.
- Show source/provenance and confidence where appropriate.
- AI should feel embedded in the clinical workflow, not like a separate chatbot.

## 8. Motion
- Restrained motion only: panel transitions, AI processing, recording states, confirmation.
- No bouncing, constant floating, parallax or decorative loops.
- Clinical content remains visually stable.

## 9. Layout & Density
- Consistent application shell across modules.
- Keep patient context persistent where useful.
- Prefer one strong primary workspace over competing panels.
- Use drawers/modals for secondary tasks.
- Use a 4/8px spacing rhythm.
- Larger gaps separate semantic sections; smaller gaps group related fields.
- Restrained, consistent radii; pills only for statuses, filters and compact semantic controls.
- Responsive behavior must preserve clinical hierarchy.

## 10. Design QA
Before approving any screen:
- Looks unmistakably Aivana.
- Satoshi/Inter used correctly.
- Dusty Blue used for brand/interaction, not decoration.
- Semantic colors reserved for meaning.
- Clinical information readable at a glance.
- Primary action obvious.
- AI vs human-verified information distinguishable.
- Glass/neumorphism used only where appropriate.
- Surfaces, borders and shadows consistent.
- No unnecessary cards, gradients or effects.
- High information density still feels calm.
- Existing tokens/components reused.
- No effect reduces accessibility or clinical clarity.

## Final Rule
Premium clinical intelligence through restraint. Aivana should feel advanced because its information architecture, hierarchy and interaction quality are exceptionally clear—not because every surface is animated, blurred or dimensional.
