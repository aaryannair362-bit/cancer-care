'use client'

import * as React from 'react'
import {
  AlertCircle,
  CheckCircle2,
  ClipboardList,
  FileText,
  ShieldCheck,
  UserRound,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { PageContainer, PageHeader } from '@/components/shell/page-container'
import { cn } from '@/lib/utils'
import { RegistrationWorkspace } from '@/components/registration-workspace'

type FormValues = {
  firstName: string
  lastName: string
  dateOfBirth: string
  sexAtBirth: string
  genderIdentity: string
  phone: string
  alternatePhone: string
  email: string
  preferredLanguage: string
  address: string
  city: string
  state: string
  postalCode: string
  idType: string
  idNumber: string
  referringClinician: string
  referringFacility: string
  cancerType: string
  diagnosisStatus: string
  reasonForReferral: string
  emergencyName: string
  emergencyRelationship: string
  emergencyPhone: string
  communicationConsent: boolean
  privacyAcknowledged: boolean
}

type FieldErrors = Partial<Record<keyof FormValues, string>>

const initialValues: FormValues = {
  firstName: 'Sunita',
  lastName: 'Patil',
  dateOfBirth: '1987-04-18',
  sexAtBirth: 'female',
  genderIdentity: '',
  phone: '+91 98765 41028',
  alternatePhone: '',
  email: 'sunita.patil@example.test',
  preferredLanguage: 'English',
  address: '14 Demo Care Lane',
  city: 'Bengaluru',
  state: 'Karnataka',
  postalCode: '560001',
  idType: 'hospital-referral',
  idNumber: 'DEMO-REF-24081',
  referringClinician: 'Referring Clinician',
  referringFacility: 'Northstar Community Hospital (Demo)',
  cancerType: 'Breast',
  diagnosisStatus: 'confirmed',
  reasonForReferral: 'Newly diagnosed early-stage breast cancer; referred for multidisciplinary oncology review.',
  emergencyName: 'Vikram Patil',
  emergencyRelationship: 'Spouse',
  emergencyPhone: '+91 98765 41029',
  communicationConsent: true,
  privacyAcknowledged: false,
}

const requiredFields: Array<keyof FormValues> = [
  'firstName',
  'lastName',
  'dateOfBirth',
  'sexAtBirth',
  'phone',
  'preferredLanguage',
  'address',
  'city',
  'state',
  'postalCode',
  'emergencyName',
  'emergencyRelationship',
  'emergencyPhone',
  'privacyAcknowledged',
]

function FieldLabel({ htmlFor, children, required }: { htmlFor: string; children: React.ReactNode; required?: boolean }) {
  return (
    <label htmlFor={htmlFor} className="text-sm font-medium text-supporting">
      {children}
      {required ? <span className="ml-1 text-critical" aria-hidden="true">*</span> : null}
    </label>
  )
}

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null
  return (
    <p id={id} className="flex items-center gap-1.5 text-xs text-critical-strong">
      <AlertCircle className="size-3.5 shrink-0" aria-hidden="true" />
      {message}
    </p>
  )
}

function SectionHeading({ icon: Icon, title, description }: { icon: typeof UserRound; title: string; description: string }) {
  return (
    <CardHeader className="border-b border-divider pb-4">
      <div className="flex items-start gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-brand-soft text-supporting">
          <Icon className="size-4" aria-hidden="true" />
        </span>
        <div>
          <CardTitle>{title}</CardTitle>
          <CardDescription className="mt-1">{description}</CardDescription>
        </div>
      </div>
    </CardHeader>
  )
}

const selectClassName =
  'flex h-10 w-full rounded-md border border-input bg-input-background px-3 py-2 text-sm text-foreground shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50'

export default function RegistrationPage() {
  return <RegistrationWorkspace />
  const [values, setValues] = React.useState<FormValues>(initialValues)
  const [errors, setErrors] = React.useState<FieldErrors>({})
  const [submitted, setSubmitted] = React.useState(false)

  const updateValue = <K extends keyof FormValues>(field: K, value: FormValues[K]) => {
    setValues((current) => ({ ...current, [field]: value }))
    if (errors[field]) setErrors((current) => ({ ...current, [field]: undefined }))
    if (submitted) setSubmitted(false)
  }

  const inputProps = (field: keyof FormValues) => ({
    'aria-invalid': Boolean(errors[field]),
    'aria-describedby': errors[field] ? `${field}-error` : undefined,
    className: cn(errors[field] && 'border-critical focus-visible:ring-critical'),
  })

  const validate = () => {
    const nextErrors: FieldErrors = {}
    for (const field of requiredFields) {
      if (!values[field]) nextErrors[field] = 'This field is required.'
    }
    if (values.email && !/^\S+@\S+\.\S+$/.test(values.email)) nextErrors.email = 'Enter a valid email address.'
    if (values.phone && values.phone.replace(/\D/g, '').length < 10) nextErrors.phone = 'Enter a valid phone number.'
    if (values.emergencyPhone && values.emergencyPhone.replace(/\D/g, '').length < 10) {
      nextErrors.emergencyPhone = 'Enter a valid emergency contact number.'
    }
    if (values.dateOfBirth && new Date(values.dateOfBirth) >= new Date()) {
      nextErrors.dateOfBirth = 'Date of birth must be in the past.'
    }
    setErrors(nextErrors)
    return Object.keys(nextErrors).length === 0
  }

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!validate()) {
      setSubmitted(false)
      requestAnimationFrame(() => document.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus())
      return
    }
    setSubmitted(true)
  }

  return (
    <PageContainer>
      <PageHeader
        title="Patient Registration"
        description="Create a patient identity record and capture the information needed to begin oncology care."
        actions={<Badge variant="brand">New registration</Badge>}
      />

      {submitted ? (
        <div role="status" className="mb-6 flex items-start gap-3 rounded-lg border border-success/30 bg-success-subtle px-4 py-3 text-success-strong">
          <CheckCircle2 className="mt-0.5 size-5 shrink-0" aria-hidden="true" />
          <div>
            <p className="text-sm font-semibold">Registration details validated</p>
            <p className="mt-0.5 text-xs">Demo only — no patient record has been created or transmitted.</p>
          </div>
        </div>
      ) : null}

      <form noValidate onSubmit={handleSubmit} className="space-y-6">
        <Card>
          <SectionHeading icon={UserRound} title="Patient identity" description="Use the patient’s legal identity as shown on their primary document." />
          <CardContent className="grid gap-5 pt-6 sm:grid-cols-2 lg:grid-cols-4">
            <div className="space-y-2">
              <FieldLabel htmlFor="firstName" required>First name</FieldLabel>
              <Input id="firstName" autoComplete="given-name" value={values.firstName} onChange={(e) => updateValue('firstName', e.target.value)} {...inputProps('firstName')} />
              <FieldError id="firstName-error" message={errors.firstName} />
            </div>
            <div className="space-y-2">
              <FieldLabel htmlFor="lastName" required>Last name</FieldLabel>
              <Input id="lastName" autoComplete="family-name" value={values.lastName} onChange={(e) => updateValue('lastName', e.target.value)} {...inputProps('lastName')} />
              <FieldError id="lastName-error" message={errors.lastName} />
            </div>
            <div className="space-y-2">
              <FieldLabel htmlFor="dateOfBirth" required>Date of birth</FieldLabel>
              <Input id="dateOfBirth" type="date" value={values.dateOfBirth} onChange={(e) => updateValue('dateOfBirth', e.target.value)} {...inputProps('dateOfBirth')} />
              <FieldError id="dateOfBirth-error" message={errors.dateOfBirth} />
            </div>
            <div className="space-y-2">
              <FieldLabel htmlFor="sexAtBirth" required>Sex at birth</FieldLabel>
              <select id="sexAtBirth" value={values.sexAtBirth} onChange={(e) => updateValue('sexAtBirth', e.target.value)} className={cn(selectClassName, errors.sexAtBirth && 'border-critical')} aria-invalid={Boolean(errors.sexAtBirth)}>
                <option value="">Select</option><option value="female">Female</option><option value="male">Male</option><option value="intersex">Intersex</option><option value="unknown">Unknown</option>
              </select>
              <FieldError id="sexAtBirth-error" message={errors.sexAtBirth} />
            </div>
            <div className="space-y-2 sm:col-span-2">
              <FieldLabel htmlFor="genderIdentity">Gender identity <span className="font-normal text-metadata">(optional)</span></FieldLabel>
              <Input id="genderIdentity" value={values.genderIdentity} onChange={(e) => updateValue('genderIdentity', e.target.value)} placeholder="Patient-described identity" />
            </div>
            <div className="space-y-2">
              <FieldLabel htmlFor="idType">Identity source</FieldLabel>
              <select id="idType" value={values.idType} onChange={(e) => updateValue('idType', e.target.value)} className={selectClassName}>
                <option value="hospital-referral">Hospital referral</option><option value="government-id">Government ID</option><option value="passport">Passport</option><option value="none">Not available</option>
              </select>
            </div>
            <div className="space-y-2">
              <FieldLabel htmlFor="idNumber">Reference number</FieldLabel>
              <Input id="idNumber" value={values.idNumber} onChange={(e) => updateValue('idNumber', e.target.value)} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <SectionHeading icon={ClipboardList} title="Contact and communication" description="Record reliable contact information and the patient’s communication preference." />
          <CardContent className="grid gap-5 pt-6 sm:grid-cols-2 lg:grid-cols-3">
            <div className="space-y-2"><FieldLabel htmlFor="phone" required>Primary phone</FieldLabel><Input id="phone" type="tel" autoComplete="tel" value={values.phone} onChange={(e) => updateValue('phone', e.target.value)} {...inputProps('phone')} /><FieldError id="phone-error" message={errors.phone} /></div>
            <div className="space-y-2"><FieldLabel htmlFor="alternatePhone">Alternate phone</FieldLabel><Input id="alternatePhone" type="tel" value={values.alternatePhone} onChange={(e) => updateValue('alternatePhone', e.target.value)} /></div>
            <div className="space-y-2"><FieldLabel htmlFor="email">Email</FieldLabel><Input id="email" type="email" autoComplete="email" value={values.email} onChange={(e) => updateValue('email', e.target.value)} {...inputProps('email')} /><FieldError id="email-error" message={errors.email} /></div>
            <div className="space-y-2"><FieldLabel htmlFor="preferredLanguage" required>Preferred language</FieldLabel><select id="preferredLanguage" value={values.preferredLanguage} onChange={(e) => updateValue('preferredLanguage', e.target.value)} className={cn(selectClassName, errors.preferredLanguage && 'border-critical')}><option value="">Select</option><option>English</option><option>Hindi</option><option>Kannada</option><option>Tamil</option><option>Telugu</option><option>Bengali</option><option>Marathi</option></select><FieldError id="preferredLanguage-error" message={errors.preferredLanguage} /></div>
            <div className="space-y-2 sm:col-span-2"><FieldLabel htmlFor="address" required>Address</FieldLabel><Input id="address" autoComplete="street-address" value={values.address} onChange={(e) => updateValue('address', e.target.value)} {...inputProps('address')} /><FieldError id="address-error" message={errors.address} /></div>
            <div className="space-y-2"><FieldLabel htmlFor="city" required>City</FieldLabel><Input id="city" autoComplete="address-level2" value={values.city} onChange={(e) => updateValue('city', e.target.value)} {...inputProps('city')} /><FieldError id="city-error" message={errors.city} /></div>
            <div className="space-y-2"><FieldLabel htmlFor="state" required>State</FieldLabel><Input id="state" autoComplete="address-level1" value={values.state} onChange={(e) => updateValue('state', e.target.value)} {...inputProps('state')} /><FieldError id="state-error" message={errors.state} /></div>
            <div className="space-y-2"><FieldLabel htmlFor="postalCode" required>Postal code</FieldLabel><Input id="postalCode" inputMode="numeric" autoComplete="postal-code" value={values.postalCode} onChange={(e) => updateValue('postalCode', e.target.value)} {...inputProps('postalCode')} /><FieldError id="postalCode-error" message={errors.postalCode} /></div>
          </CardContent>
        </Card>

        <Card>
          <SectionHeading icon={FileText} title="Oncology referral" description="Capture referral context only; detailed clinical assessment belongs in the intake workflow." />
          <CardContent className="grid gap-5 pt-6 sm:grid-cols-2 lg:grid-cols-3">
            <div className="space-y-2"><FieldLabel htmlFor="referringClinician">Referring clinician</FieldLabel><Input id="referringClinician" value={values.referringClinician} onChange={(e) => updateValue('referringClinician', e.target.value)} /></div>
            <div className="space-y-2 sm:col-span-2"><FieldLabel htmlFor="referringFacility">Referring facility</FieldLabel><Input id="referringFacility" value={values.referringFacility} onChange={(e) => updateValue('referringFacility', e.target.value)} /></div>
            <div className="space-y-2"><FieldLabel htmlFor="cancerType">Suspected or confirmed cancer type</FieldLabel><select id="cancerType" value={values.cancerType} onChange={(e) => updateValue('cancerType', e.target.value)} className={selectClassName}><option value="">Not specified</option><option>Breast</option><option>Lung</option><option>Gastrointestinal</option><option>Genitourinary</option><option>Gynaecologic</option><option>Head and neck</option><option>Haematologic</option><option>Other</option></select></div>
            <div className="space-y-2"><FieldLabel htmlFor="diagnosisStatus">Diagnosis status</FieldLabel><select id="diagnosisStatus" value={values.diagnosisStatus} onChange={(e) => updateValue('diagnosisStatus', e.target.value)} className={selectClassName}><option value="suspected">Suspected</option><option value="confirmed">Confirmed</option><option value="recurrence">Possible recurrence</option><option value="second-opinion">Second opinion</option></select></div>
            <div className="space-y-2 sm:col-span-2 lg:col-span-3"><FieldLabel htmlFor="reasonForReferral">Reason for referral</FieldLabel><textarea id="reasonForReferral" rows={3} value={values.reasonForReferral} onChange={(e) => updateValue('reasonForReferral', e.target.value)} className={cn(selectClassName, 'h-auto resize-y')} /></div>
          </CardContent>
        </Card>

        <Card>
          <SectionHeading icon={ShieldCheck} title="Emergency contact and acknowledgement" description="Confirm who may be contacted and acknowledge registration handling requirements." />
          <CardContent className="space-y-6 pt-6">
            <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              <div className="space-y-2"><FieldLabel htmlFor="emergencyName" required>Contact name</FieldLabel><Input id="emergencyName" value={values.emergencyName} onChange={(e) => updateValue('emergencyName', e.target.value)} {...inputProps('emergencyName')} /><FieldError id="emergencyName-error" message={errors.emergencyName} /></div>
              <div className="space-y-2"><FieldLabel htmlFor="emergencyRelationship" required>Relationship</FieldLabel><Input id="emergencyRelationship" value={values.emergencyRelationship} onChange={(e) => updateValue('emergencyRelationship', e.target.value)} {...inputProps('emergencyRelationship')} /><FieldError id="emergencyRelationship-error" message={errors.emergencyRelationship} /></div>
              <div className="space-y-2"><FieldLabel htmlFor="emergencyPhone" required>Contact phone</FieldLabel><Input id="emergencyPhone" type="tel" value={values.emergencyPhone} onChange={(e) => updateValue('emergencyPhone', e.target.value)} {...inputProps('emergencyPhone')} /><FieldError id="emergencyPhone-error" message={errors.emergencyPhone} /></div>
            </div>
            <div className="space-y-3 border-t border-divider pt-5">
              <label className="flex items-start gap-3 text-sm text-supporting"><input type="checkbox" checked={values.communicationConsent} onChange={(e) => updateValue('communicationConsent', e.target.checked)} className="mt-0.5 size-4 rounded border-input accent-primary" /><span>Patient permits appointment and care-coordination messages using the contact details above.</span></label>
              <label className={cn('flex items-start gap-3 text-sm text-supporting', errors.privacyAcknowledged && 'text-critical-strong')}><input id="privacyAcknowledged" type="checkbox" checked={values.privacyAcknowledged} onChange={(e) => updateValue('privacyAcknowledged', e.target.checked)} aria-invalid={Boolean(errors.privacyAcknowledged)} aria-describedby={errors.privacyAcknowledged ? 'privacyAcknowledged-error' : undefined} className="mt-0.5 size-4 rounded border-input accent-primary" /><span>I confirm that the patient was informed how registration information will be used and that the details above were reviewed for accuracy. <span className="text-critical" aria-hidden="true">*</span></span></label>
              <FieldError id="privacyAcknowledged-error" message={errors.privacyAcknowledged} />
            </div>
          </CardContent>
        </Card>

        <div className="sticky bottom-0 z-10 -mx-4 flex flex-col-reverse gap-3 border-t border-border bg-surface/95 px-4 py-4 shadow-soft backdrop-blur-sm sm:mx-0 sm:flex-row sm:items-center sm:justify-between sm:rounded-lg sm:border">
          <p className="text-xs text-metadata"><span className="text-critical">*</span> Required fields · Demo submission is not persisted</p>
          <div className="flex gap-2">
            <Button type="button" variant="secondary" onClick={() => { setValues(initialValues); setErrors({}); setSubmitted(false) }}>Reset demo</Button>
            <Button type="submit">Validate registration</Button>
          </div>
        </div>
      </form>
    </PageContainer>
  )
}
