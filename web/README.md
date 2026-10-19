# Visa Employer Watch (website)

A designed website over `h1b-data/research.db`: a small JSON API (`api.py`, built with FastAPI) and a
hand-written front end (`static/`) with no build step. It has Employer lookup, an employer page for each
employer, a page for each visa application, the Watchlist, a state Map and an Industries view. The database is
opened read-only and is downloaded on first start if it is missing (see Run it).

## Run it

From the repo root:

1. Make a virtual environment (a private set of Python libraries for this project) and install the requirements:

        python3 -m venv .venv
        source .venv/bin/activate
        pip install -r requirements.txt

   In a new terminal, run `source .venv/bin/activate` again before starting the site.

2. Start the server:

        uvicorn web.api:app --reload

   Then open http://localhost:8000 in a browser. `--reload` restarts the server when a file changes.

The site reads `h1b-data/research.db`. If that file is missing, it downloads a gzipped copy (about 219 MB) from
the repo's `data-2026-10-18` release on first start, checks its SHA-256 and unpacks it there. To use your own
build instead, run `h1b-data/scripts/build_db.py` (see `h1b-data/README.md`) or copy a `research.db` into
`h1b-data/` before starting.

Settings (environment variables, all optional):

| Variable | What it does |
| --- | --- |
| RESEARCH_DB | Path of the database to use instead of `h1b-data/research.db` |
| RESEARCH_DB_URL | Where to download the gzipped database from instead of the release |
| RESEARCH_DB_SHA256 | Checksum of the file at RESEARCH_DB_URL |

## Pages

| Address | Page |
| --- | --- |
| `#/` | Employer lookup: search by name, trade name (DBA) or tax ID, or open an application by its DOL case number. Before a search it shows a small map of cases per 100 visa employers, headline totals and three starting points: the employers with the most back wages, employers whose WHD cases found violations of their own visa program and that still filed in FY2025, and the industries with the highest share of employers with a case |
| `#/employer/<id>` | One employer: DOL flags, totals, red flags in its own applications (each with its counts and the same share across the program), linked cases by year, same tax ID elsewhere, its jobs, worksites, offered against prevailing wages and representatives, each linked WHD case with its match rule and source, its linked OSHA inspections (each citation with the standard broken, its type, workers exposed and the proposed and current penalty, accident reports with OSHA's written account, the match rule and a link to OSHA's page), and its visa applications |
| `#/application/<case number>` | One application: headline job, workers, wages and dates, then every field of its DOL disclosure record by section |
| `#/watchlist` | Every employer with linked WHD cases, sortable, with filters (program, state, industry, year, visa violations, applications) kept in the address |
| `#/safety` | Every employer with linked OSHA inspections, sortable by penalties, citations, Serious, Willful or Repeat items and deaths, with its WHD back wages beside it; filters (program, state, industry, only employers with WHD cases too) kept in the address |
| `#/map` | A tile map of states colored by cases per 100 visa employers, share with a case, back wages or case count, with top-state bars and a table |
| `#/representatives` | Law firms, agent businesses and individual attorneys or agents, ranked by how many of their client employers have visa-program violations, with the rate against typical for the same mix of programs |
| `#/representative/<kind>/<key>` | One firm or person: totals, spellings, tax IDs and every client employer with its WHD record. Employer pages link here from a "Who else their representatives file for" panel |
| `#/debarments` | DOL's published debarment lists (H-1B, H-2A, H-2B, permanent labor certification), each entry with its period, status and reason, and the FY2025 visa filers that match it by name (and state when DOL gives one) |
| `#/industries` | NAICS sectors: a size-against-rate bubble chart, bars for share with a case and back wages, and a table |

On the Map and Industries pages, hovering a state or industry shows its numbers, and clicking it opens
the Watchlist filtered to it.

The footer on every page has a "Use this data with AI (MCP)" section with the MCP server's address and the
steps for adding it to Claude. `static/llms.txt`, served at `/llms.txt`, is a plain-text guide for AI tools
that lists the MCP address, its tools, what the data means and which pages to cite.

## API

| Endpoint | Returns |
| --- | --- |
| `GET /api/search?q=` | Up to 50 employers matching a name or tax ID; for a DOL case number, the employer that filed it, with the case number |
| `GET /api/employers/{id}` | The employer, its totals, other rows with the same tax ID, its linked WHD cases by year and in full, and a profile of its applications (jobs, worksites, law firms, attorneys, offered against prevailing wage) |
| `GET /api/employers/{id}/applications?page=` | Its visa applications, newest decision first, 100 per page, with job, worksite, wages, law firm and a link to each DOL file |
| `GET /api/applications/{case_number}` | One application: headline numbers and every disclosure field, grouped into sections |
| `GET /api/watchlist?program=&state=&sector=&since=&visa_only=&min_apps=` | Watchlist rows plus totals that count each case once |
| `GET /api/osha?program=&state=&sector=&with_whd=&min_apps=` | Employers with linked OSHA inspections and their OSHA totals and WHD back wages, plus OSHA totals that count each inspection once. The employer endpoint also returns `osha`: its OSHA totals and each linked inspection with its citations and accidents |
| `GET /api/states?program=&visa_only=` | Per state: visa employers, applications, employers with a case, cases, back wages, penalties, visa violations and rates |
| `GET /api/sectors?program=&visa_only=` | The same numbers per NAICS sector |
| `GET /api/overview` | Headline totals and the lookup page's starting points (computed once per server start) |
| `GET /api/debarments` | Every DOL debarment entry with its status and matching visa employers. The employer endpoint also returns the entries matching that employer, with its applications decided inside each debarment period |
| `GET /api/representatives?kind=&program=&q=&sort=&min_clients=` | Law firms and agent businesses (`kind=firm`) or attorneys and agents (`kind=person`) across all the employers they file for: clients, clients with WHD cases, clients with visa-program violations against the rate for the same mix of programs, debarred clients and back wages |
| `GET /api/representatives/{kind}/{key}` | One representative: name variants, tax IDs, firms on the form (for a person) and every client employer with its WHD record. The employer endpoint also returns `representatives`, the firms and people on its applications |
| `GET /api/watchlist.csv?…` | The Watchlist as CSV with the same filters, each employer with the WHD case IDs behind its row and a link to its page |
| `GET /api/employers/{id}/applications.csv` | All of an employer's applications as CSV: DOL case number, job, wages, worksite, law firm, source file and a link to each application page |
| `GET /api/employers/{id}/cases.csv` | An employer's linked WHD cases as CSV, with WHD's field names, load date, match rule and error rate, and source file |
| `GET /api/employers/{id}/osha.csv` | An employer's linked OSHA inspections as CSV, one row per citation item, with OSHA's activity number and field names, the standard as a citation, load date, match rule and a link to OSHA's page |
| `GET /api/debarments.csv` | Every DOL debarment entry as CSV, with the IDs of matching employers |
| `GET /api/meta` | States, NAICS sector names and the source files behind the database |
| `POST /mcp` | The chat's ten lookups as a read-only MCP server (see `chat/README.md` for connecting Claude Desktop) |

FastAPI also serves interactive API docs at http://localhost:8000/docs.

## Hosting

`render.yaml` at the repo root describes the site for [Render](https://render.com). In Render, choose
**New > Blueprint**, pick this repository, and confirm. The free plan sleeps after about 15 minutes
without visitors, so the first visit after that takes 30 to 60 seconds while it wakes and downloads the
database.

The site can also run as a [Hugging Face Space](https://huggingface.co/spaces), whose free CPU tier
has 2 CPUs and 16 GB of memory and sleeps after 48 hours without visitors. The `Dockerfile` at the repo
root describes the container; it downloads the database while building, so the site starts in seconds.
`.github/workflows/hf-space.yml` copies the repo to the Space on every push to `main`, and the Space
rebuilds. To set it up:

1. On huggingface.co, create a Space with the **Docker** SDK (Blank template) and the free
   **CPU basic** hardware.
2. Under **Settings > Access Tokens**, create a token with write access.
3. In this GitHub repo, open **Settings > Secrets and variables > Actions**. Add the token as the secret
   `HF_TOKEN`, and on the **Variables** tab add `HF_SPACE` set to the Space's name, such as
   `hartmanjd/visa-employer-watch`.
4. Run **Actions > Deploy to Hugging Face Space > Run workflow** once, or merge to `main`.

Links in the site, `llms.txt`, CSV downloads and the MCP server use the Space's own address, which Hugging
Face passes in as `SPACE_HOST`; set `SITE_URL` to override it.

## Files

| File | What it is |
| --- | --- |
| api.py | The API and static file server; reuses the lookups in `dashboard/queries.py` and serves the MCP server in `chat/` |
| static/index.html | Page shell: top bar, navigation, fonts |
| static/styles.css | Design: colors (light and dark), type, cards, tables, filters |
| static/app.js | Routing, the seven views and their charts, fetching from the API |
| tests/test_api.py | API tests on a small made-up database |

Run the tests from the repo root with `python3 -m unittest discover web/tests`.
