# Admin Lessons — browse by track, subject and class

## Problem

`/admin/lessons` lists every topic in the database in one flat table. The page
loads all subjects, all their topics, and each topic's lesson blocks in a single
query, then renders one row per topic. As the curriculum grows this becomes both
slow to load and impossible to scan: an admin who wants to upload a note for
SS2 Physics has to hunt for it among hundreds of unrelated rows.

The curriculum already has the structure needed to narrow this down. It is just
not exposed:

- `Subject.trackCategory` — `CORE | SCIENCE | ARTS | COMMERCIAL | VOCATIONAL`
- `CurriculumLevel.classLevel` — `SS1 | SS2 | SS3`
- `CurriculumLevel.term` — `FIRST | SECOND | THIRD`
- `Topic` belongs to one `Subject` and one `CurriculumLevel`

## Solution

Put a filter bar above the table: **Track → Subject → Class → Term**. Subject is
required; nothing lists until one is chosen. Class and Term are optional
narrowing on top of it.

Selection lives in the URL (`?track=&subject=&class=&term=`), so a filtered view
is bookmarkable and browser-back after an upload returns to the same list.

### Why subject-required rather than all-optional

A subject's topic list is short enough to scan (tens, not hundreds) and is the
unit an admin actually works in. Requiring Track *and* Class as well would cost
three picks before anything appears, when the admin usually already knows the
subject they want. Requiring nothing keeps the page-weight problem this change
exists to remove.

### Track is a flat category, not student semantics

`relevantTrackCategories()` in `src/lib/subjects.ts` expands a student's track to
`CORE + <track>`, because every candidate sits English and Mathematics whatever
their track. That is right for students and wrong here: an admin browsing
content is looking for *where a subject lives*, and one subject appearing under
four different tracks makes the list harder, not easier, to reason about.

So the Track dropdown is a plain category filter — `All tracks`, `Core`,
`Science`, `Arts`, `Commercial`, `Vocational` — and each subject appears under
exactly one. `relevantTrackCategories()` is not used by this page.

## Components

### `src/app/admin/lessons/page.tsx` (server component)

Reads the four search params and runs at most two queries:

1. **Always** — `subject.findMany({ id, name, trackCategory })`, ordered by
   name. Cheap; feeds the Track and Subject dropdowns.
2. **Only when a subject is selected** — that subject's topics, ordered by
   `curriculumLevel.classLevel`, then `curriculumLevel.term`, then
   `orderIndex`, selecting `curriculumLevel: { classLevel, term }` alongside the
   existing `topicLessonSelectWith({ blocks: true, createdBy: true })` fragment.
   Class and Term, when set, are applied as `where` clauses on the relation.

With no subject selected the second query does not run, so the unfiltered page
does no topic work at all.

A third, deliberately light query (class level and term only) backs the Class
and Term dropdowns. It has to span the subject's *whole* topic set: deriving the
options from the filtered rows would leave the selected class as the only class
on offer and strand the admin there.

The lesson-resolution logic is unchanged: `resolveTopicLesson`, `parseBlocks`
and `isAuthored` continue to produce block count and authored status, still via
the canonical `topicLessonSelectWith` fragment so this page and Classroom cannot
drift apart.

Postgres orders enum columns by declaration order, so `classLevel` sorts
`SS1 → SS2 → SS3` and `term` sorts `FIRST → SECOND → THIRD` without a manual
sort key.

### `src/components/admin/lesson-filter-bar.tsx` (client component)

The four `<select>` controls. On change it rewrites the URL with
`router.replace`, preserving the params that are still valid:

- Changing **Track** clears Subject, Class and Term.
- Changing **Subject** clears Class and Term.
- Changing **Class** clears Term.
- Changing **Term** clears nothing.

Each dropdown only offers values that exist: a track with no subjects is
omitted, and Class/Term list only the levels the selected subject actually has
topics for, so a subject with no third-term content does not offer "Third".

The current filter arrives as a prop from the server component rather than from
`useSearchParams`, so this component needs no Suspense boundary — the server has
already parsed and validated the params, and reading them twice would let the
two disagree.

### `src/lib/admin-lesson-browse.ts` (pure functions, unit tested)

The logic worth isolating from both React and Prisma:

- `normaliseFilter(params)` — coerces raw query strings into a valid filter,
  dropping unknown tracks, class levels and terms rather than passing junk into
  a Prisma `where`.
- `tracksWithSubjects(subjects)` — the Track options that have at least one
  subject, in `TRACK_CATEGORIES` order, with labels from `TRACK_LABELS`.
- `subjectsForTrack(subjects, track)` — the Subject options for the chosen
  track, or all subjects when track is `All`.
- `levelsPresent(topics, classLevel)` — the distinct class levels present in the
  selected subject's topics, and the distinct terms present within the selected
  class (or across all classes when none is selected). Feeds the Class and Term
  dropdowns. Scoping terms to the class is why changing Class clears Term: a
  term valid for SS1 may not exist for SS2.
- `groupByClass(rows)` — groups topic rows into ordered class sections with a
  per-section count, for rendering when Class is `All`.

## Rendering

- **No subject selected** — a short prompt in place of the table: choose a track
  and subject to list its topics. Not an empty grid.
- **Subject selected, Class = All** — rows grouped under class headings
  (`SS1 · 24 topics`), each row showing Topic, Term, Blocks, Status, Action.
- **Subject and Class selected** — a single flat table, same columns.
- **Filter matches nothing** — an explicit "No topics match this filter" row.

The summary line above the table (`N of M topics have an authored lesson note`)
reflects the current filter, not the whole database.

The Term column stays on every row even when Term is filtered, so the value is
never hidden; the Subject column is dropped, since the subject is now named in
the filter bar.

The Action link is unchanged: `/admin/lessons/upload?topicId=<id>`, labelled
"Replace" or "Upload" depending on authored status.

## Testing

`scripts/test-admin-lesson-browse.mts`, added to the `test` script in
`package.json`, covering:

- `normaliseFilter` drops an unknown track, class level and term, and preserves
  valid ones.
- `normaliseFilter` drops Class and Term when no subject is given, since they
  cannot apply.
- `tracksWithSubjects` omits a category with no subjects and returns the rest in
  `TRACK_CATEGORIES` order.
- `subjectsForTrack` returns every subject when track is unset, and only that
  category's subjects otherwise — a `CORE` subject does not appear under
  `SCIENCE`.
- `levelsPresent` returns only the class levels and terms the topics actually
  use, in enum order, and scopes the terms to the selected class.
- `groupByClass` orders sections `SS1 → SS2 → SS3` and counts each section.

## Out of scope

- Text search over topic titles — the filtered list is short enough to scan.
- Pagination — a single subject's topics are tens of rows.
- Persisting the last-used filter to storage — the URL already does this.
- Any change to the upload page or to lesson parsing.
