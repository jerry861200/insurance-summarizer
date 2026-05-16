# Sample Summary — Leland Stanford Term Life Policy

This is the Markdown summary the pipeline produces for the sample PDF
(`sample/leland_stanford_policy.pdf`). The format follows the
`SUMMARY_SYSTEM_PROMPT` template in `app/extractors/llm/prompts.py` — the LLM
fills the slots, but the structure is fixed so a broker always sees the same
sections in the same order.

The example below is a handwritten illustration of the expected output. The
live pipeline produces this Markdown on each `POST /documents` and persists it
in the `summaries` table.

---

## Policy Summary

**Policy** VF99999990 — Pacific Life Insurance Company Term Life
**Insured:** LELAND STANFORD
**Effective:** 2008-05-01 — **Expires:** 2068-05-01 (age 95)
**Premium:** $36.00 (monthly)
**Face Amount / Coverage:** $500,000

### Key Terms
- Death benefit of $500,000 payable on death of the insured while the policy is in force
- Initial monthly premium of $36, increasing annually after the first policy year (see Premium Table years 1-60: $350 → $373,675)
- Coverage extends to age 95 with maturity date of 2068-05-01
- Convertible to a new permanent policy before 2018-05-01 without proof of insurability
- 31-day grace period for premium payments after the first

### Notable Exclusions & Conditions
- Suicide exclusion: if insured dies by suicide within 2 years of the policy date, the death benefit is limited to the sum of premiums paid
- Material misrepresentation in the application can void coverage during the first 2 years (incontestability clause)
- Misstatement of sex or birth date adjusts policy benefits to what the premiums paid would have purchased
- Non-participating: policy does not share in surplus earnings

### Things to Verify
- Free Look Right: 20-day return window after delivery — confirm date the policyholder received the policy
- Premium increases steeply after year 5 — confirm broker has explained the annual premium curve to the client
- Conversion period ends 2018-05-01 — past for new clients; verify this is acceptable
