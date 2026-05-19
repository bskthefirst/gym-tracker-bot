#!/usr/bin/env python3
"""
Daily system health monitor for Mac Mini.
Checks: CPU load, zombie uvicorn processes, critical services, port conflicts.
Acts: kills runaway processes.
Reports: Telegram message with status summary.
"""
import os
import sys
import subprocess
import json
import time
import re

TELEGRAM_TOKEN = os.environ.get("GYM_BOT_TOKEN", "").strip()
USER_ID = os.environ.get("USER_ID", "8578040659").strip()


def sh(cmd, timeout=30):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def send_telegram(msg):
    if not TELEGRAM_TOKEN or not USER_ID:
        print("[SKIP] No token or user_id")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": USER_ID, "text": msg, "parse_mode": "HTML"}
    try:
        import urllib.request
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")


def get_load():
    out, _, _ = sh("sysctl -n vm.loadavg")
    m = re.search(r"\{\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\}", out)
    if m:
        return float(m.group(1)), float(m.group(2)), float(m.group(3))
    return 0.0, 0.0, 0.0


def get_cpu_idle():
    out, _, _ = sh("top -l 2 -n 0 -s 1 | grep 'CPU usage'")
    lines = out.split("\n")
    for line in lines:
        if "idle" in line:
            m = re.search(r"([\d.]+)%\s+idle", line)
            if m:
                return float(m.group(1))
    return 100.0


def get_uvicorn_pids():
    out, _, _ = sh("pgrep -f 'uvicorn main:app'")
    return [p for p in out.split("\n") if p.strip()]


def get_port_8000_listeners():
    out, _, _ = sh("lsof -i :8000 | grep LISTEN")
    listeners = []
    for line in out.split("\n"):
        if line.strip() and "LISTEN" in line:
            parts = line.split()
            if len(parts) >= 2:
                listeners.append(parts[1])  # PID
    return listeners


def kill_pids(pids):
    for pid in pids:
        try:
            os.kill(int(pid), 9)
            print(f"[KILL] {pid}")
        except Exception as e:
            print(f"[KILL FAIL] {pid}: {e}")


def check_url(url, timeout=15):
    try:
        import urllib.request
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "Mozilla/5.0")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, True
    except Exception as e:
        return str(e), False


def check_launchd(label_substring):
    out, _, rc = sh(f"launchctl list | grep '{label_substring}'")
    if rc == 0 and out.strip():
        parts = out.strip().split("\t")
        if len(parts) >= 3:
            pid = parts[0]
            status = parts[1]
            name = parts[2]
            return pid != "-", name, pid, status
    return False, None, None, None


def main():
    actions = []
    alerts = []

    # 1. CPU / Load
    load1, load5, load15 = get_load()
    cpu_idle = get_cpu_idle()
    print(f"Load: {load1:.2f} / {load5:.2f} / {load15:.2f}")
    print(f"CPU idle: {cpu_idle:.1f}%")

    if load1 > 15:
        alerts.append(f"⚠️ High load: {load1:.1f}")

    # 2. Uvicorn zombie hunt
    uvicorn_pids = get_uvicorn_pids()
    port8k_pids = get_port_8000_listeners()
    print(f"Uvicorn PIDs: {uvicorn_pids}")
    print(f"Port 8000 listeners: {port8k_pids}")

    # Kill duplicate uvicorn processes
    if len(uvicorn_pids) > 1:
        # Keep the one with lowest PID (usually the first/oldest stable one)
        sorted_pids = sorted(uvicorn_pids, key=lambda x: int(x))
        keep = sorted_pids[0]
        kill = sorted_pids[1:]
        kill_pids(kill)
        actions.append(f"Killed {len(kill)} duplicate uvicorn (kept {keep})")
        time.sleep(2)
        # Recheck
        uvicorn_pids = get_uvicorn_pids()
        port8k_pids = get_port_8000_listeners()

    # 3. Port 8000 listener count
    if len(port8k_pids) > 1:
        alerts.append(f"⚠️ Port 8000 has {len(port8k_pids)} listeners")

    # 4. Service checks
    # ingredientcompliance.com web
    ic_web_ok, ic_web_name, ic_web_pid, ic_web_status = check_launchd("ingredientcompliance.web")
    if not ic_web_ok:
        alerts.append("❌ ingredientcompliance.web DOWN")
        # Try to reload
        sh("launchctl load ~/Library/LaunchAgents/com.ingredientcompliance.web.plist")
        actions.append("Reloaded ingredientcompliance.web")
        time.sleep(3)
        ic_web_ok, _, _, _ = check_launchd("ingredientcompliance.web")

    # gym-tracker bot
    gym_ok, gym_name, gym_pid, gym_status = check_launchd("gym-tracker")
    if not gym_ok:
        alerts.append("❌ gym-tracker bot DOWN")
        sh("launchctl load ~/Library/LaunchAgents/com.gym-tracker.plist")
        actions.append("Reloaded gym-tracker")
        time.sleep(3)
        gym_ok, _, _, _ = check_launchd("gym-tracker")

    # 5. URL health checks
    ic_http_status, ic_http_ok = check_url("https://ingredientcompliance.com/")
    dashboard_status, dashboard_ok = check_url("https://bskthefirst.github.io/gym-tracker-bot/")

    if not ic_http_ok:
        alerts.append(f"❌ ingredientcompliance.com unreachable ({ic_http_status})")
    if not dashboard_ok:
        alerts.append(f"❌ Dashboard unreachable ({dashboard_status})")

    # 6. Report
    now = time.strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        f"📊 <b>Daily Health Report</b> — {now}",
        f"Load: {load1:.2f} | CPU idle: {cpu_idle:.0f}%",
        f"",
        f"🌐 ingredientcompliance.com: {'✅' if ic_http_ok else '❌'} ({ic_http_status})",
        f"🏋️ Dashboard: {'✅' if dashboard_ok else '❌'} ({dashboard_status})",
        f"",
        f"🔧 Services:",
        f"  ic-web: {'✅' if ic_web_ok else '❌'} (pid {ic_web_pid or 'none'})",
        f"  gym-bot: {'✅' if gym_ok else '❌'} (pid {gym_pid or 'none'})",
        f"  port8000: {len(port8k_pids)} listener(s)",
    ]

    if actions:
        lines.append("")
        lines.append("⚡ Actions taken:")
        for a in actions:
            lines.append(f"  • {a}")

    if alerts:
        lines.append("")
        lines.append("🚨 Alerts:")
        for a in alerts:
            lines.append(f"  • {a}")

    msg = "\n".join(lines)
    print("\n" + "=" * 40)
    print(msg)
    print("=" * 40)

    send_telegram(msg)

    # If load is critically high, do an emergency kill
    if load1 > 20:
        emergency_msg = f"🚨 EMERGENCY: Load {load1:.1f}. Killing all uvicorn..."
        send_telegram(emergency_msg)
        sh("pkill -9 -f 'uvicorn main:app'")
        sh("launchctl load ~/Library/LaunchAgents/com.ingredientcompliance.web.plist")
        time.sleep(5)
        send_telegram("Emergency reload done. Rechecking...")
        # Recursion guard: just call main again once
        main()


if __name__ == "__main__":
    main()
