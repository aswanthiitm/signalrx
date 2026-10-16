from functools import lru_cache
from pathlib import Path

import yaml

PACK = Path(__file__).with_name("track_d.yaml")


@lru_cache(maxsize=1)
def load_pack() -> dict:
    with PACK.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)
