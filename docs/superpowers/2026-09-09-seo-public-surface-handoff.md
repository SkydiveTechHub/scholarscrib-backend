# SEO public surface — handoff

Branch: `ft/seo-public-surface`, cut from `ft/try-sdash` at `452c462`.
27 commits, 55 files, +3192/−135. Spec and plan are committed under `docs/superpowers/`.

## What shipped

A public, indexable content surface where previously only `/`, `/login` and
`/register` were reachable.

| Surface | Pages |
|---|---|
| `/learn` + subject hubs | 1 + 6 |
| Topic pages | 71 |
| `/past-questions` + exam/subject branches | 1 + 10 |
| Past-paper pages | 47 |
| **Sitemapped total** | **137** |

Plus: `robots.txt`, three sharded sitemaps, `noindex` on every gated route,
canonical/OG/Twitter tags on every public page, JSON-LD (Organization,
WebSite, FAQPage, BreadcrumbList, Course, Quiz), dynamic OG images, icons and
a web manifest.

Nine new test files, all registered in `npm test`:
`test-seo-metadata`, `test-seo-paths`, `test-public-routes`,
`test-seo-eligibility`, `test-seo-samples`, `test-seo-copy`,
`test-seo-exam-segment`, `test-seo-jsonld`, `test-seo-sitemap`.

## Before this ships — manual steps I cannot do from here

1. **Set `NEXT_PUBLIC_APP_URL` to the real production host.** It is currently
   `http://localhost:3000` in `.env`, so `robots.txt` advertises
   `Sitemap: http://localhost:3000/sitemap/...`. Everything canonical derives
   from this one variable; nothing else needs changing. The fallback when it is
   unset is `https://prepwell.ng` — correct that in `src/lib/seo/site.ts` if
   that is not your domain.
2. **Run Google's Rich Results Test** on one topic page and one paper page.
   This environment has no external network, so schema conformance was proven
   only by local `JSON.parse`. The Quiz markup is the valuable one — it targets
   the Practice Problems rich result.
3. **Submit the three sitemap URLs to Google Search Console** once deployed:
   `/sitemap/static.xml`, `/sitemap/learn.xml`, `/sitemap/past-questions.xml`.
   There is deliberately no `/sitemap.xml` index — `generateSitemaps` does not
   emit one.
4. **Look at one OG image.** Long topic titles may clip; this was never
   visually inspected.

## The content ceiling — the finding that matters most

The surface is bounded by public (non-provider, objective) question supply, not
by the code.

**Topics:** 38 of 44 subjects have zero eligible topics. Only six carry the
tree — biology 20/20, commerce 17/23, financial-accounting 15/15,
economics 14/14, physics 4/49, mathematics 1/44.

Of the 547 ineligible topics, **547 fail on question supply and 0 fail on
content**. Every one already has an adequate description or subtopics. Nothing
needs writing; questions need promoting.

**Papers:** 47 eligible — JAMB 45, WAEC 2, NECO 0. The past-questions tree is
effectively JAMB-only, and `/past-questions/neco` 404s by design rather than
publishing an empty page.

Mathematics (44 topics, 1 eligible) and Physics (49 topics, 4 eligible) are the
highest-leverage targets: the topic structure already exists, only the
questions are missing. Roughly three public objective questions per topic would
unlock a page.

Lowering the thresholds would not fix this. It would publish thinner pages, and
most of those 547 topics would still fail at any honest threshold.

## Design decisions worth knowing

**One eligibility rule, shared everywhere.** `keepRenderable` in
`learn-data.ts` is called by all four loaders. `generateStaticParams`,
`notFound()`, hub links and the sitemap all derive from the same source, so a
page cannot be prerendered or sitemapped yet 404. This is structural, not a
property of today's data — it was rebuilt that way after a review proved the
two paths could diverge.

**Every internal link points at a publishable page.** The same bug — rendering
links to ineligible targets — appeared in three separate tasks before it was
made an explicit invariant. Subject lists, topic lists, siblings,
prerequisites, paper topic rows and prev/next year links are all filtered. On a
paper page, rows for ineligible topics stay (so counts still sum to the paper's
real total) but render as plain text rather than links.

**Structured data describes only what is on the page.** The Quiz block covers
the five rendered samples, never the full paper. `quizJsonLd` returns `null`
rather than emit an accepted answer absent from the options. The Course block
states only `courseMode: online`, a `courseWorkload` derived from real
`Topic.estimatedMinutes`, and free `offers` — no invented date, instructor,
location, price or rating, even though adding them might improve rich-result
eligibility.

**The proxy had to change.** `src/proxy.ts` allowlisted exactly one public path
by equality (`"/"`), so every page this branch adds would have 307-redirected
anonymous crawlers to `/login`. The allowlist moved to a tested predicate in
`src/lib/public-routes.ts`. Its tests enumerate every gated tree and assert each
stays closed; a security review ran 46 adversarial inputs (path traversal,
encoded traversal, segment smuggling, case variation, null bytes) and confirmed
no gated route is anonymously reachable.

## Known trade-offs left in place

- Sample questions are drawn only from non-provider questions, per your
  licensing decision. This is the direct cause of the content ceiling above.
- `revalidate = 86400`, so new content appears within a day rather than
  immediately.
- The same description string serves as both the meta description and the
  visible intro paragraph on topic and paper pages. It reads as a meta tag
  pasted into the body — worth rewriting as real prose.
