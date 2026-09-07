'use client'

import { useEffect, useState } from 'react'
import { usePathname } from 'next/navigation'
import { getDashboardSummary } from '@/lib/api'
import type { DashboardSummary } from '@/lib/types'

interface NavItem {
  label: string
  href: string
  icon: React.ReactNode
  badge?: number
}

const sparkles = (
  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11.017 2.814a1 1 0 0 1 1.966 0l1.051 5.558a2 2 0 0 0 1.594 1.594l5.558 1.051a1 1 0 0 1 0 1.966l-5.558 1.051a2 2 0 0 0-1.594 1.594l-1.051 5.558a1 1 0 0 1-1.966 0l-1.051-5.558a2 2 0 0 0-1.594-1.594l-5.558-1.051a1 1 0 0 1 0-1.966l5.558-1.051a2 2 0 0 0 1.594-1.594z"/><path d="M20 2v4"/><path d="M22 4h-4"/><circle cx="4" cy="20" r="2"/></svg>
)

const icon = (d: React.ReactNode) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">{d}</svg>
)

const icons = {
  overview: icon(<><rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/></>),
  models: icon(<><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><path d="M16 3.128a4 4 0 0 1 0 7.744"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><circle cx="9" cy="7" r="4"/></>),
  production: icon(<><path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72"/><path d="m14 7 3 3"/><path d="M5 6v4"/><path d="M19 14v4"/><path d="M10 2v2"/><path d="M7 8H3"/><path d="M21 16h-4"/><path d="M11 3H9"/></>),
  calendar: icon(<><path d="M8 2v3"/><path d="M16 2v3"/><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M8 13h.01"/><path d="M12 13h.01"/><path d="M16 13h.01"/><path d="M8 17h.01"/><path d="M12 17h.01"/><path d="M16 17h.01"/></>),
  analytics: icon(<><path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/></>),
  registry: icon(<><path d="M4 10c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h4c1.1 0 2 .9 2 2"/><path d="M10 16c-1.1 0-2-.9-2-2v-4c0-1.1.9-2 2-2h4c1.1 0 2 .9 2 2"/><rect width="8" height="8" x="14" y="14" rx="2"/></>),
  rights: icon(<><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/></>),
  chat: icon(<><path d="M7.9 20A9 9 0 1 0 4 16.1L2 22z"/></>),
  settings: icon(<><path d="M9.671 4.136a2.34 2.34 0 0 1 4.659 0 2.34 2.34 0 0 0 3.319 1.915 2.34 2.34 0 0 1 2.33 4.033 2.34 2.34 0 0 0 0 3.831 2.34 2.34 0 0 1-2.33 4.033 2.34 2.34 0 0 0-3.319 1.915 2.34 2.34 0 0 1-4.659 0 2.34 2.34 0 0 0-3.32-1.915 2.34 2.34 0 0 1-2.33-4.033 2.34 2.34 0 0 0 0-3.831A2.34 2.34 0 0 1 6.35 6.051a2.34 2.34 0 0 0 3.319-1.915"/><circle cx="12" cy="12" r="3"/></>),
  ellipsis: icon(<><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/></>),
}

export default function Sidebar() {
  const pathname = usePathname()
  const [shootCount, setShootCount] = useState(0)
  const [healthChecks, setHealthChecks] = useState<{ service: string; status: string }[]>([])

  useEffect(() => {
    getDashboardSummary()
      .then((data: DashboardSummary) => {
        setShootCount(data.total_shoots || 0)
        setHealthChecks(data.health?.checks || [])
      })
      .catch(() => {})
  }, [])

  const isActive = (href: string) => {
    if (href === '/') return pathname === '/'
    return pathname.startsWith(href)
  }

  const safeChecks = Array.isArray(healthChecks) ? healthChecks : []
  const onlineCount = safeChecks.filter(c => c.status === 'green').length
  const totalCount = safeChecks.length || 1

  // Show top 3 services in mini-health, cycling through available ones
  const miniServices = safeChecks.slice(0, 3)

  const workspaceItems: NavItem[] = [
    { label: 'Overview', href: '/', icon: icons.overview },
    { label: 'Models', href: '/models', icon: icons.models },
    { label: 'Production', href: '/production', icon: icons.production, badge: shootCount },
    { label: 'Chat', href: '/chat', icon: icons.chat },
    { label: 'Mailboxes', href: '/mailbox', icon: icon(<><rect width="20" height="14" x="2" y="5" rx="2"/><path d="M22 5v14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></>) },
    { label: 'Calendar', href: '/calendar', icon: icons.calendar },
    { label: 'Analytics', href: '/analytics', icon: icons.analytics },
  ]

  const systemItems: NavItem[] = [
    { label: 'Model registry', href: '/models', icon: icons.registry },
    { label: 'Rights & consent', href: '/settings', icon: icons.rights },
    { label: 'Settings', href: '/settings', icon: icons.settings },
  ]

  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">{sparkles}</span>
        <span>Persona<span>Studio</span></span>
      </div>

      <button className="sidebar-close" aria-label="Close menu">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
      </button>

      <nav className="main-nav" aria-label="Primary navigation">
        <p>Workspace</p>
        {workspaceItems.map(item => (
          <a key={item.href + item.label} href={item.href}>
            <button className={isActive(item.href) ? 'active' : ''}>
              {item.icon}
              <span>{item.label}</span>
              {item.badge != null && item.badge > 0 && <b>{item.badge}</b>}
            </button>
          </a>
        ))}

        <p>System</p>
        {systemItems.map(item => (
          <a key={item.label} href={item.href}>
            <button className={isActive(item.href) ? 'active' : ''}>
              {item.icon}
              <span>{item.label}</span>
            </button>
          </a>
        ))}
      </nav>

      <div className="system-card">
        <div>
          <span className="pulse-dot"></span>
          <p>
            <b>{onlineCount === totalCount ? 'System operational' : `${totalCount - onlineCount} service${totalCount - onlineCount !== 1 ? 's' : ''} degraded`}</b>
            <small>{onlineCount} of {totalCount} services online</small>
          </p>
        </div>
        <div className="mini-health">
          {miniServices.map(check => (
            <span key={check.service}>{check.service}</span>
          ))}
          {miniServices.map(check => (
            <i key={check.service + '-dot'} className={check.status !== 'green' ? 'warn' : ''}></i>
          ))}
        </div>
      </div>

      <div className="profile">
        <div className="profile-avatar">PS</div>
        <p><b>Persona Studio</b><small>Workspace</small></p>
        {icons.ellipsis}
      </div>
    </aside>
  )
}
