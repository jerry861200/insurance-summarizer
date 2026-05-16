from __future__ import annotations

EXTRACTION_SYSTEM_PROMPT = """You are a careful, conservative insurance policy data extractor. Your job is to read
the provided policy document and extract a fixed set of structured fields.

Rules:
1. Use only information present in the document. NEVER invent or infer values that
   are not explicitly stated.
2. If a field is genuinely not present, return null rather than guessing.
3. Normalize dates to ISO 8601 (YYYY-MM-DD). The source format varies (e.g.,
   "May 1, 2008", "05/01/2008", "1st May 2008") — convert to YYYY-MM-DD.
4. Normalize money amounts to plain decimal numbers. Strip currency symbols, commas,
   and trailing text. "$500,000.00" -> 500000.00.
5. Premium frequency: if the document shows multiple frequencies (e.g., "Monthly: $36
   / Annual: $432"), prefer monthly and report that.
6. For face_amount on life policies, this is the death benefit (sometimes labeled
   "Face Amount", "Sum Insured", "Coverage Amount", or "Death Benefit").
7. For exclusions, summarize each exclusion as one short sentence. Do not paraphrase
   in a way that changes meaning — when in doubt, quote.
8. Populate source_page_hints with the page number(s) where you found each field
   value. The document is delimited by "--- PAGE N ---" markers.
9. Use extraction_notes to flag any ambiguities: multiple candidate values, unclear
   dates, conflicting amounts, etc.

You MUST call the save_policy_fields tool exactly once. Do not produce any text
response outside the tool call."""


SUMMARY_SYSTEM_PROMPT = """You are an insurance brokerage analyst producing a concise, factual summary of an
insurance policy for a busy broker. The reader needs to grasp the policy in under
60 seconds.

Rules:
1. Use ONLY information from the provided extracted fields and document excerpts.
   Do not introduce facts not present in the inputs.
2. Be specific: use exact numbers, dates, and names.
3. Be brief: target 150-250 words total.
4. Output Markdown with this exact structure:

   ## Policy Summary

   **Policy** {number} — {insurer} {policy_type}
   **Insured:** {name}
   **Effective:** {effective_date} — **Expires:** {expiration_date}
   **Premium:** {amount} ({frequency})
   **Face Amount / Coverage:** {amount}

   ### Key Terms
   - {2-4 bullets covering coverage highlights}

   ### Notable Exclusions & Conditions
   - {2-4 bullets summarizing the most material exclusions}

   ### Things to Verify
   - {1-3 bullets calling out anything ambiguous, missing, or worth a broker's
     second look}

5. If a field is missing (null), write "Not specified in document" rather than
   omitting the line.
6. Do not invent exclusions. If fewer than 2 exclusions were extracted, list what
   you have and add "(no other exclusions detected)"."""


EXTRACTION_TOOL_SCHEMA = {
    "name": "save_policy_fields",
    "description": "Save the structured fields extracted from an insurance policy document. Call this exactly once per document with your best extraction.",
    "input_schema": {
        "type": "object",
        "properties": {
            "policy_number": {"type": ["string", "null"], "description": "The unique policy identifier as printed on the document. Preserve original formatting. Return null if not found."},
            "insured_name": {"type": ["string", "null"], "description": "Full name of the primary insured person or entity. Return null if not found."},
            "insurer_name": {"type": ["string", "null"], "description": "Name of the insurance company issuing the policy (e.g., 'Pacific Life Insurance Company')."},
            "policy_type": {"type": ["string", "null"], "description": "Type of insurance policy in 1-4 words (e.g., 'Term Life', 'Whole Life', 'Auto', 'Homeowners')."},
            "effective_date": {"type": ["string", "null"], "description": "Policy effective/inception date in ISO 8601 format (YYYY-MM-DD). Return null if not found."},
            "expiration_date": {"type": ["string", "null"], "description": "Policy expiration/termination date in ISO 8601 format (YYYY-MM-DD). For life policies, use the maturity date or coverage end date. Return null if not found or if perpetual."},
            "premium_amount": {"type": ["number", "null"], "description": "The premium amount as a decimal number (no currency symbol). Use the initial or current premium. Return null if not found."},
            "premium_frequency": {"type": "string", "enum": ["monthly", "quarterly", "semi-annual", "annual", "single", "unknown"], "description": "How often the premium is paid."},
            "premium_currency": {"type": "string", "description": "ISO 4217 currency code (e.g., 'USD'). Default to 'USD' if not specified."},
            "face_amount": {"type": ["number", "null"], "description": "For life insurance: the death benefit / face amount as a decimal. For other policies: the primary coverage limit. Return null if not found."},
            "coverage_limits": {
                "type": "array",
                "description": "Each distinct coverage and its limit. Examples: face amount, bodily injury, property damage, deductibles.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "limit_amount": {"type": ["number", "null"]},
                        "currency": {"type": "string"},
                        "notes": {"type": ["string", "null"]}
                    },
                    "required": ["name"]
                }
            },
            "exclusions": {
                "type": "array",
                "description": "Each listed exclusion or limitation, summarized in one short sentence.",
                "items": {"type": "string"}
            },
            "source_page_hints": {
                "type": "object",
                "description": "For each top-level field above, the page number(s) where it appears. Keys are field names; values are arrays of integers.",
                "additionalProperties": {"type": "array", "items": {"type": "integer"}}
            },
            "extraction_notes": {
                "type": "string",
                "description": "Brief free-text note about any ambiguities. 1-3 sentences max."
            }
        },
        "required": ["policy_number", "insured_name", "insurer_name", "policy_type", "effective_date", "expiration_date", "premium_amount", "premium_frequency", "premium_currency", "face_amount", "coverage_limits", "exclusions", "source_page_hints", "extraction_notes"]
    }
}
