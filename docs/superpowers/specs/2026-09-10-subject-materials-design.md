# Subject Materials — Design

**Date:** 2026-09-10
**Status:** Approved for planning

## Problem

The student library at `/library` is filled from a hardcoded map,
`SUBJECT_RESOURCES` in `prisma/seed.ts`, seeded into `SubjectResource` rows.
Nobody can change it without a code deploy. There is no admin surface for
library content at all: the console covers lessons, questions, students,
team and audit, and stops there.

The seeded rows are placeholders — third-party links chosen to make the
shelf look populated, not material anyone vetted. They also leak sideways:
`SubjectResource` is the fallback source for classroom topic resources
(`src/lib/classroom-topic.ts`), so placeholder library content appears under
lessons too.

## Goal

Empty the library of seeded placeholder content, and give admins a console
section that files real materials — PDFs, images, videos and links — under a
subject.

**Non-goals:** topic-level or lesson-level materials (`LessonResource` is a
separate table with its own flow and is untouched), student-submitted
materials, a draft/publish workflow, class-level targeting, full-text search
across materials, bulk import. Each is a separate piece of work.

## Decisions

Four decisions were settled during brainstorming and the design follows
them; they are recorded here because each closed off a plausible
alternative.

1. **Wipe rows and stop seeding.** Not a soft archive. The seeded rows have
   no value worth preserving, and leaving the seed block in place means a
   future `prisma db seed` silently repopulates the shelf.
2. **Free/Premium is the only visibility lever.** No `isPublished` flag. A
   material an admin saves is live.
3. **Four material types**, as a Prisma enum rather than the free-text
   string in use today.
4. **Cloudinary for PDFs and images; a pasted URL for video and links.**
   Uploads go directly to Cloudinary from the browser against a
   server-issued signature, not proxied through the Next server.

### Why not self-host video

Cloudinary and Supabase Storage both serve a video file as one fixed
bitrate with no adaptive streaming. Most of this audience is on metered
Nigerian mobile data, where that is the difference between a video that
plays and one that buffers. A YouTube or Vimeo embed gives adaptive
bitrate and a CDN at no cost. Self-hosting is worse for the student *and*
more expensive, so `VIDEO` means a link to a recognised host.

### Why the browser uploads directly

A serverless deploy caps request bodies at roughly 4.5MB. A proxied upload
route — the shape `src/app/api/user/avatar/route.ts` uses for 2MB avatars —
therefore cannot carry a textbook PDF. Signing an upload server-side and
letting the browser send the file to Cloudinary removes the ceiling, costs
no server bandwidth, and makes upload progress available for free.

The trade-off is orphaned files: an admin who uploads and then abandons the
form leaves an asset in Cloudinary with no row pointing at it. This is
accepted. Orphans are cheap, harmless, and identifiable by folder — a sweep
can be written if the volume ever justifies one.

## Data model

`resourceType` stops being free text:

```prisma
enum MaterialType {
  PDF
  IMAGE
  VIDEO
  LINK
}
```

`SubjectResource` keeps every field it has today — `title`, `description`,
`url`, `author`, `isFree`, `orderIndex` — and gains none. Only the
`resourceType` column changes, from `String` to `MaterialType`.

`LessonResource.resourceType` stays a string. It is a different table
serving a different flow, and widening this change to cover it would be
scope the goal does not ask for.

### The clear

One migration, in this order:

1. `DELETE FROM "SubjectResource";`
2. Create the `MaterialType` enum type.
3. Retype `SubjectResource.resourceType` to `MaterialType`.

Deleting first is what makes the retype safe: with no rows there is no
`textbook -> ?` mapping to invent, so no `USING` clause and no data-loss
judgement call.

`SUBJECT_RESOURCES` and `seedSubjectResources()` are removed from
`prisma/seed.ts` in the same change, along with the call site at the bottom
of the seed script.

### Applying the migration

`prisma migrate deploy` cannot reach Supabase from the development machine.
The migration file is written by hand with LF line endings — CRLF silently
drifts Prisma's migration checksums — and its SQL is applied through the
Supabase SQL Editor. The editor reports success on batches it has only half
applied, so the result is verified against the catalog rather than the
editor's message:

```sql
select column_name, data_type, udt_name
from information_schema.columns
where table_name = 'SubjectResource' and column_name = 'resourceType';

select count(*) from "SubjectResource";
```

Expected: `udt_name = 'MaterialType'`, count `0`.

### Call sites that read `resourceType`

Three places assume the old string values and change with the enum:

- `src/components/library/library-view.tsx` — `RESOURCE_ICONS` drops
  `textbook`, `worksheet` and `past_paper`, and gains `IMAGE`. `isReadable()`
  keys off `resourceType === "PDF"` instead of sniffing the URL for a `.pdf`
  suffix. This is both more honest and a fix: a Cloudinary PDF URL does not
  necessarily end in `.pdf`, so the current check would refuse to open one.
- `src/components/classroom/topic-resources.tsx` and
  `src/lib/classroom-topic.ts` — the subject-resource fallback keeps working
  unchanged; it simply returns nothing until materials are added.
- `src/lib/library.ts` — unchanged. Premium URL-stripping stays exactly as
  it is, and the enum does not touch it.

## Admin section

### Route

`/admin/library`, inside the `(console)` group, with a nav entry added to
`src/lib/admin-nav.ts` in the Content group beside Lessons.

The page is a server component. It resolves the subject list, and a selected
subject's materials are listed in the existing `AdminTable` ordered by
`orderIndex`, with add, edit, delete and reorder controls. Deletion is
confirmed through the existing `ConfirmDialog`.

### Authorization

Every active admin can create, edit and delete materials — the tier that
`canEditStudent` uses, not the owner tier. Managing library content is
reversible: a deleted material can be re-added, and nothing about it locks
anyone out of anything. Routes call the guard themselves; hiding a control
in the UI is presentation, not authorization.

### Pure rules

`src/lib/admin-material.ts`, database-free so it is unit-testable the way
`src/lib/admin-access.ts` is:

- `requiresUpload(type)` — `PDF` and `IMAGE` are uploaded; `VIDEO` and
  `LINK` carry a pasted URL.
- `validateMaterialUrl(type, url)` — https only. For `VIDEO`, the host must
  be a recognised video host (YouTube, Vimeo), so a raw `.mp4` link cannot
  quietly become self-hosted video through the back door.
- `signingParamsFor(type)` — the folder, allowed formats and byte cap that
  constrain one signed upload.
- `nextOrderIndex(existing)` — where a newly added material lands.

### Routes

All behind `requireAdminApi()`, all audited through `recordAudit`:

| Route | Purpose |
|---|---|
| `GET /admin/api/materials?subjectId=` | list a subject's materials |
| `POST /admin/api/materials` | create |
| `PATCH /admin/api/materials/[id]` | edit, including `orderIndex` — reordering is a `PATCH`, not its own route |
| `DELETE /admin/api/materials/[id]` | remove |
| `POST /admin/api/materials/sign` | signature, timestamp and params for one upload |

New audit actions: `material.create`, `material.update`, `material.delete`.
The `AdminAudit.action` comment in the schema is extended to list them.

Request bodies are validated with Zod schemas added to the existing
`src/lib/validators.ts`.

### Upload flow

1. Admin picks a type. For `PDF` and `IMAGE` the form shows a file input;
   for `VIDEO` and `LINK` it shows a URL field.
2. On file selection the form calls `POST /admin/api/materials/sign`, which
   returns a signature over params constrained by `signingParamsFor()`.
3. The browser uploads directly to Cloudinary and reports progress.
4. The returned `secure_url` populates the form's URL field.
5. Saving posts the whole material to `POST /admin/api/materials`.

`src/lib/cloudinary.ts` gains `signUpload()` beside the existing avatar
helper, sharing its credential check and `UploadRejectedError` rather than
starting a second module with a second notion of "configured".

The client component is `src/components/admin/material-form.tsx`.

### Cloudinary PDF delivery

Cloudinary disables PDF and ZIP delivery by default on new accounts.
Uploads succeed and the stored URL returns 401 until Settings → Security →
"PDF and ZIP files delivery" is enabled. This is surfaced as an explicit
hint in the admin UI beside the PDF upload control, because the failure
otherwise presents as a broken link with no indication of the cause.

## Testing

`scripts/test-admin-material.mts`, run under `node --test` and added to the
`test` script in `package.json`, covering the pure rules:

- `requiresUpload` for each of the four types
- `validateMaterialUrl` rejects http, accepts https
- `validateMaterialUrl` rejects a direct `.mp4` for `VIDEO`, accepts a
  YouTube and a Vimeo URL
- `signingParamsFor` produces a distinct folder and format allowlist per
  type, and a byte cap
- `nextOrderIndex` on an empty list and on a list with gaps

Route handlers and the form are exercised manually, which is how
`admin-question` is covered in this repo.

## Verification

- `npm run lint`, `npx tsc --noEmit`, `npm test` all clean.
- The catalog queries above return `MaterialType` and a count of zero.
- `/library` renders an empty shelf without erroring, and a classroom topic
  with no lesson resources shows no subject fallback rather than crashing.
- A material of each of the four types can be created, edited, reordered
  and deleted from `/admin/library`, appears on the student shelf with the
  right icon, and a premium material's URL is stripped for a free account.
