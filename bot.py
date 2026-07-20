import os
import telebot
from telebot import types
import database
import admin

TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

database.init_db()


user_langs: dict[int, str] = {}
waiting_search: set[int] = set()

# texts (FA / EN)

TEXTS = {
    "start": {
        "fa": "📚 به ربات کتابخانه فیزیک خوش اومدی!\nاز دکمه‌های پایین استفاده کن 👇",
        "en": "📚 Welcome to the Physics Library Bot!\nUse the buttons below 👇"
    },
    "no_books": {
        "fa": "📭 هنوز کتابی ثبت نشده.",
        "en": "📭 No books found."
    },
    "search_prompt": {
        "fa": "🔍 کلمه‌ای که می‌خوای جستجو کنی رو بنویس:",
        "en": "🔍 Type the keyword you want to search:"
    },
    "not_found": {
        "fa": "🔍 نتیجه‌ای پیدا نشد.",
        "en": "🔍 No results found."
    },
    "download": {
        "fa": "📥 دریافت",
        "en": "📥 Download"
    },
    "downloaded": {
        "fa": "✅ دانلود ثبت شد",
        "en": "✅ Download recorded"
    },
    "book_missing": {
        "fa": "❌ کتاب پیدا نشد.",
        "en": "❌ Book not found."
    },
    "lang_changed_fa": {
        "fa": "🌐 زبان به فارسی تغییر کرد.",
        "en": "🌐 Language changed to Persian (FA)."
    },
    "lang_changed_en": {
        "fa": "🌐 Language changed to English.",
        "en": "🌐 Language changed to English."
    },
    "fields_header": {
        "fa": "🧲 فیلدهای فیزیک موجود در کتابخانه:\nروی هر فیلد بزن تا کتاب‌هاش رو ببینی 👇",
        "en": "🧲 Available physics fields:\nTap a field to see its books 👇"
    },
    "no_fields": {
        "fa": "📭 هنوز هیچ کتابی اضافه نشده.",
        "en": "📭 No books have been added yet."
    },
    "stats_header": {
        "fa": "📊 آمار کتابخانه",
        "en": "📊 Library Statistics"
    },
    "top_books_header": {
        "fa": "🏆 پرطرفدارترین کتاب‌ها",
        "en": "🏆 Top Downloaded Books"
    },
    "books_list_header": {
        "fa": "📚 لیست کتاب‌ها — روی هر کتاب بزن تا مشخصاتش و لینک دانلودش رو ببینی 👇",
        "en": "📚 Book list — tap a book to see its details and download link 👇"
    },
    "help": {
        "fa": (
            "📖 راهنما:\n\n"
            "📚 همه کتاب‌ها ← لیست ۲۰ کتاب اخیر\n"
            "🔍 جستجو ← جستجو در عنوان و نویسنده\n"
            "🧲 فیلدهای فیزیک ← مرور بر اساس موضوع\n"
            "📊 آمار ← آمار کلی کتابخانه\n"
            "🏆 پرطرفدارها ← کتاب‌های بیشتر دانلودشده\n"
            "🌐 تغییر زبان ← سوئیچ FA / EN\n"
            "⚠️ هرجایی از ربات گیر کردید از /start استفاده کنید"
        ),
        "en": (
            "📖 Help:\n\n"
            "📚 All Books ← List of 20 recent books\n"
            "🔍 Search ← Search by title or author\n"
            "🧲 Physics Fields ← Browse by topic\n"
            "📊 Stats ← Library statistics\n"
            "🏆 Top Books ← Most downloaded\n"
            "🌐 Change Language ← Switch FA / EN\n"
            "⚠️ Bot isn't respond?! Use /start "
        )
    }
}

# Main Buttons

BTN = {
    "books":   {"fa": "📚 همه کتاب‌ها",    "en": "📚 All Books"},
    "search":  {"fa": "🔍 جستجو",          "en": "🔍 Search"},
    "fields":  {"fa": "🧲 فیلدهای فیزیک", "en": "🧲 Physics Fields"},
    "stats":   {"fa": "📊 آمار",           "en": "📊 Stats"},
    "top":     {"fa": "🏆 پرطرفدارها",    "en": "🏆 Top Books"},
    "lang":    {"fa": "🌐 English",        "en": "🌐 فارسی"},
    "help":    {"fa": "❓ راهنما",         "en": "❓ Help"},
}



# helpers
def get_lang(user: types.User) -> str:
    uid = user.id
    if uid in user_langs:
        return user_langs[uid]


    lang = database.get_user_lang(uid)
    if not lang:
        lang = "fa" if (user.language_code or "").startswith("fa") else "en"
        database.set_user_lang(uid, lang)

    user_langs[uid] = lang
    return lang


def t(user: types.User, key: str) -> str:
    return TEXTS[key][get_lang(user)]


def btn(user: types.User, key: str) -> str:
    return BTN[key][get_lang(user)]


def main_keyboard(user: types.User) -> types.ReplyKeyboardMarkup:
    """کیبورد اصلی ثابت پایین صفحه."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton(btn(user, "books")),
        types.KeyboardButton(btn(user, "search")),
    )
    kb.add(
        types.KeyboardButton(btn(user, "fields")),
        types.KeyboardButton(btn(user, "stats")),
    )
    kb.add(
        types.KeyboardButton(btn(user, "top")),
        types.KeyboardButton(btn(user, "lang")),
    )
    kb.add(
        types.KeyboardButton(btn(user, "help")),
    )
    return kb


def cancel_keyboard(user: types.User) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    lang = get_lang(user)
    kb.add(types.KeyboardButton("❌ لغو" if lang == "fa" else "❌ Cancel"))
    return kb


def send_home(chat_id: int, user: types.User):
    bot.send_message(
        chat_id,
        t(user, "start"),
        reply_markup=main_keyboard(user)
    )
    if admin.is_admin(user.id):
        lang = get_lang(user)
        bot.send_message(
            chat_id,
            admin.tr("open_panel_btn", lang),
            reply_markup=admin.open_panel_markup(lang)
        )


# /start
@bot.message_handler(commands=["start"])
def start(message: types.Message):
    user = message.from_user
    database.upsert_user(
        user_id=user.id,
        username=user.username or "",
        first_name=user.first_name or ""
    )
    send_home(message.chat.id, user)


# /admin
@bot.message_handler(commands=["admin"])
def admin_command(message: types.Message):
    admin.handle_admin_command(bot, message)

# /backup 
@bot.message_handler(commands=["backup"])
def backup_command(message: types.Message):
    admin.handle_backup_command(bot, message)

# /addadmin <id> 
@bot.message_handler(commands=["addadmin"])
def addadmin_command(message: types.Message):
    parts = message.text.split(maxsplit=1)
    args = parts[1] if len(parts) > 1 else ""
    admin.handle_addadmin_command(bot, message, args)

# /restore
@bot.message_handler(commands=["restore"])
def restore_command(message: types.Message):
    admin.handle_restore_command(bot, message)

# /cancel
@bot.message_handler(commands=["cancel"])
def cancel_command(message: types.Message):
    admin.handle_cancel_command(bot, message)



# document handler (ادمین آپلود PDF)

@bot.message_handler(content_types=["document"])
def document_handler(message: types.Message):
    admin.handle_admin_document(bot, message)


@bot.message_handler(func=lambda m: m.forward_from is not None, content_types=["text"])
def forward_handler(message: types.Message):
    if admin.handle_admin_forward(bot, message):
        return
    text_handler(message)


# admin callback
@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_"))
def admin_callback(callback: types.CallbackQuery):
    admin.handle_admin_callback(bot, callback)



@bot.message_handler(content_types=["text"])
def text_handler(message: types.Message):
    user = message.from_user
    text = message.text.strip()
    uid  = user.id
    if admin.handle_admin_text(bot, message):
        return

    # waiting search
    if uid in waiting_search:
        waiting_search.discard(uid)

        cancel_labels = {"❌ لغو", "❌ Cancel"}
        if text in cancel_labels:
            bot.send_message(
                message.chat.id,
                "↩️ لغو شد." if get_lang(user) == "fa" else "↩️ Cancelled.",
                reply_markup=main_keyboard(user)
            )
            return

        handle_search_query(message, text)
        return

    # main buttons
    all_btns = {BTN[k][l] for k in BTN for l in ("fa", "en")}

    if text == btn(user, "books"):
        handle_books(message)

    elif text == btn(user, "search"):
        waiting_search.add(uid)
        bot.send_message(
            message.chat.id,
            t(user, "search_prompt"),
            reply_markup=cancel_keyboard(user)
        )

    elif text == btn(user, "fields"):
        handle_fields(message)

    elif text == btn(user, "stats"):
        handle_stats(message)

    elif text == btn(user, "top"):
        handle_top(message)

    elif text == btn(user, "lang"):
        toggle_language(message)

    elif text == btn(user, "help"):
        bot.send_message(
            message.chat.id,
            t(user, "help"),
            reply_markup=main_keyboard(user)
        )

    
    elif text not in all_btns:
        send_home(message.chat.id, user)


# handlers
def handle_books(message: types.Message):
    #list of all books
    user = message.from_user
    rows = database.search_books(limit=20)
    send_book_list(message.chat.id, user, rows, header_key="books_list_header")


def handle_search_query(message: types.Message, query: str):
    user = message.from_user
    rows = database.search_books(query=query, limit=20)
    if not rows:
        bot.send_message(message.chat.id, t(user, "not_found"), reply_markup=main_keyboard(user))
        return
    send_book_list(message.chat.id, user, rows, header_key="books_list_header")
    bot.send_message(message.chat.id, "─" * 10, reply_markup=main_keyboard(user))


def handle_fields(message: types.Message):
    user = message.from_user
    lang = get_lang(user)

    buttons = []
    for field_key, (label_fa, label_en) in database.PHYSICS_FIELDS.items():
        label = label_fa if lang == "fa" else label_en
        buttons.append(
            types.InlineKeyboardButton(label, callback_data=f"field:{field_key}")
        )

    
    markup = types.InlineKeyboardMarkup()
    for i in range(0, len(buttons), 2):
        markup.row(*buttons[i:i+2])

    bot.send_message(
        message.chat.id,
        t(user, "fields_header"),
        reply_markup=markup
    )


def handle_stats(message: types.Message):
    user = message.from_user
    s = database.get_library_stats()
    lang = get_lang(user)

    if lang == "fa":
        text = (
            f"📊 آمار کتابخانه\n\n"
            f"📚 کل کتاب‌ها: {s['total_books']}\n"
            f"فارسی: {s['fa_books']}\n"
            f"انگلیسی: {s['en_books']}\n"
            f"⬇️ کل دانلودها: {s['total_downloads']}\n"
            f"🧲 فیلدهای فعال: {s['unique_fields']}"
        )
    else:
        text = (
            f"📊 Library Stats\n\n"
            f"📚 Total Books: {s['total_books']}\n"
            f"Persian: {s['fa_books']}\n"
            f"English: {s['en_books']}\n"
            f"⬇️ Total Downloads: {s['total_downloads']}\n"
            f"🧲 Active Fields: {s['unique_fields']}"
        )

    bot.send_message(message.chat.id, text, reply_markup=main_keyboard(user))


def handle_top(message: types.Message):
    user = message.from_user
    rows = database.get_top_downloads(limit=10)
    send_book_list(message.chat.id, user, rows, header_key="top_books_header")


def toggle_language(message: types.Message):
    user = message.from_user
    current = get_lang(user)
    new_lang = "en" if current == "fa" else "fa"
    user_langs[user.id] = new_lang
    database.set_user_lang(user.id, new_lang)

    key = "lang_changed_en" if new_lang == "en" else "lang_changed_fa"
    bot.send_message(
        message.chat.id,
        TEXTS[key][new_lang],
        reply_markup=main_keyboard(user)   
    )
    if admin.is_admin(user.id):
        bot.send_message(
            message.chat.id,
            admin.tr("open_panel_btn", new_lang),
            reply_markup=admin.open_panel_markup(new_lang)
        )


def send_book_list(chat_id: int, user: types.User, rows, header_key: str):
    if not rows:
        bot.send_message(chat_id, t(user, "no_books"), reply_markup=main_keyboard(user))
        return

    header = TEXTS[header_key][get_lang(user)]
    markup = types.InlineKeyboardMarkup()
    for book in rows:
        disp = database.get_display_id(book)
        label = f"{disp} — {book['title'][:40]}"
        markup.add(types.InlineKeyboardButton(label, callback_data=f"bookinfo:{book['id']}"))

    bot.send_message(chat_id, header, reply_markup=markup)


# Book Card
def send_book_card(chat_id: int, user: types.User, book):
    lang = get_lang(user)
    field_fa, field_en = database.PHYSICS_FIELDS.get(
        book["physics_field"], ("نامشخص", "Unknown")
    )
    field = field_fa if lang == "fa" else field_en
    lang_label = "فارسی" if book["language"] == "fa" else "English"
    disp = database.get_display_id(book)

    text = (
        f"📘 {book['title']}\n"
        f"✍ {book['author']}\n"
        f"🌐 {lang_label}\n"
        f"🧲 {field}\n"
        f"🔖 {disp}\n"
        f"⬇️ {book['download_count']}"
    )

    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton(
            t(user, "download"),
            callback_data=f"download:{book['id']}"
        )
    )
    bot.send_message(chat_id, text, reply_markup=markup)


# callback: book specs from list
@bot.callback_query_handler(func=lambda c: c.data.startswith("bookinfo:"))
def book_info(callback: types.CallbackQuery):
    user = callback.from_user
    book_id = int(callback.data.split(":")[1])
    book = database.get_book(book_id)

    if not book:
        bot.answer_callback_query(callback.id, t(user, "book_missing"), show_alert=True)
        return

    bot.answer_callback_query(callback.id)
    send_book_card(callback.message.chat.id, user, book)


# callback: download

@bot.callback_query_handler(func=lambda c: c.data.startswith("download:"))
def download(callback: types.CallbackQuery):
    user = callback.from_user
    book_id = int(callback.data.split(":")[1])
    book = database.get_book(book_id)

    if not book:
        bot.answer_callback_query(callback.id, t(user, "book_missing"))
        return

    bot.send_document(
        callback.message.chat.id,
        book["file_id"],
        caption=f"📘 {book['title']}"
    )
    database.record_download(book_id, user.id)
    bot.answer_callback_query(callback.id, t(user, "downloaded"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("field:"))
def field_books(callback: types.CallbackQuery):
    user = callback.from_user
    field_key = callback.data.split(":", 1)[1]
    rows = database.get_books_by_field(field_key, limit=20)

    if not rows:
        bot.answer_callback_query(callback.id, t(user, "no_books"), show_alert=True)
        return

    bot.answer_callback_query(callback.id)
    send_book_list(callback.message.chat.id, user, rows, header_key="books_list_header")



print("Bot is running...")
bot.infinity_polling()
