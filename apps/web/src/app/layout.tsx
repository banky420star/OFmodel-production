import type { Metadata, Viewport } from 'next'
import './globals.css'
import Sidebar from '@/components/Sidebar'
import RegisterSW from '@/components/RegisterSW'

export const metadata: Metadata = {
  title: 'Persona Studio',
  description: 'Create and operate versioned fictional synthetic creator identities with controlled workflows.',
  manifest: '/manifest.webmanifest',
  icons: {
    icon: [
      { url: '/icon-192.png', sizes: '192x192', type: 'image/png' },
      { url: '/icon-512.png', sizes: '512x512', type: 'image/png' },
    ],
    apple: '/icon-192.png',
  },
  appleWebApp: {
    capable: true,
    title: 'Persona Studio',
    statusBarStyle: 'black-translucent',
  },
}

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: '#0d0d10',
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
        <RegisterSW />
      </body>
    </html>
  )
}