QQQ Scalper v4.1 watcher - setup
================================

1. TradingView
   - Add "qqq v4.1 - playwright hidden script.txt" to a QQQ 15m chart (replace v4).
   - Open the Data Window (right toolbar). You should see rows "WATCH type", "WATCH day", ...
   - Save the layout. Copy the chart URL from the address bar (https://www.tradingview.com/chart/xxxx/).

2. Install (once, in a terminal in this folder)
   pip install -r requirements.txt
   python -m playwright install chromium

3. Config
   Copy config.example.json to config.json and set:
   - chart_url: your saved layout URL
   - At least one channel:
     ntfy (easiest): install the ntfy app, subscribe to a long random topic name, put the same name in "topic".
     Telegram: bot_token from @BotFather + your chat_id.
     Discord: a channel webhook URL.

4. First run
   python watcher.py --login   (log in to TradingView in the window that opens, open the Data Window, press Enter)
   python watcher.py --test    (should print the WATCH values and send a test notification)

5. Daily
   Run start_watcher.bat (logs to watcher.log). It exits by itself at stop_after_et (16:10 ET).
   To automate: Windows Task Scheduler -> Create Basic Task -> Weekly Mon-Fri at 9:20 AM ET
   -> Start a program -> start_watcher.bat. Set the PC to not sleep during market hours.

Notes
   - Don't leave the mouse hovering over the watcher's chart: the Data Window then shows the hovered bar.
   - The first run only saves a baseline; you get notified for events after that.
   - You get a warning notification if the chart can't be read for 3 minutes.
