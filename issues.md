# Media Receipts - Issues and Decisions

This file records issues found during testing, the evidence used to diagnose
them, and the agreed solution. Changes should be tested locally before they are
committed, pushed, or deployed.

## 1. Filename requires a complete job code

### Issue

The production processor currently validates the filename before opening the
PDF. A filename such as:

```text
CFXL 1208327-3 Sept 20 2026 JAY.pdf
```

contains only the client initials (`JAY`), not a complete job code such as
`JAY-3646`. `validate_filename()` therefore returns a naming error, and the
processor routes the file as a naming error without reaching PDF text
extraction.

### Finding

The existing `extract_job_codes()` function can search PDF text when the
filename has no complete code. In an isolated, non-destructive test of 25 files
from Dropbox Incoming, it found:

- 19 apparent single-job invoices
- 5 genuine multi-job invoices
- 1 image-only Spotify PDF with no embedded text

The fallback was initially blocked because filename validation ran first. It was
then enabled locally after the 25-file isolated test.

### Solution implemented locally

Treat a missing filename job code as a warning rather than a hard naming error.
Then extract the PDF text and classify the filtered results as follows:

```text
One job code       -> verify the job through Function Point
Multiple job codes -> Manual Enter - Multi-Job; no Function Point lookup
No job code        -> Manual Review
No readable text   -> Manual Review/OCR candidate
```

Filename initials are treated only as a human hint. When the PDF contains one
complete job code, that code is used and validated through Function Point even
if the trailing filename initials are different.

## 2. Regex detects non-job text as job codes

### Issue

The job pattern is intentionally broad enough to recognize codes with spaces or
dashes. It consequently detected some code-shaped non-job text:

```text
P.O. BOX 7400        -> BOX-7400
CMA / RMR 2026       -> RMR-2026
```

These false candidates made otherwise single-job invoices appear multi-job.

### Solution implemented locally

Added contextual filters to `extract_job_codes()`:

- Ignore `BOX-####` when it occurs in a `P.O. BOX ####` address.
- Ignore `RMR-####` when it occurs in an Astral `CMA / RMR ####` format line.
- Preserve genuine codes on PO, reference, product, campaign, and ad-delivery
  lines.

The filters were tested against synthetic examples and all 25 real incoming
PDFs. They are local only and have not been committed, pushed, or deployed.

## 3. Genuine multi-job invoices

### Finding

The following tested invoices contain multiple real billed job lines and should
remain multi-job:

- Two Meta invoices with 10 BEA campaign/job codes each
- Two Pattison JWI invoices containing `JWI-3492` and `JWI-3736`
- One Reddit invoice containing `DE-3672` and `DE-3718`

The processor already performs Function Point lookup only when exactly one job
code remains. Genuine multi-job invoices therefore do not trigger one API call
per detected job; they route directly to `Manual Enter - Multi-Job`.

## 4. Filename initials differ from the PDF job

### Issue

Two files in the test batch have filename initials that do not agree with the
job code visible in their PDF text:

```text
Astral AST_230970 Sept 27 2026 NT.pdf -> PDF contains JAY-3646
CJAY 2085307-3 Sept 27 2026 NT.pdf    -> PDF contains JAY-3646
```

### Decision

Do not treat this as a conflict. The initials are not reliable enough to reject
an otherwise complete code. Use the single complete job code extracted from the
PDF and allow the normal Function Point lookup to validate it. If Function Point
cannot resolve the job or expense match, route it to Manual Review through the
existing lookup-failure behavior.

## 5. Image-only PDFs

### Issue

`Spotify 5208182 Sept 19 2026 CBH.pdf` returned no embedded text through
`pdfplumber`.

### Proposed handling

Keep OCR or Claude PDF vision as a later fallback. For now, route unreadable or
image-only PDFs to manual review. Claude remains a last resort if deterministic
regex, contextual filtering, and Function Point validation leave too many
unresolved invoices.

## Intended decision flow

```text
PDF enters Incoming
        |
Filename has full job code? ---- yes ---> use filename code
        |
        no
        |
Extract embedded PDF text
        |
Regex finds code-shaped candidates
        |
Remove contextual false positives
        |
        +-- one code ---> Function Point validation ---> process/review
        |
        +-- many real codes ---> Multi-Job (no FP lookup)
        |
        +-- none/unreadable ---> Manual Review or future OCR/Claude fallback
```

## 6. Admin Function Point API key returned 401

### Issue

The new long-lived key created in Function Point Admin was stored correctly in
DigitalOcean, but deployed Preview returned `401 Unauthorized` during
`GET /dockets?number=...`.

### Cause and solution

Direct read-only testing of the local annual key confirmed the required formats:

```text
X-API-Key: <admin key>              -> 200 OK
Authorization: <admin key>          -> 401 JWT Token not found
Authorization: Bearer <admin key>   -> 401 Invalid JWT Token
```

The backend now identifies login JWTs by their three dot-separated segments.
JWTs use `Authorization: Bearer`; admin-created keys use `X-API-Key`.

## 7. Astral invoice number and date were missing

### Issue

Astral PDFs use compact bilingual labels that did not match the general
extractors:

```text
Invoice / FactureAST/230970
Date:27 Sept/Sep 2026
```

This produced `NO_INVOICE_NUMBER; NO_DATE` even though both values were visible.

### Solution

Added general extraction for compact bilingual `Invoice / Facture` identifiers
and explicitly labelled bilingual `Date:DD Sept/Sep YYYY` values. The filename
date parser also recognizes both `Sep` and `Sept`, so `Sept 27 2026` remains a
fallback when the PDF date is missing or unreadable. A labelled PDF date is
still preferred over campaign or flight-date ranges elsewhere on the invoice.

## 8. Generic no-import-row preview messages

### Issue

Some correctly routed files displayed `No import-ready row was generated`,
which did not explain the underlying result.

- CJAY rows used the literal header word `Invoice` as their reference, so the
  preview could not associate the generated row with its source filename.
- Genuine multi-job files had no import row by design, but the preview did not
  include their detected job list.

### Solution

- Added the broadcast table pattern where `Invoice #` and `Invoice Date` labels
  are on one line and their values are on the next.
- Added explicit filename supplier mappings for Astral, CJAY-FM, CHUP-FM, and
  Postmedia so their confidence and FP matching do not depend on parent-company
  text inside the PDF.
- Multi-job preview messages now list the detected jobs and explain that a
  manual split is required.
