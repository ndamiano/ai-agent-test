# Legal & Ops — Terms, Privacy, Ownership, Entity

## Why
Taking money and generating content for others needs legal basics. **Lower priority for a private
alpha** (few trusted users, manual/free access) — but still important, and **mandatory before public
or paid access.** Capture it now so it's not a scramble at launch.

## Guardrail / phasing
- **Private alpha** (trusted handful, manual accounts, no real payments): minimal — a short ToS /
  "this is an alpha, no guarantees, don't misuse it" + a basic privacy note. Lower risk because users
  are known.
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
- [ ] **Privacy policy** — what's stored (emails, prompts, generated content, payment via provider),
      retention, deletion.
- [ ] **Refund policy** — especially failed builds (auth T3 refund-on-fail) + credit refunds.
- [ ] **Content / IP ownership clause** — encode the model above (base = play/download; 100-credit or
      contact-me = resale; owner may list for sale). The clause users will actually read for "is it
      mine?".
- [ ] **Age gate** — mature content requires an age affirmation; illegal content is blocked
      (`safety_filter.md`), but mature-but-legal still needs gating.
- [ ] **Business entity** — the thing that takes money (LLC or equiv), so payments/taxes are clean.
- [ ] **PII / data handling** — emails + any account data; payment PII stays with the processor
      (never store card data). Ties to `auth_and_billing.md` + `build_deploy.md` datastore.

## Ordering
Alpha: the minimal ToS/privacy note only. Everything else lands in the paid-beta stage of
`launch_plan.md`, before real payments. Cheap to draft; get counsel review for ToS + CSAM obligations
(overlaps `safety_filter.md` Phase 1).

## Parked
- Jurisdiction / where the entity operates (also drives `safety_filter.md` illegal-content scope).
- Exact resale-tier terms (revenue split if owner lists it for sale, the "contact me" baseline flow).
