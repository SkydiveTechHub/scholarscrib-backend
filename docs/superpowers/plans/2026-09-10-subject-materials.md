# Subject Materials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Empty the seeded placeholder library and give admins a console section that files PDFs, images, videos and links under a subject.

**Architecture:** `SubjectResource.resourceType` becomes a four-value enum (`PDF`, `IMAGE`, `VIDEO`, `LINK`). A new `/admin/library` console section does CRUD over `SubjectResource` through `/admin/api/materials/*`. PDFs and images are uploaded straight from the browser to Cloudinary against a server-issued signature; videos and links are pasted URLs. The student shelf at `/library` is unchanged except for reading the new enum.

**Tech Stack:** Next.js 16 (App Router, route handlers), React 19, Prisma 6 / PostgreSQL on Supabase, NextAuth v5 (separate admin auth), Zod 4, Tailwind 4, `react-icons/lu`, `node:test` via tsx.

**Spec:** `docs/superpowers/specs/2026-09-10-subject-materials-design.md`

## Global Constraints

- **Read the Next.js docs before writing route or page code.** This repo pins Next 16 and its conventions differ from older releases. The relevant files are `node_modules/next/dist/docs/01-app/01-getting-started/15-route-handlers.md` and `node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/route.md`. In particular: dynamic route params arrive as a **Promise** (`{ params }: { params: Promise<{ id: string }> }`) and must be awaited, and `searchParams` on a page is likewise a Promise.
- **Every admin route calls `requireAdminApi()` itself.** The layout's check does not re-run on client-side navigation between admin routes. Same for pages: call `requireAdminPage()` in the page body.
- **Every admin mutation records an audit entry** through `recordAudit` from `@/lib/admin-audit`.
- **Migrations are applied by hand.** `prisma migrate deploy` cannot reach Supabase from this machine. Write the migration file with **LF** line endings (`core.autocrlf=true` here will otherwise drift Prisma's checksums) and apply the SQL through the Supabase SQL Editor. The editor reports success on batches it has only half applied — verify against `information_schema`, never the success message.
- **`npx prisma generate` fails with EPERM while the dev server is running** (it holds the query-engine DLL open). Stop the dev server before generating; a stale client shows up as bogus `tsc` errors.
- **The four material types are exactly `PDF`, `IMAGE`, `VIDEO`, `LINK`.** No `textbook`, `worksheet` or `past_paper`.
- **Visibility has one lever: `isFree`.** No draft/publish flag, no class-level targeting.
- Test files are `scripts/test-*.mts`, run under `node --test`, and must be added to the `test` script in `package.json`.

### Refinement to the spec

The spec puts the pure rules in `src/lib/admin-material.ts`. Two of those values — the type list and the display labels — are also needed by **student-facing** components (`library-view.tsx`, `topic-resources.tsx`), which should not import from an `admin-` module. So the plan splits them:

- `src/lib/materials.ts` — shared: `MATERIAL_TYPES`, `MaterialType`, `MATERIAL_LABELS`.
- `src/lib/admin-material.ts` — admin-only rules, importing from `materials.ts`.

Both are covered by `scripts/test-admin-material.mts`. Nothing else in the spec changes.

## File Structure

**Create:**
- `src/lib/materials.ts` — the four types and their display labels. Shared by admin and student code.
- `src/lib/admin-material.ts` — pure admin rules: upload-vs-URL, URL validation, signing params, ordering.
- `src/lib/admin-material-data.ts` — every database read/write for materials. No HTTP, no auth.
- `scripts/test-admin-material.mts` — unit tests for the two pure modules.
- `prisma/migrations/20260910000000_subject_materials/migration.sql` — clear + retype.
- `src/app/admin/api/materials/route.ts` — `GET` list, `POST` create.
- `src/app/admin/api/materials/[id]/route.ts` — `PATCH` update, `DELETE` remove.
- `src/app/admin/api/materials/sign/route.ts` — `POST` upload signature.
- `src/app/admin/(console)/library/page.tsx` — the console section.
- `src/components/admin/material-form.tsx` — client form for one material: type picker, upload, save.
- `src/components/admin/material-manager.tsx` — client list: subject picker, table, reorder, delete. Owns the interactions the page's server component cannot.

**Modify:**
- `prisma/schema.prisma` — add `enum MaterialType`, retype `SubjectResource.resourceType`, extend the `AdminAudit.action` comment.
- `prisma/seed.ts` — delete `ResourceDef`, `SUBJECT_RESOURCES`, `seedSubjectResources()` and its call site.
- `src/lib/cloudinary.ts` — add `signUpload()`.
- `src/lib/admin-audit.ts` — add three `AuditAction` values.
- `src/lib/validators.ts` — add the material schemas.
- `src/lib/admin-nav.ts` — add the Library nav entry.
- `src/components/library/library-view.tsx` — icons and `isReadable()` for the enum.
- `src/components/classroom/topic-resources.tsx` — case-insensitive icon lookup.
- `package.json` — add the new test file to the `test` script.

---

### Task 1: Material types and pure admin rules

Pure functions first, with no database and no Prisma import, so they are testable without a generated client. `MaterialType` is declared as a TypeScript union whose members match the Prisma enum added in Task 2 by name.

**Files:**
- Create: `src/lib/materials.ts`
- Create: `src/lib/admin-material.ts`
- Test: `scripts/test-admin-material.mts`
- Modify: `package.json` (the `test` script)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `MATERIAL_TYPES: readonly ["PDF", "IMAGE", "VIDEO", "LINK"]`
  - `type MaterialType = "PDF" | "IMAGE" | "VIDEO" | "LINK"`
  - `MATERIAL_LABELS: Record<MaterialType, string>`
  - `requiresUpload(type: MaterialType): boolean`
  - `validateMaterialUrl(type: MaterialType, url: string): { ok: true } | { ok: false; reason: string }`
  - `signingParamsFor(type: MaterialType): { folder: string; allowedFormats: string[]; maxBytes: number; resourceType: "image" | "raw" }`
  - `nextOrderIndex(existing: readonly { orderIndex: number }[]): number`

- [ ] **Step 1: Write the failing test**

Create `scripts/test-admin-material.mts`:

```ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { MATERIAL_TYPES, MATERIAL_LABELS } from "../src/lib/materials";
import {
  requiresUpload,
  validateMaterialUrl,
  signingParamsFor,
  nextOrderIndex,
} from "../src/lib/admin-material";

test("there are exactly four material types", () => {
  assert.deepEqual([...MATERIAL_TYPES], ["PDF", "IMAGE", "VIDEO", "LINK"]);
});

test("every type has a display label", () => {
  // "PDF" must not render as "Pdf", which is what a CSS capitalize would do.
  assert.equal(MATERIAL_LABELS.PDF, "PDF");
  assert.equal(MATERIAL_LABELS.IMAGE, "Image");
  assert.equal(MATERIAL_LABELS.VIDEO, "Video");
  assert.equal(MATERIAL_LABELS.LINK, "Link");
});

test("files are uploaded, videos and links are pasted", () => {
  assert.equal(requiresUpload("PDF"), true);
  assert.equal(requiresUpload("IMAGE"), true);
  assert.equal(requiresUpload("VIDEO"), false);
  assert.equal(requiresUpload("LINK"), false);
});

test("http is refused for every type", () => {
  // A student's browser blocks mixed content, so an http material is a
  // material that silently fails to load.
  const result = validateMaterialUrl("LINK", "http://example.com/notes");
  assert.equal(result.ok, false);
});

test("https links are accepted", () => {
  assert.equal(validateMaterialUrl("LINK", "https://example.com/notes").ok, true);
});

test("a direct video file is refused", () => {
  // Self-hosted MP4 means one fixed bitrate with no adaptive streaming, which
  // is the wrong answer on metered mobile data. Video must be a hosted embed.
  const result = validateMaterialUrl("VIDEO", "https://cdn.example.com/lesson.mp4");
  assert.equal(result.ok, false);
});

test("youtube and vimeo are accepted for video", () => {
  assert.equal(
    validateMaterialUrl("VIDEO", "https://www.youtube.com/watch?v=abc123").ok,
    true,
  );
  assert.equal(validateMaterialUrl("VIDEO", "https://youtu.be/abc123").ok, true);
  assert.equal(validateMaterialUrl("VIDEO", "https://vimeo.com/123456").ok, true);
});

test("a host that merely contains a video host name is refused", () => {
  // Suffix matching, not substring: youtube.com.evil.test must not pass.
  assert.equal(
    validateMaterialUrl("VIDEO", "https://youtube.com.evil.test/x").ok,
    false,
  );
});

test("garbage is refused rather than thrown on", () => {
  assert.equal(validateMaterialUrl("LINK", "not a url").ok, false);
});

test("each uploaded type gets its own folder and format allowlist", () => {
  const pdf = signingParamsFor("PDF");
  const image = signingParamsFor("IMAGE");

  assert.notEqual(pdf.folder, image.folder);
  assert.deepEqual(pdf.allowedFormats, ["pdf"]);
  assert.deepEqual(image.allowedFormats, ["jpg", "jpeg", "png", "webp"]);
  // Cloudinary stores a PDF as a raw asset, not an image.
  assert.equal(pdf.resourceType, "raw");
  assert.equal(image.resourceType, "image");
  assert.ok(pdf.maxBytes > image.maxBytes);
});

test("the first material lands at index zero", () => {
  assert.equal(nextOrderIndex([]), 0);
});

test("a new material lands after the last one, gaps notwithstanding", () => {
  assert.equal(nextOrderIndex([{ orderIndex: 0 }, { orderIndex: 7 }]), 8);
});
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
node --import tsx --test --test-force-exit scripts/test-admin-material.mts
```

Expected: FAIL — `Cannot find module '../src/lib/materials'`.

- [ ] **Step 3: Write `src/lib/materials.ts`**

```ts
/**
 * The four kinds of library material, shared by the admin console and the
 * student shelf.
 *
 * Declared as a TypeScript union rather than imported from `@prisma/client` so
 * the pure rules built on it stay testable without a generated client. The
 * members match the `MaterialType` enum in `prisma/schema.prisma` by name.
 */
export const MATERIAL_TYPES = ["PDF", "IMAGE", "VIDEO", "LINK"] as const;

export type MaterialType = (typeof MATERIAL_TYPES)[number];

/**
 * Display text. A map rather than a CSS `capitalize`, which would render the
 * initialism "PDF" as "Pdf".
 */
export const MATERIAL_LABELS: Record<MaterialType, string> = {
  PDF: "PDF",
  IMAGE: "Image",
  VIDEO: "Video",
  LINK: "Link",
};

export function isMaterialType(value: string): value is MaterialType {
  return (MATERIAL_TYPES as readonly string[]).includes(value);
}
```

- [ ] **Step 4: Write `src/lib/admin-material.ts`**

```ts
import type { MaterialType } from "./materials";

/**
 * The rules governing a library material, as pure functions.
 *
 * Database-free so they can be unit tested the way `admin-access.ts` is. The
 * database work lives in `admin-material-data.ts`; keeping the two apart is
 * what makes these testable.
 */

/** PDFs and images are uploaded; videos and links carry a pasted URL. */
export function requiresUpload(type: MaterialType): boolean {
  return type === "PDF" || type === "IMAGE";
}

/**
 * Hosts we accept for a `VIDEO`.
 *
 * Self-hosting video would mean one fixed bitrate with no adaptive streaming —
 * the wrong trade on the metered mobile connections most of this audience is
 * on. An embed from one of these gives adaptive bitrate and a CDN for free.
 */
const VIDEO_HOSTS = ["youtube.com", "youtu.be", "vimeo.com"] as const;

function hostMatches(hostname: string, host: string): boolean {
  // Suffix match on a dot boundary, so `youtube.com.evil.test` does not pass
  // the way a plain `includes` would let it.
  return hostname === host || hostname.endsWith(`.${host}`);
}

export type UrlCheck = { ok: true } | { ok: false; reason: string };

export function validateMaterialUrl(type: MaterialType, url: string): UrlCheck {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return { ok: false, reason: "Enter a full URL, starting with https://" };
  }

  // http would be blocked as mixed content in the student's browser, so an
  // http material is a material that silently fails to load.
  if (parsed.protocol !== "https:") {
    return { ok: false, reason: "The URL must start with https://" };
  }

  if (type === "VIDEO") {
    const hostname = parsed.hostname.replace(/^www\./, "");
    const known = VIDEO_HOSTS.some((host) => hostMatches(hostname, host));
    if (!known) {
      return {
        ok: false,
        reason: "Video must be a YouTube or Vimeo link, not a direct video file",
      };
    }
  }

  return { ok: true };
}

export type SigningParams = {
  folder: string;
  allowedFormats: string[];
  maxBytes: number;
  /** Cloudinary's upload endpoint segment. A PDF is a raw asset, not an image. */
  resourceType: "image" | "raw";
};

export function signingParamsFor(type: MaterialType): SigningParams {
  if (type === "PDF") {
    return {
      folder: "scholarscrib/materials/pdf",
      allowedFormats: ["pdf"],
      maxBytes: 50 * 1024 * 1024,
      resourceType: "raw",
    };
  }
  if (type === "IMAGE") {
    return {
      folder: "scholarscrib/materials/image",
      allowedFormats: ["jpg", "jpeg", "png", "webp"],
      maxBytes: 5 * 1024 * 1024,
      resourceType: "image",
    };
  }
  // VIDEO and LINK carry a pasted URL and never reach the signing route; the
  // route rejects them before calling this.
  throw new Error(`${type} materials are not uploaded`);
}

/** Where a newly added material lands: after the last one, gaps and all. */
export function nextOrderIndex(
  existing: readonly { orderIndex: number }[],
): number {
  if (existing.length === 0) return 0;
  return Math.max(...existing.map((row) => row.orderIndex)) + 1;
}
```

- [ ] **Step 5: Run the test and verify it passes**

Run:

```bash
node --import tsx --test --test-force-exit scripts/test-admin-material.mts
```

Expected: PASS, 12 tests.

- [ ] **Step 6: Register the test file**

In `package.json`, append ` scripts/test-admin-material.mts` to the end of the `test` script's file list (it currently ends with `scripts/test-seo-sitemap.mts`).

- [ ] **Step 7: Run the whole suite**

Run: `npm test`
Expected: PASS, including the new file.

- [ ] **Step 8: Commit**

```bash
git add src/lib/materials.ts src/lib/admin-material.ts scripts/test-admin-material.mts package.json
git commit -m "feat(library): material types and the rules governing them"
```

---

### Task 2: Clear the library and retype the column

Deletes every `SubjectResource` row, turns `resourceType` into an enum, and removes the seed block that would otherwise repopulate the shelf. Deleting before retyping is what avoids inventing a `textbook -> ?` mapping.

**Files:**
- Modify: `prisma/schema.prisma` (the `SubjectResource` model, ~line 339; the `AdminAudit.action` comment, ~line 925)
- Create: `prisma/migrations/20260910000000_subject_materials/migration.sql`
- Modify: `prisma/seed.ts` (delete lines ~383–494 and the call at ~line 755)

**Interfaces:**
- Consumes: `MaterialType` member names from Task 1 (`PDF`, `IMAGE`, `VIDEO`, `LINK`).
- Produces: `SubjectResource.resourceType` typed `MaterialType` in the generated Prisma client.

- [ ] **Step 1: Add the enum to the schema**

In `prisma/schema.prisma`, immediately above `model SubjectResource`:

```prisma
/// The four kinds of library material. Mirrored as a TypeScript union in
/// `src/lib/materials.ts`, which the pure rules build on so they stay
/// testable without a generated client.
enum MaterialType {
  PDF
  IMAGE
  VIDEO
  LINK
}
```

- [ ] **Step 2: Retype the column**

In `model SubjectResource`, replace this line:

```prisma
  resourceType String // textbook, video, pdf, link, worksheet, past_paper
```

with:

```prisma
  resourceType MaterialType
```

Leave every other field on the model exactly as it is.

- [ ] **Step 3: Extend the audit action comment**

In `model AdminAudit`, the `action` field's trailing comment lists the known actions. Append `| "material.create" | "material.update" | "material.delete"` to it. This is a comment only — no column change.

- [ ] **Step 4: Write the migration**

Create `prisma/migrations/20260910000000_subject_materials/migration.sql`:

```sql
-- Clear the seeded placeholder library. These rows were third-party links
-- chosen to make the shelf look populated; nothing here is worth preserving,
-- and emptying the table first is what lets the column be retyped without
-- inventing a mapping from the old free-text values.
DELETE FROM "SubjectResource";

CREATE TYPE "MaterialType" AS ENUM ('PDF', 'IMAGE', 'VIDEO', 'LINK');

ALTER TABLE "SubjectResource"
  ALTER COLUMN "resourceType" TYPE "MaterialType"
  USING "resourceType"::"MaterialType";
```

**The file must have LF line endings.** With `core.autocrlf=true` on this machine, CRLF would silently drift Prisma's migration checksum. Verify before committing:

```bash
file prisma/migrations/20260910000000_subject_materials/migration.sql
```

Expected: no mention of "CRLF line terminators". If it reports CRLF, rewrite it with `printf` or `dos2unix`.

- [ ] **Step 5: Apply the SQL through the Supabase SQL Editor**

`prisma migrate deploy` cannot reach Supabase from here. Open the Supabase dashboard → SQL Editor, paste the three statements above, and run them.

- [ ] **Step 6: Verify against the catalog, not the editor's message**

The SQL Editor reports success on batches it has only half applied. Run this separately and read the result:

```sql
select column_name, udt_name
from information_schema.columns
where table_name = 'SubjectResource' and column_name = 'resourceType';

select count(*) from "SubjectResource";
```

Expected: `udt_name` is `MaterialType`, and the count is `0`. If `udt_name` is still `text`, the `ALTER` did not land — run it again on its own.

- [ ] **Step 7: Record the migration as applied**

Prisma's `_prisma_migrations` table must know about a migration applied by hand, or the next `migrate` command will try to replay it. In the SQL Editor:

```sql
insert into "_prisma_migrations"
  (id, checksum, finished_at, migration_name, logs, rolled_back_at, started_at, applied_steps_count)
values
  (gen_random_uuid()::text, '', now(), '20260910000000_subject_materials', null, null, now(), 1);
```

- [ ] **Step 8: Remove the seed block**

In `prisma/seed.ts`, delete:
- the `// ─── Subject Resources ───` banner comment, the `ResourceDef` type and the whole `SUBJECT_RESOURCES` map (starting at the banner above `type ResourceDef` and ending at the map's closing `};`)
- the entire `seedSubjectResources()` function
- the line `await seedSubjectResources();` inside `main()`

Leave `seedSubjects`, `seedCurriculum`, `seedAchievements` and `seedLessons` untouched.

- [ ] **Step 9: Regenerate the client and typecheck**

Stop the dev server first — it holds the query-engine DLL open and `prisma generate` fails with EPERM, which then surfaces as bogus `tsc` errors.

```bash
npx prisma generate
npx tsc --noEmit
```

Expected: `generate` succeeds. `tsc` reports errors **only** in `src/components/library/library-view.tsx` and `src/components/classroom/topic-resources.tsx` if it reports any — those are fixed in Task 6. If it reports an error in `prisma/seed.ts`, a reference to the deleted map was missed.

- [ ] **Step 10: Commit**

```bash
git add prisma/schema.prisma prisma/migrations prisma/seed.ts
git commit -m "feat(library)!: clear the seeded library and type materials as an enum"
```

---

### Task 3: Signed Cloudinary uploads

Adds the signing helper beside the existing avatar upload so both share one notion of "configured", and the route that hands a signature to the browser.

**Files:**
- Modify: `src/lib/cloudinary.ts` (append after `uploadAvatar`)
- Create: `src/app/admin/api/materials/sign/route.ts`

**Interfaces:**
- Consumes: `requiresUpload`, `signingParamsFor` (Task 1); `requireAdminApi` from `@/lib/admin-session`.
- Produces:
  - `signUpload(params: Record<string, string>): { signature: string; timestamp: number; apiKey: string; cloudName: string } | null` — `null` when Cloudinary is not configured.
  - `POST /admin/api/materials/sign`, body `{ type: MaterialType }`, 200 response `{ cloudName, apiKey, timestamp, signature, folder, allowedFormats, maxBytes, resourceType }`.

- [ ] **Step 1: Add `signUpload` to `src/lib/cloudinary.ts`**

Append at the end of the file:

```ts
/**
 * Sign an upload the browser will perform itself.
 *
 * The avatar path posts the file through us, which is fine for a 2MB image but
 * cannot carry a textbook: a serverless deploy caps request bodies at about
 * 4.5MB. Signing here and letting the browser send the bytes straight to
 * Cloudinary removes that ceiling and costs us no bandwidth.
 *
 * `params` are the upload parameters being authorised — every one of them is
 * covered by the signature, so the browser cannot widen the folder or the
 * format allowlist after the fact. `timestamp` is added here.
 */
export function signUpload(params: Record<string, string>) {
  const creds = credentials();
  if (!creds) return null;

  const timestamp = Math.floor(Date.now() / 1000);
  const signed: Record<string, string> = {
    ...params,
    timestamp: String(timestamp),
  };

  // Cloudinary signs the alphabetically sorted, &-joined parameter string.
  const toSign = Object.keys(signed)
    .sort()
    .map((key) => `${key}=${signed[key]}`)
    .join("&");

  const signature = crypto
    .createHash("sha1")
    .update(toSign + creds.apiSecret)
    .digest("hex");

  return {
    signature,
    timestamp,
    apiKey: creds.apiKey,
    cloudName: creds.cloudName,
  };
}
```

- [ ] **Step 2: Write the signing route**

Read `node_modules/next/dist/docs/01-app/01-getting-started/15-route-handlers.md` first if you have not.

Create `src/app/admin/api/materials/sign/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import { requireAdminApi } from "@/lib/admin-session";
import { signUpload } from "@/lib/cloudinary";
import { requiresUpload, signingParamsFor } from "@/lib/admin-material";
import { isMaterialType } from "@/lib/materials";

export const dynamic = "force-dynamic";

// POST /admin/api/materials/sign — authorise one direct-to-Cloudinary upload.
//
// The signature covers the folder and the format allowlist, so a caller cannot
// widen either after we have signed them.
export async function POST(req: NextRequest) {
  try {
    const guard = await requireAdminApi();
    if (!guard.ok) return guard.response;

    const body = (await req.json()) as { type?: unknown };
    const type = typeof body.type === "string" ? body.type : "";

    if (!isMaterialType(type)) {
      return NextResponse.json({ error: "Unknown material type" }, { status: 400 });
    }

    if (!requiresUpload(type)) {
      return NextResponse.json(
        { error: `${type} materials carry a URL, not a file` },
        { status: 400 },
      );
    }

    const params = signingParamsFor(type);
    const signed = signUpload({
      folder: params.folder,
      allowed_formats: params.allowedFormats.join(","),
    });

    if (!signed) {
      return NextResponse.json(
        {
          error:
            "File uploads aren't configured. Add your Cloudinary credentials to .env.",
        },
        { status: 503 },
      );
    }

    return NextResponse.json({
      cloudName: signed.cloudName,
      apiKey: signed.apiKey,
      timestamp: signed.timestamp,
      signature: signed.signature,
      folder: params.folder,
      allowedFormats: params.allowedFormats,
      maxBytes: params.maxBytes,
      resourceType: params.resourceType,
    });
  } catch (error) {
    console.error("Error signing material upload:", error);
    return NextResponse.json({ error: "Failed to sign upload" }, { status: 500 });
  }
}
```

- [ ] **Step 3: Typecheck**

Run: `npx tsc --noEmit`
Expected: no new errors from these two files.

- [ ] **Step 4: Commit**

```bash
git add src/lib/cloudinary.ts src/app/admin/api/materials/sign/route.ts
git commit -m "feat(library): sign direct-to-Cloudinary material uploads"
```

---

### Task 4: Validation, data layer and CRUD routes

**Files:**
- Modify: `src/lib/validators.ts` (append before the type exports at the end)
- Modify: `src/lib/admin-audit.ts` (the `AuditAction` union)
- Create: `src/lib/admin-material-data.ts`
- Create: `src/app/admin/api/materials/route.ts`
- Create: `src/app/admin/api/materials/[id]/route.ts`

**Interfaces:**
- Consumes: `MATERIAL_TYPES` (Task 1), `validateMaterialUrl`, `nextOrderIndex` (Task 1), the `MaterialType` Prisma enum (Task 2), `requireAdminApi`, `recordAudit`.
- Produces:
  - `materialCreateSchema`, `materialUpdateSchema` and the inferred types `MaterialCreateInput`, `MaterialUpdateInput`
  - `listMaterials(subjectId: string)`, `createMaterial(input: MaterialCreateInput)`, `updateMaterial(id: string, input: MaterialUpdateInput)`, `deleteMaterial(id: string)`, `listMaterialSubjects()`
  - the four routes in the table below

- [ ] **Step 1: Add the audit actions**

In `src/lib/admin-audit.ts`, add three members to the `AuditAction` union, after `"lesson.import"`:

```ts
  | "material.create"
  | "material.update"
  | "material.delete"
```

- [ ] **Step 2: Add the Zod schemas**

In `src/lib/validators.ts`, add near the other admin schemas (before the `// Type exports` block):

```ts
// ─── Library materials ────────────────────────────

const materialUrlRefinement = <T extends { resourceType: MaterialType; url: string }>(
  value: T,
  ctx: z.RefinementCtx,
) => {
  const check = validateMaterialUrl(value.resourceType, value.url);
  if (!check.ok) {
    ctx.addIssue({ code: "custom", message: check.reason, path: ["url"] });
  }
};

export const materialCreateSchema = z
  .object({
    subjectId: z.string().min(1),
    title: z.string().trim().min(2, "Title is required").max(200),
    description: z.string().trim().max(600).optional(),
    resourceType: z.enum(MATERIAL_TYPES),
    url: z.string().min(1, "A URL or an uploaded file is required"),
    author: z.string().trim().max(120).optional(),
    isFree: z.boolean().default(true),
  })
  .superRefine(materialUrlRefinement);

// Every field optional: the form saves the whole record, but reordering sends
// only `orderIndex`. An absent key means "leave unchanged".
export const materialUpdateSchema = z
  .object({
    title: z.string().trim().min(2).max(200).optional(),
    description: z.string().trim().max(600).optional(),
    resourceType: z.enum(MATERIAL_TYPES).optional(),
    url: z.string().min(1).optional(),
    author: z.string().trim().max(120).optional(),
    isFree: z.boolean().optional(),
    orderIndex: z.number().int().min(0).optional(),
  })
  .superRefine((value, ctx) => {
    // Only checkable when both arrive together; a URL change without a type
    // change is validated against the stored type in the route.
    if (value.resourceType && value.url) {
      materialUrlRefinement({ resourceType: value.resourceType, url: value.url }, ctx);
    }
  });
```

Add these imports at the top of the file, beside the existing ones:

```ts
import { MATERIAL_TYPES, type MaterialType } from "@/lib/materials";
import { validateMaterialUrl } from "@/lib/admin-material";
```

And in the `// Type exports` block at the bottom:

```ts
export type MaterialCreateInput = z.infer<typeof materialCreateSchema>;
export type MaterialUpdateInput = z.infer<typeof materialUpdateSchema>;
```

- [ ] **Step 3: Write the data layer**

Create `src/lib/admin-material-data.ts`:

```ts
import { db } from "@/lib/db";
import { nextOrderIndex } from "@/lib/admin-material";
import type { MaterialCreateInput, MaterialUpdateInput } from "@/lib/validators";

/**
 * Every database read and write for library materials. No HTTP, no auth — the
 * routes own those, the way `admin-question-data.ts` is arranged.
 */

/** Subjects for the console's subject picker. */
export async function listMaterialSubjects() {
  return db.subject.findMany({
    orderBy: [{ trackCategory: "asc" }, { name: "asc" }],
    select: {
      id: true,
      name: true,
      code: true,
      trackCategory: true,
      _count: { select: { resources: true } },
    },
  });
}

export async function listMaterials(subjectId: string) {
  return db.subjectResource.findMany({
    where: { subjectId },
    orderBy: [{ orderIndex: "asc" }, { title: "asc" }],
  });
}

export async function createMaterial(input: MaterialCreateInput) {
  const existing = await db.subjectResource.findMany({
    where: { subjectId: input.subjectId },
    select: { orderIndex: true },
  });

  return db.subjectResource.create({
    data: {
      subjectId: input.subjectId,
      title: input.title,
      description: input.description || null,
      resourceType: input.resourceType,
      url: input.url,
      author: input.author || null,
      isFree: input.isFree,
      orderIndex: nextOrderIndex(existing),
    },
  });
}

export async function getMaterial(id: string) {
  return db.subjectResource.findUnique({ where: { id } });
}

export async function updateMaterial(id: string, input: MaterialUpdateInput) {
  return db.subjectResource.update({
    where: { id },
    data: {
      ...(input.title !== undefined && { title: input.title }),
      ...(input.description !== undefined && {
        description: input.description || null,
      }),
      ...(input.resourceType !== undefined && { resourceType: input.resourceType }),
      ...(input.url !== undefined && { url: input.url }),
      ...(input.author !== undefined && { author: input.author || null }),
      ...(input.isFree !== undefined && { isFree: input.isFree }),
      ...(input.orderIndex !== undefined && { orderIndex: input.orderIndex }),
    },
  });
}

export async function deleteMaterial(id: string) {
  return db.subjectResource.delete({ where: { id } });
}
```

- [ ] **Step 4: Write the collection route**

Create `src/app/admin/api/materials/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import { requireAdminApi } from "@/lib/admin-session";
import { recordAudit } from "@/lib/admin-audit";
import { materialCreateSchema } from "@/lib/validators";
import { createMaterial, listMaterials } from "@/lib/admin-material-data";

export const dynamic = "force-dynamic";

// GET /admin/api/materials?subjectId= — one subject's materials, in shelf order
export async function GET(req: NextRequest) {
  try {
    const guard = await requireAdminApi();
    if (!guard.ok) return guard.response;

    const subjectId = new URL(req.url).searchParams.get("subjectId");
    if (!subjectId) {
      return NextResponse.json({ error: "subjectId is required" }, { status: 400 });
    }

    return NextResponse.json(await listMaterials(subjectId));
  } catch (error) {
    console.error("Error listing materials:", error);
    return NextResponse.json({ error: "Failed to list materials" }, { status: 500 });
  }
}

// POST /admin/api/materials — file a new material under a subject
export async function POST(req: NextRequest) {
  try {
    const guard = await requireAdminApi();
    if (!guard.ok) return guard.response;

    const parsed = materialCreateSchema.safeParse(await req.json());
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Validation failed", details: parsed.error.flatten() },
        { status: 400 },
      );
    }

    const material = await createMaterial(parsed.data);

    await recordAudit({
      actorId: guard.actor.id,
      action: "material.create",
      entity: "SubjectResource",
      entityId: material.id,
      summary: `Added ${material.resourceType} material "${material.title}"`,
    });

    return NextResponse.json(material, { status: 201 });
  } catch (error) {
    console.error("Error creating material:", error);
    return NextResponse.json({ error: "Failed to create material" }, { status: 500 });
  }
}
```

- [ ] **Step 5: Write the item route**

Dynamic params are a Promise in Next 16 and must be awaited — see `src/app/admin/api/questions/[id]/route.ts` for the same shape.

Create `src/app/admin/api/materials/[id]/route.ts`:

```ts
import { NextRequest, NextResponse } from "next/server";
import { requireAdminApi } from "@/lib/admin-session";
import { recordAudit } from "@/lib/admin-audit";
import { materialUpdateSchema } from "@/lib/validators";
import {
  deleteMaterial,
  getMaterial,
  updateMaterial,
} from "@/lib/admin-material-data";
import { validateMaterialUrl } from "@/lib/admin-material";

export const dynamic = "force-dynamic";

// PATCH /admin/api/materials/[id] — partial update, including reordering.
//
// A URL sent without a type must be checked against the STORED type, not left
// unchecked: patching a VIDEO's url alone would otherwise slip a direct .mp4
// past the rule that keeps video on an adaptive-bitrate host.
export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const guard = await requireAdminApi();
    if (!guard.ok) return guard.response;

    const { id } = await params;

    const parsed = materialUpdateSchema.safeParse(await req.json());
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Validation failed", details: parsed.error.flatten() },
        { status: 400 },
      );
    }

    const existing = await getMaterial(id);
    if (!existing) {
      return NextResponse.json({ error: "Material not found" }, { status: 404 });
    }

    const input = parsed.data;
    const mergedType = input.resourceType ?? existing.resourceType;
    const mergedUrl = input.url ?? existing.url;
    const check = validateMaterialUrl(mergedType, mergedUrl);
    if (!check.ok) {
      return NextResponse.json(
        { error: "Validation failed", details: { fieldErrors: { url: [check.reason] } } },
        { status: 400 },
      );
    }

    const material = await updateMaterial(id, input);

    await recordAudit({
      actorId: guard.actor.id,
      action: "material.update",
      entity: "SubjectResource",
      entityId: material.id,
      summary: `Updated material "${material.title}"`,
    });

    return NextResponse.json(material);
  } catch (error) {
    console.error("Error updating material:", error);
    return NextResponse.json({ error: "Failed to update material" }, { status: 500 });
  }
}

// DELETE /admin/api/materials/[id] — remove it from the shelf
export async function DELETE(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  try {
    const guard = await requireAdminApi();
    if (!guard.ok) return guard.response;

    const { id } = await params;

    const existing = await getMaterial(id);
    if (!existing) {
      return NextResponse.json({ error: "Material not found" }, { status: 404 });
    }

    await deleteMaterial(id);

    await recordAudit({
      actorId: guard.actor.id,
      action: "material.delete",
      entity: "SubjectResource",
      entityId: id,
      summary: `Deleted material "${existing.title}"`,
    });

    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("Error deleting material:", error);
    return NextResponse.json({ error: "Failed to delete material" }, { status: 500 });
  }
}
```

- [ ] **Step 6: Typecheck and lint**

```bash
npx tsc --noEmit
npm run lint
```

Expected: no new errors.

- [ ] **Step 7: Commit**

```bash
git add src/lib/validators.ts src/lib/admin-audit.ts src/lib/admin-material-data.ts src/app/admin/api/materials
git commit -m "feat(library): admin CRUD routes for subject materials"
```

---

### Task 5: The admin console section

**Files:**
- Create: `src/app/admin/(console)/library/page.tsx`
- Create: `src/components/admin/material-form.tsx`
- Modify: `src/lib/admin-nav.ts`

**Interfaces:**
- Consumes: `listMaterialSubjects`, `listMaterials` (Task 4); `MATERIAL_TYPES`, `MATERIAL_LABELS` (Task 1); `requiresUpload` (Task 1); the routes from Tasks 3 and 4.
- Produces: `/admin/library`, and `MaterialForm` as the console's editor for one material.

- [ ] **Step 1: Add the nav entry**

In `src/lib/admin-nav.ts`, add `LuLibrary` to the `react-icons/lu` import, then add an item to the `Content` group after Lessons:

```ts
      { name: "Library", href: "/admin/library", icon: LuLibrary },
```

Leave `MOBILE_NAV_HREFS` alone — Library reaches the mobile bar through the "More" sheet, the way Lessons does.

- [ ] **Step 2: Write the material form**

Create `src/components/admin/material-form.tsx`:

```tsx
"use client";

import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { StatusBanner } from "@/components/admin/status-banner";
import { MATERIAL_TYPES, MATERIAL_LABELS, type MaterialType } from "@/lib/materials";
import { requiresUpload } from "@/lib/admin-material";

export type MaterialRow = {
  id: string;
  title: string;
  description: string | null;
  resourceType: MaterialType;
  url: string;
  author: string | null;
  isFree: boolean;
  orderIndex: number;
};

type Props = {
  subjectId: string;
  /** Absent when adding. */
  material?: MaterialRow;
  onSaved: () => void;
  onCancel: () => void;
};

type Signature = {
  cloudName: string;
  apiKey: string;
  timestamp: number;
  signature: string;
  folder: string;
  allowedFormats: string[];
  maxBytes: number;
  resourceType: "image" | "raw";
};

export function MaterialForm({ subjectId, material, onSaved, onCancel }: Props) {
  const fieldId = useId();
  const [type, setType] = useState<MaterialType>(material?.resourceType ?? "PDF");
  const [title, setTitle] = useState(material?.title ?? "");
  const [description, setDescription] = useState(material?.description ?? "");
  const [author, setAuthor] = useState(material?.author ?? "");
  const [url, setUrl] = useState(material?.url ?? "");
  const [isFree, setIsFree] = useState(material?.isFree ?? true);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const upload = requiresUpload(type);

  async function handleFile(file: File) {
    setError(null);
    setUploading(true);
    try {
      // Ask the server to authorise this one upload. The signature covers the
      // folder and the format allowlist, so the browser cannot widen either.
      const signRes = await fetch("/admin/api/materials/sign", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type }),
      });
      const signed = (await signRes.json()) as Signature & { error?: string };
      if (!signRes.ok) throw new Error(signed.error ?? "Could not start the upload");

      if (file.size > signed.maxBytes) {
        throw new Error(
          `That file is ${(file.size / 1024 / 1024).toFixed(1)}MB. The limit is ${
            signed.maxBytes / 1024 / 1024
          }MB.`,
        );
      }

      const body = new FormData();
      body.append("file", file);
      body.append("api_key", signed.apiKey);
      body.append("timestamp", String(signed.timestamp));
      body.append("folder", signed.folder);
      body.append("allowed_formats", signed.allowedFormats.join(","));
      body.append("signature", signed.signature);

      // Straight to Cloudinary: a serverless deploy caps request bodies at
      // about 4.5MB, which no textbook fits inside.
      const uploadRes = await fetch(
        `https://api.cloudinary.com/v1_1/${signed.cloudName}/${signed.resourceType}/upload`,
        { method: "POST", body },
      );
      const json = (await uploadRes.json()) as {
        secure_url?: string;
        error?: { message?: string };
      };
      if (!uploadRes.ok || !json.secure_url) {
        throw new Error(json.error?.message ?? "Cloudinary rejected the file");
      }

      setUrl(json.secure_url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSaving(true);
    try {
      const payload = {
        title,
        description: description || undefined,
        resourceType: type,
        url,
        author: author || undefined,
        isFree,
      };

      const res = material
        ? await fetch(`/admin/api/materials/${material.id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          })
        : await fetch("/admin/api/materials", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...payload, subjectId }),
          });

      const json = (await res.json()) as {
        error?: string;
        details?: { fieldErrors?: Record<string, string[]> };
      };
      if (!res.ok) {
        const fieldError = Object.values(json.details?.fieldErrors ?? {})[0]?.[0];
        throw new Error(fieldError ?? json.error ?? "Could not save this material");
      }

      onSaved();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="card space-y-4 p-5">
      {error && <StatusBanner tone="error" message={error} />}

      <div>
        <label htmlFor={`${fieldId}-type`} className="text-sm font-semibold">
          Type
        </label>
        <select
          id={`${fieldId}-type`}
          value={type}
          onChange={(event) => {
            // Switching between an uploaded and a pasted type invalidates the
            // URL either way, so clear it rather than carry a stale one over.
            setType(event.target.value as MaterialType);
            setUrl("");
          }}
          className="mt-1 w-full rounded-lg border border-border bg-card px-3 py-2 text-sm"
        >
          {MATERIAL_TYPES.map((value) => (
            <option key={value} value={value}>
              {MATERIAL_LABELS[value]}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label htmlFor={`${fieldId}-title`} className="text-sm font-semibold">
          Title
        </label>
        <input
          id={`${fieldId}-title`}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          required
          minLength={2}
          className="mt-1 w-full rounded-lg border border-border bg-card px-3 py-2 text-sm"
        />
      </div>

      <div>
        <label htmlFor={`${fieldId}-description`} className="text-sm font-semibold">
          Description <span className="font-normal text-muted">(optional)</span>
        </label>
        <textarea
          id={`${fieldId}-description`}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          rows={3}
          maxLength={600}
          className="mt-1 w-full rounded-lg border border-border bg-card px-3 py-2 text-sm"
        />
      </div>

      <div>
        <label htmlFor={`${fieldId}-author`} className="text-sm font-semibold">
          Author <span className="font-normal text-muted">(optional)</span>
        </label>
        <input
          id={`${fieldId}-author`}
          value={author}
          onChange={(event) => setAuthor(event.target.value)}
          className="mt-1 w-full rounded-lg border border-border bg-card px-3 py-2 text-sm"
        />
      </div>

      {upload ? (
        <div>
          <label htmlFor={`${fieldId}-file`} className="text-sm font-semibold">
            {MATERIAL_LABELS[type]} file
          </label>
          <input
            id={`${fieldId}-file`}
            type="file"
            accept={type === "PDF" ? "application/pdf" : "image/*"}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void handleFile(file);
            }}
            className="mt-1 w-full text-sm"
          />
          {uploading && <p className="mt-1 text-xs text-muted">Uploading…</p>}
          {url && !uploading && (
            <p className="mt-1 truncate text-xs text-muted">Uploaded: {url}</p>
          )}
          {type === "PDF" && (
            <p className="mt-2 text-xs text-muted">
              Cloudinary blocks PDF delivery by default. If an uploaded PDF will
              not open, enable Settings → Security → “PDF and ZIP files
              delivery” in the Cloudinary dashboard.
            </p>
          )}
        </div>
      ) : (
        <div>
          <label htmlFor={`${fieldId}-url`} className="text-sm font-semibold">
            {type === "VIDEO" ? "YouTube or Vimeo URL" : "URL"}
          </label>
          <input
            id={`${fieldId}-url`}
            value={url}
            onChange={(event) => setUrl(event.target.value)}
            placeholder="https://"
            required
            className="mt-1 w-full rounded-lg border border-border bg-card px-3 py-2 text-sm"
          />
          {type === "VIDEO" && (
            <p className="mt-1 text-xs text-muted">
              A hosted embed, not a direct video file — it gives students
              adaptive quality on slow connections.
            </p>
          )}
        </div>
      )}

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={!isFree}
          onChange={(event) => setIsFree(!event.target.checked)}
        />
        Premium — listed to everyone, opens only for subscribers
      </label>

      <div className="flex gap-2">
        <Button type="submit" disabled={saving || uploading || !url}>
          {saving ? "Saving…" : material ? "Save changes" : "Add material"}
        </Button>
        <Button type="button" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
```

Before writing this file, open `src/components/ui/button.tsx` and `src/components/admin/status-banner.tsx` and match their actual prop names — the `variant`/`tone` names above follow the repo's convention but must be checked, not assumed.

- [ ] **Step 3: Write the console page**

Create `src/app/admin/(console)/library/page.tsx`. It is a server component that resolves subjects and the selected subject's materials, then hands them to a client list that owns the add/edit/delete/reorder interactions.

```tsx
import { requireAdminPage } from "@/lib/admin-session";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState } from "@/components/admin/empty-state";
import {
  listMaterialSubjects,
  listMaterials,
} from "@/lib/admin-material-data";
import { MaterialManager } from "@/components/admin/material-manager";

export const dynamic = "force-dynamic";

export default async function AdminLibraryPage({
  searchParams,
}: {
  searchParams: Promise<{ subjectId?: string }>;
}) {
  // The layout's check does not re-run on client-side navigation between admin
  // routes, so each page carries its own.
  await requireAdminPage();

  const { subjectId } = await searchParams;
  const subjects = await listMaterialSubjects();
  const materials = subjectId ? await listMaterials(subjectId) : [];

  return (
    <div>
      <PageHeader
        title="Library"
        description="File PDFs, images, videos and links under a subject. Students see them on their shelf."
      />

      {subjects.length === 0 ? (
        <EmptyState
          title="No subjects yet"
          message="Seed the subject list before adding materials."
        />
      ) : (
        <MaterialManager
          subjects={subjects}
          selectedSubjectId={subjectId ?? null}
          materials={materials}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 4: Write the manager component**

Create `src/components/admin/material-manager.tsx`. It renders the subject picker, the table, and the form; it re-fetches through `router.refresh()` after every mutation so the server component stays the source of truth.

```tsx
"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AdminTable, AdminTd, AdminTh, AdminTr } from "@/components/admin/admin-table";
import { EmptyState } from "@/components/admin/empty-state";
import { ConfirmDialog } from "@/components/admin/confirm-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MATERIAL_LABELS } from "@/lib/materials";
import { MaterialForm, type MaterialRow } from "@/components/admin/material-form";

type SubjectOption = {
  id: string;
  name: string;
  code: string;
  trackCategory: string;
  _count: { resources: number };
};

export function MaterialManager({
  subjects,
  selectedSubjectId,
  materials,
}: {
  subjects: SubjectOption[];
  selectedSubjectId: string | null;
  materials: MaterialRow[];
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [editing, setEditing] = useState<MaterialRow | null>(null);
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState<MaterialRow | null>(null);

  function selectSubject(id: string) {
    const params = new URLSearchParams(searchParams.toString());
    if (id) params.set("subjectId", id);
    else params.delete("subjectId");
    router.push(`/admin/library?${params.toString()}`);
  }

  function done() {
    setAdding(false);
    setEditing(null);
    router.refresh();
  }

  async function move(material: MaterialRow, direction: -1 | 1) {
    const ordered = [...materials].sort((a, b) => a.orderIndex - b.orderIndex);
    const index = ordered.findIndex((row) => row.id === material.id);
    const swapWith = ordered[index + direction];
    if (!swapWith) return;

    // Swap the two indexes. Sequential, not parallel: two PATCHes racing on
    // adjacent rows can interleave and leave both holding the same index.
    await fetch(`/admin/api/materials/${material.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ orderIndex: swapWith.orderIndex }),
    });
    await fetch(`/admin/api/materials/${swapWith.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ orderIndex: material.orderIndex }),
    });
    router.refresh();
  }

  async function confirmDelete() {
    if (!deleting) return;
    await fetch(`/admin/api/materials/${deleting.id}`, { method: "DELETE" });
    setDeleting(null);
    router.refresh();
  }

  return (
    <div className="space-y-5">
      <div>
        <label htmlFor="material-subject" className="text-sm font-semibold">
          Subject
        </label>
        <select
          id="material-subject"
          value={selectedSubjectId ?? ""}
          onChange={(event) => selectSubject(event.target.value)}
          className="mt-1 w-full max-w-sm rounded-lg border border-border bg-card px-3 py-2 text-sm"
        >
          <option value="">Choose a subject…</option>
          {subjects.map((subject) => (
            <option key={subject.id} value={subject.id}>
              {subject.name} ({subject._count.resources})
            </option>
          ))}
        </select>
      </div>

      {!selectedSubjectId ? (
        <EmptyState
          title="Choose a subject"
          message="Pick a subject above to see and manage its materials."
        />
      ) : (
        <>
          {!adding && !editing && (
            <Button onClick={() => setAdding(true)}>Add material</Button>
          )}

          {(adding || editing) && (
            <MaterialForm
              subjectId={selectedSubjectId}
              material={editing ?? undefined}
              onSaved={done}
              onCancel={done}
            />
          )}

          {materials.length === 0 ? (
            <EmptyState
              title="No materials yet"
              message="Nothing is filed under this subject. Add the first one above."
            />
          ) : (
            <AdminTable caption="Materials filed under this subject">
              <thead>
                <AdminTr>
                  <AdminTh>Title</AdminTh>
                  <AdminTh>Type</AdminTh>
                  <AdminTh>Access</AdminTh>
                  <AdminTh align="right">Order</AdminTh>
                  <AdminTh align="right">Actions</AdminTh>
                </AdminTr>
              </thead>
              <tbody>
                {materials.map((material) => (
                  <AdminTr key={material.id}>
                    <AdminTd>{material.title}</AdminTd>
                    <AdminTd>{MATERIAL_LABELS[material.resourceType]}</AdminTd>
                    <AdminTd>
                      {material.isFree ? (
                        <Badge variant="neutral">Free</Badge>
                      ) : (
                        <Badge variant="amber">Premium</Badge>
                      )}
                    </AdminTd>
                    <AdminTd align="right">
                      <button onClick={() => void move(material, -1)} aria-label="Move up">
                        ↑
                      </button>{" "}
                      <button onClick={() => void move(material, 1)} aria-label="Move down">
                        ↓
                      </button>
                    </AdminTd>
                    <AdminTd align="right">
                      <button onClick={() => setEditing(material)}>Edit</button>{" "}
                      <button onClick={() => setDeleting(material)}>Delete</button>
                    </AdminTd>
                  </AdminTr>
                ))}
              </tbody>
            </AdminTable>
          )}
        </>
      )}

      {deleting && (
        <ConfirmDialog
          title="Delete this material?"
          message={`"${deleting.title}" will disappear from the student shelf. This cannot be undone.`}
          confirmLabel="Delete"
          onConfirm={() => void confirmDelete()}
          onCancel={() => setDeleting(null)}
        />
      )}
    </div>
  );
}
```

Open `src/components/admin/confirm-dialog.tsx`, `src/components/admin/admin-table.tsx` and `src/components/ui/badge.tsx` and match their real prop names before writing this — the names above follow the repo's convention but must be verified.

- [ ] **Step 5: Typecheck and lint**

```bash
npx tsc --noEmit
npm run lint
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/app/admin/\(console\)/library src/components/admin/material-form.tsx src/components/admin/material-manager.tsx src/lib/admin-nav.ts
git commit -m "feat(library): admin console section for subject materials"
```

---

### Task 6: Teach the student surface the new types

The two student components key their icons off the old lowercase strings. Left alone, every material falls through to the generic file icon and the type chip renders "Pdf".

**Files:**
- Modify: `src/components/library/library-view.tsx` (`RESOURCE_ICONS` ~line 57, `isReadable` ~line 71, the type chip ~line 100)
- Modify: `src/components/classroom/topic-resources.tsx` (`RESOURCE_ICONS` ~line 29, the chip ~line 87)

**Interfaces:**
- Consumes: `MATERIAL_LABELS` (Task 1).
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Fix the library shelf icons**

In `src/components/library/library-view.tsx`, replace the `RESOURCE_ICONS` map with:

```tsx
// Keyed by the MaterialType members. Lesson resources use their own lowercase
// strings, so the lookup lowercases before matching and both kinds land on the
// same icon language.
const RESOURCE_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  pdf: LuFileText,
  image: LuImage,
  video: LuVideo,
  link: LuLink,
};

function ResourceIcon({ type }: { type: string }) {
  const Icon = RESOURCE_ICONS[type.toLowerCase()] ?? LuFile;
  return <Icon className="h-5 w-5" />;
}
```

Add `LuImage` to the `react-icons/lu` import and remove `LuBook`, `LuClipboardList` and `LuScrollText` **only if** nothing else in the file still uses them — check with a search before deleting an import.

- [ ] **Step 2: Fix the readability check**

Replace `isReadable`:

```tsx
function isReadable(resource: SubjectResource) {
  // A locked resource arrives with its url stripped, so there is nothing to
  // open and nothing to read.
  if (resource.locked) return false;
  // Keyed off the type, not a `.pdf` suffix: a Cloudinary raw URL does not
  // necessarily end in `.pdf`, and the old suffix check refused to open one.
  return resource.resourceType === "PDF";
}
```

- [ ] **Step 3: Fix the type chip**

In the same file, the chip renders `{resource.resourceType.replace("_", " ")}` under a `capitalize` class, which would show "Pdf". Replace the expression with `{MATERIAL_LABELS[resource.resourceType as MaterialType] ?? resource.resourceType}` and drop `capitalize` from that element's class list. Import at the top:

```tsx
import { MATERIAL_LABELS, type MaterialType } from "@/lib/materials";
```

Also widen the local `SubjectResource` type's `resourceType` from `string` to `MaterialType`.

- [ ] **Step 4: Fix the classroom resource list**

`src/components/classroom/topic-resources.tsx` mixes lesson resources (lowercase strings like `"image"`, `"diagram"`) with subject materials (uppercase enum members), so its lookup must be case-insensitive too. Apply the same change as Step 1 to its `RESOURCE_ICONS` and `ResourceIcon`, keeping `diagram: LuImage` for the lesson side:

```tsx
const RESOURCE_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  pdf: LuFileText,
  image: LuImage,
  diagram: LuImage,
  video: LuVideo,
  link: LuLink,
};

function ResourceIcon({ type }: { type: string }) {
  const Icon = RESOURCE_ICONS[type.toLowerCase()] ?? LuFile;
  return <Icon className="h-5 w-5" />;
}
```

And replace its chip expression `{item.resourceType.replace("_", " ")}` with a label lookup that falls back for the lesson-side strings:

```tsx
{MATERIAL_LABELS[item.resourceType as MaterialType] ?? item.resourceType}
```

Keep `capitalize` on this one — the lesson-side values are still lowercase words that need it.

- [ ] **Step 5: Typecheck and lint**

```bash
npx tsc --noEmit
npm run lint
```

Expected: clean. This is the point at which the errors Task 2 predicted should disappear.

- [ ] **Step 6: Commit**

```bash
git add src/components/library/library-view.tsx src/components/classroom/topic-resources.tsx
git commit -m "fix(library): render material types from the enum, not the old strings"
```

---

### Task 7: End-to-end verification

No new code. This is the gate the spec's Verification section describes; do not report the feature complete before every box here is ticked with observed output.

**Files:** none.

- [ ] **Step 1: Full static check**

```bash
npm run lint
npx tsc --noEmit
npm test
```

Expected: all three clean. Paste the actual output — a claim of "passing" without it does not count.

- [ ] **Step 2: Confirm the database state**

In the Supabase SQL Editor:

```sql
select column_name, udt_name
from information_schema.columns
where table_name = 'SubjectResource' and column_name = 'resourceType';
```

Expected: `udt_name` = `MaterialType`.

- [ ] **Step 3: Check the student surface survives an empty shelf**

Start the dev server, sign in as a student, and open `/library`. Expected: subjects list with a resource count of 0 and an empty state inside a subject — not an error. Then open a classroom topic that has no lesson resources of its own. Expected: no subject-resource fallback section, no crash.

- [ ] **Step 4: Exercise the admin section**

Sign in at `/admin/login`, open `/admin/library`, pick a subject, and add one material of each of the four types:
- a **PDF** upload (if it will not open on the shelf, that is the Cloudinary PDF-delivery setting the form warns about, not a code bug)
- an **IMAGE** upload
- a **VIDEO** as a YouTube link — and confirm a direct `.mp4` URL is refused with a readable message
- a **LINK** — and confirm an `http://` URL is refused

Then edit one, reorder two with the arrows, and delete one through the confirm dialog.

- [ ] **Step 5: Confirm the audit trail**

Open `/admin/audit`. Expected: `material.create`, `material.update` and `material.delete` rows for the actions just performed.

- [ ] **Step 6: Confirm the student sees them**

Back on `/library` as a student, open that subject. Expected: the materials appear in the order set in the console, each with the right icon and type label, premium ones badged. Sign in as a free account and confirm a premium material is listed but does not open.

- [ ] **Step 7: Commit any fixes and report**

If steps 3–6 turned up defects, fix them, re-run Step 1, and commit. Report the outcome with the actual command output — including anything that failed.
