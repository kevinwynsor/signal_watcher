"""
QQQ Scalper v4.1 signal watcher.

Opens your TradingView chart in a Playwright-controlled browser, reads the hidden
"WATCH ..." values from the Data Window, and sends a phone notification whenever
the strategy prints a new event (call/put entry, TP1, exit).

Usage:
  python watcher.py --login    first run: log in to TradingView, open the Data Window, then close the browser
  python watcher.py --test     print what it reads right now + send a test notification
  python watcher.py --export-session   save your login to session.json (paste it into the GitHub secret TV_SESSION)
  python watcher.py --once     single check, then exit (used by GitHub Actions every 15 min)
  python watcher.py            run the watcher (stops itself after market hours if stop_after_et is set)
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "state.json"
PROFILE_DIR = HERE / "browser_profile"
SESSION_PATH = HERE / "session.json"   # exported login (cookies + local storage) for GitHub Actions
ET = ZoneInfo("America/New_York")

FIELDS = ["type", "day", "time", "dir", "score", "strike", "premium",
          "stop", "price", "result", "contracts", "dte"]

# Finds every Data Window row titled "WATCH <field>" and returns {field: value text}.
READ_JS = """
(fields) => {
  const want = new Set(fields.map(f => 'WATCH ' + f));
  const out = {};
  for (const el of document.querySelectorAll('div, span')) {
    if (el.children.length) continue;
    const t = (el.textContent || '').trim();
    if (!want.has(t) || (t.slice(6) in out)) continue;
    let row = el.parentElement;
    for (let i = 0; i < 3 && row; i++) {
      const v = (row.innerText || '').replace(t, '').trim();
      if (v) { out[t.slice(6)] = v.split('\\n')[0].trim(); break; }
      row = row.parentElement;
    }
  }
  return out;
}
"""


def log(msg):
    print(f"[{datetime.now(ET):%Y-%m-%d %H:%M:%S} ET] {msg}", flush=True)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def num(text):
    if text is None:
        return None
    t = text.replace("−", "-").replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        return None  # "∅", "n/a" etc.


# ─────────────── notifications ───────────────
def notify(cfg, title, body, urgent=False):
    sent = False
    n = cfg.get("ntfy", {})
    if n.get("topic"):
        try:
            requests.post(f"{n.get('server', 'https://ntfy.sh').rstrip('/')}/{n['topic']}",
                          data=body.encode("utf-8"),
                          headers={"Title": title.encode("utf-8"), "Priority": "high" if urgent else "default"},
                          timeout=10).raise_for_status()
            sent = True
        except Exception as e:
            log(f"ntfy failed: {e}")
    t = cfg.get("telegram", {})
    if t.get("bot_token") and t.get("chat_id"):
        try:
            requests.post(f"https://api.telegram.org/bot{t['bot_token']}/sendMessage",
                          json={"chat_id": t["chat_id"], "text": f"{title}\n{body}"}, timeout=10).raise_for_status()
            sent = True
        except Exception as e:
            log(f"telegram failed: {e}")
    d = cfg.get("discord", {})
    if d.get("webhook_url"):
        try:
            requests.post(d["webhook_url"], json={"content": f"**{title}**\n{body}"}, timeout=10).raise_for_status()
            sent = True
        except Exception as e:
            log(f"discord failed: {e}")
    if not sent:
        log("WARNING: no notification channel succeeded (check config.json)")
    return sent


def describe(v):
    """Turn the WATCH values into a notification title + body."""
    typ = int(num(v["type"]) or 0)
    d = int(num(v.get("dir")) or 0)
    side = "CALL" if d == 1 else "PUT"
    day = str(int(num(v["day"]) or 0))
    hm = int(num(v["time"]) or 0)
    when = f"{day[4:6]}/{day[6:8]} {hm // 100:02d}:{hm % 100:02d} ET"
    px = v.get("price", "?")
    if typ in (1, -1):
        stop_side = "below" if typ == 1 else "above"
        title = f"QQQ LONG {side} - score {v.get('score', '?')}"
        body = (f"{v.get('strike', '?')}{side[0]} {v.get('dte', '?')}DTE ~${v.get('premium', '?')} "
                f"(QQQ {px}, candle close {when})\n"
                f"Wrong if a 15m candle closes {stop_side} {v.get('stop', '?')}\n"
                f"Size <= {v.get('contracts', '?')} contracts. Confirm premium/delta at your broker.")
        return title, body, True
    if typ == 2:
        return (f"QQQ {side}: TP1 hit",
                f"Est. banked {v.get('result', '?')}% so far (QQQ {px}, {when}). "
                f"Sell your TP1 portion and move the runner stop.", True)
    if typ == 3:
        return (f"QQQ {side}: EXIT",
                f"Est. trade result {v.get('result', '?')}% on premium (QQQ {px}, {when}).", True)
    return None


# ─────────────── main loop ───────────────
def read_values(page):
    vals = page.evaluate(READ_JS, FIELDS)
    return vals if all(k in vals for k in ("type", "day", "time")) else None


def event_key(v):
    return f"{v['type']}|{v['day']}|{v['time']}"


def open_chart(pw, cfg, headless=False):
    ctx = pw.chromium.launch_persistent_context(
        str(PROFILE_DIR), headless=headless, viewport={"width": 1400, "height": 900},
        args=["--disable-background-timer-throttling",
              "--disable-renderer-backgrounding",
              "--disable-backgrounding-occluded-windows"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(cfg["chart_url"], wait_until="domcontentloaded", timeout=90000)
    return ctx, page


def past_stop_time(cfg):
    stop = cfg.get("stop_after_et")
    if not stop:
        return False
    h, m = map(int, stop.split(":"))
    now = datetime.now(ET)
    return now.hour * 60 + now.minute >= h * 60 + m


def run(cfg):
    state = load_json(STATE_PATH, {})
    poll = float(cfg.get("poll_seconds", 5))
    with sync_playwright() as pw:
        ctx, page = open_chart(pw, cfg)
        log("Chart opened, waiting for the Data Window values...")
        missing_since = None
        warned = False
        last_reload = time.time()
        first = True
        while True:
            if past_stop_time(cfg):
                log("Past stop time, exiting.")
                break
            try:
                v = read_values(page)
            except Exception as e:
                log(f"read error: {e}")
                v = None

            if v is None:
                missing_since = missing_since or time.time()
                gone = time.time() - missing_since
                if gone > 180 and not warned:
                    notify(cfg, "QQQ watcher: can't read the chart",
                           "WATCH values not found for 3 min. Is the Data Window open and the v4.1 script on the chart?")
                    warned = True
                if gone > 90 and time.time() - last_reload > 120:
                    log("Values missing, reloading the chart")
                    page.reload(wait_until="domcontentloaded", timeout=90000)
                    last_reload = time.time()
            else:
                if warned:
                    notify(cfg, "QQQ watcher: back online", "Reading the chart again.")
                missing_since, warned = None, False
                key = event_key(v)
                if first:
                    log(f"Reading OK. Latest event on chart: {key}")
                    first = False
                if key != state.get("last_key"):
                    is_real_event = int(num(v["type"]) or 0) != 0
                    if is_real_event and state.get("last_key") is not None:
                        msg = describe(v)
                        if msg:
                            title, body, urgent = msg
                            log(f"NEW EVENT {key}: {title}")
                            notify(cfg, title, body, urgent)
                    elif state.get("last_key") is None:
                        log("First run: baseline saved, no notification for the existing event.")
                    state["last_key"] = key
                    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
            time.sleep(poll)
        ctx.close()


def in_market_window():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= mins <= 16 * 60 + 15


def process(cfg, state, v):
    """Compare the chart's latest event to the saved one and notify if new."""
    key = event_key(v)
    if key == state.get("last_key"):
        log(f"No new event (latest: {key})")
        return
    if state.get("last_key") is None:
        log(f"First run: baseline saved ({key}), no notification.")
    elif int(num(v["type"]) or 0) != 0:
        msg = describe(v)
        if msg:
            title, body, urgent = msg
            log(f"NEW EVENT {key}: {title}")
            notify(cfg, title, body, urgent)
    state["last_key"] = key


def once(cfg, force=False):
    if not force and not in_market_window():
        log("Outside market hours, skipping.")
        return
    state = load_json(STATE_PATH, {})
    v = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)  # headed under xvfb: less likely to be flagged
        ctx = browser.new_context(
            viewport={"width": 1400, "height": 900},
            storage_state=str(SESSION_PATH) if SESSION_PATH.exists() else None)
        page = ctx.new_page()
        page.goto(cfg["chart_url"], wait_until="domcontentloaded", timeout=90000)
        for _ in range(18):  # up to ~90 s for the chart + Data Window to load
            page.wait_for_timeout(5000)
            try:
                v = read_values(page)
            except Exception as e:
                log(f"read error: {e}")
            if v:
                break
        if not v:
            page.screenshot(path=str(HERE / "debug.png"))
        browser.close()

    if v:
        if state.get("warned"):
            notify(cfg, "QQQ watcher: back online", "Reading the chart again.")
            state["warned"] = False
        process(cfg, state, v)
    else:
        log("Could not read WATCH values (see debug.png in the run's artifacts)")
        if not state.get("warned"):
            notify(cfg, "QQQ watcher: can't read the chart",
                   "GitHub run couldn't find the WATCH values. Session expired? "
                   "Re-run --export-session and update the TV_SESSION secret.")
            state["warned"] = True
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def export_session(cfg):
    with sync_playwright() as pw:
        ctx, page = open_chart(pw, cfg)
        print("Make sure you're logged in and the Data Window is open, then press Enter.")
        input()
        v = read_values(page)
        print("Read:", v if v else "NOTHING - open the Data Window first!")
        ctx.storage_state(path=str(SESSION_PATH))
        ctx.close()
    print(f"Saved {SESSION_PATH}. Paste its full contents into the GitHub secret TV_SESSION.\n"
          "Keep this file private: it is your TradingView login.")


def login(cfg):
    with sync_playwright() as pw:
        ctx, page = open_chart(pw, cfg)
        print("\n1) Log in to TradingView in the browser window (if needed).\n"
              "2) Make sure the chart shows QQQ 15m with the v4.1 script.\n"
              "3) Open the Data Window from the right-hand toolbar and leave it open.\n"
              "4) Save the layout, then come back here and press Enter.")
        input()
        v = read_values(page)
        print("Read:", v if v else "NOTHING - the Data Window rows 'WATCH ...' were not found")
        ctx.close()


def test(cfg):
    with sync_playwright() as pw:
        ctx, page = open_chart(pw, cfg)
        print("Waiting 20 s for the chart to load...")
        page.wait_for_timeout(20000)
        v = read_values(page)
        print("Read:", json.dumps(v, indent=2) if v else "NOTHING")
        if v and describe(v):
            print("Latest event as a message:", describe(v)[:2])
        ctx.close()
    ok = notify(cfg, "QQQ watcher test", "If you see this, notifications work.")
    print("Test notification sent." if ok else "Test notification FAILED - check config.json.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--force", action="store_true", help="with --once: ignore market hours")
    ap.add_argument("--export-session", action="store_true")
    a = ap.parse_args()
    cfg = load_json(CONFIG_PATH, None)
    if not cfg:
        sys.exit("Missing config.json - copy config.example.json to config.json and fill it in.")
    if a.login:
        login(cfg)
    elif a.export_session:
        export_session(cfg)
    elif a.once:
        once(cfg, a.force)
    elif a.test:
        test(cfg)
    else:
        run(cfg)
