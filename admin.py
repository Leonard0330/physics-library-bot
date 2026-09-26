import csv
import io
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

# Notify callback — set by bot.py after init
_notify_callback = None

def set_notify_callback(fn) -> None:
    global _notify_callback
    _notify_callback = fn

# Backup / Restore state 
_backup_restore_lock = threading.Lock()  
_restore_sessions: dict[int, dict] = {} 
RESTORE_TIMEOUT = 600                     

def is_admin(user_id: int) -> bool:
    return database.is_admin(user_id)

def get_lang(user_id: int) -> str:
    return database.get_user_lang(user_id) or DEFAULT_LANG

# Bilingual Texts
T = {
    "no_access":        {"fa": "⛔️ دسترسی ندارید.",                              "en": "⛔️ You don't have access."},
    "panel_title":      {"fa": "👤 پنل ادمین\nیکی از گزینه‌ها رو انتخاب کن:",   "en": "👤 Admin Panel\nChoose an option:"},
    "cancelled":        {"fa": "↩️ عملیات لغو شد.",                             "en": "↩️ Operation cancelled."},
    "ask_book_id_del":  {"fa": "🗑 شناسه کتاب یا مقاله رو بنویس (مثلاً REL-14 یا آیدی عددی):",
                          "en": "🗑 Enter the book or article ID (e.g. REL-14 or the raw numeric ID):"},
    "ask_book_id_edit": {"fa": "✏️ شناسه کتاب یا مقاله ای که می‌خوای ویرایش کنی رو بنویس (مثلاً REL-14):",
                          "en": "✏️ Enter the ID of the book or article to edit (e.g. REL-14):"},
    "not_a_number":     {"fa": "❗️ فرمت شناسه اشتباهه.",                        "en": "❗️ Invalid ID format."},
    "book_not_found":   {"fa": "❌ کتابی با شناسه {id} پیدا نشد.",               "en": "❌ No book found with ID {id}."},
    "book_deleted":     {"fa": "🗑 کتاب «{title}» (<code>{disp}</code>) حذف شد.",             "en": "🗑 Book \"{title}\" (<code>{disp}</code>) deleted."},
    "back_to_panel":    {"fa": "بازگشت به پنل:",                               "en": "Back to panel:"},
    "send_pdf":         {"fa": "📤 فایل منبع رو بفرست (PDF، ZIP یا DjVu):", "en": "📤 Send the resource file (PDF, ZIP, or DjVu):"},
    "pdf_only":         {"fa": "❗️ فقط فایل‌های PDF، ZIP و DjVu قبول می‌شن.", "en": "❗️ Only PDF, ZIP, and DjVu files are accepted."},
    "file_received":    {"fa": "✅ فایل دریافت شد: {name}\n\n📘 حالا عنوان فایل رو بنویس:",
                          "en": "✅ File received: {name}\n\n📕 Now type the file's title:"},
    "ask_author":       {"fa": "✍ نام نویسنده:",                                "en": "✍ Author's name:"},
    "ask_lang":         {"fa": "🌐 زبان منبع رو انتخاب کن:",                     "en": "🌐 Choose the resource's language:"},
    "ask_field":        {"fa": "🌌 فیلد فیزیکی رو انتخاب کن:",                   "en": "🌌 Choose the physics field:"},
    "ask_year":         {"fa": "📅 سال انتشار (مثلاً 2020) — یا رد کن:",         "en": "📅 Publication year (e.g. 2020) — or skip:"},
    "year_not_number":  {"fa": "❗️ سال رو به عدد وارد کن (مثلاً 2020):",         "en": "❗️ Enter the year as a number (e.g. 2020):"},
    "ask_edition":      {"fa": "🔖 ویرایش (مثلاً 3rd) — یا رد کن:",              "en": "🔖 Edition (e.g. 3rd) — or skip:"},
    "ask_desc":         {"fa": "📝 توضیحات کوتاه — یا رد کن:",                  "en": "📝 A short description — or skip:"},
    "confirm_question": {"fa": "آیا اطلاعات صحیح است؟",                        "en": "Is this information correct?"},
    "wrong_step":       {"fa": "⚠️ مرحله اشتباه",                              "en": "⚠️ Wrong step"},
    "missing_fields":   {"fa": "❌ این فیلدها خالی هستن: {fields}\nدوباره از ابتدا شروع کن.",
                          "en": "❌ These fields are missing: {fields}\nPlease start over."},
    "saved_ok":         {"fa": "✅ کتاب با موفقیت ذخیره شد!\n🔖 شناسه: <code>{disp}</code>\n📘 {title}",
                          "en": "✅ Book saved successfully!\n🔖 ID: <code>{disp}</code>\n📕 {title}"},
    "save_error":       {"fa": "❌ خطا در ذخیره:\n{err}",                       "en": "❌ Error while saving:\n{err}"},
    "no_books":         {"fa": "📭 هنوز کتابی ثبت نشده.",                       "en": "📭 No books have been added yet."},
    "list_header":      {"fa": "📋 لیست کتاب‌ها:\n",                            "en": "📋 List of books:\n"},
    "stats_header":     {"fa": "📊 آمار کتابخانه",                              "en": "📊 Library Stats"},
    "unknown_field":    {"fa": "نامشخص",                                        "en": "Unknown"},
    "lang_fa":          {"fa": "فارسی",                                     "en": "Persian"},
    "lang_en":          {"fa": "انگلیسی",                                   "en": "English"},

    # Main Panel Buttons

    "btn_add":          {"fa": "➕ افزودن منبع",           "en": "➕ Add Resource"},
    "btn_edit":         {"fa": "✏️ ویرایش منبع",           "en": "✏️ Edit Resource"},
    "btn_list":         {"fa": "📋 لیست منابع",          "en": "📋 Resources List"},
    "btn_delete":       {"fa": "🗑 حذف منبع",              "en": "🗑 Delete Resource"},
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
    "edit_field_title":    {"fa": "📘 عنوان",   "en": "📕 Title"},
    "edit_field_author":   {"fa": "✍ نویسنده",  "en": "✍ Author"},
    "edit_field_year":     {"fa": "📅 سال",     "en": "📅 Year"},
    "edit_field_edition":  {"fa": "🔖 ویرایش",  "en": "🔖 Edition"},
    "edit_field_desc":     {"fa": "📝 توضیحات", "en": "📝 Description"},
    "edit_field_physics":  {"fa": "🌌 فیلد فیزیکی", "en": "🌌 Physics Field"},
    # Article-specific edit fields
    "edit_field_doi":           {"fa": "🔗 DOI",           "en": "🔗 DOI"},
    "edit_field_journal":       {"fa": "📰 مجله",           "en": "📰 Journal"},
    "edit_field_volume":        {"fa": "🔢 جلد",            "en": "🔢 Volume"},
    "edit_field_issue":         {"fa": "🔢 شماره",          "en": "🔢 Issue"},
    "edit_field_pages":         {"fa": "📄 صفحات",          "en": "📄 Pages"},
    "edit_field_pub_date":      {"fa": "📅 تاریخ انتشار",   "en": "📅 Publication Date"},
    "edit_field_url":           {"fa": "🌐 URL",            "en": "🌐 URL"},
    "ask_new_value":    {"fa": "مقدار جدید رو بنویس:",                          "en": "Enter the new value:"},
    "edit_saved":       {"fa": "✅ منبع <code>{disp}</code> به‌روزرسانی شد.",                 "en": "✅ Resource <code>{disp}</code> updated."},

    # Summary

    "summary_title":    {"fa": "📋 خلاصه اطلاعات کتاب:\n",  "en": "📋 Book Summary:\n"},
    "summary_book":     {"fa": "📘 عنوان: {v}",  "en": "📕 Title: {v}"},
    "summary_author":   {"fa": "✍ نویسنده: {v}", "en": "✍ Author: {v}"},
    "summary_lang":     {"fa": "🌐 زبان: {v}",    "en": "🌐 Language: {v}"},
    "summary_field":    {"fa": "🌌 فیلد: {v}",    "en": "🌌 Field: {v}"},
    "summary_year":     {"fa": "📅 سال: {v}",     "en": "📅 Year: {v}"},
    "summary_edition":  {"fa": "🔖 ویرایش: {v}",  "en": "🔖 Edition: {v}"},
    "summary_desc":     {"fa": "📝 توضیحات: {v}", "en": "📝 Description: {v}"},
    "summary_file":     {"fa": "📁 فایل: {v}",    "en": "📁 File: {v}"},

    # Resource type selection

    "ask_resource_type":  {"fa": "📂 نوع منبع رو انتخاب کن:",              "en": "📂 Choose the resource type:"},
    "btn_type_book":      {"fa": "📘 کتاب",                                "en": "📕 Book"},
    "btn_type_article":   {"fa": "📄 مقاله",                               "en": "📄 Article"},

    # Article-specific prompts

    "ask_authors":        {"fa": "✍ نام نویسنده(ها) — یا رد کن:",          "en": "✍ Author(s) name — or skip:"},
    "ask_journal":        {"fa": "📰 نام مجله/ژورنال — یا رد کن:",         "en": "📰 Journal name — or skip:"},
    "ask_volume":         {"fa": "🔢 جلد (Volume) — یا رد کن:",            "en": "🔢 Volume — or skip:"},
    "ask_issue":          {"fa": "🔢 شماره (Issue) — یا رد کن:",           "en": "🔢 Issue — or skip:"},
    "ask_pages":          {"fa": "📄 صفحات (مثلاً 12-25) — یا رد کن:",     "en": "📄 Pages (e.g. 12-25) — or skip:"},
    "ask_doi":            {"fa": "🔗 DOI — یا رد کن:",                     "en": "🔗 DOI — or skip:"},
    "ask_url":            {"fa": "🌐 URL — یا رد کن:",                     "en": "🌐 URL — or skip:"},
    "ask_pub_date":       {"fa": "📅 تاریخ انتشار (مثلاً 2023-06) — یا رد کن:", "en": "📅 Publication date (e.g. 2023-06) — or skip:"},
    "ask_pdf_optional":   {"fa": "📤 فایل PDF رو بفرست:",
                           "en": "📤 Send the PDF file:"},

    # Article summary additions

    "summary_resource_type": {"fa": "📂 نوع: {v}",    "en": "📂 Type: {v}"},
    "summary_journal":    {"fa": "📰 مجله: {v}",       "en": "📰 Journal: {v}"},
    "summary_volume":     {"fa": "🔢 جلد: {v}",        "en": "🔢 Volume: {v}"},
    "summary_issue":      {"fa": "🔢 شماره: {v}",      "en": "🔢 Issue: {v}"},
    "summary_pages":      {"fa": "📄 صفحات: {v}",      "en": "📄 Pages: {v}"},
    "summary_doi":        {"fa": "🔗 DOI: {v}",        "en": "🔗 DOI: {v}"},
    "summary_url":        {"fa": "🌐 URL: {v}",        "en": "🌐 URL: {v}"},
    "summary_pub_date":   {"fa": "📅 تاریخ: {v}",      "en": "📅 Date: {v}"},

    # Resource-type-agnostic messages

    "ask_resource_id_del":  {"fa": "🗑 شناسه کتاب یا مقاله رو بنویس (مثلاً REL-B14 یا REL-A3 یا آیدی عددی):",
                              "en": "🗑 Enter book/article ID (e.g. REL-B14 or REL-A3 or numeric ID):"},
    "ask_resource_id_edit": {"fa": "✏️ شناسه کتاب یا مقاله‌ای که می‌خوای ویرایش کنی رو بنویس:",
                              "en": "✏️ Enter the ID of the book or article to edit:"},
    "resource_not_found":   {"fa": "❌ منبعی با شناسه {id} پیدا نشد.",      "en": "❌ No resource found with ID {id}."},
    "resource_deleted":     {"fa": "🗑 «{title}» (<code>{disp}</code>) حذف شد.",         "en": "🗑 \"{title}\" (<code>{disp}</code>) deleted."},
    "saved_ok_article":     {"fa": "✅ مقاله با موفقیت ذخیره شد!\n🔖 شناسه: <code>{disp}</code>\n📄 {title}",
                              "en": "✅ Article saved successfully!\n🔖 ID: <code>{disp}</code>\n📄 {title}"},
    "no_resources":         {"fa": "📭 هنوز هیچ منبعی ثبت نشده.",            "en": "📭 No resources have been added yet."},
    "list_header_all":      {"fa": "📋 لیست منابع:\n",                        "en": "📋 Resource list:\n"},

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

    # ── CSV Import (Phase 2) ──────────────────────────────────────────────────
    "btn_csv_import":       {"fa": "📥 وارد کردن CSV",              "en": "📥 Import CSV"},
    "csv_ask_file":         {"fa": "📤 فایل CSV متادیتا رو بفرست.\n"
                                   "برای لغو دکمه لغو رو بزن.",
                              "en": "📤 Send the metadata CSV file.\n"
                                   "Press Cancel to abort."},
    "csv_not_csv":          {"fa": "❌ فقط فایل‌های .csv قبول می‌شن.",
                              "en": "❌ Only .csv files are accepted."},
    "csv_too_large":        {"fa": "❌ فایل CSV خیلی بزرگه (حداکثر ۵ مگابایت).",
                              "en": "❌ CSV file is too large (max 5 MB)."},
    "csv_parse_error":      {"fa": "❌ خطا در خواندن CSV:\n{err}",
                              "en": "❌ Error reading CSV:\n{err}"},
    "csv_missing_cols":     {"fa": "❌ ستون‌های ضروری وجود ندارن:\n{cols}\n\n"
                                   "ستون‌های موجود:\n{found}",
                              "en": "❌ Required columns are missing:\n{cols}\n\n"
                                   "Columns found:\n{found}"},
    "csv_empty":            {"fa": "❌ فایل CSV هیچ ردیف داده‌ای ندارد.",
                              "en": "❌ The CSV file contains no data rows."},
    "csv_preview":          {"fa": "📊 پیش‌نمایش وارد کردن CSV:\n\n"
                                   "📁 فایل: {filename}\n"
                                   "📋 کل ردیف‌ها: {total}\n"
                                   "✅ معتبر: {valid}\n"
                                   "❌ نامعتبر: {invalid}",
                              "en": "📊 CSV Import Preview:\n\n"
                                   "📁 File: {filename}\n"
                                   "📋 Total rows: {total}\n"
                                   "✅ Valid: {valid}\n"
                                   "❌ Invalid: {invalid}"},
    "csv_errors_header":    {"fa": "⚠️ خطاهای یافت‌شده:",          "en": "⚠️ Errors found:"},
    "csv_confirm_question": {"fa": "ردیف‌های معتبر وارد بشن؟",     "en": "Import the valid rows?"},
    "csv_btn_confirm":      {"fa": "✅ تأیید وارد کردن",             "en": "✅ Confirm Import"},
    "csv_btn_cancel":       {"fa": "❌ لغو",                         "en": "❌ Cancel"},
    "csv_imported":         {"fa": "✅ {count} ردیف با موفقیت وارد شد و در صف انتظار قرار گرفت.",
                              "en": "✅ {count} row(s) successfully imported into the pending queue."},
    "csv_import_cancelled": {"fa": "↩️ وارد کردن CSV لغو شد.",      "en": "↩️ CSV import cancelled."},
    "csv_no_valid_rows":    {"fa": "❌ هیچ ردیف معتبری برای وارد کردن وجود ندارد.",
                              "en": "❌ No valid rows to import."},
    "csv_import_error":     {"fa": "❌ خطا در ذخیره‌سازی:\n{err}",  "en": "❌ Error during save:\n{err}"},

    # ── Pending Resources (Phase 3) ───────────────────────────────────────────
    "btn_pending":              {"fa": "📦 منابع در انتظار",              "en": "📦 Pending Resources"},
    "pending_overview":         {
        "fa": (
            "📦 منابع در انتظار\n\n"
            "📋 کل: {total}\n"
            "⏳ بدون فایل: {no_file}\n"
            "✅ با فایل / آماده انتشار: {has_file}\n\n"
            "📘 کتاب‌ها: {books}   📄 مقالات: {articles}"
        ),
        "en": (
            "📦 Pending Resources\n\n"
            "📋 Total: {total}\n"
            "⏳ Without file: {no_file}\n"
            "✅ With file / ready to publish: {has_file}\n\n"
            "📘 Books: {books}   📄 Articles: {articles}"
        ),
    },
    "pending_empty":            {"fa": "📭 هیچ منبع در انتظاری وجود ندارد.",
                                  "en": "📭 No pending resources found."},
    "btn_pending_list_all":     {"fa": "📋 نمایش همه",                   "en": "📋 Show All"},
    "btn_pending_list_nofile":  {"fa": "⏳ بدون فایل",                   "en": "⏳ Without File"},
    "btn_pending_list_ready":   {"fa": "✅ آماده انتشار",                 "en": "✅ Ready to Publish"},
    "btn_pending_enter_id":     {"fa": "🔢 ورود شناسه",                  "en": "🔢 Enter ID"},
    "btn_pending_back":         {"fa": "⬅️ بازگشت به منابع در انتظار",    "en": "⬅️ Back to Pending"},

    "pending_list_header":      {"fa": "📦 منابع در انتظار ({filter}) — صفحه {page}/{total_pages}:\n",
                                  "en": "📦 Pending Resources ({filter}) — page {page}/{total_pages}:\n"},
    "pending_filter_all":       {"fa": "همه",       "en": "All"},
    "pending_filter_nofile":    {"fa": "بدون فایل", "en": "No File"},
    "pending_filter_ready":     {"fa": "آماده",     "en": "Ready"},

    "pending_ask_id":           {"fa": "🔢 شناسه داخلی (P-ID) منبع در انتظار رو وارد کن:\n"
                                       "مثلاً: P27 یا فقط عدد 27",
                                  "en": "🔢 Enter the internal pending ID (P-ID):\n"
                                       "e.g. P27 or just 27"},
    "pending_not_found":        {"fa": "❌ منبعی با شناسه P{pid} پیدا نشد.",
                                  "en": "❌ No pending resource found with ID P{pid}."},
    "pending_already_done":     {"fa": "⚠️ این منبع قبلاً منتشر یا رد شده (وضعیت: {status}).",
                                  "en": "⚠️ This resource has already been published or rejected (status: {status})."},

    "pending_detail":           {
        "fa": (
            "📦 جزئیات منبع در انتظار\n\n"
            "🆔 شناسه: P{pid}\n"
            "📂 نوع: {rtype}\n"
            "📘 عنوان: {title}\n"
            "✍ نویسنده: {author}\n"
            "📅 سال: {year}\n"
            "{edition_line}"
            "🌌 فیلد: {field}\n"
            "🌐 زبان: {lang}\n"
            "{article_meta}"
            "{desc_line}"
            "\n"
            "📁 فایل: {file_status}\n"
            "🔖 وضعیت: {status}"
        ),
        "en": (
            "📦 Pending Resource Detail\n\n"
            "🆔 ID: P{pid}\n"
            "📂 Type: {rtype}\n"
            "📕 Title: {title}\n"
            "✍ Author: {author}\n"
            "📅 Year: {year}\n"
            "{edition_line}"
            "🌌 Field: {field}\n"
            "🌐 Language: {lang}\n"
            "{article_meta}"
            "{desc_line}"
            "\n"
            "📁 File: {file_status}\n"
            "🔖 Status: {status}"
        ),
    },
    "pending_has_file_warn":    {"fa": "⚠️ این منبع از قبل فایل دارد ({fname}).\n"
                                       "ارسال فایل جدید، فایل قبلی رو جایگزین می‌کند.",
                                  "en": "⚠️ This resource already has a file ({fname}).\n"
                                       "Sending a new file will replace it."},
    "btn_send_file":            {"fa": "📎 ارسال فایل",                  "en": "📎 Send File"},
    "btn_replace_file":         {"fa": "🔄 جایگزینی فایل",               "en": "🔄 Replace File"},

    "pending_ask_file":         {"fa": "📤 فایل منبع رو بفرست (PDF، ZIP یا DjVu):\n"
                                       "برای لغو دکمه لغو رو بزن.",
                                  "en": "📤 Send the resource file (PDF, ZIP, or DjVu):\n"
                                       "Press Cancel to abort."},
    "pending_file_only":        {"fa": "❗️ فقط فایل‌های PDF، ZIP و DjVu قبول می‌شن.",
                                  "en": "❗️ Only PDF, ZIP, and DjVu files are accepted."},
    "pending_no_active":        {"fa": "⚠️ هیچ منبع در انتظار فعالی برای دریافت فایل وجود ندارد.",
                                  "en": "⚠️ No active pending resource is awaiting a file."},
    "pending_stale":            {"fa": "⚠️ منبع P{pid} دیگر در حالت در انتظار نیست (شاید حذف یا منتشر شده).",
                                  "en": "⚠️ Resource P{pid} is no longer pending (it may have been deleted or published)."},

    "pending_file_saved":       {
        "fa": (
            "✅ فایل دریافت شد\n\n"
            "🆔 #{pid} — {title}\n"
            "📋 متادیتا: ✅\n"
            "📁 فایل: ✅ ({fname})\n"
            "🔖 وضعیت: آماده انتشار"
        ),
        "en": (
            "✅ File received\n\n"
            "🆔 #{pid} — {title}\n"
            "📋 Metadata: ✅\n"
            "📁 File: ✅ ({fname})\n"
            "🔖 Status: Ready to publish"
        ),
    },
    "pending_file_error":       {"fa": "❌ خطا در ذخیره فایل:\n{err}",
                                  "en": "❌ Error saving file:\n{err}"},

    # ── Publish (Phase 4) ─────────────────────────────────────────────────────
    "publish_confirm": {
        "fa": (
            "🚀 تأیید انتشار\n\n"
            "🆔 شناسه در انتظار: P{pid}\n"
            "📂 نوع: {rtype}\n"
            "📘 عنوان: {title}\n"
            "✍ نویسنده: {author}\n"
            "🌌 فیلد: {field}\n"
            "🌐 زبان: {lang}\n"
            "{year_line}"
            "{edition_line}"
            "{article_meta}"
            "📁 فایل: {fname}\n\n"
            "آیا این منبع منتشر شود؟"
        ),
        "en": (
            "🚀 Publish Confirmation\n\n"
            "🆔 Pending ID: P{pid}\n"
            "📂 Type: {rtype}\n"
            "📕 Title: {title}\n"
            "✍ Author: {author}\n"
            "🌌 Field: {field}\n"
            "🌐 Language: {lang}\n"
            "{year_line}"
            "{edition_line}"
            "{article_meta}"
            "📁 File: {fname}\n\n"
            "Publish this resource?"
        ),
    },
    "publish_success": {
        "fa": (
            "✅ منبع با موفقیت منتشر شد\n"
            "📘 {title}\n"
            "🔖 <code>{disp}</code>\n"
            "Pending #P{pid} منتشر شد."
        ),
        "en": (
            "✅ Resource published successfully\n"
            "📘 {title}\n"
            "🔖 <code>{disp}</code>\n"
            "Pending #P{pid} is now published."
        ),
    },
    "publish_error":        {"fa": "❌ خطا در انتشار:\n{err}",
                              "en": "❌ Publish failed:\n{err}"},
    "publish_not_ready":    {"fa": "⚠️ این منبع آماده انتشار نیست (وضعیت: {status}).",
                              "en": "⚠️ This resource is not ready to publish (status: {status})."},
    "publish_already":      {"fa": "🔒 این منبع قبلاً منتشر شده — انتشار مجدد مجاز نیست.",
                              "en": "🔒 This resource has already been published — double-publish blocked."},
    "publish_dup_warning": {
        "fa": (
            "⚠️ احتمال تکراری بودن\n\n"
            "منبع مشابهی در کتابخانه وجود دارد:\n"
            "📘 {dup_title}\n"
            "✍ {dup_author}\n"
            "🔖 <code>{dup_disp}</code>\n\n"
            "آیا با وجود این موضوع منتشر شود؟"
        ),
        "en": (
            "⚠️ Possible duplicate detected\n\n"
            "A similar resource already exists in the Library:\n"
            "📘 {dup_title}\n"
            "✍ {dup_author}\n"
            "🔖 <code>{dup_disp}</code>\n\n"
            "Publish anyway?"
        ),
    },
    "btn_publish":          {"fa": "🚀 انتشار",           "en": "🚀 Publish"},
    "btn_publish_anyway":   {"fa": "🚀 انتشار به‌هرحال",  "en": "🚀 Publish Anyway"},
    "btn_view_existing":    {"fa": "👁 مشاهده موجود",      "en": "👁 View Existing"},
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


def _resolve_resource(text: str):
    """Resolve a book or article by numeric ID or display ID (e.g. QM-B7 / QM-A3)."""
    text = text.strip()
    if text.isdigit():
        return database.get_resource(int(text))
    return database.find_resource_by_display_id(text) or database.find_book_by_display_id(text)

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
    kb.add(
        types.KeyboardButton(tr("btn_csv_import", lang)),
        types.KeyboardButton(tr("btn_pending", lang)),
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
        types.InlineKeyboardButton("فارسی", callback_data="adm_lang:fa"),
        types.InlineKeyboardButton("English", callback_data="adm_lang:en"),
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


def csv_import_confirm_keyboard(lang: str) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(tr("csv_btn_confirm", lang), callback_data="adm_csv_import:yes"),
        types.InlineKeyboardButton(tr("csv_btn_cancel",  lang), callback_data="adm_csv_import:no"),
    )
    return markup


def pending_overview_keyboard(lang: str) -> types.InlineKeyboardMarkup:
    """Inline keyboard shown on the Pending Resources overview screen."""
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(tr("btn_pending_list_all",    lang), callback_data="adm_pnd:list:all:0"),
        types.InlineKeyboardButton(tr("btn_pending_list_nofile", lang), callback_data="adm_pnd:list:nofile:0"),
    )
    markup.row(
        types.InlineKeyboardButton(tr("btn_pending_list_ready",  lang), callback_data="adm_pnd:list:ready:0"),
        types.InlineKeyboardButton(tr("btn_pending_enter_id",    lang), callback_data="adm_pnd:enter_id"),
    )
    return markup


def pending_detail_keyboard(lang: str, pending_id: int, has_file: bool) -> types.InlineKeyboardMarkup:
    """Inline keyboard shown for a single pending resource."""
    markup = types.InlineKeyboardMarkup()
    file_btn_key = "btn_replace_file" if has_file else "btn_send_file"
    markup.row(
        types.InlineKeyboardButton(tr(file_btn_key, lang), callback_data=f"adm_pnd:send_file:{pending_id}"),
    )
    if has_file:
        markup.row(
            types.InlineKeyboardButton(tr("btn_publish", lang), callback_data=f"adm_pnd:publish:{pending_id}"),
        )
    markup.row(
        types.InlineKeyboardButton(tr("btn_pending_back", lang), callback_data="adm_pnd:overview"),
    )
    return markup


def pending_file_received_keyboard(lang: str, pending_id: int = 0) -> types.InlineKeyboardMarkup:
    """Inline keyboard shown after a file is successfully attached to a pending resource."""
    markup = types.InlineKeyboardMarkup()
    if pending_id:
        markup.row(
            types.InlineKeyboardButton(tr("btn_publish", lang), callback_data=f"adm_pnd:publish:{pending_id}"),
        )
    markup.row(
        types.InlineKeyboardButton(tr("btn_replace_file",   lang), callback_data="adm_pnd:replace_same"),
        types.InlineKeyboardButton(tr("btn_pending_back",   lang), callback_data="adm_pnd:overview"),
    )
    return markup


def publish_confirm_keyboard(lang: str, pending_id: int) -> types.InlineKeyboardMarkup:
    """Inline keyboard for the publish confirmation screen."""
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(tr("btn_publish", lang),  callback_data=f"adm_pnd:do_publish:{pending_id}"),
        types.InlineKeyboardButton(tr("btn_confirm_no", lang), callback_data=f"adm_pnd:detail:{pending_id}"),
    )
    return markup


def publish_dup_keyboard(lang: str, pending_id: int, dup_id: int) -> types.InlineKeyboardMarkup:
    """Inline keyboard shown when a duplicate is detected before publishing."""
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(tr("btn_view_existing", lang),  callback_data=f"adm_pnd:view_lib:{dup_id}"),
    )
    markup.row(
        types.InlineKeyboardButton(tr("btn_publish_anyway", lang), callback_data=f"adm_pnd:do_publish:{pending_id}:force"),
        types.InlineKeyboardButton(tr("btn_confirm_no", lang),     callback_data=f"adm_pnd:detail:{pending_id}"),
    )
    return markup


_PENDING_PAGE_SIZE = 8   # items per page in the pending list


def resource_type_keyboard(lang: str) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(tr("btn_type_book", lang),    callback_data="adm_rtype:book"),
        types.InlineKeyboardButton(tr("btn_type_article", lang), callback_data="adm_rtype:article"),
    )
    return markup


def edit_field_keyboard(lang: str, resource_type: str = "book") -> types.InlineKeyboardMarkup:
    """کیبورد انتخاب فیلد ویرایش — بر اساس resource_type فیلدهای مناسب نمایش داده می‌شود."""
    markup = types.InlineKeyboardMarkup()
    # فیلدهای مشترک بین کتاب و مقاله
    markup.row(
        types.InlineKeyboardButton(tr("edit_field_title", lang),   callback_data="adm_editfield:title"),
        types.InlineKeyboardButton(tr("edit_field_author", lang),  callback_data="adm_editfield:author"),
    )
    markup.row(
        types.InlineKeyboardButton(tr("edit_field_desc", lang),    callback_data="adm_editfield:description"),
        types.InlineKeyboardButton(tr("edit_field_physics", lang), callback_data="adm_editfield:physics_field"),
    )

    if resource_type == "article":
        # فیلدهای اختصاصی مقاله
        markup.row(
            types.InlineKeyboardButton(tr("edit_field_journal", lang), callback_data="adm_editfield:journal"),
            types.InlineKeyboardButton(tr("edit_field_doi", lang),     callback_data="adm_editfield:doi"),
        )
        markup.row(
            types.InlineKeyboardButton(tr("edit_field_volume", lang),  callback_data="adm_editfield:volume"),
            types.InlineKeyboardButton(tr("edit_field_issue", lang),   callback_data="adm_editfield:issue"),
        )
        markup.row(
            types.InlineKeyboardButton(tr("edit_field_pages", lang),   callback_data="adm_editfield:pages"),
            types.InlineKeyboardButton(tr("edit_field_pub_date", lang), callback_data="adm_editfield:publication_date"),
        )
        markup.row(
            types.InlineKeyboardButton(tr("edit_field_url", lang),     callback_data="adm_editfield:url"),
        )
        markup.row(
            types.InlineKeyboardButton(tr("edit_field_year", lang),    callback_data="adm_editfield:year"),
        )
    else:
        # فیلدهای اختصاصی کتاب
        markup.row(
            types.InlineKeyboardButton(tr("edit_field_year", lang),    callback_data="adm_editfield:year"),
            types.InlineKeyboardButton(tr("edit_field_edition", lang), callback_data="adm_editfield:edition"),
        )
    return markup


def open_panel_markup(lang: str) -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(tr("open_panel_btn", lang), callback_data="adm_open_panel"))
    return markup


# Book Summary

def summary_text(data: dict, lang: str) -> str:
    field_fa, field_en = database.PHYSICS_FIELDS.get(
        data.get("physics_field", ""), (tr("unknown_field", lang), tr("unknown_field", lang))
    )
    field_label = field_fa if lang == "fa" else field_en
    lang_label = tr("lang_fa", lang) if data.get("language") == "fa" else tr("lang_en", lang)
    rtype = data.get("resource_type", "book")
    rtype_label = tr("btn_type_article", lang) if rtype == "article" else tr("btn_type_book", lang)

    lines = [
        tr("summary_title", lang),
        tr("summary_resource_type", lang, v=rtype_label),
        tr("summary_book", lang, v=data.get("title", "-")),
        tr("summary_author", lang, v=data.get("author", "-")),
        tr("summary_lang", lang, v=lang_label),
        tr("summary_field", lang, v=field_label),
    ]

    if rtype == "article":
        if data.get("journal"):
            lines.append(tr("summary_journal", lang, v=data["journal"]))
        if data.get("volume"):
            lines.append(tr("summary_volume", lang, v=data["volume"]))
        if data.get("issue"):
            lines.append(tr("summary_issue", lang, v=data["issue"]))
        if data.get("pages"):
            lines.append(tr("summary_pages", lang, v=data["pages"]))
        if data.get("doi"):
            lines.append(tr("summary_doi", lang, v=data["doi"]))
        if data.get("url"):
            lines.append(tr("summary_url", lang, v=data["url"]))
        if data.get("publication_date"):
            lines.append(tr("summary_pub_date", lang, v=data["publication_date"]))
    else:
        lines.append(tr("summary_year", lang, v=data.get("year") or "-"))
        lines.append(tr("summary_edition", lang, v=data.get("edition") or "-"))

    lines.append(tr("summary_desc", lang, v=data.get("description") or "-"))
    lines.append(tr("summary_file", lang, v=data.get("file_name") or "-"))
    return "\n".join(lines)


def _book_summary_text(book, lang: str) -> str:
    keys = book.keys() if hasattr(book, "keys") else book

    def _g(k):
        try:
            v = book[k]
            return v if v is not None else ""
        except (KeyError, IndexError):
            return ""

    return summary_text({
        "title":            _g("title"),
        "author":           _g("author"),
        "language":         _g("language"),
        "physics_field":    _g("physics_field"),
        "resource_type":    book["resource_type"] if "resource_type" in keys else "book",
        "year":             _g("year"),
        "edition":          _g("edition"),
        "description":      _g("description"),
        "file_name":        _g("file_name"),
        "doi":              _g("doi"),
        "journal":          _g("journal"),
        "volume":           _g("volume"),
        "issue":            _g("issue"),
        "pages":            _g("pages"),
        "url":              _g("url"),
        "publication_date": _g("publication_date"),
    }, lang) + f"\n🔖 <code>{_disp(book)}</code>"


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

# Backup helpers

def _make_backup_file(suffix: str = "") -> str:
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
        # Send to all Admins
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        tmp_path = broadcast_backup_to_admins(bot, "backup_caption", time=time_str)
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    finally:
        _backup_restore_lock.release()


# Restore helpers

def _validate_restore_db(path: str) -> bool:
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
            # emergency_path
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
    try:
        _restore_sessions[uid] = {
            "step": "wait_file",
            "expire": datetime.now().timestamp() + RESTORE_TIMEOUT,
            "emergency_path": None,
            "tmp_db_path": None,
        }
        bot.send_message(message.chat.id, tr("restore_prompt", lang))
    except Exception:
        _backup_restore_lock.release()
        raise


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
    # Delete Temporary files
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
    uid = message.from_user.id
    if uid not in _restore_sessions:
        return False
    sess = _restore_sessions[uid]
    if sess.get("step") != "wait_file":
        return False

    # Check Time-out
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

    # download file to temp
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

    # Emergency Backup
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
    uid = message.from_user.id
    if uid not in _restore_sessions:
        return False
    sess = _restore_sessions[uid]
    if sess.get("step") != "wait_confirm":
        return False

    
    if datetime.now().timestamp() > sess["expire"]:
        _restore_sessions.pop(uid)
        _backup_restore_lock.release()
        bot.send_message(message.chat.id, tr("restore_timeout", get_lang(uid)))
        return True

    text = message.text.strip()
    lang = get_lang(uid)

    if text != "CONFIRM RESTORE":
        # Cancel all except CONFIRM RESTORE
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

    # Run Restore
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
        bot.send_message(message.chat.id, tr("ask_resource_id_edit", lang), reply_markup=cancel_keyboard(lang))
        return True

    if text in (T["btn_list"]["fa"], T["btn_list"]["en"]):
        _show_list(bot, message, lang)
        return True

    if text in (T["btn_delete"]["fa"], T["btn_delete"]["en"]):
        admin_sessions[uid] = {"step": "wait_delete_id"}
        bot.send_message(
            message.chat.id,
            tr("ask_resource_id_del", lang),
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

    if text in (T["btn_csv_import"]["fa"], T["btn_csv_import"]["en"]):
        admin_sessions[uid] = {"step": "wait_csv_file"}
        bot.send_message(message.chat.id, tr("csv_ask_file", lang), reply_markup=cancel_keyboard(lang))
        return True

    if text in (T["btn_pending"]["fa"], T["btn_pending"]["en"]):
        admin_sessions.pop(uid, None)
        _show_pending_overview(bot, message.chat.id, lang)
        return True

    if text in (T["btn_exit"]["fa"], T["btn_exit"]["en"]):
        admin_sessions.pop(uid, None)
        return False   

    if uid not in admin_sessions:
        return False

    step = admin_sessions[uid].get("step", "")

    # Article: allow skipping PDF upload
    if _handle_article_skip_file(bot, message, uid, lang):
        return True

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
        rtype = admin_sessions[uid]["data"].get("resource_type", "book")
        if rtype == "article":
            # Articles don't have an "edition" — skip straight to journal info.
            admin_sessions[uid]["step"] = "wait_journal"
            bot.send_message(message.chat.id, tr("ask_journal", lang), reply_markup=skip_cancel_keyboard(lang))
        else:
            admin_sessions[uid]["step"] = "wait_edition"
            bot.send_message(
                message.chat.id,
                tr("ask_edition", lang),
                reply_markup=skip_cancel_keyboard(lang)
            )
        return True

    # Edition (books only)
    if step == "wait_edition":
        admin_sessions[uid]["data"]["edition"] = "" if text == tr("btn_skip", lang) else text
        admin_sessions[uid]["step"] = "wait_desc"
        bot.send_message(
            message.chat.id,
            tr("ask_desc", lang),
            reply_markup=skip_cancel_keyboard(lang)
        )
        return True

    # Description / Comment — last field before confirmation, for both books and articles
    if step == "wait_desc":
        admin_sessions[uid]["data"]["description"] = "" if text == tr("btn_skip", lang) else text
        admin_sessions[uid]["step"] = "confirm"
        data = admin_sessions[uid]["data"]
        bot.send_message(message.chat.id, summary_text(data, lang), reply_markup=admin_keyboard(lang))
        bot.send_message(message.chat.id, tr("confirm_question", lang), reply_markup=confirm_keyboard(lang))
        return True

    # Article-specific steps

    if step == "wait_journal":
        admin_sessions[uid]["data"]["journal"] = "" if text == tr("btn_skip", lang) else text
        admin_sessions[uid]["step"] = "wait_volume"
        bot.send_message(message.chat.id, tr("ask_volume", lang), reply_markup=skip_cancel_keyboard(lang))
        return True

    if step == "wait_volume":
        admin_sessions[uid]["data"]["volume"] = "" if text == tr("btn_skip", lang) else text
        admin_sessions[uid]["step"] = "wait_issue"
        bot.send_message(message.chat.id, tr("ask_issue", lang), reply_markup=skip_cancel_keyboard(lang))
        return True

    if step == "wait_issue":
        admin_sessions[uid]["data"]["issue"] = "" if text == tr("btn_skip", lang) else text
        admin_sessions[uid]["step"] = "wait_pages"
        bot.send_message(message.chat.id, tr("ask_pages", lang), reply_markup=skip_cancel_keyboard(lang))
        return True

    if step == "wait_pages":
        admin_sessions[uid]["data"]["pages"] = "" if text == tr("btn_skip", lang) else text
        admin_sessions[uid]["step"] = "wait_doi"
        bot.send_message(message.chat.id, tr("ask_doi", lang), reply_markup=skip_cancel_keyboard(lang))
        return True

    if step == "wait_doi":
        admin_sessions[uid]["data"]["doi"] = "" if text == tr("btn_skip", lang) else text
        admin_sessions[uid]["step"] = "wait_url"
        bot.send_message(message.chat.id, tr("ask_url", lang), reply_markup=skip_cancel_keyboard(lang))
        return True

    if step == "wait_url":
        admin_sessions[uid]["data"]["url"] = "" if text == tr("btn_skip", lang) else text
        admin_sessions[uid]["step"] = "wait_pub_date"
        bot.send_message(message.chat.id, tr("ask_pub_date", lang), reply_markup=skip_cancel_keyboard(lang))
        return True

    if step == "wait_pub_date":
        admin_sessions[uid]["data"]["publication_date"] = "" if text == tr("btn_skip", lang) else text
        admin_sessions[uid]["step"] = "wait_desc"
        bot.send_message(
            message.chat.id,
            tr("ask_desc", lang),
            reply_markup=skip_cancel_keyboard(lang)
        )
        return True

    # Delete Book or Article
    if step == "wait_delete_id":
        resource = _resolve_resource(text)
        if not resource:
            bot.send_message(message.chat.id, tr("resource_not_found", lang, id=text))
        else:
            disp = _disp(resource)
            database.delete_book(resource["id"])
            bot.send_message(message.chat.id, tr("resource_deleted", lang, title=resource["title"], disp=disp), parse_mode="HTML")
        admin_sessions.pop(uid, None)
        bot.send_message(message.chat.id, tr("back_to_panel", lang), reply_markup=admin_keyboard(lang))
        return True

    if step == "wait_edit_id":
        resource = _resolve_resource(text)
        if not resource:
            bot.send_message(message.chat.id, tr("resource_not_found", lang, id=text))
            admin_sessions.pop(uid, None)
            bot.send_message(message.chat.id, tr("back_to_panel", lang), reply_markup=admin_keyboard(lang))
            return True
        rtype = "article" if resource["resource_type"] == "article" else "book"
        admin_sessions[uid] = {
            "step": "edit_choose_field",
            "book_id": resource["id"],
            "resource_type": rtype,
        }
        bot.send_message(
            message.chat.id,
            tr("edit_found", lang, summary=_book_summary_text(resource, lang)),
            reply_markup=admin_keyboard(lang),
            parse_mode="HTML",
        )
        bot.send_message(message.chat.id, "👇", reply_markup=edit_field_keyboard(lang, rtype))
        return True

    if step == "wait_edit_value":
        field   = admin_sessions[uid]["edit_field"]
        book_id = admin_sessions[uid]["book_id"]
        value: object = text

        # فیلدهایی که قابل Skip هستند
        skippable = {
            "year", "doi", "journal", "volume", "issue",
            "pages", "publication_date", "url", "edition", "description",
        }
        if field in skippable and text == tr("btn_skip", lang):
            value = None if field == "year" else ""
        elif field == "year":
            try:
                value = int(text)
            except ValueError:
                bot.send_message(message.chat.id, tr("year_not_number", lang))
                return True

        database.update_book(book_id, **{field: value})
        resource = database.get_resource(book_id)
        admin_sessions.pop(uid, None)
        bot.send_message(
            message.chat.id,
            tr("edit_saved", lang, disp=_disp(resource)),
            reply_markup=admin_keyboard(lang),
            parse_mode="HTML",
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

    # ── Phase 3: Pending ID entry ──────────────────────────────────────────────
    if step == "wait_pending_id":
        # Accept "P27", "p027", or bare "27"
        raw = text.strip().upper().lstrip("P")
        if not raw.isdigit():
            bot.send_message(message.chat.id, tr("not_a_number", lang))
            return True
        pid = int(raw)
        admin_sessions.pop(uid, None)
        _show_pending_detail(bot, message.chat.id, lang, pid)
        return True

    # ── Phase 3: Pending file upload ───────────────────────────────────────────
    # Text while in wait_pending_file → only Cancel is valid (handled above)
    if step == "wait_pending_file":
        # Any non-cancel text is ignored with a gentle reminder
        bot.send_message(message.chat.id, tr("pending_ask_file", lang), reply_markup=cancel_keyboard(lang))
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

    # ── CSV import document handler (Phase 2)
    if admin_sessions[uid].get("step") == "wait_csv_file":
        _handle_csv_upload(bot, message, uid)
        return True

    # ── Phase 3: Pending resource file upload
    if admin_sessions[uid].get("step") == "wait_pending_file":
        _handle_pending_file_upload(bot, message, uid)
        return True

    if admin_sessions[uid].get("step") != "wait_file":
        return False

    lang = get_lang(uid)
    doc = message.document
    ALLOWED_EXTENSIONS = {".pdf", ".zip", ".djvu"}
    if not any((doc.file_name or "").lower().endswith(ext) for ext in ALLOWED_EXTENSIONS):
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


def _handle_article_skip_file(bot, message: types.Message, uid: int, lang: str) -> bool:
    """Called when user sends skip-text while in wait_file step for an article."""
    if uid not in admin_sessions:
        return False
    sess = admin_sessions[uid]
    if sess.get("step") != "wait_file":
        return False
    if sess.get("data", {}).get("resource_type") != "article":
        return False
    if message.text and message.text.strip() == tr("btn_skip", lang):
        admin_sessions[uid]["data"].setdefault("file_id", "")
        admin_sessions[uid]["data"].setdefault("file_name", "")
        admin_sessions[uid]["data"].setdefault("file_size", 0)
        admin_sessions[uid]["step"] = "wait_title"
        bot.send_message(
            message.chat.id,
            "📄 حالا عنوان مقاله رو بنویس:" if lang == "fa" else "📄 Now type the article's title:",
            reply_markup=cancel_keyboard(lang)
        )
        return True
    return False


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

    # Resource type selection (first step of add flow)
    if data.startswith("adm_rtype:"):
        if uid not in admin_sessions or admin_sessions[uid].get("step") != "wait_resource_type":
            bot.answer_callback_query(callback.id, tr("wrong_step", lang))
            return True
        rtype = data.split(":")[1]  # "book" or "article"
        admin_sessions[uid]["data"]["resource_type"] = rtype
        bot.answer_callback_query(callback.id, "✅")
        if rtype == "article":
            admin_sessions[uid]["step"] = "wait_file"
            bot.send_message(callback.message.chat.id, tr("ask_pdf_optional", lang),
                             reply_markup=skip_cancel_keyboard(lang))
        else:
            admin_sessions[uid]["step"] = "wait_file"
            bot.send_message(callback.message.chat.id, tr("send_pdf", lang),
                             reply_markup=cancel_keyboard(lang))
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
        rtype = d.get("resource_type", "book")

        # For articles, file_id is optional; for books it is required
        if rtype == "book":
            required = ["file_id", "title", "author", "language", "physics_field"]
        else:
            required = ["title", "author", "language", "physics_field"]
        missing = [k for k in required if not d.get(k)]
        if missing:
            bot.send_message(
                callback.message.chat.id,
                tr("missing_fields", lang, fields=", ".join(missing)),
                reply_markup=admin_keyboard(lang)
            )
            admin_sessions.pop(uid, None)
            return True

        try:
            if rtype == "article":
                resource_id = database.add_resource(
                    title            = d["title"],
                    author           = d.get("author", ""),
                    language         = d["language"],
                    physics_field    = d["physics_field"],
                    resource_type    = "article",
                    file_id          = d.get("file_id", ""),
                    file_name        = d.get("file_name", ""),
                    file_size        = d.get("file_size", 0),
                    description      = d.get("description", ""),
                    doi              = d.get("doi", ""),
                    journal          = d.get("journal", ""),
                    volume           = d.get("volume", ""),
                    issue            = d.get("issue", ""),
                    pages            = d.get("pages", ""),
                    url              = d.get("url", ""),
                    publication_date = d.get("publication_date", ""),
                    added_by         = uid,
                )
                resource = database.get_resource(resource_id)
                bot.send_message(
                    callback.message.chat.id,
                    tr("saved_ok_article", lang, disp=_disp(resource), title=d["title"]),
                    reply_markup=admin_keyboard(lang),
                    parse_mode="HTML",
                )
                if _notify_callback:
                    _notify_callback(bot, d["physics_field"], d["title"], resource_id, "article")
            else:
                book_id = database.add_book(
                    title         = d["title"],
                    author        = d["author"],
                    language      = d["language"],
                    physics_field = d["physics_field"],
                    file_id       = d["file_id"],
                    file_name     = d["file_name"],
                    file_size     = d["file_size"],
                    description   = d.get("description", ""),
                    edition       = d.get("edition", ""),
                    year          = d.get("year"),
                    added_by      = uid,
                )
                book = database.get_book(book_id)
                bot.send_message(
                    callback.message.chat.id,
                    tr("saved_ok", lang, disp=_disp(book), title=d["title"]),
                    reply_markup=admin_keyboard(lang),
                    parse_mode="HTML",
                )
                if _notify_callback:
                    _notify_callback(bot, d["physics_field"], d["title"], book_id, "book")
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
        # فیلدهایی که می‌توان رد کرد (Skip)
        skippable = {
            "year", "edition", "description",
            "doi", "journal", "volume", "issue", "pages", "publication_date", "url",
        }
        kb = skip_cancel_keyboard(lang) if field in skippable else cancel_keyboard(lang)
        bot.send_message(callback.message.chat.id, tr("ask_new_value", lang), reply_markup=kb)
        return True

    
    if data.startswith("adm_editfieldval:"):
        if uid not in admin_sessions or admin_sessions[uid].get("step") != "wait_edit_new_field":
            bot.answer_callback_query(callback.id, tr("wrong_step", lang))
            return True
        new_field = data.split(":", 1)[1]
        book_id = admin_sessions[uid]["book_id"]
        database.update_book(book_id, physics_field=new_field)
        resource = database.get_resource(book_id)
        bot.answer_callback_query(callback.id, "✅")
        admin_sessions.pop(uid, None)
        bot.send_message(
            callback.message.chat.id,
            tr("edit_saved", lang, disp=_disp(resource)),
            reply_markup=admin_keyboard(lang),
            parse_mode="HTML",
        )
        return True

    # ── Phase 3: Pending Resources callbacks ──────────────────────────────────
    if data.startswith("adm_pnd:"):
        bot.answer_callback_query(callback.id)
        parts = data.split(":")   # ["adm_pnd", action, ...]

        action = parts[1] if len(parts) > 1 else ""

        # Overview screen
        if action == "overview":
            admin_sessions.pop(uid, None)
            _show_pending_overview(bot, callback.message.chat.id, lang)
            return True

        # Paginated list: adm_pnd:list:<filter>:<page>
        if action == "list":
            filter_key = parts[2] if len(parts) > 2 else "all"
            page       = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            _pending_list_page(
                bot, callback.message.chat.id, lang, filter_key, page,
                edit_message_id=callback.message.message_id,
            )
            return True

        # Show detail for a specific pending resource: adm_pnd:detail:<pid>
        if action == "detail":
            if len(parts) > 2 and parts[2].isdigit():
                admin_sessions.pop(uid, None)
                _show_pending_detail(bot, callback.message.chat.id, lang, int(parts[2]))
            return True

        # Enter ID manually
        if action == "enter_id":
            admin_sessions[uid] = {"step": "wait_pending_id"}
            bot.send_message(
                callback.message.chat.id,
                tr("pending_ask_id", lang),
                reply_markup=cancel_keyboard(lang),
            )
            return True

        # Initiate file send for a pending resource: adm_pnd:send_file:<pid>
        if action == "send_file":
            if len(parts) > 2 and parts[2].isdigit():
                pid = int(parts[2])
                row = database.get_pending_resource(pid)
                if not row or row["status"] in ("published", "rejected"):
                    bot.send_message(
                        callback.message.chat.id,
                        tr("pending_stale", lang, pid=pid),
                        reply_markup=admin_keyboard(lang),
                    )
                    return True
                admin_sessions[uid] = {"step": "wait_pending_file", "pending_id": pid}
                bot.send_message(
                    callback.message.chat.id,
                    tr("pending_ask_file", lang),
                    reply_markup=cancel_keyboard(lang),
                )
            return True

        # Replace file after successful upload (same resource, re-enter wait state)
        if action == "replace_same":
            sess = admin_sessions.get(uid, {})
            pid  = sess.get("last_pid")
            if not pid:
                _show_pending_overview(bot, callback.message.chat.id, lang)
                return True
            row = database.get_pending_resource(pid)
            if not row or row["status"] in ("published", "rejected"):
                admin_sessions.pop(uid, None)
                bot.send_message(
                    callback.message.chat.id,
                    tr("pending_stale", lang, pid=pid),
                    reply_markup=admin_keyboard(lang),
                )
                return True
            admin_sessions[uid] = {"step": "wait_pending_file", "pending_id": pid}
            bot.send_message(
                callback.message.chat.id,
                tr("pending_ask_file", lang),
                reply_markup=cancel_keyboard(lang),
            )
            return True

        # ── Phase 4: Show publish confirmation ────────────────────────────────
        # adm_pnd:publish:<pid>
        if action == "publish":
            if len(parts) > 2 and parts[2].isdigit():
                pid = int(parts[2])
                _show_publish_confirm(bot, callback.message.chat.id, lang, pid)
            return True

        # ── Phase 4: Execute publish (optionally forced past dup warning) ─────
        # adm_pnd:do_publish:<pid>        — normal publish
        # adm_pnd:do_publish:<pid>:force  — publish despite duplicate warning
        if action == "do_publish":
            if len(parts) > 2 and parts[2].isdigit():
                pid   = int(parts[2])
                force = len(parts) > 3 and parts[3] == "force"
                _do_publish(bot, callback.message.chat.id, lang, pid, uid, force=force)
            return True

        # ── Phase 4: View an existing library resource (from dup warning) ─────
        # adm_pnd:view_lib:<lib_id>
        if action == "view_lib":
            if len(parts) > 2 and parts[2].isdigit():
                lib_id = int(parts[2])
                resource = database.get_resource(lib_id)
                if resource:
                    bot.send_message(
                        callback.message.chat.id,
                        _book_summary_text(resource, lang),
                        parse_mode="HTML",
                    )
                else:
                    bot.send_message(
                        callback.message.chat.id,
                        tr("resource_not_found", lang, id=lib_id),
                    )
            return True

        return True   # unknown adm_pnd sub-action — swallow gracefully

    if data.startswith("adm_csv_import:"):
        if uid not in admin_sessions or admin_sessions[uid].get("step") != "wait_csv_confirm":
            bot.answer_callback_query(callback.id, tr("wrong_step", lang))
            return True
        bot.answer_callback_query(callback.id)
        choice = data.split(":")[1]
        if choice == "no":
            admin_sessions.pop(uid, None)
            bot.send_message(callback.message.chat.id, tr("csv_import_cancelled", lang),
                             reply_markup=admin_keyboard(lang))
            return True
        # Confirm — insert valid rows
        valid_rows = admin_sessions[uid].get("csv_valid_rows", [])
        admin_sessions.pop(uid, None)
        if not valid_rows:
            bot.send_message(callback.message.chat.id, tr("csv_no_valid_rows", lang),
                             reply_markup=admin_keyboard(lang))
            return True
        try:
            inserted = database.bulk_create_pending_resources(valid_rows)
            bot.send_message(callback.message.chat.id,
                             tr("csv_imported", lang, count=len(inserted)),
                             reply_markup=admin_keyboard(lang))
        except Exception as e:
            bot.send_message(callback.message.chat.id,
                             tr("csv_import_error", lang, err=e),
                             reply_markup=admin_keyboard(lang))
        return True

    return False

# ── Phase 3: Pending file upload handler ───────────────────────────────────────

def _handle_pending_file_upload(bot, message, uid: int) -> None:
    """Called when a document arrives while the admin is in wait_pending_file."""
    lang    = get_lang(uid)
    sess    = admin_sessions.get(uid, {})
    pid     = sess.get("pending_id")

    if not pid:
        admin_sessions.pop(uid, None)
        bot.send_message(message.chat.id, tr("pending_no_active", lang), reply_markup=admin_keyboard(lang))
        return

    # Re-fetch the pending row to guard against stale state
    row = database.get_pending_resource(pid)
    if not row or row["status"] in ("published", "rejected"):
        admin_sessions.pop(uid, None)
        bot.send_message(message.chat.id, tr("pending_stale", lang, pid=pid), reply_markup=admin_keyboard(lang))
        return

    doc = message.document
    ALLOWED_EXTENSIONS = {".pdf", ".zip", ".djvu"}
    if not any((doc.file_name or "").lower().endswith(ext) for ext in ALLOWED_EXTENSIONS):
        bot.send_message(message.chat.id, tr("pending_file_only", lang))
        return  # stay in wait_pending_file

    # Attach the file to the pending resource
    try:
        ok = database.attach_pending_file(
            pending_id=pid,
            file_id=doc.file_id,
            file_name=doc.file_name or "",
            file_size=doc.file_size or 0,
        )
    except Exception as e:
        bot.send_message(message.chat.id, tr("pending_file_error", lang, err=e))
        return

    if not ok:
        admin_sessions.pop(uid, None)
        bot.send_message(message.chat.id, tr("pending_stale", lang, pid=pid), reply_markup=admin_keyboard(lang))
        return

    # Success — clear upload state but remember last pid for "Replace File" convenience
    admin_sessions.pop(uid, None)

    confirmation = tr(
        "pending_file_saved", lang,
        pid=pid,
        title=row["title"],
        fname=doc.file_name or doc.file_id,
    )
    bot.send_message(
        message.chat.id,
        confirmation,
        reply_markup=pending_file_received_keyboard(lang, pending_id=pid),
    )
    # Store last pid in a lightweight session for the "Replace File" button
    admin_sessions[uid] = {"step": "pending_file_done", "last_pid": pid}


# ── CSV Import helpers (Phase 2) ───────────────────────────────────────────────

# Maximum CSV file size accepted (bytes).
_CSV_MAX_BYTES = 5 * 1024 * 1024  # 5 MB

# Columns that MUST be present in every CSV (case-insensitive header match).
_CSV_REQUIRED_COLS = {"resource_type", "title", "author", "language", "physics_field"}

# All columns the importer recognises (optional ones are silently defaulted).
_CSV_ALL_COLS = _CSV_REQUIRED_COLS | {
    "description", "edition", "year",
    "doi", "journal", "volume", "issue", "pages", "url", "publication_date",
}


def _normalise_csv_headers(raw_headers: list[str]) -> dict[str, str]:
    """Return {normalised_lower: original} mapping for each header."""
    return {h.strip().lower(): h for h in raw_headers}


def _validate_csv_row(row_num: int, raw: dict, norm_map: dict[str, str]) -> tuple[dict | None, str | None]:
    """Validate a single CSV row dict (keys already lower-cased).

    Returns (cleaned_kwargs, None) on success or (None, error_message) on failure.
    cleaned_kwargs can be passed directly to database.create_pending_resource().
    """
    def get(col: str) -> str:
        return raw.get(col, "").strip()

    resource_type = get("resource_type").lower()
    if resource_type not in ("book", "article"):
        return None, f"row {row_num}: resource_type نامعتبر '{resource_type}' (باید 'book' یا 'article' باشد)"

    title = get("title")
    if not title:
        return None, f"row {row_num}: title خالی است"

    author = get("author")
    if not author:
        return None, f"row {row_num}: author خالی است"

    language = get("language").lower()
    if language not in ("fa", "en"):
        return None, f"row {row_num}: language نامعتبر '{language}' (باید 'fa' یا 'en' باشد)"

    physics_field = get("physics_field").strip()
    if physics_field not in database.PHYSICS_FIELDS:
        return None, (
            f"row {row_num}: physics_field نامعتبر '{physics_field}'. "
            f"مقادیر مجاز: {', '.join(sorted(database.PHYSICS_FIELDS))}"
        )

    # year: optional integer
    year_raw = get("year")
    year: int | None = None
    if year_raw:
        try:
            year = int(year_raw)
            if not (1000 <= year <= 2100):
                return None, f"row {row_num}: year خارج از محدوده ({year})"
        except ValueError:
            return None, f"row {row_num}: year باید عدد باشد ('{year_raw}')"

    kwargs: dict = {
        "resource_type":    resource_type,
        "title":            title,
        "author":           author,
        "language":         language,
        "physics_field":    physics_field,
        "description":      get("description"),
        "edition":          get("edition"),
        "year":             year,
        "doi":              get("doi"),
        "journal":          get("journal"),
        "volume":           get("volume"),
        "issue":            get("issue"),
        "pages":            get("pages"),
        "url":              get("url"),
        "publication_date": get("publication_date"),
    }
    return kwargs, None


def _parse_csv_bytes(raw_bytes: bytes) -> tuple[list[dict], list[str]]:
    """Decode and parse CSV bytes; return (list_of_row_dicts_lower_keys, errors).

    Tries UTF-8-with-BOM first, then UTF-8, then Windows-1256 (Persian).
    Row dicts use lower-cased, stripped column names as keys.
    """
    for encoding in ("utf-8-sig", "utf-8", "windows-1256", "latin-1"):
        try:
            text = raw_bytes.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        return [], ["فایل CSV قابل رمزگشایی نیست (encoding ناشناخته)"]

    reader = csv.DictReader(io.StringIO(text))
    try:
        raw_headers = reader.fieldnames or []
    except Exception as e:
        return [], [f"خطا در خواندن هدر CSV: {e}"]

    norm_map = _normalise_csv_headers(list(raw_headers))
    # Check required columns
    missing = _CSV_REQUIRED_COLS - set(norm_map.keys())
    if missing:
        found_str  = ", ".join(sorted(norm_map.keys())) or "(هیچ‌کدام)"
        missing_str = ", ".join(sorted(missing))
        return [], [f"ستون‌های ضروری ندارند: {missing_str}\nستون‌های موجود: {found_str}"]

    rows = []
    try:
        for raw_row in reader:
            # Normalise keys to lower-case stripped versions
            normalised = {k.strip().lower(): (v or "").strip() for k, v in raw_row.items() if k}
            rows.append(normalised)
    except Exception as e:
        return [], [f"خطا در خواندن ردیف‌های CSV: {e}"]

    return rows, []


def _handle_csv_upload(bot, message: types.Message, uid: int) -> None:
    """Process a document sent while the admin is in wait_csv_file step."""
    lang = get_lang(uid)
    doc  = message.document

    # Extension check
    if not (doc.file_name or "").lower().endswith(".csv"):
        bot.send_message(message.chat.id, tr("csv_not_csv", lang))
        return

    # Size check
    if doc.file_size and doc.file_size > _CSV_MAX_BYTES:
        bot.send_message(message.chat.id, tr("csv_too_large", lang))
        return

    # Download
    try:
        file_info    = bot.get_file(doc.file_id)
        raw_bytes    = bot.download_file(file_info.file_path)
    except Exception as e:
        bot.send_message(message.chat.id, tr("csv_parse_error", lang, err=e))
        return

    # Parse
    rows, parse_errors = _parse_csv_bytes(raw_bytes)
    if parse_errors:
        bot.send_message(message.chat.id, tr("csv_parse_error", lang, err="\n".join(parse_errors)))
        admin_sessions.pop(uid, None)
        bot.send_message(message.chat.id, tr("back_to_panel", lang), reply_markup=admin_keyboard(lang))
        return

    if not rows:
        bot.send_message(message.chat.id, tr("csv_empty", lang), reply_markup=admin_keyboard(lang))
        admin_sessions.pop(uid, None)
        return

    # Validate each row
    valid_rows:   list[dict] = []
    row_errors:   list[str]  = []
    seen_keys:    set[tuple] = set()   # dedup within this import by (resource_type, title, author)

    for i, raw_row in enumerate(rows, start=2):   # row 1 is header
        cleaned, err = _validate_csv_row(i, raw_row, {})
        if err:
            row_errors.append(err)
            continue

        # Within-import duplicate detection
        dedup_key = (cleaned["resource_type"], cleaned["title"].lower(), cleaned["author"].lower())
        if dedup_key in seen_keys:
            row_errors.append(f"row {i}: تکراری در همین CSV (عنوان+نویسنده+نوع تکراری است)")
            continue
        seen_keys.add(dedup_key)
        valid_rows.append(cleaned)

    total   = len(rows)
    n_valid = len(valid_rows)
    n_bad   = len(row_errors)

    # Preview message
    preview = tr("csv_preview", lang,
                 filename=doc.file_name or "?",
                 total=total,
                 valid=n_valid,
                 invalid=n_bad)
    bot.send_message(message.chat.id, preview)

    # Show row errors (cap at 20 to avoid Telegram message length limits)
    if row_errors:
        error_lines = [tr("csv_errors_header", lang)]
        for e in row_errors[:20]:
            error_lines.append(f"• {e}")
        if len(row_errors) > 20:
            error_lines.append(f"… و {len(row_errors) - 20} خطای دیگر" if lang == "fa"
                               else f"… and {len(row_errors) - 20} more error(s)")
        bot.send_message(message.chat.id, "\n".join(error_lines))

    if not valid_rows:
        bot.send_message(message.chat.id, tr("csv_no_valid_rows", lang),
                         reply_markup=admin_keyboard(lang))
        admin_sessions.pop(uid, None)
        return

    # Store valid rows in session and ask for confirmation
    admin_sessions[uid] = {
        "step":           "wait_csv_confirm",
        "csv_valid_rows": valid_rows,
    }
    bot.send_message(message.chat.id, tr("csv_confirm_question", lang),
                     reply_markup=csv_import_confirm_keyboard(lang))


# ── Phase 3: Pending Resources helpers ─────────────────────────────────────────

def _pending_status_label(status: str, lang: str) -> str:
    mapping = {
        "pending":       {"fa": "⏳ در انتظار فایل", "en": "⏳ Awaiting file"},
        "file_received": {"fa": "✅ آماده انتشار",   "en": "✅ Ready to publish"},
        "published":     {"fa": "📗 منتشر شده",      "en": "📗 Published"},
        "rejected":      {"fa": "🚫 رد شده",         "en": "🚫 Rejected"},
    }
    return mapping.get(status, {}).get(lang, status)


def _pending_overview_stats() -> dict:
    """Return aggregate counts needed for the overview screen."""
    all_rows   = database.list_pending_resources(limit=100_000, offset=0)
    books_cnt  = sum(1 for r in all_rows if r["resource_type"] == "book")
    arts_cnt   = sum(1 for r in all_rows if r["resource_type"] == "article")
    no_file    = sum(1 for r in all_rows if not r["file_id"])
    has_file   = sum(1 for r in all_rows if r["file_id"])
    return {
        "total":    len(all_rows),
        "books":    books_cnt,
        "articles": arts_cnt,
        "no_file":  no_file,
        "has_file": has_file,
    }


def _show_pending_overview(bot, chat_id: int, lang: str):
    stats = _pending_overview_stats()
    if stats["total"] == 0:
        bot.send_message(chat_id, tr("pending_empty", lang), reply_markup=admin_keyboard(lang))
        return
    text = tr(
        "pending_overview", lang,
        total=stats["total"],
        no_file=stats["no_file"],
        has_file=stats["has_file"],
        books=stats["books"],
        articles=stats["articles"],
    )
    bot.send_message(chat_id, text, reply_markup=pending_overview_keyboard(lang))


def _pending_list_page(bot, chat_id: int, lang: str, filter_key: str, page: int,
                       edit_message_id: int | None = None):
    """Fetch and display a paginated list of pending resources with select buttons."""
    status_filter = ""
    if filter_key == "nofile":
        # We filter manually after fetching (no direct status filter for "no file")
        rows_all = database.list_pending_resources(limit=100_000, offset=0)
        rows_all = [r for r in rows_all if not r["file_id"]]
    elif filter_key == "ready":
        rows_all = database.list_pending_resources(status="file_received", limit=100_000, offset=0)
    else:
        rows_all = database.list_pending_resources(limit=100_000, offset=0)

    total      = len(rows_all)
    total_pages = max(1, (total + _PENDING_PAGE_SIZE - 1) // _PENDING_PAGE_SIZE)
    page        = max(0, min(page, total_pages - 1))
    offset      = page * _PENDING_PAGE_SIZE
    page_rows   = rows_all[offset: offset + _PENDING_PAGE_SIZE]

    filter_label = tr(f"pending_filter_{filter_key}", lang)
    header = tr("pending_list_header", lang,
                filter=filter_label, page=page + 1, total_pages=total_pages)

    lines = [header]
    markup = types.InlineKeyboardMarkup()

    for r in page_rows:
        pid    = r["id"]
        rtype  = "📘" if r["resource_type"] == "book" else "📄"
        fmark  = "✅" if r["file_id"] else "⏳"
        title  = (r["title"] or "")[:30]
        label  = f"{fmark} P{pid} {rtype} {title}"
        lines.append(f"  {fmark} P{pid} — {r['title'][:40]} | {r['author'][:20]}")
        markup.add(types.InlineKeyboardButton(label, callback_data=f"adm_pnd:detail:{pid}"))

    # Pagination row
    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton("◀️", callback_data=f"adm_pnd:list:{filter_key}:{page - 1}"))
    if page < total_pages - 1:
        nav.append(types.InlineKeyboardButton("▶️", callback_data=f"adm_pnd:list:{filter_key}:{page + 1}"))
    if nav:
        markup.row(*nav)
    markup.row(types.InlineKeyboardButton(tr("btn_pending_back", lang), callback_data="adm_pnd:overview"))

    text = "\n".join(lines)
    if edit_message_id:
        try:
            bot.edit_message_text(text, chat_id, edit_message_id, reply_markup=markup)
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, reply_markup=markup)


def _pending_detail_text(r, lang: str) -> str:
    """Build the detail text block for a single pending resource row."""
    pid   = r["id"]
    rtype = "📘 کتاب" if lang == "fa" else "📕 Book"
    if r["resource_type"] == "article":
        rtype = "📄 مقاله" if lang == "fa" else "📄 Article"

    field_fa, field_en = database.PHYSICS_FIELDS.get(
        r["physics_field"], (r["physics_field"], r["physics_field"])
    )
    field = field_fa if lang == "fa" else field_en

    lang_label = ("فارسی" if lang == "fa" else "Persian") if r["language"] == "fa" else ("انگلیسی" if lang == "fa" else "English")

    year    = str(r["year"]) if r["year"] else ("-")
    edition = r["edition"] or ""
    edition_line = (f"🔖 {'ویرایش' if lang == 'fa' else 'Edition'}: {edition}\n") if edition else ""

    # Article metadata
    article_parts = []
    if r["resource_type"] == "article":
        if r.get("journal"):
            article_parts.append(f"📰 {'مجله' if lang == 'fa' else 'Journal'}: {r['journal']}")
        if r.get("doi"):
            article_parts.append(f"🔗 DOI: {r['doi']}")
        if r.get("volume"):
            article_parts.append(f"🔢 {'جلد' if lang == 'fa' else 'Vol'}: {r['volume']}")
        if r.get("issue"):
            article_parts.append(f"🔢 {'شماره' if lang == 'fa' else 'Issue'}: {r['issue']}")
        if r.get("pages"):
            article_parts.append(f"📄 {'صفحات' if lang == 'fa' else 'Pages'}: {r['pages']}")
    article_meta = ("\n".join(article_parts) + "\n") if article_parts else ""

    desc      = (r["description"] or "").strip()
    desc_line = (f"📝 {'توضیحات' if lang == 'fa' else 'Description'}: {desc[:200]}\n") if desc else ""

    if r["file_id"]:
        file_status = f"✅ {r['file_name'] or r['file_id']}"
    else:
        file_status = ("⏳ ندارد" if lang == "fa" else "⏳ None")

    status_label = _pending_status_label(r["status"], lang)

    return tr(
        "pending_detail", lang,
        pid=pid,
        rtype=rtype,
        title=r["title"],
        author=r["author"],
        year=year,
        edition_line=edition_line,
        field=field,
        lang=lang_label,
        article_meta=article_meta,
        desc_line=desc_line,
        file_status=file_status,
        status=status_label,
    )


def _show_pending_detail(bot, chat_id: int, lang: str, pending_id: int):
    """Fetch a pending resource and show its detail card with action buttons."""
    row = database.get_pending_resource(pending_id)
    if not row:
        bot.send_message(chat_id, tr("pending_not_found", lang, pid=pending_id))
        return
    if row["status"] in ("published", "rejected"):
        bot.send_message(
            chat_id,
            tr("pending_already_done", lang, status=_pending_status_label(row["status"], lang))
        )
        return

    text = _pending_detail_text(row, lang)
    has_file = bool(row["file_id"])
    if has_file:
        text += "\n\n" + tr("pending_has_file_warn", lang, fname=row["file_name"] or row["file_id"])

    bot.send_message(chat_id, text, reply_markup=pending_detail_keyboard(lang, pending_id, has_file))


# ── Phase 4: Publish helpers ───────────────────────────────────────────────────

def _publish_confirm_text(r, lang: str) -> str:
    """Build the publish-confirmation message text for a pending resource."""
    rtype_label = ("📘 کتاب" if lang == "fa" else "📕 Book") if r["resource_type"] == "book" \
                  else ("📄 مقاله" if lang == "fa" else "📄 Article")

    field_fa, field_en = database.PHYSICS_FIELDS.get(
        r["physics_field"], (r["physics_field"], r["physics_field"])
    )
    field = field_fa if lang == "fa" else field_en
    lang_label = ("فارسی" if lang == "fa" else "Persian") if r["language"] == "fa" \
                 else ("انگلیسی" if lang == "fa" else "English")

    year_line    = (f"📅 {'سال' if lang == 'fa' else 'Year'}: {r['year']}\n") if r["year"] else ""
    edition_line = (f"🔖 {'ویرایش' if lang == 'fa' else 'Edition'}: {r['edition']}\n") if r.get("edition") else ""

    article_parts = []
    if r["resource_type"] == "article":
        if r.get("journal"):    article_parts.append(f"📰 Journal: {r['journal']}")
        if r.get("doi"):        article_parts.append(f"🔗 DOI: {r['doi']}")
        if r.get("volume"):     article_parts.append(f"🔢 Vol: {r['volume']}")
        if r.get("issue"):      article_parts.append(f"🔢 Issue: {r['issue']}")
        if r.get("pages"):      article_parts.append(f"📄 Pages: {r['pages']}")
        if r.get("publication_date"): article_parts.append(f"📅 Date: {r['publication_date']}")
    article_meta = ("\n".join(article_parts) + "\n") if article_parts else ""

    fname = r["file_name"] or r["file_id"] or "?"

    return tr(
        "publish_confirm", lang,
        pid=r["id"],
        rtype=rtype_label,
        title=r["title"],
        author=r["author"],
        field=field,
        lang=lang_label,
        year_line=year_line,
        edition_line=edition_line,
        article_meta=article_meta,
        fname=fname,
    )


def _show_publish_confirm(bot, chat_id: int, lang: str, pending_id: int):
    """Show the publish confirmation screen for a pending resource."""
    row = database.get_pending_resource(pending_id)
    if not row:
        bot.send_message(chat_id, tr("pending_not_found", lang, pid=pending_id))
        return

    if row["status"] == "published":
        bot.send_message(chat_id, tr("publish_already", lang))
        return

    if row["status"] != "file_received":
        bot.send_message(
            chat_id,
            tr("publish_not_ready", lang, status=_pending_status_label(row["status"], lang))
        )
        return

    if not row["file_id"]:
        bot.send_message(
            chat_id,
            tr("publish_not_ready", lang, status=_pending_status_label(row["status"], lang))
        )
        return

    # Duplicate detection before showing the confirmation
    dups = database.find_library_duplicates(
        resource_type=row["resource_type"],
        title=row["title"],
        author=row["author"],
        doi=row.get("doi") or "",
    )

    if dups:
        dup = dups[0]
        bot.send_message(
            chat_id,
            tr(
                "publish_dup_warning", lang,
                dup_title=dup["title"],
                dup_author=dup["author"] or "-",
                dup_disp=_disp(dup),
            ),
            reply_markup=publish_dup_keyboard(lang, pending_id, dup["id"]),
            parse_mode="HTML",
        )
        return

    text = _publish_confirm_text(row, lang)
    bot.send_message(
        chat_id,
        text,
        reply_markup=publish_confirm_keyboard(lang, pending_id),
    )


def _do_publish(bot, chat_id: int, lang: str, pending_id: int, added_by: int, force: bool = False):
    """Execute the actual publish: insert into Library, mark pending as published."""
    # Final safety checks before publishing
    row = database.get_pending_resource(pending_id)
    if not row:
        bot.send_message(chat_id, tr("pending_not_found", lang, pid=pending_id))
        return

    if row["status"] == "published":
        bot.send_message(chat_id, tr("publish_already", lang))
        return

    if row["status"] != "file_received" or not row["file_id"]:
        bot.send_message(
            chat_id,
            tr("publish_not_ready", lang, status=_pending_status_label(row["status"], lang))
        )
        return

    # Duplicate check (skip if force=True, i.e. admin clicked "Publish Anyway")
    if not force:
        dups = database.find_library_duplicates(
            resource_type=row["resource_type"],
            title=row["title"],
            author=row["author"],
            doi=row.get("doi") or "",
        )
        if dups:
            dup = dups[0]
            bot.send_message(
                chat_id,
                tr(
                    "publish_dup_warning", lang,
                    dup_title=dup["title"],
                    dup_author=dup["author"] or "-",
                    dup_disp=_disp(dup),
                ),
                reply_markup=publish_dup_keyboard(lang, pending_id, dup["id"]),
                parse_mode="HTML",
            )
            return

    # Atomically publish
    try:
        new_id = database.publish_pending_resource(pending_id, added_by=added_by)
    except ValueError as e:
        bot.send_message(chat_id, tr("publish_error", lang, err=e), reply_markup=admin_keyboard(lang))
        return
    except Exception as e:
        bot.send_message(chat_id, tr("publish_error", lang, err=e), reply_markup=admin_keyboard(lang))
        return

    # Fetch the newly created library resource to get its display ID
    new_resource = database.get_resource(new_id)
    disp = _disp(new_resource) if new_resource else f"#{new_id}"

    bot.send_message(
        chat_id,
        tr("publish_success", lang, title=row["title"], disp=disp, pid=pending_id),
        reply_markup=admin_keyboard(lang),
        parse_mode="HTML",
    )


# more functions

def _start_add(bot, message: types.Message, lang: str):
    uid = message.from_user.id
    admin_sessions[uid] = {"step": "wait_resource_type", "data": {}}
    bot.send_message(message.chat.id, tr("ask_resource_type", lang), reply_markup=cancel_keyboard(lang))
    bot.send_message(message.chat.id, "👇", reply_markup=resource_type_keyboard(lang))


def _build_csv(books: list, articles: list) -> io.BytesIO:
    """یک فایل CSV شامل همه کتاب‌ها و مقالات می‌سازد."""
    output = io.StringIO()
    writer = csv.writer(output)

    # کتاب‌ها
    writer.writerow(["--- BOOKS ---"])
    writer.writerow(["ID", "Title", "Author", "Edition", "Year", "Field", "Language", "Downloads", "File"])
    for b in books:
        field_fa, field_en = database.PHYSICS_FIELDS.get(b["physics_field"], ("", b["physics_field"]))
        writer.writerow([
            _disp(b),
            b["title"],
            b["author"] or "",
            b["edition"] or "",
            b["year"] or "",
            field_en,
            b["language"],
            b["download_count"],
            b["file_name"] or "",
        ])

    writer.writerow([])

    # مقالات
    writer.writerow(["--- ARTICLES ---"])
    writer.writerow(["ID", "Title", "Author", "Journal", "Volume", "Issue", "Pages", "DOI", "Field", "Language", "Downloads"])
    for a in articles:
        field_fa, field_en = database.PHYSICS_FIELDS.get(a["physics_field"], ("", a["physics_field"]))
        writer.writerow([
            _disp(a),
            a["title"],
            a["author"] or "",
            a["journal"] or "",
            a["volume"] or "",
            a["issue"] or "",
            a["pages"] or "",
            a["doi"] or "",
            field_en,
            a["language"],
            a["download_count"],
        ])

    output.seek(0)
    return io.BytesIO(output.read().encode("utf-8-sig"))  # utf-8-sig برای باز شدن درست در Excel


def _show_list(bot, message: types.Message, lang: str):
    books    = database.search_resources(resource_type="book",    limit=10_000, offset=0)
    articles = database.search_resources(resource_type="article", limit=10_000, offset=0)

    if not books and not articles:
        bot.send_message(message.chat.id, tr("no_resources", lang), reply_markup=admin_keyboard(lang))
        return

    PREVIEW = 10   # تعداد آیتم‌هایی که در متن نمایش داده می‌شود

    # ── خلاصه متنی ────────────────────────────────────────────────────────
    lines = []

    if books:
        lines.append(f"📕 {'کتاب‌ها' if lang == 'fa' else 'Books'} ({len(books)}):")
        for b in books[:PREVIEW]:
            edition_part = f" [{b['edition']}]" if b["edition"] and str(b["edition"]).strip() else ""
            year_part    = f" ({b['year']})"    if b["year"] else ""
            lines.append(f"  {_disp(b)} — {b['title']}{edition_part}{year_part} | ✍ {b['author']} | ⬇️{b['download_count']}")
        if len(books) > PREVIEW:
            remaining = len(books) - PREVIEW
            lines.append(f"  … و {'و ' if lang == 'fa' else ''}{remaining} {'عنوان دیگر' if lang == 'fa' else 'more'} (در فایل CSV 👇)")

    if articles:
        if lines:
            lines.append("")
        lines.append(f"📄 {'مقالات' if lang == 'fa' else 'Articles'} ({len(articles)}):")
        for a in articles[:PREVIEW]:
            journal_part = f" | 📰 {a['journal']}" if a["journal"] else ""
            lines.append(f"  {_disp(a)} — {a['title']}{journal_part} | ✍ {a['author'] or '-'} | ⬇️{a['download_count']}")
        if len(articles) > PREVIEW:
            remaining = len(articles) - PREVIEW
            lines.append(f"  … و {remaining} {'عنوان دیگر' if lang == 'fa' else 'more'} (در فایل CSV 👇)")

    bot.send_message(message.chat.id, "\n".join(lines))

    # ── send CSV
    csv_buf = _build_csv(books, articles)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename  = f"library_{timestamp}.csv"
    caption = (
        f"📊 لیست کامل: {len(books)} کتاب، {len(articles)} مقاله\n"
        f"قابل باز شدن در Excel یا Google Sheets"
        if lang == "fa" else
        f"📊 Full list: {len(books)} books, {len(articles)} articles\n"
        f"Open with Excel or Google Sheets"
    )
    bot.send_document(
        message.chat.id,
        csv_buf,
        caption=caption,
        visible_file_name=filename,
        reply_markup=admin_keyboard(lang),
    )


def _show_stats(bot, message: types.Message, lang: str):
    s = database.get_library_stats()
    header = tr("stats_header", lang)
    if lang == "fa":
        text = (
            f"{header}\n\n"
            f"📚 کتاب‌ها: {s['total_books']}\n"
            f"📄 مقالات: {s['total_articles']}\n"
            f"فارسی: {s['fa_books']}\n"
            f"انگلیسی: {s['en_books']}\n"
            f"⬇️ کل دانلودها: {s['total_downloads']}\n"
            f"🌌 فیلدهای فعال: {s['unique_fields']}\n"
            f"👥 کل کاربران: {s['total_users']}"
        )
    else:
        text = (
            f"{header}\n\n"
            f"📚 Books: {s['total_books']}\n"
            f"📄 Articles: {s['total_articles']}\n"
            f"Persian: {s['fa_books']}\n"
            f"English: {s['en_books']}\n"
            f"⬇️ Total Downloads: {s['total_downloads']}\n"
            f"🌌 Active Fields: {s['unique_fields']}\n"
            f"👥 Total Users: {s['total_users']}"
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
