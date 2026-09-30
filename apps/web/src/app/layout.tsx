import type { Metadata } from 'next'
import './globals.css'
import Sidebar from '@/components/Sidebar'
import BootCheck from '@/components/BootCheck'

export const metadata: Metadata = {
  title: 'Persona Studio',
  description: 'Create and operate versioned fictional synthetic creator identities with controlled workflows.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;600;700&display=swap" rel="stylesheet" />
      </head>
      <body>
        <div className="app-shell">
          <Sidebar />
          {children}
        </div>
        {/* Mounted outside the shell so it overlays the whole app. In the
            layout because it is a whole-app concern, not a page's — it guards
            itself to one run per tab session. See BootCheck.tsx. */}
        <BootCheck />
      </body>
    </html>
  )
}
