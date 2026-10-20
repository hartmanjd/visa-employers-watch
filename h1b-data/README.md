# H-1B / H-2B data check

Scripts and results from the FY2025 feasibility check (2026-10-15): can DOL Wage and Hour (WHD) violation cases be linked to H-1B and H-2B employers?

The employer and application tables (`filers_*`), the database (`research.db`) and all raw downloads are not in git; rebuild them with the scripts below. Needs Python 3 with pandas and openpyxl.

| File | What it is |
| --- | --- |
| filers_lca.csv (not in git) | One row per H-1B (LCA) employer: cleaned name, state, ZIP, address, phone, tax ID, counts, first and latest fiscal year, H-1B-dependent and willful-violator flags |
| filers_h2b.csv (not in git) | Same for H-2B employers |
| filers_*_applications.csv (not in git) | One row per application with every column of the DOL disclosure file (job, worksite, wages, point of contact, attorney or agent, preparer, attestations), in lower case, plus the source file and the employer key that links each employer row to its filings |
| name_match_benchmark.csv | How well name rules re-find the 144 employers in both programs, using tax ID as the answer key |
| scripts/employer_keys.py | Name cleaning and ZIP/tax ID helpers |
| scripts/provenance.py | Writes and checks the `.manifest.json` beside each data file |
| scripts/xlsx_to_csv.py | Streams a DOL disclosure workbook to CSV, skipping blank padding rows |
| scripts/build_filers.py | Turns the disclosure CSVs into the employer and application tables; a case number seen in several quarters is kept once, with its latest record. Column names that differ between files (`H-1B_DEPENDENT` and `H_1B_DEPENDENT`) are read as one |
| scripts/name_match_benchmark.py | Produces name_match_benchmark.csv |
| scripts/fetch_whd.py | Downloads every column of the WHD enforcement table (key from env var DOL_API_KEY, sent in the URL). 1,000 rows a page, backs off on rate limits, re-run with the same output file to resume |
| scripts/match_whd.py | Match rates of WHD H-1B/H-2B violation cases to filers |
| scripts/fetch_osha.py | Downloads OSHA inspection records through the same DOL API: first a lean index of every inspection (name, site and mailing ZIP and state, open date; one request a month), then, for the inspections that match a visa filer, every column of the inspection, its citation items, general duty clause text, accident reports with their written accounts and injured workers, and OSHA's injury code list. `merge` combines two details folders, such as an earlier download and one for employers added since |
| scripts/match_osha.py | Lists the inspections in the index that match a visa filer, with build_db.py's rules, for the detail download |
| scripts/build_db.py | Builds `research.db` (SQLite) from the tables above; see Database |
| research.db (not in git) | The database |
| debarments.csv | DOL's published debarment lists in one table (H-1B, H-2A, H-2B and permanent labor certification): entity, type, location, period, reason, CFR citation, source URL and file. Kept in git because it is small and is read by the website directly |
| scripts/parse_debarments.py | Builds debarments.csv from the three DOL files; see Debarments |
| whd_match_rates.csv | Output of match_whd.py (run 2026-10-15 on 370,964 WHD cases) |
| tests/ | Tests for manifests, the employer build, the WHD download and the OSHA tables (no network needed) |

## Provenance

Every data file a script writes gets a manifest next to it: `X.csv` → `X.csv.manifest.json`. It records:

- the file's SHA-256 checksum, size and row count
- when it was made, by which script, and that script's checksum
- the input files it was built from, each with its own checksum and row count
- where the data came from (source URLs) and the settings used (the API key is never recorded)

A checksum is a fingerprint of the file: change one character and it changes. `provenance.verify_manifest("X.csv")` raises an error if a file no longer matches its manifest. WHD rows also keep `load_dt`, the date DOL loaded each case, and the application tables keep every DOL case number.

## Database

`build_db.py` loads everything into one SQLite file. Each data row has a `source_id` pointing to the `sources` row of the file it came from, and the build refuses any input that no longer matches its manifest.

| Table | One row per | Key columns |
| --- | --- | --- |
| sources | input file | file, sha256, rows, urls, created_utc, manifest |
| employers | employer per program (H-1B or H-2B) | employer_id, program, name_clean, state, zip5, address, phone, fein, n_applications, n_certified, first_fiscal_year, last_fiscal_year, h1b_dependent, willful_violator (1 when any application answers Yes to the willful violator question; the dashboard flags an employer only when most do) |
| applications | visa application | case_number, employer_id, fiscal_year, case_status, visa_class, then every disclosure column (179 in all; H-2B-only columns are empty on H-1B rows and the other way round) |
| employer_years | employer and fiscal year | employer_id, fiscal_year, applications, certified (for the employer page's chart and the watchlist's "Filed in" filter) |
| violation_cases | WHD enforcement case | every column WHD publishes (110): case_id, names, address, industry, dates, totals, then violations, back wages, workers owed and penalties under each law (minimum wage and overtime, child labor and minors employed, FMLA, federal contracts, farmworker protections, H-1B, H-2A, H-2B and others), the FLSA repeat/willful flag, load_dt |
| matches | WHD case linked to an employer | case_id, employer_id, method, est_false_match_rate, whd_name_field, employer_name_field |
| osha_inspections | OSHA inspection linked to a visa filer (opened 2004 or later by default) | every column OSHA publishes: activity_nr, establishment name, site and mailing address, NAICS, inspection type, scope, owner, union status, employees, open and close dates, load_dt |
| osha_violations | OSHA citation item | activity_nr, citation_id, standard, viol_type (Serious, Willful, Repeat, Other), gravity, workers exposed, instances, initial and current penalty, abatement, contest and final order dates, delete_flag (X for deleted items, which are kept but left out of totals), load_dt |
| osha_gen_duty_text | line of a general duty clause citation | activity_nr, citation_id, line_nr, line_text (the hazard in OSHA's words) |
| osha_accidents, osha_accident_text | accident tied to a linked inspection, and each line of its written account | summary_nr, event_date, event_desc, keywords, fatality; summary_nr, line_nr, abstract_text |
| osha_injuries | injured worker in such an accident | summary_nr, rel_insp_nr (the inspection), age, sex, degree_of_inj (1 killed, 2 hospitalized), nature, body part, occupation codes |
| osha_codes | OSHA injury code | accident_code (code table), accident_number, accident_value |
| osha_matches | OSHA inspection linked to an employer | activity_nr, employer_id, method, est_false_match_rate, osha_name_field, osha_address_field, employer_name_field |
| osha_employer_totals | employer with linked inspections | inspections, citation items, Serious, Willful and Repeat items, initial and current penalties, accidents, deaths, first and latest inspection (stored so the website does not add them up) |
| flag_baselines | red flag per program | flag, program, share (the share of all the program's applications that meet the flag's condition; the employer page compares each employer with it) |
| rep_filings | employer, law firm and attorney or agent combination | employer_id, firm, firm_fein, first_name, last_name, state, applications (the law firms and agents pages are built from it) |

The applications table keeps the public disclosure record as DOL published it, including the employer's point of contact,
the attorney or agent and law firm, and the preparer. Wages, prevailing wages, hours and worker counts are stored as
numbers; everything else (ZIPs, phone numbers, codes) is text. `n_certified` counts H-1B "Certified" and "Certified -
Withdrawn" and H-2B "Determination Issued - Certification" or "Partial Certification".

WHD cases have no tax ID, so `matches` links them by cleaned name + ZIP, or failing that cleaned name + state, using the WHD trade or legal name against the employer's name or DBA. `est_false_match_rate` is that rule's false-match rate on the tax-ID benchmark (`name_match_benchmark.csv`).

OSHA inspections are linked the same way: cleaned name + ZIP, else cleaned name + state, trying the inspected site and the mailing address (a jobsite can be far from the company's office), and both the establishment name and any name after "DBA" in it. The error rates are the WHD rules' rates, since the benchmark has no OSHA answer key.

`flag_baselines` and `rep_filings` are summaries of the applications table that take the website several seconds of CPU to compute, so the build stores them (`add_summaries` in `dashboard/queries.py`). The website still works on a database without them, computing the same values on first use.

## Debarments

`debarments.csv` combines three DOL sources:

- OFLC's Program Debarments PDF (PERM, H-2A, H-2B): https://www.dol.gov/agencies/eta/foreign-labor/program-debarments
- WHD's current H-1B list, the table on https://www.dol.gov/agencies/whd/immigration/h1b/debarment
- WHD's older H-1B workbook, `H1BDebarmentList.xls`, linked from that page

dol.gov refuses scripted downloads, so save the three files with a browser: open the PDF and use Save, save the
H-1B page with **Save Page As > Webpage, HTML only**, and click the workbook's Download link. Then run:

    pip install pdfplumber xlrd
    python3 h1b-data/scripts/parse_debarments.py Debarment_List.pdf debarment.html H1BDebarmentList.xls

The website matches each entry to employers by cleaned name (the whole name, or any part around "d/b/a" or
"aka") and by state when DOL gives a location. DOL's H-1B list has no addresses, so those entries match on
name alone.

## Rebuilding

To rebuild the employer tables, download the LCA and H-2B disclosure files for each fiscal year from
https://www.dol.gov/agencies/eta/foreign-labor/performance (the website uses FY2020 to the latest: LCA in quarterly
files, H-2B in one file a year; DOL's file for the current year is cumulative, so only its latest quarter is
needed). Convert each to CSV with `python3 scripts/xlsx_to_csv.py IN.xlsx OUT.csv --source-url URL`, then pass
the CSVs in date order (oldest year first, quarters in order), with each workbook's URL as a `--source-url` so the
website can link every application to its file. Each application gets the fiscal year in its file's name, and a
case number in several files keeps only its latest record. The files are read one at a time, so seven years of
LCA files (about 4 GB of CSV) build in under 16 GB of memory:

    python3 scripts/build_filers.py filers_lca.csv LCA_Disclosure_Data_FY2020_Q1.csv ... LCA_Disclosure_Data_FY2026_Q3.csv \
        --source-url https://www.dol.gov/sites/dolgov/files/ETA/oflc/pdfs/LCA_Disclosure_Data_FY2020_Q1.xlsx ...
    python3 scripts/build_filers.py filers_h2b.csv H2B_Disclosure_Data_FY2020.csv ... --source-url ...

To re-run the WHD match (raw download about 180 MB and 45 to 80 minutes, depending on DOL's rate limits; key in env var DOL_API_KEY):

    python3 scripts/fetch_whd.py /tmp/whd.csv
    python3 scripts/match_whd.py /tmp/whd.csv filers_lca.csv filers_h2b.csv .

To download the OSHA inspections linked to visa filers (key in env var DOL_API_KEY; each step backs off on rate limits, and the index step resumes where it stopped):

    python3 scripts/fetch_osha.py index /tmp/osha_index.csv 2004-01-01
    python3 scripts/match_osha.py /tmp/osha_index.csv filers_lca.csv filers_h2b.csv /tmp/osha_ids.txt
    python3 scripts/fetch_osha.py details /tmp/osha_ids.txt /tmp/osha

To build the database (leave out `--osha` for a database without OSHA data):

    python3 scripts/build_db.py research.db --lca filers_lca.csv --h2b filers_h2b.csv \
        --whd /tmp/whd.csv --benchmark name_match_benchmark.csv --osha /tmp/osha

To run the tests (from the repo root):

    python3 -m unittest discover h1b-data/tests
