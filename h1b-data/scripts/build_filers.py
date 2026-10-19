"""Collapse OFLC disclosure CSVs (one row per application) into one row per employer.

Writes two tables, each with a .manifest.json (checksums, inputs, sources):
  OUT.csv               one row per employer (cleaned name + state + ZIP) with its
                        name, DBA, address, phone, tax ID and NAICS code
  OUT_applications.csv  one row per application with every column of the public
                        disclosure file (job, worksite, wages, point of contact,
                        attorney or agent, preparer, attestations), renamed to
                        lower case, plus its source file and the employer key, so
                        every employer row can be traced back to its exact filings.

Pass the inputs in date order (FY2024 Q4 before FY2025 Q1 ...). A case number
that appears in more than one file (e.g. certified in Q1, withdrawn in Q3) is
kept once, with its latest record, so it is not counted twice. Each application
gets the fiscal year of the file it came from, and each employer row its first
and latest fiscal year. Files are read one at a time, so many years fit in memory.

Usage: python3 build_filers.py OUT.csv IN1.csv [IN2.csv ...] [--source-url URL ...]
"""
import argparse, csv, os, re, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from employer_keys import raw_name, clean_name, zip5, fein
from provenance import write_manifest

KEY = ["name_clean", "state", "zip5"]
# DOL column names that differ between files, or that are not plain identifiers.
RENAME = {"H_1B_DEPENDENT": "H-1B_DEPENDENT", "H1B_DEPENDENT": "H-1B_DEPENDENT",
          "EMPLOYER_POC_ADDRESS_1": "EMPLOYER_POC_ADDRESS1", "EMPLOYER_POC_ADDRESS_2": "EMPLOYER_POC_ADDRESS2",
          "1ST_PWD_CASE_NUMBER": "PWD_CASE_NUMBER_1",
          "2ND_PWD_CASE_NUMBER": "PWD_CASE_NUMBER_2", "3RD_PWD_CASE_NUMBER": "PWD_CASE_NUMBER_3"}
# Columns the build adds, which a disclosure column must not overwrite.
DERIVED = {"source_file", "fein", "fiscal_year", "name_clean", "state", "zip5"}
DEFAULT_SOURCE = "https://www.dol.gov/agencies/eta/foreign-labor/performance"


def applications_path(out):
    root, ext = os.path.splitext(out)
    return f"{root}_applications{ext or '.csv'}"


def fiscal_year(path, decision_dates):
    """The federal fiscal year (October to September) a disclosure file covers, from its name
    (FY2024, FY24); for a file without one, each application's from its decision date."""
    m = re.search(r"FY ?(\d{4}|\d{2})(?!\d)", os.path.basename(path), re.I)
    if m:
        y = int(m.group(1))
        return str(y + 2000 if y < 100 else y)
    d = pd.to_datetime(decision_dates, errors="coerce")
    return (d.dt.year + (d.dt.month >= 10)).astype("Int64").astype("string").fillna("")


def header(path):
    with open(path, newline="") as f:
        return [RENAME.get(c.strip().upper(), c.strip().upper()) for c in next(csv.reader(f))]


def latest_owner(ins):
    """For each case number, the index of the last input that carries it. A case decided in one
    quarter and withdrawn in a later one keeps only its latest record."""
    owner = {}
    for i, p in enumerate(ins):
        cases = pd.read_csv(p, dtype=str, keep_default_na=False,
                            usecols=lambda c: c.strip().upper() == "CASE_NUMBER").iloc[:, 0].str.strip()
        owner.update(dict.fromkeys(cases[cases != ""], i))
    return owner


def load_file(p, i, owner):
    """One disclosure file, with the derived columns, keeping only the case numbers it is the latest record for."""
    df = pd.read_csv(p, dtype=str, keep_default_na=False)
    df.columns = header(p)
    df["CASE_NUMBER"] = df.CASE_NUMBER.str.strip()
    df = df[df.CASE_NUMBER != ""]  # sheets carry many blank padding rows
    df = df.drop_duplicates("CASE_NUMBER", keep="last")
    df = df[df.CASE_NUMBER.map(owner) == i].copy()  # latest decision wins
    df["source_file"] = os.path.basename(p)
    df["fiscal_year"] = fiscal_year(p, df.get("DECISION_DATE", pd.Series("", index=df.index)))
    if "VISA_CLASS" not in df:
        df["VISA_CLASS"] = "H-2B"
    for c in ["TRADE_NAME_DBA", "H-1B_DEPENDENT", "WILLFUL_VIOLATOR", "EMPLOYER_FEIN", "EMPLOYER_ADDRESS1",
              "EMPLOYER_ADDRESS2", "EMPLOYER_CITY", "EMPLOYER_PHONE", "EMPLOYER_PHONE_EXT", "NAICS_CODE"]:
        if c not in df:
            df[c] = ""
    df["name_raw"] = df.EMPLOYER_NAME.map(raw_name)
    df["name_clean"] = df.EMPLOYER_NAME.map(clean_name)
    df["dba_clean"] = df.TRADE_NAME_DBA.map(clean_name)
    df["zip5"] = df.EMPLOYER_POSTAL_CODE.map(zip5)
    df["state"] = df.EMPLOYER_STATE.str.upper().str.strip()
    df["fein"] = df.EMPLOYER_FEIN.map(fein)
    # H-1B: "Certified" or "Certified - Withdrawn"; H-2B: "Determination Issued - (Partial) Certification".
    status = df.CASE_STATUS.str.upper()
    df["certified"] = status.str.startswith("CERTIFIED") | status.str.contains("CERTIFICATION", regex=False)
    df["dependent"] = df["H-1B_DEPENDENT"].str.upper() == "YES"
    df["willful"] = df.WILLFUL_VIOLATOR.str.upper() == "YES"
    return df


# The columns an employer row is built from, kept from every file while the applications are written out.
EMP_COLS = KEY + ["EMPLOYER_NAME", "TRADE_NAME_DBA", "name_raw", "dba_clean", "EMPLOYER_CITY", "EMPLOYER_ADDRESS1",
                  "EMPLOYER_ADDRESS2", "EMPLOYER_POSTAL_CODE", "EMPLOYER_PHONE", "EMPLOYER_PHONE_EXT", "fein",
                  "NAICS_CODE", "VISA_CLASS", "certified", "dependent", "willful", "source_file", "fiscal_year"]


def _joined(s):
    return ";".join(sorted({x for x in s if x}))


def employers(df):
    """One row per cleaned name, state and ZIP. Name, address and phone come from the employer's
    latest fiscal year (its first filing in file order within that year)."""
    df = df.sort_values("fiscal_year", ascending=False, kind="stable")
    g = df.groupby(KEY, dropna=False, sort=True)
    return g.agg(employer_name=("EMPLOYER_NAME", "first"), dba=("TRADE_NAME_DBA", "first"),
                 name_raw=("name_raw", "first"), dba_clean=("dba_clean", "first"),
                 city=("EMPLOYER_CITY", "first"), address=("EMPLOYER_ADDRESS1", "first"),
                 address2=("EMPLOYER_ADDRESS2", "first"), postal_code=("EMPLOYER_POSTAL_CODE", "first"),
                 phone=("EMPLOYER_PHONE", "first"), phone_ext=("EMPLOYER_PHONE_EXT", "first"),
                 fein=("fein", _joined), naics=("NAICS_CODE", "first"), visa_classes=("VISA_CLASS", _joined),
                 n_applications=("certified", "size"), n_certified=("certified", "sum"),
                 h1b_dependent=("dependent", "any"), willful_violator=("willful", "any"),
                 first_fiscal_year=("fiscal_year", "min"), last_fiscal_year=("fiscal_year", "max"),
                 source_files=("source_file", _joined),
                 ).reset_index()


def app_columns(ins):
    """Every disclosure column across the inputs, in first-seen order, then the derived ones."""
    cols = dict.fromkeys(c for p in ins for c in header(p) if c.lower().replace("-", "") not in DERIVED)
    if "VISA_CLASS" not in cols:
        cols["VISA_CLASS"] = None
    return list(cols) + ["fein", "fiscal_year", "source_file"] + KEY


def write_applications(ins, app_out):
    """Streams the inputs one file at a time to the applications table and returns the columns
    the employer rows are built from, so a run over many years does not hold every column in memory."""
    owner = latest_owner(ins)
    cols = app_columns(ins)
    names = [c.lower().replace("-", "") if c == c.upper() else c for c in cols]
    parts = []
    for i, p in enumerate(ins):
        df = load_file(p, i, owner)
        out = df.reindex(columns=cols).fillna("")
        out.columns = names
        out.to_csv(app_out, index=False, mode="w" if i == 0 else "a", header=i == 0)
        parts.append(df[EMP_COLS])
        print(f"  {os.path.basename(p)}: {len(df)} applications")
    return pd.concat(parts, ignore_index=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--source-url", action="append", default=[],
                    help="where the raw disclosure files came from (repeatable)")
    a = ap.parse_args(argv)
    app_out = applications_path(a.out)
    df = write_applications(a.inputs, app_out)
    emp = employers(df)
    emp.to_csv(a.out, index=False)
    sources = [{"url": u} for u in (a.source_url or [DEFAULT_SOURCE])]
    for path in (a.out, app_out):
        write_manifest(path, __file__, inputs=a.inputs, sources=sources)
    print(f"{len(df)} applications -> {len(emp)} employer rows -> {a.out}; "
          f"case numbers -> {app_out}")


if __name__ == "__main__":
    main()
