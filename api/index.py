# api/index.py
import json
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

# -----------------------------
# 🔑 التوكن مدموج داخل الكود حسب طلبك
# -----------------------------
TOKEN = "8427063575:AAGyQSTbjGHOrBHhZeVucVnNWc47amwR7RA"

# -----------------------------
# الحالة العامة في الذاكرة
# -----------------------------
queues = {}
awaiting_input = {}  # لتخزين المرحلة الحالية لكل شات (مفتاحه chat_id)

# -----------------------------
# مساعدات صغيرة
# -----------------------------
def make_main_keyboard(chat_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 انضم / انسحب", callback_data=f"join|{chat_id}")],
        [
            InlineKeyboardButton("🗑️ ريموف", callback_data=f"remove_menu|{chat_id}"),
            InlineKeyboardButton("🔒 إنهاء الدور", callback_data=f"close|{chat_id}")
        ],
        [InlineKeyboardButton("⭐ إدارة المشرفين", callback_data=f"manage_admins|{chat_id}")]
    ])

def is_admin_or_creator(user_id, q):
    return user_id == q["creator"] or user_id in q["admins"]

# -----------------------------
# Handlers: start / collect_info / forceclose
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # استخدم send_message بدل reply_text لتجنب BadRequest عندما تكون الرسالة غير قابلة للرد
    chat_id = update.effective_chat.id
    if chat_id in queues and not queues[chat_id].get("closed", True):
        await context.bot.send_message(chat_id=chat_id, text="⚠️ فيه دور شغال بالفعل، اقفله الأول قبل تبدأ جديد.")
        return

    awaiting_input[chat_id] = {"step": "teacher"}
    await context.bot.send_message(chat_id=chat_id, text="👩‍🏫 اكتب اسم المعلمة:")

async def collect_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ✅ تأكد إن الرسالة نص مش زرار
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user_input = update.message.text.strip()

    if chat_id not in awaiting_input:
        return

    step = awaiting_input[chat_id]["step"]

    if step == "teacher":
        awaiting_input[chat_id]["teacher"] = user_input
        awaiting_input[chat_id]["step"] = "class_name"
        await context.bot.send_message(chat_id=chat_id, text="📘 اكتب اسم الحلقة:")
        return

    elif step == "class_name":
        teacher_name = awaiting_input[chat_id]["teacher"]
        class_name = user_input
        creator_name = update.effective_user.full_name

        queues[chat_id] = {
            "creator": update.effective_user.id,
            "creator_name": creator_name,
            "admins": set(),
            "members": [],
            "removed": set(),
            "all_joined": set(),
            "closed": False,
            "usernames": {},
            "teacher_name": teacher_name,
            "class_name": class_name
        }

        del awaiting_input[chat_id]

        text = (
            f"👤 *بدأ الدور:* {creator_name}\n"
            f"📚 *اسم المعلمة:* {teacher_name}\n"
            f"🏫 *اسم الحلقة:* {class_name}\n\n"
            f"🎯 *القائمة الحالية:* (فاضية)"
        )
        # نرسل الرسالة للقناة/الشات التي بدأ فيها الدور
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=make_main_keyboard(chat_id), parse_mode="Markdown")

async def force_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_name = update.effective_user.full_name

    if chat_id in queues:
        del queues[chat_id]
    if chat_id in awaiting_input:
        del awaiting_input[chat_id]

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🚨 تم قفل أو حذف أي دور مفتوح بواسطة *{user_name}* ✅",
        parse_mode="Markdown"
    )

# -----------------------------
# CallbackQuery handler (محدث)
# -----------------------------
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    data = query.data or ""
    user = query.from_user
    parts = data.split("|")
    action = parts[0] if parts else ""

    # طباعة بسيطة للـ logs (تظهر في Vercel)
    print(f"[callback] action={action} from={user.id} data={data}")

    # بعض الأفعال الخاصة التي لا تحتاج لقائمة queues أولاً
    if action == "select_channel":
        try:
            target_chat_id = int(parts[1])
        except Exception:
            await query.answer("❌ خطأ في بيانات القناة.")
            return
        await query.answer("اخترت القناة. سيتم بدء إدخال البيانات.")
        awaiting_input[target_chat_id] = {
            "step": "teacher",
            "creator_id": user.id,
            "creator_name": user.full_name,
            "private_chat_id": update.effective_chat.id
        }
        try:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="👩‍🏫 اكتب اسم المعلمة:")
        except Exception:
            pass
        return

    if len(parts) < 2:
        await query.answer("❌ خطأ في بيانات الزر.")
        return

    try:
        chat_id = int(parts[1])
    except Exception:
        await query.answer("❌ خطأ في ID الدردشة.")
        return

    q = queues.get(chat_id)
    if not q:
        await query.answer("❌ مفيش دور شغال.")
        return

    # ----------------- join / leave -----------------
    if action == "join":
        if q["closed"]:
            await query.answer("🚫 التسجيل مقفول.")
            return

        q["usernames"][user.id] = user.full_name

        if user.id in q["removed"]:
            await query.answer("🚫 تم حذفك من الدور. استنى الدور الجديد.")
            return

        if user.id in q["members"]:
            q["members"].remove(user.id)
            if user.id in q["all_joined"]:
                q["all_joined"].remove(user.id)
            await query.answer("❌ تم انسحابك.")
        else:
            q["members"].append(user.id)
            q["all_joined"].add(user.id)
            await query.answer("✅ تم تسجيلك!")

        members_text = "\n".join([f"{i+1}. {q['usernames'].get(uid, 'مجهول')}" for i, uid in enumerate(q["members"])]) or "(فاضية)"
        text = (
            f"👤 *بدأ الدور:* {q['creator_name']}\n"
            f"📚 *اسم المعلمة:* {q['teacher_name']}\n"
            f"🏫 *اسم الحلقة:* {q['class_name']}\n\n"
            f"🎯 *القائمة الحالية:*\n{members_text}"
        )
        try:
            await query.edit_message_text(text, reply_markup=make_main_keyboard(chat_id), parse_mode="Markdown")
        except Exception as e:
            print("Warning: could not edit message after join:", e)
            try:
                await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=make_main_keyboard(chat_id), parse_mode="Markdown")
            except Exception as e2:
                print("Also failed to send message to chat:", e2)
        return

    # ----------------- remove_menu -----------------
    if action == "remove_menu":
        if not is_admin_or_creator(user.id, q):
            await query.answer("🚫 مش من صلاحياتك.")
            return
        if not q["members"]:
            await query.answer("📋 مفيش حد في الدور.")
            return

        await query.answer()
        keyboard = []
        for i, uid in enumerate(q["members"]):
            name = q["usernames"].get(uid, "مجهول")
            keyboard.append([InlineKeyboardButton(f"❌ {name}", callback_data=f"remove_member|{chat_id}|{i}")])
        keyboard.append([InlineKeyboardButton("🔙 إلغاء", callback_data=f"cancel_remove|{chat_id}")])

        text = "🗑️ *اختر الاسم اللي عايز تمسحه:*"
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except Exception as e:
            print("Warning: could not edit message for remove_menu:", e)
        return

    # ----------------- remove_member -----------------
    if action == "remove_member":
        if not is_admin_or_creator(user.id, q):
            await query.answer("🚫 مش من صلاحياتك.")
            return
        try:
            index = int(parts[2])
        except Exception:
            await query.answer("❌ خطأ في الفهرس.")
            return
        if 0 <= index < len(q["members"]):
            target = q["members"].pop(index)
            q["removed"].add(target)
        await query.answer("✅ تم حذف العضو.")

        members_text = "\n".join([f"{i+1}. {q['usernames'].get(uid, 'مجهول')}" for i, uid in enumerate(q["members"])]) or "(فاضية)"
        text = (
            f"👤 *بدأ الدور:* {q['creator_name']}\n"
            f"📚 *اسم المعلمة:* {q['teacher_name']}\n"
            f"🏫 *اسم الحلقة:* {q['class_name']}\n\n"
            f"🎯 *القائمة الحالية:*\n{members_text}"
        )
        try:
            await query.edit_message_text(text, reply_markup=make_main_keyboard(chat_id), parse_mode="Markdown")
        except Exception as e:
            print("Warning: could not edit message after remove_member:", e)
        return

    # ----------------- cancel_remove -----------------
    if action == "cancel_remove":
        await query.answer("تم الإلغاء ✅")
        members_text = "\n".join([f"{i+1}. {q['usernames'].get(uid, 'مجهول')}" for i, uid in enumerate(q["members"])]) or "(فاضية)"
        text = (
            f"👤 *بدأ الدور:* {q['creator_name']}\n"
            f"📚 *اسم المعلمة:* {q['teacher_name']}\n"
            f"🏫 *اسم الحلقة:* {q['class_name']}\n\n"
            f"🎯 *القائمة الحالية:*\n{members_text}"
        )
        try:
            await query.edit_message_text(text, reply_markup=make_main_keyboard(chat_id), parse_mode="Markdown")
        except Exception as e:
            print("Warning: could not edit message after cancel_remove:", e)
        return

    # ----------------- close -----------------
    if action == "close":
        if not is_admin_or_creator(user.id, q):
            await query.answer("🚫 مش من صلاحياتك.")
            return
        q["closed"] = True
        await query.answer("🔒 تم إنهاء الدور.")

        all_joined = list(q["all_joined"])
        removed = list(q["removed"])
        remaining = [uid for uid in q["members"] if uid not in removed]

        full_list_text = "\n".join([f"{i+1}. {q['usernames'].get(uid, 'مجهول')}" for i, uid in enumerate(all_joined)]) or "(فاضية)"
        removed_text = "\n".join([f"{i+1}. {q['usernames'].get(uid, 'مجهول')}" for i, uid in enumerate(removed)]) or "(مفيش)"
        remaining_text = "\n".join([f"{i+1}. {q['usernames'].get(uid, 'مجهول')}" for i, uid in enumerate(remaining)]) or "(مفيش)"

        final_text = (
            f"👤 *بدأ الدور:* {q['creator_name']}\n"
            f"📚 *اسم المعلمة:* {q['teacher_name']}\n"
            f"🏫 *اسم الحلقة:* {q['class_name']}\n\n"
            "📋 *القائمة النهائية للدور:*\n\n"
            "👥 *كل اللي شاركوا فعليًا:*\n"
            f"{full_list_text}\n\n"
            "✅ *تمت القراءه:*\n"
            f"{removed_text}\n\n"
            "❌ *لم يقرأ:*\n"
            f"{remaining_text}"
        )
        try:
            await query.message.reply_text(final_text, parse_mode="Markdown")
        except Exception as e:
            # لو reply فشل، نرسل كرسالة عادية للشات
            print("Warning: could not reply with final_text on close:", e)
            try:
                await context.bot.send_message(chat_id=chat_id, text=final_text, parse_mode="Markdown")
            except Exception as e2:
                print("Also failed to send final_text to chat:", e2)

        if chat_id in queues:
            del queues[chat_id]
        return

    # ----------------- manage_admins / toggle_admin -----------------
    if action == "manage_admins":
        if user.id != q["creator"]:
            await query.answer("🚫 بس اللي بدأ الدور يقدر يدير المشرفين.")
            return

        if not q["members"]:
            await query.answer("📋 مفيش حد في الدور.")
            return

        await query.answer()
        keyboard = []
        for uid in q["members"]:
            if uid == q["creator"]:
                continue
            name = q["usernames"].get(uid, "مجهول")
            label = f"⭐ أزل {name} من المشرفين" if uid in q["admins"] else f"⭐ عيّن {name} مشرف"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"toggle_admin|{chat_id}|{uid}")])
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"cancel_remove|{chat_id}")])

        try:
            await query.edit_message_text("👮 *إدارة المشرفين:*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except Exception as e:
            print("Warning: could not edit message for manage_admins:", e)
        return

    if action == "toggle_admin":
        if user.id != q["creator"]:
            await query.answer("🚫 بس اللي بدأ الدور يقدر يعمل كده.")
            return
        try:
            target_id = int(parts[2])
        except Exception:
            await query.answer("❌ خطأ في بيانات العضو.")
            return

        if target_id in q["admins"]:
            q["admins"].remove(target_id)
            await query.answer("❌ تم إزالة الإشراف.")
        else:
            q["admins"].add(target_id)
            await query.answer("⭐ تم تعيينه مشرفًا.")

        members_to_manage = [uid for uid in q["members"] if uid != q["creator"]]
        keyboard = []
        for uid in members_to_manage:
            name = q["usernames"].get(uid, "مجهول")
            label = f"⭐ أزل {name} من المشرفين" if uid in q["admins"] else f"⭐ عيّن {name} مشرف"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"toggle_admin|{chat_id}|{uid}")])
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data=f"cancel_remove|{chat_id}")])

        try:
            await query.edit_message_text("👮 *إدارة المشرفين:*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except Exception as e:
            print("Warning: could not edit message after toggle_admin:", e)
        return

    await query.answer("❌ فعل غير معروف.")
    return

# -----------------------------
# إعداد Application (Telegram) و FastAPI (Webhook)
# -----------------------------
application = ApplicationBuilder().token(TOKEN).build()

# تسجيل الهاندلرز
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("forceclose", force_close))
application.add_handler(CallbackQueryHandler(button))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, collect_info))

# FastAPI app
app = FastAPI()

@app.on_event("startup")
async def on_startup():
    try:
        await application.initialize()
        print("Application initialized.")
    except Exception as e:
        print("Error initializing application:", e)

@app.on_event("shutdown")
async def on_shutdown():
    try:
        await application.shutdown()
        print("Application shutdown.")
    except Exception as e:
        print("Error during application shutdown:", e)

@app.post("/api")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status":"error","message":"Invalid JSON"})

    try:
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return {"status":"ok"}
    except Exception as e:
        print("Error processing update:", e)
        return JSONResponse(status_code=500, content={"status":"error","message":str(e)})

@app.get("/api")
async def root():
    return {"message":"Telegram Bot is ready to receive webhooks!"}
