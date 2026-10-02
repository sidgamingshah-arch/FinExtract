#!/usr/bin/env bash
# Build an OFFLINE bundle: every Python package Docling needs, plus its models, in one archive an
# air-gapped server installs from with no network access at all.
#
#   ./scripts/build_offline_bundle.sh            # on a machine WITH network access, same OS/Python
#                                                # as the target server
#   → dist/finex-docling-offline.tar.gz
#
# On the offline server, from the backend folder:
#   tar xzf finex-docling-offline.tar.gz
#   pip install --no-index --find-links offline/wheelhouse -e ".[pdf,docling]"
#   mkdir -p models && cp -r offline/models/docling models/docling
#
# PyTorch comes from the CPU-only index, which is a fraction of the size of the default wheel and
# all this app needs. Build on the target's OS and Python version: wheels are platform-specific.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT=offline
rm -rf "$OUT" && mkdir -p "$OUT/wheelhouse" "$OUT/models" dist

python -m pip download -d "$OUT/wheelhouse" --index-url https://download.pytorch.org/whl/cpu \
  torch torchvision
python -m pip download -d "$OUT/wheelhouse" --extra-index-url https://download.pytorch.org/whl/cpu \
  ".[pdf,docling]"

# Models: install Docling into a throwaway environment just long enough to fetch them.
python -m venv "$OUT/.fetch-venv"
"$OUT/.fetch-venv/bin/pip" install -q --no-index --find-links "$OUT/wheelhouse" -e ".[docling]"
"$OUT/.fetch-venv/bin/python" scripts/fetch_docling_models.py --dir "$OUT/models/docling"
rm -rf "$OUT/.fetch-venv"

tar czf dist/finex-docling-offline.tar.gz "$OUT"
echo "Built dist/finex-docling-offline.tar.gz ($(du -h dist/finex-docling-offline.tar.gz | cut -f1))"
