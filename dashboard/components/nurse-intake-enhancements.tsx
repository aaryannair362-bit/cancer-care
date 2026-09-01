'use client'

import * as React from 'react'
import { CheckCircle2, FileSearch, FileText, ShieldAlert } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { DocumentUploadField } from '@/components/documents/document-upload-field'
import { useDocuments } from '@/lib/documents/store'
import { DEMO_PATIENT_ID } from '@/lib/oncology/seed-data'
import type { ActorRef } from '@/lib/oncology/types'

const seedDocuments = [
  { key:'seed-0', name:'Pathology report',type:'Pathology',date:'18 Jun 2026',status:'Reviewed' },
  { key:'seed-1', name:'Previous CBC',type:'Laboratory',date:'19 Aug 2026',status:'Needs review' },
  { key:'seed-2', name:'External prescription',type:'Prescription',date:'20 Aug 2026',status:'Reviewed' },
]
const seedExtracted = [
  ['Hemoglobin','9.8 g/dL','96%','Needs verification'],['WBC','3.2 ×10⁹/L','94%','Needs verification'],['ER','Positive','99%','Verified'],['PR','Positive','98%','Verified'],['HER2','Negative','97%','Verified'],
]

/**
 * "Previous Documents" — the seeded fictional documents below remain as illustrative,
 * clearly-labeled demo content (see status badges). What changed: the "Upload / Capture
 * document" action used to only set a fake status string ("Demo capture ready — no file
 * was stored."). It now runs a real upload + real client-side OCR (see
 * lib/documents/ocr.ts) and a genuinely-uploaded document appears in the same list with
 * its own real extracted text/fields, reviewed the same way the seeded examples are.
 */
export function PreviousDocumentsSection({ actor }: { actor: ActorRef }) {
  const { getDocumentsForPatient } = useDocuments()
  const uploadedDocuments = getDocumentsForPatient(DEMO_PATIENT_ID)

  const [selectedKey, setSelectedKey] = React.useState('seed-0')
  const [verified,setVerified]=React.useState<Record<string,boolean>>({ER:true,PR:true,HER2:true})
  const [editing,setEditing]=React.useState<string | null>(null)
  const [values,setValues]=React.useState<Record<string,string>>(()=>Object.fromEntries(seedExtracted.map(([field,value])=>[field,value])))

  const selectedSeed = seedDocuments.find((d) => d.key === selectedKey)
  const selectedUploaded = uploadedDocuments.find((d) => d.id === selectedKey)

  return <Card><CardHeader className="border-b border-divider"><div className="flex flex-col gap-3 sm:flex-row sm:items-start"><span className="flex size-9 items-center justify-center rounded-md bg-brand-soft"><FileSearch className="size-4"/></span><div className="min-w-0 flex-1"><CardTitle>Previous Documents</CardTitle><CardDescription className="mt-1">{seedDocuments.length} fictional document(s) plus any uploaded this session</CardDescription></div></div>
    <div className="mt-4 max-w-md"><DocumentUploadField patientId={DEMO_PATIENT_ID} actor={actor} documentType="Previous medical record" buttonLabel="Upload / Capture document" onUploaded={(record)=>setSelectedKey(record.id)} /></div>
  </CardHeader><CardContent className="grid gap-5 pt-6 lg:grid-cols-2">
    <div className="space-y-2">
      {seedDocuments.map((document)=><button type="button" key={document.key} onClick={()=>setSelectedKey(document.key)} className="flex w-full items-center justify-between gap-3 rounded-md border border-border p-3 text-left hover:bg-surface-app"><div><p className="text-sm font-semibold text-supporting">{document.name}</p><p className="mt-1 text-xs text-metadata">{document.type} · {document.date} · Fictional</p></div><Badge variant={document.status==='Reviewed'?'success':'warning'}>{document.status}</Badge></button>)}
      {uploadedDocuments.map((document)=><button type="button" key={document.id} onClick={()=>setSelectedKey(document.id)} className="flex w-full items-center justify-between gap-3 rounded-md border border-border p-3 text-left hover:bg-surface-app"><div className="flex items-center gap-2"><FileText className="size-4 shrink-0 text-metadata"/><div><p className="text-sm font-semibold text-supporting">{document.filename}</p><p className="mt-1 text-xs text-metadata">{document.documentType} · {new Date(document.uploadedAt).toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'})}</p></div></div><Badge variant={document.ocrStatus==='completed'?'success':document.ocrStatus==='failed'?'critical':document.ocrStatus==='needs_review'?'warning':'information'}>{document.ocrStatus.replace('_',' ')}</Badge></button>)}
    </div>

    {selectedUploaded ? (
      <div className="rounded-lg border border-ai-highlight bg-ai-panel p-4">
        <div className="flex items-start justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-wider text-information-strong">Document Review</p><p className="mt-1 text-sm font-semibold">{selectedUploaded.filename}</p></div><Badge variant="information">{selectedUploaded.ocrEngine ?? 'Uploaded'}</Badge></div>
        {selectedUploaded.dataUrl.startsWith('data:image') ? <img src={selectedUploaded.dataUrl} alt={selectedUploaded.filename} className="mt-4 max-h-40 w-full rounded-md border border-border object-contain" /> : <div className="mt-4 flex h-24 items-center justify-center rounded-md border border-dashed border-border bg-surface text-xs text-metadata">{selectedUploaded.contentType}</div>}
        {selectedUploaded.ocrStatus==='failed' ? <p className="mt-3 flex items-center gap-2 text-xs text-critical-strong"><ShieldAlert className="size-3.5 shrink-0"/>{selectedUploaded.ocrError}</p> : (
          <>
            <p className="mt-4 text-xs font-semibold uppercase tracking-wider text-metadata">Extracted information — real OCR, reviewed here</p>
            {selectedUploaded.extractedFields && Object.keys(selectedUploaded.extractedFields).length>0 ? (
              <div className="mt-2 divide-y divide-divider">{Object.entries(selectedUploaded.extractedFields).map(([field,value])=><div key={field} className="grid gap-2 py-2 text-xs sm:grid-cols-[110px_1fr] sm:items-center"><span className="font-medium capitalize text-supporting">{field}</span><span>{value}</span></div>)}</div>
            ) : <p className="mt-2 text-xs text-metadata">Text extracted; no labeled fields (Diagnosis:/Medications:/etc.) were found — review the raw text below.</p>}
            {selectedUploaded.extractedText ? <details className="mt-3"><summary className="cursor-pointer text-xs font-semibold text-brand-deep">View full extracted text</summary><p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-metadata">{selectedUploaded.extractedText}</p></details> : null}
          </>
        )}
        <p className="mt-3 text-xs text-metadata">Real client-side OCR. Verify every value against the original document before relying on it clinically.</p>
      </div>
    ) : selectedSeed ? (
      <div className="rounded-lg border border-ai-highlight bg-ai-panel p-4"><div className="flex items-start justify-between gap-3"><div><p className="text-xs font-semibold uppercase tracking-wider text-information-strong">AI Document Review</p><p className="mt-1 text-sm font-semibold">{selectedSeed.name}</p></div><Badge variant="information">Demo extraction</Badge></div><div className="mt-4 flex h-24 items-center justify-center rounded-md border border-dashed border-border bg-surface text-xs text-metadata">Original fictional document preview placeholder</div><p className="mt-4 text-xs font-semibold uppercase tracking-wider text-metadata">Extracted information</p><div className="mt-2 divide-y divide-divider">{seedExtracted.map(([field,,confidence,status])=><div key={field} className="grid gap-2 py-2 text-xs sm:grid-cols-[80px_1fr_auto] sm:items-center"><span className="font-medium text-supporting">{field}</span>{editing===field?<Input aria-label={`Edit ${field} extracted value`} value={values[field]} onChange={(event)=>setValues((current)=>({...current,[field]:event.target.value}))}/>:<span>{values[field]} · {confidence}</span>}<Badge variant={verified[field]?'success':'warning'}>{verified[field]?'Verified':status}</Badge><div className="flex justify-end gap-2 sm:col-span-3"><Button type="button" size="sm" variant="ghost" onClick={()=>{setVerified((current)=>({...current,[field]:true}));setEditing(null)}}>Accept</Button><Button type="button" size="sm" variant="ghost" onClick={()=>setEditing(field)}>Edit</Button></div></div>)}</div><p className="mt-3 text-xs text-metadata">Fictional demo document — illustrative extraction only, not real OCR.</p></div>
    ) : null}
  </CardContent></Card>
}

export function StructuredOncologyHistory() {
  const [tobacco,setTobacco]=React.useState(false)
  const fields=[['Diagnosis','Invasive ductal carcinoma'],['Histology','Grade 2 IDC'],['Laterality','Left breast'],['ER','Positive'],['PR','Positive'],['HER2','Negative'],['Stage','IIA'],['Surgery','Breast-conserving surgery'],['Surgery date','15 Jun 2026']]
  return <div className="space-y-5 sm:col-span-2"><div className="grid gap-4 sm:grid-cols-3">{fields.map(([label,value])=><label key={label} className="text-xs font-medium text-metadata">{label}<Input className="mt-1" defaultValue={value}/></label>)}</div><div className="grid gap-3 sm:grid-cols-2">{[['Family history','Maternal aunt with breast cancer at 62.'],['Reproductive history','G2P2 · First birth at 27.'],['Hormonal history','No current hormone replacement therapy.']].map(([title,content])=><details key={title} className="rounded-md border border-border p-3"><summary className="cursor-pointer text-sm font-semibold text-supporting">{title}</summary><p className="mt-2 text-xs text-metadata">{content}</p></details>)}<details className="rounded-md border border-border p-3" open><summary className="cursor-pointer text-sm font-semibold text-supporting">Social history</summary><div className="mt-3"><p className="text-xs font-medium text-metadata">Tobacco use</p><div className="mt-2 flex gap-2"><Button type="button" size="sm" variant={!tobacco?'primary':'outline'} onClick={()=>setTobacco(false)}>No</Button><Button type="button" size="sm" variant={tobacco?'primary':'outline'} onClick={()=>setTobacco(true)}>Yes</Button></div>{tobacco?<div className="mt-3 grid gap-3 sm:grid-cols-3"><Input aria-label="Tobacco type" placeholder="Type"/><Input aria-label="Tobacco duration" placeholder="Duration"/><Input aria-label="Tobacco quantity" placeholder="Quantity"/></div>:null}</div></details></div></div>
}

export function NurseHandoffSummary({ allergyConfirmed, priority }: { allergyConfirmed:boolean; priority:string }) {
  const items=['Identity verified','Vitals recorded','Medication reconciled',allergyConfirmed?'Allergy reviewed':'Allergy review pending','ECOG recorded','Safety assessment completed','Nurse observations recorded']
  const priorityLabel = priority === 'urgent' ? 'Urgent clinician review' : priority === 'priority' ? 'Priority review' : 'Routine review'
  return <Card><CardHeader className="border-b border-divider"><CardTitle>Nurse Handoff Summary</CardTitle><CardDescription>Information prepared for the receiving oncology clinician</CardDescription></CardHeader><CardContent className="grid gap-5 pt-6 md:grid-cols-2"><div className="space-y-2">{items.map((item)=><p key={item} className="flex items-center gap-2 text-sm text-supporting"><CheckCircle2 className="size-4 text-success-strong"/>{item}</p>)}</div><dl className="space-y-3 rounded-md border border-border bg-input-background p-4 text-sm"><div className="flex justify-between"><dt className="text-metadata">Handoff priority</dt><dd className="font-medium">{priorityLabel}</dd></div><div className="flex justify-between"><dt className="text-metadata">Destination</dt><dd className="font-medium">Medical Oncology</dd></div><div className="flex justify-between"><dt className="text-metadata">Receiving clinician</dt><dd className="font-medium">Medical Oncologist</dd></div></dl>{!allergyConfirmed?<div className="flex gap-2 rounded-md border border-warning/30 bg-warning-subtle p-3 text-xs text-warning-strong md:col-span-2"><ShieldAlert className="size-4"/>Confirm the documented allergy before completing handoff.</div>:null}</CardContent></Card>
}
