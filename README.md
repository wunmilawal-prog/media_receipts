# ZGM Media Receipt Processing System

Automates extraction and FunctionPointe import preparation for ZGM media invoices.

---

## Quick Start

1. Drop PDF invoices into **`/Media Receipts/Incoming`** in Dropbox
2. Run the script:
   ```
   python3 process_media_receipts.py
   ```
3. Upload **`/Media Receipts/Output/FP_Import_*.csv`** into FunctionPointe
4. Handle anything in the Dropbox **`Manual Enter - Multi-Job/`** or
   **`Manual Review/`** folders

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

Invoices **must** follow this naming pattern:

```
[Supplier] [InvoiceNumber] - [JobCode].pdf
```

**Examples:**
```
Meta 3U55W7VD72 - DCC-3074.pdf
Netflix CINV-5414568 - EE 3270.pdf
Oilers Entertainment Group 19584-24878 - DE 3237.pdf
Dandelion INV-13174 - Dec-25 - breakdown Susila.pdf
```

- **Supplier** — matches a known vendor (Meta, Netflix, Dandelion, etc.)
- **InvoiceNumber** — the vendor's invoice/reference number
- **JobCode** — the ZGM job code (e.g. `DCC-3074`, `EE-3270`). Spaces or dashes both work.

Files with incorrect naming are moved to `Naming Errors/` and logged.

---

## Dropbox Folder Structure

```
Media Receipts/
├── Incoming/                   ← Drop new invoices here
├── Processed/                  ← Successfully processed (FP import generated)
├── Output/                     ← Generated CSVs and summary reports
│   ├── FP_Import_*.csv         ← Upload directly to FunctionPointe
│   ├── MultiJob_Summary_*.csv  ← Reference for manual multi-job entry
│   ├── ManualReview_*.csv      ← Items needing human verification
│   └── Processing_Summary_*.xlsx ← Full run report with colour coding
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
2. **Validates** filename against naming convention — bad names go to `Naming Errors/`
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
8. **Uploads** output files to Dropbox `Output/`
9. **Moves** each original Dropbox invoice to its routed folder only after
   generated outputs upload successfully

---

## FP Import CSV Columns

The generated CSV matches the FunctionPointe External Expense template:

| Column | Source |
|--------|--------|
| Reference Number | Invoice number from filename/PDF |
| *Supplier | Detected Function Point supplier name |
| Expense Date | Date from PDF |
| Payable Account | Blank (fill in FP or set default in script) |
| Office | `CGY` (configurable in script) |
| Description | Auto-generated summary |
| Terms | `Net 30` (configurable) |
| *Job | Job code from filename |
| *Expense Type | Matching external-expense name from the Function Point job |
| Quantity | `1` |
| Rate | Subtotal amount from PDF |
| Billed | `Yes` |
| Markup% | `0` |
| Tax Group | `GST` for Canadian vendors, blank for US digital |
| Service Group | Parent estimate-phase name of the matched external expense |
| Confidence | Calculated percentage — for your review |
| Flag | Any issues flagged during extraction |

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
    "Office":       "CGY",      # Calgary office
    "Terms":        "Net 30",
    "Billed":       "Yes",
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
DROPBOX_MEDIA_ROOT=/Media Receipts
```

The `.env` file is excluded from Git and must not be placed in Dropbox or
another shared folder.

Python 3.9+

---

## Web API and Lovable

The FastAPI service in `api.py` exposes the existing processor to the separate
Lovable frontend:

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

**File appears in Naming Errors/** — Rename it to match: `Supplier InvoiceNum - JobCode.pdf`

**Supplier shows as UNKNOWN** — Add the vendor keyword to `SUPPLIER_MAP` in the script.

**Amount shows N/A** — The PDF layout may differ from known patterns; move to Manual Review and add a new extraction pattern.

**Dandelion invoices always go to Multi-Job** — Correct behaviour; Dandelion bills multiple jobs in one invoice. Use the `MultiJob_Summary_*.csv` as a reference when splitting in FP.

---

*ZGM Modern Marketing Partners · Accounting Automation · v1.0*
