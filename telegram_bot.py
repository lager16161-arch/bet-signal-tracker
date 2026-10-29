import asyncio, json, logging, os, re, sqlite3
from pathlib import Path
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNELS = {
    "football_pre": "⚽ Футбол — До начала", "football_live": "🔴 Футбол — Лайв",
    "tennis_pre": "🎾 Теннис — До начала", "tennis_live": "🔴 Теннис — Лайв",
    "cs2_pre": "🎮 CS2 — До начала", "cs2_live": "🔴 CS2 — Лайв",
    "dota_pre": "👾 Dota 2 — До начала", "dota_live": "🔴 Dota 2 — Лайв",
}
CF, AF, DB = Path("telegram_channels.json"), Path("telegram_admins.json"), Path("telegram_stats.db")
STAKE = 100.0
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["📊 Статистика", "📈 Отчёт"]],
    resize_keyboard=True,
    is_persistent=True,
)
CATEGORY_LABELS = {
    "single": "🎯 Ординары",
    "big_single": "💰 Крупные ординары",
    "express": "📈 Экспрессы",
    "big_express": "🚀 Крупные экспрессы",
}


def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_message_text(message):
    """Read plain text or caption; formatting entities (including Premium custom emoji) remain in the text."""
    for attr in ("text", "caption", "text_html", "caption_html"):
        value = getattr(message, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def admins():
    return set(load(AF, []))


def conn():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS bets(
        id INTEGER PRIMARY KEY AUTOINCREMENT, created TEXT, channel_key TEXT, text TEXT,
        odds REAL, stake REAL, result TEXT, profit REAL, channel_message_id INTEGER,
        category TEXT, sport TEXT, mode TEXT)""")
    # Safe migration for databases created by earlier versions; does not delete existing data.
    columns = {r[1] for r in db.execute("PRAGMA table_info(bets)").fetchall()}
    for name, typ in (("category", "TEXT"), ("sport", "TEXT"), ("mode", "TEXT")):
        if name not in columns:
            db.execute(f"ALTER TABLE bets ADD COLUMN {name} {typ}")
    db.commit()
    return db


def parse_odds(text):
    patterns = [
        r"(?:общий\s+коэффициент|коэффициент|коэф\.?|кф|odds)\s*[:=]?\s*(\d{1,4}(?:[.,]\d{1,3})?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            try:
                value = float(match.group(1).replace(",", "."))
                if 1.0 <= value <= 10000:
                    return value
            except ValueError:
                pass
    return None


def classify(text, channel_key):
    low = text.lower()
    if "крупный экспресс" in low:
        category = "big_express"
    elif "экспресс" in low:
        category = "express"
    elif "крупная ставка" in low:
        category = "big_single"
    else:
        category = "single"

    if "футбол" in low or "⚽" in text or channel_key.startswith("football_"):
        sport = "football"
    elif "теннис" in low or "🎾" in text or channel_key.startswith("tennis_"):
        sport = "tennis"
    elif "cs2" in low or "counter-strike" in low or "🎮" in text or channel_key.startswith("cs2_"):
        sport = "cs2"
    elif "dota" in low or "👾" in text or channel_key.startswith("dota_"):
        sport = "dota"
    else:
        sport = channel_key.split("_")[0]

    if "лайв" in low or "🔴" in text or channel_key.endswith("_live"):
        mode = "live"
    else:
        mode = "pre"
    return category, sport, mode


def odds_band(value):
    if value is None:
        return "⚪"
    if value <= 1.50:
        return "🟢"
    if value <= 2.00:
        return "🟡"
    return "🔴"


def result_keyboard(bet_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅", callback_data=f"r:w:{bet_id}"),
        InlineKeyboardButton("❌", callback_data=f"r:l:{bet_id}"),
        InlineKeyboardButton("➖", callback_data=f"r:v:{bet_id}"),
    ]])


def stat_keyboard():
    rows = [
        [InlineKeyboardButton("📊 Общая", callback_data="st:all")],
        [InlineKeyboardButton("📺 По каналам", callback_data="st:channels")],
        [InlineKeyboardButton("⚽ Футбол", callback_data="st:sport:football"), InlineKeyboardButton("🎾 Теннис", callback_data="st:sport:tennis")],
        [InlineKeyboardButton("🎮 CS2", callback_data="st:sport:cs2"), InlineKeyboardButton("👾 Dota 2", callback_data="st:sport:dota")],
        [InlineKeyboardButton("🔴 Все лайвы", callback_data="st:mode:live"), InlineKeyboardButton("⏳ Весь прематч", callback_data="st:mode:pre")],
        [InlineKeyboardButton("📈 Экспрессы", callback_data="st:category:express"), InlineKeyboardButton("🚀 Крупные экспрессы", callback_data="st:category:big_express")],
        [InlineKeyboardButton("🎯 Обычные", callback_data="st:category:single"), InlineKeyboardButton("💰 Крупные", callback_data="st:category:big_single")],
        [InlineKeyboardButton("🧹 Сбросить статистику", callback_data="reset:confirm")],
    ]
    return InlineKeyboardMarkup(rows)


def calc_stats(where="1=1", params=()):
    with conn() as db:
        row = db.execute(f"""SELECT COUNT(*) total,
          SUM(CASE WHEN result IN ('w','win') THEN 1 ELSE 0 END) wins,
          SUM(CASE WHEN result IN ('l','loss') THEN 1 ELSE 0 END) losses,
          SUM(CASE WHEN result IN ('v','void') THEN 1 ELSE 0 END) voids,
          SUM(CASE WHEN result IS NULL THEN 1 ELSE 0 END) pending,
          COALESCE(SUM(CASE WHEN result IS NOT NULL THEN COALESCE(profit,0) ELSE 0 END),0) profit,
          COALESCE(SUM(CASE WHEN result IS NOT NULL THEN stake ELSE 0 END),0) resolved_stake
          FROM bets WHERE {where}""", params).fetchone()
        bands = db.execute(f"""SELECT
          SUM(CASE WHEN odds BETWEEN 1.0 AND 1.50 THEN 1 ELSE 0 END) green,
          SUM(CASE WHEN odds > 1.50 AND odds <= 2.00 THEN 1 ELSE 0 END) yellow,
          SUM(CASE WHEN odds > 2.00 THEN 1 ELSE 0 END) red,
          SUM(CASE WHEN odds IS NULL OR odds < 1.0 THEN 1 ELSE 0 END) unknown
          FROM bets WHERE {where}""", params).fetchone()
        categories = db.execute(f"""SELECT category, COUNT(*) n FROM bets
          WHERE {where} GROUP BY category""", params).fetchall()
    result = dict(row)
    for key in ("total", "wins", "losses", "voids", "pending"):
        result[key] = result[key] or 0
    result["profit"] = result["profit"] or 0.0
    result["roi"] = result["profit"] / result["resolved_stake"] * 100 if result["resolved_stake"] else 0.0
    result["bands"] = dict(bands)
    result["categories"] = {r["category"]: r["n"] for r in categories}
    return result


def format_stats(title, s):
    b = s["bands"]
    return (f"{title}\n"
            f"Всего: {s['total']}\n✅ Побед: {s['wins']}\n❌ Проигрышей: {s['losses']}\n"
            f"➖ Возвратов: {s['voids']}\n⏳ Без результата: {s['pending']}\n"
            f"Прибыль: {s['profit']:+.2f} ₽\nROI: {s['roi']:+.2f}%\n\n"
            f"Коэффициенты в этой категории:\n"
            f"🟢 1.01–1.50: {b['green'] or 0}\n🟡 1.51–2.00: {b['yellow'] or 0}\n"
            f"🔴 2.01+: {b['red'] or 0}\n⚪ Коэффициент не найден: {b['unknown'] or 0}\n\n"
            f"Типы ставок в этом разделе:\n"
            f"🎯 Ординары: {s['categories'].get('single', 0)}\n"
            f"💰 Крупные ординары: {s['categories'].get('big_single', 0)}\n"
            f"📈 Экспрессы: {s['categories'].get('express', 0)}\n"
            f"🚀 Крупные экспрессы: {s['categories'].get('big_express', 0)}\n\n"
            f"Расчёт: 100 ₽ на каждую ставку. Суммы валют из сигнала игнорируются.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    known = admins(); known.add(update.effective_user.id); save(AF, sorted(known))
    await update.effective_message.reply_text(
        "Бот работает ✅\nЧтобы подключить канал назначения, отправь /connect и затем перешли любой пост ИЗ этого канала.\n"
        "Чтобы опубликовать сигнал, просто перешли его боту и выбери канал.\n"
        "Статистику и отчёт можно открыть кнопками снизу или командами /stats и /report.",
        reply_markup=MAIN_KEYBOARD,
    )


async def connect_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in admins():
        return await update.effective_message.reply_text("Нет доступа.")
    context.user_data["awaiting_channel_connection"] = True
    await update.effective_message.reply_text(
        "Перешли любой пост ИЗ канала, который нужно подключить как канал назначения."
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in admins():
        return await update.effective_message.reply_text("Нет доступа.")
    await update.effective_message.reply_text("📊 Выбери раздел статистики:", reply_markup=stat_keyboard())


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in admins():
        return await update.effective_message.reply_text("Нет доступа.")
    with conn() as db:
        rows = db.execute("SELECT channel_key, COUNT(*) n, COALESCE(SUM(CASE WHEN result IS NOT NULL THEN COALESCE(profit,0) ELSE 0 END),0) p FROM bets GROUP BY channel_key ORDER BY channel_key").fetchall()
    if not rows:
        return await update.effective_message.reply_text("Пока нет опубликованных ставок.")
    await update.effective_message.reply_text(
        "📈 Отчёт по каналам\n\n" + "\n\n".join(f"{CHANNELS.get(r['channel_key'], r['channel_key'])}\nСтавок: {r['n']}\nПрибыль: {r['p']:+.2f} ₽" for r in rows),
        reply_markup=MAIN_KEYBOARD,
    )


async def incoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return
    # Persistent menu buttons should never be mistaken for bet signals.
    command_text = (message.text or "").strip()
    if command_text == "📊 Статистика":
        if update.effective_user.id not in admins():
            return await message.reply_text("Нет доступа.", reply_markup=MAIN_KEYBOARD)
        return await message.reply_text("📊 Выбери раздел статистики:", reply_markup=stat_keyboard())
    if command_text == "📈 Отчёт":
        return await report(update, context)
    origin = getattr(message, "forward_origin", None)
    forwarded_chat = getattr(origin, "chat", None) if origin else None
    if context.user_data.get("awaiting_channel_connection"):
        if not forwarded_chat or getattr(forwarded_chat, "type", None) != "channel":
            return await message.reply_text("Не вижу источник-канал. Перешли пост именно из нужного канала.")
        context.user_data["connect_id"] = forwarded_chat.id
        context.user_data["awaiting_channel_connection"] = False
        return await message.reply_text(
            "Выбери, какой канал подключаем:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="c:" + key)] for key, label in CHANNELS.items()])
        )
    # A forwarded channel post is a signal by default. Use /connect explicitly for channel setup,
    # so channel-origin messages (including Premium-formatted posts) aren't intercepted as setup.
    text = extract_message_text(message)
    if not text.strip():
        logging.warning(
            "Forwarded/message has no readable text: content_type=%s, forward_origin=%s, caption_entities=%s, entities=%s",
            getattr(message, "effective_attachment", None).__class__.__name__ if getattr(message, "effective_attachment", None) is not None else "text-or-empty",
            type(origin).__name__ if origin else None,
            bool(getattr(message, "caption_entities", None)),
            bool(getattr(message, "entities", None)),
        )
        return await message.reply_text(
            "Не удалось прочитать текст этого сообщения. Если это пост с Telegram Premium, попробуй переслать его ещё раз. "
            "Если снова не получится, пришли скриншот самого поста — проверим, что именно Telegram передаёт боту."
        )
    context.user_data["pending_text"] = text
    await message.reply_text("Выбери канал:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="s:" + key)] for key, label in CHANNELS.items()]))


async def stats_callback(query, data):
    if data == "st:all":
        return await query.edit_message_text(format_stats("📊 Общая статистика", calc_stats()), reply_markup=stat_keyboard())
    if data == "st:channels":
        with conn() as db:
            rows = db.execute("SELECT channel_key, category, COUNT(*) n FROM bets GROUP BY channel_key, category ORDER BY channel_key, category").fetchall()
        by_channel = {}
        for row in rows:
            item = by_channel.setdefault(row["channel_key"], {"total": 0, "categories": {}})
            item["total"] += row["n"]
            item["categories"][row["category"]] = row["n"]
        chunks = []
        for key, item in by_channel.items():
            chunks.append(
                f"{CHANNELS.get(key, key)} — {item['total']} ставок\n" +
                " · ".join(f"{CATEGORY_LABELS[cat]}: {item['categories'].get(cat, 0)}" for cat in CATEGORY_LABELS)
            )
        text = "📺 Статистика по каналам\n\n" + ("\n\n".join(chunks) if chunks else "Пока нет ставок.")
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ К меню", callback_data="st:menu")]]))
    bits = data.split(":")
    kind, value = bits[1], bits[2]
    if kind == "sport":
        names = {"football":"⚽ Футбол", "tennis":"🎾 Теннис", "cs2":"🎮 CS2", "dota":"👾 Dota 2"}
        title, where, params = names.get(value, value), "sport=?", (value,)
    elif kind == "mode":
        title, where, params = ("🔴 Все лайвы", "mode=?", ("live",)) if value == "live" else ("⏳ Весь прематч", "mode=?", ("pre",))
    else:
        names = {"single":"🎯 Обычные ставки", "big_single":"💰 Крупные ставки", "express":"📈 Экспрессы", "big_express":"🚀 Крупные экспрессы"}
        title, where, params = names.get(value, value), "category=?", (value,)
    return await query.edit_message_text(format_stats(title, calc_stats(where, params)), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ К меню", callback_data="st:menu")]]))


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    if query.from_user.id not in admins():
        return await query.answer("Нет доступа", show_alert=True)
    await query.answer()
    if data == "st:menu":
        return await query.edit_message_text("📊 Выбери раздел статистики:", reply_markup=stat_keyboard())
    if data == "reset:confirm":
        return await query.edit_message_text(
            "⚠️ Сбросить ВСЮ статистику? Все записанные ставки и их результаты будут удалены. "
            "Каналы и настройки останутся на месте.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Да, сбросить", callback_data="reset:yes"),
                InlineKeyboardButton("❌ Отмена", callback_data="st:menu"),
            ]]),
        )
    if data == "reset:yes":
        with conn() as db:
            db.execute("DELETE FROM bets")
            # Reset SQLite's generated bet IDs as well, for a clean new run.
            db.execute("DELETE FROM sqlite_sequence WHERE name='bets'")
        return await query.edit_message_text(
            "✅ Статистика сброшена. Можно начинать заново.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ К статистике", callback_data="st:menu")]]),
        )
    if data.startswith("st:"):
        return await stats_callback(query, data)
    if data.startswith("c:"):
        key = data[2:]; channel_id = context.user_data.get("connect_id")
        if not channel_id:
            return await query.edit_message_text("Перешли пост из канала ещё раз.")
        mapping = load(CF, {}); mapping[key] = channel_id; save(CF, mapping)
        return await query.edit_message_text("Канал подключён ✅ " + CHANNELS[key])
    if data.startswith("s:"):
        key = data[2:]; destination = load(CF, {}).get(key); text = context.user_data.get("pending_text")
        if not destination:
            return await query.edit_message_text("Канал не подключён. Перешли боту любой пост ИЗ этого канала и выбери его название.")
        if not text:
            return await query.edit_message_text("Перешли сигнал заново.")
        category, sport, mode = classify(text, key)
        coefficient = parse_odds(text)
        with conn() as db:
            cursor = db.execute("INSERT INTO bets(created,channel_key,text,odds,stake,category,sport,mode) VALUES(?,?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), key, text, coefficient, STAKE, category, sport, mode))
            bet_id = cursor.lastrowid
        try:
            sent = await context.bot.send_message(chat_id=destination, text=text, reply_markup=result_keyboard(bet_id))
            with conn() as db:
                db.execute("UPDATE bets SET channel_message_id=? WHERE id=?", (sent.message_id, bet_id))
            return await query.edit_message_text("Опубликовано текстом в канале: " + CHANNELS[key] + " ✅")
        except Exception as exc:
            with conn() as db: db.execute("DELETE FROM bets WHERE id=?", (bet_id,))
            logging.exception("Publish failed")
            return await query.edit_message_text("Не удалось отправить. Проверь права бота в канале. Ошибка: " + type(exc).__name__)
    if data.startswith("r:"):
        _, short_result, bet_text = data.split(":")
        bet_id = int(bet_text)
        result = {"w":"win", "l":"loss", "v":"void"}[short_result]
        with conn() as db:
            row = db.execute("SELECT * FROM bets WHERE id=?", (bet_id,)).fetchone()
            if not row:
                return await query.answer("Ставка не найдена", show_alert=True)
            if row["result"] is not None:
                return await query.answer("Результат уже отмечен", show_alert=True)
            if short_result == "w":
                profit = round(STAKE * (row["odds"] - 1), 2) if row["odds"] is not None else None
            elif short_result == "l":
                profit = -STAKE
            else:
                profit = 0.0
            db.execute("UPDATE bets SET result=?, profit=? WHERE id=?", (result, profit, bet_id))
        # Keep the signal text intact and append only the requested two emoji.
        final_emoji = {"w":"✅", "l":"❌", "v":"➖"}[short_result]
        band = odds_band(row["odds"])
        final_text = (row["text"].rstrip() + "\n" + band + final_emoji)
        try:
            await query.edit_message_text(final_text, reply_markup=None)
        except Exception:
            logging.exception("Could not edit resolved bet message")
        return


async def main():
    if not TOKEN:
        raise RuntimeError("Не найден TELEGRAM_BOT_TOKEN в Secrets.")
    conn().close()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("connect", connect_channel))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, incoming))
    app.add_handler(CallbackQueryHandler(button))
    await app.initialize(); await app.start(); await app.updater.start_polling()
    print("Бот запущен. Остановка: Ctrl+C")
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop(); await app.stop(); await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
