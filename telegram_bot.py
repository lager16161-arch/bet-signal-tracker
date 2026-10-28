import os, logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNELS = {
 "football_pre":"⚽ Футбол — До начала", "football_live":"🔴 Футбол — Лайв",
 "tennis_pre":"🎾 Теннис — До начала", "tennis_live":"🔴 Теннис — Лайв",
 "cs2_pre":"🎮 CS2 — До начала", "cs2_live":"🔴 CS2 — Лайв",
 "dota_pre":"👾 Dota 2 — До начала", "dota_live":"🔴 Dota 2 — Лайв",
}
RESULTS={"win":"✅ Победа","loss":"❌ Проигрыш","void":"↩️ Возврат"}
ADMIN_IDS={int(x) for x in os.getenv("BOT_ADMIN_IDS","").split(",") if x.strip().isdigit()}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text="Бот работает. Перешли сюда сигнал, затем выбери канал.\n\nПодключение каналов: в каждом канале отправь команду /setchannel KEY от имени канала.\n"
    text += "\n".join(f"{k} — {v}" for k,v in CHANNELS.items())
    await update.effective_message.reply_text(text)

async def setchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat=update.effective_chat
    if chat.type not in ("channel","supergroup"):
        await update.effective_message.reply_text("Команду нужно отправить внутри целевого канала.")
        return
    if not context.args or context.args[0] not in CHANNELS:
        await update.effective_message.reply_text("Укажи ключ, например /setchannel football_pre")
        return
    context.application.bot_data.setdefault("channels",{})[context.args[0]]=chat.id
    await update.effective_message.reply_text("Канал подключён: "+CHANNELS[context.args[0]])

async def incoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m=update.effective_message
    if not m or update.effective_chat.type!="private": return
    context.user_data["pending_id"]=m.message_id
    rows=[]
    items=list(CHANNELS.items())
    for i in range(0,len(items),2):
        rows.append([InlineKeyboardButton(items[i][1],callback_data="route:"+items[i][0]),
                     InlineKeyboardButton(items[i+1][1],callback_data="route:"+items[i+1][0])])
    await m.reply_text("Куда опубликовать сигнал?",reply_markup=InlineKeyboardMarkup(rows))

async def clicked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    await q.answer()
    data=q.data.split(":")
    if data[0]=="route":
        key=data[1]
        dest=context.application.bot_data.get("channels",{}).get(key)
        mid=context.user_data.get("pending_id")
        if not dest or not mid:
            await q.edit_message_text(f"Канал не подключён или сигнал потерян. В нужном канале отправь /setchannel {key}, затем перешли сигнал снова.")
            return
        try:
            sent=await context.bot.copy_message(dest,q.message.chat_id,mid)
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Победа",callback_data=f"result:win:{key}"),
                InlineKeyboardButton("❌ Проигрыш",callback_data=f"result:loss:{key}"),
                InlineKeyboardButton("↩️ Возврат",callback_data=f"result:void:{key}")]])
            await context.bot.send_message(dest,"Отметить результат:",reply_to_message_id=sent.message_id,reply_markup=kb)
            await q.edit_message_text("Сигнал опубликован: "+CHANNELS[key])
        except Exception:
            logging.exception("publish failed")
            await q.edit_message_text("Не удалось опубликовать. Проверь права бота в канале.")
    elif data[0]=="result":
        if not update.effective_user or update.effective_user.id not in ADMIN_IDS:
            await q.answer("Только администратор бота может отмечать результат.",show_alert=True); return
        label=RESULTS[data[1]]
        await q.edit_message_text("Результат отмечен: "+label)

def main():
    if not TOKEN: raise SystemExit("Нет TELEGRAM_BOT_TOKEN. Добавь secret и перезапусти Codespace.")
    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("setchannel",setchannel))
    app.add_handler(CallbackQueryHandler(clicked))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND,incoming))
    app.run_polling()
if __name__=="__main__": main()
