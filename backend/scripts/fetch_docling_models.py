"""Download Docling's models ONCE, into the folder the app reads them from offline.

Run on a machine with network access (or while building the image):

    python scripts/fetch_docling_models.py              # into [ocr] docling_models_dir
    python scripts/fetch_docling_models.py --dir /opt/finex/models/docling

After this the app never downloads anything: `adapters.docling_ocr` hands this folder to Docling as
its `artifacts_path` (fully-offline operation) and switches the Hugging Face client offline. Copy the
folder to an offline server unchanged, or ship it in the bundle `build_offline_bundle.sh` makes.

Only what reading a scanned statement needs is fetched — the layout model, the table-structure
model and RapidOCR's models — not the formula, picture or vision-language models Docling can also
use, which this app never calls.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv: list[str] | None = None) -> int:
    from app.adapters.docling_ocr import models_dir

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", help="target folder (default: [ocr] docling_models_dir)")
    parser.add_argument("--force", action="store_true", help="download again even if present")
    args = parser.parse_args(argv)

    target = Path(args.dir).resolve() if args.dir else models_dir()
    if target is None:
        print("No target: set [ocr] docling_models_dir in config.toml or pass --dir.")
        return 2
    try:
        from docling.utils.model_downloader import download_models
    except ModuleNotFoundError:
        print('Docling is not installed. Run: pip install -e ".[docling]"')
        return 2

    target.mkdir(parents=True, exist_ok=True)
    print(f"Downloading Docling models into {target} …")
    download_models(output_dir=target, force=args.force, progress=True,
                    with_layout=True, with_tableformer=True, with_rapidocr=True,
                    with_code_formula=False, with_picture_classifier=False)
    print(f"Done. The app will read them from {target} with no network access.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
