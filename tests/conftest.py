import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BUNDLES = ROOT / "bundles"
FIX = Path(__file__).with_name("fixtures")
