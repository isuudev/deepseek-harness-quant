# -*- coding: utf-8 -*-
"""data/_platform.py — 跨平台工具（隐藏控制台 / 进程存活 / 计划任务 / 进程管理）

统一封装 Windows 专属的 ctypes / schtasks / tasklist / wmic / powershell / netstat
等调用。非 Windows 平台一律返回安全默认值（no-op / False / 空），保证脚本在
macOS / Linux 上不因缺失 Windows 命令而崩溃。
"""
import os
import subprocess


def is_windows() -> bool:
    """当前是否为 Windows。"""
    return os.name == "nt"


def hide_console() -> None:
    """隐藏控制台窗口（仅 Windows 有效；其他平台 no-op）。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        h = ctypes.windll.kernel32.GetConsoleWindow()
        if h:
            ctypes.windll.user32.ShowWindow(h, 0)
    except Exception:
        pass


def pid_alive(pid) -> bool:
    """检查进程是否存活。Windows 用 tasklist，POSIX 用 os.kill(pid, 0)。"""
    if pid is None:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        if os.name == "nt":
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
                               capture_output=True, timeout=15)
            out = r.stdout.decode("gbk", errors="ignore")
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def scheduler_supported() -> bool:
    """系统是否支持 schtasks 计划任务（仅 Windows）。"""
    return os.name == "nt"


def task_query(name: str) -> str:
    """查询单个计划任务状态（schtasks /Query /FO LIST）。
    非 Windows 或查询失败返回空字符串。"""
    if os.name != "nt":
        return ""
    try:
        r = subprocess.run(["schtasks", "/Query", "/TN", name, "/FO", "LIST"],
                           capture_output=True, timeout=15)
        return r.stdout.decode("gbk", errors="ignore") + r.stderr.decode("gbk", errors="ignore")
    except Exception:
        return ""


def task_query_all() -> str:
    """查询全部计划任务（schtasks /query /fo LIST /v）。
    非 Windows 或查询失败返回空字符串。"""
    if os.name != "nt":
        return ""
    try:
        r = subprocess.run(["schtasks", "/query", "/fo", "LIST", "/v"],
                           capture_output=True, timeout=30)
        return r.stdout.decode("gbk", errors="replace") + r.stderr.decode("gbk", errors="replace")
    except Exception:
        return ""


def task_set(name: str, enabled: bool) -> bool:
    """启用/禁用计划任务（schtasks /Change）。非 Windows 返回 False。"""
    if os.name != "nt":
        return False
    flag = "/Enable" if enabled else "/Disable"
    try:
        r = subprocess.run(["schtasks", "/Change", "/TN", name, flag],
                           capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def find_pids_by_cmdline(fragment: str) -> list:
    """按命令行片段查找匹配进程 PID（跨平台）。

    Windows 用 wmic / powershell 查 python.exe 命令行；
    POSIX 用 pgrep -f 匹配。查询失败返回空列表。
    """
    pids = []
    try:
        if os.name == "nt":
            cmdline = ""
            try:
                r = subprocess.run(["wmic", "process", "where", "name='python.exe'",
                                    "get", "ProcessId,CommandLine"],
                                   capture_output=True, text=True, errors="replace", timeout=15)
                cmdline = r.stdout or ""
            except Exception:
                pass
            if fragment not in cmdline:
                try:
                    r2 = subprocess.run(
                        ["powershell", "-NoProfile", "-Command",
                         "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\").CommandLine"],
                        capture_output=True, text=True, errors="replace", timeout=15)
                    cmdline = r2.stdout or ""
                except Exception:
                    pass
            for line in cmdline.splitlines():
                if fragment in line:
                    pid = line.split()[-1].strip()
                    if pid.isdigit():
                        pids.append(int(pid))
        else:
            r = subprocess.run(["pgrep", "-f", fragment],
                               capture_output=True, text=True, timeout=10)
            for ln in (r.stdout or "").splitlines():
                ln = ln.strip()
                if ln.isdigit():
                    pids.append(int(ln))
    except Exception:
        pass
    return pids


def kill_process(pid) -> bool:
    """终止进程。Windows 用 taskkill /F，POSIX 用 os.kill SIGKILL。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        if os.name == "nt":
            r = subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            return r.returncode == 0
        os.kill(pid, 9)
        return True
    except Exception:
        return False
