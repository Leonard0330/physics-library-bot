"""
admin.py / Admin's Panel
"""

import os
import sqlite3
import tempfile
import threading
import logging
from datetime import datetime

from telebot import types
import database

admin_sessions: dict[int, dict] = {}

DEFAULT_LANG = "fa"

# ── Backup / Restore state ──────────────────────────────────────────────────
_backup_restore_lock = threading.Lock()   # فقط یک عملیات هم‌زمان
_restore_sessions: dict[int, dict] = {}  # uid → {expire, emergency_path}
RESTORE_TIMEOUT = 600                     # ثانیه (۱۰ دقیقه)


def is_admin(user_id: int) -> bool:
    return database.is_admin(user_id)


def get_lang(user_id: int) -> str:
    return database.get_user_lang(user_id) or DEFAULT_LANG



# Bilingual Texts
T = {
    "no_access":       {"fa": "⛔️ دسترسی ندارید.",                              "en": "⛔️ You don't have access."},
    "panel_title":      {"fa": "👨‍💼 پنل ادمین\nیکی از گزینه‌ها رو انتخاب کن:",   "en": "👨‍💼 Admin Panel\nChoose an option:"},
    "cancelled":        {"fa": "↩️ عملیات لغو شد.",                             "en": "↩️ Operation cancelled."},
    "ask_book_id_del":  {"fa": "🗑 شناسه کتاب رو بنویس (مثلاً REL-14 یا آیدی عددی):",
                          "en": "🗑 Enter the book ID (e.g. REL-14 or the raw numeric ID):"},
    "ask_book_id_edit": {"fa": "✏️ شناسه کتابی که می‌خوای ویرایش کنی رو بنویس (مثلاً REL-14):",
                          "en": "✏️ Enter the ID of the book to edit (e.g. REL-14):"},
    "not_a_number":     {"fa": "❗️ فرمت شناسه اشتباهه.",                        "en": "❗️ Invalid ID format."},
    "book_not_found":   {"fa": "❌ کتابی با شناسه {id} پیدا نشد.",               "en": "❌ No book found with ID {id}."},
    "book_deleted":     {"fa": "🗑 کتاب «{title}» ({disp}) حذف شد.",             "en": "🗑 Book \"{title}\" ({disp}) deleted."},
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
    "saved_ok":         {"fa": "✅ کتاب با موفقیت ذخیره شد!\n🔖 شناسه: {disp}\n📘 {title}",
                          "en": "✅ Book saved successfully!\n🔖 ID: {disp}\n📘 {title}"},
    "save_error":       {"fa": "❌ خطا در ذخیره:\n{err}",                       "en": "❌ Error while saving:\n{err}"},
    "no_books":         {"fa": "📭 هنوز کتابی ثبت نشده.",                       "en": "📭 No books have been added yet."},
    "list_header":      {"fa": "📋 لیست کتاب‌ها:\n",                            "en": "📋 List of books:\n"},
    "stats_header":     {"fa": "📊 آمار کتابخانه",                              "en": "📊 Library Stats"},
    "unknown_field":    {"fa": "نامشخص",                                        "en": "Unknown"},
    "lang_fa":          {"fa": "فارسی",                                     "en": "Persian"},
    "lang_en":          {"fa": "انگلیسی",                                   "en": "English"},

    # Main Panel Buttons
    "btn_add":          {"fa": "➕ افزودن کتاب",           "en": "➕ Add Book"},
    "btn_edit":         {"fa": "✏️ ویرایش کتاب",           "en": "✏️ Edit Book"},
    "btn_list":         {"fa": "📋 لیست کتاب‌ها",          "en": "📋 Book List"},
    "btn_delete":       {"fa": "🗑 حذف کتاب",              "en": "🗑 Delete Book"},
    "btn_stats":        {"fa": "📊 آمار ادمین",            "en": "📊 Admin Stats"},
    "btn_admins":       {"fa": "👥 مدیریت ادمین‌ها",       "en": "👥 Manage Admins"},
    "btn_exit":         {"fa": "🔙 خروج از پنل ادمین",     "en": "🔙 Exit Admin Panel"},
    "btn_cancel":       {"fa": "❌ لغو عملیات",            "en": "❌ Cancel Operation"},
    "btn_skip":         {"fa": "⏭ رد کردن",                "en": "⏭ Skip"},
    "btn_confirm_save": {"fa": "✅ تأیید و ذخیره",          "en": "✅ Confirm & Save"},
    "btn_confirm_no":   {"fa": "❌ لغو",                    "en": "❌ Cancel"},

    # Admin Management
    "btn_admin_add":    {"fa": "➕ افزودن ادمین",          "en": "➕ Add Admin"},
    "btn_admin_list":   {"fa": "📋 لیست ادمین‌ها",         "en": "📋 Admins"},
    "btn_admin_remove": {"fa": "➖ حذف ادمین",             "en": "➖ Remove Admin"},
    "btn_back":         {"fa": "🔙 بازگشت",                "en": "🔙 Back"},
    "admins_menu_title":{"fa": "👥 مدیریت ادمین‌ها:",       "en": "👥 Manage Admins:"},
    "ask_new_admin_id": {"fa": "🆔 آیدی عددی کاربر رو بنویس، یا پیامش رو فوروارد کن:\n"
                                "(آیدی عددی رو می‌تونه با بات @userinfobot بگیره)",
                          "en": "🆔 Enter the user's numeric ID, or forward a message from them:\n"
                                "(they can get their numeric ID from @userinfobot)"},
    "ask_remove_admin_id": {"fa": "🆔 آیدی عددی ادمینی که می‌خوای حذف کنی رو بنویس:",
                             "en": "🆔 Enter the numeric ID of the admin to remove:"},
    "admin_added":      {"fa": "✅ کاربر {id} حالا ادمینه.",                     "en": "✅ User {id} is now an admin."},
    "admin_removed":    {"fa": "✅ دسترسی ادمین کاربر {id} حذف شد.",             "en": "✅ Admin access removed for user {id}."},
    "admin_already":    {"fa": "ℹ️ این کاربر از قبل ادمین بود.",                 "en": "ℹ️ This user was already an admin."},
    "cannot_remove_self": {"fa": "⚠️ نمی‌تونی دسترسی ادمین خودت رو از همینجا حذف کنی.",
                            "en": "⚠️ You can't remove your own admin access from here."},
    "admins_list_header": {"fa": "👥 ادمین‌های فعلی:\n",                        "en": "👥 Current admins:\n"},
    "no_admins":        {"fa": "📭 هیچ ادمینی ثبت نشده (این عجیبه!).",           "en": "📭 No admins found (that's odd!)."},

    # Edit Books
    "edit_found":       {"fa": "کتاب پیدا شد:\n\n{summary}\n\nکدوم فیلد رو می‌خوای ویرایش کنی؟",
                          "en": "Book found:\n\n{summary}\n\nWhich field do you want to edit?"},
    "edit_field_title":    {"fa": "📘 عنوان",   "en": "📘 Title"},
    "edit_field_author":   {"fa": "✍ نویسنده",  "en": "✍ Author"},
    "edit_field_year":     {"fa": "📅 سال",     "en": "📅 Year"},
    "edit_field_edition":  {"fa": "🔖 ویرایش",  "en": "🔖 Edition"},
    "edit_field_desc":     {"fa": "📝 توضیحات", "en": "📝 Description"},
    "edit_field_physics":  {"fa": "🧲 فیلد فیزیکی", "en": "🧲 Physics Field"},
    "ask_new_value":    {"fa": "مقدار جدید رو بنویس:",                          "en": "Enter the new value:"},
    "edit_saved":       {"fa": "✅ کتاب {disp} به‌روزرسانی شد.",                 "en": "✅ Book {disp} updated."},

    # Summary
    "summary_title":    {"fa": "📋 خلاصه اطلاعات کتاب:\n",  "en": "📋 Book Summary:\n"},
    "summary_book":     {"fa": "📘 عنوان: {v}",  "en": "📘 Title: {v}"},
    "summary_author":   {"fa": "✍ نویسنده: {v}", "en": "✍ Author: {v}"},
    "summary_lang":     {"fa": "🌐 زبان: {v}",    "en": "🌐 Language: {v}"},
    "summary_field":    {"fa": "🧲 فیلد: {v}",    "en": "🧲 Field: {v}"},
    "summary_year":     {"fa": "📅 سال: {v}",     "en": "📅 Year: {v}"},
    "summary_edition":  {"fa": "🔖 ویرایش: {v}",  "en": "🔖 Edition: {v}"},
    "summary_desc":     {"fa": "📝 توضیحات: {v}", "en": "📝 Description: {v}"},
    "summary_file":     {"fa": "📁 فایل: {v}",    "en": "📁 File: {v}"},

    # admin panel button
    "open_panel_btn":   {"fa": "پنل ادمین", "en": "Admin Panel"},

    # Backup / Restore
    "backup_sending":   {"fa": "⏳ در حال ارسال بکاپ...",          "en": "⏳ Sending backup..."},
    "backup_caption":   {"fa": "💾 بکاپ دیتابیس — {time}",        "en": "💾 Database backup — {time}"},
    "backup_error":     {"fa": "❌ خطا در بکاپ: {err}",            "en": "❌ Backup error: {err}"},
    "backup_busy":      {"fa": "⚠️ یک عملیات بکاپ/ریستور در جریان است. کمی صبر کن.",
                          "en": "⚠️ A backup/restore operation is already running. Please wait."},
    "restore_prompt":   {"fa": "📤 فایل .db را ارسال کن.\n"
                                "برای لغو /cancel بزن.\n"
                                "⏱ تایم‌اوت: ۱۰ دقیقه.",
                          "en": "📤 Send the .db file.\n"
                                "Type /cancel to abort.\n"
                                "⏱ Timeout: 10 minutes."},
    "restore_bad_file": {"fa": "❌ فایل باید پسوند .db داشته باشد.",
                          "en": "❌ File must have a .db extension."},
    "restore_invalid":  {"fa": "❌ فایل SQLite معتبر نیست یا با schema پروژه سازگار نیست.",
                          "en": "❌ Not a valid SQLite file or incompatible schema."},
    "restore_confirm_prompt": {
        "fa": "⚠️ بکاپ اضطراری ساخته شد و برای ادمین‌ها ارسال شد.\n"
              "برای تأیید ریستور عبارت زیر را **عیناً** تایپ کن:\n\n"
              "`CONFIRM RESTORE`",
        "en": "⚠️ Emergency backup created and sent to admins.\n"
              "To confirm the restore, type **exactly**:\n\n"
              "`CONFIRM RESTORE`"},
    "restore_cancelled":{"fa": "↩️ ریستور لغو شد.",                "en": "↩️ Restore cancelled."},
    "restore_timeout":  {"fa": "⏱ تایم‌اوت ریستور. دوباره /restore بزن.",
                          "en": "⏱ Restore timed out. Run /restore again."},
    "restore_ok":       {"fa": "✅ ریستور با موفقیت انجام شد. ربات ادامه می‌دهد.",
                          "en": "✅ Restore successful. Bot continues running."},
    "restore_failed":   {"fa": "❌ ریستور ناموفق بود: {err}\nبکاپ اضطراری برگردانده شد.",
                          "en": "❌ Restore failed: {err}\nRolled back to emergency backup."},
    "restore_rollback_failed": {
        "fa": "🆘 rollback هم شکست خورد: {err}\nبکاپ اضطراری را نگه می‌داریم.",
        "en": "🆘 Rollback also failed: {err}\nKeeping the emergency backup file."},
    "restore_no_session":{"fa": "⚠️ هیچ ریستوری در انتظار تأیید نیست.",
                           "en": "⚠️ No pending restore to cancel."},
    "cancel_not_yours": {"fa": "⚠️ این ریستور متعلق به تو نیست.",
                          "en": "⚠️ This restore was not started by you."},
    "emergency_caption":{"fa": "🆘 بکاپ اضطراری قبل از ریستور — {time}",
                          "en": "🆘 Emergency backup before restore — {time}"},
}


def tr(key: str, lang: str, **kwargs) -> str:
    text = T[key][lang if lang in ("fa", "en") else DEFAULT_LANG]
    return text.format(**kwargs) if kwargs else text


def _disp(book) -> str:
    return database.get_display_id(book)


def _resolve_book(text: str):
    text = text.strip()
    if text.isdigit():
        return database.get_book(int(text))
    return database.find_book_by_display_id(text)

# Keyboards

def admin_keyboard(lang: str) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton(tr("btn_add", lang)),
        types.KeyboardButton(tr("btn_edit", lang)),
    )
    kb.add(
        types.KeyboardButton(tr("btn_list", lang)),
        types.KeyboardButton(tr("btn_delete", lang)),
    )
    kb.add(
        types.KeyboardButton(tr("btn_stats", lang)),
        types.KeyboardButton(tr("btn_admins", lang)),
    )
    kb.add(types.KeyboardButton(tr("btn_exit", lang)))
    return kb


def admins_menu_keyboard(lang: str) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton(tr("btn_admin_add", lang)),
        types.KeyboardButton(tr("btn_admin_remove", lang)),
    )
    kb.add(types.KeyboardButton(tr("btn_admin_list", lang)))
    kb.add(types.KeyboardButton(tr("btn_back", lang)))
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


def field_keyboard(lang: str, prefix: str = "adm_field") -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    buttons = []
    for key, (label_fa, label_en) in database.PHYSICS_FIELDS.items():
        label = label_fa if lang == "fa" else label_en
        buttons.append(
            types.InlineKeyboardButton(label, callback_data=f"{prefix}:{key}")
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


def edit_field_keyboard(lang: str) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(tr("edit_field_title", lang),   callback_data="adm_editfield:title"),
        types.InlineKeyboardButton(tr("edit_field_author", lang),  callback_data="adm_editfield:author"),
    )
    markup.row(
        types.InlineKeyboardButton(tr("edit_field_year", lang),    callback_data="adm_editfield:year"),
        types.InlineKeyboardButton(tr("edit_field_edition", lang), callback_data="adm_editfield:edition"),
    )
    markup.row(
        types.InlineKeyboardButton(tr("edit_field_desc", lang),    callback_data="adm_editfield:description"),
        types.InlineKeyboardButton(tr("edit_field_physics", lang), callback_data="adm_editfield:physics_field"),
    )
    return markup


def open_panel_markup(lang: str) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(tr("open_panel_btn", lang), callback_data="adm_open_panel"))
    return markup


# خلاصه کتاب

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


def _book_summary_text(book, lang: str) -> str:
    return summary_text({
        "title": book["title"],
        "author": book["author"],
        "language": book["language"],
        "physics_field": book["physics_field"],
        "year": book["year"],
        "edition": book["edition"],
        "description": book["description"],
        "file_name": book["file_name"],
    }, lang) + f"\n🔖 {_disp(book)}"


# /admin entrance

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


# /addadmin <id> 

def handle_addadmin_command(bot, message: types.Message, args: str):
    uid = message.from_user.id
    lang = get_lang(uid)
    if not is_admin(uid):
        bot.send_message(message.chat.id, tr("no_access", lang))
        return

    target_text = args.strip()
    target_id = None
    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
    elif target_text.isdigit():
        target_id = int(target_text)

    if not target_id:
        bot.send_message(message.chat.id, tr("ask_new_admin_id", lang))
        return

    _add_admin(bot, message.chat.id, target_id, lang)


def _add_admin(bot, chat_id: int, target_id: int, lang: str):
    was_admin = database.is_admin(target_id)
    database.set_admin(target_id, True)
    key = "admin_already" if was_admin else "admin_added"
    bot.send_message(chat_id, tr(key, lang, id=target_id))

# ── Backup helpers ────────────────────────────────────────────────────────

def _make_backup_file(suffix: str = "") -> str:
    """
    یک فایل بکاپ SQLite معتبر می‌سازد (Online Backup API) و مسیرش را برمی‌گرداند.
    فراخوان‌دهنده مسئول پاک‌کردن فایل موقت است.
    """
    db_path = database.DB_PATH
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"physics_library_backup_{timestamp}{suffix}.db"
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="tgbot_backup_")
    os.close(tmp_fd)
    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(tmp_path)
        with dst:
            src.backup(dst)          # WAL-safe Online Backup API
        src.close()
        dst.close()
    except Exception:
        os.unlink(tmp_path)
        raise
    return tmp_path, fname


def send_db_backup(bot, chat_id: int, lang: str, caption: str | None = None) -> bool:
    """بکاپ می‌سازد، ارسال می‌کند و فایل موقت را پاک می‌کند. True در موفقیت."""
    if caption is None:
        caption = tr("backup_caption", lang, time=datetime.now().strftime("%Y-%m-%d %H:%M"))
    try:
        tmp_path, fname = _make_backup_file()
    except Exception as e:
        bot.send_message(chat_id, tr("backup_error", lang, err=e))
        return False
    try:
        with open(tmp_path, "rb") as f:
            bot.send_document(chat_id, f, caption=caption, visible_file_name=fname)
        return True
    except Exception as e:
        bot.send_message(chat_id, tr("backup_error", lang, err=e))
        return False
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def broadcast_backup_to_admins(bot, caption_key: str, **caption_kwargs) -> str | None:
    """
    بکاپ را یک بار می‌سازد و برای همه ادمین‌ها ارسال می‌کند.
    شکست ارسال به یک ادمین، بقیه را متوقف نمی‌کند.
    مسیر فایل موقت را برمی‌گرداند (پاک‌سازی بر عهده فراخوان‌دهنده).
    """
    try:
        tmp_path, fname = _make_backup_file()
    except Exception as e:
        logging.error("broadcast_backup_to_admins: backup creation failed: %s", e)
        return None

    for a in database.list_admins():
        uid_a = a["user_id"]
        lang_a = get_lang(uid_a)
        caption = tr(caption_key, lang_a, **caption_kwargs)
        try:
            with open(tmp_path, "rb") as f:
                bot.send_document(uid_a, f, caption=caption, visible_file_name=fname)
        except Exception as e:
            logging.warning("broadcast_backup_to_admins: failed to send to %s: %s", uid_a, e)

    return tmp_path


def handle_backup_command(bot, message: types.Message):
    uid = message.from_user.id
    lang = get_lang(uid)
    if not is_admin(uid):
        bot.send_message(message.chat.id, tr("no_access", lang))
        return
    if not _backup_restore_lock.acquire(blocking=False):
        bot.send_message(message.chat.id, tr("backup_busy", lang))
        return
    try:
        bot.send_message(message.chat.id, tr("backup_sending", lang))
        # ارسال برای همه ادمین‌ها (شامل خود فرستنده)
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        tmp_path = broadcast_backup_to_admins(bot, "backup_caption", time=time_str)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    finally:
        _backup_restore_lock.release()


# ── Restore helpers ───────────────────────────────────────────────────────

def _validate_restore_db(path: str) -> bool:
    """فایل را بررسی می‌کند: SQLite معتبر و دارای جداول اصلی پروژه."""
    required_tables = {"books", "users", "download_logs"}
    try:
        conn = sqlite3.connect(path)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        return required_tables.issubset(tables)
    except Exception:
        return False


def _do_restore(bot, initiator_uid: int, tmp_db_path: str, emergency_path: str):
    """
    ریستور واقعی را انجام می‌دهد.
    در موفقیت emergency_path را پاک می‌کند.
    در شکست، rollback می‌کند.
    در شکست rollback، emergency_path را نگه می‌دارد.
    """
    lang = get_lang(initiator_uid)
    db_path = database.DB_PATH
    try:
        src = sqlite3.connect(tmp_db_path)
        dst = sqlite3.connect(db_path)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        bot.send_message(initiator_uid, tr("restore_ok", lang))
        try:
            os.unlink(tmp_db_path)
        except Exception:
            pass
        try:
            os.unlink(emergency_path)
        except Exception:
            pass
    except Exception as e:
        logging.error("_do_restore: restore failed: %s", e)
        bot.send_message(initiator_uid, tr("restore_failed", lang, err=e))
        # rollback با emergency backup
        try:
            src = sqlite3.connect(emergency_path)
            dst = sqlite3.connect(db_path)
            with dst:
                src.backup(dst)
            src.close()
            dst.close()
        except Exception as rb_err:
            logging.error("_do_restore: rollback failed: %s", rb_err)
            bot.send_message(initiator_uid, tr("restore_rollback_failed", lang, err=rb_err))
            # emergency_path را نگه می‌داریم — نپاک می‌کنیم
            return
        try:
            os.unlink(tmp_db_path)
        except Exception:
            pass


def handle_restore_command(bot, message: types.Message):
    uid = message.from_user.id
    lang = get_lang(uid)
    if not is_admin(uid):
        bot.send_message(message.chat.id, tr("no_access", lang))
        return
    if not _backup_restore_lock.acquire(blocking=False):
        bot.send_message(message.chat.id, tr("backup_busy", lang))
        return
    # lock را نگه می‌داریم تا پایان کل فرآیند (ارسال فایل → تأیید → اجرا)
    _restore_sessions[uid] = {
        "step": "wait_file",
        "expire": datetime.now().timestamp() + RESTORE_TIMEOUT,
        "emergency_path": None,
        "tmp_db_path": None,
    }
    bot.send_message(message.chat.id, tr("restore_prompt", lang))


def handle_cancel_command(bot, message: types.Message):
    uid = message.from_user.id
    lang = get_lang(uid)
    if not is_admin(uid):
        bot.send_message(message.chat.id, tr("no_access", lang))
        return
    if uid not in _restore_sessions:
        bot.send_message(message.chat.id, tr("restore_no_session", lang))
        return
    sess = _restore_sessions.pop(uid)
    # پاک‌کردن فایل‌های موقت
    for key in ("emergency_path", "tmp_db_path"):
        p = sess.get(key)
        if p:
            try:
                os.unlink(p)
            except Exception:
                pass
    _backup_restore_lock.release()
    bot.send_message(message.chat.id, tr("restore_cancelled", lang))


def handle_restore_document(bot, message: types.Message) -> bool:
    """
    وقتی ادمین‌ای در مرحله wait_file است و یک فایل ارسال می‌کند.
    در handle_admin_document فراخوانی می‌شود (قبل از چک PDF).
    True برمی‌گرداند اگر پیام را مصرف کرده باشد.
    """
    uid = message.from_user.id
    if uid not in _restore_sessions:
        return False
    sess = _restore_sessions[uid]
    if sess.get("step") != "wait_file":
        return False

    # بررسی تایم‌اوت
    if datetime.now().timestamp() > sess["expire"]:
        _restore_sessions.pop(uid)
        _backup_restore_lock.release()
        bot.send_message(message.chat.id, tr("restore_timeout", get_lang(uid)))
        return True

    lang = get_lang(uid)
    doc = message.document
    if not (doc.file_name or "").lower().endswith(".db"):
        bot.send_message(message.chat.id, tr("restore_bad_file", lang))
        return True

    # دانلود فایل به temp
    try:
        file_info = bot.get_file(doc.file_id)
        downloaded = bot.download_file(file_info.file_path)
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="tgbot_restore_")
        os.write(tmp_fd, downloaded)
        os.close(tmp_fd)
    except Exception as e:
        bot.send_message(message.chat.id, tr("backup_error", lang, err=e))
        return True

    # اعتبارسنجی schema
    if not _validate_restore_db(tmp_path):
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        bot.send_message(message.chat.id, tr("restore_invalid", lang))
        return True

    # بکاپ اضطراری
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    emergency_tmp = broadcast_backup_to_admins(
        bot, "emergency_caption", time=time_str
    )
    # نگه‌داشتن emergency برای rollback — یک نسخه محلی جداگانه
    try:
        em_fd, em_path = tempfile.mkstemp(suffix=".db", prefix="tgbot_emergency_")
        os.close(em_fd)
        src = sqlite3.connect(database.DB_PATH)
        dst = sqlite3.connect(em_path)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
    except Exception as e:
        logging.error("handle_restore_document: emergency backup failed: %s", e)
        em_path = None

    if emergency_tmp:
        try:
            os.unlink(emergency_tmp)
        except Exception:
            pass

    sess["step"] = "wait_confirm"
    sess["tmp_db_path"] = tmp_path
    sess["emergency_path"] = em_path
    bot.send_message(message.chat.id, tr("restore_confirm_prompt", lang), parse_mode="Markdown")
    return True


def handle_restore_confirm_text(bot, message: types.Message) -> bool:
    """
    در handle_admin_text فراخوانی می‌شود تا CONFIRM RESTORE را بررسی کند.
    True برمی‌گرداند اگر پیام را مصرف کرده باشد.
    """
    uid = message.from_user.id
    if uid not in _restore_sessions:
        return False
    sess = _restore_sessions[uid]
    if sess.get("step") != "wait_confirm":
        return False

    # بررسی تایم‌اوت
    if datetime.now().timestamp() > sess["expire"]:
        _restore_sessions.pop(uid)
        _backup_restore_lock.release()
        bot.send_message(message.chat.id, tr("restore_timeout", get_lang(uid)))
        return True

    text = message.text.strip()
    lang = get_lang(uid)

    if text != "CONFIRM RESTORE":
        # لغو — هر چیزی غیر از عبارت دقیق
        _restore_sessions.pop(uid)
        for key in ("emergency_path", "tmp_db_path"):
            p = sess.get(key)
            if p:
                try:
                    os.unlink(p)
                except Exception:
                    pass
        _backup_restore_lock.release()
        bot.send_message(message.chat.id, tr("restore_cancelled", lang))
        return True

    # اجرای ریستور
    tmp_db_path = sess.pop("tmp_db_path", None)
    emergency_path = sess.pop("emergency_path", None)
    _restore_sessions.pop(uid)
    try:
        _do_restore(bot, uid, tmp_db_path, emergency_path)
    finally:
        _backup_restore_lock.release()
    return True

def handle_admin_text(bot, message: types.Message) -> bool:
    uid  = message.from_user.id
    text = message.text.strip()

    if not is_admin(uid):
        return False

    # بررسی تأیید ریستور (CONFIRM RESTORE یا لغو ضمنی)
    if handle_restore_confirm_text(bot, message):
        return True

    lang = get_lang(uid)

    
    if text in (T["btn_cancel"]["fa"], T["btn_cancel"]["en"]):
        admin_sessions.pop(uid, None)
        bot.send_message(message.chat.id, tr("cancelled", lang), reply_markup=admin_keyboard(lang))
        return True

    if text in (T["btn_back"]["fa"], T["btn_back"]["en"]):
        admin_sessions.pop(uid, None)
        bot.send_message(message.chat.id, tr("panel_title", lang), reply_markup=admin_keyboard(lang))
        return True

    if text in (T["btn_add"]["fa"], T["btn_add"]["en"]):
        _start_add(bot, message, lang)
        return True

    if text in (T["btn_edit"]["fa"], T["btn_edit"]["en"]):
        admin_sessions[uid] = {"step": "wait_edit_id"}
        bot.send_message(message.chat.id, tr("ask_book_id_edit", lang), reply_markup=cancel_keyboard(lang))
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

    if text in (T["btn_admins"]["fa"], T["btn_admins"]["en"]):
        admin_sessions.pop(uid, None)
        bot.send_message(message.chat.id, tr("admins_menu_title", lang), reply_markup=admins_menu_keyboard(lang))
        return True

    if text in (T["btn_admin_add"]["fa"], T["btn_admin_add"]["en"]):
        admin_sessions[uid] = {"step": "wait_new_admin_id"}
        bot.send_message(message.chat.id, tr("ask_new_admin_id", lang), reply_markup=cancel_keyboard(lang))
        return True

    if text in (T["btn_admin_remove"]["fa"], T["btn_admin_remove"]["en"]):
        admin_sessions[uid] = {"step": "wait_remove_admin_id"}
        bot.send_message(message.chat.id, tr("ask_remove_admin_id", lang), reply_markup=cancel_keyboard(lang))
        return True

    if text in (T["btn_admin_list"]["fa"], T["btn_admin_list"]["en"]):
        _show_admins(bot, message, lang)
        return True

    if text in (T["btn_exit"]["fa"], T["btn_exit"]["en"]):
        admin_sessions.pop(uid, None)
        return False   

    if uid not in admin_sessions:
        return False

    step = admin_sessions[uid].get("step", "")

    # Title
    if step == "wait_title":
        admin_sessions[uid]["data"]["title"] = text
        admin_sessions[uid]["step"] = "wait_author"
        bot.send_message(message.chat.id, tr("ask_author", lang), reply_markup=cancel_keyboard(lang))
        return True

    # Author
    if step == "wait_author":
        admin_sessions[uid]["data"]["author"] = text
        admin_sessions[uid]["step"] = "wait_language"
        bot.send_message(message.chat.id, tr("ask_lang", lang), reply_markup=cancel_keyboard(lang))
        bot.send_message(message.chat.id, "👇", reply_markup=lang_keyboard())
        return True

    # Callback
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

    # Edition
    if step == "wait_edition":
        admin_sessions[uid]["data"]["edition"] = "" if text == tr("btn_skip", lang) else text
        admin_sessions[uid]["step"] = "wait_desc"
        bot.send_message(
            message.chat.id,
            tr("ask_desc", lang),
            reply_markup=skip_cancel_keyboard(lang)
        )
        return True

    # Descrption / Comment
    if step == "wait_desc":
        admin_sessions[uid]["data"]["description"] = "" if text == tr("btn_skip", lang) else text
        admin_sessions[uid]["step"] = "confirm"
        data = admin_sessions[uid]["data"]
        bot.send_message(
            message.chat.id,
            summary_text(data, lang),
            reply_markup=admin_keyboard(lang)   
        )
        bot.send_message(message.chat.id, tr("confirm_question", lang), reply_markup=confirm_keyboard(lang))
        return True

    # Delete Book
    if step == "wait_delete_id":
        book = _resolve_book(text)
        if not book:
            bot.send_message(message.chat.id, tr("book_not_found", lang, id=text))
        else:
            disp = _disp(book)
            database.delete_book(book["id"])
            bot.send_message(message.chat.id, tr("book_deleted", lang, title=book["title"], disp=disp))
        admin_sessions.pop(uid, None)
        bot.send_message(message.chat.id, tr("back_to_panel", lang), reply_markup=admin_keyboard(lang))
        return True

    if step == "wait_edit_id":
        book = _resolve_book(text)
        if not book:
            bot.send_message(message.chat.id, tr("book_not_found", lang, id=text))
            admin_sessions.pop(uid, None)
            bot.send_message(message.chat.id, tr("back_to_panel", lang), reply_markup=admin_keyboard(lang))
            return True
        admin_sessions[uid] = {"step": "edit_choose_field", "book_id": book["id"]}
        bot.send_message(
            message.chat.id,
            tr("edit_found", lang, summary=_book_summary_text(book, lang)),
            reply_markup=admin_keyboard(lang)
        )
        bot.send_message(message.chat.id, "👇", reply_markup=edit_field_keyboard(lang))
        return True

    if step == "wait_edit_value":
        field = admin_sessions[uid]["edit_field"]
        book_id = admin_sessions[uid]["book_id"]
        value: object = text

        if field == "year":
            if text == tr("btn_skip", lang):
                value = None
            else:
                try:
                    value = int(text)
                except ValueError:
                    bot.send_message(message.chat.id, tr("year_not_number", lang))
                    return True

        database.update_book(book_id, **{field: value})
        book = database.get_book(book_id)
        admin_sessions.pop(uid, None)
        bot.send_message(
            message.chat.id,
            tr("edit_saved", lang, disp=_disp(book)),
            reply_markup=admin_keyboard(lang)
        )
        return True

    # new admin
    if step == "wait_new_admin_id":
        if not text.isdigit():
            bot.send_message(message.chat.id, tr("not_a_number", lang))
            return True
        _add_admin(bot, message.chat.id, int(text), lang)
        admin_sessions.pop(uid, None)
        bot.send_message(message.chat.id, tr("admins_menu_title", lang), reply_markup=admins_menu_keyboard(lang))
        return True

    # delete admin
    if step == "wait_remove_admin_id":
        if not text.isdigit():
            bot.send_message(message.chat.id, tr("not_a_number", lang))
            return True
        target_id = int(text)
        if target_id == uid:
            bot.send_message(message.chat.id, tr("cannot_remove_self", lang))
        else:
            database.set_admin(target_id, False)
            bot.send_message(message.chat.id, tr("admin_removed", lang, id=target_id))
        admin_sessions.pop(uid, None)
        bot.send_message(message.chat.id, tr("admins_menu_title", lang), reply_markup=admins_menu_keyboard(lang))
        return True

    return False


def handle_admin_forward(bot, message: types.Message) -> bool:
    uid = message.from_user.id
    if not is_admin(uid):
        return False
    if uid not in admin_sessions or admin_sessions[uid].get("step") != "wait_new_admin_id":
        return False
    if not message.forward_from:
        return False  

    lang = get_lang(uid)
    target_id = message.forward_from.id
    _add_admin(bot, message.chat.id, target_id, lang)
    admin_sessions.pop(uid, None)
    bot.send_message(message.chat.id, tr("admins_menu_title", lang), reply_markup=admins_menu_keyboard(lang))
    return True


# PDF Handler

def handle_admin_document(bot, message: types.Message) -> bool:
    uid = message.from_user.id
    if not is_admin(uid):
        return False
    # ابتدا restore session را بررسی می‌کنیم
    if handle_restore_document(bot, message):
        return True
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


# admin callback handler

def handle_admin_callback(bot, callback: types.CallbackQuery) -> bool:
    uid  = callback.from_user.id
    data = callback.data

    if not is_admin(uid):
        return False

    lang = get_lang(uid)

    
    if data == "adm_open_panel":
        bot.answer_callback_query(callback.id)
        open_panel(bot, callback.message.chat.id, uid)
        return True

    
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
            book = database.get_book(book_id)
            bot.send_message(
                callback.message.chat.id,
                tr("saved_ok", lang, disp=_disp(book), title=d["title"]),
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

    
    if data.startswith("adm_editfield:"):
        if uid not in admin_sessions or admin_sessions[uid].get("step") != "edit_choose_field":
            bot.answer_callback_query(callback.id, tr("wrong_step", lang))
            return True
        field = data.split(":", 1)[1]
        bot.answer_callback_query(callback.id)

        if field == "physics_field":
            admin_sessions[uid]["step"] = "wait_edit_new_field"
            bot.send_message(callback.message.chat.id, tr("ask_field", lang), reply_markup=cancel_keyboard(lang))
            bot.send_message(callback.message.chat.id, "👇", reply_markup=field_keyboard(lang, prefix="adm_editfieldval"))
            return True

        admin_sessions[uid]["step"] = "wait_edit_value"
        admin_sessions[uid]["edit_field"] = field
        kb = skip_cancel_keyboard(lang) if field == "year" else cancel_keyboard(lang)
        bot.send_message(callback.message.chat.id, tr("ask_new_value", lang), reply_markup=kb)
        return True

    
    if data.startswith("adm_editfieldval:"):
        if uid not in admin_sessions or admin_sessions[uid].get("step") != "wait_edit_new_field":
            bot.answer_callback_query(callback.id, tr("wrong_step", lang))
            return True
        new_field = data.split(":", 1)[1]
        book_id = admin_sessions[uid]["book_id"]
        database.update_book(book_id, physics_field=new_field)
        book = database.get_book(book_id)
        bot.answer_callback_query(callback.id, "✅")
        admin_sessions.pop(uid, None)
        bot.send_message(
            callback.message.chat.id,
            tr("edit_saved", lang, disp=_disp(book)),
            reply_markup=admin_keyboard(lang)
        )
        return True

    return False

# more functions

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
        lines.append(f"{_disp(b)} — {b['title']} | {b['author']} | ⬇️{b['download_count']}")
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


def _show_admins(bot, message: types.Message, lang: str):
    rows = database.list_admins()
    if not rows:
        bot.send_message(message.chat.id, tr("no_admins", lang), reply_markup=admins_menu_keyboard(lang))
        return
    lines = [tr("admins_list_header", lang)]
    for a in rows:
        name = a["first_name"] or a["username"] or "-"
        lines.append(f"🆔 {a['user_id']} — {name}")
    bot.send_message(message.chat.id, "\n".join(lines), reply_markup=admins_menu_keyboard(lang))
