import sys
from pathlib import Path

# 确保项目根在 sys.path（pytest 从项目根运行时已包含，此处兜底）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
