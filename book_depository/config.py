"""Shared loader for local_config/config.yml — the local-machine runtime config.

One config file, one parser. Consumers: run_local.py, scripts/backfill_metadata.py,
scripts/check_sources.py, scripts/publish_catalog.py (each previously carried its
own copy of this logic).

Two constraints shape this module:

  * It must NEVER import book_depository.db (or anything that does) — db.py reads
    BOOK_DATA_DIR at import time, so config must be loadable and applied to the
    environment BEFORE that import happens. Stdlib + yaml only.
  * `yaml` is imported lazily inside load_config() — pyyaml lives in
    requirements-local.txt only, so nothing that runs on Render can crash just by
    importing this module.
"""

import os
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "local_config" / "config.yml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError:
        raise ImportError(
            f"{path} exists but pyyaml is not installed to read it — "
            "run: pip install -r requirements-local.txt"
        ) from None
    return yaml.safe_load(path.read_text()) or {}


def apply_env(cfg: dict) -> None:
    data_dir = cfg.get("data_dir")
    if data_dir and not os.environ.get("BOOK_DATA_DIR"):
        os.environ["BOOK_DATA_DIR"] = os.path.expanduser(data_dir)

    key = cfg.get("google_books_api_key") or cfg.get("GOOGLE_BOOKS_API_KEY")
    if key and not os.environ.get("GOOGLE_BOOKS_API_KEY"):
        os.environ["GOOGLE_BOOKS_API_KEY"] = key
