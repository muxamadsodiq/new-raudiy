import asyncio
import logging
import sqlite3
import time
import datetime
import os
from aiogram import Bot, Dispatcher, F, html, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

logging.basicConfig(level=logging.INFO)

# ============================================================
#                        SOZLAMALAR
# ============================================================
TOKEN = "8371029652:AAGlBnB0N_DmWaK1y-ZSlIUP9k_XxXbuFnA"
MAIN_ADMIN_ID = 5724592490
DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "combined_bot.db")

LIMIT_PEOPLE = 3
LIMIT_TIME = 600
WARNING_TIMEOUT = 180  # 3 daqiqa
SUB_CHECK_INTERVAL = 1 # 4 soniyada bir obuna qayta tekshiriladi

# Ogohlantirishlar nazorati uchun global lug'atlar
# {(chat_id, user_id): asyncio.Task} -> ogohlantirish tasklari
active_warnings = {}
# {(chat_id, user_id): asyncio.Task} -> obuna polling tasklari
active_sub_polls = {}
group_stats = {}

# ============================================================
#                       MA'LUMOTLAR BAZASI
# ============================================================
def get_db():
    return sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)

def init_db():
    conn = get_db()
    try:
        c = conn.cursor()

        # --- JADVALLAR ---
        c.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, full_name TEXT)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS group_settings (
                group_username TEXT PRIMARY KEY,
                channels TEXT,
                sub_style TEXT DEFAULT 'primary',
                owner_id INTEGER DEFAULT 0
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
        # KUNLIK POST LIMITI JADVALI
        c.execute("""
            CREATE TABLE IF NOT EXISTS group_post_limits (
                group_id INTEGER PRIMARY KEY,
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
                post_date TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, group_id, post_date)
            )
        """)

        # Ustun qo'shish (eski DB bilan moslik)
        for alter in [
            "ALTER TABLE group_settings ADD COLUMN owner_id INTEGER DEFAULT 0",
            "ALTER TABLE group_settings ADD COLUMN sub_style TEXT DEFAULT 'primary'",
            "ALTER TABLE mod_word_rules ADD COLUMN mode INTEGER DEFAULT 0",
            "ALTER TABLE group_post_limits ADD COLUMN apply_mode INTEGER DEFAULT 0",
        ]:
            try:
                c.execute(alter); conn.commit()
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

# ---- Bazaviy funksiyalar ----
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

def save_group_channels(group_username, channels, sub_style="primary", owner_id=0):
    if not group_username.startswith("@"): group_username = "@" + group_username
    conn = get_db()
    try:
        conn.cursor().execute(
            "INSERT OR REPLACE INTO group_settings (group_username, channels, sub_style, owner_id) VALUES (?,?,?,?)",
            (group_username, channels, sub_style, owner_id)
        )
        conn.commit()
    except: pass
    finally: conn.close()

def get_group_channels(group_username):
    conn = get_db()
    try:
        res = conn.cursor().execute(
            "SELECT channels, sub_style FROM group_settings WHERE group_username=?", (group_username,)
        ).fetchone()
        return (res[0], res[1]) if res else (None, None)
    except: return None, None
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
        )
        conn.commit()
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
            "INSERT OR IGNORE INTO admin_groups (admin_id, chat_id) VALUES (?,?)",
            (int(admin_id), int(chat_id))
        )
        conn.commit()
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
            "SELECT ag.chat_id, kc.title FROM admin_groups ag LEFT JOIN known_chats kc ON ag.chat_id = kc.chat_id WHERE ag.admin_id=?",
            (int(admin_id),)
        ).fetchall()
    except: return []
    finally: conn.close()

# ---- Moderator so'z filtri funksiyalari ----
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
        )
        conn.commit()
    except: pass
    finally: conn.close()

def mod_add_rule(group_id, words, reply, mode=0):
    """
    mode=0 -> Faqat userlar (adminlar o'tkaza oladi)
    mode=1 -> Faqat adminlar (faqat adminlarga taqiqlangan, anti-reklama)
    mode=2 -> Hamma (Ownerdan tashqari barchaga taqiqlangan)
    """
    conn = get_db()
    try:
        conn.cursor().execute(
            "INSERT INTO mod_word_rules (group_id, words, reply, mode) VALUES (?,?,?,?)",
            (int(group_id), words, reply, int(mode))
        )
        conn.commit()
    except: pass
    finally: conn.close()

def mod_get_rules(group_id):
    conn = get_db()
    try:
        return conn.cursor().execute(
            "SELECT id, words, reply, mode FROM mod_word_rules WHERE group_id=?", (int(group_id),)
        ).fetchall()
    except: return []
    finally: conn.close()

def mod_delete_rule(rule_id):
    conn = get_db()
    try: conn.cursor().execute("DELETE FROM mod_word_rules WHERE id=?", (rule_id,)); conn.commit()
    except: pass
    finally: conn.close()

def mod_delete_rule_word(rule_id, word_to_del):
    conn = get_db()
    try:
        c = conn.cursor()
        res = c.execute("SELECT words FROM mod_word_rules WHERE id=?", (rule_id,)).fetchone()
        if not res: return
        new_list = [w.strip() for w in res[0].split(",") if w.strip() != word_to_del]
        if new_list: c.execute("UPDATE mod_word_rules SET words=? WHERE id=?", (",".join(new_list), rule_id))
        else: c.execute("DELETE FROM mod_word_rules WHERE id=?", (rule_id,))
        conn.commit()
    except: pass
    finally: conn.close()

# ---- POST LIMIT FUNKSIYALARI ----
def set_post_limit(group_id, limit_count, char_threshold, warning_text, apply_mode):
    """
    apply_mode=0 -> Faqat userlar (adminlar cheksiz)
    apply_mode=1 -> Faqat adminlar (userlarga cheksiz, adminlarga limit)
    apply_mode=2 -> Hamma (Ownerdan tashqari barchaga limit)
    """
    conn = get_db()
    try:
        conn.cursor().execute("""
            INSERT OR REPLACE INTO group_post_limits (group_id, limit_count, char_threshold, warning_text, apply_mode)
            VALUES (?,?,?,?,?)
        """, (int(group_id), limit_count, char_threshold, warning_text, int(apply_mode)))
        conn.commit()
    except: pass
    finally: conn.close()

def remove_post_limit(group_id):
    conn = get_db()
    try: conn.cursor().execute("DELETE FROM group_post_limits WHERE group_id=?", (int(group_id),)); conn.commit()
    except: pass
    finally: conn.close()

def get_post_limit_settings(group_id):
    conn = get_db()
    try:
        return conn.cursor().execute(
            "SELECT limit_count, char_threshold, warning_text, apply_mode FROM group_post_limits WHERE group_id=?",
            (int(group_id),)
        ).fetchone()
    except: return None
    finally: conn.close()

def check_and_inc_user_post(user_id, group_id, limit_max):
    today = datetime.date.today().isoformat()
    conn = get_db()
    try:
        c = conn.cursor()
        res = c.execute(
            "SELECT count FROM user_daily_posts WHERE user_id=? AND group_id=? AND post_date=?",
            (user_id, group_id, today)
        ).fetchone()
        current_count = res[0] if res else 0
        if current_count >= limit_max: return False, current_count
        new_count = current_count + 1
        c.execute(
            "INSERT OR REPLACE INTO user_daily_posts (user_id, group_id, post_date, count) VALUES (?,?,?,?)",
            (user_id, group_id, today, new_count)
        )
        conn.commit()
        return True, new_count
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
            if member.status in ["creator", "administrator"]: accessible.append((g_id, g_name))
        except (TelegramBadRequest, TelegramForbiddenError): continue
    return accessible

# ============================================================
#                           FSM STATES
# ============================================================
class SubState(StatesGroup):
    waiting_group     = State()
    waiting_channels  = State()
    waiting_sub_color = State()
    waiting_del_group = State()

class PostState(StatesGroup):
    waiting_content       = State()
    waiting_btn_name      = State()
    waiting_btn_url       = State()
    waiting_btn_color     = State()
    choose_target         = State()
    waiting_specific_chat = State()

class AdminManage(StatesGroup):
    waiting_new_admin_id    = State()
    waiting_remove_admin_id = State()

class ProManage(StatesGroup):
    waiting_pro_add_id    = State()
    waiting_pro_remove_id = State()

class ModState(StatesGroup):
    waiting_for_words = State()
    waiting_for_reply = State()
    waiting_word_mode = State()
    waiting_del_rule  = State()

class LimitState(StatesGroup):
    waiting_count  = State()
    waiting_chars  = State()
    waiting_text   = State()
    waiting_target = State()

# ============================================================
#                     INLINE KLAVIATURALAR
# ============================================================
def main_menu_inline(uid):
    kb = InlineKeyboardBuilder()
    if is_admin(uid):
        kb.button(text="📝 Post Yaratish",      callback_data="menu_post", style="primary")
        kb.button(text="🔒 Majburiy Obuna",     callback_data="menu_sub", style="primary")
        kb.button(text="🛡 So'z Filtri & Limit", callback_data="menu_mod", style="primary")
    if is_founder(uid):
        kb.button(text="👤 Adminlar boshqaruvi", callback_data="menu_admins", style="success")
        kb.button(text="⭐️ Pro boshqaruvi",      callback_data="menu_pro_mgmt", style="success")
    if not is_admin(uid):
        kb.button(text="⭐️ Pro Versiya",   callback_data="menu_pro", style="success")
        kb.button(text="🔑 Admin so'rash", callback_data="menu_req_admin", style="primary")
    kb.adjust(1)
    return kb.as_markup()

def color_kb(prefix):
    kb = InlineKeyboardBuilder()
    kb.button(text="Yashil 🟢", callback_data=f"{prefix}_success", style="success")
    kb.button(text="Qizil 🔴",  callback_data=f"{prefix}_danger", style="danger")
    kb.button(text="Ko'k 🔵",   callback_data=f"{prefix}_primary", style="primary")
    kb.adjust(1)
    return kb.as_markup()

def cancel_kb(prefix="cancel"):
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Bekor qilish", callback_data=prefix, style="danger")
    return kb.as_markup()

def done_or_cancel_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Tayyor — Tugma kerak emas", callback_data="btn_done", style="success")
    kb.button(text="🔙 Bekor qilish", callback_data="cancel", style="danger")
    kb.adjust(1)
    return kb.as_markup()

def admin_manage_inline():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Admin qo'shish",    callback_data="adm_add", style="success")
    kb.button(text="➖ Admin o'chirish",   callback_data="adm_remove", style="danger")
    kb.button(text="📋 Adminlar ro'yxati", callback_data="adm_list", style="primary")
    kb.button(text="🔙 Orqaga",            callback_data="menu_back", style="danger")
    kb.adjust(2)
    return kb.as_markup()

def pro_manage_inline():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Pro berish",    callback_data="pro_add", style="success")
    kb.button(text="➖ Pro o'chirish", callback_data="pro_remove", style="danger")
    kb.button(text="📋 Pro ro'yxati",  callback_data="pro_list", style="primary")
    kb.button(text="🔙 Orqaga",        callback_data="menu_back", style="danger")
    kb.adjust(2)
    return kb.as_markup()

def target_inline(uid):
    kb = InlineKeyboardBuilder()
    if is_founder(uid):
        kb.button(text="🌐 Barchaga yuborish",      callback_data="target_all", style="primary")
        kb.button(text="🎯 Maxsus chatga yuborish",  callback_data="target_specific", style="primary")
    groups = get_admin_groups_info(uid)
    for g in groups:
        title = g[1] or f"Chat {g[0]}"
        kb.button(text=f"📢 {title}", callback_data=f"target_group_{g[0]}", style="primary")
    kb.button(text="📤 O'zimga yuborish", callback_data="target_self", style="success")
    kb.button(text="🔙 Bekor qilish",    callback_data="cancel", style="danger")
    kb.adjust(1)
    return kb.as_markup()

def mod_main_kb():
    return (
        InlineKeyboardBuilder()
        .button(text="🏢 Mening guruhlarim",    callback_data="mod_list_groups", style="primary")
        .button(text="🔄 Ro'yxatni yangilash",  callback_data="mod_list_groups", style="success")
        .button(text="🔙 Bosh menyu",           callback_data="menu_back", style="danger")
        .adjust(1)
        .as_markup()
    )

def sub_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Obuna qo'shish",   callback_data="sub_add", style="success")
    kb.button(text="➖ Obunaни o'chirish", callback_data="sub_del", style="danger")
    kb.button(text="🔙 Orqaga",           callback_data="menu_back", style="danger")
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
#                        BOT SETUP
# ============================================================
dp = Dispatcher()
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# ============================================================
#                     YORDAMCHI FUNKSIYALAR
# ============================================================
async def auto_delete_warning(chat_id, user_id, msg, delay: int):
    """Ogohlantirishni belgilangan vaqtdan keyin o'chiradi"""
    await asyncio.sleep(delay)
    try: await msg.delete()
    except: pass
    finally:
        user_key = (chat_id, user_id)
        active_warnings.pop(user_key, None)

async def poll_sub_until_joined(chat_id, user_id, warn_msg, channels: str, delay: int):
    """
    Har SUB_CHECK_INTERVAL soniyada userning obuna holatini tekshiradi.
    Agar user obuna bo'lsa yoki vaqt tugasa, ogohlantirishni o'chiradi.
    """
    start = time.time()
    while time.time() - start < delay:
        await asyncio.sleep(SUB_CHECK_INTERVAL)
        nosub = await check_sub(user_id, channels)
        if not nosub:
            # User obuna bo'ldi — ogohlantirishni darhol o'chiramiz
            try: await warn_msg.delete()
            except: pass
            user_key = (chat_id, user_id)
            active_warnings.pop(user_key, None)
            active_sub_polls.pop(user_key, None)
            return
    # Vaqt tugadi — ogohlantirishni o'chiramiz
    try: await warn_msg.delete()
    except: pass
    user_key = (chat_id, user_id)
    active_warnings.pop(user_key, None)
    active_sub_polls.pop(user_key, None)

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

async def _do_send(data, targets):
    builder = InlineKeyboardBuilder()
    for b in data.get('btns', []):
        try: builder.button(text=b['text'], url=b['url'], style=b.get('style', 'primary'))
        except: pass
    rm = builder.as_markup() if data.get('btns') else None
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

async def is_chat_admin(bot_instance, chat_id, user_id):
    try:
        m = await bot_instance.get_chat_member(chat_id, user_id)
        return m.status in ["creator", "administrator"]
    except: return False

# ============================================================
#                  ASOSIY HANDLERLAR
# ============================================================

# ---- JOIN REQUEST ----
@dp.chat_join_request()
async def handle_join_request(update: types.ChatJoinRequest):
    chat_id = update.chat.id; now = time.time()
    if chat_id not in group_stats:
        group_stats[chat_id] = {'start_time': now, 'count': 0}
    s = group_stats[chat_id]
    if now - s['start_time'] > LIMIT_TIME:
        s['start_time'] = now; s['count'] = 0
    if s['count'] < LIMIT_PEOPLE:
        try: await update.approve(); s['count'] += 1
        except: pass

# ---- KIRISH/CHIQISH XABARLARI ----
@dp.message(F.new_chat_members | F.left_chat_member)
async def delete_service_messages(message: Message):
    try: await message.delete()
    except: pass

# ---- BOT GURUHGA QO'SHILGANDA ----
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

# ---- START ----
@dp.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    add_known_chat(message.chat.id, message.chat.type, message.chat.title or message.from_user.full_name)
    if message.chat.type != 'private': return
    await message.answer("👋", reply_markup=ReplyKeyboardRemove())
    await send_main_menu(message, message.from_user.id)

# ---- ORQAGA / BEKOR QILISH ----
@dp.callback_query(F.data == "menu_back")
@dp.callback_query(F.data == "cancel")
async def back_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await send_main_menu(call, call.from_user.id)
    await call.answer()

# ============================================================
#                     ADMIN SO'ROV
# ============================================================
@dp.callback_query(F.data == "menu_req_admin")
async def req_admin_cb(call: CallbackQuery):
    uid = call.from_user.id
    if is_admin(uid): return await call.answer("Siz allaqachon adminsiz!", show_alert=True)
    kb = (
        InlineKeyboardBuilder()
        .button(text="✅ Tasdiqlash", callback_data=f"approve_{uid}", style="success")
        .button(text="❌ Rad etish",  callback_data=f"reject_{uid}", style="danger")
        .as_markup()
    )
    try:
        await bot.send_message(
            MAIN_ADMIN_ID,
            f"🔔 <b>Admin so'rovi!</b>\nIsm: {html.bold(call.from_user.full_name)}\nID: <code>{uid}</code>",
            reply_markup=kb
        )
        await call.message.edit_text("✅ So'rov yuborildi! Founder ko'rib chiqadi.", reply_markup=cancel_kb())
    except: await call.answer("Xatolik yuz berdi!", show_alert=True)

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
#                     ADMIN BOSHQARUVI
# ============================================================
@dp.callback_query(F.data == "menu_admins")
async def admin_panel_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    await call.message.edit_text("👤 <b>Adminlar boshqaruvi</b>", reply_markup=admin_manage_inline())

@dp.callback_query(F.data == "adm_list")
async def adm_list_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    admins = get_all_admins()
    if admins:
        text = "👤 <b>Adminlar ro'yxati:</b>\n\n" + "\n".join([f"• {a[1]} — <code>{a[0]}</code>" for a in admins])
    else:
        text = "Adminlar ro'yxati bo'sh."
    kb = InlineKeyboardBuilder().button(text="🔙 Orqaga", callback_data="menu_admins", style="danger")
    await call.message.edit_text(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "adm_add")
async def adm_add_cb(call: CallbackQuery, state: FSMContext):
    if not is_founder(call.from_user.id): return
    await state.set_state(AdminManage.waiting_new_admin_id)
    await call.message.edit_text("➕ Yangi admin <b>ID</b>sini yuboring:", reply_markup=cancel_kb("menu_admins"))

@dp.message(AdminManage.waiting_new_admin_id)
async def adm_add_msg(message: Message, state: FSMContext):
    if not is_founder(message.from_user.id): return
    parts = message.text.strip().split(maxsplit=1)
    uid_str = parts[0]
    name = parts[1] if len(parts) > 1 else "Admin"
    if uid_str.isdigit():
        add_admin_db(int(uid_str), name)
        await state.clear()
        await message.answer(f"✅ <code>{uid_str}</code> — <b>{name}</b> admin qilindi.")
        try: await bot.send_message(int(uid_str), "🎉 Siz admin bo'ldingiz! /start bosing.")
        except: pass
    else:
        await message.answer("❌ Noto'g'ri format. Faqat ID raqam kiriting (va ixtiyoriy ism).")

@dp.callback_query(F.data == "adm_remove")
async def adm_remove_cb(call: CallbackQuery, state: FSMContext):
    if not is_founder(call.from_user.id): return
    admins = get_all_admins()
    if not admins:
        return await call.answer("Adminlar ro'yxati bo'sh!", show_alert=True)
    kb = InlineKeyboardBuilder()
    for uid, name in admins:
        kb.button(text=f"❌ {name} ({uid})", callback_data=f"adm_del_{uid}", style="danger")
    kb.button(text="🔙 Orqaga", callback_data="menu_admins", style="primary")
    kb.adjust(1)
    await call.message.edit_text("O'chirmoqchi bo'lgan adminni tanlang:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("adm_del_"))
async def adm_del_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    uid = int(call.data.split("_")[2])
    remove_admin_db(uid)
    await call.message.edit_text(f"✅ <code>{uid}</code> adminlikdan o'chirildi.", reply_markup=cancel_kb("menu_admins"))
    try: await bot.send_message(uid, "ℹ️ Adminligingiz bekor qilindi.")
    except: pass

# ============================================================
#                     PRO BOSHQARUVI
# ============================================================
@dp.callback_query(F.data == "menu_pro_mgmt")
async def pro_panel_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    await call.message.edit_text("⭐️ <b>Pro boshqaruvi</b>", reply_markup=pro_manage_inline())

@dp.callback_query(F.data == "pro_list")
async def pro_list_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    pros = get_all_pro_users()
    text = "⭐️ <b>Pro Foydalanuvchilar:</b>\n\n" + "\n".join([f"• {a[1]} — <code>{a[0]}</code>" for a in pros]) if pros else "Ro'yxat bo'sh."
    kb = InlineKeyboardBuilder().button(text="🔙 Orqaga", callback_data="menu_pro_mgmt", style="danger")
    await call.message.edit_text(text, reply_markup=kb.as_markup())

@dp.callback_query(F.data == "pro_add")
async def pro_add_cb(call: CallbackQuery, state: FSMContext):
    if not is_founder(call.from_user.id): return
    await state.set_state(ProManage.waiting_pro_add_id)
    await call.message.edit_text("➕ Pro bermoqchi bo'lgan foydalanuvchi <b>ID</b>sini yuboring:", reply_markup=cancel_kb("menu_pro_mgmt"))

@dp.message(ProManage.waiting_pro_add_id)
async def pro_add_msg(message: Message, state: FSMContext):
    if not is_founder(message.from_user.id): return
    parts = message.text.strip().split(maxsplit=1)
    uid_str = parts[0]
    name = parts[1] if len(parts) > 1 else "Pro User"
    if uid_str.isdigit():
        add_pro_user(int(uid_str), name)
        await state.clear()
        await message.answer(f"✅ <code>{uid_str}</code> — <b>{name}</b> Pro oldi.")
        try: await bot.send_message(int(uid_str), "🌟 Tabriklaymiz! Sizga Pro maqomi berildi!")
        except: pass
    else:
        await message.answer("❌ Noto'g'ri format. Faqat ID raqam kiriting.")

@dp.callback_query(F.data == "pro_remove")
async def pro_remove_cb(call: CallbackQuery, state: FSMContext):
    if not is_founder(call.from_user.id): return
    pros = get_all_pro_users()
    if not pros:
        return await call.answer("Pro foydalanuvchilar bo'sh!", show_alert=True)
    kb = InlineKeyboardBuilder()
    for uid, name in pros:
        kb.button(text=f"❌ {name} ({uid})", callback_data=f"pro_del_{uid}", style="danger")
    kb.button(text="🔙 Orqaga", callback_data="menu_pro_mgmt", style="primary")
    kb.adjust(1)
    await call.message.edit_text("O'chirmoqchi bo'lgan Pro userni tanlang:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("pro_del_"))
async def pro_del_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    uid = int(call.data.split("_")[2])
    remove_pro_user(uid)
    await call.message.edit_text(f"✅ <code>{uid}</code> Pro maqomidan o'chirildi.", reply_markup=cancel_kb("menu_pro_mgmt"))
    try: await bot.send_message(uid, "ℹ️ Pro maqomingiz bekor qilindi.")
    except: pass

@dp.callback_query(F.data == "menu_pro")
async def pro_info_cb(call: CallbackQuery):
    uid = call.from_user.id
    if is_pro(uid):
        await call.answer("Siz allaqachon Pro foydalanuvchisiz! ⭐️", show_alert=True)
    else:
        await call.answer("Pro versiya haqida ma'lumot uchun adminga murojaat qiling.", show_alert=True)

# ============================================================
#                     POST YARATISH
# ============================================================
@dp.callback_query(F.data == "menu_post")
async def post_start_cb(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    await state.set_state(PostState.waiting_content)
    await state.update_data(user_id=call.from_user.id)
    await call.message.edit_text(
        "📝 Post mazmunini yuboring (Rasm, Video, GIF, Ovozli xabar yoki Matn):",
        reply_markup=cancel_kb()
    )

@dp.message(PostState.waiting_content)
async def post_content(message: Message, state: FSMContext):
    await state.update_data(from_chat_id=message.chat.id, message_id=message.message_id, btns=[])
    await state.set_state(PostState.waiting_btn_name)
    await message.answer("✅ Qabul qilindi!\n\nTugma nomi yuboring yoki ✅ Tayyor bosing:", reply_markup=done_or_cancel_kb())

@dp.callback_query(F.data == "btn_done", PostState.waiting_btn_name)
async def post_done_cb(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.set_state(PostState.choose_target)
    await call.message.edit_text("📤 Qayerga yuboramiz?", reply_markup=target_inline(data['user_id']))

@dp.message(PostState.waiting_btn_name)
async def btn_name_msg(message: Message, state: FSMContext):
    await state.update_data(t_n=message.text)
    await state.set_state(PostState.waiting_btn_url)
    await message.answer(f"🔗 «{message.text}» tugmasi uchun link yuboring:", reply_markup=cancel_kb())

@dp.message(PostState.waiting_btn_url)
async def btn_url_msg(message: Message, state: FSMContext):
    if message.text.startswith("http"):
        await state.update_data(t_u=message.text)
        await state.set_state(PostState.waiting_btn_color)
        await message.answer("🎨 Tugma rangini tanlang:", reply_markup=color_kb("style"))
    else:
        await message.answer("❌ Link http:// yoki https:// bilan boshlanishi kerak.")

@dp.callback_query(F.data.startswith("style_"), PostState.waiting_btn_color)
async def style_cb(call: CallbackQuery, state: FSMContext):
    color = call.data.split("_")[1]
    data = await state.get_data()
    btns = data.get('btns', [])
    btns.append({'text': data['t_n'], 'url': data['t_u'], 'style': color})
    await state.update_data(btns=btns)
    await state.set_state(PostState.waiting_btn_name)
    await call.message.edit_text(
        f"✅ Tugma qo'shildi (Jami: {len(btns)} ta).\n\nYana tugma qo'shing yoki ✅ Tayyor bosing:",
        reply_markup=done_or_cancel_kb()
    )

# ---- TARGET CALLBACKLAR ----
@dp.callback_query(F.data == "target_all", PostState.choose_target)
async def target_all_cb(call: CallbackQuery, state: FSMContext):
    if not is_founder(call.from_user.id): return
    data = await state.get_data()
    await state.clear()
    chats = [(r[0],) for r in get_all_chats()]
    await call.message.edit_text("⏳ Yuborilmoqda...")
    success = await _do_send(data, chats)
    await call.message.edit_text(f"✅ Barcha chatlarga yuborildi!\nMuvaffaqiyatli: {success} ta")

@dp.callback_query(F.data == "target_specific", PostState.choose_target)
async def target_specific_cb(call: CallbackQuery, state: FSMContext):
    if not is_founder(call.from_user.id): return
    await state.set_state(PostState.waiting_specific_chat)
    await call.message.edit_text("🎯 Maxsus chat ID yuboring:", reply_markup=cancel_kb())

@dp.message(PostState.waiting_specific_chat)
async def specific_chat_msg(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    try:
        chat_id = int(message.text.strip())
        success = await _do_send(data, [(chat_id,)])
        await message.answer(f"✅ Yuborildi! Muvaffaqiyat: {success}")
    except:
        await message.answer("❌ Noto'g'ri chat ID.")

@dp.callback_query(F.data == "target_self", PostState.choose_target)
async def target_self_cb(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    success = await _do_send(data, [(call.from_user.id,)])
    await call.message.edit_text(f"✅ O'zingizga yuborildi!" if success else "❌ Yuborishda xatolik.")

@dp.callback_query(F.data.startswith("target_group_"), PostState.choose_target)
async def target_group_cb(call: CallbackQuery, state: FSMContext):
    g_id = int(call.data.split("_")[2])
    data = await state.get_data()
    groups = get_admin_groups(data['user_id'])
    if g_id not in groups and not is_founder(call.from_user.id):
        return await call.answer("Bu guruh sizning guruhingiz emas!", show_alert=True)
    await state.clear()
    success = await _do_send(data, [(g_id,)])
    await call.message.edit_text(f"✅ Guruhga yuborildi!" if success else "❌ Yuborishda xatolik.")

# ============================================================
#                     MAJBURIY OBUNA
# ============================================================
@dp.callback_query(F.data == "menu_sub")
async def sub_menu_cb(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("🔒 <b>Majburiy Obuna Boshqaruvi</b>", reply_markup=sub_menu_kb())

@dp.callback_query(F.data == "sub_add")
async def sub_add_cb(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    await state.set_state(SubState.waiting_group)
    await call.message.edit_text(
        "📝 Guruh @username yuboring:\n\n<i>Misol: @mygroupname</i>",
        reply_markup=cancel_kb("menu_sub")
    )

@dp.callback_query(F.data == "sub_del")
async def sub_del_cb(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    await state.set_state(SubState.waiting_del_group)
    await call.message.edit_text(
        "🗑 O'chirmoqchi bo'lgan guruh @username yuboring:",
        reply_markup=cancel_kb("menu_sub")
    )

@dp.message(SubState.waiting_del_group)
async def sub_del_msg(message: Message, state: FSMContext):
    gr = message.text.strip()
    if not gr.startswith("@"): gr = "@" + gr
    delete_group_channels(gr)
    await state.clear()
    await message.answer(f"✅ <b>{gr}</b> guruhining majburiy obunasi o'chirildi.")

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
    await state.set_state(SubState.waiting_sub_color)
    await message.answer("🎨 Obuna tugmasi rangini tanlang:", reply_markup=color_kb("subcolor"))

@dp.callback_query(F.data.startswith("subcolor_"))
async def subcolor_cb(call: CallbackQuery, state: FSMContext):
    color = call.data.split("_")[1]
    data = await state.get_data()
    save_group_channels(data['gr'], data['chans'], sub_style=color, owner_id=call.from_user.id)
    await call.message.edit_text(
        f"✅ <b>{data['gr']}</b> guruhi uchun majburiy obuna saqlandi!\n"
        f"Kanallar: {data['chans']}\nTugma rangi: {color}"
    )
    await state.clear()

# ============================================================
#                     MODERATOR & LIMIT
# ============================================================
@dp.callback_query(F.data == "menu_mod")
async def mod_menu_cb(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("🛡 <b>So'z Filtri & Kunlik Limit</b>", reply_markup=mod_main_kb())

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

@dp.callback_query(F.data.startswith("mod_manage_"))
async def mod_manage_cb(call: CallbackQuery):
    g_id = call.data.split("_")[2]
    rules = mod_get_rules(g_id)
    l_set = get_post_limit_settings(int(g_id))
    limit_text = f"✅ Yoqilgan (kunda {l_set[0]} ta)" if l_set else "❌ O'chirilgan"
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Yangi so'z filtri qo'shish", callback_data=f"mod_add_{g_id}", style="success")
    kb.button(text=f"🗑 Filtrlarni ko'rish ({len(rules)} ta)", callback_data=f"mod_view_{g_id}", style="primary")
    kb.button(text=f"📊 Kunlik Limit: {limit_text}", callback_data=f"mod_limit_{g_id}", style="primary")
    if l_set:
        kb.button(text="🗑 Limitni o'chirish", callback_data=f"mod_limit_del_{g_id}", style="danger")
    kb.button(text="🔙 Orqaga", callback_data="mod_list_groups", style="danger")
    kb.adjust(1)
    await call.message.edit_text("⚙️ <b>Guruh sozlamalari:</b>", reply_markup=kb.as_markup())

# ---- SO'Z FILTRI QO'SHISH ----
@dp.callback_query(F.data.startswith("mod_add_"))
async def mod_add_cb(call: CallbackQuery, state: FSMContext):
    g_id = call.data.split("_")[2]
    await state.update_data(active_gid=g_id)
    await state.set_state(ModState.waiting_for_words)
    await call.message.edit_text(
        "📝 Taqiqlangan so'zlarni vergul bilan yuboring:\n\n<i>Misol: reklama, spam, link</i>",
        reply_markup=cancel_kb(f"mod_manage_{g_id}")
    )

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
        "🛡 <b>Faqat Adminlarga</b> — Faqat adminlarga taqiqlangan (anti-reklama)\n"
        "🔥 <b>Hammaga</b> — Ownerdan tashqari barchaga taqiqlangan",
        reply_markup=word_mode_kb()
    )

@dp.callback_query(F.data.startswith("wmode_"), ModState.waiting_word_mode)
async def mod_mode_cb(call: CallbackQuery, state: FSMContext):
    mode = int(call.data.split("_")[1])
    data = await state.get_data()
    mod_add_rule(data['active_gid'], data['w_words'], data['w_reply'], mode)
    mode_text = {0: "👥 Faqat Userlarga", 1: "🛡 Faqat Adminlarga", 2: "🔥 Hammaga"}[mode]
    await call.message.edit_text(
        f"✅ So'z filtri qo'shildi!\n\n"
        f"So'zlar: <code>{data['w_words']}</code>\n"
        f"Javob: {data['w_reply']}\n"
        f"Rejim: {mode_text}"
    )
    await state.clear()

# ---- SO'Z FILTRLARINI KO'RISH ----
@dp.callback_query(F.data.startswith("mod_view_"))
async def mod_view_cb(call: CallbackQuery):
    g_id = call.data.split("_")[2]
    rules = mod_get_rules(g_id)
    if not rules:
        return await call.message.edit_text(
            "❌ Hech qanday filtr yo'q.",
            reply_markup=cancel_kb(f"mod_manage_{g_id}")
        )
    kb = InlineKeyboardBuilder()
    mode_icons = {0: "👥", 1: "🛡", 2: "🔥"}
    for r_id, words, reply, mode in rules:
        icon = mode_icons.get(mode, "👥")
        kb.button(
            text=f"{icon} {words[:25]}... | {reply[:15]}",
            callback_data=f"mod_rule_{r_id}_{g_id}",
            style="primary"
        )
    kb.button(text="🔙 Orqaga", callback_data=f"mod_manage_{g_id}", style="danger")
    kb.adjust(1)
    await call.message.edit_text(f"🗂 Filtrlar ({len(rules)} ta):", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("mod_rule_"))
async def mod_rule_detail_cb(call: CallbackQuery):
    parts = call.data.split("_")
    r_id = parts[2]; g_id = parts[3]
    rules = mod_get_rules(g_id)
    rule = next((r for r in rules if str(r[0]) == r_id), None)
    if not rule:
        return await call.answer("Filtr topilmadi!", show_alert=True)
    mode_text = {0: "👥 Faqat Userlarga", 1: "🛡 Faqat Adminlarga", 2: "🔥 Hammaga"}.get(rule[3], "Noma'lum")
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Filterni o'chirish", callback_data=f"mod_del_rule_{r_id}_{g_id}", style="danger")
    kb.button(text="🔙 Orqaga", callback_data=f"mod_view_{g_id}", style="primary")
    kb.adjust(1)
    await call.message.edit_text(
        f"📋 <b>Filtr ma'lumotlari:</b>\n\n"
        f"So'zlar: <code>{rule[1]}</code>\n"
        f"Javob: {rule[2]}\n"
        f"Rejim: {mode_text}",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data.startswith("mod_del_rule_"))
async def mod_del_rule_cb(call: CallbackQuery):
    parts = call.data.split("_")
    r_id = int(parts[3]); g_id = parts[4]
    mod_delete_rule(r_id)
    await call.message.edit_text("✅ Filtr o'chirildi.", reply_markup=cancel_kb(f"mod_manage_{g_id}"))

# ---- KUNLIK POST LIMITI ----
@dp.callback_query(F.data.startswith("mod_limit_del_"))
async def mod_limit_del_cb(call: CallbackQuery):
    g_id = int(call.data.split("_")[3])
    remove_post_limit(g_id)
    await call.message.edit_text("✅ Kunlik limit o'chirildi.", reply_markup=cancel_kb(f"mod_manage_{g_id}"))

@dp.callback_query(F.data.startswith("mod_limit_"))
async def mod_limit_start(call: CallbackQuery, state: FSMContext):
    # "mod_limit_del_" bilan boshlanmasligi uchun allaqachon handler yuqorida
    g_id = call.data.split("_")[2]
    await state.update_data(active_gid=g_id)
    current = get_post_limit_settings(int(g_id))
    cur_text = f"\n\n📊 Hozirgi sozlama: kunda {current[0]} ta, {current[1]} belgi" if current else ""
    await state.set_state(LimitState.waiting_count)
    await call.message.edit_text(
        f"🔢 Bir kunda necha marta post tashlasa bo'ladi? (Misol: 3){cur_text}",
        reply_markup=cancel_kb(f"mod_manage_{g_id}")
    )

@dp.message(LimitState.waiting_count)
async def limit_count_set(message: Message, state: FSMContext):
    if message.text.strip().isdigit():
        await state.update_data(l_count=int(message.text.strip()))
        await state.set_state(LimitState.waiting_chars)
        await message.answer(
            "📏 Xabar necha belgidan uzun bo'lsa 'post' hisoblansin? (Misol: 60)\n\n"
            "<i>Bu qisqa salomlashuv xabarlarini limitga kiritmaslik uchun.</i>",
            reply_markup=cancel_kb()
        )
    else:
        await message.answer("❌ Faqat raqam kiriting!")

@dp.message(LimitState.waiting_chars)
async def limit_chars_set(message: Message, state: FSMContext):
    if message.text.strip().isdigit():
        await state.update_data(l_chars=int(message.text.strip()))
        await state.set_state(LimitState.waiting_text)
        await message.answer(
            "📝 Limit to'lganda foydalanuvchiga yuboriladigan ogohlantirish matnini yuboring:",
            reply_markup=cancel_kb()
        )
    else:
        await message.answer("❌ Faqat raqam kiriting!")

@dp.message(LimitState.waiting_text)
async def limit_text_set(message: Message, state: FSMContext):
    await state.update_data(l_text=message.text.strip())
    await state.set_state(LimitState.waiting_target)
    await message.answer(
        "🎯 Limit kimga amal qilsin?\n\n"
        "👥 <b>Faqat Userlarga</b> — Adminlar xohlagancha post tashlaydi\n"
        "🛡 <b>Faqat Adminlarga</b> — Userlarga cheksiz, adminlarga limit\n"
        "🔥 <b>Hammaga</b> — Ownerdan tashqari hamma limitga tushadi",
        reply_markup=limit_mode_kb()
    )

@dp.callback_query(LimitState.waiting_target)
async def limit_final_cb(call: CallbackQuery, state: FSMContext):
    mode = int(call.data.split("_")[2])
    data = await state.get_data()
    set_post_limit(data['active_gid'], data['l_count'], data['l_chars'], data['l_text'], mode)
    mode_text = {0: "👥 Faqat Userlarga", 1: "🛡 Faqat Adminlarga", 2: "🔥 Hammaga"}[mode]
    await call.message.edit_text(
        f"✅ <b>Kunlik limit saqlandi!</b>\n\n"
        f"Post soni: kunda <b>{data['l_count']}</b> ta\n"
        f"Belgi chegarasi: <b>{data['l_chars']}</b> ta\n"
        f"Rejim: {mode_text}\n"
        f"Ogohlantirish: {data['l_text']}"
    )
    await state.clear()

# ============================================================
#              GURUH NAZORATI — ASOSIY WATCHER
# ============================================================
@dp.message()
async def watcher(message: Message):
    if message.chat.type == 'private': return
    add_known_chat(message.chat.id, message.chat.type, message.chat.title or "")
    if is_admin(message.from_user.id):
        register_admin_group(message.from_user.id, message.chat.id)

    chat_id   = message.chat.id
    user_id   = message.from_user.id
    user_key  = (chat_id, user_id)

    # ✅ FOUNDER BYPASS — hech qanday cheklovga tushmaydi
    if is_founder(user_id): return

    # ============================================================
    # 1. OBUNA TEKSHIRUVI
    # ============================================================
    if message.chat.username:
        chans, style = get_group_channels(f"@{message.chat.username}")
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
                                kb.button(
                                    text=f"📢 {c.strip()} ga obuna bo'lish",
                                    url=f"https://t.me/{c_clean}",
                                    style=style or "primary"
                                )
                            kb.adjust(1)
                            w = await message.answer(
                                f"⚠️ {message.from_user.mention_html()}, quyidagi kanallarga a'zo bo'lmasangiz "
                                f"bu guruhda yoza olmaysiz!\n\n"
                                f"A'zo bo'lgach xabaringiz avtomatik o'chiriladi.",
                                reply_markup=kb.as_markup()
                            )
                            # Ogohlantirish tasklari (parallel ishlaydi)
                            active_warnings[user_key] = True

                            # Background task: 4 soniyada bir obuna tekshirish
                            poll_task = asyncio.create_task(
                                poll_sub_until_joined(chat_id, user_id, w, chans, WARNING_TIMEOUT)
                            )
                            active_sub_polls[user_key] = poll_task
                        else:
                            # Allaqachon ogohlantirish bor — faqat xabarni o'chiramiz
                            pass
                        return
            except: pass

    # ============================================================
    # 2. SO'Z FILTRI
    # ============================================================
    if message.text:
        rules = mod_get_rules(chat_id)
        if rules:
            msg_lower = message.text.lower()
            is_c_admin = await is_chat_admin(bot, chat_id, user_id)
            is_c_owner = False
            try:
                m = await bot.get_chat_member(chat_id, user_id)
                if m.status == "creator": is_c_owner = True
            except: pass

            for r_id, words, reply, mode in rules:
                word_list = [w.strip() for w in words.split(",") if w.strip()]
                if not any(w in msg_lower for w in word_list): continue

                # mode=0: Faqat userlarga (adminlar o'tkaza oladi)
                if mode == 0 and is_c_admin: continue
                # mode=1: Faqat adminlarga (userlar o'tkaza oladi)
                if mode == 1 and not is_c_admin: continue
                # mode=2: Hamma (owner o'tkaza oladi)
                if mode == 2 and is_c_owner: continue

                try:
                    await message.delete()
                    if user_key not in active_warnings:
                        w = await message.answer(
                            f"⚠️ {message.from_user.mention_html()}, {reply}"
                        )
                        active_warnings[user_key] = asyncio.create_task(
                            auto_delete_warning(chat_id, user_id, w, WARNING_TIMEOUT)
                        )
                except: pass
                return

    # ============================================================
    # 3. KUNLIK POST LIMITI
    # ============================================================
    if message.text:
        l_set = get_post_limit_settings(chat_id)
        if l_set:
            l_max, l_chars, l_warn, l_mode = l_set

            if len(message.text) >= l_chars:
                is_c_admin = await is_chat_admin(bot, chat_id, user_id)
                is_c_owner = False
                try:
                    m = await bot.get_chat_member(chat_id, user_id)
                    if m.status == "creator": is_c_owner = True
                except: pass

                # mode=0: Faqat userlarga — adminlar o'tkazib yuboriladi
                if l_mode == 0 and is_c_admin: return
                # mode=1: Faqat adminlarga — userlar o'tkazib yuboriladi
                if l_mode == 1 and not is_c_admin: return
                # mode=2: Hamma — owner o'tkazib yuboriladi
                if l_mode == 2 and is_c_owner: return

                allowed, count = check_and_inc_user_post(user_id, chat_id, l_max)
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