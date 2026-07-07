# Unit Economics — Cost per Game & Credit Pricing

## Why
Know COGS and set a pricing hypothesis so credits are priced above cost. Not an MVP blocker (validate
demand first), but the number that decides whether this is a business at all and what a credit costs.

## Owner's data + decisions (Background)
- **COGS baseline:** renting a **5090 (≈ owner's own hardware)** generates a game in **~1 hour for
  ~$0.99**. That's the base compute cost per game.
- **Pricing hypothesis:** **1 game credit = $5.00** for a base game. Premium builds cost **more
  credits** (e.g. 2).
- **Premium is TWO axes, not just speed** — "more credits" can buy either or both:
  - **Faster** — A100 rental, quicker wall-clock (the original "speed up").
  - **Higher quality** — larger LLM (better writing/coherence) + stronger image models (better art).
  These are separate levers with separate COGS; a build could be faster, better, or both. Price each
  axis (or a combined premium tier) once the cost of each is measured.
- **Model: credit-per-GAME, not time-metered.** Matches the Steam "buy a game" vibe — a credit buys a
  finished artifact, not minutes. (Locks the `auth_and_billing.md` cost-formula direction: base =
  1 credit/game; premium tiers = more credits.)
- **Validate before committing:** put it in front of a few people first; confirm demand + real
  cost/time/failure before setting a public price. MVP does NOT require pricing figured out.
- **Ownership/resale is a SEPARATE, later tier** (not base credits): ~**100 game credits** unlocks the
  ability to sell the game (or a "contact me" to create a baseline they then modify); owner may also
  list the game for sale on the site. Down the line — see `legal_ops.md`.

## Rough margin sketch (base game, sanity check only)
- Price $5.00 − compute $0.99 − payment fees (~2.9% + $0.30 ≈ $0.45) ≈ **~$3.56 gross** before
  storage/egress, any API asset costs, and overhead. Healthy on compute alone.
- **Reliability IS margin.** A failed/thrashed build that must retry doubles the $0.99. At a 1-in-3
  retry rate effective compute ≈ $1.30–2.00. A refund-on-fail (auth T3) protects the user but eats the
  compute. → the quality/failure-rate bar (`quality_backlog.md` §1) is also an economics lever.
- **Premium tiers — verify each axis separately.** Faster (A100) looks high-margin IF genuinely
  ~2× faster (2 credits = $10 for possibly *less* wall-clock compute) — verify A100 $/hr × build
  time. Higher-quality (larger LLM + stronger image models) adds its own COGS (bigger model = more
  GPU/VRAM/time, or an API bill) — measure it before pricing; "better" is not free.

## Fixed costs (the part per-game margin ignores)
Per-game margin is gross; **fixed monthly costs come off the top** and set the break-even volume.
Owner's rough view: scales to **a few hundred $/month** at some point. Components:
- **Site hosting** — ~$4/mo now, **will need to scale** (more users/traffic).
- **Asset + game storage/hosting** — **not yet scoped** (real gap): storing generated games +
  assets for download, and the egress when users download them. Grows with every game made/kept.
  Needs a retention policy (keep forever? expire?).
- **Runpod network volume** — ~$20/mo (persistent storage for the inference stack).
- **Other** — domain, payment processor minimums, any managed DB, monitoring, email.

**Break-even framing:** fixed $/mo ÷ ~$3.5 gross margin/game = games/mo just to cover fixed. E.g.
**$300/mo ÷ $3.5 ≈ ~86 games/mo** before per-game margin is actual profit. Storage is the sneaky one
— it accrues even for games nobody re-downloads.

## Future revenue ideas (spitball — not committed)

### Marketplace — sell premade games with a creator cut
Sell already-generated games from a catalog at some price, and pay the creator an incentive per sale
(owner's spitball: ~**0.05 credits per sale**, or a %).
- **Why it's high-leverage ("profit without sacrificing"):**
  - **Near-zero marginal COGS on a resale** — the game is already generated; a sale only costs
    storage/egress. Almost pure margin vs a fresh $0.99 build.
  - **Creator paid in CREDITS, not cash** — the incentive stays in-system; creators spend it
    generating more games → a flywheel (more supply, more usage, no cash out the door).
  - **Grows the catalog for free** — user-generated supply the owner doesn't pay to create.
- **Open decisions:**
  - Premade-game price + split (per-sale credits vs %; credits vs cash payout).
  - **Curation / quality bar** — an open resale catalog needs a floor or it fills with slop (ties to
    the judge, `quality_backlog.md` §1).
  - **Safety applies to the STOREFRONT too** — reselling user-generated content means
    `safety_filter.md` gates what can be listed, not just what's generated.
  - Ties to the ownership tier (`legal_ops.md`: 100-credit resale rights, owner-lists-for-sale) —
    the marketplace is the surface where that right is exercised; rev-split terms live there.

## Tasks
- [ ] **Measure real cost/time per game** across a few genres on the actual rental (5090), not the
      estimate — including asset generation, not just the LLM loop.
- [ ] **Measure failure/retry rate** — how often a build thrashes or produces a bad game needing a
      re-run. This sets effective COGS and the refund rate.
- [ ] **Verify the premium tiers** — (a) faster: A100 $/hr × build time vs the credit price;
      (b) higher-quality: larger-LLM + stronger-image-model COGS (GPU/VRAM/time or API) vs its credit
      price. Confirm each is actually better/faster AND still profitable.
- [ ] **Add the per-game hidden costs** — download egress, any per-asset API calls, payment processor
      fees, idle GPU / warm-pool cost (`scaleout.md` S3 tradeoff).
- [ ] **Model the FIXED monthly costs + break-even** — site hosting (scaling), asset/game
      storage+egress (scope this — currently unplanned), runpod network volume (~$20), DB, misc.
      Compute games/mo to break even; set a retention policy so storage doesn't accrue forever.
- [ ] **Set the launch price** from the above (validate the $5 hypothesis), and define the credit
      pack sizes.
- [ ] **Feed the numbers** into `auth_and_billing.md` (cost formula: 1 base / 2 speed-up) and
      `scaleout.md` S3 (metering) and `launch_plan.md` (viability gate).

## Ordering
Not an MVP blocker — validate demand in the private alpha first (manual credit grants, no real
pricing). Do the measurement DURING the alpha (real builds = real cost/failure data), then set price
before the paid stage.

## Parked
- Credit pack sizing + any subscription vs one-off.
- Whether the 100-credit resale tier is the right number (`legal_ops.md`).
