'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { logout, me, type FanPersona } from '@/lib/fanApi'

// The fan shell.
//
// `position: fixed; inset: 0` rather than another flex child: the root layout
// wraps every page in `.app-shell` alongside the operator `<Sidebar/>`, and
// Sidebar returns null on /fan. Left as a normal flex child, the fan surface
// would sit next to an empty sidebar with its width reserved. Covering the
// viewport completely is simpler than making two shells negotiate, and it means
// no operator chrome can leak into a fan's page by accident.
//
// The signed-out state is handled here too: the landing page is public, every
// other fan page needs a session, and the redirect happens in one place.

const NAV = [
  { href: '/fan/chat', label: 'Chat' },
  { href: '/fan/content', label: 'Content' },
  { href: '/fan/wallet', label: 'Wallet' },
]

export default function FanLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const [persona, setPersona] = useState<FanPersona | null>(null)
  const [signedIn, setSignedIn] = useState<boolean | null>(null)

  const isLanding = pathname === '/fan'

  useEffect(() => {
    let alive = true
    me()
      .then((data) => {
        if (!alive) return
        setPersona(data.persona)
        setSignedIn(true)
      })
      .catch(() => {
        if (!alive) return
        setSignedIn(false)
        if (!isLanding) router.replace('/fan')
      })
    return () => {
      alive = false
    }
  }, [pathname, isLanding, router])

  async function signOut() {
    try {
      await logout()
    } catch {
      // A failed logout that still drops the local view is better than a fan
      // stuck staring at a page they asked to leave.
    }
    setSignedIn(false)
    setPersona(null)
    router.replace('/fan')
  }

  return (
    <div className="fan-shell">
      {!isLanding && (
        <header className="fan-topbar">
          <Link href="/fan/chat" className="fan-brand">
            {persona?.avatar_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={persona.avatar_url} alt="" className="fan-avatar-xs" />
            ) : (
              <span className="fan-avatar-xs fan-avatar-fallback">
                {persona?.name?.[0] ?? '·'}
              </span>
            )}
            <span className="fan-brand-name">{persona?.name ?? 'Loading…'}</span>
            <span className="fan-ai-tag" title="This creator is an AI, not a person">
              AI
            </span>
          </Link>

          <nav className="fan-nav">
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={pathname.startsWith(item.href) ? 'active' : ''}
              >
                {item.label}
              </Link>
            ))}
          </nav>

          {signedIn && (
            <button className="fan-signout" onClick={signOut}>
              Sign out
            </button>
          )}
        </header>
      )}

      <main className="fan-main">{children}</main>
    </div>
  )
}
