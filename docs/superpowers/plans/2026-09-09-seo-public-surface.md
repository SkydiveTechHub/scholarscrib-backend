# Public SEO Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an indexable public content surface — topic pages and past-paper pages with real substance and gated depth — plus the metadata, robots, sitemap and structured-data infrastructure the whole site has been missing.

**Architecture:** All shaping logic lives as pure functions in `src/lib/seo/*` so it is unit-testable without a database; route files are thin and only call those functions plus `cache()`-wrapped Prisma loaders. Public pages live in a new `src/app/(public)/` route group that owns the landing nav and footer. Every public page is guarded by one shared eligibility predicate that also drives sitemap inclusion, so the sitemap can never advertise a 404.

**Tech Stack:** Next.js 16.2.11 (App Router), React 19.2.4, TypeScript, Prisma 6 / PostgreSQL, Tailwind CSS v4, `node:test` via `tsx`.

**Spec:** `docs/superpowers/specs/2026-09-09-seo-public-surface-design.md`

## Global Constraints

- **This is Next.js 16.2.11 — read `node_modules/next/dist/docs/` before using an API you are unsure of.** The APIs differ from older training data.
- `themeColor`, `colorScheme`, and `viewport` **must not** appear inside a `metadata` object — deprecated since Next.js 14. Use `generateViewport` instead.
- `export const revalidate` and `export const dynamicParams` are used, and are correct **only because `cacheComponents` is not enabled** in `next.config.ts`. Do not enable Cache Components as part of this work.
- Public sample questions come **only** from questions with no `ProviderQuestion` row. Every question query on a public page goes through `publicQuestionWhere()` (Task 6).
- `lastModified` is **omitted** when no honest timestamp exists. Never substitute `new Date()`.
- Canonical host: `process.env.NEXT_PUBLIC_APP_URL`, falling back to `https://scholarscrib.com`.
- Prisma client is imported as `import { db } from "@/lib/db"`.
- Tests are `scripts/test-*.mts` using `node:test` + `node:assert/strict`, and each new file is appended to the `test` script in `package.json`.
- **Two typecheck commands, both required before each commit.** The root `tsconfig.json` has `"exclude": ["node_modules", "scripts"]`, so the app typecheck never sees the new test files:
  ```bash
  rm -f .next/dev/types/validator.ts   # see below
  npx tsc -p tsconfig.json --noEmit    # app + src
  npm run typecheck:tests              # the .mts test files
  ```
- `npm run typecheck:tests` has **5 pre-existing errors** in three unrelated files (`test-analytics-insight.mts`, `test-provider-ingest.mts`, `test-provider-ingest-batching.mts`). The gate is "no errors in `scripts/test-seo-*.mts`", not a clean exit. Do not fix that inherited debt — it is out of scope.
- The root tsconfig includes `.next/dev/types/**/*.ts`. A live dev server regenerates that validator and can leave it torn mid-write, producing a syntax error in a file nobody wrote. `rm -f .next/dev/types/validator.ts` before typechecking; the dev server regenerates it. If the Prisma query engine DLL throws EPERM instead, a dev server is holding it — report that rather than working around it.

---

## File Structure

**Created — pure logic (no DB, no React):**
- `src/lib/seo/site.ts` — canonical host resolution and absolute URL building.
- `src/lib/seo/metadata.ts` — `buildMetadata()`, the single source of canonical + OG + Twitter tags.
- `src/lib/seo/paths.ts` — the gated-path list, shared by `robots.ts` and the sitemap.
- `src/lib/seo/eligibility.ts` — thin-content thresholds.
- `src/lib/seo/question-scope.ts` — `publicQuestionWhere()`.
- `src/lib/seo/samples.ts` — deterministic sample selection.
- `src/lib/seo/exam-segment.ts` — `waec`/`jamb`/`neco` URL segment parser.
- `src/lib/seo/sitemap-shape.ts` — shard assignment, dedupe, gated-path exclusion.
- `src/lib/seo/jsonld.ts` — structured-data builders.
- `src/lib/seo/copy.ts` — title and description templates.

**Created — data loaders (Prisma, `cache()`-wrapped):**
- `src/lib/seo/learn-data.ts`
- `src/lib/seo/paper-data.ts`

**Created — components and routes:**
- `src/components/seo/json-ld.tsx`
- `src/components/landing/faq-data.ts`
- `src/components/seo/sample-question.tsx`
- `src/app/(public)/layout.tsx`, `src/app/(public)/page.tsx` (moved)
- `src/app/(public)/learn/` — `page.tsx`, `[subjectSlug]/page.tsx`, `[subjectSlug]/[topicSlug]/page.tsx`
- `src/app/(public)/past-questions/` — `page.tsx`, `[exam]/page.tsx`, `[exam]/[subjectSlug]/page.tsx`, `[exam]/[subjectSlug]/[year]/page.tsx`
- `src/app/robots.ts`, `src/app/sitemap.ts`, `src/app/manifest.ts`, `src/app/opengraph-image.tsx`

**Modified:**
- `src/app/layout.tsx` — `metadataBase`, title template, OG defaults, `generateViewport`.
- `src/app/(dashboard)/layout.tsx`, `src/app/(auth)/layout.tsx`, `src/app/admin/(console)/layout.tsx` — `noindex`.
- `src/components/landing/nav.tsx`, `src/components/landing/footer.tsx`, `src/components/landing/faq.tsx` — root-relative fragment links, FAQ data extraction.
- `package.json` — new test files.

---

# Phase 1 — Foundation

## Task 1: Canonical URLs and the metadata builder

**Files:**
- Create: `src/lib/seo/site.ts`
- Create: `src/lib/seo/metadata.ts`
- Test: `scripts/test-seo-metadata.mts`
- Modify: `package.json` (`test` script)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `siteUrl: string`, `siteName: string`, `siteDescription: string`
  - `normaliseSiteUrl(raw: string | null | undefined): string`
  - `absoluteUrl(path: string): string`
  - `buildMetadata(input: SeoInput): Metadata` where `SeoInput = { title: string; description: string; path: string; image?: string; noindex?: boolean }`
  - `NOINDEX: Metadata`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-seo-metadata.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { absoluteUrl, normaliseSiteUrl, siteUrl } from "../src/lib/seo/site";
import { buildMetadata, NOINDEX } from "../src/lib/seo/metadata";

test("an unset or blank app url falls back to production", () => {
  assert.equal(normaliseSiteUrl(undefined), "https://scholarscrib.com");
  assert.equal(normaliseSiteUrl(""), "https://scholarscrib.com");
  assert.equal(normaliseSiteUrl("   "), "https://scholarscrib.com");
});

test("garbage in the env var does not produce a garbage canonical", () => {
  // A broken canonical is worse than a wrong-but-valid one: crawlers drop the
  // page entirely rather than guessing what was meant.
  assert.equal(normaliseSiteUrl("not a url"), "https://scholarscrib.com");
});

test("path, trailing slash and query are stripped from the host", () => {
  assert.equal(normaliseSiteUrl("https://scholarscrib.com/"), "https://scholarscrib.com");
  assert.equal(normaliseSiteUrl("http://localhost:3000/"), "http://localhost:3000");
  assert.equal(normaliseSiteUrl("https://scholarscrib.com/app?x=1"), "https://scholarscrib.com");
});

test("absoluteUrl normalises the join from either side", () => {
  assert.equal(absoluteUrl("learn"), `${siteUrl}/learn`);
  assert.equal(absoluteUrl("/learn"), `${siteUrl}/learn`);
  assert.equal(absoluteUrl("/learn/"), `${siteUrl}/learn`);
});

test("the home page canonical keeps its single trailing slash", () => {
  // Root is the one path where the trailing slash is canonical, and "" would
  // emit a bare origin that some crawlers treat as a different URL.
  assert.equal(absoluteUrl("/"), `${siteUrl}/`);
  assert.equal(absoluteUrl(""), `${siteUrl}/`);
});

test("an already-absolute image url is passed through untouched", () => {
  const meta = buildMetadata({
    title: "T",
    description: "D",
    path: "/learn",
    image: "https://res.cloudinary.com/demo/og.png",
  });
  assert.deepEqual(meta.openGraph?.images, [
    { url: "https://res.cloudinary.com/demo/og.png" },
  ]);
});

test("canonical, open graph and twitter all describe the same url", () => {
  const meta = buildMetadata({ title: "T", description: "D", path: "/learn/biology" });
  const canonical = `${siteUrl}/learn/biology`;
  assert.equal(meta.alternates?.canonical, canonical);
  assert.equal(meta.openGraph?.url, canonical);
  assert.equal(meta.title, "T");
  assert.equal(meta.openGraph?.title, "T");
  assert.equal(meta.twitter?.title, "T");
  assert.equal(meta.description, "D");
  assert.equal(meta.openGraph?.description, "D");
  assert.equal(meta.twitter?.description, "D");
});

test("open graph carries the Nigerian locale and the site name", () => {
  const meta = buildMetadata({ title: "T", description: "D", path: "/" });
  assert.equal(meta.openGraph?.locale, "en_NG");
  assert.equal(meta.openGraph?.siteName, "ScholarsCrib");
});

test("robots is left alone unless noindex is asked for", () => {
  assert.equal(buildMetadata({ title: "T", description: "D", path: "/" }).robots, undefined);
  const hidden = buildMetadata({ title: "T", description: "D", path: "/x", noindex: true });
  assert.deepEqual(hidden.robots, { index: false, follow: false });
});

test("the shared noindex constant blocks both indexing and link following", () => {
  assert.deepEqual(NOINDEX.robots, { index: false, follow: false });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx tsx --test scripts/test-seo-metadata.mts`
Expected: FAIL — cannot find module `../src/lib/seo/site`.

- [ ] **Step 3: Write the implementation**

Create `src/lib/seo/site.ts`:

```ts
/**
 * The canonical host every public URL is built from.
 *
 * Read from NEXT_PUBLIC_APP_URL (already used by the Paystack lib) so preview
 * deployments canonicalise to themselves instead of to production. Anything
 * unparseable falls back rather than throwing: a build that dies because an env
 * var was fat-fingered is worse than one that ships a slightly wrong canonical.
 */
const FALLBACK_SITE_URL = "https://scholarscrib.com";

export function normaliseSiteUrl(raw: string | null | undefined): string {
  const candidate = raw?.trim();
  if (!candidate) return FALLBACK_SITE_URL;
  try {
    const parsed = new URL(candidate);
    return `${parsed.protocol}//${parsed.host}`;
  } catch {
    return FALLBACK_SITE_URL;
  }
}

export const siteUrl = normaliseSiteUrl(process.env.NEXT_PUBLIC_APP_URL);
export const siteName = "ScholarsCrib";
export const siteDescription =
  "Nigeria's learning platform for WAEC, JAMB and NECO. Structured lessons, past questions with worked answers, mock exams and a study plan that adapts to you.";

/**
 * Absolute URL for a site-relative path. Already-absolute inputs pass through,
 * so callers can hand this a CDN image URL without special-casing.
 */
export function absoluteUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;

  const trimmed = path.trim().replace(/^\/+/, "").replace(/\/+$/, "");
  return trimmed ? `${siteUrl}/${trimmed}` : `${siteUrl}/`;
}
```

Create `src/lib/seo/metadata.ts`:

```ts
import type { Metadata } from "next";
import { absoluteUrl, siteName } from "./site";

export type SeoInput = {
  title: string;
  description: string;
  /** Site-relative path, e.g. "/learn/biology". */
  path: string;
  /** Site-relative or absolute image URL. Falls back to the route's own OG image. */
  image?: string;
  noindex?: boolean;
};

/**
 * The one place canonical, Open Graph and Twitter tags are derived, so the
 * three cannot disagree — the failure mode where a page's canonical says one
 * URL and its og:url says another is both common and silently damaging.
 */
export function buildMetadata({
  title,
  description,
  path,
  image,
  noindex,
}: SeoInput): Metadata {
  const url = absoluteUrl(path);
  const images = image ? [{ url: absoluteUrl(image) }] : undefined;

  return {
    title,
    description,
    alternates: { canonical: url },
    openGraph: {
      type: "website",
      url,
      title,
      description,
      siteName,
      locale: "en_NG",
      ...(images ? { images } : {}),
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      ...(images ? { images: images.map((i) => i.url) } : {}),
    },
    ...(noindex ? { robots: { index: false, follow: false } } : {}),
  };
}

/**
 * For gated layouts. robots.txt Disallow stops crawling but does not remove a
 * URL already in the index; this directive does.
 */
export const NOINDEX: Metadata = { robots: { index: false, follow: false } };
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx tsx --test scripts/test-seo-metadata.mts`
Expected: PASS, 10 tests.

- [ ] **Step 5: Register the test file**

In `package.json`, append ` scripts/test-seo-metadata.mts` to the end of the `test` script value.

- [ ] **Step 6: Typecheck and commit**

```bash
npx tsc -p tsconfig.json --noEmit
git add src/lib/seo/site.ts src/lib/seo/metadata.ts scripts/test-seo-metadata.mts package.json
git commit -m "feat(seo): derive canonical, OG and Twitter tags from one input"
```

---

## Task 2: Site-wide metadata defaults and noindex on the gated tree

**Files:**
- Modify: `src/app/layout.tsx`
- Modify: `src/app/(dashboard)/layout.tsx`
- Modify: `src/app/(auth)/layout.tsx`
- Modify: `src/app/admin/(console)/layout.tsx`

**Interfaces:**
- Consumes: `siteUrl`, `siteName`, `siteDescription` from `@/lib/seo/site`; `NOINDEX` from `@/lib/seo/metadata`.
- Produces: nothing importable. Every child route now inherits `metadataBase`, so `buildMetadata`'s absolute URLs resolve correctly and relative OG image paths work.

- [ ] **Step 1: Replace the metadata export in the root layout**

In `src/app/layout.tsx`, replace the existing `export const metadata` block with:

```tsx
export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: "ScholarsCrib — Ace Your WAEC, JAMB & NECO",
    template: "%s | ScholarsCrib",
  },
  description: siteDescription,
  applicationName: siteName,
  keywords: [
    "WAEC", "JAMB", "NECO", "past questions", "Nigeria education",
    "secondary school", "SS1", "SS2", "SS3", "UTME", "CBT practice",
  ],
  openGraph: {
    type: "website",
    siteName,
    locale: "en_NG",
    url: siteUrl,
  },
  twitter: { card: "summary_large_image" },
};

// themeColor must live here, not in `metadata` — the metadata key has been
// deprecated since Next.js 14 and is ignored.
export function generateViewport(): Viewport {
  return {
    width: "device-width",
    initialScale: 1,
    themeColor: "#ffffff",
  };
}
```

Add to the imports at the top of the file:

```tsx
import type { Metadata, Viewport } from "next";
import { siteDescription, siteName, siteUrl } from "@/lib/seo/site";
```

(The file already imports `type { Metadata }` — extend that line rather than duplicating it.)

- [ ] **Step 2: Add noindex to the three gated layouts**

In each of `src/app/(dashboard)/layout.tsx`, `src/app/(auth)/layout.tsx`, and `src/app/admin/(console)/layout.tsx`, add these two lines above the default export:

```tsx
import { NOINDEX } from "@/lib/seo/metadata";

// Nothing under here is useful in a search result, and an indexed login wall
// is a ranking liability. Async layouts can still export static metadata.
export const metadata = NOINDEX;
```

- [ ] **Step 3: Verify in the browser**

Run `npm run dev`, then check the rendered head:

```bash
curl -s http://localhost:3000/ | grep -o '<meta name="viewport"[^>]*>'
curl -s http://localhost:3000/login | grep -o '<meta name="robots"[^>]*>'
```

Expected: the viewport tag is present on `/`; `/login` reports `content="noindex, nofollow"`. `/` must **not** carry a robots noindex.

- [ ] **Step 4: Typecheck and commit**

```bash
npx tsc -p tsconfig.json --noEmit
git add src/app/layout.tsx "src/app/(dashboard)/layout.tsx" "src/app/(auth)/layout.tsx" "src/app/admin/(console)/layout.tsx"
git commit -m "feat(seo): set metadata defaults and noindex every gated route"
```

---

## Task 3: robots.txt and the shared gated-path list

**Files:**
- Create: `src/lib/seo/paths.ts`
- Create: `src/app/robots.ts`
- Test: `scripts/test-seo-paths.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `absoluteUrl` from `@/lib/seo/site`.
- Produces: `GATED_PATH_PREFIXES: readonly string[]`, `isGatedPath(path: string): boolean`, `SITEMAP_SHARDS: readonly ["static", "learn", "past-questions"]`, `type SitemapShard`.

**Ruling carried in (R2):** `SITEMAP_SHARDS` lives here, not in `sitemap-shape.ts`, so `robots.ts` can name the shard URLs without depending on Task 19.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-seo-paths.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { GATED_PATH_PREFIXES, SITEMAP_SHARDS, isGatedPath } from "../src/lib/seo/paths";

test("every gated segment and its descendants are gated", () => {
  for (const prefix of GATED_PATH_PREFIXES) {
    assert.equal(isGatedPath(prefix), true, `${prefix} should be gated`);
    assert.equal(isGatedPath(`${prefix}/deeper`), true, `${prefix}/deeper should be gated`);
  }
});

test("the public surface is not gated", () => {
  for (const path of [
    "/",
    "/learn",
    "/learn/biology",
    "/learn/biology/cell-structure",
    "/past-questions",
    "/past-questions/waec",
    "/past-questions/waec/biology/2019",
  ]) {
    assert.equal(isGatedPath(path), false, `${path} should be public`);
  }
});

test("a public path is not gated by a prefix it merely resembles", () => {
  // "/past-questions" shares no prefix with "/practice", but a naive
  // startsWith over bare strings would gate "/librarian" under "/library".
  assert.equal(isGatedPath("/practice-tips"), false);
  assert.equal(isGatedPath("/librarian"), false);
  assert.equal(isGatedPath("/settings-guide"), false);
});

test("the sitemap shard names are fixed", () => {
  // robots.ts names one sitemap URL per shard, so the list has to live beside
  // the gated paths rather than in the sitemap module Task 19 adds later.
  assert.deepEqual(SITEMAP_SHARDS, ["static", "learn", "past-questions"]);
});

test("the list covers the routes that actually exist behind auth", () => {
  for (const prefix of [
    "/api", "/admin", "/dashboard", "/classroom", "/practice", "/flashcards",
    "/performance", "/study-plan", "/achievements", "/library", "/settings",
    "/login", "/register",
  ]) {
    assert.ok(
      GATED_PATH_PREFIXES.includes(prefix),
      `${prefix} is missing from GATED_PATH_PREFIXES`,
    );
  }
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx tsx --test scripts/test-seo-paths.mts`
Expected: FAIL — cannot find module `../src/lib/seo/paths`.

- [ ] **Step 3: Write the implementation**

Create `src/lib/seo/paths.ts`:

```ts
/**
 * Every route prefix that must never appear in a sitemap or be crawled.
 *
 * One list, two consumers (robots.ts and the sitemap), because the failure
 * mode of two lists is a gated URL leaking into the sitemap months later.
 */
export const GATED_PATH_PREFIXES: readonly string[] = [
  "/api",
  "/admin",
  "/dashboard",
  "/classroom",
  "/practice",
  "/flashcards",
  "/performance",
  "/study-plan",
  "/achievements",
  "/library",
  "/settings",
  "/login",
  "/register",
];

/**
 * Sitemap shards. Defined here rather than in sitemap-shape.ts because
 * robots.ts must name one sitemap URL per shard, and robots.ts is built before
 * the sitemap module exists.
 */
export const SITEMAP_SHARDS = ["static", "learn", "past-questions"] as const;
export type SitemapShard = (typeof SITEMAP_SHARDS)[number];

/** Segment-aware: "/librarian" is not inside "/library". */
export function isGatedPath(path: string): boolean {
  const normalised = path.replace(/\/+$/, "") || "/";
  return GATED_PATH_PREFIXES.some(
    (prefix) => normalised === prefix || normalised.startsWith(`${prefix}/`),
  );
}
```

Create `src/app/robots.ts`:

```ts
import type { MetadataRoute } from "next";
import { GATED_PATH_PREFIXES, SITEMAP_SHARDS } from "@/lib/seo/paths";
import { absoluteUrl } from "@/lib/seo/site";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      // Bare prefixes, no trailing slash: robots.txt matching is prefix-based,
      // so "/login/" would leave "/login" itself crawlable while "/login"
      // covers both it and its subtree. Disallow only stops crawling — the
      // noindex directives on the gated layouts are what keep these out of
      // the index.
      disallow: [...GATED_PATH_PREFIXES],
    },
    // generateSitemaps emits /sitemap/<id>.xml per shard and no index file, so
    // every shard is named here. The field accepts string[].
    sitemap: SITEMAP_SHARDS.map((shard) => absoluteUrl(`/sitemap/${shard}.xml`)),
  };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx tsx --test scripts/test-seo-paths.mts`
Expected: PASS, 5 tests.

- [ ] **Step 5: Verify the generated file**

With the dev server running: `curl -s http://localhost:3000/robots.txt`
Expected: `User-Agent: *`, `Allow: /`, 13 `Disallow:` lines with **no** trailing slashes, and three `Sitemap:` lines (`/sitemap/static.xml`, `/sitemap/learn.xml`, `/sitemap/past-questions.xml`).

- [ ] **Step 6: Register, typecheck and commit**

Append ` scripts/test-seo-paths.mts` to the `test` script in `package.json`.

```bash
npx tsc -p tsconfig.json --noEmit
git add src/lib/seo/paths.ts src/app/robots.ts scripts/test-seo-paths.mts package.json
git commit -m "feat(seo): add robots.txt over a shared gated-path list"
```

---

## Task 4: The (public) route group, the landing move, and the fragment-link fix

**Files:**
- Create: `src/app/(public)/layout.tsx`
- Move: `src/app/page.tsx` → `src/app/(public)/page.tsx`
- Create: `src/components/landing/faq-data.ts`
- Modify: `src/components/landing/faq.tsx`
- Modify: `src/components/landing/nav.tsx`
- Modify: `src/components/landing/footer.tsx`

**Interfaces:**
- Consumes: `buildMetadata` from `@/lib/seo/metadata`.
- Produces: `FAQS: readonly { question: string; answer: string }[]` exported from `@/components/landing/faq-data`, consumed by both the FAQ component and the `FAQPage` JSON-LD builder in Task 15.

- [ ] **Step 1: Move the landing page and create the group layout**

```bash
mkdir -p "src/app/(public)"
git mv src/app/page.tsx "src/app/(public)/page.tsx"
```

Create `src/app/(public)/layout.tsx`:

```tsx
import { Nav } from "@/components/landing/nav";
import { Footer } from "@/components/landing/footer";

/**
 * Chrome for every publicly indexable page. The `landing` class is not
 * decorative — it sets --landing-bg and --landing-ink, so dropping it leaves
 * the content pages unstyled.
 */
export default function PublicLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="landing min-h-screen">
      <Nav />
      <main>{children}</main>
      <Footer />
    </div>
  );
}
```

- [ ] **Step 2: Strip the now-duplicated chrome from the landing page**

In `src/app/(public)/page.tsx`, delete the `Nav` and `Footer` imports and the wrapping `<div className="landing min-h-screen">`, `<Nav />`, `<main>`, `</main>` and `<Footer />` elements — the layout owns them now. The component body becomes a fragment holding the sections:

```tsx
export default function LandingPage() {
  return (
    <>
      <Hero />
      <TrustedBy />
      <WhyUs />
      <Showcase />
      <Journey />
      <Subjects />
      <DeepDive />
      <Testimonials />
      <StatsBand />
      <Pricing />
      <Faq />
      <FinalCta />
    </>
  );
}
```

Replace its `export const metadata` with:

```tsx
export const metadata = buildMetadata({
  title: "ScholarsCrib — Learn Smarter. Score Higher. Build Your Future.",
  description:
    "Nigeria's learning platform for WAEC, JAMB and NECO. Interactive lessons, an AI tutor, smart flashcards, quizzes, CBT practice and a study plan that adapts to you.",
  path: "/",
});
```

and import it: `import { buildMetadata } from "@/lib/seo/metadata";`

- [ ] **Step 3: Make every fragment link root-relative**

The nav and footer now render on `/learn/...` and `/past-questions/...`, where `href="#pricing"` resolves against the current path and scrolls nowhere.

In `src/components/landing/nav.tsx`, change `NAV_LINKS` to:

```tsx
const NAV_LINKS = [
  { name: "Features", href: "/#features" },
  { name: "Product", href: "/#product" },
  { name: "Subjects", href: "/#subjects" },
  { name: "Pricing", href: "/#pricing" },
  { name: "FAQ", href: "/#faq" },
];
```

In `src/components/landing/footer.tsx`, prefix every `href` that begins with `#` with `/` (`"#features"` → `"/#features"`, `"#top"` → `"/"`, `"#subjects"` → `"/#subjects"`, `"#faq"` → `"/#faq"`). Leave `mailto:` alone. `#top` becomes plain `/` because there is no `#top` anchor to land on.

Then add the two real destinations the footer can now point at. In the "Product" column, add these two entries alongside the existing fragment links:

```tsx
      { label: "Browse subjects", href: "/learn" },
      { label: "Past questions", href: "/past-questions" },
```

These are the primary internal links into the new surface, and the footer appears on every page.

- [ ] **Step 4: Extract the FAQ content**

Create `src/components/landing/faq-data.ts` containing the `FAQS` array currently defined in `faq.tsx`, moved verbatim:

```ts
/**
 * Shared so the rendered accordion and the FAQPage structured data cannot
 * drift. Marking up copy that is not on the page is a rich-result violation.
 */
export const FAQS = [
  // ... move the six entries from faq.tsx here unchanged ...
] as const;
```

In `src/components/landing/faq.tsx`, delete the local `const FAQS = [...]` and add `import { FAQS } from "./faq-data";`.

- [ ] **Step 5: Verify nothing regressed**

```bash
npx tsc -p tsconfig.json --noEmit
npm run lint
```

With the dev server running, load `http://localhost:3000/` and confirm: the page renders with landing styling, the nav links scroll to their sections, and the FAQ accordion still opens.

- [ ] **Step 6: Commit**

```bash
git add -A "src/app/(public)" src/app/page.tsx src/components/landing
git commit -m "refactor(landing): move the landing page into a shared (public) group

The nav and footer now render on the new content pages too, so their bare
fragment hrefs had to become root-relative -- \"#pricing\" resolves against
whatever path it is rendered on."
```

---

## Task 5: Icons and the web manifest

**Files:**
- Create: `src/app/icon.svg`
- Create: `src/app/apple-icon.png`
- Create: `src/app/manifest.ts`

**Interfaces:**
- Consumes: `siteName`, `siteDescription` from `@/lib/seo/site`.
- Produces: nothing importable.

- [ ] **Step 1: Add the icon**

`public/` currently ships only Next.js placeholder SVGs. Create `src/app/icon.svg` — a 32×32 mark using the brand primary, matching the `LuGraduationCap` motif already used in the auth layout:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">
  <rect width="32" height="32" rx="7" fill="#1d4ed8"/>
  <path d="M16 8 4 13.2l12 5.2 12-5.2L16 8Zm-8 8.6v4.1c0 2.1 3.6 3.8 8 3.8s8-1.7 8-3.8v-4.1l-8 3.5-8-3.5Z" fill="#fff"/>
</svg>
```

- [ ] **Step 2: Add the Apple touch icon**

Create `src/app/apple-icon.png` — a 180×180 PNG of the same mark on the solid brand background (Apple ignores transparency and composites on black). Generate it from the SVG above; if no rasteriser is available locally, note it in the commit body and leave the file out rather than committing a placeholder.

- [ ] **Step 3: Add the manifest**

Create `src/app/manifest.ts`:

```ts
import type { MetadataRoute } from "next";
import { siteDescription, siteName } from "@/lib/seo/site";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: siteName,
    short_name: "ScholarsCrib",
    description: siteDescription,
    start_url: "/",
    display: "standalone",
    background_color: "#ffffff",
    theme_color: "#ffffff",
    icons: [{ src: "/icon.svg", sizes: "any", type: "image/svg+xml" }],
  };
}
```

- [ ] **Step 4: Verify**

With the dev server running:

```bash
curl -s -o /dev/null -w "%{http_code} %{content_type}\n" http://localhost:3000/manifest.webmanifest
curl -s http://localhost:3000/ | grep -o '<link rel="icon"[^>]*>'
```

Expected: `200 application/manifest+json`, and an icon link tag in the head.

- [ ] **Step 5: Commit**

```bash
# Drop apple-icon.png from this list if Step 2 could not rasterise it.
git add src/app/icon.svg src/app/apple-icon.png src/app/manifest.ts
git commit -m "feat(seo): add real icons and a web manifest"
```

---

# Phase 2 — the /learn tree

## Task 6: Eligibility thresholds and the public question scope

**Files:**
- Create: `src/lib/seo/eligibility.ts`
- Create: `src/lib/seo/question-scope.ts`
- Test: `scripts/test-seo-eligibility.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `Prisma` types from `@prisma/client`.
- Produces:
  - `TOPIC_MIN_SUBTOPICS = 2`, `TOPIC_MIN_QUESTIONS = 3`, `PAPER_MIN_QUESTIONS = 10`, `TOPIC_SAMPLE_COUNT = 3`, `PAPER_SAMPLE_COUNT = 5`
  - `isTopicPageEligible(input: { description: string | null; subtopicCount: number; publicQuestionCount: number }): boolean`
  - `isPaperPageEligible(input: { publicQuestionCount: number }): boolean`
  - `publicQuestionWhere(extra?: Prisma.QuestionWhereInput): Prisma.QuestionWhereInput`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-seo-eligibility.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  PAPER_MIN_QUESTIONS,
  TOPIC_MIN_QUESTIONS,
  isPaperPageEligible,
  isTopicPageEligible,
} from "../src/lib/seo/eligibility";
import { publicQuestionWhere } from "../src/lib/seo/question-scope";

const topic = (over: Partial<Parameters<typeof isTopicPageEligible>[0]> = {}) => ({
  description: "Cells are the basic unit of life.",
  subtopicCount: 3,
  publicQuestionCount: 5,
  ...over,
});

test("a topic with prose and enough questions is eligible", () => {
  assert.equal(isTopicPageEligible(topic()), true);
});

test("subtopics substitute for a missing description", () => {
  assert.equal(isTopicPageEligible(topic({ description: null, subtopicCount: 2 })), true);
  assert.equal(isTopicPageEligible(topic({ description: "   ", subtopicCount: 2 })), true);
});

test("no prose and too few subtopics is not eligible", () => {
  assert.equal(isTopicPageEligible(topic({ description: null, subtopicCount: 1 })), false);
});

test("questions are required even when the prose is good", () => {
  // A topic page with nothing to practise is a brochure, and a few hundred
  // brochures is what a doorway-page classification looks like.
  assert.equal(
    isTopicPageEligible(topic({ publicQuestionCount: TOPIC_MIN_QUESTIONS - 1 })),
    false,
  );
  assert.equal(isTopicPageEligible(topic({ publicQuestionCount: 0 })), false);
});

test("the topic threshold is inclusive at the boundary", () => {
  assert.equal(isTopicPageEligible(topic({ publicQuestionCount: TOPIC_MIN_QUESTIONS })), true);
});

test("a paper needs a real number of questions", () => {
  assert.equal(isPaperPageEligible({ publicQuestionCount: PAPER_MIN_QUESTIONS }), true);
  assert.equal(isPaperPageEligible({ publicQuestionCount: PAPER_MIN_QUESTIONS - 1 }), false);
});

test("the public scope excludes provider-sourced questions", () => {
  // Provider content is licensed for use inside the product, not for
  // republication on indexable pages.
  assert.deepEqual(publicQuestionWhere().providerQuestion, { is: null });
});

test("the public scope is objective questions only", () => {
  // Theory questions have no options, and the sample renderer needs them.
  assert.equal(publicQuestionWhere().questionType, "OBJECTIVE");
});

test("extra filters merge without dropping the public constraints", () => {
  const where = publicQuestionWhere({ subjectId: "s1", examYear: 2019 });
  assert.equal(where.subjectId, "s1");
  assert.equal(where.examYear, 2019);
  assert.deepEqual(where.providerQuestion, { is: null });
  assert.equal(where.questionType, "OBJECTIVE");
});

test("a caller cannot accidentally override the public constraints", () => {
  const where = publicQuestionWhere({
    providerQuestion: undefined,
    questionType: "THEORY",
  });
  assert.deepEqual(where.providerQuestion, { is: null });
  assert.equal(where.questionType, "OBJECTIVE");
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx tsx --test scripts/test-seo-eligibility.mts`
Expected: FAIL — cannot find module `../src/lib/seo/eligibility`.

- [ ] **Step 3: Write the implementation**

Create `src/lib/seo/eligibility.ts`:

```ts
/**
 * The thin-content gate.
 *
 * Generating one page per topic and per exam-subject-year triple produces
 * hundreds of URLs. Hundreds of near-empty auto-generated URLs is how a site
 * earns a doorway-page classification, which applies sitewide and is hard to
 * reverse. So a page that fails these thresholds 404s AND is left out of the
 * sitemap — both driven from here, so the two cannot disagree.
 */
export const TOPIC_MIN_SUBTOPICS = 2;
export const TOPIC_MIN_QUESTIONS = 3;
export const PAPER_MIN_QUESTIONS = 10;

/** How many worked questions each page shows before the gate. */
export const TOPIC_SAMPLE_COUNT = 3;
export const PAPER_SAMPLE_COUNT = 5;

export type TopicEligibilityInput = {
  description: string | null;
  subtopicCount: number;
  /** Count already narrowed by publicQuestionWhere(). */
  publicQuestionCount: number;
};

export function isTopicPageEligible({
  description,
  subtopicCount,
  publicQuestionCount,
}: TopicEligibilityInput): boolean {
  const hasProse = (description?.trim().length ?? 0) > 0;
  const hasSubstance = hasProse || subtopicCount >= TOPIC_MIN_SUBTOPICS;
  return hasSubstance && publicQuestionCount >= TOPIC_MIN_QUESTIONS;
}

export function isPaperPageEligible({
  publicQuestionCount,
}: {
  publicQuestionCount: number;
}): boolean {
  return publicQuestionCount >= PAPER_MIN_QUESTIONS;
}
```

Create `src/lib/seo/question-scope.ts`:

```ts
import type { Prisma } from "@prisma/client";

/**
 * The only question filter a public page may use.
 *
 * `providerQuestion: { is: null }` excludes anything ingested from a provider
 * (currently SDASH): that content is licensed for use inside the product, and
 * putting it on an indexable page is republication.
 *
 * The public constraints are spread last so a caller's `extra` can narrow the
 * query but never widen it past the two rules above.
 */
export function publicQuestionWhere(
  extra: Prisma.QuestionWhereInput = {},
): Prisma.QuestionWhereInput {
  return {
    ...extra,
    providerQuestion: { is: null },
    questionType: "OBJECTIVE",
  };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx tsx --test scripts/test-seo-eligibility.mts`
Expected: PASS, 10 tests.

- [ ] **Step 5: Register, typecheck and commit**

Append ` scripts/test-seo-eligibility.mts` to the `test` script in `package.json`.

```bash
npx tsc -p tsconfig.json --noEmit
git add src/lib/seo/eligibility.ts src/lib/seo/question-scope.ts scripts/test-seo-eligibility.mts package.json
git commit -m "feat(seo): gate public pages on content thresholds and non-provider questions"
```

---

## Task 7: Deterministic sample selection

**Files:**
- Create: `src/lib/seo/samples.ts`
- Test: `scripts/test-seo-samples.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: nothing.
- Produces: `pickSamples<T extends { id: string }>(items: readonly T[], count: number, seed: string): T[]`.

- [ ] **Step 1: Write the failing test**

Create `scripts/test-seo-samples.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { pickSamples } from "../src/lib/seo/samples";

const items = Array.from({ length: 20 }, (_, i) => ({ id: `q${i}` }));
const ids = (picked: { id: string }[]) => picked.map((p) => p.id);

test("the same seed always yields the same questions in the same order", () => {
  // A page whose visible content shuffles on every revalidation looks
  // unstable to crawlers and is impossible to debug rankings against.
  assert.deepEqual(ids(pickSamples(items, 3, "topic-a")), ids(pickSamples(items, 3, "topic-a")));
});

test("different pages get different samples", () => {
  assert.notDeepEqual(ids(pickSamples(items, 3, "topic-a")), ids(pickSamples(items, 3, "topic-b")));
});

test("the pick does not depend on the order the rows arrived in", () => {
  // Prisma is free to return rows in any order without an ORDER BY, so the
  // selection has to be stable against that.
  const reversed = [...items].reverse();
  assert.deepEqual(ids(pickSamples(items, 3, "topic-a")), ids(pickSamples(reversed, 3, "topic-a")));
});

test("asking for more than exists returns everything, without duplicates", () => {
  const picked = pickSamples(items.slice(0, 2), 5, "topic-a");
  assert.equal(picked.length, 2);
  assert.equal(new Set(ids(picked)).size, 2);
});

test("the requested count is honoured and the picks are distinct", () => {
  const picked = pickSamples(items, 5, "topic-a");
  assert.equal(picked.length, 5);
  assert.equal(new Set(ids(picked)).size, 5);
});

test("degenerate counts return nothing rather than throwing", () => {
  assert.deepEqual(pickSamples(items, 0, "s"), []);
  assert.deepEqual(pickSamples(items, -1, "s"), []);
  assert.deepEqual(pickSamples([], 3, "s"), []);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx tsx --test scripts/test-seo-samples.mts`
Expected: FAIL — cannot find module `../src/lib/seo/samples`.

- [ ] **Step 3: Write the implementation**

Create `src/lib/seo/samples.ts`:

```ts
/** FNV-1a. Small, dependency-free, and stable across Node versions — which
 * `Math.random()` and object key order are not. */
function hash(value: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < value.length; i += 1) {
    h ^= value.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/**
 * Choose `count` items deterministically from `items`.
 *
 * Keyed on the page's own identity plus each item's id, so a given page always
 * shows the same questions no matter how often it revalidates or what order
 * the database returned rows in. Ties break on id so the result is total.
 */
export function pickSamples<T extends { id: string }>(
  items: readonly T[],
  count: number,
  seed: string,
): T[] {
  if (count <= 0 || items.length === 0) return [];

  return [...items]
    .map((item) => ({ item, rank: hash(`${seed}:${item.id}`) }))
    .sort((a, b) => a.rank - b.rank || a.item.id.localeCompare(b.item.id))
    .slice(0, count)
    .map(({ item }) => item);
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx tsx --test scripts/test-seo-samples.mts`
Expected: PASS, 6 tests.

- [ ] **Step 5: Register, typecheck and commit**

Append ` scripts/test-seo-samples.mts` to the `test` script in `package.json`.

```bash
npx tsc -p tsconfig.json --noEmit
git add src/lib/seo/samples.ts scripts/test-seo-samples.mts package.json
git commit -m "feat(seo): pick sample questions deterministically per page"
```

---

## Task 8: Copy templates

**Files:**
- Create: `src/lib/seo/copy.ts`
- Test: `scripts/test-seo-copy.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `topicPageTitle(input: { topicTitle: string; subjectName: string }): string`
  - `topicPageDescription(input: { topicTitle: string; subjectName: string; description: string | null; subtopicTitles: readonly string[] }): string`
  - `paperPageTitle(input: { exam: string; year: number; subjectName: string }): string`
  - `paperPageDescription(input: { exam: string; year: number; subjectName: string; questionCount: number; topicCount: number }): string`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-seo-copy.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  paperPageDescription,
  paperPageTitle,
  topicPageDescription,
  topicPageTitle,
} from "../src/lib/seo/copy";

test("the topic title names the topic before the subject", () => {
  assert.equal(
    topicPageTitle({ topicTitle: "Cell Structure", subjectName: "Biology" }),
    "Cell Structure — Biology",
  );
});

test("a real topic description is used as written", () => {
  const description = "Cells are the basic structural unit of every organism.";
  assert.equal(
    topicPageDescription({
      topicTitle: "Cell Structure",
      subjectName: "Biology",
      description,
      subtopicTitles: ["Organelles"],
    }),
    description,
  );
});

test("a missing description falls back to the subtopics, not to boilerplate", () => {
  const built = topicPageDescription({
    topicTitle: "Cell Structure",
    subjectName: "Biology",
    description: null,
    subtopicTitles: ["Organelles", "The cell membrane", "Cell division"],
  });
  assert.match(built, /Cell Structure/);
  assert.match(built, /Biology/);
  assert.match(built, /Organelles/);
});

test("descriptions stay inside the length search engines will render", () => {
  const long = "x".repeat(400);
  const built = topicPageDescription({
    topicTitle: "T",
    subjectName: "S",
    description: long,
    subtopicTitles: [],
  });
  assert.ok(built.length <= 160, `got ${built.length} characters`);
  assert.ok(built.endsWith("…"), "a truncated description should be marked as such");
});

test("truncation happens at a word boundary", () => {
  const built = topicPageDescription({
    topicTitle: "T",
    subjectName: "S",
    description: `${"word ".repeat(60)}end`,
    subtopicTitles: [],
  });
  assert.ok(!built.includes("wor…"), "should not cut mid-word");
});

test("the paper title matches how students actually search", () => {
  assert.equal(
    paperPageTitle({ exam: "WAEC", year: 2019, subjectName: "Biology" }),
    "WAEC 2019 Biology Past Questions and Answers",
  );
});

test("the paper description states real counts", () => {
  const built = paperPageDescription({
    exam: "WAEC",
    year: 2019,
    subjectName: "Biology",
    questionCount: 42,
    topicCount: 7,
  });
  assert.match(built, /42/);
  assert.match(built, /7/);
  assert.match(built, /WAEC 2019 Biology/);
});

test("a single topic is not described in the plural", () => {
  const built = paperPageDescription({
    exam: "JAMB",
    year: 2021,
    subjectName: "Physics",
    questionCount: 1,
    topicCount: 1,
  });
  assert.ok(!/1 topics/.test(built), built);
  assert.ok(!/1 questions/.test(built), built);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx tsx --test scripts/test-seo-copy.mts`
Expected: FAIL — cannot find module `../src/lib/seo/copy`.

- [ ] **Step 3: Write the implementation**

Create `src/lib/seo/copy.ts`:

```ts
/** Google renders roughly 155-160 characters of a description. */
const MAX_DESCRIPTION = 160;

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

/** Truncate at a word boundary so the snippet never ends mid-word. */
function clamp(text: string): string {
  const trimmed = text.trim().replace(/\s+/g, " ");
  if (trimmed.length <= MAX_DESCRIPTION) return trimmed;

  const cut = trimmed.slice(0, MAX_DESCRIPTION - 1);
  const lastSpace = cut.lastIndexOf(" ");
  return `${(lastSpace > 40 ? cut.slice(0, lastSpace) : cut).trimEnd()}…`;
}

export function topicPageTitle({
  topicTitle,
  subjectName,
}: {
  topicTitle: string;
  subjectName: string;
}): string {
  return `${topicTitle} — ${subjectName}`;
}

export function topicPageDescription({
  topicTitle,
  subjectName,
  description,
  subtopicTitles,
}: {
  topicTitle: string;
  subjectName: string;
  description: string | null;
  subtopicTitles: readonly string[];
}): string {
  const authored = description?.trim();
  if (authored) return clamp(authored);

  // No boilerplate-only fallback: the subtopic titles are the page's real
  // content, so the snippet describes them.
  const covered = subtopicTitles.slice(0, 3).join(", ");
  const tail = covered ? ` Covers ${covered}.` : "";
  return clamp(
    `${topicTitle} in ${subjectName} for WAEC, JAMB and NECO, with worked past questions.${tail}`,
  );
}

export function paperPageTitle({
  exam,
  year,
  subjectName,
}: {
  exam: string;
  year: number;
  subjectName: string;
}): string {
  return `${exam} ${year} ${subjectName} Past Questions and Answers`;
}

export function paperPageDescription({
  exam,
  year,
  subjectName,
  questionCount,
  topicCount,
}: {
  exam: string;
  year: number;
  subjectName: string;
  questionCount: number;
  topicCount: number;
}): string {
  return clamp(
    `${plural(questionCount, "question")} from the ${exam} ${year} ${subjectName} paper across ${plural(topicCount, "topic")}, each with the correct answer and a worked explanation.`,
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx tsx --test scripts/test-seo-copy.mts`
Expected: PASS, 8 tests.

- [ ] **Step 5: Register, typecheck and commit**

Append ` scripts/test-seo-copy.mts` to the `test` script in `package.json`.

```bash
npx tsc -p tsconfig.json --noEmit
git add src/lib/seo/copy.ts scripts/test-seo-copy.mts package.json
git commit -m "feat(seo): template page titles and descriptions from real data"
```

---

## Task 9: The /learn data loaders

**Files:**
- Create: `src/lib/seo/learn-data.ts`

**Interfaces:**
- Consumes: `db` from `@/lib/db`; `publicQuestionWhere` from `./question-scope`; `isTopicPageEligible`, `TOPIC_SAMPLE_COUNT` from `./eligibility`; `pickSamples` from `./samples`.
- Produces:
  - `loadPublicSubjects(): Promise<PublicSubject[]>` where `PublicSubject = { slug: string; name: string; description: string; trackCategory: string; isWaec: boolean; isJamb: boolean; isNeco: boolean }`
  - `loadPublicSubject(slug: string): Promise<PublicSubjectDetail | null>` — adds `topics: { slug: string; title: string; description: string | null }[]`
  - `loadPublicTopic(subjectSlug: string, topicSlug: string): Promise<PublicTopic | null>` — returns `null` when the topic does not exist **or** is ineligible
  - `loadEligibleTopicParams(): Promise<{ subjectSlug: string; topicSlug: string; lastModified: Date | null }[]>`

- [ ] **Step 1: Write the loaders**

Create `src/lib/seo/learn-data.ts`:

```ts
import { cache } from "react";
import { db } from "@/lib/db";
import { TOPIC_SAMPLE_COUNT, isTopicPageEligible } from "./eligibility";
import { publicQuestionWhere } from "./question-scope";
import { pickSamples } from "./samples";

/**
 * Every loader is cache()-wrapped so generateMetadata and the page body share
 * one database round trip instead of issuing the same query twice.
 */

export type PublicSubject = {
  slug: string;
  name: string;
  description: string;
  trackCategory: string;
  isWaec: boolean;
  isJamb: boolean;
  isNeco: boolean;
};

export const loadPublicSubjects = cache(async (): Promise<PublicSubject[]> => {
  const subjects = await db.subject.findMany({
    orderBy: { name: "asc" },
    select: {
      slug: true, name: true, description: true, trackCategory: true,
      isWaec: true, isJamb: true, isNeco: true,
    },
  });
  return subjects;
});

export type PublicSubjectDetail = PublicSubject & {
  topics: { slug: string; title: string; description: string | null }[];
};

export const loadPublicSubject = cache(
  async (slug: string): Promise<PublicSubjectDetail | null> => {
    const subject = await db.subject.findUnique({
      where: { slug },
      select: {
        slug: true, name: true, description: true, trackCategory: true,
        isWaec: true, isJamb: true, isNeco: true,
        topics: {
          orderBy: { orderIndex: "asc" },
          select: { slug: true, title: true, description: true },
        },
      },
    });
    return subject;
  },
);

export type PublicSampleQuestion = {
  id: string;
  questionText: string;
  options: Record<string, string>;
  correctAnswer: string;
  explanation: string;
};

export type PublicTopic = {
  subject: { slug: string; name: string };
  slug: string;
  title: string;
  description: string | null;
  waecWeight: number;
  jambWeight: number;
  subtopics: { title: string; description: string | null }[];
  prerequisites: { slug: string; title: string; rationale: string | null }[];
  siblings: { slug: string; title: string }[];
  questionCount: number;
  samples: PublicSampleQuestion[];
};

export const loadPublicTopic = cache(
  async (subjectSlug: string, topicSlug: string): Promise<PublicTopic | null> => {
    const topic = await db.topic.findFirst({
      where: { slug: topicSlug, subject: { slug: subjectSlug } },
      select: {
        id: true, slug: true, title: true, description: true,
        waecWeight: true, jambWeight: true,
        subject: { select: { id: true, slug: true, name: true } },
        subtopics: {
          orderBy: { orderIndex: "asc" },
          select: { title: true, description: true },
        },
        prereqEdges: {
          select: {
            rationale: true,
            prereqTopic: { select: { slug: true, title: true } },
          },
        },
      },
    });
    if (!topic) return null;

    const questions = await db.question.findMany({
      where: publicQuestionWhere({ topicId: topic.id }),
      select: {
        id: true, questionText: true, options: true,
        correctAnswer: true, explanation: true,
      },
    });

    // Options is a Json column; a row with no parsed options cannot be
    // rendered as a sample, so it must not count toward eligibility either.
    const renderable = questions.flatMap((q) => {
      const options = q.options as Record<string, string> | null;
      if (!options || Object.keys(options).length === 0) return [];
      return [{ ...q, options }];
    });

    if (
      !isTopicPageEligible({
        description: topic.description,
        subtopicCount: topic.subtopics.length,
        publicQuestionCount: renderable.length,
      })
    ) {
      return null;
    }

    const siblings = await db.topic.findMany({
      where: { subjectId: topic.subject.id, id: { not: topic.id } },
      orderBy: { orderIndex: "asc" },
      take: 8,
      select: { slug: true, title: true },
    });

    return {
      subject: { slug: topic.subject.slug, name: topic.subject.name },
      slug: topic.slug,
      title: topic.title,
      description: topic.description,
      waecWeight: topic.waecWeight,
      jambWeight: topic.jambWeight,
      subtopics: topic.subtopics,
      prerequisites: topic.prereqEdges.map((edge) => ({
        slug: edge.prereqTopic.slug,
        title: edge.prereqTopic.title,
        rationale: edge.rationale,
      })),
      siblings,
      questionCount: renderable.length,
      samples: pickSamples(renderable, TOPIC_SAMPLE_COUNT, topic.id),
    };
  },
);

/**
 * Params for generateStaticParams and for the sitemap, from one query, so a
 * prerendered page and a sitemapped URL are always the same set.
 *
 * lastModified is the newest question in the topic. Topic has no updatedAt
 * column, so when a topic has no dated question the field stays null and the
 * sitemap omits it — `new Date()` would claim every page changed on every
 * build, which teaches crawlers to ignore the signal.
 */
export const loadEligibleTopicParams = cache(async () => {
  const topics = await db.topic.findMany({
    orderBy: [{ subject: { slug: "asc" } }, { orderIndex: "asc" }],
    select: {
      id: true,
      slug: true,
      description: true,
      subject: { select: { slug: true } },
      _count: { select: { subtopics: true } },
      questions: {
        where: publicQuestionWhere(),
        orderBy: { createdAt: "desc" },
        select: { createdAt: true },
      },
    },
  });

  return topics
    .filter((topic) =>
      isTopicPageEligible({
        description: topic.description,
        subtopicCount: topic._count.subtopics,
        publicQuestionCount: topic.questions.length,
      }),
    )
    .map((topic) => ({
      subjectSlug: topic.subject.slug,
      topicSlug: topic.slug,
      lastModified: topic.questions[0]?.createdAt ?? null,
    }));
});
```

- [ ] **Step 2: Verify against the real database**

Create a throwaway probe in the scratchpad and run it:

```bash
cat > scripts/probe-learn.mts <<'EOF'
import { loadEligibleTopicParams, loadPublicSubjects } from "../src/lib/seo/learn-data";
console.log("subjects:", (await loadPublicSubjects()).length);
const params = await loadEligibleTopicParams();
console.log("eligible topics:", params.length);
console.log(params.slice(0, 5));
EOF
npx tsx scripts/probe-learn.mts
```

Expected: a subject count above zero and a list of eligible topics. **If the eligible count is 0**, stop and report it — that means the seeded question bank is either empty or entirely provider-sourced, and the whole `/learn` tree would be 404s. Do not lower the thresholds to make the number look better without saying so.

Then `rm scripts/probe-learn.mts` — it is throwaway and must not be committed. It lives under `scripts/` because the `../src/...` import only resolves one level below the repo root.

- [ ] **Step 3: Typecheck and commit**

```bash
npx tsc -p tsconfig.json --noEmit
git add src/lib/seo/learn-data.ts
git commit -m "feat(seo): load public subject and topic content

The eligible-topic query doubles as the source for both
generateStaticParams and the sitemap, so a prerendered page and a
sitemapped URL can never be different sets."
```

---

## Task 10: The sample-question component

**Files:**
- Create: `src/components/seo/sample-question.tsx`

**Interfaces:**
- Consumes: `PublicSampleQuestion` from `@/lib/seo/learn-data`.
- Produces: `<SampleQuestion question={...} index={n} />` — a server component, no client JS.

- [ ] **Step 1: Write the component**

Create `src/components/seo/sample-question.tsx`:

```tsx
import type { PublicSampleQuestion } from "@/lib/seo/learn-data";

/**
 * A worked question, rendered fully open.
 *
 * No accordion and no client JS on purpose: content hidden behind an
 * interaction is discounted by crawlers, and these samples are the reason the
 * page deserves to rank at all.
 */
export function SampleQuestion({
  question,
  index,
}: {
  question: PublicSampleQuestion;
  index: number;
}) {
  const entries = Object.entries(question.options).sort(([a], [b]) =>
    a.localeCompare(b),
  );

  return (
    <article className="surface rounded-2xl border border-black/5 p-5 sm:p-6">
      <h3 className="text-base font-semibold ink">
        <span className="ink-muted mr-2">{index}.</span>
        {question.questionText}
      </h3>

      <ol className="mt-4 space-y-2">
        {entries.map(([letter, text]) => (
          <li
            key={letter}
            className={
              letter === question.correctAnswer
                ? "flex gap-3 rounded-xl bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-800"
                : "flex gap-3 rounded-xl px-3 py-2 text-sm ink-muted"
            }
          >
            <span className="font-bold">{letter}.</span>
            <span>{text}</span>
          </li>
        ))}
      </ol>

      <div className="mt-4 border-t border-black/5 pt-4">
        <p className="text-xs font-bold uppercase tracking-widest ink-muted">
          Answer — {question.correctAnswer}
        </p>
        <p className="mt-2 text-sm leading-relaxed ink">{question.explanation}</p>
      </div>
    </article>
  );
}
```

- [ ] **Step 2: Typecheck and commit**

```bash
npx tsc -p tsconfig.json --noEmit
git add src/components/seo/sample-question.tsx
git commit -m "feat(seo): render sample questions open, with no client JS"
```

---

## Task 11: /learn and /learn/[subjectSlug]

**Files:**
- Create: `src/app/(public)/learn/page.tsx`
- Create: `src/app/(public)/learn/[subjectSlug]/page.tsx`

**Interfaces:**
- Consumes: `loadPublicSubjects`, `loadPublicSubject` from `@/lib/seo/learn-data`; `buildMetadata`; `TRACK_LABELS` from `@/lib/subjects`.
- Produces: nothing importable.

- [ ] **Step 1: Create the subject index**

Create `src/app/(public)/learn/page.tsx`:

```tsx
import Link from "next/link";
import { buildMetadata } from "@/lib/seo/metadata";
import { loadPublicSubjects } from "@/lib/seo/learn-data";

export const revalidate = 86400;

export const metadata = buildMetadata({
  title: "Subjects — WAEC, JAMB & NECO syllabus topics",
  description:
    "Every subject ScholarsCrib covers, from Mathematics to Economics, with the topics each WAEC, JAMB and NECO syllabus expects you to know.",
  path: "/learn",
});

export default async function LearnIndexPage() {
  const subjects = await loadPublicSubjects();

  return (
    <div className="landing-container py-16">
      <h1 className="text-3xl font-bold ink sm:text-4xl">Subjects</h1>
      <p className="mt-3 max-w-2xl ink-muted">
        Topic-by-topic coverage of the Nigerian secondary curriculum, with past
        questions and worked answers for WAEC, JAMB and NECO.
      </p>

      <ul className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {subjects.map((subject) => (
          <li key={subject.slug}>
            <Link
              href={`/learn/${subject.slug}`}
              className="surface block h-full rounded-2xl border border-black/5 p-5 transition hover:border-black/15"
            >
              <h2 className="font-semibold ink">{subject.name}</h2>
              <p className="mt-2 line-clamp-3 text-sm ink-muted">
                {subject.description}
              </p>
              <p className="mt-3 text-xs font-semibold uppercase tracking-widest ink-muted">
                {[
                  subject.isWaec && "WAEC",
                  subject.isJamb && "JAMB",
                  subject.isNeco && "NECO",
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 2: Create the subject hub**

Create `src/app/(public)/learn/[subjectSlug]/page.tsx`:

```tsx
import Link from "next/link";
import { notFound } from "next/navigation";
import { buildMetadata } from "@/lib/seo/metadata";
import { loadPublicSubject, loadPublicSubjects } from "@/lib/seo/learn-data";

export const revalidate = 86400;
export const dynamicParams = true;

type Props = { params: Promise<{ subjectSlug: string }> };

export async function generateStaticParams() {
  const subjects = await loadPublicSubjects();
  return subjects.map((subject) => ({ subjectSlug: subject.slug }));
}

export async function generateMetadata({ params }: Props) {
  const { subjectSlug } = await params;
  const subject = await loadPublicSubject(subjectSlug);
  if (!subject) return {};

  return buildMetadata({
    title: `${subject.name} — WAEC, JAMB & NECO topics`,
    description: subject.description,
    path: `/learn/${subject.slug}`,
  });
}

export default async function SubjectPage({ params }: Props) {
  const { subjectSlug } = await params;
  const subject = await loadPublicSubject(subjectSlug);
  if (!subject) notFound();

  return (
    <div className="landing-container py-16">
      <nav className="text-sm ink-muted">
        <Link href="/learn" className="hover:underline">
          Subjects
        </Link>
        <span className="mx-2">/</span>
        <span>{subject.name}</span>
      </nav>

      <h1 className="mt-4 text-3xl font-bold ink sm:text-4xl">{subject.name}</h1>
      <p className="mt-3 max-w-2xl ink-muted">{subject.description}</p>

      <h2 className="mt-12 text-xl font-semibold ink">Topics</h2>
      <ul className="mt-4 grid gap-3 sm:grid-cols-2">
        {subject.topics.map((topic) => (
          <li key={topic.slug}>
            <Link
              href={`/learn/${subject.slug}/${topic.slug}`}
              className="surface block rounded-xl border border-black/5 p-4 transition hover:border-black/15"
            >
              <span className="font-medium ink">{topic.title}</span>
              {topic.description ? (
                <span className="mt-1 block line-clamp-2 text-sm ink-muted">
                  {topic.description}
                </span>
              ) : null}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

Note: the hub lists every topic, including ineligible ones whose pages 404. Task 12 removes those links once the eligibility set is available per subject — do not try to solve it here.

- [ ] **Step 3: Verify in the browser**

With the dev server running, load `http://localhost:3000/learn` and click through to a subject. Then:

```bash
curl -s http://localhost:3000/learn | grep -o '<link rel="canonical"[^>]*>'
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/learn/not-a-real-subject
```

Expected: a canonical pointing at `/learn`; `404` for the bogus slug.

- [ ] **Step 4: Typecheck and commit**

```bash
npx tsc -p tsconfig.json --noEmit
git add "src/app/(public)/learn"
git commit -m "feat(seo): add the public subject index and subject hubs"
```

---

## Task 12: The topic page

**Files:**
- Create: `src/app/(public)/learn/[subjectSlug]/[topicSlug]/page.tsx`
- Modify: `src/lib/seo/learn-data.ts` (filter the hub's topic list to eligible topics)
- Modify: `src/app/(public)/learn/[subjectSlug]/page.tsx`

**Interfaces:**
- Consumes: `loadPublicTopic`, `loadEligibleTopicParams`, `loadEligibleTopicSlugs` from `@/lib/seo/learn-data`; `SampleQuestion`; `topicPageTitle`, `topicPageDescription`.
- Produces: nothing importable.

- [ ] **Step 1: Create the topic page**

Create `src/app/(public)/learn/[subjectSlug]/[topicSlug]/page.tsx`:

```tsx
import Link from "next/link";
import { notFound } from "next/navigation";
import { SampleQuestion } from "@/components/seo/sample-question";
import { topicPageDescription, topicPageTitle } from "@/lib/seo/copy";
import { loadEligibleTopicParams, loadPublicTopic } from "@/lib/seo/learn-data";
import { buildMetadata } from "@/lib/seo/metadata";

export const revalidate = 86400;
export const dynamicParams = true;

type Props = { params: Promise<{ subjectSlug: string; topicSlug: string }> };

export async function generateStaticParams() {
  const params = await loadEligibleTopicParams();
  return params.map(({ subjectSlug, topicSlug }) => ({ subjectSlug, topicSlug }));
}

export async function generateMetadata({ params }: Props) {
  const { subjectSlug, topicSlug } = await params;
  const topic = await loadPublicTopic(subjectSlug, topicSlug);
  if (!topic) return {};

  return buildMetadata({
    title: topicPageTitle({
      topicTitle: topic.title,
      subjectName: topic.subject.name,
    }),
    description: topicPageDescription({
      topicTitle: topic.title,
      subjectName: topic.subject.name,
      description: topic.description,
      subtopicTitles: topic.subtopics.map((s) => s.title),
    }),
    path: `/learn/${subjectSlug}/${topicSlug}`,
  });
}

export default async function TopicPage({ params }: Props) {
  const { subjectSlug, topicSlug } = await params;
  const topic = await loadPublicTopic(subjectSlug, topicSlug);
  // Null covers both "no such topic" and "too thin to publish".
  if (!topic) notFound();

  const remaining = topic.questionCount - topic.samples.length;

  return (
    <div className="landing-container py-16">
      <nav className="text-sm ink-muted">
        <Link href="/learn" className="hover:underline">Subjects</Link>
        <span className="mx-2">/</span>
        <Link href={`/learn/${topic.subject.slug}`} className="hover:underline">
          {topic.subject.name}
        </Link>
        <span className="mx-2">/</span>
        <span>{topic.title}</span>
      </nav>

      <h1 className="mt-4 text-3xl font-bold ink sm:text-4xl">
        {topic.title} — {topic.subject.name}
      </h1>

      <p className="mt-4 max-w-2xl leading-relaxed ink-muted">
        {topicPageDescription({
          topicTitle: topic.title,
          subjectName: topic.subject.name,
          description: topic.description,
          subtopicTitles: topic.subtopics.map((s) => s.title),
        })}
      </p>

      {topic.waecWeight > 0 || topic.jambWeight > 0 ? (
        <dl className="mt-6 flex flex-wrap gap-4 text-sm">
          {topic.waecWeight > 0 ? (
            <div className="surface rounded-xl px-4 py-3">
              <dt className="text-xs font-bold uppercase tracking-widest ink-muted">
                WAEC weighting
              </dt>
              <dd className="mt-1 font-semibold ink">
                {Math.round(topic.waecWeight * 100)}%
              </dd>
            </div>
          ) : null}
          {topic.jambWeight > 0 ? (
            <div className="surface rounded-xl px-4 py-3">
              <dt className="text-xs font-bold uppercase tracking-widest ink-muted">
                JAMB weighting
              </dt>
              <dd className="mt-1 font-semibold ink">
                {Math.round(topic.jambWeight * 100)}%
              </dd>
            </div>
          ) : null}
        </dl>
      ) : null}

      {topic.subtopics.length > 0 ? (
        <section className="mt-12">
          <h2 className="text-xl font-semibold ink">What you&apos;ll learn</h2>
          <ul className="mt-4 space-y-3">
            {topic.subtopics.map((subtopic) => (
              <li key={subtopic.title} className="surface rounded-xl p-4">
                <p className="font-medium ink">{subtopic.title}</p>
                {subtopic.description ? (
                  <p className="mt-1 text-sm ink-muted">{subtopic.description}</p>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {topic.prerequisites.length > 0 ? (
        <section className="mt-12">
          <h2 className="text-xl font-semibold ink">Study these first</h2>
          <ul className="mt-4 space-y-2">
            {topic.prerequisites.map((prereq) => (
              <li key={prereq.slug}>
                <Link
                  href={`/learn/${topic.subject.slug}/${prereq.slug}`}
                  className="font-medium ink hover:underline"
                >
                  {prereq.title}
                </Link>
                {prereq.rationale ? (
                  <span className="ink-muted"> — {prereq.rationale}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="mt-12">
        <h2 className="text-xl font-semibold ink">
          {topic.title} past questions and answers
        </h2>
        <div className="mt-4 space-y-4">
          {topic.samples.map((question, i) => (
            <SampleQuestion key={question.id} question={question} index={i + 1} />
          ))}
        </div>

        {remaining > 0 ? (
          <div className="surface-2 mt-6 rounded-2xl p-6 text-center">
            <p className="font-semibold ink">
              {remaining} more {topic.title} question{remaining === 1 ? "" : "s"},
              with timed practice and instant marking.
            </p>
            <Link
              href="/register"
              className="mt-4 inline-block rounded-xl bg-primary px-6 py-3 font-semibold text-white"
            >
              Create a free account
            </Link>
          </div>
        ) : null}
      </section>

      {topic.siblings.length > 0 ? (
        <section className="mt-12">
          <h2 className="text-xl font-semibold ink">
            More {topic.subject.name} topics
          </h2>
          <ul className="mt-4 flex flex-wrap gap-2">
            {topic.siblings.map((sibling) => (
              <li key={sibling.slug}>
                <Link
                  href={`/learn/${topic.subject.slug}/${sibling.slug}`}
                  className="surface inline-block rounded-full px-4 py-2 text-sm ink hover:underline"
                >
                  {sibling.title}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 2: Stop the subject hub linking to pages that 404**

In `src/lib/seo/learn-data.ts`, add:

```ts
/** Slugs whose topic pages actually exist, so hubs never link into a 404. */
export const loadEligibleTopicSlugs = cache(
  async (subjectSlug: string): Promise<Set<string>> => {
    const params = await loadEligibleTopicParams();
    return new Set(
      params
        .filter((param) => param.subjectSlug === subjectSlug)
        .map((param) => param.topicSlug),
    );
  },
);
```

In `src/app/(public)/learn/[subjectSlug]/page.tsx`, import it and filter the list before rendering:

```tsx
  const eligible = await loadEligibleTopicSlugs(subjectSlug);
  const topics = subject.topics.filter((topic) => eligible.has(topic.slug));
```

Render `topics` rather than `subject.topics`, and if `topics.length === 0`, render the subject description and a link to `/past-questions` instead of an empty "Topics" heading.

- [ ] **Step 3: Verify against the real data**

With the dev server running, pick an eligible topic from the Task 9 probe output and load it. Then check the three things that matter:

```bash
curl -s "http://localhost:3000/learn/<subject>/<topic>" | grep -o '<link rel="canonical"[^>]*>'
curl -s "http://localhost:3000/learn/<subject>/<topic>" | grep -c "Answer —"
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:3000/learn/<subject>/nonexistent-topic"
```

Expected: a canonical for the topic path; 3 worked answers; `404` for the bogus topic. Also confirm an **ineligible** topic slug (one absent from the probe's list but present in the database) returns 404.

- [ ] **Step 4: Typecheck, lint and commit**

```bash
npx tsc -p tsconfig.json --noEmit
npm run lint
git add "src/app/(public)/learn" src/lib/seo/learn-data.ts
git commit -m "feat(seo): add public topic pages behind the eligibility gate

Prerequisites come from TopicEdge, so the learning-path graph doubles as
genuine internal linking. Ineligible topics 404 and are not linked from
their subject hub."
```

---

# Phase 3 — the /past-questions tree

## Task 13: The exam URL segment parser

**Files:**
- Create: `src/lib/seo/exam-segment.ts`
- Test: `scripts/test-seo-exam-segment.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `ExamType` from `@prisma/client`.
- Produces:
  - `PUBLIC_EXAM_SEGMENTS: readonly PublicExamSegment[]` — the values `"waec"`, `"jamb"`, `"neco"`
  - `type PublicExamSegment = "waec" | "jamb" | "neco"`
  - `parseExamSegment(segment: string): { segment: PublicExamSegment; examType: "WAEC" | "JAMB" | "NECO"; label: string } | null`
  - `examSegmentFor(examType: string): PublicExamSegment | null`
  - `parseYearSegment(segment: string): number | null`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-seo-exam-segment.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseExamSegment, parseYearSegment } from "../src/lib/seo/exam-segment";

test("the three public exams parse to their enum values", () => {
  assert.deepEqual(parseExamSegment("waec"), { segment: "waec", examType: "WAEC", label: "WAEC" });
  assert.deepEqual(parseExamSegment("jamb"), { segment: "jamb", examType: "JAMB", label: "JAMB" });
  assert.deepEqual(parseExamSegment("neco"), { segment: "neco", examType: "NECO", label: "NECO" });
});

test("uppercase is rejected rather than accepted", () => {
  // Accepting both cases would serve the same content at two URLs and split
  // its ranking. One canonical spelling, everything else 404s.
  assert.equal(parseExamSegment("WAEC"), null);
  assert.equal(parseExamSegment("Waec"), null);
});

test("CUSTOM is internal and has no public route", () => {
  assert.equal(parseExamSegment("custom"), null);
});

test("unknown and malformed segments are rejected", () => {
  for (const segment of ["", " ", "gce", "waec/", "../waec", "waec2019"]) {
    assert.equal(parseExamSegment(segment), null, `${segment} should be rejected`);
  }
});

test("a four-digit year parses", () => {
  assert.equal(parseYearSegment("2019"), 2019);
});

test("non-years are rejected instead of coerced", () => {
  // Number("2019abc") is NaN but parseInt would happily return 2019, which
  // would make /2019abc a duplicate of /2019.
  for (const segment of ["19", "20190", "2019abc", "abc", "", "-2019", "20.19"]) {
    assert.equal(parseYearSegment(segment), null, `${segment} should be rejected`);
  }
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx tsx --test scripts/test-seo-exam-segment.mts`
Expected: FAIL — cannot find module `../src/lib/seo/exam-segment`.

- [ ] **Step 3: Write the implementation**

Create `src/lib/seo/exam-segment.ts`:

```ts
/**
 * URL spelling of the exam boards. Lowercase only, and CUSTOM is absent: it is
 * an internal assessment type with nothing public behind it.
 *
 * Case-insensitive matching is deliberately not offered — two spellings of the
 * same page is two URLs competing for one ranking.
 */
const SEGMENT_TO_EXAM = {
  waec: "WAEC",
  jamb: "JAMB",
  neco: "NECO",
} as const;

export const PUBLIC_EXAM_SEGMENTS = Object.keys(
  SEGMENT_TO_EXAM,
) as readonly PublicExamSegment[];

export type PublicExamSegment = keyof typeof SEGMENT_TO_EXAM;
export type PublicExamType = (typeof SEGMENT_TO_EXAM)[PublicExamSegment];

export function parseExamSegment(
  segment: string,
): { segment: PublicExamSegment; examType: PublicExamType; label: string } | null {
  if (!Object.hasOwn(SEGMENT_TO_EXAM, segment)) return null;

  const key = segment as PublicExamSegment;
  const examType = SEGMENT_TO_EXAM[key];
  return { segment: key, examType, label: examType };
}

export function examSegmentFor(examType: string): PublicExamSegment | null {
  const found = PUBLIC_EXAM_SEGMENTS.find(
    (segment) => SEGMENT_TO_EXAM[segment] === examType,
  );
  return found ?? null;
}

/** Exactly four digits. Number() rather than parseInt so "2019abc" fails. */
export function parseYearSegment(segment: string): number | null {
  if (!/^\d{4}$/.test(segment)) return null;
  return Number(segment);
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx tsx --test scripts/test-seo-exam-segment.mts`
Expected: PASS, 6 tests.

- [ ] **Step 5: Register, typecheck and commit**

Append ` scripts/test-seo-exam-segment.mts` to the `test` script in `package.json`.

```bash
npx tsc -p tsconfig.json --noEmit
git add src/lib/seo/exam-segment.ts scripts/test-seo-exam-segment.mts package.json
git commit -m "feat(seo): parse exam and year URL segments in one canonical spelling"
```

---

## Task 14: The past-paper data loaders

**Files:**
- Create: `src/lib/seo/paper-data.ts`

**Interfaces:**
- Consumes: `db`; `publicQuestionWhere`; `isPaperPageEligible`, `PAPER_SAMPLE_COUNT`; `pickSamples`; `PublicExamType`, `examSegmentFor`; `PublicSampleQuestion` from `./learn-data`.
- Produces:
  - `loadExamSubjects(examType: PublicExamType): Promise<{ slug: string; name: string; questionCount: number }[]>`
  - `loadPaperYears(examType, subjectSlug): Promise<{ subject: { id: string; name: string }; years: { year: number; questionCount: number }[] } | null>`
  - `loadPaper(examType: PublicExamType, subjectSlug: string, year: number): Promise<PublicPaper | null>`
  - `loadEligiblePaperParams(): Promise<{ examSegment: string; subjectSlug: string; year: number; lastModified: Date | null }[]>`

- [ ] **Step 1: Write the loaders**

Create `src/lib/seo/paper-data.ts`:

```ts
import { cache } from "react";
import { db } from "@/lib/db";
import { PAPER_SAMPLE_COUNT, isPaperPageEligible } from "./eligibility";
import { examSegmentFor, type PublicExamType } from "./exam-segment";
import type { PublicSampleQuestion } from "./learn-data";
import { publicQuestionWhere } from "./question-scope";
import { pickSamples } from "./samples";

/**
 * Rows the public pages may draw from: objective, non-provider questions that
 * carry a year. A question with no examYear belongs to no paper.
 */
const withYear = { examYear: { not: null } } as const;

export const loadExamSubjects = cache(async (examType: PublicExamType) => {
  const rows = await db.question.groupBy({
    by: ["subjectId"],
    where: publicQuestionWhere({ examType, ...withYear }),
    _count: { _all: true },
  });

  const subjects = await db.subject.findMany({
    where: { id: { in: rows.map((row) => row.subjectId) } },
    orderBy: { name: "asc" },
    select: { id: true, slug: true, name: true },
  });

  const counts = new Map(rows.map((row) => [row.subjectId, row._count._all]));
  return subjects.map((subject) => ({
    slug: subject.slug,
    name: subject.name,
    questionCount: counts.get(subject.id) ?? 0,
  }));
});

export const loadPaperYears = cache(
  async (examType: PublicExamType, subjectSlug: string) => {
    const subject = await db.subject.findUnique({
      where: { slug: subjectSlug },
      select: { id: true, name: true },
    });
    if (!subject) return null;

    const rows = await db.question.groupBy({
      by: ["examYear"],
      where: publicQuestionWhere({ examType, subjectId: subject.id, ...withYear }),
      _count: { _all: true },
      orderBy: { examYear: "desc" },
    });

    return {
      subject,
      // Only years that clear the gate — listing a year whose page 404s is a
      // crawl trap and wastes the crawl budget on this section.
      years: rows
        .filter((row) => isPaperPageEligible({ publicQuestionCount: row._count._all }))
        .map((row) => ({ year: row.examYear as number, questionCount: row._count._all })),
    };
  },
);

export type PublicPaper = {
  examType: PublicExamType;
  year: number;
  subject: { slug: string; name: string };
  questionCount: number;
  topics: { slug: string | null; title: string; questionCount: number }[];
  samples: PublicSampleQuestion[];
  adjacentYears: { previous: number | null; next: number | null };
  lastModified: Date | null;
};

export const loadPaper = cache(
  async (
    examType: PublicExamType,
    subjectSlug: string,
    year: number,
  ): Promise<PublicPaper | null> => {
    const subject = await db.subject.findUnique({
      where: { slug: subjectSlug },
      select: { id: true, slug: true, name: true },
    });
    if (!subject) return null;

    const questions = await db.question.findMany({
      where: publicQuestionWhere({ examType, subjectId: subject.id, examYear: year }),
      select: {
        id: true, questionText: true, options: true, correctAnswer: true,
        explanation: true, createdAt: true,
        topic: { select: { slug: true, title: true } },
      },
    });

    const renderable = questions.flatMap((q) => {
      const options = q.options as Record<string, string> | null;
      if (!options || Object.keys(options).length === 0) return [];
      return [{ ...q, options }];
    });

    if (!isPaperPageEligible({ publicQuestionCount: renderable.length })) return null;

    const byTopic = new Map<string, { slug: string | null; title: string; questionCount: number }>();
    for (const question of renderable) {
      const title = question.topic?.title ?? "General";
      const existing = byTopic.get(title);
      if (existing) existing.questionCount += 1;
      else
        byTopic.set(title, {
          slug: question.topic?.slug ?? null,
          title,
          questionCount: 1,
        });
    }

    const otherYears = await db.question.groupBy({
      by: ["examYear"],
      where: publicQuestionWhere({ examType, subjectId: subject.id, ...withYear }),
      _count: { _all: true },
    });
    const eligibleYears = otherYears
      .filter((row) => isPaperPageEligible({ publicQuestionCount: row._count._all }))
      .map((row) => row.examYear as number)
      .sort((a, b) => a - b);

    const index = eligibleYears.indexOf(year);
    const newest = renderable.reduce<Date | null>(
      (latest, q) => (!latest || q.createdAt > latest ? q.createdAt : latest),
      null,
    );

    return {
      examType,
      year,
      subject: { slug: subject.slug, name: subject.name },
      questionCount: renderable.length,
      topics: [...byTopic.values()].sort((a, b) => b.questionCount - a.questionCount),
      samples: pickSamples(
        renderable,
        PAPER_SAMPLE_COUNT,
        `${examType}:${subject.id}:${year}`,
      ),
      adjacentYears: {
        previous: index > 0 ? eligibleYears[index - 1] : null,
        next: index >= 0 && index < eligibleYears.length - 1 ? eligibleYears[index + 1] : null,
      },
      lastModified: newest,
    };
  },
);

/** One query for both generateStaticParams and the sitemap. */
export const loadEligiblePaperParams = cache(async () => {
  const rows = await db.question.groupBy({
    by: ["examType", "subjectId", "examYear"],
    where: publicQuestionWhere(withYear),
    _count: { _all: true },
    _max: { createdAt: true },
  });

  const subjects = await db.subject.findMany({ select: { id: true, slug: true } });
  const slugById = new Map(subjects.map((subject) => [subject.id, subject.slug]));

  return rows.flatMap((row) => {
    if (!isPaperPageEligible({ publicQuestionCount: row._count._all })) return [];

    const examSegment = examSegmentFor(row.examType);
    const subjectSlug = slugById.get(row.subjectId);
    // CUSTOM has no public route, and a question pointing at a deleted subject
    // has no URL to live at.
    if (!examSegment || !subjectSlug || row.examYear === null) return [];

    return [{
      examSegment,
      subjectSlug,
      year: row.examYear,
      lastModified: row._max.createdAt ?? null,
    }];
  });
});
```

- [ ] **Step 2: Verify against the real database**

```bash
cat > scripts/probe-papers.mts <<'EOF'
import { loadEligiblePaperParams } from "../src/lib/seo/paper-data";
const params = await loadEligiblePaperParams();
console.log("eligible papers:", params.length);
console.log(params.slice(0, 10));
EOF
npx tsx scripts/probe-papers.mts
```

Expected: a list of exam/subject/year triples. **If the count is 0**, report it rather than lowering `PAPER_MIN_QUESTIONS` — it means the non-provider bank has no complete papers, which is a content problem, not a code problem. Then `rm scripts/probe-papers.mts` — throwaway, do not commit it.

- [ ] **Step 3: Typecheck and commit**

```bash
npx tsc -p tsconfig.json --noEmit
git add src/lib/seo/paper-data.ts
git commit -m "feat(seo): load past-paper content grouped by exam, subject and year"
```

---

## Task 15: The past-questions index pages

**Files:**
- Create: `src/app/(public)/past-questions/page.tsx`
- Create: `src/app/(public)/past-questions/[exam]/page.tsx`
- Create: `src/app/(public)/past-questions/[exam]/[subjectSlug]/page.tsx`

**Interfaces:**
- Consumes: `parseExamSegment`, `PUBLIC_EXAM_SEGMENTS`; `loadExamSubjects`, `loadPaperYears`; `buildMetadata`.
- Produces: nothing importable.

- [ ] **Step 1: Create the exam index**

Create `src/app/(public)/past-questions/page.tsx`:

```tsx
import Link from "next/link";
import { buildMetadata } from "@/lib/seo/metadata";

export const revalidate = 86400;

export const metadata = buildMetadata({
  title: "Past Questions — WAEC, JAMB and NECO",
  description:
    "Real WAEC, JAMB UTME and NECO past questions with the correct answers and worked explanations, organised by subject and year.",
  path: "/past-questions",
});

const EXAMS = [
  { segment: "waec", label: "WAEC", blurb: "The West African Senior School Certificate Examination, sat in SS3." },
  { segment: "jamb", label: "JAMB", blurb: "The UTME, taken under CBT conditions for university admission." },
  { segment: "neco", label: "NECO", blurb: "The National Examinations Council's senior certificate examination." },
];

export default function PastQuestionsIndexPage() {
  return (
    <div className="landing-container py-16">
      <h1 className="text-3xl font-bold ink sm:text-4xl">Past Questions</h1>
      <p className="mt-3 max-w-2xl ink-muted">
        Past papers by exam, subject and year — each question with its correct
        answer and a worked explanation.
      </p>

      <ul className="mt-10 grid gap-4 sm:grid-cols-3">
        {EXAMS.map((exam) => (
          <li key={exam.segment}>
            <Link
              href={`/past-questions/${exam.segment}`}
              className="surface block h-full rounded-2xl border border-black/5 p-6 transition hover:border-black/15"
            >
              <h2 className="text-lg font-bold ink">{exam.label}</h2>
              <p className="mt-2 text-sm ink-muted">{exam.blurb}</p>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 2: Create the per-exam subject list**

Create `src/app/(public)/past-questions/[exam]/page.tsx`:

```tsx
import Link from "next/link";
import { notFound } from "next/navigation";
import { PUBLIC_EXAM_SEGMENTS, parseExamSegment } from "@/lib/seo/exam-segment";
import { buildMetadata } from "@/lib/seo/metadata";
import { loadExamSubjects } from "@/lib/seo/paper-data";

export const revalidate = 86400;
export const dynamicParams = false;

type Props = { params: Promise<{ exam: string }> };

export function generateStaticParams() {
  return PUBLIC_EXAM_SEGMENTS.map((exam) => ({ exam }));
}

export async function generateMetadata({ params }: Props) {
  const { exam } = await params;
  const parsed = parseExamSegment(exam);
  if (!parsed) return {};

  return buildMetadata({
    title: `${parsed.label} Past Questions by Subject`,
    description: `Every subject with ${parsed.label} past questions on ScholarsCrib, with correct answers and worked explanations for each paper.`,
    path: `/past-questions/${parsed.segment}`,
  });
}

export default async function ExamPage({ params }: Props) {
  const { exam } = await params;
  const parsed = parseExamSegment(exam);
  if (!parsed) notFound();

  const subjects = await loadExamSubjects(parsed.examType);

  return (
    <div className="landing-container py-16">
      <nav className="text-sm ink-muted">
        <Link href="/past-questions" className="hover:underline">Past Questions</Link>
        <span className="mx-2">/</span>
        <span>{parsed.label}</span>
      </nav>

      <h1 className="mt-4 text-3xl font-bold ink sm:text-4xl">
        {parsed.label} Past Questions
      </h1>

      <ul className="mt-10 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {subjects.map((subject) => (
          <li key={subject.slug}>
            <Link
              href={`/past-questions/${parsed.segment}/${subject.slug}`}
              className="surface flex items-center justify-between rounded-xl border border-black/5 p-4 transition hover:border-black/15"
            >
              <span className="font-medium ink">{subject.name}</span>
              <span className="text-sm ink-muted">{subject.questionCount}</span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

`dynamicParams = false` here because the three exam segments are a closed set — anything else must 404 rather than render.

- [ ] **Step 3: Create the year list**

Create `src/app/(public)/past-questions/[exam]/[subjectSlug]/page.tsx`:

```tsx
import Link from "next/link";
import { notFound } from "next/navigation";
import { parseExamSegment } from "@/lib/seo/exam-segment";
import { buildMetadata } from "@/lib/seo/metadata";
import { loadPaperYears } from "@/lib/seo/paper-data";

export const revalidate = 86400;
export const dynamicParams = true;

type Props = { params: Promise<{ exam: string; subjectSlug: string }> };

export async function generateMetadata({ params }: Props) {
  const { exam, subjectSlug } = await params;
  const parsed = parseExamSegment(exam);
  if (!parsed) return {};
  const data = await loadPaperYears(parsed.examType, subjectSlug);
  if (!data) return {};

  return buildMetadata({
    title: `${parsed.label} ${data.subject.name} Past Questions by Year`,
    description: `Every ${parsed.label} ${data.subject.name} paper on ScholarsCrib, year by year, with correct answers and worked explanations.`,
    path: `/past-questions/${parsed.segment}/${subjectSlug}`,
  });
}

export default async function SubjectYearsPage({ params }: Props) {
  const { exam, subjectSlug } = await params;
  const parsed = parseExamSegment(exam);
  if (!parsed) notFound();

  const data = await loadPaperYears(parsed.examType, subjectSlug);
  if (!data || data.years.length === 0) notFound();

  return (
    <div className="landing-container py-16">
      <nav className="text-sm ink-muted">
        <Link href="/past-questions" className="hover:underline">Past Questions</Link>
        <span className="mx-2">/</span>
        <Link href={`/past-questions/${parsed.segment}`} className="hover:underline">
          {parsed.label}
        </Link>
        <span className="mx-2">/</span>
        <span>{data.subject.name}</span>
      </nav>

      <h1 className="mt-4 text-3xl font-bold ink sm:text-4xl">
        {parsed.label} {data.subject.name} Past Questions
      </h1>

      <ul className="mt-10 grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
        {data.years.map((entry) => (
          <li key={entry.year}>
            <Link
              href={`/past-questions/${parsed.segment}/${subjectSlug}/${entry.year}`}
              className="surface block rounded-xl border border-black/5 p-4 text-center transition hover:border-black/15"
            >
              <span className="block text-lg font-bold ink">{entry.year}</span>
              <span className="text-xs ink-muted">
                {entry.questionCount} questions
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 4: Verify**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/past-questions
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/past-questions/waec
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/past-questions/WAEC
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/past-questions/custom
```

Expected: `200`, `200`, `404`, `404`.

- [ ] **Step 5: Typecheck and commit**

```bash
npx tsc -p tsconfig.json --noEmit
git add "src/app/(public)/past-questions"
git commit -m "feat(seo): add the past-questions exam, subject and year indexes"
```

---

## Task 16: The past-paper page

**Files:**
- Create: `src/app/(public)/past-questions/[exam]/[subjectSlug]/[year]/page.tsx`

**Interfaces:**
- Consumes: `parseExamSegment`, `parseYearSegment`; `loadPaper`, `loadEligiblePaperParams`; `paperPageTitle`, `paperPageDescription`; `SampleQuestion`.
- Produces: nothing importable.

- [ ] **Step 1: Create the page**

Create `src/app/(public)/past-questions/[exam]/[subjectSlug]/[year]/page.tsx`:

```tsx
import Link from "next/link";
import { notFound } from "next/navigation";
import { SampleQuestion } from "@/components/seo/sample-question";
import { paperPageDescription, paperPageTitle } from "@/lib/seo/copy";
import { parseExamSegment, parseYearSegment } from "@/lib/seo/exam-segment";
import { buildMetadata } from "@/lib/seo/metadata";
import { loadEligiblePaperParams, loadPaper } from "@/lib/seo/paper-data";

export const revalidate = 86400;
export const dynamicParams = true;

type Props = {
  params: Promise<{ exam: string; subjectSlug: string; year: string }>;
};

export async function generateStaticParams() {
  const params = await loadEligiblePaperParams();
  return params.map((param) => ({
    exam: param.examSegment,
    subjectSlug: param.subjectSlug,
    year: String(param.year),
  }));
}

/** Resolve the three segments, or null if any of them is not a real page. */
async function resolve(props: Props) {
  const { exam, subjectSlug, year } = await props.params;
  const parsed = parseExamSegment(exam);
  const parsedYear = parseYearSegment(year);
  if (!parsed || parsedYear === null) return null;

  const paper = await loadPaper(parsed.examType, subjectSlug, parsedYear);
  if (!paper) return null;

  return { parsed, paper };
}

export async function generateMetadata(props: Props) {
  const resolved = await resolve(props);
  if (!resolved) return {};
  const { parsed, paper } = resolved;

  return buildMetadata({
    title: paperPageTitle({
      exam: parsed.label,
      year: paper.year,
      subjectName: paper.subject.name,
    }),
    description: paperPageDescription({
      exam: parsed.label,
      year: paper.year,
      subjectName: paper.subject.name,
      questionCount: paper.questionCount,
      topicCount: paper.topics.length,
    }),
    path: `/past-questions/${parsed.segment}/${paper.subject.slug}/${paper.year}`,
  });
}

export default async function PaperPage(props: Props) {
  const resolved = await resolve(props);
  if (!resolved) notFound();
  const { parsed, paper } = resolved;

  const remaining = paper.questionCount - paper.samples.length;

  return (
    <div className="landing-container py-16">
      <nav className="text-sm ink-muted">
        <Link href="/past-questions" className="hover:underline">Past Questions</Link>
        <span className="mx-2">/</span>
        <Link href={`/past-questions/${parsed.segment}`} className="hover:underline">
          {parsed.label}
        </Link>
        <span className="mx-2">/</span>
        <Link
          href={`/past-questions/${parsed.segment}/${paper.subject.slug}`}
          className="hover:underline"
        >
          {paper.subject.name}
        </Link>
        <span className="mx-2">/</span>
        <span>{paper.year}</span>
      </nav>

      <h1 className="mt-4 text-3xl font-bold ink sm:text-4xl">
        {parsed.label} {paper.year} {paper.subject.name} Past Questions and Answers
      </h1>

      <p className="mt-4 max-w-2xl leading-relaxed ink-muted">
        {paperPageDescription({
          exam: parsed.label,
          year: paper.year,
          subjectName: paper.subject.name,
          questionCount: paper.questionCount,
          topicCount: paper.topics.length,
        })}
      </p>

      <section className="mt-12">
        <h2 className="text-xl font-semibold ink">Sample questions</h2>
        <div className="mt-4 space-y-4">
          {paper.samples.map((question, i) => (
            <SampleQuestion key={question.id} question={question} index={i + 1} />
          ))}
        </div>

        {remaining > 0 ? (
          <div className="surface-2 mt-6 rounded-2xl p-6 text-center">
            <p className="font-semibold ink">
              {remaining} more question{remaining === 1 ? "" : "s"} from this
              paper, sittable under real CBT conditions.
            </p>
            <Link
              href="/register"
              className="mt-4 inline-block rounded-xl bg-primary px-6 py-3 font-semibold text-white"
            >
              Practise the full paper free
            </Link>
          </div>
        ) : null}
      </section>

      <section className="mt-12">
        <h2 className="text-xl font-semibold ink">What this paper covers</h2>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="ink-muted">
                <th className="py-2 font-semibold">Topic</th>
                <th className="py-2 font-semibold">Questions</th>
              </tr>
            </thead>
            <tbody>
              {paper.topics.map((topic) => (
                <tr key={topic.title} className="border-t border-black/5">
                  <td className="py-2">
                    {topic.slug ? (
                      <Link
                        href={`/learn/${paper.subject.slug}/${topic.slug}`}
                        className="ink hover:underline"
                      >
                        {topic.title}
                      </Link>
                    ) : (
                      <span className="ink">{topic.title}</span>
                    )}
                  </td>
                  <td className="py-2 ink-muted">{topic.questionCount}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {paper.adjacentYears.previous || paper.adjacentYears.next ? (
        <nav className="mt-12 flex justify-between text-sm">
          {paper.adjacentYears.previous ? (
            <Link
              href={`/past-questions/${parsed.segment}/${paper.subject.slug}/${paper.adjacentYears.previous}`}
              className="ink hover:underline"
            >
              ← {parsed.label} {paper.adjacentYears.previous} {paper.subject.name}
            </Link>
          ) : (
            <span />
          )}
          {paper.adjacentYears.next ? (
            <Link
              href={`/past-questions/${parsed.segment}/${paper.subject.slug}/${paper.adjacentYears.next}`}
              className="ink hover:underline"
            >
              {parsed.label} {paper.adjacentYears.next} {paper.subject.name} →
            </Link>
          ) : (
            <span />
          )}
        </nav>
      ) : null}
    </div>
  );
}
```

The topic-breakdown table links only where `topic.slug` exists, but a linked topic may still be ineligible and 404. Accept that for now — Task 19's link audit is where it gets caught, and a single wrong link is a smaller problem than duplicating the eligibility query on every row.

- [ ] **Step 2: Verify against real data**

Using a triple from the Task 14 probe:

```bash
curl -s "http://localhost:3000/past-questions/waec/<subject>/<year>" | grep -o '<title>[^<]*</title>'
curl -s "http://localhost:3000/past-questions/waec/<subject>/<year>" | grep -c "Answer —"
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:3000/past-questions/waec/<subject>/1066"
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:3000/past-questions/waec/<subject>/2019abc"
```

Expected: a title of the form `WAEC <year> <Subject> Past Questions and Answers | ScholarsCrib`; 5 worked answers; `404` for the unavailable year and `404` for the malformed year.

- [ ] **Step 3: Typecheck, lint and commit**

```bash
npx tsc -p tsconfig.json --noEmit
npm run lint
git add "src/app/(public)/past-questions"
git commit -m "feat(seo): add past-paper pages with worked samples and a topic breakdown"
```

---

# Phase 4 — discovery and sharing

## Task 17: Structured data

**Files:**
- Create: `src/lib/seo/jsonld.ts`
- Create: `src/components/seo/json-ld.tsx`
- Test: `scripts/test-seo-jsonld.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `absoluteUrl`, `siteName`, `siteDescription`.
- Produces:
  - `organisationJsonLd()`, `websiteJsonLd()`
  - `faqPageJsonLd(faqs: readonly { question: string; answer: string }[])`
  - `breadcrumbJsonLd(crumbs: readonly { name: string; path: string }[])`
  - `courseJsonLd(input: { name: string; description: string; path: string })`
  - `quizJsonLd(input: { name: string; path: string; about: string; questions: readonly { questionText: string; options: Record<string, string>; correctAnswer: string; explanation: string }[] })`
  - `serialiseJsonLd(data: unknown): string`
  - `<JsonLd data={...} />`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-seo-jsonld.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  breadcrumbJsonLd,
  courseJsonLd,
  faqPageJsonLd,
  organisationJsonLd,
  quizJsonLd,
  serialiseJsonLd,
  websiteJsonLd,
} from "../src/lib/seo/jsonld";
import { siteUrl } from "../src/lib/seo/site";

test("organisation and website declare their schema type and identity", () => {
  const org = organisationJsonLd();
  assert.equal(org["@context"], "https://schema.org");
  assert.equal(org["@type"], "Organization");
  assert.equal(org.url, `${siteUrl}/`);

  const site = websiteJsonLd();
  assert.equal(site["@type"], "WebSite");
  assert.equal(site.inLanguage, "en-NG");
});

test("breadcrumbs are positioned from one and carry absolute urls", () => {
  const crumbs = breadcrumbJsonLd([
    { name: "Subjects", path: "/learn" },
    { name: "Biology", path: "/learn/biology" },
  ]);
  assert.equal(crumbs["@type"], "BreadcrumbList");
  assert.equal(crumbs.itemListElement.length, 2);
  assert.equal(crumbs.itemListElement[0].position, 1);
  assert.equal(crumbs.itemListElement[1].position, 2);
  assert.equal(crumbs.itemListElement[1].item, `${siteUrl}/learn/biology`);
});

test("the faq markup mirrors the answers exactly", () => {
  const faq = faqPageJsonLd([{ question: "Q1?", answer: "A1." }]);
  assert.equal(faq["@type"], "FAQPage");
  assert.equal(faq.mainEntity[0]["@type"], "Question");
  assert.equal(faq.mainEntity[0].name, "Q1?");
  assert.equal(faq.mainEntity[0].acceptedAnswer.text, "A1.");
});

test("a course points at its own canonical url and names the provider", () => {
  const course = courseJsonLd({
    name: "Cell Structure",
    description: "D",
    path: "/learn/biology/cell-structure",
  });
  assert.equal(course["@type"], "Course");
  assert.equal(course.url, `${siteUrl}/learn/biology/cell-structure`);
  assert.equal(course.provider["@type"], "Organization");
});

test("a quiz marks up every option and the accepted answer", () => {
  const quiz = quizJsonLd({
    name: "WAEC 2019 Biology",
    path: "/past-questions/waec/biology/2019",
    about: "Biology",
    questions: [
      {
        questionText: "What is a cell?",
        options: { A: "A unit", B: "A rock", C: "A gas", D: "A star" },
        correctAnswer: "B",
        explanation: "Because.",
      },
    ],
  });
  assert.equal(quiz["@type"], "Quiz");
  const question = quiz.hasPart[0];
  assert.equal(question["@type"], "Question");
  assert.equal(question.eduQuestionType, "Multiple choice");
  assert.equal(question.suggestedAnswer.length, 3);
  assert.equal(question.acceptedAnswer.text, "A rock");
  assert.equal(question.acceptedAnswer.comment.text, "Because.");
});

test("a quiz with an unmatched correct answer is not marked up as a quiz", () => {
  // Claiming an accepted answer that is not among the options is invalid
  // markup, and invalid markup on a rich result is worse than none.
  const quiz = quizJsonLd({
    name: "N",
    path: "/x",
    about: "A",
    questions: [
      {
        questionText: "Q",
        options: { A: "one", B: "two" },
        correctAnswer: "Z",
        explanation: "E",
      },
    ],
  });
  assert.equal(quiz, null);
});

test("a closing script tag in the content cannot break out of the script block", () => {
  // The classic JSON-LD XSS: an explanation containing </script> ends the
  // block early and everything after it is parsed as HTML.
  const output = serialiseJsonLd({ text: "a </script><img onerror=alert(1)> b" });
  assert.ok(!output.includes("</script>"), output);
  assert.match(output, /\\u003c/);
});

test("serialised output round-trips back to the same data", () => {
  const data = { a: 1, b: "two <three>" };
  assert.deepEqual(JSON.parse(serialiseJsonLd(data)), data);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx tsx --test scripts/test-seo-jsonld.mts`
Expected: FAIL — cannot find module `../src/lib/seo/jsonld`.

- [ ] **Step 3: Write the implementation**

Create `src/lib/seo/jsonld.ts`:

```ts
import { absoluteUrl, siteDescription, siteName } from "./site";

/**
 * Structured data describes only what is on the page. Marking up gated
 * questions, or an answer that is not among the rendered options, is a
 * rich-result policy violation — so quizJsonLd returns null rather than
 * emitting something invalid.
 */
const CONTEXT = "https://schema.org";

export function organisationJsonLd() {
  return {
    "@context": CONTEXT,
    "@type": "Organization",
    name: siteName,
    url: absoluteUrl("/"),
    description: siteDescription,
    logo: absoluteUrl("/icon.svg"),
    areaServed: "NG",
  } as const;
}

export function websiteJsonLd() {
  return {
    "@context": CONTEXT,
    "@type": "WebSite",
    name: siteName,
    url: absoluteUrl("/"),
    description: siteDescription,
    inLanguage: "en-NG",
    publisher: { "@type": "Organization", name: siteName, url: absoluteUrl("/") },
  } as const;
}

export function faqPageJsonLd(
  faqs: readonly { question: string; answer: string }[],
) {
  return {
    "@context": CONTEXT,
    "@type": "FAQPage",
    mainEntity: faqs.map((faq) => ({
      "@type": "Question",
      name: faq.question,
      acceptedAnswer: { "@type": "Answer", text: faq.answer },
    })),
  } as const;
}

export function breadcrumbJsonLd(
  crumbs: readonly { name: string; path: string }[],
) {
  return {
    "@context": CONTEXT,
    "@type": "BreadcrumbList",
    itemListElement: crumbs.map((crumb, index) => ({
      "@type": "ListItem",
      position: index + 1,
      name: crumb.name,
      item: absoluteUrl(crumb.path),
    })),
  } as const;
}

export function courseJsonLd({
  name,
  description,
  path,
}: {
  name: string;
  description: string;
  path: string;
}) {
  return {
    "@context": CONTEXT,
    "@type": "Course",
    name,
    description,
    url: absoluteUrl(path),
    inLanguage: "en-NG",
    provider: { "@type": "Organization", name: siteName, url: absoluteUrl("/") },
  } as const;
}

export function quizJsonLd({
  name,
  path,
  about,
  questions,
}: {
  name: string;
  path: string;
  about: string;
  questions: readonly {
    questionText: string;
    options: Record<string, string>;
    correctAnswer: string;
    explanation: string;
  }[];
}) {
  const hasPart = questions.flatMap((question) => {
    const accepted = question.options[question.correctAnswer];
    if (!accepted) return [];

    return [{
      "@type": "Question",
      eduQuestionType: "Multiple choice",
      text: question.questionText,
      acceptedAnswer: {
        "@type": "Answer",
        text: accepted,
        comment: { "@type": "Comment", text: question.explanation },
      },
      suggestedAnswer: Object.entries(question.options)
        .filter(([letter]) => letter !== question.correctAnswer)
        .map(([, text]) => ({ "@type": "Answer", text })),
    }];
  });

  if (hasPart.length !== questions.length || hasPart.length === 0) return null;

  return {
    "@context": CONTEXT,
    "@type": "Quiz",
    name,
    url: absoluteUrl(path),
    about: { "@type": "Thing", name: about },
    inLanguage: "en-NG",
    hasPart,
  } as const;
}

/**
 * `<` is escaped so a "</script>" inside any string cannot terminate the
 * script block and turn page content into markup.
 */
export function serialiseJsonLd(data: unknown): string {
  return JSON.stringify(data).replace(/</g, "\\u003c");
}
```

Create `src/components/seo/json-ld.tsx`:

```tsx
import { serialiseJsonLd } from "@/lib/seo/jsonld";

/** Renders nothing visible. `data` of null renders no tag at all. */
export function JsonLd({ data }: { data: unknown }) {
  if (!data) return null;

  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: serialiseJsonLd(data) }}
    />
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx tsx --test scripts/test-seo-jsonld.mts`
Expected: PASS, 8 tests.

- [ ] **Step 5: Register, typecheck and commit**

Append ` scripts/test-seo-jsonld.mts` to the `test` script in `package.json`.

```bash
npx tsc -p tsconfig.json --noEmit
git add src/lib/seo/jsonld.ts src/components/seo/json-ld.tsx scripts/test-seo-jsonld.mts package.json
git commit -m "feat(seo): build structured data with script-breakout escaping"
```

---

## Task 18: Wire structured data into the pages

**Files:**
- Modify: `src/app/(public)/page.tsx`
- Modify: `src/app/(public)/learn/[subjectSlug]/[topicSlug]/page.tsx`
- Modify: `src/app/(public)/past-questions/[exam]/[subjectSlug]/[year]/page.tsx`

**Interfaces:**
- Consumes: `JsonLd`; the builders from Task 17; `FAQS` from `@/components/landing/faq-data`.
- Produces: nothing importable.

- [ ] **Step 1: Add Organization, WebSite and FAQPage to the landing page**

In `src/app/(public)/page.tsx`, add the imports and render the three blocks inside the fragment, before `<Hero />`:

```tsx
import { JsonLd } from "@/components/seo/json-ld";
import { FAQS } from "@/components/landing/faq-data";
import { faqPageJsonLd, organisationJsonLd, websiteJsonLd } from "@/lib/seo/jsonld";
```

```tsx
      <JsonLd data={organisationJsonLd()} />
      <JsonLd data={websiteJsonLd()} />
      <JsonLd data={faqPageJsonLd(FAQS)} />
```

- [ ] **Step 2: Add Course and BreadcrumbList to the topic page**

In the topic page, add:

```tsx
import { JsonLd } from "@/components/seo/json-ld";
import { breadcrumbJsonLd, courseJsonLd } from "@/lib/seo/jsonld";
```

and render, as the first children inside the outer `<div>`:

```tsx
      <JsonLd
        data={breadcrumbJsonLd([
          { name: "Subjects", path: "/learn" },
          { name: topic.subject.name, path: `/learn/${topic.subject.slug}` },
          { name: topic.title, path: `/learn/${topic.subject.slug}/${topic.slug}` },
        ])}
      />
      <JsonLd
        data={courseJsonLd({
          name: `${topic.title} — ${topic.subject.name}`,
          description: topicPageDescription({
            topicTitle: topic.title,
            subjectName: topic.subject.name,
            description: topic.description,
            subtopicTitles: topic.subtopics.map((s) => s.title),
          }),
          path: `/learn/${topic.subject.slug}/${topic.slug}`,
        })}
      />
```

- [ ] **Step 3: Add Quiz and BreadcrumbList to the paper page**

In the paper page, add:

```tsx
import { JsonLd } from "@/components/seo/json-ld";
import { breadcrumbJsonLd, quizJsonLd } from "@/lib/seo/jsonld";
```

and render, as the first children inside the outer `<div>`:

```tsx
      <JsonLd
        data={breadcrumbJsonLd([
          { name: "Past Questions", path: "/past-questions" },
          { name: parsed.label, path: `/past-questions/${parsed.segment}` },
          {
            name: paper.subject.name,
            path: `/past-questions/${parsed.segment}/${paper.subject.slug}`,
          },
          {
            name: String(paper.year),
            path: `/past-questions/${parsed.segment}/${paper.subject.slug}/${paper.year}`,
          },
        ])}
      />
      <JsonLd
        data={quizJsonLd({
          name: `${parsed.label} ${paper.year} ${paper.subject.name}`,
          path: `/past-questions/${parsed.segment}/${paper.subject.slug}/${paper.year}`,
          about: paper.subject.name,
          // Only the samples: marking up the gated questions would claim
          // content the page does not show.
          questions: paper.samples,
        })}
      />
```

- [ ] **Step 4: Validate the emitted JSON**

With the dev server running, extract and parse each block:

```bash
curl -s http://localhost:3000/ \
  | grep -o '<script type="application/ld+json">[^<]*</script>' | wc -l
curl -s "http://localhost:3000/past-questions/waec/<subject>/<year>" \
  | python -c "import re,sys,json; [json.loads(m) for m in re.findall(r'application/ld\+json\">(.*?)</script>', sys.stdin.read(), re.S)]; print('valid json')"
```

Expected: 3 blocks on the landing page; `valid json` for the paper page. Then paste one page's HTML into Google's Rich Results Test and confirm the Quiz result is detected with no errors.

- [ ] **Step 5: Typecheck and commit**

```bash
npx tsc -p tsconfig.json --noEmit
git add "src/app/(public)"
git commit -m "feat(seo): emit Organization, FAQPage, Course, Quiz and breadcrumb markup"
```

---

## Task 19: Sharded sitemaps

**Files:**
- Create: `src/lib/seo/sitemap-shape.ts`
- Create: `src/app/sitemap.ts`
- Test: `scripts/test-seo-sitemap.mts`
- Modify: `package.json`

**Interfaces:**
- Consumes: `isGatedPath`, `SITEMAP_SHARDS`, `SitemapShard` from `./paths` (Task 3 defines them — do NOT redefine here); `absoluteUrl`; `loadEligibleTopicParams`; `loadEligiblePaperParams`; `loadPublicSubjects`.
- Produces:
  - `type SitemapRecord = { path: string; lastModified?: Date | null; changeFrequency?: "daily" | "weekly" | "monthly" | "yearly"; priority?: number }`
  - `buildSitemap(records: readonly SitemapRecord[]): MetadataRoute.Sitemap`
  - `shardFor(path: string): SitemapShard`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-seo-sitemap.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSitemap, shardFor } from "../src/lib/seo/sitemap-shape";
import { siteUrl } from "../src/lib/seo/site";

test("paths land in the shard that matches their tree", () => {
  assert.equal(shardFor("/"), "static");
  assert.equal(shardFor("/past-questions"), "static");
  assert.equal(shardFor("/learn"), "static");
  assert.equal(shardFor("/learn/biology"), "learn");
  assert.equal(shardFor("/learn/biology/cell-structure"), "learn");
  assert.equal(shardFor("/past-questions/waec"), "past-questions");
  assert.equal(shardFor("/past-questions/waec/biology/2019"), "past-questions");
});

test("entries become absolute urls", () => {
  const [entry] = buildSitemap([{ path: "/learn/biology" }]);
  assert.equal(entry.url, `${siteUrl}/learn/biology`);
});

test("a gated path is dropped rather than published", () => {
  // The single most damaging sitemap bug: advertising URLs that answer with a
  // login redirect.
  const entries = buildSitemap([
    { path: "/learn/biology" },
    { path: "/dashboard" },
    { path: "/practice/past-questions" },
    { path: "/api/health" },
  ]);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].url, `${siteUrl}/learn/biology`);
});

test("a duplicate url appears once", () => {
  const entries = buildSitemap([
    { path: "/learn/biology" },
    { path: "/learn/biology/" },
    { path: "learn/biology" },
  ]);
  assert.equal(entries.length, 1);
});

test("lastModified is omitted, never invented, when there is no timestamp", () => {
  // Stamping new Date() on every entry tells crawlers the whole site changed
  // on every build, which trains them to ignore the field.
  const [absent] = buildSitemap([{ path: "/learn/biology", lastModified: null }]);
  assert.ok(!("lastModified" in absent), JSON.stringify(absent));

  const [undef] = buildSitemap([{ path: "/learn/chemistry" }]);
  assert.ok(!("lastModified" in undef), JSON.stringify(undef));
});

test("a real timestamp is carried through", () => {
  const when = new Date("2026-01-02T03:04:05.000Z");
  const [entry] = buildSitemap([{ path: "/learn/biology", lastModified: when }]);
  assert.equal(entry.lastModified, when);
});

test("the newer of two duplicate timestamps wins", () => {
  const older = new Date("2025-01-01T00:00:00.000Z");
  const newer = new Date("2026-01-01T00:00:00.000Z");
  const entries = buildSitemap([
    { path: "/learn/biology", lastModified: older },
    { path: "/learn/biology", lastModified: newer },
  ]);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].lastModified, newer);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx tsx --test scripts/test-seo-sitemap.mts`
Expected: FAIL — cannot find module `../src/lib/seo/sitemap-shape`.

- [ ] **Step 3: Write the shaping logic**

Create `src/lib/seo/sitemap-shape.ts`:

```ts
import type { MetadataRoute } from "next";
import { type SitemapShard, isGatedPath } from "./paths";
import { absoluteUrl } from "./site";

export type SitemapRecord = {
  path: string;
  lastModified?: Date | null;
  changeFrequency?: "daily" | "weekly" | "monthly" | "yearly";
  priority?: number;
};

/**
 * Shards exist because the exam-by-subject-by-year cross product runs to
 * thousands of URLs: one unbounded sitemap is both a build cost and a
 * 50,000-URL ceiling. The index pages stay in `static` so the small shard is
 * the one crawlers hit first.
 */
export function shardFor(path: string): SitemapShard {
  const segments = path.split("/").filter(Boolean);
  if (segments.length <= 1) return "static";
  if (segments[0] === "learn") return "learn";
  if (segments[0] === "past-questions") return "past-questions";
  return "static";
}

export function buildSitemap(
  records: readonly SitemapRecord[],
): MetadataRoute.Sitemap {
  const byUrl = new Map<string, SitemapRecord & { url: string }>();

  for (const record of records) {
    // Gated URLs are filtered here rather than at each call site, so a new
    // caller cannot forget.
    if (isGatedPath(`/${record.path.replace(/^\/+/, "")}`)) continue;

    const url = absoluteUrl(record.path);
    const existing = byUrl.get(url);
    if (
      existing &&
      !(record.lastModified && (!existing.lastModified || record.lastModified > existing.lastModified))
    ) {
      continue;
    }
    byUrl.set(url, { ...record, url });
  }

  return [...byUrl.values()].map((record) => ({
    url: record.url,
    // Omitted, not faked, when absent.
    ...(record.lastModified ? { lastModified: record.lastModified } : {}),
    ...(record.changeFrequency ? { changeFrequency: record.changeFrequency } : {}),
    ...(record.priority !== undefined ? { priority: record.priority } : {}),
  }));
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx tsx --test scripts/test-seo-sitemap.mts`
Expected: PASS, 7 tests.

- [ ] **Step 5: Write the sitemap route**

Create `src/app/sitemap.ts`:

```ts
import type { MetadataRoute } from "next";
import { notFound } from "next/navigation";
import { loadEligibleTopicParams, loadPublicSubjects } from "@/lib/seo/learn-data";
import { loadEligiblePaperParams } from "@/lib/seo/paper-data";
import { SITEMAP_SHARDS, type SitemapShard } from "@/lib/seo/paths";
import { buildSitemap, type SitemapRecord } from "@/lib/seo/sitemap-shape";

export const revalidate = 86400;

export function generateSitemaps() {
  return SITEMAP_SHARDS.map((id) => ({ id }));
}

async function recordsFor(shard: SitemapShard): Promise<SitemapRecord[]> {
  if (shard === "static") {
    return [
      { path: "/", changeFrequency: "weekly", priority: 1 },
      { path: "/learn", changeFrequency: "weekly", priority: 0.8 },
      { path: "/past-questions", changeFrequency: "weekly", priority: 0.8 },
    ];
  }

  if (shard === "learn") {
    const [subjects, topics] = await Promise.all([
      loadPublicSubjects(),
      loadEligibleTopicParams(),
    ]);
    return [
      ...subjects.map((subject) => ({
        path: `/learn/${subject.slug}`,
        changeFrequency: "monthly" as const,
        priority: 0.7,
      })),
      ...topics.map((topic) => ({
        path: `/learn/${topic.subjectSlug}/${topic.topicSlug}`,
        lastModified: topic.lastModified,
        changeFrequency: "monthly" as const,
        priority: 0.6,
      })),
    ];
  }

  const papers = await loadEligiblePaperParams();
  const branches = new Set<string>();
  for (const paper of papers) {
    branches.add(`/past-questions/${paper.examSegment}`);
    branches.add(`/past-questions/${paper.examSegment}/${paper.subjectSlug}`);
  }

  return [
    ...[...branches].map((path) => ({
      path,
      changeFrequency: "monthly" as const,
      priority: 0.7,
    })),
    ...papers.map((paper) => ({
      path: `/past-questions/${paper.examSegment}/${paper.subjectSlug}/${paper.year}`,
      lastModified: paper.lastModified,
      changeFrequency: "yearly" as const,
      priority: 0.6,
    })),
  ];
}

function isShard(value: string): value is SitemapShard {
  return (SITEMAP_SHARDS as readonly string[]).includes(value);
}

/**
 * As of Next.js 16 the id from generateSitemaps arrives as a promise resolving
 * to a string — see
 * node_modules/next/dist/docs/01-app/03-api-reference/04-functions/generate-sitemaps.md
 * ("v16.0.0: The id values returned from generateSitemaps are now passed as a
 * promise that resolves to a string"). Destructuring it as a plain value, or
 * casting it to the shard union without checking, is wrong.
 */
export default async function sitemap(props: {
  id: Promise<string>;
}): Promise<MetadataRoute.Sitemap> {
  const id = await props.id;
  if (!isShard(id)) notFound();

  return buildSitemap(await recordsFor(id));
}
```

- [ ] **Step 6: Verify every sitemapped URL actually resolves**

This is the check that matters: a sitemap that advertises 404s is worse than no sitemap.

```bash
for shard in static learn past-questions; do
  echo "--- $shard ---"
  curl -s "http://localhost:3000/sitemap/$shard.xml" | grep -c "<loc>"
done

# Every URL in the learn shard must answer 200.
curl -s http://localhost:3000/sitemap/learn.xml \
  | grep -o '<loc>[^<]*</loc>' | sed 's/<[^>]*>//g' \
  | while read -r url; do
      code=$(curl -s -o /dev/null -w "%{http_code}" "$url")
      [ "$code" = "200" ] || echo "BAD $code $url"
    done
```

Expected: a `<loc>` count above zero per shard, and **no `BAD` lines**. Repeat for the `past-questions` shard. Any `BAD` line means the eligibility predicate and the page guard have diverged — fix that, do not exclude the URL by hand.

- [ ] **Step 7: Register, typecheck and commit**

Append ` scripts/test-seo-sitemap.mts` to the `test` script in `package.json`.

```bash
npx tsc -p tsconfig.json --noEmit
git add src/lib/seo/sitemap-shape.ts src/app/sitemap.ts scripts/test-seo-sitemap.mts package.json
git commit -m "feat(seo): publish sharded sitemaps driven by the eligibility gate"
```

---

## Task 20: Open Graph images

**Files:**
- Create: `src/app/opengraph-image.tsx`
- Create: `src/app/(public)/learn/[subjectSlug]/[topicSlug]/opengraph-image.tsx`
- Create: `src/app/(public)/past-questions/[exam]/[subjectSlug]/[year]/opengraph-image.tsx`

**Interfaces:**
- Consumes: `ImageResponse` from `next/og`; `loadPublicTopic`; `loadPaper`; `parseExamSegment`, `parseYearSegment`; `siteName`.
- Produces: nothing importable.

- [ ] **Step 1: Create the site-wide default**

Create `src/app/opengraph-image.tsx`:

```tsx
import { ImageResponse } from "next/og";
import { siteName } from "@/lib/seo/site";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = `${siteName} — WAEC, JAMB and NECO preparation`;

export default function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "80px",
          background: "linear-gradient(135deg, #1d4ed8 0%, #3730a3 100%)",
          color: "white",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ fontSize: 34, opacity: 0.85, letterSpacing: 4 }}>
          WAEC · JAMB · NECO
        </div>
        <div style={{ fontSize: 82, fontWeight: 800, marginTop: 20, lineHeight: 1.1 }}>
          {siteName}
        </div>
        <div style={{ fontSize: 36, opacity: 0.9, marginTop: 24 }}>
          Learn smarter. Score higher.
        </div>
      </div>
    ),
    size,
  );
}
```

- [ ] **Step 2: Create the topic image**

Create `src/app/(public)/learn/[subjectSlug]/[topicSlug]/opengraph-image.tsx`. Same `size`, `contentType` and gradient wrapper as Step 1, but the copy comes from the topic:

```tsx
import { ImageResponse } from "next/og";
import { loadPublicTopic } from "@/lib/seo/learn-data";
import { siteName } from "@/lib/seo/site";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default async function Image({
  params,
}: {
  params: Promise<{ subjectSlug: string; topicSlug: string }>;
}) {
  const { subjectSlug, topicSlug } = await params;
  const topic = await loadPublicTopic(subjectSlug, topicSlug);

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "80px",
          background: "linear-gradient(135deg, #1d4ed8 0%, #3730a3 100%)",
          color: "white",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ fontSize: 34, opacity: 0.85, letterSpacing: 3 }}>
          {topic?.subject.name.toUpperCase() ?? "SCHOLARSCRIB"}
        </div>
        <div style={{ fontSize: 72, fontWeight: 800, marginTop: 20, lineHeight: 1.1 }}>
          {topic?.title ?? siteName}
        </div>
        <div style={{ fontSize: 32, opacity: 0.9, marginTop: 28 }}>
          Past questions with worked answers
        </div>
      </div>
    ),
    size,
  );
}
```

- [ ] **Step 3: Create the paper image**

Create `src/app/(public)/past-questions/[exam]/[subjectSlug]/[year]/opengraph-image.tsx`, identical in structure, with the eyebrow reading `{label} {year}` and the headline `{subject} Past Questions`:

```tsx
import { ImageResponse } from "next/og";
import { parseExamSegment, parseYearSegment } from "@/lib/seo/exam-segment";
import { loadPaper } from "@/lib/seo/paper-data";
import { siteName } from "@/lib/seo/site";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default async function Image({
  params,
}: {
  params: Promise<{ exam: string; subjectSlug: string; year: string }>;
}) {
  const { exam, subjectSlug, year } = await params;
  const parsed = parseExamSegment(exam);
  const parsedYear = parseYearSegment(year);
  const paper =
    parsed && parsedYear !== null
      ? await loadPaper(parsed.examType, subjectSlug, parsedYear)
      : null;

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "80px",
          background: "linear-gradient(135deg, #1d4ed8 0%, #3730a3 100%)",
          color: "white",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ fontSize: 34, opacity: 0.85, letterSpacing: 3 }}>
          {parsed && parsedYear !== null ? `${parsed.label} ${parsedYear}` : siteName}
        </div>
        <div style={{ fontSize: 72, fontWeight: 800, marginTop: 20, lineHeight: 1.1 }}>
          {paper ? `${paper.subject.name} Past Questions` : "Past Questions"}
        </div>
        <div style={{ fontSize: 32, opacity: 0.9, marginTop: 28 }}>
          {paper
            ? `${paper.questionCount} questions with worked answers`
            : "With worked answers"}
        </div>
      </div>
    ),
    size,
  );
}
```

- [ ] **Step 4: Verify the images render and are advertised**

```bash
curl -s -o /dev/null -w "%{http_code} %{content_type}\n" http://localhost:3000/opengraph-image
curl -s "http://localhost:3000/learn/<subject>/<topic>" | grep -o '<meta property="og:image"[^>]*>'
```

Expected: `200 image/png`, and an absolute `og:image` URL on the topic page. Open the image URL in a browser and confirm the text is not clipped — long topic titles are the failure case to look for.

- [ ] **Step 5: Typecheck and commit**

```bash
npx tsc -p tsconfig.json --noEmit
git add src/app/opengraph-image.tsx "src/app/(public)"
git commit -m "feat(seo): generate branded OG images per topic and per paper"
```

---

## Task 21: Full-suite verification and the production build

**Files:**
- Modify: `package.json` (only if a test file was missed)

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

- [ ] **Step 1: Run the whole test suite**

Run: `npm test`
Expected: PASS, including all eight new `test-seo-*.mts` files (`metadata`,
`paths`, `eligibility`, `samples`, `copy`, `exam-segment`, `jsonld`, `sitemap`). If any new file is missing from the run, add it to the `test` script.

- [ ] **Step 2: Lint and typecheck**

```bash
npm run lint
npx tsc -p tsconfig.json --noEmit
```

Expected: both clean.

- [ ] **Step 3: Run a production build**

Run: `npm run build`
Expected: success. Record the reported count of prerendered `/learn/...` and `/past-questions/...` pages, and how long the build took. If the build time has become unreasonable, say so with the number rather than silently reducing `generateStaticParams` output.

- [ ] **Step 4: Audit the built output**

```bash
npm run start &
sleep 5
curl -s http://localhost:3000/robots.txt
curl -s http://localhost:3000/sitemap/static.xml
curl -s http://localhost:3000/dashboard | grep -o '<meta name="robots"[^>]*>'
```

Expected: robots.txt lists every gated prefix; the static sitemap lists three URLs; `/dashboard` carries `noindex, nofollow`.

- [ ] **Step 5: Report, then commit any fixes**

Report to the user: the number of live topic pages, the number of live paper pages, the build time, and anything that came out lower than expected. Do not claim the surface is complete without those numbers.

```bash
git add -A
git commit -m "test(seo): register the full SEO suite and verify the production build"
```

---

## Deferred, deliberately

These are real gaps, left out so the plan stays one shippable unit. Do not silently fold them in:

- **Guides/blog at `/guides`** — the user excluded it during brainstorming.
- **Editorial subject hubs** — `/learn/[subjectSlug]` is a navigational index by decision, not by oversight.
- **`ItemList` markup on index pages** — worth adding once the indexes prove they attract impressions.
- **Analytics and Search Console** — measurement is a separate project, and this plan ships nothing that depends on it.
- **The topic-breakdown link audit** on paper pages, which can still link to an ineligible topic (noted in Task 16).
