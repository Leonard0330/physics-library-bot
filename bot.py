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
        "fa": (
            "📚 به ربات کتابخانه فیزیک خوش آمدید!\n\n"
            "این ربات مجموعه‌ای منتخب از کتاب‌ها و مقالات علمی فیزیک را در شاخه‌های مختلف این علم در اختیار شما قرار می‌دهد.\n\n"
            "🔍 جستجو — جستجو در همه منابع\n"
            "📂 کتابخانه  —  کتاب‌ها، مقالات و فیلدهای فیزیک\n"
            "🌐 زبان — تغییر زبان رابط\n\n"
            "برای راهنمای کامل: درباره ← راهنما 👇"
        ),
        "en": (
            "📚 Welcome to the Physics Library Bot!\n\n"
            "This bot provides a curated collection of physics books and research articles across multiple fields of physics.\n\n"
            "🔍 Search — Quickly find any book or article by title or keywords.\n"
            "📂 Browse — Explore the library by category, popularity, or recently added resources.\n"
            "🌐 Language — switch interface language\n\n"
            "For detailed instructions and additional information, open About → Help 👇"
        ),
    },
    "no_books": {
        "fa": "📭 هنوز منبعی ثبت نشده.",
        "en": "📭 No resources found."
    },
    "search_prompt": {
        "fa": "🔍 جستجو در همه منابع فارسی:\n"
        "کلمه کلیدی موردنظر خود را تایپ کنید:\n\n"
        "To search among Persian-language resources, change the language and then search again."
        "برای جستجو میان منابع انگلیسی زبان را تغییر دهید و سپس دوباره جستجو کنید",
        "en": "🔍 Search in all English resources:\n"
        "Type the keyword you want to search:\n\n"
        "برای جستجو میان منابع انگلیسی، زبان را تغییر دهید و سپس دوباره جستجو کنید."
        "To search among Persian-language resources, change the language and then search again."
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
        "fa": "❌ منبع پیدا نشد.",
        "en": "❌ Resource not found."
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
        "fa": "🌌 فیلدهای فیزیک:\nروی هر فیلد بزن تا منابعش رو ببینی 👇",
        "en": "🌌 Physics Fields:\nTap a field to see its resources 👇"
    },
    "no_fields": {
        "fa": "📭 هنوز هیچ منبعی اضافه نشده.",
        "en": "📭 No resources have been added yet."
    },
    "stats_header": {
        "fa": "📊 آمار کتابخانه",
        "en": "📊 Library Statistics"
    },
    "top_books_header": {
        "fa": "⭐ پرطرفدارترین منابع",
        "en": "⭐ Top Resources"
    },
    "books_list_header": {
        "fa": "📘 لیست کتاب‌ها — روی هر کتاب بزن 👇",
        "en": "📘 Books — tap to see details 👇"
    },
    "articles_list_header": {
        "fa": "📄 لیست مقالات — روی هر مقاله بزن 👇",
        "en": "📄 Articles — tap to see details 👇"
    },
    "resources_list_header": {
        "fa": "📋 نتایج جستجو — روی هر مورد بزن 👇",
        "en": "📋 Search results — tap to see details 👇"
    },
    "browse_header": {
        "fa": "📂 کتابخانه — یه گزینه انتخاب کن:",
        "en": "📂 Browse — choose an option:"
    },
    "browse_books_header": {
        "fa": "📘 کتاب‌ها — یه گزینه انتخاب کن:",
        "en": "📘 Books — choose an option:"
    },
    "browse_articles_header": {
        "fa": "📄 مقالات — یه گزینه انتخاب کن:",
        "en": "📄 Articles — choose an option:"
    },
    "about_header": {
        "fa": "ℹ️ درباره — یه گزینه انتخاب کن:",
        "en": "ℹ️ About — choose an option:"
    },
    "about_project": {
        "fa": (
            "درباره پروژه\n\n"
            "کتابخانه فیزیک یک ربات تلگرام است که با هدف فراهم کردن دسترسی آسان به مجموعه‌ای رو‌به‌رشد از کتاب‌ها و مقالات علمی فیزیک طراحی شده است.\n"
            "این کتابخانه طیف گسترده‌ای از شاخه‌های فیزیک، از مباحث پایه تا زمینه‌های تخصصی، را پوشش می‌دهد و تلاش می‌کند دانشجویان، پژوهشگران و علاقه‌مندان به فیزیک بتوانند منابع موردنیاز خود را به‌سادگی پیدا کنند.\n"
            "این پروژه به‌صورت مستمر در حال توسعه است و به مرور زمان کتاب‌ها و مقالات جدیدی به آن افزوده خواهند شد.\n\n"
            "📬 ارتباط و پشتیبانی: @Kimhmda0705\n"
            "Version: 2.0"
        ),
        "en": (
            "🔭 About the Project\n\n"
            "Physics Library is a Telegram bot designed to provide easy access to a growing collection of physics books and research articles.\n"
            "The library covers a wide range of topics, from foundational physics to specialized fields, and aims to help students, educators, and researchers quickly discover useful learning resources.The project is continuously expanding, with new books and articles being added over time.\n\n"
            "Thank you for using Physics Library and supporting its growth.\n"
            "📬 Contact & Support: @Kimhmda0705\n"
            "Version: 2.0"
        ),
    },
    "help": {
        "fa": (
            "📖 راهنما:\n\n"
            "🔍 جستجو ← جستجو در عنوان، نویسنده و همه منابع\n"
            "📂 کتابخانه ←  کتاب‌ها، مقالات، فیلدهای فیزیک، پرطرفدارها و جدیدترین‌ها\n"
            "   ↳ 📘 کتاب‌ها ← همه / فیلد / پرطرفدار / جدید\n"
            "   ↳ 📄 مقالات ← همه / فیلد / پرطرفدار / جدید\n"
            "   ↳ 🌌 فیلدهای فیزیک ← جستجو بر اساس موضوع\n"
            "   ↳ ⭐ پرطرفدارها ← پرطرفدارترین منابع\n"
            "   ↳ 🆕 جدیدترین‌ها ← آخرین منابع اضافه‌شده\n"
            "ℹ️ درباره ← راهنما / آمار / پرطرفدارها / درباره پروژه\n"
            "🌐 زبان ← سوئیچ FA / EN\n\n"
            "⚠️ هرجایی گیر کردی از /start استفاده کن"
        ),
        "en": (
            "📖 Help:\n\n"
            "🔍 Search ← search by title, author, across all resources\n"
            "📂 Browse ← books, articles, physics fields, popular & recent\n"
            "   ↳ 📘 Books ← All / Field / Popular / Recent\n"
            "   ↳ 📄 Articles ← All / Field / Popular / Recent\n"
            "   ↳ 🌌 Physics Fields ← browse by topic\n"
            "   ↳ ⭐ Top Resources ← most downloaded\n"
            "   ↳ 🆕 Recently Added ← latest resources\n"
            "ℹ️ About ← Help / Stats / Top / About Project\n"
            "🌐 Language ← switch FA / EN\n\n"
            "⚠️ Stuck? Use /start"
        ),
    },
}

# Main Buttons

BTN = {
    # Main menu (4 buttons)
    "search":  {"fa": "🔍 جستجو",          "en": "🔍 Search"},
    "browse":  {"fa": "📂 کتابخانه",           "en": "📂 Browse"},
    "about":   {"fa": "ℹ️ درباره",         "en": "ℹ️ About"},
    "lang":    {"fa": "🌐 English",        "en": "🌐 فارسی"},

    # Browse sub-menu inline buttons
    "b_books":    {"fa": "📘 کتاب‌ها",        "en": "📘 Books"},
    "b_articles": {"fa": "📄 مقالات",         "en": "📄 Articles"},
    "b_fields":   {"fa": "🌌 فیلدهای فیزیک", "en": "🌌 Physics Fields"},
    "b_top":      {"fa": "⭐ پرطرفدارها",    "en": "⭐ Popular"},
    "b_recent":   {"fa": "🆕 جدیدترین‌ها",   "en": "🆕 Recently Added"},

    # Books/Articles sub-menu inline buttons
    "sb_all":    {"fa": "📋 همه",           "en": "📋 All"},
    "sb_fields": {"fa": "🌌 فیلدها",        "en": "🌌 Fields"},
    "sb_top":    {"fa": "⭐ پرطرفدار",      "en": "⭐ Popular"},
    "sb_recent": {"fa": "🆕 جدید",          "en": "🆕 Recent"},

    # About sub-menu inline buttons
    "ab_help":    {"fa": "❓ راهنما",          "en": "❓ Help"},
    "ab_stats":   {"fa": "📊 آمار کتابخانه",   "en": "📊 Library Stats"},
    "ab_top":     {"fa": "⭐ پرطرفدارها",     "en": "⭐ Top Resources"},
    "ab_about":   {"fa": "🔭 درباره پروژه",    "en": "🔭 About Project"},

    # kept for backward-compat (used in old inline keyboards that may still exist)
    "books":   {"fa": "📚 همه کتاب‌ها",    "en": "📚 All Books"},
    "fields":  {"fa": "🌌 فیلدهای فیزیک", "en": "🌌 Physics Fields"},
    "stats":   {"fa": "📊 آمار",           "en": "📊 Stats"},
    "top":     {"fa": "🏆 پرطرفدارها",    "en": "🏆 Top Books"},
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
    """کیبورد اصلی ثابت پایین صفحه — ۴ دکمه."""
    lang = get_lang(user)
    lang_label = f"🌐 {'فارسی' if lang == 'en' else 'English'}"
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton(btn(user, "search")),
        types.KeyboardButton(btn(user, "browse")),
    )
    kb.add(
        types.KeyboardButton(btn(user, "about")),
        types.KeyboardButton(lang_label),
    )
    return kb


def cancel_keyboard(user: types.User) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    lang = get_lang(user)
    kb.add(types.KeyboardButton("❌ لغو" if lang == "fa" else "❌ Cancel"))
    return kb


def browse_keyboard(user: types.User) -> types.InlineKeyboardMarkup:
    lang = get_lang(user)
    mk = types.InlineKeyboardMarkup()
    mk.row(
        types.InlineKeyboardButton(BTN["b_books"][lang],    callback_data="browse:books"),
        types.InlineKeyboardButton(BTN["b_articles"][lang], callback_data="browse:articles"),
    )
    mk.row(
        types.InlineKeyboardButton(BTN["b_fields"][lang],   callback_data="browse:fields"),
    )
    mk.row(
        types.InlineKeyboardButton(BTN["b_top"][lang],      callback_data="browse:top"),
        types.InlineKeyboardButton(BTN["b_recent"][lang],   callback_data="browse:recent"),
    )
    return mk


def books_submenu_keyboard(user: types.User) -> types.InlineKeyboardMarkup:
    lang = get_lang(user)
    mk = types.InlineKeyboardMarkup()
    mk.row(
        types.InlineKeyboardButton(BTN["sb_all"][lang],    callback_data="booksub:all"),
        types.InlineKeyboardButton(BTN["sb_fields"][lang], callback_data="booksub:fields"),
    )
    mk.row(
        types.InlineKeyboardButton(BTN["sb_top"][lang],    callback_data="booksub:top"),
        types.InlineKeyboardButton(BTN["sb_recent"][lang], callback_data="booksub:recent"),
    )
    return mk


def articles_submenu_keyboard(user: types.User) -> types.InlineKeyboardMarkup:
    lang = get_lang(user)
    mk = types.InlineKeyboardMarkup()
    mk.row(
        types.InlineKeyboardButton(BTN["sb_all"][lang],    callback_data="artsub:all"),
        types.InlineKeyboardButton(BTN["sb_fields"][lang], callback_data="artsub:fields"),
    )
    mk.row(
        types.InlineKeyboardButton(BTN["sb_top"][lang],    callback_data="artsub:top"),
        types.InlineKeyboardButton(BTN["sb_recent"][lang], callback_data="artsub:recent"),
    )
    return mk


def about_keyboard(user: types.User) -> types.InlineKeyboardMarkup:
    lang = get_lang(user)
    mk = types.InlineKeyboardMarkup()
    mk.row(
        types.InlineKeyboardButton(BTN["ab_help"][lang],  callback_data="about:help"),
        types.InlineKeyboardButton(BTN["ab_stats"][lang], callback_data="about:stats"),
    )
    mk.row(
        types.InlineKeyboardButton(BTN["ab_top"][lang],   callback_data="about:top"),
        types.InlineKeyboardButton(BTN["ab_about"][lang], callback_data="about:project"),
    )
    return mk


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
    # also match dynamic lang button labels
    all_btns.update({"🌐 English", "🌐 فارسی"})

    if text == btn(user, "search"):
        waiting_search.add(uid)
        bot.send_message(
            message.chat.id,
            t(user, "search_prompt"),
            reply_markup=cancel_keyboard(user)
        )

    elif text == btn(user, "browse"):
        bot.send_message(
            message.chat.id,
            t(user, "browse_header"),
            reply_markup=browse_keyboard(user)
        )

    elif text == btn(user, "about"):
        bot.send_message(
            message.chat.id,
            t(user, "about_header"),
            reply_markup=about_keyboard(user)
        )

    elif text in ("🌐 English", "🌐 فارسی"):
        toggle_language(message)

    # backward-compat: old reply-keyboard buttons still work
    elif text == btn(user, "books"):
        handle_books(message)
    elif text == btn(user, "fields"):
        handle_fields(message)
    elif text == btn(user, "stats"):
        handle_stats(message)
    elif text == btn(user, "top"):
        handle_top(message)
    elif text == btn(user, "help"):
        bot.send_message(message.chat.id, t(user, "help"), reply_markup=main_keyboard(user))

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
    rows = database.search_resources(query=query, limit=20)
    if not rows:
        bot.send_message(message.chat.id, t(user, "not_found"), reply_markup=main_keyboard(user))
        return
    send_resource_list(message.chat.id, user, rows, header_key="resources_list_header")
    bot.send_message(message.chat.id, "─" * 10, reply_markup=main_keyboard(user))


def handle_fields(message: types.Message, user_override: types.User = None, resource_type: str = None):
    user = user_override or message.from_user
    lang = get_lang(user)

    buttons = []
    for field_key, (label_fa, label_en) in database.PHYSICS_FIELDS.items():
        label = label_fa if lang == "fa" else label_en
        # Always use fieldres: so all three flows (no filter / book / article)
        # go through field_resources() and call the same search_resources().
        # rtype is empty string when there's no filter, checked with `or None` in handler.
        rtype_part = resource_type or ""
        cb = f"fieldres:{field_key}:{rtype_part}"
        buttons.append(types.InlineKeyboardButton(label, callback_data=cb))

    markup = types.InlineKeyboardMarkup()
    for i in range(0, len(buttons), 2):
        markup.row(*buttons[i:i+2])

    bot.send_message(message.chat.id, t(user, "fields_header"), reply_markup=markup)


def handle_stats(message: types.Message, user_override: types.User = None):
    user = user_override or message.from_user
    s = database.get_library_stats()
    lang = get_lang(user)

    if lang == "fa":
        text = (
            f"📊 آمار کتابخانه\n\n"
            f"📘 کتاب‌ها: {s['total_books']}\n"
            f"📄 مقالات: {s.get('total_articles', 0)}\n"
            f"فارسی: {s['fa_books']}  |  انگلیسی: {s['en_books']}\n"
            f"⬇️ کل دانلودها: {s['total_downloads']}\n"
            f"🌌 فیلدهای فعال: {s['unique_fields']}"
        )
    else:
        text = (
            f"📊 Library Stats\n\n"
            f"📘 Books: {s['total_books']}\n"
            f"📄 Articles: {s.get('total_articles', 0)}\n"
            f"Persian: {s['fa_books']}  |  English: {s['en_books']}\n"
            f"⬇️ Total Downloads: {s['total_downloads']}\n"
            f"🌌 Active Fields: {s['unique_fields']}"
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
        edition_part = f" [{book['edition']}]" if book["edition"] and book["edition"].strip() else ""
        label = f"{disp} — {book['title'][:35]}{edition_part}"
        markup.add(types.InlineKeyboardButton(label, callback_data=f"bookinfo:{book['id']}"))

    bot.send_message(chat_id, header, reply_markup=markup)


def send_resource_list(chat_id: int, user: types.User, rows, header_key: str):
    """مثل send_book_list ولی برای همه انواع منابع (کتاب + مقاله)."""
    if not rows:
        bot.send_message(chat_id, t(user, "no_books"), reply_markup=main_keyboard(user))
        return
    lang = get_lang(user)
    header = TEXTS[header_key][lang]
    markup = types.InlineKeyboardMarkup()
    for res in rows:
        disp = database.get_display_id(res)
        rtype = res["resource_type"] if "resource_type" in res.keys() else "book"
        icon = "📄" if rtype == "article" else "📘"
        edition_part = f" [{res['edition']}]" if rtype == "book" and res.get("edition") and str(res["edition"]).strip() else ""
        label = f"{icon} {disp} — {res['title'][:35]}{edition_part}"
        markup.add(types.InlineKeyboardButton(label, callback_data=f"resinfo:{res['id']}"))
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

    edition_line = f"\n📖 {book['edition']}" if book.get("edition") and str(book["edition"]).strip() else ""
    text = (
        f"📘 {book['title']}{edition_line}\n"
        f"✍ {book['author']}\n"
        f"🌐 {lang_label}\n"
        f"🌌 {field}\n"
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


def send_resource_card(chat_id: int, user: types.User, res):
    """کارت نمایش منبع — کتاب یا مقاله."""
    lang = get_lang(user)
    rtype = res["resource_type"] if "resource_type" in res.keys() else "book"
    if rtype == "book":
        send_book_card(chat_id, user, res)
        return

    # Article card
    field_fa, field_en = database.PHYSICS_FIELDS.get(res["physics_field"], ("نامشخص", "Unknown"))
    field = field_fa if lang == "fa" else field_en
    lang_label = "فارسی" if res["language"] == "fa" else "English"
    disp = database.get_display_id(res)

    lines = [
        f"📄 {res['title']}",
        f"✍ {res['author']}" if res.get("author") else "",
        f"🌐 {lang_label}",
        f"🌌 {field}",
        f"🔖 {disp}",
    ]
    if res.get("journal"):
        lines.append(f"📰 {res['journal']}")
    if res.get("volume") or res.get("issue"):
        vi = f"Vol.{res['volume']}" if res.get("volume") else ""
        if res.get("issue"):
            vi += f" No.{res['issue']}"
        lines.append(f"🔢 {vi.strip()}")
    if res.get("pages"):
        lines.append(f"📄 pp. {res['pages']}")
    if res.get("doi"):
        lines.append(f"🔗 DOI: {res['doi']}")
    if res.get("url"):
        lines.append(f"🌐 {res['url']}")
    if res.get("publication_date"):
        lines.append(f"📅 {res['publication_date']}")
    lines.append(f"⬇️ {res['download_count']}")

    markup = types.InlineKeyboardMarkup()
    # Show the download button for articles that have a PDF file OR an external
    # link/DOI — the download handler sends a document or a text card accordingly.
    if res.get("file_id") or res.get("url") or res.get("doi"):
        markup.add(types.InlineKeyboardButton(
            t(user, "download"), callback_data=f"download:{res['id']}"
        ))
    bot.send_message(chat_id, "\n".join(l for l in lines if l), reply_markup=markup)


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
    res_id = int(callback.data.split(":")[1])
    res = database.get_resource(res_id)

    if not res:
        bot.answer_callback_query(callback.id, t(user, "book_missing"))
        return

    lang = get_lang(user)
    field_fa, field_en = database.PHYSICS_FIELDS.get(
        res["physics_field"], ("نامشخص", "Unknown")
    )
    field = field_fa if lang == "fa" else field_en
    lang_label = "فارسی" if res["language"] == "fa" else "English"
    disp = database.get_display_id(res)
    rtype = res["resource_type"] if "resource_type" in res.keys() else "book"

    if rtype == "article":
        # --- Article download ---
        lines = [
            f"📄 {res['title']}",
            f"✍ {res['author']}" if res.get("author") else "",
            f"🌐 {lang_label}",
            f"🌌 {field}",
            f"🔖 {disp}",
        ]
        if res.get("journal"):
            lines.append(f"📰 {res['journal']}")
        if res.get("volume") or res.get("issue"):
            vi = f"Vol.{res['volume']}" if res.get("volume") else ""
            if res.get("issue"):
                vi += f" No.{res['issue']}"
            lines.append(f"🔢 {vi.strip()}")
        if res.get("pages"):
            lines.append(f"📄 pp. {res['pages']}")
        if res.get("doi"):
            lines.append(f"🔗 DOI: {res['doi']}")
        if res.get("url"):
            lines.append(f"🌐 {res['url']}")
        if res.get("publication_date"):
            lines.append(f"📅 {res['publication_date']}")
        lines.append(f"⬇️ {res['download_count']}")
        lines.append("")
        lines.append("@PhysisLib_Bot")

        caption = "\n".join(l for l in lines if l is not None)

        if res.get("file_id"):
            # Article has an attached PDF — send it as a document
            bot.send_document(
                callback.message.chat.id,
                res["file_id"],
                caption=caption,
            )
        else:
            # Link-only article — send the metadata as a text message
            bot.send_message(callback.message.chat.id, caption)

    else:
        # --- Book download (original behaviour, unchanged) ---
        edition_val = res["edition"] if res["edition"] else ""
        year_val    = res["year"]    if res["year"]    else ""
        desc_val    = res["description"] if res["description"] else ""

        edition_line = f"\n📖 {edition_val}" if edition_val else ""
        year_line    = f"\n📅 {year_val}"    if year_val    else ""
        desc_line    = f"\n📝 {desc_val}"    if desc_val    else ""

        caption = (
            f"📘 {res['title']}{edition_line}\n"
            f"✍ {res['author']}\n"
            f"🌐 {lang_label}\n"
            f"🧲 {field}\n"
            f"🔖 {disp}\n"
            f"⬇️ {res['download_count']}"
            f"{year_line}"
            f"{desc_line}\n\n"
            f"@PhysisLib_Bot"
        )
        bot.send_document(
            callback.message.chat.id,
            res["file_id"],
            caption=caption,
        )

    database.record_download(res_id, user.id)
    bot.answer_callback_query(callback.id, t(user, "downloaded"))




# callback: resinfo (unified resource card)
@bot.callback_query_handler(func=lambda c: c.data.startswith("resinfo:"))
def resource_info(callback: types.CallbackQuery):
    user = callback.from_user
    res_id = int(callback.data.split(":")[1])
    res = database.get_resource(res_id)
    if not res:
        bot.answer_callback_query(callback.id, t(user, "book_missing"), show_alert=True)
        return
    bot.answer_callback_query(callback.id)
    send_resource_card(callback.message.chat.id, user, res)


# callback: browse sub-menu
@bot.callback_query_handler(func=lambda c: c.data.startswith("browse:"))
def browse_callback(callback: types.CallbackQuery):
    user = callback.from_user
    action = callback.data.split(":")[1]
    bot.answer_callback_query(callback.id)
    chat_id = callback.message.chat.id

    if action == "books":
        bot.send_message(chat_id, t(user, "browse_books_header"),
                         reply_markup=books_submenu_keyboard(user))
    elif action == "articles":
        bot.send_message(chat_id, t(user, "browse_articles_header"),
                         reply_markup=articles_submenu_keyboard(user))
    elif action == "fields":
        handle_fields(callback.message, user_override=user)
    elif action == "top":
        rows = database.get_top_downloads(limit=10)
        send_resource_list(chat_id, user, rows, header_key="top_books_header")
    elif action == "recent":
        rows = database.search_resources(limit=20)
        send_resource_list(chat_id, user, rows, header_key="resources_list_header")


# callback: books sub-menu
@bot.callback_query_handler(func=lambda c: c.data.startswith("booksub:"))
def booksub_callback(callback: types.CallbackQuery):
    user = callback.from_user
    action = callback.data.split(":")[1]
    bot.answer_callback_query(callback.id)
    chat_id = callback.message.chat.id

    if action == "all":
        rows = database.search_resources(resource_type="book", limit=20)
        send_resource_list(chat_id, user, rows, header_key="books_list_header")
    elif action == "fields":
        handle_fields(callback.message, user_override=user, resource_type="book")
    elif action == "top":
        rows = database.get_top_downloads(limit=10, resource_type="book")
        send_resource_list(chat_id, user, rows, header_key="books_list_header")
    elif action == "recent":
        rows = database.search_resources(resource_type="book", limit=20)
        send_resource_list(chat_id, user, rows, header_key="books_list_header")


# callback: articles sub-menu
@bot.callback_query_handler(func=lambda c: c.data.startswith("artsub:"))
def artsub_callback(callback: types.CallbackQuery):
    user = callback.from_user
    action = callback.data.split(":")[1]
    bot.answer_callback_query(callback.id)
    chat_id = callback.message.chat.id

    if action == "all":
        rows = database.search_resources(resource_type="article", limit=20)
        send_resource_list(chat_id, user, rows, header_key="articles_list_header")
    elif action == "fields":
        handle_fields(callback.message, user_override=user, resource_type="article")
    elif action == "top":
        rows = database.get_top_downloads(limit=10, resource_type="article")
        send_resource_list(chat_id, user, rows, header_key="articles_list_header")
    elif action == "recent":
        rows = database.search_resources(resource_type="article", limit=20)
        send_resource_list(chat_id, user, rows, header_key="articles_list_header")


# callback: about sub-menu
@bot.callback_query_handler(func=lambda c: c.data.startswith("about:"))
def about_callback(callback: types.CallbackQuery):
    user = callback.from_user
    action = callback.data.split(":")[1]
    bot.answer_callback_query(callback.id)
    chat_id = callback.message.chat.id

    if action == "help":
        bot.send_message(chat_id, t(user, "help"), reply_markup=main_keyboard(user))
    elif action == "stats":
        handle_stats(callback.message, user_override=user)
    elif action == "top":
        rows = database.get_top_downloads(limit=10)
        send_resource_list(chat_id, user, rows, header_key="top_books_header")
    elif action == "project":
        bot.send_message(chat_id, t(user, "about_project"), reply_markup=main_keyboard(user))


# callback: field filter — handles all three flows:
# 1. Browse → Fields → Field          (fieldres:key:)    rtype=None → all
# 2. Browse → Books → Fields → Field  (fieldres:key:book) rtype=book
# 3. Browse → Articles → Fields → Field (fieldres:key:article) rtype=article
@bot.callback_query_handler(func=lambda c: c.data.startswith("fieldres:"))
def field_resources(callback: types.CallbackQuery):
    user = callback.from_user
    parts = callback.data.split(":")
    field_key = parts[1]
    rtype = parts[2] if len(parts) > 2 and parts[2] else None  # empty string → None
    rows = database.search_resources(physics_field=field_key, resource_type=rtype, limit=20)
    if not rows:
        bot.answer_callback_query(callback.id, t(user, "no_books"), show_alert=True)
        return
    bot.answer_callback_query(callback.id)
    hkey = "articles_list_header" if rtype == "article" else "books_list_header" if rtype == "book" else "resources_list_header"
    send_resource_list(callback.message.chat.id, user, rows, header_key=hkey)


print("Bot is running...")
bot.infinity_polling()
