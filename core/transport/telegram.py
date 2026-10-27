"""Telegram: передаёт нажатия и текст в App и показывает ответы. Больше ничего не знает.

Экран (View) уходит картинкой: несколько страниц — несколько картинок, кнопки — под последней.
Каждую минуту — проверка расписания рассылок; каждые 10 минут — почта/Диск клиента (если настроены).
Админ может прислать файл 1С боту — он примется вручную (и за месяц на согласовании).
"""
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from core.ingest.pipeline import ingest
from core.jobs import schedule
from core.ui import views

log = logging.getLogger("kpi")


def build(app, token, mailbox=None, drive=None):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
    from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler,
                              filters)

    tg = Application.builder().token(token).build()
    tz = ZoneInfo(app.cfg.timezone)

    def kb(reply):
        if not reply.buttons:
            return None
        return InlineKeyboardMarkup([[InlineKeyboardButton(b.label, callback_data=b.to) if not b.url else
                                      InlineKeyboardButton(b.label, url=b.url) for b in row] for row in reply.buttons])

    async def send(bot, chat_id, reply):
        chat_id = reply.to or chat_id
        markup = kb(reply)
        if reply.view is not None:
            n = views.pages(reply.view)
            for p in range(n):
                png = await asyncio.to_thread(views.to_png, reply.view, p)
                last = p == n - 1
                await bot.send_photo(chat_id, png, caption=(reply.text[:1000] or None) if last else None,
                                     reply_markup=markup if last else None)
        elif reply.text:
            await bot.send_message(chat_id, reply.text[:4000], reply_markup=markup)
        for name, data in reply.files:
            await bot.send_document(chat_id, InputFile(data, filename=name))

    async def deliver(bot, chat_id, replies):
        for r in replies:
            try:
                await send(bot, chat_id, r)
            except Exception:                         # noqa: BLE001
                log.exception("не отправилось в %s", r.to or chat_id)

    async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        await deliver(context.bot, update.effective_chat.id, await asyncio.to_thread(app.start, u.id, u.full_name))

    async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        await deliver(context.bot, update.effective_chat.id, await asyncio.to_thread(app.press, q.from_user.id, q.data))

    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        await deliver(context.bot, update.effective_chat.id,
                      await asyncio.to_thread(app.text, u.id, update.message.text or ""))

    async def on_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
        u = update.effective_user
        if u.id not in app.admins or app.demo:
            await update.message.reply_text("Файлы отчётов принимает только администратор.")
            return
        doc = update.message.document
        f = await doc.get_file()
        data = bytes(await f.download_as_bytearray())
        res = await asyncio.to_thread(ingest, app.client_id, app.db(), [(doc.file_name, data)], True, app.hooks)
        await update.message.reply_text("\n".join(r.text() for r in res)[:4000])

    async def tick(context: ContextTypes.DEFAULT_TYPE):
        now = datetime.now(tz)
        for j in schedule.due(app, now):
            replies = await asyncio.to_thread(schedule.run, app, j)
            await deliver(context.bot, 0, replies)

    async def pull(context: ContextTypes.DEFAULT_TYPE):
        files, done = [], None
        try:
            if mailbox:
                got, uids = await asyncio.to_thread(mailbox.fetch)
                files += got
                done = uids
            if drive:
                seen = app.db().get("drive_seen", {}) or {}
                got, seen, errs = await asyncio.to_thread(drive.fetch, seen)
                files += got
                app.db().put("drive_seen", seen)
        except Exception:                             # noqa: BLE001
            log.exception("почта/Диск")
            return
        if not files:
            return
        res = await asyncio.to_thread(ingest, app.client_id, app.db(), files, False, app.hooks)
        if mailbox and done:
            await asyncio.to_thread(mailbox.mark_seen, done)
        text = "📬 Новые отчёты:\n" + "\n".join(r.text() for r in res)
        for a in app.admins:
            try:
                await context.bot.send_message(a, text[:4000])
            except Exception:                         # noqa: BLE001
                log.exception("админу %s", a)

    tg.add_handler(CommandHandler("start", on_start))
    tg.add_handler(CallbackQueryHandler(on_button))
    tg.add_handler(MessageHandler(filters.Document.ALL, on_file))
    tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    tg.job_queue.run_repeating(tick, interval=60, first=5, name="schedule")
    if mailbox or drive:
        tg.job_queue.run_repeating(pull, interval=600, first=30, name="pull")
    return tg
