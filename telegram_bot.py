import asyncio, json, logging, os, re, sqlite3
from pathlib import Path
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters
logging.basicConfig(level=logging.INFO)
TOKEN=os.getenv("TELEGRAM_BOT_TOKEN")
CHANNELS={"football_pre":"⚽ Футбол — До начала","football_live":"🔴 Футбол — Лайв","tennis_pre":"🎾 Теннис — До начала","tennis_live":"🔴 Теннис — Лайв","cs2_pre":"🎮 CS2 — До начала","cs2_live":"🔴 CS2 — Лайв","dota_pre":"👾 Dota 2 — До начала","dota_live":"🔴 Dota 2 — Лайв"}
CF=Path("telegram_channels.json"); AF=Path("telegram_admins.json"); DB=Path("telegram_stats.db"); STAKE=100.0
def load(p,d):
 try:return json.loads(p.read_text(encoding="utf-8"))
 except (OSError,json.JSONDecodeError):return d
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding="utf-8")
def admins():return set(load(AF,[]))
def conn():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
 c.execute("""CREATE TABLE IF NOT EXISTS bets(id INTEGER PRIMARY KEY AUTOINCREMENT,created TEXT,channel_key TEXT,text TEXT,odds REAL,stake REAL,result TEXT,profit REAL,channel_message_id INTEGER)"""); c.commit(); return c
def odds(text):
 for p in [r"(?:коэффициент|коэф\.?|кф|odds)\s*[:=]?\s*(\d{1,3}(?:[.,]\d{1,3})?)",r"(?<![\d/])([1-9]\d?\.\d{2})(?!\d)"]:
  m=re.search(p,text,re.I)
  if m:
   try:
    x=float(m.group(1).replace(",","."))
    if 1<x<=1000:return x
   except ValueError:pass
 return None
def kb(bid):return InlineKeyboardMarkup([[InlineKeyboardButton("✅",callback_data=f"r:w:{bid}"),InlineKeyboardButton("❌",callback_data=f"r:l:{bid}"),InlineKeyboardButton("➖",callback_data=f"r:v:{bid}")]])
async def start(u,c):
 a=admins(); a.add(u.effective_user.id); save(AF,sorted(a))
 await u.effective_message.reply_text("Бот работает ✅\nПодключение канала: перешли мне любой пост ИЗ нужного канала и выбери его название.\nСтатистика: /stats или /report")
async def stats(u,c):
 if u.effective_user.id not in admins(): return await u.effective_message.reply_text("Нет доступа.")
 with conn() as x:
  n=x.execute("SELECT COUNT(*) n FROM bets").fetchone()["n"]
  w=x.execute("SELECT COUNT(*) n FROM bets WHERE result='w'").fetchone()["n"]
  l=x.execute("SELECT COUNT(*) n FROM bets WHERE result='l'").fetchone()["n"]
  v=x.execute("SELECT COUNT(*) n FROM bets WHERE result='v'").fetchone()["n"]
  pending=x.execute("SELECT COUNT(*) n FROM bets WHERE result IS NULL").fetchone()["n"]
  p=x.execute("SELECT COALESCE(SUM(profit),0) p FROM bets WHERE result IS NOT NULL").fetchone()["p"]
  base=x.execute("SELECT COALESCE(SUM(stake),0) s FROM bets WHERE result IS NOT NULL").fetchone()["s"]
 roi=p/base*100 if base else 0
 await u.effective_message.reply_text(f"📊 Статистика\nВсего: {n}\n✅ Побед: {w}\n❌ Проигрышей: {l}\n➖ Возвратов: {v}\n⏳ Без результата: {pending}\nПрибыль: {p:+.2f}\nROI: {roi:+.2f}%\n\nСтавка для расчёта — 100 условных единиц. Только новые публикации этой версии.")
async def report(u,c):
 if u.effective_user.id not in admins(): return await u.effective_message.reply_text("Нет доступа.")
 with conn() as x: rows=x.execute("SELECT channel_key,COUNT(*) n,COALESCE(SUM(CASE WHEN result IS NOT NULL THEN profit ELSE 0 END),0) p FROM bets GROUP BY channel_key").fetchall()
 if not rows:return await u.effective_message.reply_text("Пока нет опубликованных ставок.")
 await u.effective_message.reply_text("📈 Отчёт по каналам\n\n"+"\n\n".join(f"{CHANNELS.get(r['channel_key'],r['channel_key'])}\nСтавок: {r['n']}\nПрибыль: {r['p']:+.2f}" for r in rows))
async def incoming(u,c):
 m=u.effective_message
 if not m:return
 o=getattr(m,"forward_origin",None); ch=getattr(o,"chat",None) if o else None
 if ch and getattr(ch,"type",None)=="channel":
  c.user_data["connect_id"]=ch.id
  await m.reply_text("Выбери, какой канал подключаем:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(v,callback_data="c:"+k)] for k,v in CHANNELS.items()])); return
 text=m.text or m.caption or ""
 if not text.strip():return await m.reply_text("Не вижу текста. Перешли текстовый сигнал.")
 c.user_data["pending_text"]=text
 await m.reply_text("Выбери канал:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(v,callback_data="s:"+k)] for k,v in CHANNELS.items()]))
async def button(u,c):
 q=u.callback_query; await q.answer(); d=q.data or ""
 if q.from_user.id not in admins():return await q.answer("Нет доступа",show_alert=True)
 if d.startswith("c:"):
  k=d[2:]; cid=c.user_data.get("connect_id")
  if not cid:return await q.edit_message_text("Перешли пост из канала ещё раз.")
  mp=load(CF,{}); mp[k]=cid; save(CF,mp); return await q.edit_message_text("Канал подключён ✅ "+CHANNELS[k])
 if d.startswith("s:"):
  k=d[2:]; dest=load(CF,{}).get(k); text=c.user_data.get("pending_text")
  if not dest:return await q.edit_message_text("Канал не подключён. Перешли боту любой пост ИЗ этого канала и выбери его название.")
  if not text:return await q.edit_message_text("Перешли сигнал заново.")
  with conn() as x:
   cur=x.execute("INSERT INTO bets(created,channel_key,text,odds,stake) VALUES(?,?,?,?,?)",(datetime.now(timezone.utc).isoformat(),k,text,odds(text),STAKE)); bid=cur.lastrowid
  try:
   sent=await c.bot.send_message(chat_id=dest,text=text,reply_markup=kb(bid))
   with conn() as x:x.execute("UPDATE bets SET channel_message_id=? WHERE id=?",(sent.message_id,bid))
   return await q.edit_message_text("Опубликовано текстом в канале: "+CHANNELS[k]+" ✅")
  except Exception as e:
   with conn() as x:x.execute("DELETE FROM bets WHERE id=?",(bid,))
   logging.exception("Publish failed"); return await q.edit_message_text("Не удалось отправить. Проверь права бота в канале. Ошибка: "+type(e).__name__)
 if d.startswith("r:"):
  _,res,bidtext=d.split(":"); bid=int(bidtext); result={"w":"win","l":"loss","v":"void"}[res]
  with conn() as x:
   row=x.execute("SELECT * FROM bets WHERE id=?",(bid,)).fetchone()
   if not row:return await q.answer("Ставка не найдена",show_alert=True)
   if row["result"] is not None:return await q.answer("Результат уже отмечен",show_alert=True)
   if res=="w": profit=round(STAKE*(row["odds"]-1),2) if row["odds"] else None
   elif res=="l":profit=-STAKE
   else:profit=0.0
   x.execute("UPDATE bets SET result=?,profit=? WHERE id=?",(result,profit,bid))
  try:await q.edit_message_reply_markup(reply_markup=None)
  except Exception:pass
  await q.answer({"w":"Победа сохранена","l":"Проигрыш сохранён","v":"Возврат сохранён"}[res],show_alert=True)
async def main():
 if not TOKEN:raise RuntimeError("Не найден TELEGRAM_BOT_TOKEN в Secrets.")
 conn().close(); app=Application.builder().token(TOKEN).build()
 app.add_handler(CommandHandler("start",start)); app.add_handler(CommandHandler("stats",stats)); app.add_handler(CommandHandler("report",report))
 app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND,incoming)); app.add_handler(CallbackQueryHandler(button))
 await app.initialize(); await app.start(); await app.updater.start_polling(); print("Бот запущен. Остановка: Ctrl+C")
 try:await asyncio.Event().wait()
 finally:await app.updater.stop(); await app.stop(); await app.shutdown()
if __name__=="__main__":asyncio.run(main())
