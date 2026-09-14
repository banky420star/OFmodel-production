# Account Lifecycle Design — Instagram (human-first, API-operated)

_Status: DESIGN, adopted 2026-09-13. Execution pending owner action. See also
`ARCHITECTURE_VISION.md` (system invariants) and `apps/api/INSTAGRAM_STATUS.md`
(run history)._

## Why this design

Three automated signups (`@trainerpath.official`, `@trainerpath.assist`,
`@trainerpath.studio`) were each suspended by Instagram within seconds of
creation. The platform prohibits automated account creation and flags exactly
this pattern; recurring evidence says the environment (datacenter-adjacent IP,
fresh automated browser profiles, mail.tm addresses) is the problem, not our
code. Fake-user-detection evasion (warmed profiles, humanized input, proxies)
is out of scope by design — it breaks platform rules and puts the owner's
devices and other accounts at risk.

The design below is the durable alternative: **humans create accounts; the
Studio operates them through Instagram's official APIs.** This is the same
structure every legitimate social-management product uses, it is what Meta's
developer platform exists for, and it converts signup from a per-account
gamble into a one-time 10-minute operator task.

## Design principles

1. **Human-first creation.** The operator (you) signs up in a normal browser,
   from a normal residential connection, at human speed — with phone/email you
   control. The Studio prepares everything around that act; it never performs it.
2. **API-first operation.** After creation, everything programmatic — posting,
   insights, commenting flows — goes through the Instagram Graph API with the
   operator's explicit OAuth grant. No browser bots against live accounts.
3. **One human session per account, front-loaded.** The costly human part
   (identity, KYC-grade verification, professional-account conversion, OAuth)
   happens once, in a single guided session. After that, the Studio needs
   nothing from the browser.
4. **Honest state machine.** The Studio tracks each account through explicit
   states; nothing is marked active until the API grant succeeds, and walls
   (phone verification, selfie checks) are recorded as blockers, never bypassed.

## The guided signup session (per account, ~10 minutes, once)

The Studio provides a checklist UI (Socials page, per account) that walks the
operator through:

1. **Identity pack displayed**: the persona's handle, display name, bio,
   profile image (from the identity-locked gallery), content theme.
2. **Operator acts**: signs up in their own browser with their own email/phone,
   completes whatever checks Instagram presents, applies the profile details.
3. **Operator converts to a Professional (Business) account** in settings —
   required for API publishing.
4. **Operator connects the account to a Facebook Page** (Meta requirement for
   Graph API publishing) — same session.
5. **OAuth grant**: operator clicks the Studio's "Connect" button, authorizes
   the app (scopes: `instagram_basic`, `instagram_content_publish`,
   `pages_show_list`, `pages_read_engagement`, `business_management`),
   and the Studio stores the long-lived token server-side.

Completion criteria (all machine-checked): token stored → `GET /me` +
`GET /{ig-user-id}` succeed → account state flips to `api_connected`.
Until then the row stays `pending_connection`, whatever the UI optimism.

## What the Studio builds for this (small, honest scope)

- **Meta app registration guide** (one-time): app id/secret + OAuth redirect
  into the Studio's settings page; tokens encrypted at rest.
- **`/socials/{id}/connect/start` + `/connect/callback`**: the OAuth handshake.
- **Publisher**: scheduled posts via `POST /{ig-user-id}/media` +
  `media_publish`, with the existing identity-locked, QA'd assets — provenance
  (`is_mock`) recorded per post as everywhere else.
- **Insights ingest**: daily pull of followers/reach/engagement into the
  existing analytics tables — feeding the measurement loop
  (`StrategyHypothesis.result`) with real per-post data.
- **Rate-limit honesty**: Graph API caps (50 posts/24h, per-endpoint
  insights quotas) enforced in the scheduler, surfaced in provider health.

## What deliberately does not exist

- No warmed/residential browser automation for signup or operation.
- No input humanization, fingerprint management, or proxy rotation.
- No mass account creation; accounts enter the system only via operator
  sessions, at operator pace.
- No third-party engagement pods, follow bots, or growth automation.

## Relationship to the rest of the architecture

- The **Fanvue Platform Manager** (built, previous cycle) already runs this
  model: compliance fields, inventory ladder, permitted-platform operations.
  Instagram professional accounts slot into the same ladder via
  `platform_accounts` once connected (a `platform` value, nothing more).
- The **measurement loop** gets real Instagram per-post performance through
  official insights — the data quality the hypothesis engine needs.
- If the owner prefers not to maintain Meta app review, Fanvue remains the
  primary monetization surface; Instagram then serves as a public-tier
  acquisition channel operated manually or via API once connected.

## Immediate next actions (owner)

1. Decide: maintain a Meta developer app (one review cycle) — yes/no.
2. If yes: register the app, add the Studio redirect URL, create the next
   account by hand following the checklist above.
3. Studio work queued: OAuth routes + publisher + insights ingest (build
   order item 5 in ARCHITECTURE_VISION.md, which unblocks on this decision).
