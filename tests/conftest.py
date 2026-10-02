"""Make the skill scripts importable as top-level modules (checker, report, ste)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "ste100" / "scripts"
sys.path.insert(0, str(SCRIPTS))
