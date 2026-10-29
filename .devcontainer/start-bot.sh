
#!/usr/bin/env bash
cd /workspaces/bet-signal-tracker

if pgrep -af '[t]elegram_bot.py' >/dev/null; then
    exit 0
fi

nohup python telegram_bot.py >> /tmp/telegram_bot.log 2>&1 < /dev/null &
