# Installable PWA Shell — Design

**Date:** 2026-09-11
**Status:** Approved for planning

## Problem

ScholarsCrib is a PWA in name only. `src/app/manifest.ts` exists, but it
ships a single 32x32 SVG icon, a hardcoded white `theme_color`, and
`start_url: "/"`. There is no service worker, so:

- The app is not installable in Chrome on Android. The installability check
  requires a raster icon of at least 192x192; an SVG-only icon list fails it.
  Students cannot add ScholarsCrib to a home screen at all.
- Every navigation is a cold network round trip. The audience is Nigerian
  secondary-school students on metered, intermittent mobile data, where a
  dropped connection means the browser's default error page.
- The installed experience would fight the app's own theming. `globals.css`
  defines a complete light/dark token system keyed on `data-theme`, while
  `generateViewport` returns `themeColor: "#ffffff"` unconditionally and the
  manifest's `background_color` is `#ffffff`. On a dark device the splash
  screen and status bar flash white against a `#070d1f` app.

## Goal

Make ScholarsCrib installable, fast on repeat visits, and graceful when the
network drops — without caching a single byte of authenticated content.

**Non-goals**, each a separate project: web push notifications, offline
access to study content, offline quiz attempts with background sync, and
general performance work (font loading, LCP, bundle size). This spec covers
PWA mechanics only.

## Constraint that shapes everything: nothing authenticated is cached

Shared phones are normal in this market. A cached HTML page keyed to one
student, served to whoever opens the browser next, is a cross-account data
leak. So the caching policy is an allowlist, not a denylist: a response is
cached only if its URL matches a rule that explicitly permits it.

The same reasoning gates `/practice` and the exam routes to network-only.
`src/components/assessment/exam-state.ts` assumes the server mediates exam
state; a cached exam shell that boots offline undermines that assumption.

## Architecture

Six new files, four modified.

### New

```
public/sw.js                                     classic service worker
public/sw-policy.js                              pure routing decision
src/components/pwa/service-worker-registrar.tsx  registration, in root layout
src/components/pwa/install-banner.tsx            dashboard install invitation
src/app/offline/page.tsx                         offline fallback screen
scripts/generate-pwa-icons.mts                   icon generation (not in build)
```

### Modified

```
src/app/manifest.ts          full manifest fields, raster + maskable icons
src/app/layout.tsx           theme-aware viewport, registrar mount
src/lib/public-routes.ts     PWA assets must be anonymously fetchable
next.config.ts               headers() for the service worker files
```

### Why a classic service worker, hand-written

**Hand-written over Serwist:** Serwist currently requires webpack
configuration. This project builds on Next 16 with Turbopack. The caching
policy here is roughly 120 lines and every rule is one we need to reason
about explicitly for the leak constraint above; a recipe library buys little
and costs a build-system conflict.

**Classic over `{ type: "module" }`:** module service workers still exclude a
slice of older Android Chrome and Safari. That slice is disproportionately
this audience.

### Why the policy lives in its own file

`public/sw-policy.js` is a classic script exposing `chooseStrategy(url,
request)`. `public/sw.js` pulls it in with `importScripts()`. Tests load it
through `node:vm` and assert on the same function.

This mirrors the existing `src/lib/public-routes.ts` +
`scripts/test-public-routes.mts` pairing: the security-relevant decision is a
pure function with direct test coverage, rather than logic embedded in an
event handler that can only be exercised in a browser.

## Caching policy

`chooseStrategy` returns one of `"cache-first"`, `"stale-while-revalidate"`,
`"network-first"`, or `"network-only"`.

| Request | Strategy | Rationale |
|---|---|---|
| `/_next/static/*`, Google font files, `/icon-*.png`, `/logo-on-*.png` | cache-first | Content-hashed or stable; immutable by construction |
| `/_next/image*`, `/questions/*`, `/resources/*` | stale-while-revalidate, entry-count capped | Heavy on metered data, tolerant of staleness |
| Public GET navigations: `/`, `/about`, `/contact`, `/learn/*`, `/past-questions/*` | network-first, falling back to cache, then `/offline` | Fresh when online, readable when not |
| Authenticated GET navigations: `/dashboard`, `/classroom/*`, `/flashcards`, `/library`, `/performance`, `/study-plan`, `/achievements`, `/settings/*` | network-only, `/offline` on failure | Never stored — see the leak constraint |
| `/practice/*` and exam routes | network-only, `/offline` on failure | Exam integrity |
| `/api/*`, `/admin/*` | network-only, untouched | Mutations and auth never see a cache |
| Any non-GET request | network-only, untouched | Same |
| Cross-origin requests | network-only, untouched | Not ours to cache |

The public-navigation rule reuses the same path shapes as
`PUBLIC_PATH_PREFIXES`. The two lists are intentionally separate — one is an
auth boundary, the other a caching boundary — but a divergence between them
is a bug, and the tests assert the overlap.

## Cache lifecycle and updates

**Two caches, only one versioned.**

- `scholarscrib-shell-v<N>` — precached on `install`: `/offline`, the icons,
  and nothing else. Purged on `activate` when the version differs.
- `scholarscrib-assets` — unversioned, holds content-hashed chunks and
  images, trimmed by entry count.

Versioning only the shell is deliberate. Purging hashed `/_next/static`
chunks on every deploy is how an already-open tab starts throwing
`ChunkLoadError` mid-quiz; hashed URLs never collide, so old entries are
harmless and aging out by count is sufficient.

**No `skipWaiting`, no `clients.claim`.** A new worker waits until every tab
closes. Swapping the worker under a student mid-exam is a worse failure than
a slightly stale shell. The registrar calls `registration.update()` on
`visibilitychange` so a new version is picked up on the next cold open, and
deliberately does *not* reload on `controllerchange` — a silent reload
discards in-progress answers.

## Manifest and icons

`scripts/generate-pwa-icons.mts` renders from the existing `src/app/icon.svg`
using `sharp` (already resolvable through Next, so no new dependency) and
writes to `public/`:

- `icon-192.png`, `icon-512.png` — `purpose: "any"`, the rounded tile as-is
- `icon-192-maskable.png`, `icon-512-maskable.png` — full-bleed `#1d4ed8`
  with the cap inset to the 80% safe zone, `purpose: "maskable"`, so Android
  does not crop the tile into a bordered blob

Output is committed. The script is not wired into `build`: it exists so the
icons are reproducible when branding changes, not as a build-time dependency.
The existing 180x180 `src/app/apple-icon.png` is already correct and stays.

Manifest gains `id: "/"`, `scope: "/"`, `orientation: "portrait"`,
`categories: ["education"]`, `lang: "en-NG"`, `dir: "ltr"`, the four raster
icons (SVG kept first so desktop gets the vector), and:

- `start_url: "/dashboard?source=pwa"` — an installed student lands on their
  dashboard, not the marketing page. The query parameter lets analytics
  separate installed sessions later. Unauthenticated opens redirect to
  `/login` through the existing auth boundary, which is correct behaviour.
- `background_color: "#f8fafc"` — matches `--app-background`, so the splash
  screen does not flash pure white before first paint.

`generateViewport` in `src/app/layout.tsx` returns the media-array form of
`themeColor`: `#f8fafc` for light, `#070d1f` for dark, matching the tokens
exactly. The manifest's single `theme_color` takes the light value, since a
manifest accepts only one.

## Install UX

`src/components/pwa/install-banner.tsx`, rendered on `/dashboard` only:

- Captures `beforeinstallprompt`, prevents the default mini-infobar, stores
  the deferred event, and shows a dismissible card that calls `prompt()`.
- On iOS Safari, which has no `beforeinstallprompt`, detects iOS plus
  non-standalone display and shows the Share → Add to Home Screen
  instruction instead.
- Renders nothing when `matchMedia("(display-mode: standalone)")` matches.
- Persists dismissal in `localStorage` under a versioned key, every read and
  write wrapped in `try`/`catch` — private mode throws on access.

Public and marketing pages stay clean; the invitation targets signed-in
students, who are the ones likely to keep the app.

## Auth boundary

`/sw.js`, `/sw-policy.js`, `/offline`, and `/icon-*.png` must be fetchable
anonymously. Without this the service worker registration 307s to `/login`
and the entire feature silently does nothing — the most likely way this ships
broken.

`src/lib/public-routes.ts` gains these entries;
`scripts/test-public-routes.mts` gains both the positive assertions and
negative ones proving the gated trees stay closed.

## Headers

A `headers()` block in `next.config.ts` scoped to `/sw.js` and
`/sw-policy.js` only:

- `Cache-Control: no-cache, no-store, must-revalidate` — a stale,
  browser-cached service worker is the most common way a PWA becomes
  unfixable in production
- `Content-Type: application/javascript; charset=utf-8`
- `Content-Security-Policy: default-src 'self'; script-src 'self'`

The Next.js PWA guide also suggests global `X-Content-Type-Options`,
`X-Frame-Options` and `Referrer-Policy` headers. Worth doing, but they affect
every route including the admin console and the Paystack callback, so they
belong in their own change with their own verification.

## Error handling

- Registration is wrapped in a feature check (`"serviceWorker" in navigator`)
  and a `catch` that logs and moves on. A browser without service worker
  support, or one where registration fails, gets today's behaviour exactly.
- Every `fetch` handler falls through to the network on any cache error. A
  corrupt cache must never turn into a failed page load.
- `/offline` is a plain server component with no data dependencies, so it
  renders from the precache with no runtime requirements.

## Testing

Two new files, both added to the `test` script in `package.json`:

**`scripts/test-pwa-policy.mts`** — loads `public/sw-policy.js` via `node:vm`
and asserts every row of the policy table, with the leak cases explicit:

- `/dashboard`, `/settings/billing`, `/classroom/biology/cell-structure` are
  never cacheable
- `/practice` and its children are network-only
- every non-GET method is network-only regardless of path
- cross-origin URLs are network-only
- `/learnable` is not treated as inside `/learn` (segment-aware matching, the
  same trap `isPublicPath` already guards)

**`scripts/test-pwa-manifest.mts`** — imports the manifest function and
asserts 192 and 512 exist in both `any` and `maskable` purposes, that `scope`
and `id` are present, and that `start_url` resolves inside `scope`.

**Manual verification**, which the unit tests cannot replace: unit tests
prove the policy function is correct, not that a worker installs. Before this
is called done, run `next dev --experimental-https` and confirm in Chrome
DevTools → Application: the worker registers and activates, the manifest
parses with no installability warnings, an offline navigation to a gated
route lands on `/offline`, a public page already visited still renders
offline, and the install banner appears and completes an install.

## Risks

- **The service worker caches the wrong thing once and it persists.**
  Mitigated by the allowlist policy, direct tests on `chooseStrategy`, and
  the no-store header that guarantees a fixed worker actually reaches
  clients.
- **Icon regeneration drifts from `icon.svg`.** The script is committed and
  deterministic; if branding changes, rerun it.
- **`start_url` pointing at a gated route.** Intentional. An installed app
  opening on the marketing page is the worse outcome; the redirect to
  `/login` for a logged-out user is the existing, correct behaviour.
