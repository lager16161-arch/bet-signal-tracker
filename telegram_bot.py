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
    """Extract user-visible text from Telegram rich-message payloads."""
    for attr in ("text", "caption", "text_html", "caption_html"):
        value = getattr(message, attr, None)
        if isinstance(value, str) and value.strip():
            return value

    candidates = []
    try:
        raw = message.to_dict()
        if isinstance(raw, dict) and raw.get("rich_message") is not None:
            candidates.append(raw["rich_message"])
        # PTB may preserve unrecognized Telegram fields in api_kwargs.
        api_kwargs = raw.get("api_kwargs") if isinstance(raw, dict) else None
        if isinstance(api_kwargs, dict) and api_kwargs.get("rich_message") is not None:
            candidates.append(api_kwargs["rich_message"])
    except Exception:
        pass

    try:
        api_kwargs = getattr(message, "api_kwargs", None)
        if isinstance(api_kwargs, dict) and api_kwargs.get("rich_message") is not None:
            candidates.append(api_kwargs["rich_message"])
    except Exception:
        pass

    layout_words = {"left", "middle", "right", "center", "top", "bottom"}
    ignored_keys = {
        "url", "style", "type", "entities", "custom_emoji_id",
        "align", "alignment", "horizontal_alignment", "vertical_alignment",
        "valign", "justify", "position", "layout", "gravity",
    }

    for rich in candidates:
        parts = []

        def walk(value, parent_key=None):
            if isinstance(value, dict):
                # Only collect strings from text-bearing fields. Do not treat
                # layout/alignment metadata as visible message content.
                for key, child in value.items():
                    if key in ignored_keys:
                        continue
                    if key in {"text", "title", "label", "caption"} and isinstance(child, str):
                        if child.strip() and child.strip().lower() not in layout_words:
                            parts.append(child.strip())
                    elif isinstance(child, (dict, list)):
                        walk(child, key)
            elif isinstance(value, list):
                for child in value:
                    walk(child, parent_key)
            elif isinstance(value, str) and parent_key in {"text", "title", "label", "caption"}:
                if value.strip() and value.strip().lower() not in layout_words:
                    parts.append(value.strip())

        walk(rich)
        cleaned = []
        for part in parts:
            if not cleaned or cleaned[-1] != part:
                cleaned.append(part)
        result = "\n".join(cleaned)
        if result.strip():
            return result
    return ""



def format_signal(text):
    """Preserve the original signal layout while removing the source channel footer."""
    text = (text or "").replace("\\r\n", "\n").replace("\n", "\n").replace("\\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    cleaned_lines = []
    for line in lines:
        # Remove the promotional channel line and any linked footer text.
        if re.search(r"STROGO\s+BOT\s+STAVOK", line, re.I):
            line = re.split(r"🤖?\s*STROGO\s+BOT\s+STAVOK", line, maxsplit=1, flags=re.I)[0].strip()
        line = re.sub(r"\s*[·|—-]?\s*Перейти\s*$", "", line, flags=re.I).strip()
        if line:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()

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
    """Extract odds from common signal layouts; infer from payout/stake if needed."""
    if not text:
        return None

    # First, look for an explicit odds value near a coefficient label.
    patterns = [
        r"(?:общий\s+коэффициент|коэффициент|коэф\.?|кф|odds)\s*[:=]?\s*(?:left|middle|right|center|\s)*(\d{1,4}(?:[.,]\d{1,3})?)",
        r"(?:коэффициент|коэф\.?|кф|odds)[^\d\n]{0,80}(\d{1,4}(?:[.,]\d{1,3})?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            try:
                value = float(match.group(1).replace(",", "."))
                if 1.0 <= value <= 10000:
                    return value
            except (ValueError, TypeError):
                pass

    # Some Premium rich messages flatten the layout so the odds label/value
    # relationship is lost. In that case, infer odds from potential payout / stake.
    def amount_after(labels):
        label_pattern = "|".join(labels)
        match = re.search(
            rf"(?:{label_pattern})[^\d\n]{{0,80}}([0-9][0-9\s\u00a0]*(?:[.,][0-9]{{1,2}})?)",
            text,
            re.I,
        )
        if not match:
            return None
        raw = re.sub(r"[\s\u00a0]", "", match.group(1)).replace(",", ".")
        try:
            return float(raw)
        except ValueError:
            return None

    stake = amount_after([r"сумма(?:\s+ставки)?", r"ставка"])
    payout = amount_after([r"возможная\s+выплата", r"выплата", r"potential\s+payout"])
    if stake and payout and stake > 0 and payout >= stake:
        inferred = payout / stake
        if 1.0 <= inferred <= 10000:
            return round(inferred, 4)

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


def category_breakdown(where, params=()):
    """Detailed results by existing bet category, using stored accounting only."""
    output = {}
    with conn() as db:
        rows = db.execute(f"""SELECT category,
            COUNT(*) total,
            SUM(CASE WHEN result IN ('w','win') THEN 1 ELSE 0 END) wins,
            SUM(CASE WHEN result IN ('l','loss') THEN 1 ELSE 0 END) losses,
            SUM(CASE WHEN result IN ('v','void') THEN 1 ELSE 0 END) voids,
            SUM(CASE WHEN result IS NULL THEN 1 ELSE 0 END) pending,
            COALESCE(SUM(CASE WHEN result IS NOT NULL THEN COALESCE(profit,0) ELSE 0 END),0) profit,
            COALESCE(SUM(CASE WHEN result IS NOT NULL THEN stake ELSE 0 END),0) resolved_stake,
            SUM(CASE WHEN odds BETWEEN 1.0 AND 1.50 THEN 1 ELSE 0 END) green,
            SUM(CASE WHEN odds > 1.50 AND odds <= 2.00 THEN 1 ELSE 0 END) yellow,
            SUM(CASE WHEN odds > 2.00 THEN 1 ELSE 0 END) red,
            SUM(CASE WHEN odds IS NULL OR odds < 1.0 THEN 1 ELSE 0 END) unknown
            FROM bets WHERE {where} GROUP BY category""", params).fetchall()
    for row in rows:
        d = dict(row)
        d['wins'] = d['wins'] or 0; d['losses'] = d['losses'] or 0
        d['voids'] = d['voids'] or 0; d['pending'] = d['pending'] or 0
        decided = d['wins'] + d['losses']
        d['hit_rate'] = d['wins'] / decided * 100 if decided else 0.0
        d['roi'] = (d['profit'] or 0) / d['resolved_stake'] * 100 if d['resolved_stake'] else 0.0
        output[d['category']] = d
    return output


def odds_band_breakdown(where, params=()):
    """Outcome and profit breakdown for the existing odds color thresholds."""
    specs = [('green', '🟢 1.01–1.50', 'odds BETWEEN 1.0 AND 1.50'),
             ('yellow', '🟡 1.51–2.00', 'odds > 1.50 AND odds <= 2.00'),
             ('red', '🔴 2.01+', 'odds > 2.00'),
             ('unknown', '⚪ Не определён', 'odds IS NULL OR odds < 1.0')]
    output = []
    with conn() as db:
        for key, label, condition in specs:
            row = db.execute(f"""SELECT COUNT(*) total,
                SUM(CASE WHEN result IN ('w','win') THEN 1 ELSE 0 END) wins,
                SUM(CASE WHEN result IN ('l','loss') THEN 1 ELSE 0 END) losses,
                SUM(CASE WHEN result IN ('v','void') THEN 1 ELSE 0 END) voids,
                SUM(CASE WHEN result IS NULL THEN 1 ELSE 0 END) pending,
                COALESCE(SUM(CASE WHEN result IS NOT NULL THEN COALESCE(profit,0) ELSE 0 END),0) profit
                FROM bets WHERE ({where}) AND ({condition})""", params).fetchone()
            d = dict(row)
            decided = (d['wins'] or 0) + (d['losses'] or 0)
            d['hit_rate'] = (d['wins'] or 0) / decided * 100 if decided else 0.0
            output.append((label, d))
    return output


def format_category_lines(where, params=(), indent=""):
    details = category_breakdown(where, params)
    lines = [f"{indent}🧩 ТИПЫ СТАВОК"]
    for key, label in CATEGORY_LABELS.items():
        d = details.get(key)
        if not d:
            lines.append(f"{indent}{label}: 0 ставок")
            continue
        lines.append(f"{indent}{label}: {d['total']} ставок")
        lines.append(f"{indent}   ✅ {d['wins']} · ❌ {d['losses']} · ➖ {d['voids']} · ⏳ {d['pending']}")
        lines.append(f"{indent}   🎯 Проходимость: {d['hit_rate']:.1f}% · ROI: {d['roi']:+.1f}%")
        lines.append(f"{indent}   💰 Прибыль: {float(d['profit'] or 0):+.2f} ₽")
        lines.append(f"{indent}   🎨 🟢{d['green'] or 0} 🟡{d['yellow'] or 0} 🔴{d['red'] or 0} ⚪{d['unknown'] or 0}")
    return lines


def format_compact_stats(title, s, where="1=1", params=()):
    b = s["bands"]
    decided = s['wins'] + s['losses']
    hit_rate = s['wins'] / decided * 100 if decided else 0.0
    lines = [
        f"{title} — {s['total']} ставок",
        f"✅ Побед: {s['wins']} · ❌ Проигрышей: {s['losses']} · ➖ Возвратов: {s['voids']} · ⏳ Ожидают: {s['pending']}",
        f"💰 Прибыль: {s['profit']:+.2f} ₽ · ROI: {s['roi']:+.2f}%",
        f"🎯 Проходимость: {hit_rate:.1f}% ({decided} решённых без возвратов)",
        f"🎨 Кэфы: 🟢 {b.get('green') or 0} · 🟡 {b.get('yellow') or 0} · 🔴 {b.get('red') or 0} · ⚪ {b.get('unknown') or 0}",
    ]
    lines.extend(format_category_lines(where, params, indent="  "))
    return "\n".join(lines)


def format_stats(title, s, where="1=1", params=()):
    b = s["bands"]
    decided = s['wins'] + s['losses']
    hit_rate = s['wins'] / decided * 100 if decided else 0.0
    chunks = [
        title, "━━━━━━━━━━━━━━━━",
        f"📌 Всего ставок: {s['total']}",
        f"✅ Побед: {s['wins']}   ❌ Проигрышей: {s['losses']}",
        f"➖ Возвратов: {s['voids']}   ⏳ Без результата: {s['pending']}",
        f"🎯 Проходимость: {hit_rate:.1f}% ({decided} решённых без возвратов)",
        f"💰 Прибыль: {s['profit']:+.2f} ₽",
        f"📊 ROI: {s['roi']:+.2f}%",
        "", "🎨 АНАЛИЗ КОЭФФИЦИЕНТОВ",
    ]
    for label, d in odds_band_breakdown(where, params):
        chunks.append(f"{label}: {d['total'] or 0} ставок · ✅{d['wins'] or 0} ❌{d['losses'] or 0} ➖{d['voids'] or 0} ⏳{d['pending'] or 0}")
        chunks.append(f"   🎯 Проходимость: {d['hit_rate']:.1f}% · 💰 {float(d['profit'] or 0):+.2f} ₽")
    chunks.extend(["", "━━━━━━━━━━━━━━━━", "🔎 СРАВНЕНИЕ LIVE И ПРЕМАТЧ"])
    for mode, label in (("live", "🔴 LIVE"), ("pre", "⏳ ПРЕМАТЧ")):
        mode_where = f"({where}) AND mode=?"
        mode_params = tuple(params) + (mode,)
        mode_stats = calc_stats(mode_where, mode_params)
        chunks.extend(["", format_compact_stats(label, mode_stats, mode_where, mode_params)])
    chunks.extend(["", "━━━━━━━━━━━━━━━━", "ℹ️ Фиксированная ставка: 100 ₽. Прибыль берётся из сохранённых результатов бота.",
                   "🎯 Проходимость = победы / (победы + поражения); возвраты и незавершённые ставки не учитываются."])
    return "\n".join(chunks)


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
        # Temporary diagnostics: log the Telegram message payload to identify why Premium forwards have no text.
        logging.warning("DEBUG MESSAGE: rich_message_present=%s, api_kwargs_keys=%s",
                        bool(getattr(message, "api_kwargs", {}).get("rich_message"))
                        if isinstance(getattr(message, "api_kwargs", None), dict) else False,
                        list(getattr(message, "api_kwargs", {}).keys())
                        if isinstance(getattr(message, "api_kwargs", None), dict) else [])
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
        return await query.edit_message_text(format_stats("📊 Общая статистика", calc_stats(), "1=1", ()), reply_markup=stat_keyboard())
    if data == "st:channels":
        with conn() as db:
            rows = db.execute("SELECT DISTINCT channel_key FROM bets ORDER BY channel_key").fetchall()
        buttons = [[InlineKeyboardButton(CHANNELS.get(r["channel_key"], r["channel_key"]), callback_data="st:channel:" + r["channel_key"])] for r in rows]
        buttons.append([InlineKeyboardButton("◀️ К меню", callback_data="st:menu")])
        return await query.edit_message_text("📺 Выбери канал для подробной статистики:", reply_markup=InlineKeyboardMarkup(buttons))
    if data.startswith("st:channel:"):
        key = data.split(":", 2)[2]
        title = "📺 " + CHANNELS.get(key, key)
        text = format_stats(title, calc_stats("channel_key=?", (key,)), "channel_key=?", (key,))
        return await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("◀️ К списку каналов", callback_data="st:channels")],
            [InlineKeyboardButton("◀️ К меню статистики", callback_data="st:menu")],
        ]))
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
    return await query.edit_message_text(format_stats(title, calc_stats(where, params), where, params), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ К меню", callback_data="st:menu")]]))


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
            pretty_text = format_signal(text)
            sent = await context.bot.send_message(chat_id=destination, text=pretty_text, reply_markup=result_keyboard(bet_id))
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
        final_text = (format_signal(row["text"]).rstrip() + "\n" + band + final_emoji)
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
