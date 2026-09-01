import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), 'utf8')
const [layout, globals, tailwind, shell, header, patients, sidebar, access] = await Promise.all([
  read('app/layout.tsx'), read('app/globals.css'), read('tailwind.config.ts'),
  read('components/shell/app-shell.tsx'), read('components/shell/header.tsx'), read('app/patients/page.tsx'),
  read('components/shell/sidebar.tsx'), read('lib/demo-access.ts'),
])

assert.match(layout, /import ['"]\.\/globals\.css['"]/, 'Root layout must import the global CSS entry')
assert.match(globals, /@tailwind base;/)
assert.match(globals, /@tailwind components;/)
assert.match(globals, /@tailwind utilities;/)
for (const source of ['./app/**/*', './components/**/*', './lib/**/*']) assert.ok(tailwind.includes(source), `Tailwind content is missing ${source}`)
assert.equal((shell.match(/data-testid="desktop-sidebar"/g) ?? []).length, 1, 'Exactly one desktop sidebar must exist')
assert.match(shell, /data-testid="desktop-sidebar" className="hidden[^\"]*lg:block"/, 'Desktop sidebar must be hidden below lg')
assert.equal((shell.match(/data-testid="mobile-navigation"/g) ?? []).length, 1, 'Exactly one mobile navigation must exist')
assert.match(shell, /fixed inset-0 z-50 lg:hidden/, 'Mobile navigation must be hidden at lg')
assert.match(shell, /data-testid="app-main"/, 'Main application container is required')
assert.match(header, /data-testid="global-search"/, 'Global search is required')
assert.match(header, /data-testid="role-selector"/, 'Role selector is required')
assert.match(patients, /<Card/)
assert.match(patients, /<Button[^>]*>Start Consultation/, 'Patients must use the styled consultation action')
for (const role of ['registration','nurse','doctor','surgical-oncology','radiation-oncology','radiologist','radiology','pathologist','lab','infusion-nurse','mdt-coordinator','mdt-clinician','navigator','finance','admin']) {
  assert.ok(access.includes(`id: '${role}'`), `Missing role contract for ${role}`)
}
for (const label of ['Guideline Pathway','Staging','Surgical Plan','Radiation Prescription','Imaging Worklist','Imaging Coordination','Pathology Worklist','Molecular Diagnostics','Treatment Day / Infusion','Assigned Cases','Care Coordination','Financial Counselling','Estimates & Clearance','Operations Dashboard','Audit & Activity']) {
  assert.ok(sidebar.includes(`label:'${label}'`), `Missing required role navigation label: ${label}`)
}
console.log('UI shell regression guard passed')
