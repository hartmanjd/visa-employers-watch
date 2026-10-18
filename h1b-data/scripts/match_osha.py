"""List the OSHA inspections that match a visa filer, so only their details are downloaded.

Uses the same rules as build_db.py (cleaned name + ZIP, else cleaned name + state; the
inspection's site or mailing address; the establishment name or the part after "DBA"
against the employer name or DBA).

Writes IDS.txt (one activity number per line) and its .manifest.json (input checksums).

Usage: python3 match_osha.py INDEX.csv FILERS_LCA.csv FILERS_H2B.csv IDS.txt
"""
import os, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_db import employer_keys, match_osha, read
from provenance import write_manifest


def main(index_p, lca_p, h2b_p, out):
    insp = read(index_p)
    emp = pd.concat([read(lca_p), read(h2b_p)], ignore_index=True)
    emp["employer_id"] = range(1, len(emp) + 1)
    m = match_osha(insp, emp, {})
    ids = sorted(set(m.activity_nr.astype(int)))
    with open(out, "w") as f:
        f.write("".join(f"{i}\n" for i in ids))
    write_manifest(out, __file__, inputs=[index_p, lca_p, h2b_p])
    print(f"{len(ids)} of {len(insp)} inspections match a visa filer "
          f"({m.groupby('method').activity_nr.nunique().to_dict()})")


if __name__ == "__main__":
    main(*sys.argv[1:])
