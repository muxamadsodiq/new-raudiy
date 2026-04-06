import asyncio
import logging
import sqlite3
import time
import datetime
import os
from aiogram import Bot, Dispatcher, F, html, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

logging.basicConfig(level=logging.INFO)

# ============================================================
#                        SOZLAMALAR
# ============================================================
TOKEN = os.getenv("BOT_TOKEN")
MAIN_ADMIN_ID = int(os.getenv("MAIN_ADMIN_ID", "0"))
DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "combined_bot.db")

LIMIT_PEOPLE       = 3
LIMIT_TIME         = 600
WARNING_TIMEOUT    = 180
SUB_CHECK_INTERVAL = 1

active_warnings  = {}
active_sub_polls = {}
group_stats      = {}

# ============================================================
#                    MA'LUMOTLAR BAZASI
# ============================================================
def get_db():
    return sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)

def init_db():
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, full_name TEXT)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS group_settings (
                group_username TEXT PRIMARY KEY,
                channels TEXT,
                sub_style TEXT DEFAULT 'primary',
                owner_id INTEGER DEFAULT 0,
                warn_text TEXT
            )
        """)
        c.execute("CREATE TABLE IF NOT EXISTS known_chats (chat_id INTEGER PRIMARY KEY, chat_type TEXT, title TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS pro_users (user_id INTEGER PRIMARY KEY, full_name TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS admin_groups (admin_id INTEGER, chat_id INTEGER, PRIMARY KEY (admin_id, chat_id))")
        c.execute("CREATE TABLE IF NOT EXISTS mod_groups (group_id INTEGER PRIMARY KEY, group_name TEXT, admin_id INTEGER)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS mod_word_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                words TEXT,
                reply TEXT,
                mode INTEGER DEFAULT 0
            )
        """)
        # Har bir limit — alohida qator (bir guruhda bir nechta limit bo'lishi mumkin)
        c.execute("""
            CREATE TABLE IF NOT EXISTS group_post_limits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                limit_count INTEGER DEFAULT 3,
                char_threshold INTEGER DEFAULT 60,
                warning_text TEXT,
                apply_mode INTEGER DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_daily_posts (
                user_id INTEGER,
                group_id INTEGER,
                limit_id INTEGER,
                post_date TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, group_id, limit_id, post_date)
            )
        """)

        for alter in [
            "ALTER TABLE group_settings ADD COLUMN owner_id INTEGER DEFAULT 0",
            "ALTER TABLE group_settings ADD COLUMN sub_style TEXT DEFAULT 'primary'",
            "ALTER TABLE group_settings ADD COLUMN warn_text TEXT",
            "ALTER TABLE mod_word_rules ADD COLUMN mode INTEGER DEFAULT 0",
            "ALTER TABLE group_post_limits ADD COLUMN apply_mode INTEGER DEFAULT 0",
        ]:
            try: c.execute(alter); conn.commit()
            except: pass

        conn.commit()
    except Exception as e:
        logging.error(f"DB init xatosi: {e}")
    finally:
        conn.close()

# ---- Rol tekshirish ----
def is_founder(uid): return int(uid) == int(MAIN_ADMIN_ID)

def is_admin(uid):
    if is_founder(uid): return True
    conn = get_db()
    try: return conn.cursor().execute("SELECT user_id FROM admins WHERE user_id=?", (int(uid),)).fetchone() is not None
    except: return False
    finally: conn.close()

def is_pro(uid):
    conn = get_db()
    try: return conn.cursor().execute("SELECT user_id FROM pro_users WHERE user_id=?", (int(uid),)).fetchone() is not None
    except: return False
    finally: conn.close()

def add_admin_db(uid, name):
    conn = get_db()
    try:
        conn.cursor().execute("INSERT OR REPLACE INTO admins (user_id, full_name) VALUES (?,?)", (int(uid), str(name)))
        conn.commit(); return True
    except: return False
    finally: conn.close()

def remove_admin_db(uid):
    conn = get_db()
    try: conn.cursor().execute("DELETE FROM admins WHERE user_id=?", (int(uid),)); conn.commit()
    except: pass
    finally: conn.close()

def get_all_admins():
    conn = get_db()
    try: return conn.cursor().execute("SELECT user_id, full_name FROM admins").fetchall()
    except: return []
    finally: conn.close()

def add_pro_user(uid, name):
    conn = get_db()
    try:
        conn.cursor().execute("INSERT OR REPLACE INTO pro_users (user_id, full_name) VALUES (?,?)", (int(uid), str(name)))
        conn.commit(); return True
    except: return False
    finally: conn.close()

def remove_pro_user(uid):
    conn = get_db()
    try: conn.cursor().execute("DELETE FROM pro_users WHERE user_id=?", (int(uid),)); conn.commit()
    except: pass
    finally: conn.close()

def get_all_pro_users():
    conn = get_db()
    try: return conn.cursor().execute("SELECT user_id, full_name FROM pro_users").fetchall()
    except: return []
    finally: conn.close()

def save_group_channels(group_username, channels, sub_style="primary", owner_id=0, warn_text=None):
    if not group_username.startswith("@"): group_username = "@" + group_username
    conn = get_db()
    try:
        conn.cursor().execute(
            "INSERT OR REPLACE INTO group_settings (group_username, channels, sub_style, owner_id, warn_text) VALUES (?,?,?,?,?)",
            (group_username, channels, sub_style, owner_id, warn_text)
        ); conn.commit()
    except: pass
    finally: conn.close()

def get_group_channels(group_username):
    conn = get_db()
    try:
        res = conn.cursor().execute(
            "SELECT channels, sub_style, warn_text FROM group_settings WHERE group_username=?", (group_username,)
        ).fetchone()
        return (res[0], res[1], res[2]) if res else (None, None, None)
    except: return None, None, None
    finally: conn.close()

def delete_group_channels(group_username):
    if not group_username.startswith("@"): group_username = "@" + group_username
    conn = get_db()
    try: conn.cursor().execute("DELETE FROM group_settings WHERE group_username=?", (group_username,)); conn.commit()
    except: pass
    finally: conn.close()

def add_known_chat(chat_id, chat_type, title):
    conn = get_db()
    try:
        conn.cursor().execute(
            "INSERT OR IGNORE INTO known_chats (chat_id, chat_type, title) VALUES (?,?,?)",
            (int(chat_id), chat_type, title)
        ); conn.commit()
    except: pass
    finally: conn.close()

def get_all_chats():
    conn = get_db()
    try: return conn.cursor().execute("SELECT chat_id FROM known_chats").fetchall()
    except: return []
    finally: conn.close()

def register_admin_group(admin_id, chat_id):
    conn = get_db()
    try:
        conn.cursor().execute(
            "INSERT OR IGNORE INTO admin_groups (admin_id, chat_id) VALUES (?,?)", (int(admin_id), int(chat_id))
        ); conn.commit()
    except: pass
    finally: conn.close()

def get_admin_groups(admin_id):
    conn = get_db()
    try: return [r[0] for r in conn.cursor().execute("SELECT chat_id FROM admin_groups WHERE admin_id=?", (int(admin_id),)).fetchall()]
    except: return []
    finally: conn.close()

def get_admin_groups_info(admin_id):
    conn = get_db()
    try:
        return conn.cursor().execute(
            "SELECT ag.chat_id, kc.title FROM admin_groups ag LEFT JOIN known_chats kc ON ag.chat_id=kc.chat_id WHERE ag.admin_id=?",
            (int(admin_id),)
        ).fetchall()
    except: return []
    finally: conn.close()

# ---- Moderator ----
def mod_get_group(group_id):
    conn = get_db()
    try: return conn.cursor().execute("SELECT group_name FROM mod_groups WHERE group_id=?", (int(group_id),)).fetchone()
    except: return None
    finally: conn.close()

def mod_save_group(group_id, group_name, admin_id):
    conn = get_db()
    try:
        conn.cursor().execute(
            "INSERT OR REPLACE INTO mod_groups (group_id, group_name, admin_id) VALUES (?,?,?)",
            (int(group_id), group_name, int(admin_id))
        ); conn.commit()
    except: pass
    finally: conn.close()

def mod_add_rule(group_id, words, reply, mode=0):
    """
    mode=0 → Faqat userlarga (adminlar o'tkazib yuboriladi)
    mode=1 → Faqat adminlarga (userlar o'tkazib yuboriladi)
    mode=2 → Hammaga (faqat owner o'tkazib yuboriladi)
    """
    conn = get_db()
    try:
        conn.cursor().execute(
            "INSERT INTO mod_word_rules (group_id, words, reply, mode) VALUES (?,?,?,?)",
            (int(group_id), words, reply, int(mode))
        ); conn.commit()
    except: pass
    finally: conn.close()

def mod_get_rules(group_id):
    conn = get_db()
    try: return conn.cursor().execute(
        "SELECT id, words, reply, mode FROM mod_word_rules WHERE group_id=?", (int(group_id),)
    ).fetchall()
    except: return []
    finally: conn.close()

def mod_delete_rule(rule_id):
    conn = get_db()
    try: conn.cursor().execute("DELETE FROM mod_word_rules WHERE id=?", (rule_id,)); conn.commit()
    except: pass
    finally: conn.close()

# ---- Post limit (har biri alohida) ----
def limit_add(group_id, limit_count, char_threshold, warning_text, apply_mode):
    """
    apply_mode=0 → Faqat userlarga
    apply_mode=1 → Faqat adminlarga
    apply_mode=2 → Hammaga (admin+user, faqat owner o'tmaydi)
    """
    conn = get_db()
    try:
        conn.cursor().execute(
            "INSERT INTO group_post_limits (group_id, limit_count, char_threshold, warning_text, apply_mode) VALUES (?,?,?,?,?)",
            (int(group_id), limit_count, char_threshold, warning_text, int(apply_mode))
        ); conn.commit()
    except: pass
    finally: conn.close()

def limit_get_all(group_id):
    """Guruhning barcha limitlarini qaytarish: [(id, limit_count, char_threshold, warning_text, apply_mode)]"""
    conn = get_db()
    try: return conn.cursor().execute(
        "SELECT id, limit_count, char_threshold, warning_text, apply_mode FROM group_post_limits WHERE group_id=?",
        (int(group_id),)
    ).fetchall()
    except: return []
    finally: conn.close()

def limit_delete(limit_id):
    conn = get_db()
    try: conn.cursor().execute("DELETE FROM group_post_limits WHERE id=?", (limit_id,)); conn.commit()
    except: pass
    finally: conn.close()

def check_and_inc_post(user_id, group_id, limit_id, limit_max):
    today = datetime.date.today().isoformat()
    conn = get_db()
    try:
        c = conn.cursor()
        res = c.execute(
            "SELECT count FROM user_daily_posts WHERE user_id=? AND group_id=? AND limit_id=? AND post_date=?",
            (user_id, group_id, limit_id, today)
        ).fetchone()
        current = res[0] if res else 0
        if current >= limit_max: return False, current
        c.execute(
            "INSERT OR REPLACE INTO user_daily_posts (user_id, group_id, limit_id, post_date, count) VALUES (?,?,?,?,?)",
            (user_id, group_id, limit_id, today, current + 1)
        ); conn.commit()
        return True, current + 1
    except: return True, 0
    finally: conn.close()

async def mod_get_user_groups(user_id: int, bot_instance):
    conn = get_db()
    try: all_groups = conn.cursor().execute("SELECT group_id, group_name FROM mod_groups").fetchall()
    except: all_groups = []
    finally: conn.close()
    accessible = []
    for g_id, g_name in all_groups:
        try:
            member = await bot_instance.get_chat_member(chat_id=g_id, user_id=user_id)
            if member.status in ["creator", "administrator"]:
                accessible.append((g_id, g_name))
        except (TelegramBadRequest, TelegramForbiddenError): continue
    return accessible

# ============================================================
#                        FSM STATES
# ============================================================
class SubState(StatesGroup):
    waiting_group     = State()
    waiting_channels  = State()
    waiting_warn_text = State()
    waiting_sub_color = State()
    waiting_del_group = State()

class PostState(StatesGroup):
    waiting_content       = State()
    waiting_btn_name      = State()
    waiting_btn_url       = State()
    waiting_btn_color     = State()
    waiting_btn_layout    = State()
    choose_target         = State()
    waiting_specific_chat = State()

class AdminManage(StatesGroup):
    waiting_new_admin_id = State()

class ProManage(StatesGroup):
    waiting_pro_add_id = State()

class ModState(StatesGroup):
    waiting_for_words = State()
    waiting_for_reply = State()
    waiting_word_mode = State()

class LimitState(StatesGroup):
    waiting_count  = State()
    waiting_chars  = State()
    waiting_text   = State()
    waiting_target = State()

# ============================================================
#                    INLINE KLAVIATURALAR
# ============================================================
def main_menu_inline(uid):
    kb = InlineKeyboardBuilder()
    if is_admin(uid):
        kb.button(text="📝 Post Yaratish",       callback_data="menu_post",    style="primary")
        kb.button(text="🔒 Majburiy Obuna",      callback_data="menu_sub",     style="primary")
        kb.button(text="🛡 So'z Filtri & Limit", callback_data="menu_mod",     style="primary")
    if is_founder(uid):
        kb.button(text="👤 Adminlar boshqaruvi", callback_data="menu_admins",  style="success")
        kb.button(text="⭐️ Pro boshqaruvi",      callback_data="menu_pro_mgmt",style="success")
    if not is_admin(uid):
        kb.button(text="⭐️ Pro Versiya",         callback_data="menu_pro",     style="success")
        kb.button(text="🔑 Admin so'rash",       callback_data="menu_req_admin",style="primary")
    kb.adjust(1)
    return kb.as_markup()

def back_kb(cb="menu_back"):
    """Faqat "Orqaga" tugmasi"""
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Orqaga", callback_data=cb, style="danger")
    return kb.as_markup()

def cancel_kb(cb="cancel"):
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Bekor qilish", callback_data=cb, style="danger")
    return kb.as_markup()

def back_cancel_kb(back_cb="menu_back"):
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Orqaga",      callback_data=back_cb, style="danger")
    kb.button(text="❌ Bekor qilish", callback_data="cancel", style="danger")
    kb.adjust(2)
    return kb.as_markup()

def done_or_cancel_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Tayyor",       callback_data="btn_done", style="success")
    kb.button(text="❌ Bekor qilish", callback_data="cancel",   style="danger")
    kb.adjust(2)
    return kb.as_markup()

def btn_layout_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="⬇️ Yangi qatorda", callback_data="btn_layout_row",    style="primary")
    kb.button(text="➡️ Yoniga",         callback_data="btn_layout_inline", style="primary")
    kb.button(text="❌ Bekor qilish",   callback_data="cancel",            style="danger")
    kb.adjust(2)
    return kb.as_markup()

def color_kb(prefix):
    kb = InlineKeyboardBuilder()
    kb.button(text="🟢 Yashil", callback_data=f"{prefix}_success", style="success")
    kb.button(text="🔴 Qizil",  callback_data=f"{prefix}_danger",  style="danger")
    kb.button(text="🔵 Ko'k",   callback_data=f"{prefix}_primary", style="primary")
    kb.adjust(3)
    return kb.as_markup()

def admin_manage_inline():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Admin qo'shish",    callback_data="adm_add",    style="success")
    kb.button(text="➖ Admin o'chirish",   callback_data="adm_remove", style="danger")
    kb.button(text="📋 Adminlar ro'yxati", callback_data="adm_list",   style="primary")
    kb.button(text="🔙 Orqaga",            callback_data="menu_back",  style="danger")
    kb.adjust(2)
    return kb.as_markup()

def pro_manage_inline():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Pro berish",    callback_data="pro_add",    style="success")
    kb.button(text="➖ Pro o'chirish", callback_data="pro_remove", style="danger")
    kb.button(text="📋 Pro ro'yxati",  callback_data="pro_list",   style="primary")
    kb.button(text="🔙 Orqaga",        callback_data="menu_back",  style="danger")
    kb.adjust(2)
    return kb.as_markup()

def target_inline(uid):
    kb = InlineKeyboardBuilder()
    if is_founder(uid):
        kb.button(text="🌐 Barchaga",       callback_data="target_all",      style="success")
        kb.button(text="🎯 Maxsus chat",    callback_data="target_specific", style="primary")
    for g in get_admin_groups_info(uid):
        title = g[1] or f"Chat {g[0]}"
        kb.button(text=f"📢 {title}", callback_data=f"target_group_{g[0]}", style="primary")
    kb.button(text="📤 O'zimga",       callback_data="target_self",  style="success")
    kb.button(text="❌ Bekor qilish",  callback_data="cancel",       style="danger")
    kb.adjust(1)
    return kb.as_markup()

def sub_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Obuna qo'shish",    callback_data="sub_add", style="success")
    kb.button(text="➖ Obunani o'chirish", callback_data="sub_del", style="danger")
    kb.button(text="🔙 Orqaga",            callback_data="menu_back", style="danger")
    kb.adjust(1)
    return kb.as_markup()

def mod_main_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🏢 Mening guruhlarim",   callback_data="mod_list_groups", style="primary")
    kb.button(text="🔄 Ro'yxatni yangilash", callback_data="mod_list_groups", style="success")
    kb.button(text="🔙 Bosh menyu",          callback_data="menu_back",       style="danger")
    kb.adjust(1)
    return kb.as_markup()

def word_mode_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="👥 Faqat Userlarga",  callback_data="wmode_0", style="primary")
    kb.button(text="🛡 Faqat Adminlarga", callback_data="wmode_1", style="danger")
    kb.button(text="🔥 Hammaga",          callback_data="wmode_2", style="success")
    kb.adjust(1)
    return kb.as_markup()

def limit_mode_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="👥 Faqat Userlarga",  callback_data="l_target_0", style="primary")
    kb.button(text="🛡 Faqat Adminlarga", callback_data="l_target_1", style="danger")
    kb.button(text="🔥 Hammaga",          callback_data="l_target_2", style="success")
    kb.adjust(1)
    return kb.as_markup()

# ============================================================
#                       BOT SETUP
# ============================================================
if not TOKEN:
    raise RuntimeError("BOT_TOKEN muhit o'zgaruvchisi o'rnatilmagan! .env faylini yozing yoki muhit o'zgaruvchisini o'rnating.")
if not MAIN_ADMIN_ID:
    raise RuntimeError("MAIN_ADMIN_ID muhit o'zgaruvchisi o'rnatilmagan!")

dp  = Dispatcher()
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# ============================================================
#                    YORDAMCHI FUNKSIYALAR
# ============================================================
async def auto_delete_warning(chat_id, user_id, msg, delay: int):
    await asyncio.sleep(delay)
    try: await msg.delete()
    except: pass
    finally: active_warnings.pop((chat_id, user_id), None)

async def poll_sub_until_joined(chat_id, user_id, warn_msg, channels: str, delay: int):
    start = time.time()
    while time.time() - start < delay:
        await asyncio.sleep(SUB_CHECK_INTERVAL)
        nosub = await check_sub(user_id, channels)
        if not nosub:
            try: await warn_msg.delete()
            except: pass
            active_warnings.pop((chat_id, user_id), None)
            active_sub_polls.pop((chat_id, user_id), None)
            return
    try: await warn_msg.delete()
    except: pass
    active_warnings.pop((chat_id, user_id), None)
    active_sub_polls.pop((chat_id, user_id), None)

async def send_main_menu(target, uid, text=None):
    if is_founder(uid):   role = "👑 Founder"
    elif is_admin(uid):   role = "👤 Admin"
    elif is_pro(uid):     role = "⭐️ Pro foydalanuvchi"
    else:                 role = "👥 Foydalanuvchi"
    msg = text or f"🤖 <b>Bosh menyu</b>\n\nRol: {role}\n\nAmalni tanlang:"
    if isinstance(target, Message):
        await target.answer(msg, reply_markup=main_menu_inline(uid))
    elif isinstance(target, CallbackQuery):
        try: await target.message.edit_text(msg, reply_markup=main_menu_inline(uid))
        except: await target.message.answer(msg, reply_markup=main_menu_inline(uid))

def _build_btn_markup(btns):
    if not btns: return None
    from itertools import groupby
    builder = InlineKeyboardBuilder()
    for row_id, group in groupby(sorted(btns, key=lambda b: b.get('row', 0)), key=lambda b: b.get('row', 0)):
        row_list = list(group)
        row_objs = []
        for b in row_list:
            try: row_objs.append(types.InlineKeyboardButton(text=b['text'], url=b['url'], style=b.get('style', 'primary')))
            except TypeError: row_objs.append(types.InlineKeyboardButton(text=b['text'], url=b['url']))
        builder.row(*row_objs)
    return builder.as_markup()

async def _do_send(data, targets):
    rm = _build_btn_markup(data.get('btns', []))
    success = 0
    for t in targets:
        try:
            await bot.copy_message(
                chat_id=t[0],
                from_chat_id=data['from_chat_id'],
                message_id=data['message_id'],
                reply_markup=rm
            )
            success += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logging.warning(f"Yuborish xatosi {t[0]}: {e}")
    return success

async def check_sub(user_id, channels):
    nosub = []
    for c in channels.split(","):
        c = c.strip()
        if not c: continue
        try:
            m = await bot.get_chat_member(c, user_id)
            if m.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
                nosub.append(c)
        except: continue
    return nosub

async def get_member_status(chat_id, user_id):
    """(is_owner, is_admin) qaytaradi"""
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status == "creator", m.status in ["creator", "administrator"]
    except: return False, False

# ============================================================
#                   ASOSIY HANDLERLAR
# ============================================================
@dp.chat_join_request()
async def handle_join_request(update: types.ChatJoinRequest):
    chat_id = update.chat.id; now = time.time()
    if chat_id not in group_stats:
        group_stats[chat_id] = {'start_time': now, 'count': 0}
    s = group_stats[chat_id]
    if now - s['start_time'] > LIMIT_TIME: s['start_time'] = now; s['count'] = 0
    if s['count'] < LIMIT_PEOPLE:
        try: await update.approve(); s['count'] += 1
        except: pass

@dp.message(F.new_chat_members | F.left_chat_member)
async def delete_service_messages(message: Message):
    try: await message.delete()
    except: pass

@dp.my_chat_member()
async def bot_added_to_group(update: types.ChatMemberUpdated):
    new = update.new_chat_member
    me = await bot.get_me()
    if new.user.id == me.id and new.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR]:
        chat = update.chat
        add_known_chat(chat.id, chat.type, chat.title or "")
        mod_save_group(chat.id, chat.title or str(chat.id), update.from_user.id)
        if is_admin(update.from_user.id):
            register_admin_group(update.from_user.id, chat.id)

@dp.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    add_known_chat(message.chat.id, message.chat.type, message.chat.title or message.from_user.full_name)
    if message.chat.type != 'private': return
    await message.answer("👋", reply_markup=ReplyKeyboardRemove())
    await send_main_menu(message, message.from_user.id)

@dp.callback_query(F.data == "menu_back")
@dp.callback_query(F.data == "cancel")
async def back_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await send_main_menu(call, call.from_user.id)
    await call.answer()

# ============================================================
#                      ADMIN SO'ROV
# ============================================================
@dp.callback_query(F.data == "menu_req_admin")
async def req_admin_cb(call: CallbackQuery):
    uid = call.from_user.id
    if is_admin(uid): return await call.answer("Siz allaqachon adminsiz!", show_alert=True)
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Tasdiqlash", callback_data=f"approve_{uid}", style="success")
    kb.button(text="❌ Rad etish",  callback_data=f"reject_{uid}",  style="danger")
    kb.adjust(2)
    try:
        await bot.send_message(
            MAIN_ADMIN_ID,
            f"🔔 <b>Admin so'rovi!</b>\nIsm: {html.bold(call.from_user.full_name)}\nID: <code>{uid}</code>",
            reply_markup=kb.as_markup()
        )
        await call.message.edit_text("✅ So'rov yuborildi! Founder ko'rib chiqadi.", reply_markup=cancel_kb())
    except: await call.answer("Xatolik yuz berdi!", show_alert=True)
    await call.answer()

@dp.callback_query(F.data.startswith("approve_"))
async def appr_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    uid = int(call.data.split("_")[1])
    add_admin_db(uid, "Tasdiqlangan")
    await call.message.edit_text(f"✅ {uid} foydalanuvchi admin qilindi.")
    try: await bot.send_message(uid, "🎉 Tabriklaymiz! Siz admin bo'ldingiz. /start bosing.")
    except: pass
    await call.answer()

@dp.callback_query(F.data.startswith("reject_"))
async def rej_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    uid = int(call.data.split("_")[1])
    await call.message.edit_text(f"❌ {uid} foydalanuvchining so'rovi rad etildi.")
    try: await bot.send_message(uid, "❌ Afsuski, admin so'rovingiz rad etildi.")
    except: pass
    await call.answer()

# ============================================================
#                    ADMIN BOSHQARUVI
# ============================================================
@dp.callback_query(F.data == "menu_admins")
async def admin_panel_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    await call.message.edit_text("👤 <b>Adminlar boshqaruvi</b>", reply_markup=admin_manage_inline())
    await call.answer()

@dp.callback_query(F.data == "adm_list")
async def adm_list_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    admins = get_all_admins()
    text = "👤 <b>Adminlar ro'yxati:</b>\n\n" + "\n".join([f"• {a[1]} — <code>{a[0]}</code>" for a in admins]) if admins else "Adminlar ro'yxati bo'sh."
    await call.message.edit_text(text, reply_markup=back_kb("menu_admins"))
    await call.answer()

@dp.callback_query(F.data == "adm_add")
async def adm_add_cb(call: CallbackQuery, state: FSMContext):
    if not is_founder(call.from_user.id): return
    await state.set_state(AdminManage.waiting_new_admin_id)
    await call.message.edit_text("➕ Yangi admin <b>ID</b>sini yuboring:", reply_markup=cancel_kb("menu_admins"))
    await call.answer()

@dp.message(AdminManage.waiting_new_admin_id)
async def adm_add_msg(message: Message, state: FSMContext):
    if not is_founder(message.from_user.id): return
    parts = message.text.strip().split(maxsplit=1)
    uid_str = parts[0]; name = parts[1] if len(parts) > 1 else "Admin"
    if uid_str.isdigit():
        add_admin_db(int(uid_str), name)
        await state.clear()
        kb = InlineKeyboardBuilder()
        kb.button(text="🔙 Adminlar boshqaruviga", callback_data="menu_admins", style="danger")
        await message.answer(f"✅ <code>{uid_str}</code> — <b>{name}</b> admin qilindi.", reply_markup=kb.as_markup())
        try: await bot.send_message(int(uid_str), "🎉 Siz admin bo'ldingiz! /start bosing.")
        except: pass
    else:
        await message.answer("❌ Noto'g'ri format. Faqat ID raqam kiriting.", reply_markup=cancel_kb("menu_admins"))

@dp.callback_query(F.data == "adm_remove")
async def adm_remove_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    admins = get_all_admins()
    if not admins: return await call.answer("Adminlar ro'yxati bo'sh!", show_alert=True)
    kb = InlineKeyboardBuilder()
    for uid, name in admins:
        kb.button(text=f"❌ {name} ({uid})", callback_data=f"adm_del_{uid}", style="danger")
    kb.button(text="🔙 Orqaga", callback_data="menu_admins", style="danger")
    kb.adjust(1)
    await call.message.edit_text("O'chirmoqchi bo'lgan adminni tanlang:", reply_markup=kb.as_markup())
    await call.answer()

@dp.callback_query(F.data.startswith("adm_del_"))
async def adm_del_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    uid = int(call.data.split("_")[2])
    remove_admin_db(uid)
    await call.message.edit_text(f"✅ <code>{uid}</code> adminlikdan o'chirildi.", reply_markup=back_kb("menu_admins"))
    try: await bot.send_message(uid, "ℹ️ Adminligingiz bekor qilindi.")
    except: pass
    await call.answer()

# ============================================================
#                     PRO BOSHQARUVI
# ============================================================
@dp.callback_query(F.data == "menu_pro_mgmt")
async def pro_panel_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    await call.message.edit_text("⭐️ <b>Pro boshqaruvi</b>", reply_markup=pro_manage_inline())
    await call.answer()

@dp.callback_query(F.data == "pro_list")
async def pro_list_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    pros = get_all_pro_users()
    text = "⭐️ <b>Pro Foydalanuvchilar:</b>\n\n" + "\n".join([f"• {a[1]} — <code>{a[0]}</code>" for a in pros]) if pros else "Ro'yxat bo'sh."
    await call.message.edit_text(text, reply_markup=back_kb("menu_pro_mgmt"))
    await call.answer()

@dp.callback_query(F.data == "pro_add")
async def pro_add_cb(call: CallbackQuery, state: FSMContext):
    if not is_founder(call.from_user.id): return
    await state.set_state(ProManage.waiting_pro_add_id)
    await call.message.edit_text("➕ Pro bermoqchi bo'lgan foydalanuvchi <b>ID</b>sini yuboring:", reply_markup=cancel_kb("menu_pro_mgmt"))
    await call.answer()

@dp.message(ProManage.waiting_pro_add_id)
async def pro_add_msg(message: Message, state: FSMContext):
    if not is_founder(message.from_user.id): return
    parts = message.text.strip().split(maxsplit=1)
    uid_str = parts[0]; name = parts[1] if len(parts) > 1 else "Pro User"
    if uid_str.isdigit():
        add_pro_user(int(uid_str), name)
        await state.clear()
        kb = InlineKeyboardBuilder()
        kb.button(text="🔙 Pro boshqaruviga", callback_data="menu_pro_mgmt", style="danger")
        await message.answer(f"✅ <code>{uid_str}</code> — <b>{name}</b> Pro oldi.", reply_markup=kb.as_markup())
        try: await bot.send_message(int(uid_str), "🌟 Tabriklaymiz! Sizga Pro maqomi berildi!")
        except: pass
    else:
        await message.answer("❌ Noto'g'ri format.", reply_markup=cancel_kb("menu_pro_mgmt"))

@dp.callback_query(F.data == "pro_remove")
async def pro_remove_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    pros = get_all_pro_users()
    if not pros: return await call.answer("Pro foydalanuvchilar bo'sh!", show_alert=True)
    kb = InlineKeyboardBuilder()
    for uid, name in pros:
        kb.button(text=f"❌ {name} ({uid})", callback_data=f"pro_del_{uid}", style="danger")
    kb.button(text="🔙 Orqaga", callback_data="menu_pro_mgmt", style="danger")
    kb.adjust(1)
    await call.message.edit_text("O'chirmoqchi bo'lgan Pro userni tanlang:", reply_markup=kb.as_markup())
    await call.answer()

@dp.callback_query(F.data.startswith("pro_del_"))
async def pro_del_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    uid = int(call.data.split("_")[2])
    remove_pro_user(uid)
    await call.message.edit_text(f"✅ <code>{uid}</code> Pro maqomidan o'chirildi.", reply_markup=back_kb("menu_pro_mgmt"))
    try: await bot.send_message(uid, "ℹ️ Pro maqomingiz bekor qilindi.")
    except: pass
    await call.answer()

@dp.callback_query(F.data == "menu_pro")
async def pro_info_cb(call: CallbackQuery):
    uid = call.from_user.id
    if is_pro(uid): await call.answer("Siz allaqachon Pro foydalanuvchisiz! ⭐️", show_alert=True)
    else: await call.answer("Pro versiya haqida ma'lumot uchun adminga murojaat qiling.", show_alert=True)

# ============================================================
#                      POST YARATISH
# ============================================================
@dp.callback_query(F.data == "menu_post")
async def post_start_cb(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    await state.set_state(PostState.waiting_content)
    await state.update_data(user_id=call.from_user.id, btns=[], next_row=0)
    await call.message.edit_text(
        "📝 <b>Post Yaratish</b>\n\nRasm, Video, GIF, Ovozli xabar yoki Matn yuboring:",
        reply_markup=cancel_kb()
    )
    await call.answer()

@dp.message(PostState.waiting_content)
async def post_content(message: Message, state: FSMContext):
    await state.update_data(from_chat_id=message.chat.id, message_id=message.message_id)
    await state.set_state(PostState.waiting_btn_name)
    await message.answer("✅ Qabul qilindi!\n\nTugma nomi yuboring yoki ✅ Tayyor bosing:", reply_markup=done_or_cancel_kb())

@dp.callback_query(PostState.waiting_btn_name, F.data == "btn_done")
async def post_done_cb(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    rm = _build_btn_markup(data.get('btns', []))
    await call.message.answer("👀 <b>Post ko'rinishi:</b>")
    try:
        await bot.copy_message(
            chat_id=call.message.chat.id,
            from_chat_id=data['from_chat_id'],
            message_id=data['message_id'],
            reply_markup=rm
        )
    except Exception as e:
        await call.message.answer(f"❌ Xatolik: {e}"); return
    await state.set_state(PostState.choose_target)
    await call.message.answer("📤 Qayerga yuboramiz?", reply_markup=target_inline(data['user_id']))
    await call.answer()

@dp.message(PostState.waiting_btn_name)
async def btn_name_msg(message: Message, state: FSMContext):
    await state.update_data(t_n=message.text)
    await state.set_state(PostState.waiting_btn_url)
    await message.answer(f"🔗 «{message.text}» tugmasi uchun link yuboring:", reply_markup=cancel_kb())

@dp.message(PostState.waiting_btn_url)
async def btn_url_msg(message: Message, state: FSMContext):
    if not message.text.startswith("http"):
        await message.answer("❌ Link http:// yoki https:// bilan boshlanishi kerak."); return
    await state.update_data(t_u=message.text.strip())
    await state.set_state(PostState.waiting_btn_color)
    await message.answer("🎨 Tugma rangini tanlang:", reply_markup=color_kb("style"))

@dp.callback_query(PostState.waiting_btn_color, F.data.startswith("style_"))
async def style_cb(call: CallbackQuery, state: FSMContext):
    color = call.data.split("_")[1]
    await state.update_data(t_c=color)
    data = await state.get_data()
    btns = data.get('btns', [])
    if not btns:
        # Birinchi tugma — joylashuv so'ralmaydi, to'g'ridan qo'shiladi
        btns.append({'text': data['t_n'], 'url': data['t_u'], 'style': color, 'row': 0})
        await state.update_data(btns=btns, next_row=1)
        await state.set_state(PostState.waiting_btn_name)
        await call.message.edit_text(
            f"✅ 1-tugma qo'shildi.\n\nYana tugma qo'shing yoki ✅ Tayyor bosing:",
            reply_markup=done_or_cancel_kb()
        )
    else:
        # Keyingi tugmalar — joylashuvni tanlash
        await state.set_state(PostState.waiting_btn_layout)
        last = btns[-1]
        await call.message.edit_text(
            f"📐 <b>«{data['t_n']}»</b> tugmasini qayerga qo'yamiz?\n\n"
            f"⬇️ <b>Yangi qatorda</b> — oldingi tugmaning pastiga\n"
            f"➡️ <b>Yoniga</b> — «{last['text']}» bilan bir qatorda",
            reply_markup=btn_layout_kb()
        )
    await call.answer()

@dp.callback_query(PostState.waiting_btn_layout, F.data.startswith("btn_layout_"))
async def btn_layout_cb(call: CallbackQuery, state: FSMContext):
    layout = call.data.split("_")[2]
    data = await state.get_data()
    btns = data.get('btns', [])
    next_row = data.get('next_row', 1)
    if layout == 'row':
        new_row = next_row
        next_row += 1
    else:
        new_row = btns[-1]['row'] if btns else 0
    btns.append({'text': data['t_n'], 'url': data['t_u'], 'style': data.get('t_c', 'primary'), 'row': new_row})
    await state.update_data(btns=btns, next_row=next_row)
    await state.set_state(PostState.waiting_btn_name)
    pos_text = "⬇️ yangi qatorda" if layout == 'row' else f"➡️ «{btns[-2]['text']}» yonida"
    await call.message.edit_text(
        f"✅ {len(btns)}-tugma qo'shildi ({pos_text}).\n\nYana tugma qo'shing yoki ✅ Tayyor bosing:",
        reply_markup=done_or_cancel_kb()
    )
    await call.answer()

@dp.callback_query(PostState.choose_target, F.data == "target_all")
async def target_all_cb(call: CallbackQuery, state: FSMContext):
    if not is_founder(call.from_user.id): return
    data = await state.get_data(); await state.clear()
    await call.message.edit_text("⏳ Yuborilmoqda...")
    success = await _do_send(data, [(r[0],) for r in get_all_chats()])
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Bosh menyu", callback_data="menu_back", style="danger")
    await call.message.edit_text(f"✅ Barcha chatlarga yuborildi!\nMuvaffaqiyatli: {success} ta", reply_markup=kb.as_markup())
    await call.answer()

@dp.callback_query(PostState.choose_target, F.data == "target_specific")
async def target_specific_cb(call: CallbackQuery, state: FSMContext):
    if not is_founder(call.from_user.id): return
    await state.set_state(PostState.waiting_specific_chat)
    await call.message.edit_text("🎯 Maxsus chat ID yuboring:", reply_markup=cancel_kb())
    await call.answer()

@dp.message(PostState.waiting_specific_chat)
async def specific_chat_msg(message: Message, state: FSMContext):
    data = await state.get_data(); await state.clear()
    try:
        success = await _do_send(data, [(int(message.text.strip()),)])
        kb = InlineKeyboardBuilder()
        kb.button(text="🔙 Bosh menyu", callback_data="menu_back", style="danger")
        await message.answer(f"✅ Yuborildi! Muvaffaqiyat: {success}", reply_markup=kb.as_markup())
    except:
        await message.answer("❌ Noto'g'ri chat ID.")

@dp.callback_query(PostState.choose_target, F.data == "target_self")
async def target_self_cb(call: CallbackQuery, state: FSMContext):
    data = await state.get_data(); await state.clear()
    success = await _do_send(data, [(call.from_user.id,)])
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Bosh menyu", callback_data="menu_back", style="danger")
    await call.message.edit_text("✅ O'zingizga yuborildi!" if success else "❌ Yuborishda xatolik.", reply_markup=kb.as_markup())
    await call.answer()

@dp.callback_query(PostState.choose_target, F.data.startswith("target_group_"))
async def target_group_cb(call: CallbackQuery, state: FSMContext):
    g_id = int(call.data.split("_")[2])
    data = await state.get_data()
    if g_id not in get_admin_groups(data['user_id']) and not is_founder(call.from_user.id):
        return await call.answer("Bu guruh sizning guruhingiz emas!", show_alert=True)
    await state.clear()
    success = await _do_send(data, [(g_id,)])
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Bosh menyu", callback_data="menu_back", style="danger")
    await call.message.edit_text("✅ Guruhga yuborildi!" if success else "❌ Yuborishda xatolik.", reply_markup=kb.as_markup())
    await call.answer()

# ============================================================
#                    MAJBURIY OBUNA
# ============================================================
@dp.callback_query(F.data == "menu_sub")
async def sub_menu_cb(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("🔒 <b>Majburiy Obuna Boshqaruvi</b>", reply_markup=sub_menu_kb())
    await call.answer()

@dp.callback_query(F.data == "sub_add")
async def sub_add_cb(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    await state.set_state(SubState.waiting_group)
    await call.message.edit_text(
        "📝 Guruh @username yuboring:\n\n<i>Misol: @mygroupname</i>",
        reply_markup=cancel_kb("menu_sub")
    )
    await call.answer()

@dp.callback_query(F.data == "sub_del")
async def sub_del_cb(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    await state.set_state(SubState.waiting_del_group)
    await call.message.edit_text(
        "🗑 O'chirmoqchi bo'lgan guruh @username yuboring:",
        reply_markup=cancel_kb("menu_sub")
    )
    await call.answer()

@dp.message(SubState.waiting_del_group)
async def sub_del_msg(message: Message, state: FSMContext):
    gr = message.text.strip()
    if not gr.startswith("@"): gr = "@" + gr
    delete_group_channels(gr)
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Obuna menyusiga", callback_data="menu_sub", style="danger")
    await message.answer(f"✅ <b>{gr}</b> guruhining majburiy obunasi o'chirildi.", reply_markup=kb.as_markup())

@dp.message(SubState.waiting_group)
async def sub_gr_msg(message: Message, state: FSMContext):
    await state.update_data(gr=message.text.strip())
    await state.set_state(SubState.waiting_channels)
    await message.answer(
        "📢 Kanallarni yuboring:\n\n<i>Misol: @kanal1, @kanal2</i>",
        reply_markup=cancel_kb("menu_sub")
    )

@dp.message(SubState.waiting_channels)
async def sub_ch_msg(message: Message, state: FSMContext):
    await state.update_data(chans=message.text.strip())
    await state.set_state(SubState.waiting_warn_text)
    await message.answer(
        "💬 Obuna bo'lmagan userlarga yuboriladigan <b>ogohlantirish matnini</b> yuboring:\n\n"
        "<i>Misol: Guruhda yozish uchun avval quyidagi kanallarga obuna bo'ling!</i>",
        reply_markup=cancel_kb("menu_sub")
    )

@dp.message(SubState.waiting_warn_text)
async def sub_warn_msg(message: Message, state: FSMContext):
    await state.update_data(warn_text=message.text.strip())
    await state.set_state(SubState.waiting_sub_color)
    await message.answer("🎨 Obuna tugmasi rangini tanlang:", reply_markup=color_kb("subcolor"))

@dp.callback_query(F.data.startswith("subcolor_"))
async def subcolor_cb(call: CallbackQuery, state: FSMContext):
    color = call.data.split("_")[1]
    data = await state.get_data()
    save_group_channels(data['gr'], data['chans'], sub_style=color, owner_id=call.from_user.id, warn_text=data.get('warn_text'))
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Obuna menyusiga", callback_data="menu_sub",  style="danger")
    kb.button(text="🏠 Bosh menyu",       callback_data="menu_back", style="danger")
    kb.adjust(2)
    await call.message.edit_text(
        f"✅ <b>{data['gr']}</b> guruhi uchun majburiy obuna saqlandi!\n\n"
        f"Kanallar: {data['chans']}\n"
        f"Tugma rangi: {color}\n"
        f"Ogohlantirish: {data.get('warn_text', 'standart')}",
        reply_markup=kb.as_markup()
    )
    await call.answer()

# ============================================================
#                   MODERATOR & LIMIT
# ============================================================
@dp.callback_query(F.data == "menu_mod")
async def mod_menu_cb(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("🛡 <b>So'z Filtri & Kunlik Limit</b>", reply_markup=mod_main_kb())
    await call.answer()

@dp.callback_query(F.data == "mod_list_groups")
async def mod_list_groups_cb(call: CallbackQuery):
    groups = await mod_get_user_groups(call.from_user.id, bot)
    if not groups:
        return await call.message.edit_text(
            "❌ Guruhlar topilmadi.\n\nBotni guruhga qo'shing va admin qiling.",
            reply_markup=cancel_kb("menu_mod")
        )
    kb = InlineKeyboardBuilder()
    for g_id, g_name in groups:
        kb.button(text=f"👥 {g_name}", callback_data=f"mod_manage_{g_id}", style="primary")
    kb.button(text="🔙 Orqaga", callback_data="menu_mod", style="danger")
    kb.adjust(1)
    await call.message.edit_text("Guruhni tanlang:", reply_markup=kb.as_markup())
    await call.answer()

@dp.callback_query(F.data.startswith("mod_manage_"))
async def mod_manage_cb(call: CallbackQuery):
    g_id = call.data.split("_")[2]
    rules = mod_get_rules(g_id)
    limits = limit_get_all(int(g_id))
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Yangi so'z filtri qo'shish",         callback_data=f"mod_add_{g_id}",   style="success")
    kb.button(text=f"📋 Filtrlarni ko'rish ({len(rules)} ta)", callback_data=f"mod_view_{g_id}", style="primary")
    kb.button(text="➕ Yangi limit qo'shish",                 callback_data=f"lim_add_{g_id}",  style="success")
    kb.button(text=f"📊 Limitlarni ko'rish ({len(limits)} ta)", callback_data=f"lim_view_{g_id}", style="primary")
    kb.button(text="🔙 Orqaga", callback_data="mod_list_groups", style="danger")
    kb.adjust(1)
    await call.message.edit_text("⚙️ <b>Guruh sozlamalari:</b>", reply_markup=kb.as_markup())
    await call.answer()

# ---- SO'Z FILTRI ----
@dp.callback_query(F.data.startswith("mod_add_"))
async def mod_add_cb(call: CallbackQuery, state: FSMContext):
    g_id = call.data.split("_")[2]
    await state.update_data(active_gid=g_id)
    await state.set_state(ModState.waiting_for_words)
    await call.message.edit_text(
        "📝 Taqiqlangan so'zlarni vergul bilan yuboring:\n\n<i>Misol: reklama, spam, link</i>",
        reply_markup=cancel_kb(f"mod_manage_{g_id}")
    )
    await call.answer()

@dp.message(ModState.waiting_for_words)
async def mod_words_msg(message: Message, state: FSMContext):
    await state.update_data(w_words=message.text.strip())
    await state.set_state(ModState.waiting_for_reply)
    await message.answer(
        "💬 Bu so'zlarni ishlatganda userga yuboriladigan ogohlantirish matnini yuboring:",
        reply_markup=cancel_kb()
    )

@dp.message(ModState.waiting_for_reply)
async def mod_reply_msg(message: Message, state: FSMContext):
    await state.update_data(w_reply=message.text.strip())
    await state.set_state(ModState.waiting_word_mode)
    await message.answer(
        "🎯 Bu filtr kimga amal qilsin?\n\n"
        "👥 <b>Faqat Userlarga</b> — Adminlar bu so'zlarni ishlata oladi\n"
        "🛡 <b>Faqat Adminlarga</b> — Faqat adminlarga taqiqlangan\n"
        "🔥 <b>Hammaga</b> — Ownerdan tashqari barchaga taqiqlangan",
        reply_markup=word_mode_kb()
    )

@dp.callback_query(ModState.waiting_word_mode, F.data.startswith("wmode_"))
async def mod_mode_cb(call: CallbackQuery, state: FSMContext):
    mode = int(call.data.split("_")[1])
    data = await state.get_data()
    mod_add_rule(data['active_gid'], data['w_words'], data['w_reply'], mode)
    mode_text = {0: "👥 Faqat Userlarga", 1: "🛡 Faqat Adminlarga", 2: "🔥 Hammaga"}[mode]
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Guruhga qaytish", callback_data=f"mod_manage_{data['active_gid']}", style="danger")
    kb.button(text="🏠 Bosh menyu",       callback_data="menu_back",                        style="danger")
    kb.adjust(2)
    await call.message.edit_text(
        f"✅ So'z filtri qo'shildi!\n\n"
        f"So'zlar: <code>{data['w_words']}</code>\n"
        f"Javob: {data['w_reply']}\n"
        f"Rejim: {mode_text}",
        reply_markup=kb.as_markup()
    )
    await call.answer()

@dp.callback_query(F.data.startswith("mod_view_"))
async def mod_view_cb(call: CallbackQuery):
    g_id = call.data.split("_")[2]
    rules = mod_get_rules(g_id)
    if not rules:
        return await call.message.edit_text(
            "❌ Hech qanday filtr yo'q.",
            reply_markup=back_kb(f"mod_manage_{g_id}")
        )
    kb = InlineKeyboardBuilder()
    mode_icons = {0: "👥", 1: "🛡", 2: "🔥"}
    for r_id, words, reply, mode in rules:
        icon = mode_icons.get(mode, "👥")
        short = words[:22] + "..." if len(words) > 22 else words
        kb.button(text=f"{icon} {short}", callback_data=f"mod_rule_{r_id}_{g_id}", style="primary")
    kb.button(text="🔙 Orqaga", callback_data=f"mod_manage_{g_id}", style="danger")
    kb.adjust(1)
    await call.message.edit_text(f"🗂 Filtrlar ({len(rules)} ta):", reply_markup=kb.as_markup())
    await call.answer()

@dp.callback_query(F.data.startswith("mod_rule_"))
async def mod_rule_detail_cb(call: CallbackQuery):
    parts = call.data.split("_")
    r_id = parts[2]; g_id = parts[3]
    rules = mod_get_rules(g_id)
    rule = next((r for r in rules if str(r[0]) == r_id), None)
    if not rule: return await call.answer("Filtr topilmadi!", show_alert=True)
    mode_text = {0: "👥 Faqat Userlarga", 1: "🛡 Faqat Adminlarga", 2: "🔥 Hammaga"}.get(rule[3], "Noma'lum")
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Filterni o'chirish", callback_data=f"mod_del_rule_{r_id}_{g_id}", style="danger")
    kb.button(text="🔙 Orqaga",             callback_data=f"mod_view_{g_id}",            style="danger")
    kb.adjust(2)
    await call.message.edit_text(
        f"📋 <b>Filtr ma'lumotlari:</b>\n\n"
        f"So'zlar: <code>{rule[1]}</code>\n"
        f"Javob: {rule[2]}\n"
        f"Rejim: {mode_text}",
        reply_markup=kb.as_markup()
    )
    await call.answer()

@dp.callback_query(F.data.startswith("mod_del_rule_"))
async def mod_del_rule_cb(call: CallbackQuery):
    parts = call.data.split("_")
    r_id = int(parts[3]); g_id = parts[4]
    mod_delete_rule(r_id)
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Filtrlarga qaytish", callback_data=f"mod_view_{g_id}",    style="danger")
    kb.button(text="🏠 Bosh menyu",          callback_data="menu_back",           style="danger")
    kb.adjust(2)
    await call.message.edit_text("✅ Filtr o'chirildi.", reply_markup=kb.as_markup())
    await call.answer()

# ---- KUNLIK POST LIMITI (har biri alohida) ----
@dp.callback_query(F.data.startswith("lim_add_"))
async def lim_add_cb(call: CallbackQuery, state: FSMContext):
    g_id = call.data.split("_")[2]
    await state.update_data(active_gid=g_id)
    await state.set_state(LimitState.waiting_count)
    await call.message.edit_text(
        "🔢 <b>Yangi Limit — 1/4</b>\n\nBir kunda necha marta post tashlasa bo'ladi?\n<i>Misol: 3</i>",
        reply_markup=cancel_kb(f"mod_manage_{g_id}")
    )
    await call.answer()

@dp.message(LimitState.waiting_count)
async def limit_count_set(message: Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("❌ Faqat raqam kiriting!", reply_markup=cancel_kb()); return
    await state.update_data(l_count=int(message.text.strip()))
    await state.set_state(LimitState.waiting_chars)
    await message.answer(
        "📏 <b>Yangi Limit — 2/4</b>\n\nXabar necha belgidan uzun bo'lsa 'post' hisoblansin?\n"
        "<i>Misol: 60 — qisqa salomlashuv xabarlarini limitga kiritmaslik uchun</i>",
        reply_markup=cancel_kb()
    )

@dp.message(LimitState.waiting_chars)
async def limit_chars_set(message: Message, state: FSMContext):
    if not message.text.strip().isdigit():
        await message.answer("❌ Faqat raqam kiriting!", reply_markup=cancel_kb()); return
    await state.update_data(l_chars=int(message.text.strip()))
    await state.set_state(LimitState.waiting_text)
    await message.answer(
        "📝 <b>Yangi Limit — 3/4</b>\n\nLimit to'lganda foydalanuvchiga yuboriladigan ogohlantirish matnini yuboring:",
        reply_markup=cancel_kb()
    )

@dp.message(LimitState.waiting_text)
async def limit_text_set(message: Message, state: FSMContext):
    await state.update_data(l_text=message.text.strip())
    await state.set_state(LimitState.waiting_target)
    await message.answer(
        "🎯 <b>Yangi Limit — 4/4</b>\n\nLimit kimga amal qilsin?\n\n"
        "👥 <b>Faqat Userlarga</b> — Adminlar xohlagancha post tashlaydi\n"
        "🛡 <b>Faqat Adminlarga</b> — Userlarga cheksiz, adminlarga limit\n"
        "🔥 <b>Hammaga</b> — Admin+user barchaga limit (faqat Owner o'tmaydi)",
        reply_markup=limit_mode_kb()
    )

@dp.callback_query(LimitState.waiting_target)
async def limit_final_cb(call: CallbackQuery, state: FSMContext):
    mode = int(call.data.split("_")[2])
    data = await state.get_data()
    limit_add(data['active_gid'], data['l_count'], data['l_chars'], data['l_text'], mode)
    mode_text = {0: "👥 Faqat Userlarga", 1: "🛡 Faqat Adminlarga", 2: "🔥 Hammaga"}[mode]
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Yana limit qo'shish", callback_data=f"lim_add_{data['active_gid']}",  style="success")
    kb.button(text="📊 Limitlarni ko'rish",   callback_data=f"lim_view_{data['active_gid']}", style="primary")
    kb.button(text="🔙 Guruhga qaytish",      callback_data=f"mod_manage_{data['active_gid']}", style="danger")
    kb.adjust(1)
    await call.message.edit_text(
        f"✅ <b>Yangi limit saqlandi!</b>\n\n"
        f"📅 Kunlik: <b>{data['l_count']} ta</b>\n"
        f"✍️ Min. belgi: <b>{data['l_chars']} ta</b>\n"
        f"🎯 Rejim: {mode_text}\n"
        f"⚠️ Ogohlantirish: {data['l_text']}",
        reply_markup=kb.as_markup()
    )
    await call.answer()

@dp.callback_query(F.data.startswith("lim_view_"))
async def lim_view_cb(call: CallbackQuery):
    g_id = call.data.split("_")[2]
    limits = limit_get_all(int(g_id))
    if not limits:
        return await call.message.edit_text(
            "❌ Hech qanday limit yo'q.",
            reply_markup=back_kb(f"mod_manage_{g_id}")
        )
    mode_icons = {0: "👥", 1: "🛡", 2: "🔥"}
    kb = InlineKeyboardBuilder()
    for lim_id, l_count, l_chars, l_warn, l_mode in limits:
        icon = mode_icons.get(l_mode, "👥")
        kb.button(
            text=f"{icon} {l_count} ta/kun | {l_chars}+ belgi",
            callback_data=f"lim_detail_{lim_id}_{g_id}",
            style="primary"
        )
    kb.button(text="🔙 Orqaga", callback_data=f"mod_manage_{g_id}", style="danger")
    kb.adjust(1)
    await call.message.edit_text(f"📊 <b>Limitlar ({len(limits)} ta):</b>", reply_markup=kb.as_markup())
    await call.answer()

@dp.callback_query(F.data.startswith("lim_detail_"))
async def lim_detail_cb(call: CallbackQuery):
    parts = call.data.split("_")
    lim_id = parts[2]; g_id = parts[3]
    limits = limit_get_all(int(g_id))
    lim = next((l for l in limits if str(l[0]) == lim_id), None)
    if not lim: return await call.answer("Limit topilmadi!", show_alert=True)
    mode_text = {0: "👥 Faqat Userlarga", 1: "🛡 Faqat Adminlarga", 2: "🔥 Hammaga"}.get(lim[4], "Noma'lum")
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Limitni o'chirish", callback_data=f"lim_del_{lim_id}_{g_id}", style="danger")
    kb.button(text="🔙 Orqaga",            callback_data=f"lim_view_{g_id}",         style="danger")
    kb.adjust(2)
    await call.message.edit_text(
        f"📊 <b>Limit ma'lumotlari:</b>\n\n"
        f"📅 Kunlik limit: <b>{lim[1]} ta</b>\n"
        f"✍️ Min. belgi: <b>{lim[2]} ta</b>\n"
        f"🎯 Rejim: {mode_text}\n"
        f"⚠️ Ogohlantirish: {lim[3]}",
        reply_markup=kb.as_markup()
    )
    await call.answer()

@dp.callback_query(F.data.startswith("lim_del_"))
async def lim_del_cb(call: CallbackQuery):
    parts = call.data.split("_")
    lim_id = int(parts[2]); g_id = parts[3]
    limit_delete(lim_id)
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Limitlarga qaytish", callback_data=f"lim_view_{g_id}",    style="danger")
    kb.button(text="🏠 Bosh menyu",          callback_data="menu_back",           style="danger")
    kb.adjust(2)
    await call.message.edit_text("✅ Limit o'chirildi.", reply_markup=kb.as_markup())
    await call.answer()

# ============================================================
#              GURUH NAZORATI — ASOSIY WATCHER
# ============================================================
@dp.message()
async def watcher(message: Message):
    if message.chat.type == 'private': return
    add_known_chat(message.chat.id, message.chat.type, message.chat.title or "")
    if is_admin(message.from_user.id):
        register_admin_group(message.from_user.id, message.chat.id)

    chat_id  = message.chat.id
    user_id  = message.from_user.id
    user_key = (chat_id, user_id)

    # Owner hech qanday cheklovga tushmaydi
    if is_founder(user_id): return

    # ---- 1. OBUNA TEKSHIRUVI ----
    if message.chat.username:
        chans, style, warn_text = get_group_channels(f"@{message.chat.username}")
        if chans:
            try:
                m = await bot.get_chat_member(chat_id, user_id)
                if m.status not in [ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR]:
                    nosub = await check_sub(user_id, chans)
                    if nosub:
                        try: await message.delete()
                        except: pass
                        if user_key not in active_warnings:
                            kb = InlineKeyboardBuilder()
                            for c in nosub:
                                c_clean = c.strip().replace('@', '')
                                try: kb.button(text=f"📢 {c.strip()}", url=f"https://t.me/{c_clean}", style=style or "primary")
                                except: kb.button(text=f"📢 {c.strip()}", url=f"https://t.me/{c_clean}")
                            kb.adjust(1)
                            text = warn_text or "Guruhda yozish uchun avval quyidagi kanallarga obuna bo'ling!"
                            w = await message.answer(
                                f"⚠️ {message.from_user.mention_html()}, {text}",
                                reply_markup=kb.as_markup()
                            )
                            active_warnings[user_key] = True
                            active_sub_polls[user_key] = asyncio.create_task(
                                poll_sub_until_joined(chat_id, user_id, w, chans, WARNING_TIMEOUT)
                            )
                        return
            except: pass

    # Admin/owner holatini bir marta aniqlaymiz
    is_owner, is_c_admin = await get_member_status(chat_id, user_id)

    # ---- 2. SO'Z FILTRI ----
    if message.text:
        rules = mod_get_rules(chat_id)
        if rules:
            msg_lower = message.text.lower()
            for r_id, words, reply, mode in rules:
                word_list = [w.strip() for w in words.split(",") if w.strip()]
                if not any(w in msg_lower for w in word_list): continue
                # mode=0: faqat userlarga — admin o'tkazib yuboriladi
                if mode == 0 and is_c_admin: continue
                # mode=1: faqat adminlarga — user o'tkazib yuboriladi
                if mode == 1 and not is_c_admin: continue
                # mode=2: hammaga — faqat owner o'tkazib yuboriladi
                if mode == 2 and is_owner: continue
                try:
                    await message.delete()
                    if user_key not in active_warnings:
                        w = await message.answer(f"⚠️ {message.from_user.mention_html()}, {reply}")
                        active_warnings[user_key] = asyncio.create_task(
                            auto_delete_warning(chat_id, user_id, w, WARNING_TIMEOUT)
                        )
                except: pass
                return

    # ---- 3. KUNLIK POST LIMITI ----
    if message.text:
        limits = limit_get_all(chat_id)
        if limits:
            for lim_id, l_max, l_chars, l_warn, l_mode in limits:
                if len(message.text) < l_chars: continue
                # mode=0: faqat userlarga — admin o'tkazib yuboriladi
                if l_mode == 0 and is_c_admin: continue
                # mode=1: faqat adminlarga — user o'tkazib yuboriladi
                if l_mode == 1 and not is_c_admin: continue
                # mode=2: hammaga — faqat owner o'tkazib yuboriladi
                if l_mode == 2 and is_owner: continue

                allowed, count = check_and_inc_post(user_id, chat_id, lim_id, l_max)
                if not allowed:
                    try:
                        await message.delete()
                        if user_key not in active_warnings:
                            w = await message.answer(
                                f"⚠️ {message.from_user.mention_html()}, {l_warn}\n\n"
                                f"<i>Bugungi limitingiz: {l_max} ta post. Ertaga yangilanadi.</i>"
                            )
                            active_warnings[user_key] = asyncio.create_task(
                                auto_delete_warning(chat_id, user_id, w, WARNING_TIMEOUT)
                            )
                    except: pass
                    return  # Bir limit ishladi — keyingisini tekshirmaslik

# ============================================================
#                          MAIN
# ============================================================
async def main():
    init_db()
    print("✅ BOT ISHGA TUSHDI!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot to'xtatildi.")