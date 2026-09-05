# -*- coding: utf-8 -*-
"""DeepSeek HARNESS Quant · 主入口（CLI）

用法：
    python main.py update      数据更新（盘后增量，等价 data/daily_pipeline.py）
    python main.py validate    P0.5 因子可行性验证（入场券，等价 validation/m3_validate.py）
    python main.py screen      多因子排名（等价 strategy/ranking_v2.py）
    python main.py backtest    回测 + 对比（等价 backtest/bt_engine.py）
    python main.py report      生成 Web 看板（见下方说明）

其余参数透传给对应脚本。例如：
    python main.py backtest --mode direction --topn 10
    python main.py validate --quick
    python main.py screen --n 15

说明：
  - report 目录的看板生成器（daily_signal/dashboard_* 等）为外包包，未随源码分发；
    日常看板请用 `python launcher.py` 启动 Web 决策台（deck :8787）。
  - 三池管理（观察/候选/决策）入口为 strategy/pool_layers.py，可单独调用。
"""
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent

# 命令 → 相对脚本路径（与 dev_auto.py 的调度入口保持一致）
COMMANDS = {
    "update": "data/daily_pipeline.py",
    "validate": "validation/m3_validate.py",
    "screen": "strategy/ranking_v2.py",
    "backtest": "backtest/bt_engine.py",
}


def _run(script_rel: str, args: list) -> int:
    script = BASE / script_rel
    if not script.exists():
        print(f"[main] 脚本不存在: {script}")
        return 1
    cmd = [sys.executable, "-X", "utf8", str(script)] + args
    print(f"[main] 执行: {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=str(BASE)).returncode


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = sys.argv[1]
    args = sys.argv[2:]
    if cmd == "report":
        print("[main] report/ 看板生成器为外包包，未随源码分发。")
        print("[main] 请用 `python launcher.py` 启动 Web 决策台（deck :8787）。")
        return 0
    script = COMMANDS.get(cmd)
    if script is None:
        print(f"[main] 未知命令: {cmd}\n{__doc__}")
        return 1
    return _run(script, args)


if __name__ == "__main__":
    sys.exit(main())
