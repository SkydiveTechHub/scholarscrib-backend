# Public SEO Surface — Design

**Date:** 2026-09-09
**Status:** Approved for planning

## Problem

ScholarsCrib's entire content library sits behind authentication. The public
surface is three pages: `/` (landing), `/login`, `/register`. There is no
`metadataBase`, no canonical URLs, no Open Graph or Twitter tags, no OG
images, no `robots.txt`, no sitemap, and no structured data. The gated tree
is not marked `noindex`, so login walls are eligible for indexing.

The consequence is that the queries this product exists to answer — "WAEC
2019 Biology past questions and answers", "JAMB Chemistry mole concept" —
land on competitors, because ScholarsCrib has no page to rank. Technical
hygiene alone will not change that: with three public pages there is
nothing to rank. The fix requires a public content surface.

## Goal

Build an indexable public surface that earns organic search traffic for
exam- and topic-intent queries, converts it into registrations, and does
not give away the paid product.

**Non-goals:** editorial subject hubs, a guides/blog system, paid search,
link building, analytics instrumentation. Each is a separate project.

## Strategy: teaser pages, gated depth

Every public page carries genuine substance — enough that it deserves to
rank on its own merits — then gates the depth. Concretely: real topic
explainers, real exam-weighting data, real prerequisite structure, and a
small number of complete sample questions with worked explanations. The
full question bank, practice tooling, CBT mode, and progress tracking stay
behind login.

## URL taxonomy

A new route group, `src/app/(public)/`, with a layout that reuses the
landing `Nav` and `Footer`:

```
/                                             landing (moved into the group)
/learn                                        subject index
/learn/[subjectSlug]                          thin subject hub
/learn/[subjectSlug]/[topicSlug]              topic page
/past-questions                               exam index
/past-questions/[exam]                        subject list for an exam
/past-questions/[exam]/[subjectSlug]          year list for exam+subject
/past-questions/[exam]/[subjectSlug]/[year]   past-paper page
```

The two page types that carry the strategy are the topic page and the
past-paper page. The four index routes are not content types; they are the
crawl paths the money pages hang from, and are built as thin navigational
indexes.

`[exam]` accepts the lowercased `ExamType` values `waec`, `jamb`, `neco`.
`CUSTOM` is internal and has no public route.

No route collides with the gated tree: the authenticated equivalents live
at `/classroom/[subjectSlug]/[topicSlug]` and
`/practice/past-questions/[subjectSlug]`, which become `noindex`, so there
is no duplicate-content competition between a public page and its gated
twin.

## Eligibility: the thin-content gate

Generating a page per topic and per exam-subject-year cross product
produces hundreds of URLs. Auto-generated pages with nothing on them are
how a site earns a doorway-page classification, which is sitewide and hard
to reverse. Eligibility is therefore a hard predicate, not a guideline. A
page that fails it returns `notFound()` **and** is absent from the sitemap;
the two must agree, or the sitemap advertises 404s.

- **Topic page** is eligible when it has a non-empty `description` **or**
  at least 2 subtopics, **and** at least 3 eligible sample questions.
- **Past-paper page** is eligible when the `(examType, examYear,
  subjectId)` triple has at least 10 eligible questions.

The predicate lives in `src/lib/seo/eligibility.ts` as pure functions over
plain count and field inputs, so it is unit-tested without a database and
cannot drift between the page and the sitemap.

## Question sourcing: non-provider only

Public sample questions are drawn **only** from questions with no
`ProviderQuestion` row (`providerQuestion: { is: null }`). Provider-sourced
content (currently SDASH) is licensed for use inside the product;
publishing it on indexable pages is republication and is not clearly
permitted. This constraint is part of the eligibility count above — a topic
whose questions are all provider-sourced is not eligible, and that is the
correct outcome.

A single shared query helper, `publicQuestionWhere()`, expresses the filter
so it cannot be forgotten at one call site.

## Page composition

### Topic page

- `h1`: `{Topic.title} — {Subject.name}`
- Intro paragraph from `Topic.description`; when absent, a template built
  from the subject name and subtopic titles.
- "What you'll learn": `Subtopic.title` and `description`, in `orderIndex`
  order.
- Exam relevance rendered from `Topic.waecWeight` and `Topic.jambWeight`.
- **Prerequisites from `TopicEdge`**, each an internal link to its own
  topic page. The learning-path graph already encodes genuine topical
  relationships, which makes it high-quality internal linking obtained for
  free.
- 3 sample questions with options, correct answer, and full explanation.
- Sibling topics in the subject, and a link to the subject's past-paper
  years.
- CTA naming the real remaining count: "N more questions in this topic".

### Past-paper page

- `h1`: `{Exam} {Year} {Subject} Past Questions and Answers`
- Intro built from real counts (questions available, topics covered). No
  invented claims.
- 5 sample questions with answers and explanations.
- Topic-breakdown table, each row linking to that topic page.
- Previous and next year links, for crawl depth across the year set.
- CTA into gated CBT practice for the full paper.

### Sample selection must be deterministic

Samples are chosen by a stable ordering keyed on the page's own identity
(topic id, or the exam-subject-year triple), never randomly and never by
`createdAt` alone. A page whose visible content changes on every
regeneration looks unstable to crawlers and cannot be reasoned about when
debugging rankings.

## Metadata infrastructure

- `src/lib/seo/site.ts` — `siteUrl` derived from `NEXT_PUBLIC_APP_URL`
  (already consumed by `src/lib/billing/paystack.ts`) with the production
  fallback `https://scholarscrib.com`, trailing slash stripped. Also `siteName`
  and default description. `NEXT_PUBLIC_APP_URL` is already documented in
  `.env.example`, so no new environment variable is introduced.
- `src/lib/seo/metadata.ts` — `buildMetadata({ title, description, path,
  image?, noindex? })` returning a Next `Metadata` object with canonical,
  Open Graph, and Twitter fields derived from one input, so the three can
  never disagree. Pure and unit-tested, including URL-join edge cases
  (leading and trailing slashes, empty path).
- Root layout gains `metadataBase`, `title.template` (`%s | ScholarsCrib`),
  and OG/Twitter defaults.
- `generateViewport` is used for `themeColor`. The `themeColor`,
  `colorScheme`, and `viewport` keys inside `metadata` have been deprecated
  since Next.js 14 and must not be used.
- Every public dynamic route exports `generateMetadata` that shares a
  `cache()`-wrapped loader with the page body, so metadata and render cost
  one database round trip rather than two.

### Marking the gated tree noindex

`robots: { index: false, follow: false }` is added via metadata on
`src/app/(dashboard)/layout.tsx`, the admin console layout, and
`src/app/(auth)/layout.tsx`. This is independent of `robots.txt`:
`Disallow` prevents crawling but does not remove an already-indexed URL,
whereas the meta directive does.

## Structured data

`src/lib/seo/jsonld.ts` builds plain objects; a small `<JsonLd>` component
serialises them with `</script>` escaped.

- `Organization` and `WebSite` on `/`.
- `FAQPage` on `/`, from the landing FAQ content.
- `BreadcrumbList` on every deep public page.
- `Course` on topic pages.
- `Quiz` with `hasPart` `Question` entries on past-paper pages. This is the
  schema behind Google's Practice Problems rich result and is the
  highest-leverage markup available for this niche.

Structured data describes only what is visible on the page. Marking up the
gated questions would be a rich-result policy violation.

### FAQ content extraction

`FAQS` is currently a module-local constant inside
`src/components/landing/faq.tsx`, a client component. It moves to a shared
module imported by both the component and the `FAQPage` builder, so the
markup cannot drift from the rendered copy.

## robots.txt and sitemaps

`src/app/robots.ts` allows crawling generally and disallows `/api/`,
`/admin/`, and every gated segment (`/dashboard`, `/classroom`, `/practice`,
`/flashcards`, `/performance`, `/study-plan`, `/achievements`, `/library`,
`/settings`, `/login`, `/register`), and points at the sitemap index.

`src/app/sitemap.ts` uses `generateSitemaps()` to shard into `static`,
`learn`, and `past-questions`. Sharding is not premature: the
exam-by-subject-by-year cross product runs to thousands of URLs, and a
single sitemap built from one unbounded query is both a build-time cost and
a 50,000-URL ceiling.

`lastModified` is derived from real data — the maximum `Question.createdAt`
in scope. `Topic` has no `updatedAt` column, so where no honest timestamp
exists the field is **omitted**. Emitting `new Date()` would tell crawlers
every page changed on every build, which trains them to ignore the signal.

Partitioning logic is pure and tested for three properties: every eligible
URL appears in exactly one shard, no gated path ever appears, and no URL
appears twice.

## Rendering and delivery

- `generateStaticParams` on both dynamic route families, enumerating only
  eligible pages.
- `export const revalidate = 86400` and `dynamicParams = true`, so new
  subjects, topics, and exam years become available without a redeploy.
  This is the pre-Cache-Components model, which remains correct because
  `cacheComponents` is not enabled in `next.config.ts`. If Cache Components
  is adopted later, these exports are removed in favour of `use cache` and
  `cacheLife` — a follow-up, not part of this work.
- Dynamic Open Graph images via `ImageResponse` (`next/og`): a branded
  default at the root, plus per-topic and per-paper images.
- `favicon.ico`, `apple-icon`, and `manifest.ts` are added. `public/`
  currently ships only the default Next.js placeholder SVGs.

## Landing page move

`src/app/page.tsx` moves to `src/app/(public)/page.tsx`. The URL does not
change; the page gains the shared public layout, nav, footer, and OG
defaults.

**This move breaks the landing nav.** `NAV_LINKS` in
`src/components/landing/nav.tsx` uses bare fragment hrefs (`#features`,
`#pricing`). Once that nav renders on `/learn/...` pages, those resolve
against the current path and scroll nowhere. They become root-relative
(`/#features`). The same check applies to `Footer` and any other landing
component with fragment links.

## Testing

Following the repository convention of `scripts/test-*.mts` run under
`node:test`, added to the `test` script. The shaping logic is deliberately
factored into pure functions in `src/lib/seo/*` so that all of it is
testable without a database:

- `test-seo-metadata.mts` — canonical and OG/Twitter derivation, URL-join
  edge cases, `noindex` handling.
- `test-seo-eligibility.mts` — threshold boundaries for both page types,
  and that provider-sourced questions do not count toward them.
- `test-seo-jsonld.mts` — shape of each schema type, and `</script>`
  escaping.
- `test-seo-sitemap.mts` — shard partitioning, absence of gated paths,
  absence of duplicates, `lastModified` omitted rather than faked.

Route-level decisions follow the pattern of `scripts/test-admin-route.mts`,
which unit-tests an extracted pure classifier rather than driving a server.
So the `[exam]` segment parser (`waec` to `ExamType.WAEC`, rejecting
`custom` and unknown values) and the eligibility predicate are both plain
functions tested directly; the route bodies only call them. Final
verification that the rendered pages carry the right tags is a manual pass
against the dev server at the end of each phase.

## Phasing

1. **Foundation** — `src/lib/seo/*`, root layout metadata, `robots.ts`,
   `noindex` on the gated tree, icons and manifest, landing page move and
   the fragment-link fix.
2. **`/learn` tree** — subject index, subject hub, topic page, eligibility
   and sample selection.
3. **`/past-questions` tree** — exam index, subject list, year list,
   past-paper page.
4. **Discovery and sharing** — sharded sitemaps, dynamic OG images, full
   JSON-LD coverage.

Each phase is independently shippable and leaves the site in a valid state.

## Risks

- **Thin content at scale.** Mitigated by the eligibility gate; the sitemap
  and the 404 behaviour are driven by one shared predicate so they cannot
  disagree.
- **Cannibalising the paid product.** Mitigated by fixed small sample counts
  and by keeping all practice tooling gated.
- **Provider licensing.** Mitigated by sourcing public samples only from
  non-provider questions.
- **Build time.** `generateStaticParams` over thousands of pages will
  lengthen builds. Eligibility filtering runs in the query, not in
  application code, and if builds become painful the fallback is fewer
  prerendered params with `dynamicParams` covering the remainder.
