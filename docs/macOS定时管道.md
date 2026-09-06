# macOS 定时管道（launchd）

> 背景：主系统在 Windows 用「任务计划程序」跑每日管道（schtasks）；macOS 移植后这些任务不存在，
> 导致每日全链（18:30）/ 盘后扫描（17:35）/ 因子池（19:15）等不会自动执行。
> `deck/system_live.py`（`/api/system_live`）已支持 launchd 检测：任务未安装时显示「未安装」而非 ERR。

## 1. 模板清单（scripts/macos/）

| 模板 | 对应 Windows 任务 | 触发 |
|---|---|---|
| `com.dshquant.deck.plist.example` | Deck 守护（launcher 全量启动） | RunAtLoad + KeepAlive |
| `com.dshquant.daily-pipeline.plist.example` | LWQuant-DailyPipeline | 每日 18:30 |
| `com.dshquant.daily-report.plist.example` | DSHQuant-AIReview（自动选股+日报） | 工作日 20:00 |

其余任务（after-close 17:35 / factor-daily 19:15 / devdriver 每 4h / deck-guard 每 30min）复制
`daily-pipeline` 模板，按注释改 Label、ProgramArguments 与触发即可。

## 2. 安装步骤

```bash
# 1) 复制并编辑（把 /Users/YOUR_USER/... 换成实际路径）
cp scripts/macos/com.dshquant.deck.plist.example ~/Library/LaunchAgents/com.dshquant.deck.plist
# 2) 加载
launchctl load ~/Library/LaunchAgents/com.dshquant.deck.plist
# 3) 验证
launchctl list | grep dshquant
```

卸载：`launchctl unload ~/Library/LaunchAgents/com.dshquant.deck.plist`
日志：`logs/deck_launchd.log` / `logs/deck_launchd.err`

## 3. 注意

- plist 内 `ProgramArguments` 用系统 python3 即可；如需项目虚拟环境，改成 `.venv/bin/python` 的绝对路径。
- 数据目录：launchd 环境变量不含 `LWQUANT_CACHE_DIR` 时走 `config/params.yaml` 的 `data.cache_dir`；
  如需指定，在 plist 加 `<key>EnvironmentVariables</key>`。
- 管道是否该跑由数据是否就绪决定；本模板只负责「到点触发」，与 Windows 行为对齐。
