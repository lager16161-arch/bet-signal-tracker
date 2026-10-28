# Telegram bot starter (first integration step)

This starter forwards messages you send to the bot into one of eight destination channels. It is not yet connected to tracker.db or statistical reports; result buttons currently mark the result in the bot chat only.

1. Add `TELEGRAM_BOT_TOKEN` in GitHub Codespaces secrets and grant access to `bet-signal-tracker`.
2. Restart/rebuild the Codespace to load the secret.
3. In Codespaces terminal run `python3 -m pip install --user python-telegram-bot`
4. Set `BOT_ADMIN_IDS` to your numeric Telegram user ID in the environment before running. This protects result buttons.
5. Run `python3 telegram_bot.py`
6. In each of the eight target channels, send the matching command as a channel admin:
`/setchannel football_pre`, `/setchannel football_live`, `/setchannel tennis_pre`, `/setchannel tennis_live`, `/setchannel cs2_pre`, `/setchannel cs2_live`, `/setchannel dota_pre`, `/setchannel dota_live`.

The report channel is not connected yet. Never share or commit the bot token.
