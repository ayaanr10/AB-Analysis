#!/usr/bin/env bash
# Fetch the datasets. Datasets are NEVER committed (SPEC.md §11).
#
#   bash data/download.sh            # the two analysed datasets
#   bash data/download.sh --all      # + the three rejected candidates, to reproduce
#                                    #   docs/dataset_selection.md
#
# Checksums for everything fetched here are in data/checksums.sha256. Where a dataset's
# official home requires authentication or has rotted, a public mirror is used and the
# checksum is what lets a reader confirm they have the same bytes this analysis ran on.
set -euo pipefail
cd "$(dirname "$0")"

fetch () { # fetch <url> <dest> <description>
  if [[ -f "$2" ]]; then echo "  have  $2"; return; fi
  echo "  get   $2  ($3)"
  curl -fSL --progress-bar --max-time 1800 -o "$2.part" "$1"
  mv "$2.part" "$2"
}

echo "Case Study 1 — Cookie Cats"
# Origin: DataCamp project 184 / Kaggle "Mobile Games A/B Testing with Cookie Cats".
# Both require an account; the mirror below is a public byte-identical copy.
fetch "https://raw.githubusercontent.com/ryanschaub/Mobile-Games-A-B-Testing-with-Cookie-Cats/master/cookie_cats.csv" \
      "cookie_cats.csv" "90,189 players, 2.6 MB"

echo "Case Study 2 — Criteo Uplift"
# Diemert, Betlei, Renaudin & Amini (AdKDD 2018). CC BY-NC-SA 4.0.
# NOTE: the URL on Criteo's own dataset page (go.criteo.net) 404s as of July 2026.
# This is the Criteo organisation's mirror on Hugging Face.
fetch "https://huggingface.co/datasets/criteo/criteo-uplift/resolve/main/criteo-research-uplift-v2.1.csv.gz" \
      "criteo-research-uplift-v2.1.csv.gz" "13,979,592 rows, 297 MB gzipped"

if [[ "${1:-}" == "--all" ]]; then
  echo "Rejected candidates — needed only to reproduce docs/dataset_selection.md"

  # ASOS Digital Experiments Dataset (Liu et al., NeurIPS 2021 D&B). osf.io/64jsb
  fetch "https://osf.io/62t7f/download" "asos_experiments.parquet" "24,153 aggregate rows"

  # Upworthy Research Archive (Matias et al., Scientific Data 2021). osf.io/jd64p
  fetch "https://osf.io/download/vy8mj/" "upworthy_exploratory.csv" "105,551 packages, 63 MB"

  # Kaggle "Marketing A/B testing" (faviovaz). Mirror of the Kaggle-hosted CSV.
  fetch "https://raw.githubusercontent.com/sumitdeole/A-B-testing-in-digital-marketing/main/marketing_AB.csv.zip" \
        "marketing_AB.csv.zip" "588,101 rows"
  [[ -f marketing_AB.csv ]] || unzip -o -q marketing_AB.csv.zip
fi

echo
echo "verifying checksums"
shasum -a 256 -c --ignore-missing checksums.sha256
