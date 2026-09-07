import React from 'react'

const svg = (viewBox: string, children: React.ReactNode) => (
  <svg width="18" height="18" viewBox={viewBox} fill="none" stroke="currentColor"
    strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    {children}
  </svg>
)

export const Icons = {
  users: svg('0 0 24 24', <><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><path d="M16 3.128a4 4 0 0 1 0 7.744"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><circle cx="9" cy="7" r="4"/></>),
  package: svg('0 0 24 24', <><path d="M12 22V12"/><path d="m16 17 2 2 4-4"/><path d="M21 11.127V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.729l7 4a2 2 0 0 0 2 .001l1.32-.753"/><path d="M3.29 7 12 12l8.71-5"/><path d="m7.5 4.27 8.997 5.148"/></>),
  dollar: svg('0 0 24 24', <><circle cx="12" cy="12" r="10"/><path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"/><path d="M12 18V6"/></>),
  trending: svg('0 0 24 24', <><path d="M16 7h6v6"/><path d="m22 7-8.5 8.5-5-5L2 17"/></>),
  activity: svg('0 0 24 24', <path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"/>),
  shield: svg('0 0 24 24', <><path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/></>),
  refresh: svg('0 0 24 24', <><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></>),
  image: svg('0 0 24 24', <><rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/></>),
  cpu: svg('0 0 24 24', <><path d="M12 20v2"/><path d="M12 2v2"/><path d="M17 20v2"/><path d="M17 2v2"/><path d="M2 12h2"/><path d="M2 17h2"/><path d="M2 7h2"/><path d="M20 12h2"/><path d="M20 17h2"/><path d="M20 7h2"/><path d="M7 20v2"/><path d="M7 2v2"/><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="8" y="8" width="8" height="8" rx="1"/></>),
  database: svg('0 0 24 24', <><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5V19A9 3 0 0 0 21 19V5"/><path d="M3 12A9 3 0 0 0 21 12"/></>),
  search: svg('0 0 24 24', <><path d="m21 21-4.34-4.34"/><circle cx="11" cy="11" r="8"/></>),
  plus: svg('0 0 24 24', <><path d="M5 12h14"/><path d="M12 5v14"/></>),
  arrowRight: svg('0 0 24 24', <><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></>),
  arrowLeft: svg('0 0 24 24', <><path d="M19 12H5"/><path d="m12 19-7-7 7-7"/></>),
  menu: svg('0 0 24 24', <><path d="M4 5h16"/><path d="M4 12h16"/><path d="M4 19h16"/></>),
  check: svg('0 0 24 24', <path d="M20 6 9 17l-5-5"/>),
}
