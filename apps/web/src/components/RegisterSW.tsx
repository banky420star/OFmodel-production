'use client'

import { useEffect } from 'react'

// Registers the minimal service worker so the app installs as a standalone
// app (dock/home-screen icon, own window) instead of a browser tab.
export default function RegisterSW() {
  useEffect(() => {
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch(() => {})
    }
  }, [])
  return null
}