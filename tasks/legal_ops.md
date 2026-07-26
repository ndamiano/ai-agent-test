# Legal & Ops — Terms, Privacy, Ownership, Entity

Verified: 2026-07-25

## Why
Taking money and generating content for others needs legal basics. **Lower priority for a private
alpha** (few trusted users, manual/free access) — but still important, and **mandatory before public
or paid access.** Capture it now so it's not a scramble at launch.

## Guardrail / phasing
- **Private alpha** — shipped (`frontend/public/terms.html` + `privacy.html`, linked from the login
  screen with the 18+ / acceptance line). Deliberately operator-favorable and promise-free (owner's
  direction): no contact path, NO refund mentions, personal-use-only rights, terms changeable at any
  time without notice. Drafted in-house — **get counsel review before paid/public.**
- **Paid / public:** the full set below is required before the first real dollar or public signup.

## Owner's direction — content ownership (Background)
- **Base credits do NOT grant resale rights.** A generated game is playable/downloadable by the user,
  but ownership/commercial rights are separate.
- **~100 game credits unlocks the ability to sell it** — or a **"contact me"** path where the owner
  creates a baseline game the buyer then modifies.
- **Owner may also list the generated game for sale on the site.**
- This tier is **down the line** — record it now, formalize the terms when it ships. Feeds
  `unit_economics.md` (the 100-credit tier) and the ToS ownership clause.

## Checklist (build before paid/public)
- [ ] **Terms of Service** — acceptable use (ties to `safety_filter.md`: no illegal generation),
      alpha/beta disclaimers, liability limits.
      → done when: a counsel-reviewed, non-alpha `frontend/public/terms.html` exists, sign-off date noted here
- [ ] **Privacy policy** — what's stored (emails, prompts, generated content, payment via provider),
      retention, deletion.
      → done when: a counsel-reviewed, non-alpha `frontend/public/privacy.html` exists, sign-off date noted here
- [ ] **Refund policy** — especially failed builds (auth T3 refund-on-fail) + credit refunds.
      → done when: `frontend/public/refunds.html` exists and is linked from `terms.html`
- [ ] **Content / IP ownership clause** — encode the model above (base = play/download; 100-credit or
      contact-me = resale; owner may list for sale). The clause users will actually read for "is it
      mine?".
      → done when: `terms.html` has a dedicated ownership-clause section, counsel sign-off noted here
- [ ] **Age gate** — the login screen's 18+ affirmation covers the alpha
      (`frontend/src/components/LoginScreen.tsx`); a real gate for mature-but-legal content at
      public signup is open (illegal content is blocked separately — `safety_filter.md`).
      → done when: the owner has decided what the public-signup age gate is, and it is recorded here
- [ ] **Business entity** — the thing that takes money (LLC or equiv), so payments/taxes are clean.
      → done when: an entity name, jurisdiction, and formation date are recorded in this section
- [ ] **PII / data handling** — emails + any account data; payment PII stays with the processor
      (never store card data). Ties to `platform_polish.md` P2 + the `data_dir` datastore.
      → done when: `privacy.html`'s storage section is counsel-reviewed, sign-off date noted here

## Ordering
Alpha: the minimal ToS/privacy note only. Everything else lands in the paid-beta stage of
`launch_plan.md`, before real payments. Cheap to draft; get counsel review for ToS + CSAM obligations
(overlaps `safety_filter.md` Phase 1).

## Parked
- Jurisdiction / where the entity operates (also drives `safety_filter.md` illegal-content scope).
- Exact resale-tier terms (revenue split if owner lists it for sale, the "contact me" baseline flow).
