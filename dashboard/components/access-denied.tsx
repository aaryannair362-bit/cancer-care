import { ShieldX } from 'lucide-react'
import { PageContainer } from '@/components/shell/page-container'
import { Card, CardContent } from '@/components/ui/card'

export function AccessDenied({ role }: { role: string }) { return <PageContainer><Card className="mx-auto max-w-2xl"><CardContent className="flex gap-4 p-6"><span className="flex size-10 shrink-0 items-center justify-center rounded-md bg-critical-subtle text-critical-strong"><ShieldX className="size-5" /></span><div><h2 className="font-display text-xl font-semibold">Access restricted</h2><p className="mt-2 text-sm text-metadata">The {role} role is not permitted to open this workspace.</p></div></CardContent></Card></PageContainer> }
