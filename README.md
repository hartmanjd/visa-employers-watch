# Visa Employer Watch

A searchable dataset of the U.S. employers that file H-1B and H-2B visa applications, side by side with the
Department of Labor's wage and safety enforcement records against them. It is built for investigators,
journalists and researchers who want to know whether a company that brings in visa workers has a history of
unpaid wages, visa-program violations, debarment or serious safety violations. Every number links back to
the DOL record it came from.

**Live site:** https://catgat-h-program.hf.space · **MCP server for AI apps:** https://catgat-h-program.hf.space/mcp

## What's in it

| | |
| --- | --- |
| Employers | 230,024 employer rows (one company name at one address in one program) |
| Visa applications | 3,856,342 H-1B (LCA) and H-2B filings, fiscal years 2020 to 2026, every published field |
| Wage and Hour cases | 370,964 WHD enforcement cases (about 2004 to now); 13,813 employers have at least one linked case |
| OSHA inspections | 90,627 inspections since 2004 linked to visa employers, with citations, penalties and accident reports |
| Debarments | DOL's published debarment lists for H-1B, H-2A, H-2B and permanent labor certification |

## Data sources

All data is public and comes from the U.S. Department of Labor.

- **H-1B and H-2B disclosure data** from the Office of Foreign Labor Certification (OFLC):
  quarterly LCA and H-2B disclosure files, FY2020 to FY2026 Q3,
  from https://www.dol.gov/agencies/eta/foreign-labor/performance. Each application keeps its DOL case
  number, and the latest record wins when a case appears in several quarters.
- **Wage and Hour Division (WHD) enforcement cases** from DOL's API
  (`apiprod.dol.gov/v4/get/WHD/enforcement`): every law WHD enforces, including minimum wage, overtime,
  child labor and the H-1B, H-2A and H-2B programs, with back wages, penalties and workers owed.
- **OSHA inspections** from the same API: inspections, citation items, general duty clause text,
  accident reports and injured workers.
- **Debarment lists:** OFLC's
  [Program Debarments](https://www.dol.gov/agencies/eta/foreign-labor/program-debarments) and WHD's
  [H-1B debarment list](https://www.dol.gov/agencies/whd/immigration/h1b/debarment).

WHD and OSHA records carry no tax ID, so they are linked to visa employers by cleaned company name plus ZIP
code, or name plus state. A test against tax IDs found 3 to 4 percent of links wrong and about a quarter of
true matches missed, so every linked record shows the rule that linked it. Every data file has a manifest
with its checksum, source URLs and download date; see [h1b-data/README.md](h1b-data/README.md).

## Features

- **Employer lookup:** search by company name, trade name (DBA) or tax ID, or open an application by its
  DOL case number. The home page shows headline totals, applications by fiscal year, a map, and starting
  points such as the employers with the most back wages.
- **Employer pages:** DOL flags, red flags in the employer's own applications (wages at the floor, mostly
  entry-level wage levels, many withdrawn or denied filings), filings by year, jobs, worksites, wages
  offered against prevailing wages, the law firms and agents who file for it, every linked WHD case and
  OSHA inspection, and other employer rows with the same tax ID.
- **Application pages:** every field of the DOL disclosure record for one filing.
- **Watchlist:** every employer with linked WHD cases, sortable, filtered by program, state, industry, year
  the cases ended, fiscal year filed and visa-program violations. Totals count each case once.
- **Safety:** every employer with linked OSHA inspections, ranked by penalties, with citations, Willful and
  Repeat items, deaths and WHD back wages beside them.
- **Map and Industries:** cases per 100 visa employers, back wages and case counts by state and by NAICS
  sector, each linking to a filtered Watchlist.
- **Law firms and agents:** the representatives named on applications, ranked by how many of their clients
  have visa-program violations against what is typical for the same mix of programs.
- **Debarments:** DOL's debarment lists, matched to visa filers by name.
- **CSV downloads** of the Watchlist and of any employer's applications, WHD cases and OSHA citations.
- **Use it from an AI app (MCP):** the site serves its lookups as a read-only MCP server, so Claude or
  another AI app can answer questions from the data and link each record. No sign-in or API key is
  needed; see [chat/README.md](chat/README.md#use-it-from-claude-desktop-mcp). `/llms.txt` describes the
  data for AI tools, and `/docs` documents the JSON API.

## Reading the data carefully

- A linked WHD case or OSHA inspection is a name match, not a confirmed identity; check the record.
- A WHD case with 0 violations and $0 back wages means WHD investigated and found nothing.
- "Willful violator" on an H-1B application is the employer's own answer, not a DOL finding.
- Red flags are reasons for a closer look, not evidence of a violation.
- Representing an employer does not make a law firm responsible for that employer's violations.

## Repository layout

| Folder | What it is |
| --- | --- |
| [h1b-data/](h1b-data/README.md) | Download, cleaning, matching and database build scripts, with provenance manifests |
| [dashboard/](dashboard/README.md) | The database lookups, shared by the website and the MCP server |
| [web/](web/README.md) | The website: a FastAPI JSON API and a hand-written front end, plus hosting notes |
| [chat/](chat/README.md) | The MCP server for AI apps, and a command-line question answerer that uses the same lookups |

## Run it locally

With Python 3:

    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn web.api:app

Then open http://localhost:8000. On first start the site downloads the database (about 1.1 GB, 4.6 GB
unpacked) from the repo's GitHub release. See [web/README.md](web/README.md) for details and
[h1b-data/README.md](h1b-data/README.md) to rebuild the database from DOL's files.

Run the tests with `python3 -m unittest discover <folder>/tests` for `h1b-data`, `dashboard`, `web` and
`chat`.
