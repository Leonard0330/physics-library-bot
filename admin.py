"""
admin.py — پنل ادمین برای اضافه کردن کتاب (دوزبانه: فارسی/انگلیسی)
"""

from telebot import types
import database

admin_sessions: dict[int, dict] = {}

DEFAULT_LANG = "fa"


def is_admin(user_id: int) -> bool:
    return database.is_admin(user_id)


def get_lang(user_id: int) -> str:
    """زبان پرسیستنت کاربر رو می‌گیره (همون چیزی که bot.py هم ذخیره می‌کنه)."""
    return database.get_user_lang(user_id) or DEFAULT_LANG


# ─────────────────────────────
# متن‌های دوزبانه
# ─────────────────────────────
T = {
    "no_access":       {"fa": "⛔️ دسترسی ندارید.",                              "en": "⛔️ You don't have access."},
    "panel_title":      {"fa": "👨‍💼 پنل ادمین\nیکی از گزینه‌ها رو انتخاب کن:",   "en": "👨‍💼 Admin Panel\nChoose an option:"},
    "cancelled":        {"fa": "↩️ عملیات لغو شد.",                             "en": "↩️ Operation cancelled."},
    "ask_book_id_del":  {"fa": "🗑 شناسه (ID) کتاب رو بنویس:",                   "en": "🗑 Enter the book ID:"},
    "not_a_number":     {"fa": "❗️ شناسه باید عدد باشه.",                       "en": "❗️ The ID must be a number."},
    "book_not_found":   {"fa": "❌ کتابی با شناسه {id} پیدا نشد.",               "en": "❌ No book found with ID {id}."},
    "book_deleted":     {"fa": "🗑 کتاب «{title}» حذف شد.",                     "en": "🗑 Book \"{title}\" deleted."},
    "back_to_panel":    {"fa": "بازگشت به پنل:",                               "en": "Back to panel:"},
    "send_pdf":         {"fa": "📤 فایل PDF کتاب رو بفرست:",                    "en": "📤 Send the book's PDF file:"},
    "pdf_only":         {"fa": "❗️ فقط فایل PDF قبول میشه.",                    "en": "❗️ Only PDF files are accepted."},
    "file_received":    {"fa": "✅ فایل دریافت شد: {name}\n\n📘 حالا عنوان کتاب رو بنویس:",
                          "en": "✅ File received: {name}\n\n📘 Now type the book's title:"},
    "ask_author":       {"fa": "✍ نام نویسنده:",                                "en": "✍ Author's name:"},
    "ask_lang":         {"fa": "🌐 زبان کتاب رو انتخاب کن:",                     "en": "🌐 Choose the book's language:"},
    "ask_field":        {"fa": "🧲 فیلد فیزیکی رو انتخاب کن:",                   "en": "🧲 Choose the physics field:"},
    "ask_year":         {"fa": "📅 سال انتشار (مثلاً 2020) — یا رد کن:",         "en": "📅 Publication year (e.g. 2020) — or skip:"},
    "year_not_number":  {"fa": "❗️ سال رو به عدد وارد کن (مثلاً 2020):",         "en": "❗️ Enter the year as a number (e.g. 2020):"},
    "ask_edition":      {"fa": "🔖 ویرایش (مثلاً 3rd) — یا رد کن:",              "en": "🔖 Edition (e.g. 3rd) — or skip:"},
    "ask_desc":         {"fa": "📝 توضیحات کوتاه — یا رد کن:",                  "en": "📝 A short description — or skip:"},
    "confirm_question": {"fa": "آیا اطلاعات صحیح است؟",                        "en": "Is this information correct?"},
    "wrong_step":       {"fa": "⚠️ مرحله اشتباه",                              "en": "⚠️ Wrong step"},
    "missing_fields":   {"fa": "❌ این فیلدها خالی هستن: {fields}\nدوباره از ابتدا شروع کن.",
                          "en": "❌ These fields are missing: {fields}\nPlease start over."},
    "saved_ok":         {"fa": "✅ کتاب با موفقیت ذخیره شد!\n🔖 شناسه: #{id}\n📘 {title}",
                          "en": "✅ Book saved successfully!\n🔖 ID: #{id}\n📘 {title}"},
    "save_error":       {"fa": "❌ خطا در ذخیره:\n{err}",                       "en": "❌ Error while saving:\n{err}"},
    "no_books":         {"fa": "📭 هنوز کتابی ثبت نشده.",                       "en": "📭 No books have been added yet."},
    "list_header":      {"fa": "📋 لیست کتاب‌ها:\n",                            "en": "📋 List of books:\n"},
    "stats_header":     {"fa": "📊 آمار کتابخانه",                              "en": "📊 Library Stats"},
    "unknown_field":    {"fa": "نامشخص",                                        "en": "Unknown"},
    "lang_fa":          {"fa": "🇮🇷 فارسی",                                     "en": "🇮🇷 Persian"},
    "lang_en":          {"fa": "🇬🇧 انگلیسی",                                   "en": "🇬🇧 English"},

    # دکمه‌های کیبورد اصلی پنل
    "btn_add":          {"fa": "➕ افزودن کتاب",           "en": "➕ Add Book"},
    "btn_list":         {"fa": "📋 لیست کتاب‌ها",          "en": "📋 Book List"},
    "btn_delete":       {"fa": "🗑 حذف کتاب",              "en": "🗑 Delete Book"},
    "btn_stats":        {"fa": "📊 آمار ادمین",            "en": "📊 Admin Stats"},
    "btn_exit":         {"fa": "🔙 خروج از پنل ادمین",     "en": "🔙 Exit Admin Panel"},
    "btn_cancel":       {"fa": "❌ لغو عملیات",            "en": "❌ Cancel Operation"},
    "btn_skip":         {"fa": "⏭ رد کردن",                "en": "⏭ Skip"},
    "btn_confirm_save": {"fa": "✅ تأیید و ذخیره",          "en": "✅ Confirm & Save"},
    "btn_confirm_no":   {"fa": "❌ لغو",                    "en": "❌ Cancel"},

    # کارت خلاصه
    "summary_title":    {"fa": "📋 خلاصه اطلاعات کتاب:\n",  "en": "📋 Book Summary:\n"},
    "summary_book":     {"fa": "📘 عنوان: {v}",  "en": "📘 Title: {v}"},
    "summary_author":   {"fa": "✍ نویسنده: {v}", "en": "✍ Author: {v}"},
    "summary_lang":     {"fa": "🌐 زبان: {v}",    "en": "🌐 Language: {v}"},
    "summary_field":    {"fa": "🧲 فیلد: {v}",    "en": "🧲 Field: {v}"},
    "summary_year":     {"fa": "📅 سال: {v}",     "en": "📅 Year: {v}"},
    "summary_edition":  {"fa": "🔖 ویرایش: {v}",  "en": "🔖 Edition: {v}"},
    "summary_desc":     {"fa": "📝 توضیحات: {v}", "en": "📝 Description: {v}"},
    "summary_file":     {"fa": "📁 فایل: {v}",    "en": "📁 File: {v}"},

    # دکمه شیشه‌ای ورود سریع به پنل (روی منوی اصلی کاربر ادمین)
    "open_panel_btn":   {"fa": "🛠 پنل ادمین", "en": "🛠 Admin Panel"},
}


def tr(key: str, lang: str, **kwargs) -> str:
    text = T[key][lang if lang in ("fa", "en") else DEFAULT_LANG]
    return text.format(**kwargs) if kwargs else text


# ─────────────────────────────
# کیبوردها
# ─────────────────────────────

def admin_keyboard(lang: str) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton(tr("btn_add", lang)),
        types.KeyboardButton(tr("btn_list", lang)),
    )
    kb.add(
        types.KeyboardButton(tr("btn_delete", lang)),
        types.KeyboardButton(tr("btn_stats", lang)),
    )
    kb.add(types.KeyboardButton(tr("btn_exit", lang)))
    return kb


def cancel_keyboard(lang: str) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton(tr("btn_cancel", lang)))
    return kb


def skip_cancel_keyboard(lang: str) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton(tr("btn_skip", lang)),
        types.KeyboardButton(tr("btn_cancel", lang)),
    )
    return kb


def lang_keyboard() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("🇮🇷 فارسی", callback_data="adm_lang:fa"),
        types.InlineKeyboardButton("🇬🇧 English", callback_data="adm_lang:en"),
    )
    return markup


def field_keyboard(lang: str) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    buttons = []
    for key, (label_fa, label_en) in database.PHYSICS_FIELDS.items():
        label = label_fa if lang == "fa" else label_en
        buttons.append(
            types.InlineKeyboardButton(label, callback_data=f"adm_field:{key}")
        )
    for i in range(0, len(buttons), 2):
        markup.row(*buttons[i:i + 2])
    return markup


def confirm_keyboard(lang: str) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(tr("btn_confirm_save", lang), callback_data="adm_confirm:yes"),
        types.InlineKeyboardButton(tr("btn_confirm_no", lang),   callback_data="adm_confirm:no"),
    )
    return markup


def open_panel_markup(lang: str) -> types.InlineKeyboardMarkup:
    """دکمه شیشه‌ای (inline) که در منوی اصلی کاربران ادمین نشون داده می‌شه."""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(tr("open_panel_btn", lang), callback_data="adm_open_panel"))
    return markup


# ─────────────────────────────
# خلاصه کتاب
# ─────────────────────────────

def summary_text(data: dict, lang: str) -> str:
    field_fa, field_en = database.PHYSICS_FIELDS.get(
        data.get("physics_field", ""), (tr("unknown_field", lang), tr("unknown_field", lang))
    )
    field_label = field_fa if lang == "fa" else field_en
    lang_label = tr("lang_fa", lang) if data.get("language") == "fa" else tr("lang_en", lang)

    lines = [
        tr("summary_title", lang),
        tr("summary_book", lang, v=data.get("title", "-")),
        tr("summary_author", lang, v=data.get("author", "-")),
        tr("summary_lang", lang, v=lang_label),
        tr("summary_field", lang, v=field_label),
        tr("summary_year", lang, v=data.get("year") or "-"),
        tr("summary_edition", lang, v=data.get("edition") or "-"),
        tr("summary_desc", lang, v=data.get("description") or "-"),
        tr("summary_file", lang, v=data.get("file_name", "-")),
    ]
    return "\n".join(lines)


# ─────────────────────────────
# ورود به پنل /admin (هم برای کامند و هم برای دکمه شیشه‌ای)
# ─────────────────────────────

def open_panel(bot, chat_id: int, user_id: int):
    lang = get_lang(user_id)
    admin_sessions.pop(user_id, None)
    bot.send_message(
        chat_id,
        tr("panel_title", lang),
        reply_markup=admin_keyboard(lang)
    )


def handle_admin_command(bot, message: types.Message):
    uid = message.from_user.id
    if not is_admin(uid):
        bot.send_message(message.chat.id, tr("no_access", get_lang(uid)))
        return
    open_panel(bot, message.chat.id, uid)


# ─────────────────────────────
# هندلر اصلی متن — از bot.py صدا زده میشه
# اگه True برگردونه یعنی پیام مال ادمینه و bot.py دیگه کاری نکنه
# ─────────────────────────────

def handle_admin_text(bot, message: types.Message) -> bool:
    uid  = message.from_user.id
    text = message.text.strip()

    if not is_admin(uid):
        return False

    lang = get_lang(uid)

    # ── لغو در هر جایی (هر دو زبان) ───────────────────────────────────
    if text in (T["btn_cancel"]["fa"], T["btn_cancel"]["en"]):
        admin_sessions.pop(uid, None)
        bot.send_message(message.chat.id, tr("cancelled", lang), reply_markup=admin_keyboard(lang))
        return True

    # ── دکمه‌های منوی اصلی ادمین (هر دو زبان) ─────────────────────────
    if text in (T["btn_add"]["fa"], T["btn_add"]["en"]):
        _start_add(bot, message, lang)
        return True

    if text in (T["btn_list"]["fa"], T["btn_list"]["en"]):
        _show_list(bot, message, lang)
        return True

    if text in (T["btn_delete"]["fa"], T["btn_delete"]["en"]):
        admin_sessions[uid] = {"step": "wait_delete_id"}
        bot.send_message(
            message.chat.id,
            tr("ask_book_id_del", lang),
            reply_markup=cancel_keyboard(lang)
        )
        return True

    if text in (T["btn_stats"]["fa"], T["btn_stats"]["en"]):
        _show_stats(bot, message, lang)
        return True

    if text in (T["btn_exit"]["fa"], T["btn_exit"]["en"]):
        admin_sessions.pop(uid, None)
        return False   # به bot.py میگیم کیبورد اصلی رو نشون بده

    # ── مراحل جاری session ──────────────────────────────────────────
    if uid not in admin_sessions:
        return False

    step = admin_sessions[uid].get("step", "")

    # --- مرحله عنوان
    if step == "wait_title":
        admin_sessions[uid]["data"]["title"] = text
        admin_sessions[uid]["step"] = "wait_author"
        bot.send_message(message.chat.id, tr("ask_author", lang), reply_markup=cancel_keyboard(lang))
        return True

    # --- مرحله نویسنده
    if step == "wait_author":
        admin_sessions[uid]["data"]["author"] = text
        admin_sessions[uid]["step"] = "wait_language"
        bot.send_message(message.chat.id, tr("ask_lang", lang), reply_markup=cancel_keyboard(lang))
        bot.send_message(message.chat.id, "👇", reply_markup=lang_keyboard())
        return True

    # --- مرحله سال (زبان و فیلد از callback میان)
    if step == "wait_year":
        if text == tr("btn_skip", lang):
            admin_sessions[uid]["data"]["year"] = None
        else:
            try:
                admin_sessions[uid]["data"]["year"] = int(text)
            except ValueError:
                bot.send_message(message.chat.id, tr("year_not_number", lang))
                return True
        admin_sessions[uid]["step"] = "wait_edition"
        bot.send_message(
            message.chat.id,
            tr("ask_edition", lang),
            reply_markup=skip_cancel_keyboard(lang)
        )
        return True

    # --- مرحله ویرایش
    if step == "wait_edition":
        admin_sessions[uid]["data"]["edition"] = "" if text == tr("btn_skip", lang) else text
        admin_sessions[uid]["step"] = "wait_desc"
        bot.send_message(
            message.chat.id,
            tr("ask_desc", lang),
            reply_markup=skip_cancel_keyboard(lang)
        )
        return True

    # --- مرحله توضیحات
    if step == "wait_desc":
        admin_sessions[uid]["data"]["description"] = "" if text == tr("btn_skip", lang) else text
        admin_sessions[uid]["step"] = "confirm"
        data = admin_sessions[uid]["data"]
        bot.send_message(
            message.chat.id,
            summary_text(data, lang),
            reply_markup=admin_keyboard(lang)   # کیبورد عادی ادمین، تأیید از inline
        )
        bot.send_message(message.chat.id, tr("confirm_question", lang), reply_markup=confirm_keyboard(lang))
        return True

    # --- مرحله حذف کتاب
    if step == "wait_delete_id":
        try:
            book_id = int(text)
        except ValueError:
            bot.send_message(message.chat.id, tr("not_a_number", lang))
            return True
        book = database.get_book(book_id)
        if not book:
            bot.send_message(message.chat.id, tr("book_not_found", lang, id=book_id))
        else:
            database.delete_book(book_id)
            bot.send_message(message.chat.id, tr("book_deleted", lang, title=book["title"]))
        admin_sessions.pop(uid, None)
        bot.send_message(message.chat.id, tr("back_to_panel", lang), reply_markup=admin_keyboard(lang))
        return True

    return False


# ─────────────────────────────
# هندلر فایل PDF
# ─────────────────────────────

def handle_admin_document(bot, message: types.Message) -> bool:
    uid = message.from_user.id
    if not is_admin(uid):
        return False
    if uid not in admin_sessions:
        return False
    if admin_sessions[uid].get("step") != "wait_file":
        return False

    lang = get_lang(uid)
    doc = message.document
    if not doc.file_name.lower().endswith(".pdf"):
        bot.send_message(message.chat.id, tr("pdf_only", lang))
        return True

    admin_sessions[uid]["data"]["file_id"]   = doc.file_id
    admin_sessions[uid]["data"]["file_name"] = doc.file_name
    admin_sessions[uid]["data"]["file_size"] = doc.file_size
    admin_sessions[uid]["step"] = "wait_title"

    bot.send_message(
        message.chat.id,
        tr("file_received", lang, name=doc.file_name),
        reply_markup=cancel_keyboard(lang)
    )
    return True


# ─────────────────────────────
# هندلر callback های ادمین
# ─────────────────────────────

def handle_admin_callback(bot, callback: types.CallbackQuery) -> bool:
    uid  = callback.from_user.id
    data = callback.data

    if not is_admin(uid):
        return False

    lang = get_lang(uid)

    # دکمه شیشه‌ای ورود سریع به پنل از منوی اصلی
    if data == "adm_open_panel":
        bot.answer_callback_query(callback.id)
        open_panel(bot, callback.message.chat.id, uid)
        return True

    # انتخاب زبان کتاب
    if data.startswith("adm_lang:"):
        if uid not in admin_sessions or admin_sessions[uid].get("step") != "wait_language":
            bot.answer_callback_query(callback.id, tr("wrong_step", lang))
            return True
        book_lang = data.split(":")[1]
        admin_sessions[uid]["data"]["language"] = book_lang
        admin_sessions[uid]["step"] = "wait_field"
        bot.answer_callback_query(callback.id, "✅")
        bot.send_message(callback.message.chat.id, tr("ask_field", lang), reply_markup=cancel_keyboard(lang))
        bot.send_message(callback.message.chat.id, "👇", reply_markup=field_keyboard(lang))
        return True

    # انتخاب فیلد
    if data.startswith("adm_field:"):
        if uid not in admin_sessions or admin_sessions[uid].get("step") != "wait_field":
            bot.answer_callback_query(callback.id, tr("wrong_step", lang))
            return True
        field_key = data.split(":", 1)[1]
        admin_sessions[uid]["data"]["physics_field"] = field_key
        admin_sessions[uid]["step"] = "wait_year"
        bot.answer_callback_query(callback.id, "✅")
        bot.send_message(
            callback.message.chat.id,
            tr("ask_year", lang),
            reply_markup=skip_cancel_keyboard(lang)
        )
        return True

    # تأیید نهایی
    if data.startswith("adm_confirm:"):
        if uid not in admin_sessions or admin_sessions[uid].get("step") != "confirm":
            bot.answer_callback_query(callback.id, tr("wrong_step", lang))
            return True

        choice = data.split(":")[1]
        bot.answer_callback_query(callback.id)

        if choice == "no":
            admin_sessions.pop(uid)
            bot.send_message(callback.message.chat.id, tr("cancelled", lang), reply_markup=admin_keyboard(lang))
            return True

        d = admin_sessions[uid]["data"]

        # چک کن همه فیلدهای اجباری هستن
        required = ["file_id", "title", "author", "language", "physics_field"]
        missing  = [k for k in required if not d.get(k)]
        if missing:
            bot.send_message(
                callback.message.chat.id,
                tr("missing_fields", lang, fields=", ".join(missing)),
                reply_markup=admin_keyboard(lang)
            )
            admin_sessions.pop(uid, None)
            return True

        try:
            book_id = database.add_book(
                title        = d["title"],
                author       = d["author"],
                language     = d["language"],
                physics_field= d["physics_field"],
                file_id      = d["file_id"],
                file_name    = d["file_name"],
                file_size    = d["file_size"],
                description  = d.get("description", ""),
                edition      = d.get("edition", ""),
                year         = d.get("year"),
                added_by     = uid,
            )
            bot.send_message(
                callback.message.chat.id,
                tr("saved_ok", lang, id=book_id, title=d["title"]),
                reply_markup=admin_keyboard(lang)
            )
        except Exception as e:
            bot.send_message(
                callback.message.chat.id,
                tr("save_error", lang, err=e),
                reply_markup=admin_keyboard(lang)
            )
        finally:
            admin_sessions.pop(uid, None)
        return True

    return False


# ─────────────────────────────
# توابع کمکی داخلی
# ─────────────────────────────

def _start_add(bot, message: types.Message, lang: str):
    uid = message.from_user.id
    admin_sessions[uid] = {"step": "wait_file", "data": {}}
    bot.send_message(
        message.chat.id,
        tr("send_pdf", lang),
        reply_markup=cancel_keyboard(lang)
    )


def _show_list(bot, message: types.Message, lang: str):
    rows = database.search_books(limit=30)
    if not rows:
        bot.send_message(message.chat.id, tr("no_books", lang), reply_markup=admin_keyboard(lang))
        return
    lines = [tr("list_header", lang)]
    for b in rows:
        lines.append(f"#{b['id']} — {b['title']} | {b['author']} | ⬇️{b['download_count']}")
    bot.send_message(message.chat.id, "\n".join(lines), reply_markup=admin_keyboard(lang))


def _show_stats(bot, message: types.Message, lang: str):
    s = database.get_library_stats()
    header = tr("stats_header", lang)
    if lang == "fa":
        text = (
            f"{header}\n\n"
            f"📚 کل کتاب‌ها: {s['total_books']}\n"
            f"🇮🇷 فارسی: {s['fa_books']}\n"
            f"🇬🇧 انگلیسی: {s['en_books']}\n"
            f"⬇️ کل دانلودها: {s['total_downloads']}\n"
            f"🧲 فیلدهای فعال: {s['unique_fields']}"
        )
    else:
        text = (
            f"{header}\n\n"
            f"📚 Total Books: {s['total_books']}\n"
            f"🇮🇷 Persian: {s['fa_books']}\n"
            f"🇬🇧 English: {s['en_books']}\n"
            f"⬇️ Total Downloads: {s['total_downloads']}\n"
            f"🧲 Active Fields: {s['unique_fields']}"
        )
    bot.send_message(message.chat.id, text, reply_markup=admin_keyboard(lang))
