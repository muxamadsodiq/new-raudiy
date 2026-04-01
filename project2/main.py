import asyncio
import logging
import sqlite3
import time
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
TOKEN = "8512313403:AAESY3RE6Myo9XNqpXx3Qww2f72iG75jHHU"
MAIN_ADMIN_ID = 5724592490
DB_NAME = "combined_bot.db"

LIMIT_PEOPLE = 3
LIMIT_TIME = 600
WARNING_TIMEOUT = 180  # 3 daqiqa

active_warnings = {}
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

        # --- SAUDIYA BOT JADVALLARI ---
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
        c.execute("""
            CREATE TABLE IF NOT EXISTS admin_groups (
                admin_id INTEGER,
                chat_id INTEGER,
                PRIMARY KEY (admin_id, chat_id)
            )
        """)

        # --- MODERATOR BOT JADVALI ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS mod_groups (
                group_id INTEGER PRIMARY KEY,
                group_name TEXT,
                admin_id INTEGER,
                words TEXT,
                reply TEXT,
                strict_words TEXT
            )
        """)

        # Ustun qo'shish (eski DB bilan moslik)
        for col, default in [("owner_id", "0"), ("sub_style", "'primary'")]:
            try:
                c.execute(f"ALTER TABLE group_settings ADD COLUMN {col} INTEGER DEFAULT {default}")
                conn.commit()
            except:
                pass
        # strict_words ustunini eski DB larga qo'shish
        try:
            c.execute("ALTER TABLE mod_groups ADD COLUMN strict_words TEXT")
            conn.commit()
        except:
            pass

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
    try:
        return conn.cursor().execute("SELECT user_id FROM admins WHERE user_id=?", (int(uid),)).fetchone() is not None
    except: return False
    finally: conn.close()

def is_pro(uid):
    conn = get_db()
    try:
        return conn.cursor().execute("SELECT user_id FROM pro_users WHERE user_id=?", (int(uid),)).fetchone() is not None
    except: return False
    finally: conn.close()

# ---- Adminlar ----
def add_admin_db(uid, name):
    conn = get_db()
    try:
        conn.cursor().execute("INSERT OR REPLACE INTO admins (user_id, full_name) VALUES (?,?)", (int(uid), str(name)))
        conn.commit(); return True
    except: return False
    finally: conn.close()

def remove_admin_db(uid):
    conn = get_db()
    try:
        conn.cursor().execute("DELETE FROM admins WHERE user_id=?", (int(uid),)); conn.commit()
    except: pass
    finally: conn.close()

def get_all_admins():
    conn = get_db()
    try: return conn.cursor().execute("SELECT user_id, full_name FROM admins").fetchall()
    except: return []
    finally: conn.close()

# ---- Pro foydalanuvchilar ----
def add_pro_user(uid, name):
    conn = get_db()
    try:
        conn.cursor().execute("INSERT OR REPLACE INTO pro_users (user_id, full_name) VALUES (?,?)", (int(uid), str(name)))
        conn.commit(); return True
    except: return False
    finally: conn.close()

def remove_pro_user(uid):
    conn = get_db()
    try:
        conn.cursor().execute("DELETE FROM pro_users WHERE user_id=?", (int(uid),)); conn.commit()
    except: pass
    finally: conn.close()

def get_all_pro_users():
    conn = get_db()
    try: return conn.cursor().execute("SELECT user_id, full_name FROM pro_users").fetchall()
    except: return []
    finally: conn.close()

# ---- Guruh sozlamalari (obuna) ----
def save_group_channels(group_username, channels, sub_style="primary", owner_id=0):
    if not group_username.startswith("@"): group_username = "@" + group_username
    conn = get_db()
    try:
        conn.cursor().execute(
            "INSERT OR REPLACE INTO group_settings (group_username, channels, sub_style, owner_id) VALUES (?,?,?,?)",
            (group_username, channels, sub_style, owner_id)
        ); conn.commit()
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
    try:
        conn.cursor().execute("DELETE FROM group_settings WHERE group_username=?", (group_username,)); conn.commit()
    except: pass
    finally: conn.close()

# ---- Known chats ----
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

# ---- Admin guruhlar ----
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
    try:
        return [r[0] for r in conn.cursor().execute(
            "SELECT chat_id FROM admin_groups WHERE admin_id=?", (int(admin_id),)
        ).fetchall()]
    except: return []
    finally: conn.close()

def get_admin_groups_info(admin_id):
    conn = get_db()
    try:
        return conn.cursor().execute("""
            SELECT ag.chat_id, kc.title FROM admin_groups ag
            LEFT JOIN known_chats kc ON ag.chat_id = kc.chat_id
            WHERE ag.admin_id=?
        """, (int(admin_id),)).fetchall()
    except: return []
    finally: conn.close()

# ---- Moderator (so'z filtri) ----
def mod_get_group(group_id):
    conn = get_db()
    try:
        return conn.cursor().execute(
            "SELECT group_name, words, reply FROM mod_groups WHERE group_id=?", (int(group_id),)
        ).fetchone()
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

def mod_update_words(group_id, words, reply, is_strict=False):
    conn = get_db()
    try:
        if is_strict:
            # Faqat strict_words ustunini yangilaydi
            conn.cursor().execute(
                "UPDATE mod_groups SET strict_words=?, reply=? WHERE group_id=?", 
                (words, reply, int(group_id))
            )
        else:
            # Faqat oddiy words ustunini yangilaydi
            conn.cursor().execute(
                "UPDATE mod_groups SET words=?, reply=? WHERE group_id=?", 
                (words, reply, int(group_id))
            )
        conn.commit()
    except Exception as e:
        logging.error(f"Update error: {e}")
    finally:
        conn.close()

def mod_get_words(group_id):
    conn = get_db()
    try:
        res = conn.cursor().execute(
            "SELECT words, reply, strict_words FROM mod_groups WHERE group_id=?", (int(group_id),)
        ).fetchone()
        return res if res else (None, None, None)
    except: return None, None, None
    finally: conn.close()

def mod_set_words(group_id, words_str):
    conn = get_db()
    try:
        conn.cursor().execute(
            "UPDATE mod_groups SET words=? WHERE group_id=?", (words_str, int(group_id))
        ); conn.commit()
    except: pass
    finally: conn.close()

def mod_set_strict_words(group_id, words_str):
    conn = get_db()
    try:
        conn.cursor().execute(
            "UPDATE mod_groups SET strict_words=? WHERE group_id=?", (words_str, int(group_id))
        ); conn.commit()
    except: pass
    finally: conn.close()

async def mod_get_user_groups(user_id: int, bot_instance):
    conn = get_db()
    try:
        all_groups = conn.cursor().execute("SELECT group_id, group_name FROM mod_groups").fetchall()
    except:
        all_groups = []
    finally:
        conn.close()

    accessible = []
    for g_id, g_name in all_groups:
        try:
            member = await bot_instance.get_chat_member(chat_id=g_id, user_id=user_id)
            if member.status in ["creator", "administrator"]:
                accessible.append((g_id, g_name))
        except (TelegramBadRequest, TelegramForbiddenError):
            continue
    return accessible

# ============================================================
#                           FSM STATES
# ============================================================
class SubState(StatesGroup):
    waiting_group    = State()
    waiting_channels = State()
    waiting_sub_color = State()

class PostState(StatesGroup):
    waiting_content      = State()
    waiting_btn_name     = State()
    waiting_btn_url      = State()
    waiting_btn_color    = State()
    choose_target        = State()
    waiting_specific_chat = State()

class AdminManage(StatesGroup):
    waiting_new_admin_id = State()

class ProManage(StatesGroup):
    waiting_pro_user_id = State()

class ModState(StatesGroup):
    waiting_for_words = State()
    waiting_for_reply = State()
    waiting_word_type = State()  # oddiy yoki strict (adminlarga ham)

# ============================================================
#                     INLINE KLAVIATURALAR
# ============================================================
def main_menu_inline(uid):
    kb = InlineKeyboardBuilder()
    if is_admin(uid):
        kb.button(text="📝 Post Yaratish",     callback_data="menu_post")
        kb.button(text="🔒 Majburiy Obuna",    callback_data="menu_sub")
        kb.button(text="🛡 So'z Filtri",        callback_data="menu_mod")
    if is_founder(uid):
        kb.button(text="👤 Adminlar boshqaruvi", callback_data="menu_admins")
        kb.button(text="⭐️ Pro boshqaruvi",      callback_data="menu_pro_mgmt")
    if not is_admin(uid):
        kb.button(text="⭐️ Pro Versiya",   callback_data="menu_pro")
        kb.button(text="🔑 Admin so'rash", callback_data="menu_req_admin")
    kb.adjust(1)
    return kb.as_markup()

def color_kb(prefix):
    kb = InlineKeyboardBuilder()
    kb.button(text="Yashil 🟢", callback_data=f"{prefix}_success")
    kb.button(text="Qizil 🔴",  callback_data=f"{prefix}_danger")
    kb.button(text="Ko'k 🔵",   callback_data=f"{prefix}_primary")
    kb.adjust(1)
    return kb.as_markup()

def cancel_kb(prefix="cancel"):
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Bekor qilish", callback_data=prefix)
    return kb.as_markup()

def done_or_cancel_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Tayyor — Tugma kerak emas", callback_data="btn_done")
    kb.button(text="🔙 Bekor qilish", callback_data="cancel")
    kb.adjust(1)
    return kb.as_markup()

def admin_manage_inline():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Admin qo'shish",    callback_data="adm_add")
    kb.button(text="➖ Admin o'chirish",   callback_data="adm_remove")
    kb.button(text="📋 Adminlar ro'yxati", callback_data="adm_list")
    kb.button(text="🔙 Orqaga",            callback_data="menu_back")
    kb.adjust(2)
    return kb.as_markup()

def pro_manage_inline():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Pro berish",    callback_data="pro_add")
    kb.button(text="➖ Pro o'chirish", callback_data="pro_remove")
    kb.button(text="📋 Pro ro'yxati",  callback_data="pro_list")
    kb.button(text="🔙 Orqaga",        callback_data="menu_back")
    kb.adjust(2)
    return kb.as_markup()

def target_inline(uid):
    kb = InlineKeyboardBuilder()
    if is_founder(uid):
        kb.button(text="🌐 Barchaga yuborish",      callback_data="target_all")
        kb.button(text="🎯 Maxsus chatga yuborish", callback_data="target_specific")
    groups = get_admin_groups_info(uid)
    for g in groups:
        title = g[1] or f"Chat {g[0]}"
        kb.button(text=f"📢 {title}", callback_data=f"target_group_{g[0]}")
    kb.button(text="📤 O'zimga yuborish", callback_data="target_self")
    kb.button(text="🔙 Bekor qilish",     callback_data="cancel")
    kb.adjust(1)
    return kb.as_markup()

def mod_main_kb():
    return InlineKeyboardBuilder().button(
        text="🏢 Mening guruhlarim", callback_data="mod_list_groups"
    ).button(
        text="🔄 Ro'yxatni yangilash", callback_data="mod_list_groups"
    ).button(
        text="🔙 Bosh menyu", callback_data="menu_back"
    ).adjust(1).as_markup()

# ============================================================
#                        BOT SETUP
# ============================================================
dp = Dispatcher()
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# ============================================================
#                     YORDAMCHI FUNKSIYALAR
# ============================================================
async def send_main_menu(target, uid, text=None):
    if is_founder(uid):     role = "👑 Founder"
    elif is_admin(uid):     role = "👤 Admin"
    elif is_pro(uid):       role = "⭐️ Pro foydalanuvchi"
    else:                   role = "👥 Foydalanuvchi"
    msg = text or f"🤖 <b>Bosh menyu</b>\n\nRol: {role}\n\nAmalni tanlang:"
    if isinstance(target, Message):
        await target.answer(msg, reply_markup=main_menu_inline(uid))
    elif isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(msg, reply_markup=main_menu_inline(uid))
        except:
            await target.message.answer(msg, reply_markup=main_menu_inline(uid))

async def _do_send(data, targets):
    builder = InlineKeyboardBuilder()
    for b in data.get('btns', []):
        try:
            builder.row(types.InlineKeyboardButton(text=b['text'], url=b['url'], style=b['style']))
        except TypeError:
            builder.row(types.InlineKeyboardButton(text=b['text'], url=b['url']))
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

# ============================================================
#                  ASOSIY HANDLERLAR
# ============================================================

# ---- JOIN REQUEST ----
@dp.chat_join_request()
async def handle_join_request(update: types.ChatJoinRequest):
    chat_id = update.chat.id
    now = time.time()
    if chat_id not in group_stats:
        group_stats[chat_id] = {'start_time': now, 'count': 0}
    s = group_stats[chat_id]
    if now - s['start_time'] > LIMIT_TIME:
        s['start_time'] = now; s['count'] = 0
    if s['count'] < LIMIT_PEOPLE:
        try:
            await update.approve(); s['count'] += 1
        except Exception as e:
            logging.error(f"Approve xatosi: {e}")

# ---- SERVIS XABARLAR ----
@dp.message(F.new_chat_members | F.left_chat_member)
async def delete_service_messages(message: Message):
    try: await message.delete()
    except: pass

# ---- BOT GURUHGA QO'SHILDI ----
@dp.my_chat_member()
async def bot_added_to_group(update: types.ChatMemberUpdated):
    new = update.new_chat_member
    me = await bot.get_me()
    if new.user.id == me.id and new.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR]:
        chat = update.chat
        add_known_chat(chat.id, chat.type, chat.title or "")
        # Moderator DB ga ham qo'shish
        mod_save_group(chat.id, chat.title or str(chat.id), update.from_user.id)
        adder_id = update.from_user.id
        if is_admin(adder_id):
            register_admin_group(adder_id, chat.id)

# ---- START ----
@dp.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    add_known_chat(message.chat.id, message.chat.type, message.chat.title or message.from_user.full_name)
    if message.chat.type != 'private': return
    await message.answer("👋", reply_markup=ReplyKeyboardRemove())
    await send_main_menu(message, message.from_user.id)

# ---- ORQAGA / BEKOR ----
@dp.callback_query(F.data == "menu_back")
async def menu_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await send_main_menu(call, call.from_user.id)
    await call.answer()

@dp.callback_query(F.data == "cancel")
async def cancel_cb(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await send_main_menu(call, call.from_user.id)
    await call.answer()

# ============================================================
#               ADMIN SO'RASH / PRO SO'RASH
# ============================================================
@dp.callback_query(F.data == "menu_req_admin")
async def req_admin_cb(call: CallbackQuery):
    uid = call.from_user.id
    if is_admin(uid):
        await call.answer("Siz allaqachon adminsiz!", show_alert=True); return
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Tasdiqlash", callback_data=f"approve_{uid}")
    kb.button(text="❌ Rad etish",  callback_data=f"reject_{uid}")
    user_link = f'<a href="tg://user?id={uid}">{html.quote(call.from_user.full_name)}</a>'
    try:
        await bot.send_message(MAIN_ADMIN_ID, f"🔔 <b>Admin so'rovi!</b>\nIsm: {user_link}\nID: <code>{uid}</code>", reply_markup=kb.as_markup())
        await call.message.edit_text("✅ So'rov yuborildi! Javob kuting.", reply_markup=cancel_kb())
    except:
        await call.answer("Xatolik!", show_alert=True)
    await call.answer()

@dp.callback_query(F.data.startswith("approve_"))
async def appr_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    uid = int(call.data.split("_")[1])
    add_admin_db(uid, "Tasdiqlangan")
    await call.message.edit_text(f"✅ ID <code>{uid}</code> admin qilindi.")
    try: await bot.send_message(uid, "🎉 Admin so'rovingiz tasdiqlandi!\n\n/start bosing.")
    except: pass
    await call.answer()

@dp.callback_query(F.data.startswith("reject_"))
async def rej_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    uid = int(call.data.split("_")[1])
    await call.message.edit_text("❌ So'rov rad etildi.")
    try: await bot.send_message(uid, "❌ Admin so'rovingiz rad etildi.")
    except: pass
    await call.answer()

@dp.callback_query(F.data == "menu_pro")
async def pro_info_cb(call: CallbackQuery):
    uid = call.from_user.id
    if is_pro(uid):
        await call.answer("Sizda allaqachon Pro bor!", show_alert=True); return
    kb = InlineKeyboardBuilder()
    kb.button(text="📩 Pro so'rash", callback_data=f"pro_request_{uid}")
    kb.button(text="🔙 Orqaga",      callback_data="menu_back")
    kb.adjust(1)
    await call.message.edit_text("⭐️ <b>Pro Versiya</b>\n\nQo'shimcha imkoniyatlar uchun:", reply_markup=kb.as_markup())
    await call.answer()

@dp.callback_query(F.data.startswith("pro_request_"))
async def pro_request_cb(call: CallbackQuery):
    uid = int(call.data.split("_")[2])
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Pro berish", callback_data=f"give_pro_{uid}")
    kb.button(text="❌ Rad etish",  callback_data=f"reject_pro_{uid}")
    user_link = f'<a href="tg://user?id={uid}">{html.quote(call.from_user.full_name)}</a>'
    try:
        await bot.send_message(MAIN_ADMIN_ID, f"⭐️ <b>Pro so'rovi!</b>\nFoydalanuvchi: {user_link}\nID: <code>{uid}</code>", reply_markup=kb.as_markup())
        await call.message.edit_text("✅ Pro so'rovi yuborildi!", reply_markup=cancel_kb())
    except:
        await call.answer("Xatolik!", show_alert=True)
    await call.answer()

@dp.callback_query(F.data.startswith("give_pro_"))
async def give_pro_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    uid = int(call.data.split("_")[2])
    add_pro_user(uid, "Pro foydalanuvchi")
    await call.message.edit_text(f"✅ ID <code>{uid}</code> ga Pro berildi.")
    try: await bot.send_message(uid, "⭐️ Pro versiya faollashtirildi!\n\n/start bosing.")
    except: pass
    await call.answer()

@dp.callback_query(F.data.startswith("reject_pro_"))
async def reject_pro_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    uid = int(call.data.split("_")[2])
    await call.message.edit_text("❌ Pro so'rovi rad etildi.")
    try: await bot.send_message(uid, "❌ Pro so'rovingiz rad etildi.")
    except: pass
    await call.answer()

# ============================================================
#                    ADMINLAR BOSHQARUVI
# ============================================================
@dp.callback_query(F.data == "menu_admins")
async def adm_panel_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    await call.message.edit_text("👤 <b>Adminlar boshqaruvi</b>", reply_markup=admin_manage_inline())
    await call.answer()

@dp.callback_query(F.data == "adm_list")
async def adm_list_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    admins = get_all_admins()
    text = "📋 <b>Adminlar:</b>\n\n" + "\n".join([f"• {a[1]} — <code>{a[0]}</code>" for a in admins]) if admins else "Adminlar yo'q."
    kb = InlineKeyboardBuilder(); kb.button(text="🔙 Orqaga", callback_data="menu_admins")
    await call.message.edit_text(text, reply_markup=kb.as_markup())
    await call.answer()

@dp.callback_query(F.data == "adm_add")
async def adm_add_cb(call: CallbackQuery, state: FSMContext):
    if not is_founder(call.from_user.id): return
    await state.set_state(AdminManage.waiting_new_admin_id)
    await call.message.edit_text("➕ <b>Admin qo'shish</b>\n\nID yuboring:\n<i>Misol: 123456789</i>", reply_markup=cancel_kb())
    await call.answer()

@dp.message(AdminManage.waiting_new_admin_id)
async def adm_add_msg(message: Message, state: FSMContext):
    if message.text and message.text.isdigit():
        uid = int(message.text)
        add_admin_db(uid, "Qo'shilgan")
        await state.clear()
        kb = InlineKeyboardBuilder(); kb.button(text="🔙 Adminlar boshqaruviga", callback_data="menu_admins")
        await message.answer(f"✅ ID <code>{uid}</code> admin qilindi.", reply_markup=kb.as_markup())
    else:
        await message.answer("❌ Faqat raqam kiriting.", reply_markup=cancel_kb())

@dp.callback_query(F.data == "adm_remove")
async def adm_remove_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    admins = get_all_admins()
    if not admins:
        await call.answer("O'chiriladigan admin yo'q.", show_alert=True); return
    kb = InlineKeyboardBuilder()
    for a in admins:
        kb.button(text=f"❌ {a[1]} ({a[0]})", callback_data=f"del_adm_{a[0]}")
    kb.button(text="🔙 Orqaga", callback_data="menu_admins")
    kb.adjust(1)
    await call.message.edit_text("➖ <b>O'chiriladigan adminni tanlang:</b>", reply_markup=kb.as_markup())
    await call.answer()

@dp.callback_query(F.data.startswith("del_adm_"))
async def del_adm_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    remove_admin_db(int(call.data.split("_")[2]))
    kb = InlineKeyboardBuilder(); kb.button(text="🔙 Orqaga", callback_data="menu_admins")
    await call.message.edit_text("✅ Admin o'chirildi.", reply_markup=kb.as_markup())
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
    text = "⭐️ <b>Pro foydalanuvchilar:</b>\n\n" + "\n".join([f"• {a[1]} — <code>{a[0]}</code>" for a in pros]) if pros else "Pro foydalanuvchilar yo'q."
    kb = InlineKeyboardBuilder(); kb.button(text="🔙 Orqaga", callback_data="menu_pro_mgmt")
    await call.message.edit_text(text, reply_markup=kb.as_markup())
    await call.answer()

@dp.callback_query(F.data == "pro_add")
async def pro_add_cb(call: CallbackQuery, state: FSMContext):
    if not is_founder(call.from_user.id): return
    await state.set_state(ProManage.waiting_pro_user_id)
    await call.message.edit_text("➕ <b>Pro berish</b>\n\nID yuboring:\n<i>Misol: 123456789</i>", reply_markup=cancel_kb())
    await call.answer()

@dp.message(ProManage.waiting_pro_user_id)
async def pro_add_msg(message: Message, state: FSMContext):
    if message.text and message.text.isdigit():
        uid = int(message.text)
        add_pro_user(uid, "Pro foydalanuvchi")
        await state.clear()
        kb = InlineKeyboardBuilder(); kb.button(text="🔙 Pro boshqaruviga", callback_data="menu_pro_mgmt")
        await message.answer(f"✅ ID <code>{uid}</code> ga Pro berildi.", reply_markup=kb.as_markup())
    else:
        await message.answer("❌ Faqat raqam kiriting.", reply_markup=cancel_kb())

@dp.callback_query(F.data == "pro_remove")
async def pro_remove_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    pros = get_all_pro_users()
    if not pros:
        await call.answer("O'chiriladigan pro foydalanuvchi yo'q.", show_alert=True); return
    kb = InlineKeyboardBuilder()
    for a in pros:
        kb.button(text=f"❌ {a[1]} ({a[0]})", callback_data=f"delpro_{a[0]}")
    kb.button(text="🔙 Orqaga", callback_data="menu_pro_mgmt")
    kb.adjust(1)
    await call.message.edit_text("➖ <b>O'chiriladigan pro foydalanuvchini tanlang:</b>", reply_markup=kb.as_markup())
    await call.answer()

@dp.callback_query(F.data.startswith("delpro_"))
async def del_pro_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    remove_pro_user(int(call.data.split("_")[1]))
    kb = InlineKeyboardBuilder(); kb.button(text="🔙 Orqaga", callback_data="menu_pro_mgmt")
    await call.message.edit_text("✅ Pro o'chirildi.", reply_markup=kb.as_markup())
    await call.answer()

# ============================================================
#                     POST YARATISH
# ============================================================
@dp.callback_query(F.data == "menu_post")
async def post_start_cb(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    if not is_admin(uid):
        await call.answer("Buning uchun Admin bo'lishingiz kerak!", show_alert=True); return
    await state.set_state(PostState.waiting_content)
    await state.update_data(user_id=uid)
    await call.message.edit_text(
        "📝 <b>Post Yaratish — 1/3-qadam</b>\n\n"
        "Rasm, video, ovozli xabar yoki matn yuboring.\n\n"
        "<i>(Premium emojilar va barcha formatlar aslicha saqlanadi!)</i>",
        reply_markup=cancel_kb()
    )
    await call.answer()

@dp.message(PostState.waiting_content)
async def post_content(message: Message, state: FSMContext):
    if not (message.text or message.photo or message.video or message.document
            or message.audio or message.voice or message.animation):
        await message.answer("❌ Qo'llab-quvvatlanadigan format yuboring.", reply_markup=cancel_kb()); return
    await state.update_data(from_chat_id=message.chat.id, message_id=message.message_id, btns=[])
    await state.set_state(PostState.waiting_btn_name)
    await message.answer(
        "✅ Saqlandi!\n\n📝 <b>2/3-qadam — Tugma qo'shish</b>\n\n"
        "Tugma nomi yuboring yoki <b>✅ Tayyor</b> bosing:",
        reply_markup=done_or_cancel_kb()
    )

@dp.callback_query(PostState.waiting_btn_name, F.data == "btn_done")
async def btn_done_cb(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    builder = InlineKeyboardBuilder()
    for b in data.get('btns', []):
        try: builder.row(types.InlineKeyboardButton(text=b['text'], url=b['url'], style=b['style']))
        except: builder.row(types.InlineKeyboardButton(text=b['text'], url=b['url']))
    rm = builder.as_markup() if data.get('btns') else None
    await call.message.answer("👀 <b>Post ko'rinishi:</b>")
    try:
        await bot.copy_message(chat_id=call.message.chat.id, from_chat_id=data['from_chat_id'],
                               message_id=data['message_id'], reply_markup=rm)
    except Exception as e:
        await call.message.answer(f"❌ Xatolik: {e}"); return
    await state.set_state(PostState.choose_target)
    await call.message.answer("📤 <b>3/3-qadam — Qayerga yuboramiz?</b>", reply_markup=target_inline(data.get('user_id')))
    await call.answer()

@dp.message(PostState.waiting_btn_name)
async def btn_name_msg(message: Message, state: FSMContext):
    await state.update_data(t_n=message.text)
    await state.set_state(PostState.waiting_btn_url)
    await message.answer(f"🔗 <b>«{message.text}»</b> uchun link:", reply_markup=cancel_kb())

@dp.message(PostState.waiting_btn_url)
async def btn_url_msg(message: Message, state: FSMContext):
    url = message.text.strip()
    if not url.startswith("http"):
        await message.answer("❌ Link «http» bilan boshlanishi kerak.", reply_markup=cancel_kb()); return
    await state.update_data(t_u=url)
    await state.set_state(PostState.waiting_btn_color)
    await message.answer("🎨 Tugma rangini tanlang:", reply_markup=color_kb("style"))

@dp.callback_query(PostState.waiting_btn_color, F.data.startswith("style_"))
async def btn_color_cb(call: CallbackQuery, state: FSMContext):
    color = call.data.split("_")[1]
    data = await state.get_data()
    btns = data.get('btns', [])
    btns.append({'text': data['t_n'], 'url': data['t_u'], 'style': color})
    await state.update_data(btns=btns)
    await call.message.edit_text(
        f"✅ <b>{data['t_n']}</b> tugmasi qo'shildi! Jami: {len(btns)} ta\n\nYana tugma yoki <b>✅ Tayyor</b>:",
        reply_markup=done_or_cancel_kb()
    )
    await state.set_state(PostState.waiting_btn_name)
    await call.answer()

@dp.callback_query(PostState.choose_target, F.data == "target_all")
async def target_all(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not is_founder(data.get('user_id')):
        await call.answer("Ruxsat yo'q!", show_alert=True); return
    success = await _do_send(data, get_all_chats())
    await call.message.edit_text(f"✅ {success} ta chatga yuborildi.")
    await send_main_menu(call, data.get('user_id'))
    await state.clear(); await call.answer()

@dp.callback_query(PostState.choose_target, F.data == "target_self")
async def target_self(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    uid = data.get('user_id')
    await _do_send(data, [(uid,)])
    await call.message.edit_text("✅ Post o'zingizga yuborildi!")
    await send_main_menu(call, uid)
    await state.clear(); await call.answer()

@dp.callback_query(PostState.choose_target, F.data == "target_specific")
async def target_specific(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not is_founder(data.get('user_id')):
        await call.answer("Ruxsat yo'q!", show_alert=True); return
    await state.set_state(PostState.waiting_specific_chat)
    await call.message.edit_text("🎯 Chat ID yuboring:\n<i>Misol: -1001234567890</i>", reply_markup=cancel_kb())
    await call.answer()

@dp.callback_query(PostState.choose_target, F.data.startswith("target_group_"))
async def target_group(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    uid = data.get('user_id')
    chat_id = int(call.data.split("_")[2])
    if chat_id not in get_admin_groups(uid) and not is_founder(uid):
        await call.answer("❌ Bu guruh sizga tegishli emas!", show_alert=True); return
    success = await _do_send(data, [(chat_id,)])
    await call.message.edit_text(f"✅ {success} ta chatga yuborildi.")
    await send_main_menu(call, uid)
    await state.clear(); await call.answer()

@dp.message(PostState.waiting_specific_chat)
async def specific_chat_msg(message: Message, state: FSMContext):
    chat_id = None
    if message.forward_from_chat:
        chat_id = message.forward_from_chat.id
    elif message.forward_from:
        chat_id = message.forward_from.id
    elif message.text and message.text.lstrip("-").isdigit():
        chat_id = int(message.text)
    if not chat_id:
        await message.answer("❌ Noto'g'ri ID.", reply_markup=cancel_kb()); return
    data = await state.get_data()
    success = await _do_send(data, [(chat_id,)])
    kb = InlineKeyboardBuilder(); kb.button(text="🏠 Bosh menyu", callback_data="menu_back")
    await message.answer(f"✅ {success} ta chatga yuborildi.", reply_markup=kb.as_markup())
    await state.clear()

# ============================================================
#                   MAJBURIY OBUNA
# ============================================================
@dp.callback_query(F.data == "menu_sub")
async def sub_start_cb(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    await state.set_state(SubState.waiting_group)
    await call.message.edit_text(
        "🔒 <b>Majburiy Obuna Sozlash</b>\n\nGuruh @username yuboring:\n<i>Misol: @guruhingiz</i>",
        reply_markup=cancel_kb()
    )
    await call.answer()

@dp.message(SubState.waiting_group)
async def sub_group_msg(message: Message, state: FSMContext):
    await state.update_data(gr=message.text)
    await state.set_state(SubState.waiting_channels)
    kb = InlineKeyboardBuilder()
    kb.button(text="🗑 Obunani o'chirish", callback_data="sub_remove")
    kb.button(text="🔙 Bekor qilish",      callback_data="cancel")
    kb.adjust(1)
    await message.answer(
        "📢 <b>Majburiy kanallarni yuboring:</b>\n\n<i>Misol: @kanal1, @kanal2</i>",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(SubState.waiting_channels, F.data == "sub_remove")
async def sub_remove_cb(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    delete_group_channels(data['gr'])
    await call.message.edit_text("✅ Majburiy obuna o'chirildi!")
    await send_main_menu(call, call.from_user.id)
    await state.clear(); await call.answer()

@dp.message(SubState.waiting_channels)
async def sub_channels_msg(message: Message, state: FSMContext):
    await state.update_data(channels=message.text.replace(" ", ""))
    await state.set_state(SubState.waiting_sub_color)
    await message.answer("🎨 Obuna tugmalarining rangini tanlang:", reply_markup=color_kb("subcolor"))

@dp.callback_query(SubState.waiting_sub_color, F.data.startswith("subcolor_"))
async def sub_color_cb(call: CallbackQuery, state: FSMContext):
    color = call.data.split("_")[1]
    data = await state.get_data()
    save_group_channels(data['gr'], data['channels'], sub_style=color, owner_id=call.from_user.id)
    await call.message.edit_text(f"✅ Saqlandi!\n\nGuruh: <b>{data['gr']}</b>\nTugma rangi: <b>{color}</b>")
    await send_main_menu(call, call.from_user.id)
    await state.clear(); await call.answer()

# ============================================================
#                  SO'Z FILTRI (MODERATOR)
# ============================================================
@dp.callback_query(F.data == "menu_mod")
async def mod_menu_cb(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Ruxsat yo'q!", show_alert=True); return
    await call.message.edit_text("🛡 <b>So'z Filtri</b>\n\nGuruhlarni tanlang:", reply_markup=mod_main_kb())
    await call.answer()

@dp.callback_query(F.data == "mod_list_groups")
async def mod_list_groups_cb(call: CallbackQuery):
    groups = await mod_get_user_groups(call.from_user.id, bot)
    if not groups:
        kb = InlineKeyboardBuilder(); kb.button(text="🔙 Orqaga", callback_data="menu_mod")
        await call.message.edit_text("⚠️ Siz admin bo'lgan guruhlar topilmadi.", reply_markup=kb.as_markup())
        await call.answer(); return
    kb = InlineKeyboardBuilder()
    for g_id, g_name in groups:
        kb.button(text=f"👥 {g_name}", callback_data=f"mod_manage_{g_id}")
    kb.button(text="🔙 Orqaga", callback_data="menu_mod")
    kb.adjust(1)
    await call.message.edit_text("📂 <b>Guruhni tanlang:</b>", reply_markup=kb.as_markup())
    await call.answer()

@dp.callback_query(F.data.startswith("mod_manage_"))
async def mod_manage_group_cb(call: CallbackQuery):
    g_id = call.data.split("_")[2]
    group = mod_get_group(g_id)
    words_count = len(group[1].split(",")) if (group and group[1]) else 0
    g_name = group[0] if group else f"Guruh {g_id}"
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Yangi so'zlar qo'shish",          callback_data=f"mod_add_{g_id}")
    kb.button(text=f"🗑 Filtrni boshqarish ({words_count})", callback_data=f"mod_view_{g_id}")
    kb.button(text="🔙 Orqaga",                            callback_data="mod_list_groups")
    kb.adjust(1)
    await call.message.edit_text(f"⚙️ <b>Guruh:</b> {g_name}\n\nAmalni tanlang:", reply_markup=kb.as_markup())
    await call.answer()

async def _show_mod_words(call, g_id: str, answer_text: str = None):
    words_str, _, strict_str = mod_get_words(g_id)
    if answer_text:
        await call.answer(answer_text)
    
    normal_words = [w.strip() for w in words_str.split(",") if w.strip()] if words_str else []
    strict_words = [w.strip() for w in strict_str.split(",") if w.strip()] if strict_str else []
    
    if not normal_words and not strict_words:
        if not answer_text:
            await call.answer("Ro'yxat bo'sh!", show_alert=True)
        kb = InlineKeyboardBuilder()
        kb.button(text="🔙 Orqaga", callback_data=f"mod_manage_{g_id}")
        try:
            await call.message.edit_text("📋 Taqiqlangan so'zlar ro'yxati bo'sh.", reply_markup=kb.as_markup())
        except: pass
        return
    
    kb = InlineKeyboardBuilder()
    if normal_words:
        for word in normal_words:
            kb.button(text=f"❌ {word}", callback_data=f"mod_del_{g_id}_n_{word}")
        kb.adjust(2)
    if strict_words:
        for word in strict_words:
            kb.button(text=f"🔒 {word}", callback_data=f"mod_del_{g_id}_s_{word}")
        kb.adjust(2)
    kb.button(text="🔙 Orqaga", callback_data=f"mod_manage_{g_id}")
    
    text = "🗑 <b>O'chirish uchun so'z ustiga bosing:</b>\n\n"
    if normal_words:
        text += "❌ — Faqat oddiy userlarga\n"
    if strict_words:
        text += "🔒 — Adminlarga ham ishlaydi"
    try:
        await call.message.edit_text(text, reply_markup=kb.as_markup())
    except: pass

@dp.callback_query(F.data.startswith("mod_view_"))
async def mod_view_words_cb(call: CallbackQuery):
    g_id = call.data.split("_")[2]
    await _show_mod_words(call, g_id)

@dp.callback_query(F.data.startswith("mod_del_"))
async def mod_delete_word_cb(call: CallbackQuery):
    parts = call.data.split("_")
    # "mod_del_{g_id}_n_{word}" yoki "mod_del_{g_id}_s_{word}"
    g_id = parts[2]
    word_type = parts[3]  # 'n' = normal, 's' = strict
    word_to_del = "_".join(parts[4:])
    words_str, _, strict_str = mod_get_words(g_id)
    if word_type == "s":
        if strict_str:
            new_list = [w.strip() for w in strict_str.split(",") if w.strip() != word_to_del]
            mod_set_strict_words(g_id, ",".join(new_list) if new_list else "")
    else:
        if words_str:
            new_list = [w.strip() for w in words_str.split(",") if w.strip() != word_to_del]
            mod_set_words(g_id, ",".join(new_list) if new_list else "")
    await _show_mod_words(call, g_id, answer_text=f"'{word_to_del}' o'chirildi")

@dp.callback_query(F.data.startswith("mod_add_"))
async def mod_add_words_cb(call: CallbackQuery, state: FSMContext):
    g_id = call.data.split("_")[2]
    await state.update_data(active_gid=g_id)
    await call.message.edit_text(
        "🚫 <b>Taqiqlangan so'zlarni yuboring</b>\n\n<i>Vergul bilan ajrating: yomon, haqorat, spam</i>",
        reply_markup=cancel_kb()
    )
    await state.set_state(ModState.waiting_for_words)
    await call.answer()

@dp.message(ModState.waiting_for_words)
async def mod_get_words_msg(message: Message, state: FSMContext):
    await state.update_data(new_words=message.text.lower())
    kb = InlineKeyboardBuilder()
    kb.button(text="👥 Faqat oddiy userlarga", callback_data="word_type_normal")
    kb.button(text="🔒 Adminlarga ham ishlaydi", callback_data="word_type_strict")
    kb.button(text="🔙 Bekor qilish", callback_data="cancel")
    kb.adjust(1)
    await message.answer(
        "⚙️ <b>Bu so'zlar kimga amal qilsin?</b>\n\n"
        "👥 <b>Faqat oddiy userlarga</b> — admin va creator yoza oladi\n"
        "🔒 <b>Adminlarga ham ishlaydi</b> — hech kim yoza olmaydi",
        reply_markup=kb.as_markup()
    )
    await state.set_state(ModState.waiting_word_type)

@dp.callback_query(ModState.waiting_word_type, F.data.in_({"word_type_normal", "word_type_strict"}))
async def mod_word_type_cb(call: CallbackQuery, state: FSMContext):
    is_strict = call.data == "word_type_strict"
    await state.update_data(is_strict=is_strict)
    type_text = "🔒 Adminlarga ham ishlaydi" if is_strict else "👥 Faqat oddiy userlarga"
    await call.message.edit_text(
        f"✅ Tur: <b>{type_text}</b>\n\n"
        "📝 <b>Ogohlantirish matnini yuboring:</b>\n"
        "<i>Misol: Iltimos, bunday so'zlardan foydalanmang!</i>",
        reply_markup=cancel_kb()
    )
    await state.set_state(ModState.waiting_for_reply)
    await call.answer()

@dp.message(ModState.waiting_for_reply)
async def mod_get_reply_msg(message: Message, state: FSMContext):
    data = await state.get_data()
    g_id = data['active_gid']
    new_words = data['new_words']
    is_strict = data.get('is_strict', False)
    words_str, _, strict_str = mod_get_words(g_id)
    if is_strict:
        updated = f"{strict_str},{new_words}" if strict_str else new_words
        mod_update_words(g_id, updated, message.text, is_strict=True)
    else:
        updated = f"{words_str},{new_words}" if words_str else new_words
        mod_update_words(g_id, updated, message.text, is_strict=False)
    await state.clear()
    type_text = "🔒 Adminlarga ham" if is_strict else "👥 Faqat oddiy userlarga"
    kb = InlineKeyboardBuilder()
    kb.button(text="🔙 Guruhga qaytish", callback_data=f"mod_manage_{g_id}")
    kb.button(text="🏠 Bosh menyu", callback_data="menu_back")
    kb.adjust(1)
    await message.answer(f"✅ So'zlar saqlandi! ({type_text})", reply_markup=kb.as_markup())

# ============================================================
#              GURUH NAZORATI (ASOSIY WATCHER)
# ============================================================
async def check_sub(user_id, channels):
    nosub = []
    for c in channels.split(","):
        c = c.strip()
        if not c: continue
        try:
            m = await bot.get_chat_member(c, user_id)
            if m.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
                nosub.append(c)
        except:
            continue
    return nosub

async def warning_lifecycle(chat_id, user_id, warning_msg, channels):
    start_time = time.time()
    try:
        while True:
            await asyncio.sleep(5)
            elapsed = time.time() - start_time
            nosub = await check_sub(user_id, channels)
            if not nosub:
                try: await warning_msg.delete()
                except: pass
                break
            if elapsed >= WARNING_TIMEOUT:
                try: await warning_msg.delete()
                except: pass
                break
    except Exception as e:
        logging.warning(f"Warning lifecycle xatosi: {e}")
    finally:
        if (chat_id, user_id) in active_warnings:
            del active_warnings[(chat_id, user_id)]

@dp.message()
async def watcher(message: Message):
    # 0. FOUNDER MUTLAQ DAXLSID - Har qanday holatda ham bot unga tegmaydi
    if int(message.from_user.id) == int(MAIN_ADMIN_ID):
        return

    # Chat ma'lumotlarini saqlash
    add_known_chat(message.chat.id, message.chat.type, message.chat.title or message.from_user.full_name)

    if message.chat.type not in ['group', 'supergroup']:
        return

    # Guruh admini ekanini tekshirish
    is_group_admin = False
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status in [ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR]:
            is_group_admin = True
    except:
        pass

    # ---- 1. MAJBURIY OBUNA (Faqat oddiy userlar uchun) ----
    if not is_group_admin:
        if message.chat.username:
            chans, sub_style = get_group_channels(f"@{message.chat.username}")
            if chans:
                nosub = await check_sub(message.from_user.id, chans)
                if nosub:
                    try: await message.delete()
                    except: pass
                    if (message.chat.id, message.from_user.id) not in active_warnings:
                        kb = InlineKeyboardBuilder()
                        for c in nosub:
                            try:
                                chat_info = await bot.get_chat(c)
                                btn_t = chat_info.title
                            except: btn_t = c
                            kb.row(types.InlineKeyboardButton(text=f"📢 {btn_t}", url=f"https://t.me/{c.replace('@', '')}"))
                        
                        user_link = f'<a href="tg://user?id={message.from_user.id}">{html.quote(message.from_user.full_name)}</a>'
                        w = await message.answer(f"⚠️ {user_link}, guruhga yozish uchun kanallarga a'zo bo'ling!", reply_markup=kb.as_markup())
                        task = asyncio.create_task(warning_lifecycle(message.chat.id, message.from_user.id, w, chans))
                        active_warnings[(message.chat.id, message.from_user.id)] = task
                    return

    # ---- 2. SO'Z FILTRI (MODERATOR) ----
    if not message.text:
        return

    words_str, reply_text, strict_str = mod_get_words(message.chat.id)
    msg_lower = message.text.lower()
    warn_msg = reply_text or "⚠️ Taqiqlangan so'z ishlatildi!"

    # A) QATTIQ FILTR (Adminlarga ham ishlaydigan so'zlar)
    if strict_str:
        strict_list = [w.strip().lower() for w in strict_str.split(",") if w.strip()]
        for sw in strict_list:
            if sw in msg_lower:
                try:
                    await message.delete()
                    await message.answer(f"🚫 {message.from_user.mention_html()}, {warn_msg}")
                except: pass
                return

    # B) ODDIY FILTR (Faqat oddiy foydalanuvchilar uchun, Adminlarga mumkin)
    if words_str and not is_group_admin:
        normal_list = [w.strip().lower() for w in words_str.split(",") if w.strip()]
        for nw in normal_list:
            if nw in msg_lower:
                try:
                    await message.delete()
                    await message.answer(f"⚠️ {message.from_user.mention_html()}, {warn_msg}")
                except: pass
                return
# ============================================================
#                          MAIN
# ============================================================
async def main():
    init_db()
    print("✅ BOT ISHGA TUSHDI — Barcha funksiyalar faol!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot to'xtatildi.")