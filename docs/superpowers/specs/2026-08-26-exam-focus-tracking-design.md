# Exam focus tracking

Stage 2 of the exam-integrity work. Stage 1 (the navigation guard) is already
built: leaving an exam through the sidebar, the mobile drawer, the user menu or
the browser back button now goes through a confirm dialog, and `beforeunload`
still covers refresh and tab close.

Stage 2 records the departures Stage 1 cannot prevent — tab switches and app
switches — and surfaces them on the result.

## Goal

Give a student's score the context of how often they left the exam, so a
teacher reading a result can tell a focused sitting from a distracted one.

## Non-goals

**No auto-submit, and no on-screen punishment.** Focus events fire constantly
for innocent reasons on the phones our students use: an incoming call, the
Android keyboard opening, a notification banner, screen timeout, a screenshot.
Ending a two-hour mock exam on any of those is unforgivable, and it would only
ever hit the honest student — someone determined to cheat uses a second device
and never leaves the tab at all. The punishment layer costs real users and
catches nobody.

**No fullscreen enforcement, no disabled copy/paste or right-click, no webcam.**
Trivially bypassed, and hostile to the majority who are not cheating.

**No prevention claims.** A browser cannot stop a phone on the desk or a friend
in the room. What follows is deterrence and an audit trail.

## What counts as an away event

One pure function, unit-tested, decides this. Two rules:

1. **`visibilitychange` only — never `blur`.** `blur` fires when focus moves to
   the devtools, the URL bar, a `<select>` popup or an OS notification, none of
   which mean the student left. `document.hidden` is the narrower, truer signal.
2. **Absences under 3 seconds are ignored.** A screenshot, a glanced-at banner
   and a mistap all land well under that. Only a return counts, so an absence
   is measured, not assumed.

An event is therefore recorded on *return to visibility*, with the duration it
lasted, and dropped if that duration is below the floor.

The exam already registers a `visibilitychange` listener for the timer
(`use-exam-session.ts:242`), so this adds a callback to an event that fires
only on a genuine tab switch.

## Where the count lives

The count is held in a **ref**, so no focus event causes a re-render.

It is mirrored into the existing `localStorage` session so a resumed exam does
not restart from zero.

> **This must be an optional field, and `STORAGE_VERSION` must not change.**
> `parseStoredSession` rejects any stored session whose `v` does not match
> (`exam-state.ts:97`), so bumping the version would discard every in-progress
> exam the moment the deploy lands. A new optional field defaulting to `0`
> leaves existing sessions readable.

It reaches the server **in the body of the existing submit request**
(`use-exam-session.ts:298`). No polling, no beacon, no extra requests during
the exam.

`navigator.sendBeacon` on each event was considered and rejected: it would
spend a request and a little of the student's data every time they take a call,
to buy durability for a number that only matters once the attempt is graded.

## Schema

One column on `AssessmentAttempt`:

```prisma
awayEvents Int @default(0)
```

No index — it is read from a row both the results view and the submit path
already fetch, and it is never a query predicate.

Applied through the Supabase SQL editor rather than `prisma migrate`, per the
project's standing constraint, with the migration file written by hand and its
checksum verified under LF endings.

## Server handling

`submitAssessmentSchema` gains an optional `awayEvents: z.number().int().min(0).max(10_000)`.

Optional so an older client, or a session stored before this ships, still
submits successfully. Absent means zero.

The bound is deliberate: this number is **client-reported and forgeable**. It is
clamped so a malformed or hostile payload cannot write nonsense, and it is
treated as a signal rather than evidence — see Limitations.

`submitAttempt` writes it alongside the grade. A replayed submission does not
overwrite the stored value; the first graded submission is authoritative.

## What the student and teacher see

**During the exam: nothing at all.** No counter, no banner, no acknowledgement
that the student was away. Recording is silent.

A soft line on returning to a timed exam — *"Welcome back — your timer kept
running."* — was considered and deliberately left out of this stage. It is
useful regardless of why someone left, but it also tells a student the page
noticed, which is the first step towards the punishment model this design
rejects. If it is wanted later it is a separate, self-contained change.

**On the result:** a quiet row in the results view, shown only when the count is
above zero and the assessment is one of `MOCK_EXAM`, `CBT_PRACTICE` or
`PAST_PAPER`. A `TOPIC_QUIZ` never displays it — nobody cheats on ten questions,
and flagging one is noise that trains people to ignore the flag.

Wording states the fact and draws no conclusion: *"Left the exam 4 times."*

## Limitations

Stated plainly so nobody builds policy on top of a number that cannot carry it:

- **Client-reported.** Anyone who can open devtools can submit a zero. The
  count catches the careless, not the determined.
- **Lost on abandonment.** An attempt that is never submitted is reaped to
  `TIMED_OUT` by `reapStaleAttempts` and its count dies with the browser. Only
  submitted attempts carry the number.
- **Blind to the second device.** The cheat that matters most produces no
  signal at all.

The strongest control remains the one already in place: the
**server-authoritative deadline**. Time spent away is time lost, decided
server-side, and not something the client can talk its way out of.

## Testing

Pure logic, in the style of `exam-state` and its `scripts/test-exam-state.mts`:

- an absence longer than the floor is recorded
- an absence shorter than the floor is ignored
- an absence exactly at the floor is ignored (boundary stated, not left to chance)
- becoming hidden alone records nothing until visibility returns
- a session resumed from storage continues from its stored count
- a stored session written before this field existed reads as zero
- the submit payload omits the field when the count is zero

Server-side, extending the existing submit tests:

- a submission with no `awayEvents` grades and stores zero
- a submission above the clamp is rejected as a validation error
- a replayed submission leaves the stored count unchanged

## Files

| File | Change |
| --- | --- |
| `src/components/assessment/exam-focus.ts` | New. Pure away-event rules. |
| `scripts/test-exam-focus.mts` | New. Tests for the above; registered in `package.json`. |
| `src/components/assessment/exam-state.ts` | Optional `awayEvents` on the stored session. **`STORAGE_VERSION` unchanged.** |
| `src/components/assessment/use-exam-session.ts` | Track returns to visibility; include the count in the submit body. |
| `src/lib/validators.ts` | Optional, clamped `awayEvents` on `submitAssessmentSchema`. |
| `src/lib/assessment-submit.ts` | Persist on grade; leave untouched on replay. |
| `src/lib/attempt-results.ts` | Select the column into the result payload. |
| `src/components/assessment/results-view.tsx` | Conditional row, high-stakes assessment types only. |
| `prisma/schema.prisma` + migration | `awayEvents Int @default(0)`. |
