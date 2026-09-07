'use client'

import { useEffect, useState, useMemo, useCallback } from 'react'
import { listPersonas, getSchedule } from '@/lib/api'
import { Icons } from '@/lib/icons'
import { StatusBadge } from '@/components/ui/StatusBadge'

interface ScheduledPost {
  id: string
  persona_id: string
  platform: string
  scheduled_at: string
  status: string
  posted_at: string | null
}

interface PostWithPersona extends ScheduledPost {
  persona_name: string
  persona_id: string
}

const PLATFORM_COLORS: Record<string, string> = {
  instagram: '#e05656',
  tiktok: '#5b9cf6',
  youtube: '#e05656',
}

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

function getDaysInMonth(year: number, month: number) {
  return new Date(year, month + 1, 0).getDate()
}

function getFirstDayOfMonth(year: number, month: number) {
  return new Date(year, month, 1).getDay()
}

function formatDateKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function isSameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}

export default function CalendarPage() {
  const [posts, setPosts] = useState<PostWithPersona[]>([])
  const [loading, setLoading] = useState(true)
  const [currentMonth, setCurrentMonth] = useState(() => {
    const now = new Date()
    return { year: now.getFullYear(), month: now.getMonth() }
  })
  const [selectedDay, setSelectedDay] = useState<Date | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)

  useEffect(() => {
    ;(async () => {
      try {
        const personas = await listPersonas() as any[]
        const allPosts: PostWithPersona[] = []
        for (const p of personas) {
          try {
            const sched = await getSchedule(p.id) as ScheduledPost[]
            for (const s of sched) {
              allPosts.push({ ...s, persona_name: p.name })
            }
          } catch {}
        }
        allPosts.sort((a, b) => new Date(a.scheduled_at).getTime() - new Date(b.scheduled_at).getTime())
        setPosts(allPosts)
      } catch {}
      setLoading(false)
    })()
  }, [])

  const postsByDate = useMemo(() => {
    const map: Record<string, PostWithPersona[]> = {}
    for (const post of posts) {
      const key = formatDateKey(new Date(post.scheduled_at))
      if (!map[key]) map[key] = []
      map[key].push(post)
    }
    return map
  }, [posts])

  const goToPrevMonth = useCallback(() => {
    setCurrentMonth(({ year, month }) => {
      if (month === 0) return { year: year - 1, month: 11 }
      return { year, month: month - 1 }
    })
  }, [])

  const goToNextMonth = useCallback(() => {
    setCurrentMonth(({ year, month }) => {
      if (month === 11) return { year: year + 1, month: 0 }
      return { year, month: month + 1 }
    })
  }, [])

  const goToToday = useCallback(() => {
    const now = new Date()
    setCurrentMonth({ year: now.getFullYear(), month: now.getMonth() })
    setSelectedDay(now)
    setDetailOpen(true)
  }, [])

  const daysInMonth = getDaysInMonth(currentMonth.year, currentMonth.month)
  const firstDay = getFirstDayOfMonth(currentMonth.year, currentMonth.month)
  const today = new Date()
  const monthName = new Date(currentMonth.year, currentMonth.month).toLocaleString('en-US', { month: 'long', year: 'numeric' })

  const calendarDays: (number | null)[] = []
  for (let i = 0; i < firstDay; i++) calendarDays.push(null)
  for (let d = 1; d <= daysInMonth; d++) calendarDays.push(d)

  const selectedPosts = selectedDay
    ? postsByDate[formatDateKey(selectedDay)] || []
    : []

  return (
    <main className="workspace">
      <header className="topbar">
        <button className="mobile-menu" aria-label="Open menu">{Icons.menu}</button>
        <div className="crumb">
          <a href="/"><span>Persona Studio</span></a><b>/</b><strong>Calendar</strong>
        </div>
      </header>
      <div className="content">
        {/* Header row */}
        <div className="cal-header">
          <div>
            <h1 style={{ fontSize: 22, fontWeight: 600, marginBottom: 2 }}>Content Calendar</h1>
            <p className="muted-md">{monthName} · {posts.length} scheduled posts across all personas</p>
          </div>
          <div className="cal-nav">
            <button className="cal-nav-btn" onClick={goToPrevMonth} aria-label="Previous month">{Icons.arrowLeft || '←'}</button>
            <button className="cal-nav-btn cal-today-btn" onClick={goToToday}>Today</button>
            <button className="cal-nav-btn" onClick={goToNextMonth} aria-label="Next month">{Icons.arrowRight || '→'}</button>
          </div>
        </div>

        {loading ? (
          <p className="muted-md" style={{ padding: '48px 0', textAlign: 'center' }}>Loading calendar…</p>
        ) : (
          <div className="cal-layout">
            {/* Calendar grid */}
            <div className="cal-grid-wrap">
              <div className="cal-weekdays">
                {WEEKDAYS.map(day => (
                  <div key={day} className="cal-weekday">{day}</div>
                ))}
              </div>
              <div className="cal-grid">
                {calendarDays.map((day, i) => {
                  if (day === null) return <div key={`empty-${i}`} className="cal-cell cal-cell-empty" />
                  const date = new Date(currentMonth.year, currentMonth.month, day)
                  const key = formatDateKey(date)
                  const dayPosts = postsByDate[key] || []
                  const isToday = isSameDay(date, today)
                  const isSelected = selectedDay && isSameDay(date, selectedDay)

                  return (
                    <button
                      key={key}
                      className={`cal-cell ${isToday ? 'cal-cell-today' : ''} ${isSelected ? 'cal-cell-selected' : ''} ${dayPosts.length > 0 ? 'cal-cell-has-posts' : ''}`}
                      onClick={() => { setSelectedDay(date); setDetailOpen(true) }}
                    >
                      <span className={`cal-day-num ${isToday ? 'cal-day-num-today' : ''}`}>{day}</span>
                      <div className="cal-dots">
                        {dayPosts.slice(0, 4).map((p, j) => (
                          <span
                            key={j}
                            className="cal-dot"
                            style={{ background: PLATFORM_COLORS[p.platform] || 'var(--text-muted)' }}
                            title={`${p.persona_name} · ${p.platform}`}
                          />
                        ))}
                        {dayPosts.length > 4 && <span className="cal-more">+{dayPosts.length - 4}</span>}
                      </div>
                      {dayPosts.length > 0 && (
                        <div className="cal-chips">
                          {dayPosts.slice(0, 2).map((p, j) => (
                            <div
                              key={j}
                              className="cal-chip"
                              style={{ borderColor: PLATFORM_COLORS[p.platform] || 'var(--border)' }}
                            >
                              <span className="cal-chip-dot" style={{ background: PLATFORM_COLORS[p.platform] || 'var(--text-muted)' }} />
                              <span className="cal-chip-text">{p.persona_name}</span>
                            </div>
                          ))}
                          {dayPosts.length > 2 && (
                            <span className="cal-chip-more">+{dayPosts.length - 2} more</span>
                          )}
                        </div>
                      )}
                    </button>
                  )
                })}
              </div>
            </div>

            {/* Day detail sidebar */}
            {detailOpen && (
              <div className={`cal-detail ${detailOpen ? 'open' : ''}`}>
                <div className="cal-detail-header">
                  <div>
                    <h3 style={{ fontSize: 16, fontWeight: 600 }}>
                      {selectedDay
                        ? selectedDay.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })
                        : 'Select a day'}
                    </h3>
                    {selectedDay && (
                      <p className="muted-sm" style={{ marginTop: 2 }}>
                        {selectedPosts.length === 0
                          ? 'No posts scheduled'
                          : `${selectedPosts.length} post${selectedPosts.length !== 1 ? 's' : ''} scheduled`}
                      </p>
                    )}
                  </div>
                  <button className="cal-close-btn" onClick={() => setDetailOpen(false)} aria-label="Close">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M18 6 6 18M6 6l12 12"/></svg>
                  </button>
                </div>

                {selectedPosts.length === 0 ? (
                  <div className="cal-empty">
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--text-muted)" strokeWidth="1.5" strokeLinecap="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>
                    <p>Nothing scheduled for this day.</p>
                  </div>
                ) : (
                  <div className="cal-detail-list">
                    {selectedPosts.map(post => (
                      <div key={post.id} className="cal-detail-item">
                        <div className="cal-detail-item-left">
                          <span
                            className="cal-detail-platform"
                            style={{ background: PLATFORM_COLORS[post.platform] || 'var(--text-muted)' }}
                          />
                          <div>
                            <div style={{ fontSize: 13, fontWeight: 500 }}>{post.persona_name}</div>
                            <div className="muted-sm" style={{ textTransform: 'capitalize' }}>{post.platform}</div>
                          </div>
                        </div>
                        <div style={{ textAlign: 'right' }}>
                          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                            {new Date(post.scheduled_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}
                          </div>
                          <StatusBadge status={post.status} />
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* Legend */}
        <div className="cal-legend">
          {Object.entries(PLATFORM_COLORS).map(([platform, color]) => (
            <span key={platform} className="cal-legend-item">
              <span className="cal-legend-dot" style={{ background: color }} />
              <span style={{ textTransform: 'capitalize' }}>{platform}</span>
            </span>
          ))}
        </div>
      </div>
    </main>
  )
}
