#!/usr/bin/env python
"""Download the OpenNGC catalog into webapp/data (run once; the files are committed).

OpenNGC by Mattia Verga, https://github.com/mattiaverga/OpenNGC
License: CC-BY-SA-4.0 - the files are shipped unmodified (gzip only).
"""
import gzip
import pathlib
import urllib.request

TAG = "v20260501"
BASE = f"https://raw.githubusercontent.com/mattiaverga/OpenNGC/{TAG}/database_files"
OUT = pathlib.Path(__file__).resolve().parent.parent / "webapp" / "data"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name in ("NGC.csv", "addendum.csv"):
        with urllib.request.urlopen(f"{BASE}/{name}", timeout=60) as r:
            data = r.read()
        target = OUT / f"openngc-{name.lower()}.gz"
        with gzip.open(target, "wb", compresslevel=9) as fh:
            fh.write(data)
        print(f"{target.name}: {len(data):,} bytes")
    (OUT / "OPENNGC.txt").write_text(
        f"OpenNGC {TAG} by Mattia Verga - https://github.com/mattiaverga/OpenNGC\n"
        "Licensed under CC-BY-SA-4.0 (https://creativecommons.org/licenses/by-sa/4.0/).\n"
        "Files: NGC.csv and addendum.csv, unmodified, gzip compressed.\n")


if __name__ == "__main__":
    main()
