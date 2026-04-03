import asyncio
import logging
import sqlite3
import time
from aiogram import Bot, Dispatcher, F, html, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)

# ---------------- SOZLAMALAR ----------------
TOKEN = "7730812654:AAH9zPwJTDibOVIkBLFVA3qRVtUePaCMY4Q"
MAIN_ADMIN_ID = 5724592490
DB_NAME = "bot_database.db"

LIMIT_PEOPLE = 3
LIMIT_TIME = 600
WARNING_TIMEOUT = 180  # 3 daqiqa = 180 sekund

active_warnings = {}
group_stats = {}

# ---------------- BAZA ----------------
def get_db_connection():
    return sqlite3.connect(DB_NAME, timeout=30, check_same_thread=False)

def init_db():
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, full_name TEXT)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS group_settings (
                group_username TEXT PRIMARY KEY,
                channels TEXT,
                sub_style TEXT DEFAULT 'primary',
                owner_id INTEGER DEFAULT 0
            )
        """)
        try: c.execute("ALTER TABLE group_settings ADD COLUMN owner_id INTEGER DEFAULT 0"); conn.commit()
        except: pass
        try: c.execute("ALTER TABLE group_settings ADD COLUMN sub_style TEXT DEFAULT 'primary'"); conn.commit()
        except: pass
        c.execute("CREATE TABLE IF NOT EXISTS known_chats (chat_id INTEGER PRIMARY KEY, chat_type TEXT, title TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS pro_users (user_id INTEGER PRIMARY KEY, full_name TEXT)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS admin_groups (
                admin_id INTEGER,
                chat_id INTEGER,
                PRIMARY KEY (admin_id, chat_id)
            )
        """)
        conn.commit()
    except Exception as e:
        logging.error(f"Baza xatosi: {e}")
    finally:
        conn.close()

def is_founder(user_id): return int(user_id) == int(MAIN_ADMIN_ID)

def is_admin(user_id):
    if is_founder(user_id): return True
    conn = get_db_connection()
    try:
        return conn.cursor().execute("SELECT user_id FROM admins WHERE user_id=?", (int(user_id),)).fetchone() is not None
    except: return False
    finally: conn.close()

def is_pro(user_id):
    conn = get_db_connection()
    try:
        return conn.cursor().execute("SELECT user_id FROM pro_users WHERE user_id=?", (int(user_id),)).fetchone() is not None
    except: return False
    finally: conn.close()

def add_admin_db(user_id, full_name):
    conn = get_db_connection()
    try:
        conn.cursor().execute("INSERT OR REPLACE INTO admins (user_id, full_name) VALUES (?,?)", (int(user_id), str(full_name)))
        conn.commit(); return True
    except: return False
    finally: conn.close()

def remove_admin_db(user_id):
    conn = get_db_connection()
    try:
        conn.cursor().execute("DELETE FROM admins WHERE user_id=?", (int(user_id),)); conn.commit()
    except: pass
    finally: conn.close()

def get_all_admins():
    conn = get_db_connection()
    try: return conn.cursor().execute("SELECT user_id, full_name FROM admins").fetchall()
    except: return[]
    finally: conn.close()

def add_pro_user(user_id, full_name):
    conn = get_db_connection()
    try:
        conn.cursor().execute("INSERT OR REPLACE INTO pro_users (user_id, full_name) VALUES (?,?)", (int(user_id), str(full_name)))
        conn.commit(); return True
    except: return False
    finally: conn.close()

def remove_pro_user(user_id):
    conn = get_db_connection()
    try:
        conn.cursor().execute("DELETE FROM pro_users WHERE user_id=?", (int(user_id),)); conn.commit()
    except: pass
    finally: conn.close()

def get_all_pro_users():
    conn = get_db_connection()
    try: return conn.cursor().execute("SELECT user_id, full_name FROM pro_users").fetchall()
    except: return[]
    finally: conn.close()

def save_group_channels(group_username, channels, sub_style="primary", owner_id=0):
    if not group_username.startswith("@"): group_username = "@" + group_username
    conn = get_db_connection()
    try:
        conn.cursor().execute(
            "INSERT OR REPLACE INTO group_settings (group_username, channels, sub_style, owner_id) VALUES (?,?,?,?)",
            (group_username, channels, sub_style, owner_id)
        ); conn.commit()
    except: pass
    finally: conn.close()

def get_group_channels(group_username):
    conn = get_db_connection()
    try:
        res = conn.cursor().execute("SELECT channels, sub_style FROM group_settings WHERE group_username=?", (group_username,)).fetchone()
        return (res[0], res[1]) if res else (None, None)
    except: return None, None
    finally: conn.close()

def delete_group_channels(group_username):
    if not group_username.startswith("@"): group_username = "@" + group_username
    conn = get_db_connection()
    try:
        conn.cursor().execute("DELETE FROM group_settings WHERE group_username=?", (group_username,)); conn.commit()
    except: pass
    finally: conn.close()

def add_known_chat(chat_id, chat_type, title):
    conn = get_db_connection()
    try:
        conn.cursor().execute("INSERT OR IGNORE INTO known_chats (chat_id, chat_type, title) VALUES (?,?,?)", (int(chat_id), chat_type, title))
        conn.commit()
    except: pass
    finally: conn.close()

def get_all_chats():
    conn = get_db_connection()
    try: return conn.cursor().execute("SELECT chat_id FROM known_chats").fetchall()
    except: return[]
    finally: conn.close()

def register_admin_group(admin_id, chat_id):
    conn = get_db_connection()
    try:
        conn.cursor().execute("INSERT OR IGNORE INTO admin_groups (admin_id, chat_id) VALUES (?,?)", (int(admin_id), int(chat_id)))
        conn.commit()
    except: pass
    finally: conn.close()

def get_admin_groups(admin_id):
    conn = get_db_connection()
    try:
        return[r[0] for r in conn.cursor().execute("SELECT chat_id FROM admin_groups WHERE admin_id=?", (int(admin_id),)).fetchall()]
    except: return[]
    finally: conn.close()

def get_admin_groups_info(admin_id):
    conn = get_db_connection()
    try:
        return conn.cursor().execute("""
            SELECT ag.chat_id, kc.title FROM admin_groups ag
            LEFT JOIN known_chats kc ON ag.chat_id = kc.chat_id
            WHERE ag.admin_id=?
        """, (int(admin_id),)).fetchall()
    except: return[]
    finally: conn.close()

# ---------------- FSM ----------------
class SubState(StatesGroup):
    waiting_group = State()
    waiting_channels = State()
    waiting_sub_color = State()

class PostState(StatesGroup):
    waiting_content = State()
    waiting_btn_name = State()
    waiting_btn_url = State()
    waiting_btn_color = State()
    choose_target = State()
    waiting_specific_chat = State()

class AdminManage(StatesGroup):
    waiting_new_admin_id = State()

class ProManage(StatesGroup):
    waiting_pro_user_id = State()

# ---------------- INLINE KLAVIATURALAR ----------------
def main_menu_inline(user_id):
    kb = InlineKeyboardBuilder()
    if is_admin(user_id):
        kb.button(text="📝 Post Yaratish", callback_data="menu_post")
        kb.button(text="🔒 Majburiy Obuna", callback_data="menu_sub")
    if is_founder(user_id):
        kb.button(text="👤 Adminlar boshqaruvi", callback_data="menu_admins")
        kb.button(text="⭐️ Pro boshqaruvi",      callback_data="menu_pro_mgmt")
    if not is_admin(user_id):
        kb.button(text="⭐️ Pro Versiya",    callback_data="menu_pro")
        kb.button(text="🔑 Admin so'rash",  callback_data="menu_req_admin")
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
    kb.button(text="➕ Admin qo'shish",   callback_data="adm_add")
    kb.button(text="➖ Admin o'chirish",  callback_data="adm_remove")
    kb.button(text="📋 Adminlar ro'yxati", callback_data="adm_list")
    kb.button(text="🔙 Orqaga",           callback_data="menu_back")
    kb.adjust(2)
    return kb.as_markup()

def pro_manage_inline():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Pro berish",       callback_data="pro_add")
    kb.button(text="➖ Pro o'chirish",    callback_data="pro_remove")
    kb.button(text="📋 Pro ro'yxati",     callback_data="pro_list")
    kb.button(text="🔙 Orqaga",           callback_data="menu_back")
    kb.adjust(2)
    return kb.as_markup()

def target_inline(user_id):
    kb = InlineKeyboardBuilder()
    if is_founder(user_id):
        kb.button(text="🌐 Barchaga yuborish",       callback_data="target_all")
        kb.button(text="🎯 Maxsus chatga yuborish",  callback_data="target_specific")
        
    # Founder va oddiy adminlar uchun guruhlarini o'z ro'yxatida chiqarish
    groups = get_admin_groups_info(user_id)
    for g in groups:
        title = g[1] or f"Chat {g[0]}"
        kb.button(text=f" {title}", callback_data=f"target_group_{g[0]}")
        
    kb.button(text="📤 O'zimga yuborish",        callback_data="target_self")
    kb.button(text="🔙 Bekor qilish", callback_data="cancel")
    kb.adjust(1)
    return kb.as_markup()

# ---------------- BOT SETUP ----------------
dp = Dispatcher()
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

# ---------------- YORDAMCHI FUNKSIYALAR ----------------
async def send_main_menu(target, user_id, text=None):
    if is_founder(user_id):   role = "👑 Founder"
    elif is_admin(user_id):   role = "👤 Admin"
    elif is_pro(user_id):     role = "⭐️ Pro foydalanuvchi"
    else:                     role = "👥 Foydalanuvchi"
    msg = text or f"🤖 <b>Bosh menyu</b>\n\nRol: {role}\n\nAmalni tanlang:"
    if isinstance(target, Message):
        await target.answer(msg, reply_markup=main_menu_inline(user_id))
    elif isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(msg, reply_markup=main_menu_inline(user_id))
        except:
            await target.message.answer(msg, reply_markup=main_menu_inline(user_id))

async def _do_send(data, targets):
    builder = InlineKeyboardBuilder()
    for b in data.get('btns',[]):
        try:
            builder.row(types.InlineKeyboardButton(text=b['text'], url=b['url'], style=b['style']))
        except TypeError:
            builder.row(types.InlineKeyboardButton(text=b['text'], url=b['url']))
            
    rm = builder.as_markup() if data.get('btns') else None
    
    success = 0
    from_chat_id = data.get('from_chat_id')
    message_id = data.get('message_id')
    
    for t in targets:
        try:
            # COPY MESSAGE premium emojilar va formatlarni aslicha tashlab beradi!
            await bot.copy_message(
                chat_id=t[0], 
                from_chat_id=from_chat_id, 
                message_id=message_id, 
                reply_markup=rm
            )
            success += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logging.warning(f"Yuborish xatosi {t[0]}: {e}")
    return success

# ---------------- JOIN REQUEST ----------------
@dp.chat_join_request()
async def handle_join_request(update: types.ChatJoinRequest):
    chat_id = update.chat.id
    current_time = time.time()
    if chat_id not in group_stats:
        group_stats[chat_id] = {'start_time': current_time, 'count': 0}
    stats = group_stats[chat_id]
    if current_time - stats['start_time'] > LIMIT_TIME:
        stats['start_time'] = current_time; stats['count'] = 0
    if stats['count'] < LIMIT_PEOPLE:
        try:
            await update.approve(); stats['count'] += 1
        except Exception as e:
            logging.error(f"Approve xatosi: {e}")

# ---------------- SERVIS XABARLARNI O'CHIRISH ----------------
@dp.message(F.new_chat_members | F.left_chat_member)
async def delete_service_messages(message: types.Message):
    try: await message.delete()
    except: pass

@dp.my_chat_member()
async def bot_added_to_group(update: types.ChatMemberUpdated):
    new = update.new_chat_member
    me = await bot.get_me()
    if new.user.id == me.id and new.status in[ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR]:
        chat = update.chat
        add_known_chat(chat.id, chat.type, chat.title or "")
        adder_id = update.from_user.id
        if is_admin(adder_id):
            register_admin_group(adder_id, chat.id)

# ---------------- START ----------------
@dp.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    add_known_chat(message.chat.id, message.chat.type, message.chat.title or message.from_user.full_name)
    if message.chat.type != 'private': return
    await message.answer("👋", reply_markup=ReplyKeyboardRemove())
    await send_main_menu(message, message.from_user.id)

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

# ---------------- QOLGAN ESKI FUNKSIYALAR ----------------
@dp.callback_query(F.data == "menu_req_admin")
async def req_admin_cb(call: CallbackQuery):
    uid = call.from_user.id
    if is_admin(uid):
        await call.answer("Siz allaqachon adminsiz!", show_alert=True); return
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Tasdiqlash", callback_data=f"approve_{uid}")
    kb.button(text="❌ Rad etish",  callback_data=f"reject_{uid}")
    try:
        user_link = f'<a href="tg://user?id={uid}">{html.quote(call.from_user.full_name)}</a>'
        await bot.send_message(MAIN_ADMIN_ID, f"🔔 <b>Admin so'rovi!</b>\nIsm: {user_link}\nID: <code>{uid}</code>", reply_markup=kb.as_markup())
        await call.message.edit_text("✅ So'rov yuborildi! Javob kuting.", reply_markup=cancel_kb())
    except: await call.answer("Xatolik!", show_alert=True)
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
    kb.button(text="🔙 Orqaga", callback_data="menu_back")
    kb.adjust(1)
    await call.message.edit_text("⭐️ <b>Pro Versiya</b>\n\nQo'shimcha imkoniyatlar olish uchun tugmani bosing:", reply_markup=kb.as_markup())
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
        await call.message.edit_text("✅ Pro so'rovi yuborildi! Javob kuting.", reply_markup=cancel_kb())
    except: await call.answer("Xatolik!", show_alert=True)
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
    await call.message.edit_text("➕ <b>Admin qo'shish</b>\n\nFoydalanuvchi ID sini yuboring:\n<i>Misol: 123456789</i>", reply_markup=cancel_kb())
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
        kb.button(text=f"❌ {a[1]} ({a[0]})", callback_data=f"del_{a[0]}")
    kb.button(text="🔙 Orqaga", callback_data="menu_admins")
    kb.adjust(1)
    await call.message.edit_text("➖ <b>O'chiriladigan adminni tanlang:</b>", reply_markup=kb.as_markup())
    await call.answer()

@dp.callback_query(F.data.startswith("del_"))
async def del_adm_cb(call: CallbackQuery):
    if not is_founder(call.from_user.id): return
    remove_admin_db(int(call.data.split("_")[1]))
    kb = InlineKeyboardBuilder(); kb.button(text="🔙 Orqaga", callback_data="menu_admins")
    await call.message.edit_text("✅ Admin o'chirildi.", reply_markup=kb.as_markup())
    await call.answer()

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
    await call.message.edit_text("➕ <b>Pro berish</b>\n\nFoydalanuvchi ID sini yuboring:\n<i>Misol: 123456789</i>", reply_markup=cancel_kb())
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

# ---------------- POST YARATISH ----------------
@dp.callback_query(F.data == "menu_post")
async def post_start_cb(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    if not is_admin(uid):
        await call.answer("Buning uchun siz Admin bo'lishingiz kerak!", show_alert=True)
        return

    await state.set_state(PostState.waiting_content)
    await state.update_data(user_id=uid)
    await call.message.edit_text(
        "📝 <b>Post Yaratish — 1/3-qadam</b>\n\n"
        "Qanday xabar yubormoqchisiz?\n"
        "Rasm, video, ovozli xabar yoki matn yuboring.\n\n"
        "<i>(Premium emojilar, shriftlar va barcha narsalar aslicha ko'rinishda saqlanadi!)</i>",
        reply_markup=cancel_kb()
    )
    await call.answer()

@dp.message(PostState.waiting_content)
async def post_content(message: Message, state: FSMContext):
    if not (message.text or message.photo or message.video or message.document or message.audio or message.voice or message.animation):
        await message.answer("❌ Iltimos, faqat qo'llab-quvvatlanadigan formatdagi xabar yuboring (rasm, video, matn, fayl, ovoz).", reply_markup=cancel_kb())
        return

    # Asl yuborilgan xabar qayerdan olinganini yozib qolamiz
    await state.update_data(
        from_chat_id=message.chat.id,
        message_id=message.message_id,
        btns=[]
    )

    await state.set_state(PostState.waiting_btn_name)
    await message.answer(
        "✅ Saqlandi!\n\n"
        "📝 <b>2/3-qadam — Tugma qo'shish</b>\n\n"
        "Tugmaga nima deb yozamiz? Nomini yuboring.\n"
        "<i>Masalan: Kanalga o'tish, Sotib olish 🛒</i>\n\n"
        "A'lo, agar tugma umuman kerak bo'lmasa pastdagi <b>✅ Tayyor</b> tugmasini bosing.",
        reply_markup=done_or_cancel_kb()
    )

@dp.callback_query(PostState.waiting_btn_name, F.data == "btn_done")
async def btn_done_cb(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    builder = InlineKeyboardBuilder()
    for b in data.get('btns',[]):
        try: builder.row(types.InlineKeyboardButton(text=b['text'], url=b['url'], style=b['style']))
        except: builder.row(types.InlineKeyboardButton(text=b['text'], url=b['url']))
    rm = builder.as_markup() if data.get('btns') else None

    await call.message.answer("👀 <b>Post ko'rinishi:</b>")
    try:
        # copy_message - premium emoji, shrift va hokazolarni 100% ishlashini ta'minlaydi
        await bot.copy_message(
            chat_id=call.message.chat.id,
            from_chat_id=data['from_chat_id'],
            message_id=data['message_id'],
            reply_markup=rm
        )
    except Exception as e:
        await call.message.answer(f"❌ Xatolik yuz berdi: {e}")
        return

    await state.set_state(PostState.choose_target)
    uid = data.get('user_id')
    await call.message.answer("📤 <b>3/3-qadam — Qayerga yuboramiz?</b>", reply_markup=target_inline(uid))
    await call.answer()

@dp.message(PostState.waiting_btn_name)
async def btn_name_msg(message: Message, state: FSMContext):
    await state.update_data(t_n=message.text)
    await state.set_state(PostState.waiting_btn_url)
    await message.answer(
        f"🔗 <b>«{message.text}»</b> tugmasi bosilganda qayerga olib borsin?\n\n"
        "Link (silka) yuboring:\n"
        "<i>Masalan: https://t.me/kanalingiz</i>",
        reply_markup=cancel_kb()
    )

@dp.message(PostState.waiting_btn_url)
async def btn_url_msg(message: Message, state: FSMContext):
    url = message.text.strip()
    if not url.startswith("http"):
        await message.answer("❌ Noto'g'ri link!\nLink «http» bilan boshlanishi kerak.", reply_markup=cancel_kb()); return
    await state.update_data(t_u=url)
    await state.set_state(PostState.waiting_btn_color)
    await message.answer("🎨 Tugma rangini tanlang:", reply_markup=color_kb("style"))

@dp.callback_query(PostState.waiting_btn_color, F.data.startswith("style_"))
async def btn_color_cb(call: CallbackQuery, state: FSMContext):
    color = call.data.split("_")[1]
    data = await state.get_data()
    btns = data.get('btns',[])
    btns.append({'text': data['t_n'], 'url': data['t_u'], 'style': color})
    await state.update_data(btns=btns)
    
    await call.message.edit_text(
        f"✅ <b>{data['t_n']}</b> tugmasi qo'shildi!\n"
        f"Jami: {len(btns)} ta tugma\n\n"
        f"Yana tugma qo'shasizmi? Unda yangi tugma nomini yozib yuboring.\n"
        f"Bo'lmasa, pastdagi <b>✅ Tayyor</b> tugmasini bosing:",
        reply_markup=done_or_cancel_kb()
    )
    await state.set_state(PostState.waiting_btn_name)
    await call.answer()

@dp.callback_query(PostState.choose_target, F.data == "target_all")
async def target_all(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not is_founder(data.get('user_id')):
        await call.answer("Ruxsat yo'q!", show_alert=True); return
    targets = get_all_chats()
    success = await _do_send(data, targets)
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
    await call.message.edit_text("🎯 Qaysi chatga yuboramiz? Uning ID sini yuboring:\n<i>Misol: -1001234567890</i>", reply_markup=cancel_kb())
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
    chat_id = message.forward_from_chat.id if message.forward_from_chat else (
        message.forward_from.id if message.forward_from else None
    )
    if not chat_id and message.text and message.text.lstrip("-").isdigit():
        chat_id = int(message.text)
    if not chat_id:
        await message.answer("❌ Noto'g'ri ID. Qaytadan kiriting.", reply_markup=cancel_kb()); return
    data = await state.get_data()
    success = await _do_send(data, [(chat_id,)])
    kb = InlineKeyboardBuilder(); kb.button(text="🏠 Bosh menyu", callback_data="menu_back")
    await message.answer(f"✅ {success} ta chatga yuborildi.", reply_markup=kb.as_markup())
    await state.clear()

# ---------------- MAJBURIY OBUNA ----------------
@dp.callback_query(F.data == "menu_sub")
async def sub_start_cb(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    await state.set_state(SubState.waiting_group)
    await call.message.edit_text(
        "🔒 <b>Majburiy Obuna Sozlash</b>\n\n"
        "Guruh @username ni yuboring:\n\n"
        "<i>Misol: @guruhingiz_username</i>",
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
        " <b>Majburiy kanallarni yuboring:</b>\n\n"
        "<i>Misol: @kanal1, @kanal2\n(Vergul bilan ajrating)</i>",
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

# ---------------- GURUH NAZORATI ----------------
async def check_sub(user_id, channels):
    nosub =[]
    for c in channels.split(","):
        c = c.strip()
        if not c: continue
        try:
            m = await bot.get_chat_member(c, user_id)
            if m.status in[ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
                nosub.append(c)
        except: continue
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

            if elapsed >= WARNING_TIMEOUT: # 3 DAQIQA 
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
    add_known_chat(message.chat.id, message.chat.type, message.chat.title or message.from_user.full_name)
    if message.chat.type in ['group', 'supergroup']:
        
        # Super qulaylik: Agar botda yozayotgan odam Admin bo'lsa, guruhni unga biriktirib qo'yadi.
        if is_admin(message.from_user.id):
            register_admin_group(message.from_user.id, message.chat.id)
            
        if not message.chat.username: return
        chans, sub_style = get_group_channels(f"@{message.chat.username}")
        if not chans: return
        try:
            m = await bot.get_chat_member(message.chat.id, message.from_user.id)
            if m.status in[ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR]: return
        except: pass
        nosub = await check_sub(message.from_user.id, chans)
        if nosub:
            try: await message.delete()
            except: pass
            
            if (message.chat.id, message.from_user.id) not in active_warnings:
                kb = InlineKeyboardBuilder()
                for c in nosub:
                    try:
                        chat_info = await bot.get_chat(c)
                        btn_text = chat_info.title
                    except: btn_text = c
                    
                    try: kb.row(types.InlineKeyboardButton(text=f" {btn_text}", url=f"https://t.me/{c.replace('@','')}", style=sub_style or "primary"))
                    except: kb.row(types.InlineKeyboardButton(text=f" {btn_text}", url=f"https://t.me/{c.replace('@','')}"))
                        
                user_link = f'<a href="tg://user?id={message.from_user.id}">{html.quote(message.from_user.full_name)}</a>'
                w = await message.answer(
                    f"⚠️ {user_link}, guruhga yozish uchun avval quyidagi kanal va guruhga obuna bo'ling!",
                    reply_markup=kb.as_markup()
                )
                task = asyncio.create_task(
                    warning_lifecycle(message.chat.id, message.from_user.id, w, chans)
                )
                active_warnings[(message.chat.id, message.from_user.id)] = task

# ---------------- MAIN ----------------
async def main():
    init_db()
    print("BOT ISHGA TUSHDI...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try: asyncio.run(main())
    except: print("Bot to'xtatildi.")
