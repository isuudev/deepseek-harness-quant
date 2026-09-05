print("MIN1")
import sqlite3
print("MIN2")
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.cache import CACHE_DIR
con = sqlite3.connect(str(CACHE_DIR / "bars.db"))
print("MIN3")
