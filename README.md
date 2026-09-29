# ZGM Media Receipt Processing System

Automates extraction and FunctionPointe import preparation for ZGM media invoices.

---

## Quick Start

The Lovable web app is the normal interface for the Accounts team:

1. Upload PDF invoices through Lovable, or place them directly in Dropbox
   **`/Automation Testing/Incoming`**.
2. Open the Lovable dashboard and refresh the invoice list.
3. Select invoices and click **Preview**. Preview is non-destructive: it shows
   the proposed spreadsheet fields and routing without moving any files.
4. Review any warnings, then select the invoices to run and click **Process**.
   The backend processes the selection as one batch and creates one combined FP
   import CSV, not one CSV per invoice.
5. Download the generated output through Lovable or use the official copy in
   Dropbox **`/Automation Testing/Output/<Mon YYYY>/`**.
6. Handle anything routed to Dropbox **`Manual Enter - Multi-Job/`** or
   **`Manual Review/`**.

Lovable does not contain Dropbox or Function Point credentials. It calls the
Python API deployed on DigitalOcean, and that backend performs the extraction,
Function Point lookup, Dropbox upload/download, report creation, and routing.

For local development or administrative testing, the processor can also be run
directly:

```bash
python3 process_media_receipts.py
```

Dropbox is the default. For troubleshooting with the original project-local
folders, run:

```bash
python3 process_media_receipts.py --local
```

Preview one Dropbox invoice without uploading or moving anything:

```bash
python3 process_media_receipts.py \
  --file "Reddit - 3948018 CFMWS-3265.pdf" \
  --dry-run
```

Preview several selected invoices as one batch by repeating `--file`:

```bash
python3 process_media_receipts.py \
  --file "Reddit - 3948018 CFMWS-3265.pdf" \
  --file "Linkedin Ireland - 781215884582 CFMWS-3265.pdf" \
  --dry-run
```

After reviewing the preview, process only that invoice:

```bash
python3 process_media_receipts.py \
  --file "Reddit - 3948018 CFMWS-3265.pdf"
```

When the API or CLI receives several `--file` selections, it downloads and
processes them in one batch. The run creates one combined FP import CSV and one
combined summary workbook, while routing each source invoice individually.

---

## File Naming Convention

Invoices must contain three useful pieces of information:

```
[Supplier hint] [Invoice/reference number] - [Full job code].pdf
```

**Examples:**
```
CFRN - 105R024853 - JAY-3313.pdf
Netflix CINV-5414568 - EE-3270.pdf
CFXL 1208327-3 Sept 20 2026 JAY-3646.pdf
```

- **Supplier hint** — the vendor or FP expense-type name used to begin matching
- **Invoice/reference number** — becomes `Reference Number` in the import file
- **Job information** — preferably the full client prefix plus job number (for
  example `JAY-3313`). If the filename contains only client initials such as
  `JAY`, the processor checks the PDF for the complete code.

The filename does not need to use one exact punctuation style. The processor
normalizes spaces, underscores, and common separators without renaming the
original Dropbox file. Descriptive words and dates may remain in the filename,
but shorter names are easier for the team to review.

Spaces around a job-code hyphen are normalized automatically. All of these are
treated as `FOR-3412`: `FOR-3412`, `FOR - 3412`, `FOR -3412`, and `FOR 3412`.
Common filename separators such as underscores are also accepted around the
job code, so `_JAY - 3313` is normalized to `JAY-3313`.
`GET /api/invoices` validates filenames before preview and returns
`naming_valid`, `naming_issues`, and normalized `job_codes` for the frontend.

Files with incorrect naming are moved to `Naming Errors/` and logged.

When the complete job code is absent from the filename, the PDF fallback:

- removes obvious non-job matches such as `P.O. BOX 7400` and
  `CMA / RMR 2026`;
- preserves multiple genuine campaign/job lines as a multi-job invoice;
- uses a complete job code found in the PDF even when the filename ends with
  different or incomplete initials;
- routes an unreadable PDF or a PDF with no usable job code to review rather
  than guessing.

### How the filename is used

The filename is a starting point, not the sole source of invoice data:

1. The full job code identifies the Function Point docket.
2. The supplier portion is treated as a hint and normalized through
   `SUPPLIER_MAP` and the Function Point supplier-code list.
3. The processor gets the job's available expense types and service groups from
   Function Point.
4. It matches the normalized supplier hint to one expense type on that job.
5. The PDF supplies or confirms the reference number, invoice date, amount, and
   GST wording.

Some invoice vendor names differ from their Function Point expense-type names.
These exceptions belong in `SUPPLIER_MAP`. For example, `CTV Edmonton` maps to
the `CFRN` expense type. Other CTV vendors are not automatically changed to
`CFRN`. If no unique Function Point match can be made, the invoice is routed to
`Manual Review/` rather than guessed.

---

## Dropbox Folder Structure

```
Automation Testing/
├── Incoming/                   ← Drop new invoices here
├── Processed/
│   └── Sep 2026/              ← Successfully processed during the run month
├── Output/
│   └── Sep 2026/              ← Generated reports grouped by run month
│       ├── FP_Import_Sep_2026_*.csv
│       ├── MultiJob_Summary_Sep_2026_*.csv
│       ├── ManualReview_Sep_2026_*.csv
│       └── Processing_Summary_Sep_2026_*.xlsx
├── Manual Enter - PO/          ← (reserved) Single-PO invoices for manual entry
├── Manual Enter - Multi-Job/   ← Invoices spanning multiple job codes
├── Manual Review/              ← Unknown supplier or missing job code
├── Naming Errors/              ← Files that don't match naming convention
├── Error/                      ← PDFs that couldn't be read
```

The Python source, supplier-code resource and `.env` remain local and are not
stored in the shared Dropbox folder.

---

## How the Script Works

1. **Lists** Dropbox `Incoming/` and downloads files to temporary local storage
2. **Validates** that the file is a PDF and reads its filename hints. A missing
   complete filename job code triggers PDF fallback rather than rejection.
3. **Extracts** text from each PDF using `pdfplumber`
4. **Detects** supplier → maps to FP supplier code
5. **Extracts** invoice number, date, amount, GST, and job code(s)
6. **Looks up single jobs in Function Point** → matches the supplier to an
   external expense and takes its parent estimate phase as the Service Group
7. **Routes** the file:
   - **Multiple job codes** → `Manual Enter - Multi-Job/` (FP can't auto-split)
   - **Missing required data, unknown supplier, or failed API match** →
     `Manual Review/`
   - **Clean single-job** → adds to FP import, moves to `Processed/`
8. **Uploads** output files to Dropbox `Output/<Mon YYYY>/`
9. **Moves** each original Dropbox invoice to its routed folder only after
   generated outputs upload successfully. Successful invoices go to
   `Processed/<Mon YYYY>/`; review and error destinations remain ungrouped.

The month folder is based on the processing run date, not the invoice date. It
is created automatically when the first run occurs in a new month. For example,
a September 2026 run uses `Sep 2026`, and a November 2026 run uses `Nov 2026`.

---

## FP Import CSV Columns

The generated CSV matches the FunctionPointe External Expense template:

| Column | Source |
|--------|--------|
| Reference Number | Invoice number from filename/PDF |
| *Supplier | Normalized supplier name derived from the filename/PDF hint |
| Expense Date | Date from PDF |
| Payable Account | Blank (fill in FP or set default in script) |
| Office | Blank |
| Description | Auto-generated summary |
| Terms | `Net 30` (configurable) |
| *Job | Full job number from the filename (numeric portion in the FP import) |
| *Expense Type | Matching external-expense name from the Function Point job |
| Quantity | `1` |
| Rate | Subtotal amount from PDF |
| Billed | Blank |
| Markup% | `0` |
| Tax Group | `GST` when GST wording appears on the invoice; otherwise blank |
| Service Group | Parent estimate-phase name of the matched external expense |
| Confidence | Calculated percentage — for your review |
| Flag | Any issues flagged during extraction |

Literal filename labels such as a trailing `_Invoice` are removed from the
Reference Number. Numeric suffixes that may be part of the vendor's reference,
such as `1144645-4`, are preserved.

The standalone word `invoice` is removed when it is only a label, but a genuine
reference such as `INV_1234ff05t` is preserved. For example,
`AST_2286368_invoice` becomes `2286368`, while `INV_1234ff05t` remains
`INV_1234ff05t`.

Location/reference/date values are separated when they follow the complete
pattern `Location_Reference_Mon_DD_YYYY`; for example,
`Edmonton_105R023453_Jan_25_2026` becomes `105R023453`. Ordinary underscore
references such as `INV_1234ff05t` remain unchanged.

Invoices dated in the current month or immediately preceding calendar month are
accepted normally. Older invoices remain in the result but receive the
`OLD_INVOICE_DATE` flag for Accounts to review.

The invoice itself determines Tax Group: visible `GST`, `GST/HST`, or `GST/TPS`
wording produces `GST`; otherwise the field is blank. If that result contradicts
the supplier's configured tax expectation, the invoice receives `GST_CONFLICT`
and is routed to Manual Review.

> **Note:** The `Confidence` and `Flag` columns are for your review — remove them before importing to FunctionPointe.

---

## Supported Vendors

The script auto-detects and maps these vendors:

| Vendor | FP Code | Notes |
|--------|---------|-------|
| Meta / Facebook | `Fac` | GST = $0 (digital services) |
| Netflix | `Netflix` | GST applicable |
| Dandelion Inc | `DaInc` | Multi-job invoices expected |
| Oilers Entertainment Group | `OiEnGro` | GST applicable |
| Google Ads | `GoAdW` | GST = $0 |
| YouTube | `YT` | GST = $0 |
| TikTok | `Tik` | GST = $0 |
| Twitter / X | `Twi` | GST = $0 |
| Spotify | `Spo` | GST = $0 |
| Rogers Digital Media | `RoDiMed` | GST applicable |
| Bell Media | `BMRGPC` | GST applicable |
| CTV | `CTVC` | GST applicable |
| CTV Edmonton | `CFRN` | Vendor-name exception; matches the CFRN FP expense type |
| Global Television | `GT` | GST applicable |
| Corus | `CSI` | GST applicable |
| Campsite Global | `CaGlInc` | GST applicable |
| AI Digital | `AIDig` | GST applicable |
| Infinite Gravity | `InGrDiMeLt` | GST applicable |
| Cineplex Digital | `CiDiMeInc` | GST applicable |

To add a new vendor, edit the `SUPPLIER_MAP` dictionary near the top of `process_media_receipts.py`.

---

## Multi-Job Invoices (e.g. Dandelion)

Some vendors (especially Dandelion for DV360) send a single invoice covering multiple ZGM jobs. The script detects multiple job codes and routes these to `Manual Enter - Multi-Job/`.

A `MultiJob_Summary_*.csv` is generated in `Output/` showing the detected job codes and total amount for reference when entering the split manually in FunctionPointe.

---

## Confidence Levels

Each processed line gets a confidence percentage based on supplier, date and
amount extraction confidence:

- **100%** — Supplier, date and amount all extracted cleanly
- **67%** — One field used a lower-confidence fallback
- **33% or 0%** — Multiple fields are uncertain

Missing invoice number, date, amount, job, or Function Point mapping is routed
to `Manual Review` rather than the FP import.

---

## Configuration (Script Defaults)

These defaults are near the top of `process_media_receipts.py` and can be adjusted.
Expense Type and Service Group are retrieved from Function Point rather than
these defaults:

```python
FP_DEFAULTS = {
    "Office":       "",         # left blank for Accounts
    "Terms":        "Net 30",
    "Billed":       "",
    "Markup_Pct":   "0",
}
```

---

## Requirements

```bash
pip install pdfplumber openpyxl requests python-dotenv
```

Create a local `.env` file containing the Function Point JWT:

```text
FP_API_KEY=your-token-here
DROPBOX_APP_KEY=your-dropbox-app-key
DROPBOX_APP_SECRET=your-dropbox-app-secret
DROPBOX_REFRESH_TOKEN=your-dropbox-refresh-token
DROPBOX_MEDIA_ROOT=/Automation Testing
APP_TIMEZONE=America/Edmonton
```

`FP_API_KEY` may be an admin-created API key or an older login JWT. The backend
sends admin keys through `X-API-Key`; login-generated JWTs use
`Authorization: Bearer`. Do not include either header name or the word `Bearer`
inside the environment-variable value.

The `.env` file is excluded from Git and must not be placed in Dropbox or
another shared folder.

Python 3.9+

---

## Web API and Lovable

The FastAPI service in `api.py` exposes the existing processor to the separate
Lovable frontend. The production flow is:

```text
User → Lovable → DigitalOcean API → Dropbox + Function Point
```

Lovable lists and uploads invoices, displays preview results, starts processing,
and provides output download buttons. DigitalOcean holds the secrets and runs
all invoice-processing logic. Dropbox remains the source of truth for incoming
invoices, processed originals, review folders, and generated outputs.

The backend endpoints are:

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/api/health` | DigitalOcean health check |
| `GET` | `/api/invoices` | List files in Dropbox `Incoming/` |
| `POST` | `/api/invoices/upload` | Upload PDF invoices to Dropbox `Incoming/` |
| `POST` | `/api/runs/preview` | Safely preview selected invoices |
| `POST` | `/api/runs` | Process all selected invoices as one batch |
| `GET` | `/api/runs/{run_id}` | Poll run status and results |
| `GET` | `/api/runs/{run_id}/files` | List generated Dropbox outputs |
| `GET` | `/api/runs/{run_id}/files/{file_id}/download` | Download an output |

Except for health, requests require `Authorization: Bearer <token>`. For local
testing, the token can match `MEDIA_API_TOKEN`. In production, Lovable should
send the signed-in user's Supabase access token; the backend verifies it with
`SUPABASE_JWT_SECRET`. Dropbox and Function Point secrets remain only in
DigitalOcean.

Request bodies for preview and processing use:

```json
{
  "filenames": ["Reddit - 3948018 CFMWS-3265.pdf"]
}
```

Invoice upload uses `multipart/form-data` with one or more fields named
`files`. The default limits are 20 files per request and 25 MB per file.
Uploads are saved to Dropbox `Incoming/` but are not automatically previewed or
processed. Duplicate filenames, non-PDF files, unsafe paths, and oversized
files are rejected individually. A filename without a recognizable job code is
accepted with a warning so the normal preview/process flow can route it for
review.

Run locally:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn api:app --reload --port 8080
```

Open `http://localhost:8080/api/docs` to exercise the API. Preview is
non-destructive. `POST /api/runs` is a live action and will upload outputs and
move successfully processed Dropbox files.

After a run succeeds, the frontend should call `/files` and display download
buttons for the FP import, processing summary, and any review reports. Downloads
are streamed from the official Dropbox copy; the API server does not retain a
second permanent copy.

### DigitalOcean App Platform

1. Push this project to a **private** GitHub repository.
2. In DigitalOcean, create an App from that repository, or edit
   `.do/app.yaml` and create the app from the spec.
3. Add every variable from `.env.example` in DigitalOcean App Settings.
   Mark API keys, refresh tokens, and JWT secrets as encrypted secrets.
4. Set `CORS_ORIGINS` to the exact Lovable production URL.
5. Deploy, then verify `https://YOUR-APP.ondigitalocean.app/api/health`.
6. Set Lovable's `VITE_API_BASE_URL` to the DigitalOcean app URL. Its API
   client should attach the current Supabase session access token.

App Platform must use one instance for this version because the in-process run
lock and run-status store are local to the instance.

---

## Troubleshooting

**"No files found in Incoming/ folder"** — Make sure PDFs are in `Incoming/`, not a subfolder.

**File appears in Naming Errors/** — Confirm that it is a PDF with a safe,
usable filename. A complete job code is preferred, but trailing client initials
such as `JAY` are accepted when the PDF contains the complete code.

**Supplier shows as UNKNOWN** — Add the vendor keyword to `SUPPLIER_MAP` in the script.

**Function Point returns 401** — The configured bearer token/API key is missing,
expired, or invalid. Replace `FP_API_KEY` in DigitalOcean; never put it in the
frontend or commit it to Git.

**Expense match is ambiguous** — The Function Point job contains more than one
possible expense type for the supplier hint (for example separate radio R1/R2
entries). Review it manually or add a confirmed mapping rule; the processor does
not guess between ambiguous choices.

**Amount shows N/A** — The PDF layout may differ from known patterns; move to Manual Review and add a new extraction pattern.

**Dandelion invoices always go to Multi-Job** — Correct behaviour; Dandelion bills multiple jobs in one invoice. Use the `MultiJob_Summary_*.csv` as a reference when splitting in FP.

---

*ZGM Modern Marketing Partners · Accounting Automation · v1.0*
