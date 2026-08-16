import os
import telebot
from telebot import types
import database
import admin

TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)


def _row_get(row, key: str, default=None):
    """sqlite3.Row does not support .get().  Use this instead of row.get(key)."""
    try:
        val = row[key]
        return val if val is not None else default
    except (IndexError, KeyError):
        return default

database.init_db()


user_langs: dict[int, str] = {}
waiting_search: set[int] = set()

# ── Pagination 
PAGE_SIZE = 10


def _pagination_keyboard(context: str, page: int, has_next: bool) -> types.InlineKeyboardMarkup | None:
    """Return an InlineKeyboardMarkup with ◀️ / ▶️ buttons, or None when not needed.

    context  – opaque string that encodes the query (stored in callback_data).
    page     – 0-based current page index.
    has_next – whether there is at least one item on the next page.
    """
    show_prev = page > 0
    show_next = has_next
    if not show_prev and not show_next:
        return None
    row = []
    if show_prev:
        row.append(types.InlineKeyboardButton("◀️", callback_data=f"page:{context}:{page - 1}"))
    if show_next:
        row.append(types.InlineKeyboardButton("▶️", callback_data=f"page:{context}:{page + 1}"))
    mk = types.InlineKeyboardMarkup()
    mk.row(*row)
    return mk


def _send_paginated_list(
    chat_id: int,
    user: types.User,
    context: str,
    page: int,
    header_key: str,
    edit_message_id: int | None = None,
):
    """Fetch one page of results and send (or edit) the list message.

    context format:  "<list_type>|<arg>"
      list_type   arg
      ─────────   ───────────────────────────────────────────
      search      <query text>
      books       (empty)
      articles    (empty)
      top_all     (empty)
      top_books   (empty)
      top_articles (empty)
      recent_all  (empty)
      field       <field_key>|<rtype>   (rtype may be empty)

    header_key is embedded at the END of context as  "…|hdr:<key>" so it
    survives round-trips without a separate store.
    """
    # ── decode header_key from context 
    if "|hdr:" in context:
        ctx_core, hkey = context.rsplit("|hdr:", 1)
    else:
        ctx_core, hkey = context, header_key  # fallback (first call)

    offset = page * PAGE_SIZE
    fetch_limit = PAGE_SIZE + 1          # fetch one extra to detect next page

    # ── fetch rows based on list type
    parts = ctx_core.split("|", 1)
    list_type = parts[0]
    arg = parts[1] if len(parts) > 1 else ""

    if list_type == "search":
        rows = database.search_resources(query=arg, limit=fetch_limit, offset=offset)
    elif list_type == "books":
        rows = database.search_resources(resource_type="book", limit=fetch_limit, offset=offset)
    elif list_type == "articles":
        rows = database.search_resources(resource_type="article", limit=fetch_limit, offset=offset)
    elif list_type == "top_all":
        rows = database.get_top_downloads(limit=fetch_limit, offset=offset)
    elif list_type == "top_books":
        rows = database.get_top_downloads(limit=fetch_limit, resource_type="book", offset=offset)
    elif list_type == "top_articles":
        rows = database.get_top_downloads(limit=fetch_limit, resource_type="article", offset=offset)
    elif list_type == "recent_all":
        rows = database.search_resources(limit=fetch_limit, offset=offset)
    elif list_type == "field":
        field_key, rtype = (arg.split("|", 1) + [""])[:2]
        rtype = rtype or None
        rows = database.search_resources(
            physics_field=field_key, resource_type=rtype, limit=fetch_limit, offset=offset
        )
    else:
        rows = []

    has_next = len(rows) > PAGE_SIZE
    page_rows = rows[:PAGE_SIZE]

    if not page_rows:
        bot.answer_callback_query  # nothing to show — should not normally happen
        return

    lang = get_lang(user)
    header = TEXTS[hkey][lang]
    total_label = f"  [{page * PAGE_SIZE + 1}–{page * PAGE_SIZE + len(page_rows)}]"
    full_header = header + total_label

    # Build item buttons
    mk = types.InlineKeyboardMarkup()
    for res in page_rows:
        disp = database.get_display_id(res)
        rtype_r = res["resource_type"] if "resource_type" in res.keys() else "book"
        icon = "📄" if rtype_r == "article" else "📘"
        edition_part = (
            f" [{res['edition']}]"
            if rtype_r == "book" and _row_get(res, "edition") and str(res["edition"]).strip()
            else ""
        )
        label = f"{icon} {disp} — {res['title'][:35]}{edition_part}"
        mk.add(types.InlineKeyboardButton(label, callback_data=f"resinfo:{res['id']}"))

    # Append nav row if needed
    full_context = f"{ctx_core}|hdr:{hkey}"
    nav_mk = _pagination_keyboard(full_context, page, has_next)
    if nav_mk:
        for row in nav_mk.keyboard:
            mk.row(*row)

    if edit_message_id:
        try:
            bot.edit_message_text(
                full_header,
                chat_id=chat_id,
                message_id=edit_message_id,
                reply_markup=mk,
            )
        except Exception:
            bot.send_message(chat_id, full_header, reply_markup=mk)
    else:
        bot.send_message(chat_id, full_header, reply_markup=mk)
# ── End Pagination 

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
        "To search among English-language resources, change the language and then search again.\n"
        "برای جستجو در منابع انگلیسی، زبان را تغییر دهید و سپس دوباره جستجو کنید.",
        "en": "🔍 Search in all English resources:\n"
        "Type the keyword you want to search:\n\n"
        "برای جستجو در منابع فارسی، زبان را تغییر دهید و سپس دوباره جستجو کنید.\n"
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
            "Version: 2.2"
        ),
        "en": (
            "🔭 About the Project\n\n"
            "Physics Library is a Telegram bot designed to provide easy access to a growing collection of physics books and research articles.\n"
            "The library covers a wide range of topics, from foundational physics to specialized fields, and aims to help students, educators, and researchers quickly discover useful learning resources.The project is continuously expanding, with new books and articles being added over time.\n\n"
            "Thank you for using Physics Library and supporting its growth.\n"
            "📬 Contact & Support: @Kimhmda0705\n"
            "Version: 2.2"
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
    """کیبورد اصلی ثابت پایین صفحه — ۴ دکمه (+ دکمه پنل ادمین برای ادمین‌ها)."""
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
    if admin.is_admin(user.id):
        kb.add(types.KeyboardButton(admin.tr("open_panel_btn", lang)))
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
    # admin panel button labels (both languages)
    all_btns.update({admin.tr("open_panel_btn", "fa"), admin.tr("open_panel_btn", "en")})

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

    elif text in (admin.tr("open_panel_btn", "fa"), admin.tr("open_panel_btn", "en")):
        admin.handle_admin_command(bot, message)

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
    # Quick check: does anything match at all?
    probe = database.search_resources(query=query, limit=1, offset=0)
    if not probe:
        bot.send_message(message.chat.id, t(user, "not_found"), reply_markup=main_keyboard(user))
        return
    send_resource_list(
        message.chat.id, user, probe, header_key="resources_list_header",
        pg_context=f"search|{query}",
    )
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


def send_resource_list(chat_id: int, user: types.User, rows, header_key: str,
                       pg_context: str | None = None):
    """مثل send_book_list ولی برای همه انواع منابع (کتاب + مقاله).

    When pg_context is given the list is rendered via the paginated helper so
    navigation buttons are included from the start.  Callers that already have
    a full row-set but no context fall back to the legacy flat render (used by
    the backward-compat handle_top path which limits to 10 items anyway).
    """
    if not rows:
        bot.send_message(chat_id, t(user, "no_books"), reply_markup=main_keyboard(user))
        return
    if pg_context is not None:
        _send_paginated_list(chat_id, user, pg_context, page=0, header_key=header_key)
        return
    # legacy flat render (≤20 items, no pagination needed)
    lang = get_lang(user)
    header = TEXTS[header_key][lang]
    markup = types.InlineKeyboardMarkup()
    for res in rows:
        disp = database.get_display_id(res)
        rtype = res["resource_type"] if "resource_type" in res.keys() else "book"
        icon = "📄" if rtype == "article" else "📘"
        edition_part = f" [{res['edition']}]" if rtype == "book" and _row_get(res, "edition") and str(res["edition"]).strip() else ""
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

    edition_line = f"\n📖 {book['edition']}" if _row_get(book, "edition") and str(book["edition"]).strip() else ""
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
        f"✍ {res['author']}" if _row_get(res, "author") else "",
        f"🌐 {lang_label}",
        f"🌌 {field}",
        f"🔖 {disp}",
    ]
    if _row_get(res, "journal"):
        lines.append(f"📰 {res['journal']}")
    if _row_get(res, "volume") or _row_get(res, "issue"):
        vi = f"Vol.{res['volume']}" if _row_get(res, "volume") else ""
        if _row_get(res, "issue"):
            vi += f" No.{res['issue']}"
        lines.append(f"🔢 {vi.strip()}")
    if _row_get(res, "pages"):
        lines.append(f"📄 pp. {res['pages']}")
    if _row_get(res, "doi"):
        lines.append(f"🔗 DOI: {res['doi']}")
    if _row_get(res, "url"):
        lines.append(f"🌐 {res['url']}")
    if _row_get(res, "publication_date"):
        lines.append(f"📅 {res['publication_date']}")
    lines.append(f"⬇️ {res['download_count']}")

    markup = types.InlineKeyboardMarkup()
    # Show the download button for articles that have a PDF file OR an external
    # link/DOI — the download handler sends a document or a text card accordingly.
    if _row_get(res, "file_id") or _row_get(res, "url") or _row_get(res, "doi"):
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
            f"✍ {res['author']}" if _row_get(res, "author") else "",
            f"🌐 {lang_label}",
            f"🌌 {field}",
            f"🔖 {disp}",
        ]
        if _row_get(res, "journal"):
            lines.append(f"📰 {res['journal']}")
        if _row_get(res, "volume") or _row_get(res, "issue"):
            vi = f"Vol.{res['volume']}" if _row_get(res, "volume") else ""
            if _row_get(res, "issue"):
                vi += f" No.{res['issue']}"
            lines.append(f"🔢 {vi.strip()}")
        if _row_get(res, "pages"):
            lines.append(f"📄 pp. {res['pages']}")
        if _row_get(res, "doi"):
            lines.append(f"🔗 DOI: {res['doi']}")
        if _row_get(res, "url"):
            lines.append(f"🌐 {res['url']}")
        if _row_get(res, "publication_date"):
            lines.append(f"📅 {res['publication_date']}")
        lines.append(f"⬇️ {res['download_count']}")
        lines.append("")
        lines.append("@PhysisLib_Bot")

        caption = "\n".join(l for l in lines if l is not None)

        if _row_get(res, "file_id"):
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
        probe = database.get_top_downloads(limit=1, offset=0)
        send_resource_list(chat_id, user, probe, header_key="top_books_header",
                           pg_context="top_all|")
    elif action == "recent":
        probe = database.search_resources(limit=1, offset=0)
        send_resource_list(chat_id, user, probe, header_key="resources_list_header",
                           pg_context="recent_all|")


# callback: books sub-menu
@bot.callback_query_handler(func=lambda c: c.data.startswith("booksub:"))
def booksub_callback(callback: types.CallbackQuery):
    user = callback.from_user
    action = callback.data.split(":")[1]
    bot.answer_callback_query(callback.id)
    chat_id = callback.message.chat.id

    if action == "all":
        probe = database.search_resources(resource_type="book", limit=1, offset=0)
        send_resource_list(chat_id, user, probe, header_key="books_list_header",
                           pg_context="books|")
    elif action == "fields":
        handle_fields(callback.message, user_override=user, resource_type="book")
    elif action == "top":
        probe = database.get_top_downloads(limit=1, resource_type="book", offset=0)
        send_resource_list(chat_id, user, probe, header_key="books_list_header",
                           pg_context="top_books|")
    elif action == "recent":
        probe = database.search_resources(resource_type="book", limit=1, offset=0)
        send_resource_list(chat_id, user, probe, header_key="books_list_header",
                           pg_context="books|")


# callback: articles sub-menu
@bot.callback_query_handler(func=lambda c: c.data.startswith("artsub:"))
def artsub_callback(callback: types.CallbackQuery):
    user = callback.from_user
    action = callback.data.split(":")[1]
    bot.answer_callback_query(callback.id)
    chat_id = callback.message.chat.id

    if action == "all":
        probe = database.search_resources(resource_type="article", limit=1, offset=0)
        send_resource_list(chat_id, user, probe, header_key="articles_list_header",
                           pg_context="articles|")
    elif action == "fields":
        handle_fields(callback.message, user_override=user, resource_type="article")
    elif action == "top":
        probe = database.get_top_downloads(limit=1, resource_type="article", offset=0)
        send_resource_list(chat_id, user, probe, header_key="articles_list_header",
                           pg_context="top_articles|")
    elif action == "recent":
        probe = database.search_resources(resource_type="article", limit=1, offset=0)
        send_resource_list(chat_id, user, probe, header_key="articles_list_header",
                           pg_context="articles|")


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
        probe = database.get_top_downloads(limit=1, offset=0)
        send_resource_list(chat_id, user, probe, header_key="top_books_header",
                           pg_context="top_all|")
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
    rtype_raw = parts[2] if len(parts) > 2 else ""
    rtype = rtype_raw or None  # empty string → None
    probe = database.search_resources(physics_field=field_key, resource_type=rtype, limit=1, offset=0)
    if not probe:
        bot.answer_callback_query(callback.id, t(user, "no_books"), show_alert=True)
        return
    bot.answer_callback_query(callback.id)
    hkey = "articles_list_header" if rtype == "article" else "books_list_header" if rtype == "book" else "resources_list_header"
    # context: "field|<field_key>|<rtype_raw>"
    send_resource_list(
        callback.message.chat.id, user, probe, header_key=hkey,
        pg_context=f"field|{field_key}|{rtype_raw}",
    )


# callback: pagination navigation
@bot.callback_query_handler(func=lambda c: c.data.startswith("page:"))
def page_callback(callback: types.CallbackQuery):
    user = callback.from_user
    # format: page:<context>:<page_number>
    # context itself may contain colons, so split from the right for page number
    _, rest = callback.data.split(":", 1)
    page_str = rest.rsplit(":", 1)[1]
    context  = rest.rsplit(":", 1)[0]
    try:
        page = int(page_str)
    except ValueError:
        bot.answer_callback_query(callback.id)
        return
    bot.answer_callback_query(callback.id)
    _send_paginated_list(
        chat_id=callback.message.chat.id,
        user=user,
        context=context,
        page=page,
        header_key="",          # extracted from context inside the function
        edit_message_id=callback.message.message_id,
    )


print("Bot is running...")
bot.infinity_polling()
