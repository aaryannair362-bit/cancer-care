import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'
import { AppShell } from '@/components/shell/app-shell'
import { DemoAccessProvider } from '@/components/demo-access-provider'
import { OncologyProvider } from '@/lib/oncology/store'
import { DocumentsProvider } from '@/lib/documents/store'

// Inter = clinical / UI typeface (body, labels, forms, tables, metadata)
const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
  display: 'swap',
})

export const metadata: Metadata = {
  title: 'Aivana',
  description: 'Clinical workflow operating system for oncology care',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" suppressHydrationWarning className={inter.variable}>
      <body className="min-h-screen bg-background font-sans text-foreground antialiased">
        <DemoAccessProvider><OncologyProvider><DocumentsProvider><AppShell>{children}</AppShell></DocumentsProvider></OncologyProvider></DemoAccessProvider>
      </body>
    </html>
  )
}
