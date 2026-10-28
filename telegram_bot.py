import json, logging, os, asyncio
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
logging.basicConfig(level=logging.INFO)
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN")
CHANNELS={"football_pre":"⚽ Футбол — До начала","football_live":"🔴 Футбол — Лайв","tennis_pre":"🎾 Теннис — До начала","tennis_live":"🔴 Теннис — Лайв","cs2_pre":"🎮 CS2 — До начала","cs2_live":"🔴 CS2 — Лайв","dota_pre":"👾 Dota 2 — До начала","dota_live":"🔴 Dota 2 — Лайв"}
RESULTS={"win":"Победа","loss":"Проигрыш","void":"Возврат"}
CF=Path("telegram_channels.json"); AF=Path("telegram_admins.json")
def load(p,d):
 try:return json.loads(p.read_text(encoding="utf-8"))
 except (OSError,json.JSONDecodeError):return d
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding="utf-8")
def admins():return set(load(AF,[]))
async def start(u,c):
 a=admins(); a.add(u.effective_user.id); save(AF,sorted(a))
 await u.effective_message.reply_text("Бот работает ✅\n\nДля подключения канала перешли мне в личку любой пост ИЗ этого канала, затем выбери его название.\nПосле подключения пересылай сюда сигналы.")
async def incoming(u,c):
 m=u.effective_message
 if not m:return
 origin=getattr(m,"forward_origin",None); ch=getattr(origin,"chat",None) if origin else None
 if ch and getattr(ch,"type",None)=="channel":
  c.user_data["connect_chat_id"]=ch.id
  kb=[[InlineKeyboardButton(v,callback_data="connect:"+k)] for k,v in CHANNELS.items()]
  await m.reply_text("Выбери, какой канал подключаем:",reply_markup=InlineKeyboardMarkup(kb)); return
 c.user_data["pending_message_id"]=m.message_id
 kb=[[InlineKeyboardButton(v,callback_data="send:"+k)] for k,v in CHANNELS.items()]
 await m.reply_text("Сигнал получил. Выбери канал:",reply_markup=InlineKeyboardMarkup(kb))
async def buttons(u,c):
 q=u.callback_query; await q.answer(); d=q.data or ""; uid=q.from_user.id
 if uid not in admins():
  await q.edit_message_text("Сначала отправь боту /start в личном чате."); return
 if d.startswith("connect:"):
  k=d.split(":",1)[1]; cid=c.user_data.get("connect_chat_id")
  if not cid: await q.edit_message_text("Перешли пост из канала ещё раз."); return
  mp=load(CF,{}); mp[k]=cid; save(CF,mp)
  await q.edit_message_text("Канал подключён ✅\n"+CHANNELS[k]); return
 if d.startswith("send:"):
  k=d.split(":",1)[1]; dest=load(CF,{}).get(k); mid=c.user_data.get("pending_message_id")
  if not dest:
   await q.edit_message_text("Этот канал ещё не подключён. Перешли боту любой пост ИЗ нужного канала и выбери его название."); return
  try: sent=await c.bot.copy_message(chat_id=dest,from_chat_id=u.effective_chat.id,message_id=mid)
  except Exception as e:
   logging.exception("publish failed"); await q.edit_message_text("Не удалось отправить. Проверь права администратора бота в канале. Ошибка: "+type(e).__name__); return
  kb=[[InlineKeyboardButton("Победа",callback_data=f"result:win:{k}"),InlineKeyboardButton("Проигрыш",callback_data=f"result:loss:{k}"),InlineKeyboardButton("Возврат",callback_data=f"result:void:{k}")]]
  await c.bot.send_message(chat_id=dest,text="Отметить результат ставки:",reply_to_message_id=sent.message_id,reply_markup=InlineKeyboardMarkup(kb))
  await q.edit_message_text("Опубликовано в канале: "+CHANNELS[k]+" ✅"); return
 if d.startswith("result:"):
  r=d.split(":")[1]
  await q.edit_message_text("Результат отмечен: "+RESULTS.get(r,r)+". В этой версии отметка пока не записывается в финансовую статистику.")
async def main():
 if not TOKEN: raise RuntimeError("Не найден TELEGRAM_BOT_TOKEN")
 app=Application.builder().token(TOKEN).build()
 app.add_handler(CommandHandler("start",start))
 app.add_handler(CommandHandler("help",start))
 app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND,incoming))
 app.add_handler(CallbackQueryHandler(buttons))
 await app.initialize(); await app.start(); await app.updater.start_polling()
 print("Бот запущен. Остановка: Ctrl+C")
 try: await asyncio.Event().wait()
 finally:
  await app.updater.stop(); await app.stop(); await app.shutdown()
if __name__=="__main__": asyncio.run(main())
