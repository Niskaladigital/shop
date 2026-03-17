import logging
import math
import os
import uuid
import asyncio
import time 
import requests
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# --- KONFIGURASI BOT ---
TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID', '8415452669'))

if not TOKEN:
    raise ValueError('BOT_TOKEN belum di-set di environment variables.')

# --- KONFIGURASI GAMBAR DARI GOOGLE DRIVE ---
BANNER_URL = 'https://drive.google.com/file/d/1HXqgLdk74-EIYPEVgd6jg4T3bnnttTFJ/view?usp=drive_link' 
QRIS_URL = 'https://drive.google.com/file/d/1iUYOnYMLiU1AF41N1gWmCdhqi1a3YpCp/view?usp=drive_link'

# --- KONFIGURASI DATABASE GOOGLE SHEETS ---
WEBAPP_URL = 'https://script.google.com/macros/s/AKfycbx1rThAIqRT0rh-o-qzu85N5X6hxcx_u24YV6aD1gxnF0GWMCKoHea7GExadVgC7uEC-g/exec'

PRODUCTS_PER_PAGE = 15
ADMIN_PRODUCTS_PER_PAGE = 8

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- DATABASE SEMENTARA ---
USERS = {}
TRANSAKSI = {}
PRODUK = {}
LAST_CATALOG_SYNC = 0

def get_direct_gdrive_link(url):
    if "drive.google.com/file/d/" in url:
        try:
            file_id = url.split("/file/d/")[1].split("/")[0]
            return f"https://drive.google.com/uc?export=download&id={file_id}"
        except:
            pass
    return url

BANNER_URL_DIRECT = get_direct_gdrive_link(BANNER_URL)
QRIS_URL_DIRECT = get_direct_gdrive_link(QRIS_URL)

# --- FUNGSI SINKRONISASI DATABASE ---
def load_data_from_db():
    global USERS, TRANSAKSI, PRODUK, LAST_CATALOG_SYNC
    try:
        logger.info("Mencoba sinkronisasi dengan Google Sheets...")
        resp = requests.post(WEBAPP_URL, json={"action": "get_all_data"}, timeout=15).json()
        if resp.get("status") == "success":
            data = resp["data"]
            USERS = {int(k): v for k, v in data.get("users", {}).items()}
            TRANSAKSI = data.get("transaksi", {})
            PRODUK = data.get("katalog", {})
            LAST_CATALOG_SYNC = time.time()
            logger.info("✅ Data sukses tersinkronisasi dari Google Sheets!")
    except Exception as e:
        logger.error(f"❌ Gagal load data dari DB: {e}")

async def send_to_db(action, data):
    def _post():
        try:
            requests.post(WEBAPP_URL, json={"action": action, "data": data}, timeout=15)
        except Exception as e:
            logger.error(f"Gagal push ke DB ({action}): {e}")
    asyncio.create_task(asyncio.to_thread(_post))

async def sync_user_data(user_id):
    try:
        resp = await asyncio.to_thread(requests.post, WEBAPP_URL, json={"action": "get_user", "data": {"user_id": user_id}}, timeout=10)
        res_data = resp.json()
        if res_data.get("status") == "success" and res_data.get("data"):
            if user_id in USERS:
                USERS[user_id].update(res_data["data"])
                USERS[user_id]['riwayat'] = res_data["data"].get("riwayat_json", [])
            else:
                USERS[user_id] = res_data["data"]
                USERS[user_id]['riwayat'] = res_data["data"].get("riwayat_json", [])
    except Exception as e:
        logger.error(f"Gagal realtime sync user: {e}")

async def sync_katalog_realtime():
    global PRODUK, LAST_CATALOG_SYNC
    if time.time() - LAST_CATALOG_SYNC > 15:
        try:
            resp = await asyncio.to_thread(requests.post, WEBAPP_URL, json={"action": "get_all_data"}, timeout=15)
            res_data = resp.json()
            if res_data.get("status") == "success":
                PRODUK = res_data["data"].get("katalog", PRODUK)
                LAST_CATALOG_SYNC = time.time()
        except Exception:
            pass

# --- FUNGSI BANTUAN ---
def format_rupiah(angka):
    return f"Rp {angka:,.0f}".replace(',', '.')

def get_timestamp():
    return datetime.now().strftime("%d-%m-%Y %H:%M:%S")

def get_main_menu(user_id=None):
    keyboard = [
        [KeyboardButton("🛒 Katalog Produk")],
        [KeyboardButton("👤 Profil & Akun"), KeyboardButton("💳 Deposit Saldo")],
        [KeyboardButton("📜 Riwayat Beli"), KeyboardButton("📞 Hubungi CS")]
    ]
    if user_id == ADMIN_ID:
        keyboard.append([KeyboardButton("🛠 Panel Admin")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def ensure_user(user):
    if user.id not in USERS:
        USERS[user.id] = {"nama": user.first_name, "saldo": 0, "total_beli": 0, "riwayat": []}
        await send_to_db("add_user", {"user_id": user.id, "nama": user.first_name})

def has_banner():
    return BANNER_URL_DIRECT.startswith('http') or os.path.exists(BANNER_URL_DIRECT)

async def send_banner_message(target_message, text, reply_markup=None, parse_mode='Markdown'):
    if has_banner():
        if BANNER_URL_DIRECT.startswith('http'):
            return await target_message.reply_photo(photo=BANNER_URL_DIRECT, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            with open(BANNER_URL_DIRECT, 'rb') as banner:
                return await target_message.reply_photo(photo=banner, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    return await target_message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)

async def send_banner_to_chat(bot, chat_id, text, reply_markup=None, parse_mode='Markdown'):
    if has_banner():
        if BANNER_URL_DIRECT.startswith('http'):
            return await bot.send_photo(chat_id=chat_id, photo=BANNER_URL_DIRECT, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            with open(BANNER_URL_DIRECT, 'rb') as banner:
                return await bot.send_photo(chat_id=chat_id, photo=banner, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    return await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)

async def append_with_banner(query, context, text, reply_markup=None, parse_mode='Markdown'):
    chat_id = query.message.chat_id
    return await send_banner_to_chat(context.bot, chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode)

def get_product_ids_sorted():
    return list(PRODUK.keys())

def get_total_pages():
    total_products = len(PRODUK)
    return max(1, math.ceil(total_products / PRODUCTS_PER_PAGE))

def get_admin_total_pages():
    total_products = len(PRODUK)
    return max(1, math.ceil(total_products / ADMIN_PRODUCTS_PER_PAGE))

def render_catalog_text(page=1):
    page = max(1, min(page, get_total_pages()))
    product_ids = get_product_ids_sorted()
    start = (page - 1) * PRODUCTS_PER_PAGE
    end = start + PRODUCTS_PER_PAGE
    current_items = product_ids[start:end]

    lines = [
        "╭ - - - - - - - - - - - - - - - - - - - ╮",
        "┊  LIST PRODUK",
        f"┊  page {page} / {get_total_pages()}",
        "┊- - - - - - - - - - - - - - - - - - - -"
    ]
    for index, pid in enumerate(current_items, start=start + 1):
        lines.append(f"┊ [{index}] {PRODUK[pid]['nama']}")
    lines.append("╰ - - - - - - - - - - - - - - - - - - - ╯")
    lines.append("\nPilih produk dari tombol di bawah.")
    return "\n".join(lines)

def get_catalog_keyboard(page=1):
    page = max(1, min(page, get_total_pages()))
    product_ids = get_product_ids_sorted()
    start = (page - 1) * PRODUCTS_PER_PAGE
    end = start + PRODUCTS_PER_PAGE
    current_items = product_ids[start:end]

    keyboard = []
    row = []
    for global_index, pid in enumerate(current_items, start=start + 1):
        row.append(InlineKeyboardButton(str(global_index), callback_data=f"detail_{pid}"))
        if len(row) == 5:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"catalog_page_{page-1}"))
    nav.append(InlineKeyboardButton("🔄 Refresh", callback_data=f"catalog_page_{page}"))
    if page < get_total_pages():
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"catalog_page_{page+1}"))
    keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("🏠 Menu Utama", callback_data="go_home")])
    return InlineKeyboardMarkup(keyboard)

def render_product_detail(pid):
    product = PRODUK[pid]
    lines = [
        "╭ - - - - - - - - - - - - - - - - - - - - - ╮",
        f"┊・ Produk: {product['nama']}",
        f"┊・ Stok Terjual: {product['sold']}",
        f"┊・ Desk: {product['desc']}",
        "╰ - - - - - - - - - - - - - - - - - - - - - ╯",
        "╭ - - - - - - - - - - - - - - - - - - - - - ╮",
        "┊ Variasi, Harga & Stok:"
    ]
    for variant_name, variant_data in product['variants'].items():
        lines.append(f"┊・ {variant_name}: {format_rupiah(variant_data['price'])} - Stok: {variant_data['stock']}")
    lines.append("╰ - - - - - - - - - - - - - - - - - - - - - ╯")
    return "\n".join(lines)

def get_variant_keyboard(pid):
    keyboard = []
    for variant_name, variant_data in PRODUK[pid]['variants'].items():
        label = f"{variant_name} | {format_rupiah(variant_data['price'])} | Stok {variant_data['stock']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"var_{pid}_{variant_name}")])

    keyboard.append([
        InlineKeyboardButton("⬅️ Kembali", callback_data="catalog_page_1"),
        InlineKeyboardButton("🔄 Refresh", callback_data=f"detail_{pid}")
    ])
    return InlineKeyboardMarkup(keyboard)

def render_admin_product_list(page=1):
    page = max(1, min(page, get_admin_total_pages()))
    product_ids = get_product_ids_sorted()
    start = (page - 1) * ADMIN_PRODUCTS_PER_PAGE
    end = start + ADMIN_PRODUCTS_PER_PAGE
    current_items = product_ids[start:end]
    lines = [
        "📦 *KELOLA PRODUK PROFESIONAL*",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"Halaman: *{page}/{get_admin_total_pages()}*",
        ""
    ]
    for pid in current_items:
        product = PRODUK[pid]
        total_variant = len(product["variants"])
        total_stock = sum(v["stock"] for v in product["variants"].values())
        lines.append(f"• `{pid}` — *{product['nama']}*")
        lines.append(f"  Variasi: {total_variant} | Stok Total: {total_stock} | Sold: {product['sold']}")
    lines.append("")
    lines.append("Pilih produk dari tombol di bawah untuk kelola detail, varian, harga, stok, atau hapus produk.")
    return "\n".join(lines)

def get_admin_product_list_keyboard(page=1):
    page = max(1, min(page, get_admin_total_pages()))
    product_ids = get_product_ids_sorted()
    start = (page - 1) * ADMIN_PRODUCTS_PER_PAGE
    end = start + ADMIN_PRODUCTS_PER_PAGE
    current_items = product_ids[start:end]
    keyboard = []
    for pid in current_items:
        keyboard.append([InlineKeyboardButton(f"{pid.upper()} • {PRODUK[pid]['nama']}", callback_data=f"admin_product_{pid}")])
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin_manage_products_page_{page-1}"))
    nav.append(InlineKeyboardButton("🔄 Refresh", callback_data=f"admin_manage_products_page_{page}"))
    if page < get_admin_total_pages():
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_manage_products_page_{page+1}"))
    keyboard.append(nav)
    keyboard.append([
        InlineKeyboardButton("➕ Tambah Produk", callback_data="admin_add_product"),
        InlineKeyboardButton("📘 Panduan", callback_data="admin_product_guide"),
    ])
    keyboard.append([InlineKeyboardButton("⬅️ Dashboard", callback_data="admin_dashboard")])
    return InlineKeyboardMarkup(keyboard)

def render_admin_product_detail(pid):
    product = PRODUK[pid]
    lines = [
        "📦 *DETAIL PRODUK ADMIN*",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"Kode   : `{pid}`",
        f"Nama   : *{product['nama']}*",
        f"Desk   : {product['desc']}",
        f"Sold   : {product['sold']}",
        ""
    ]
    lines.append("*Daftar Variasi:*")
    for variant_name, variant_data in product["variants"].items():
        lines.append(
            f"• *{variant_name}* — {format_rupiah(variant_data['price'])} | "
            f"Stok: {variant_data['stock']}"
        )
    lines.append("")
    lines.append("Gunakan tombol di bawah untuk edit info dasar, atur variasi, atau hapus produk.")
    return "\n".join(lines)

def get_admin_product_detail_keyboard(pid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Edit Info Dasar", callback_data=f"admin_edit_basic_{pid}")],
        [InlineKeyboardButton("🧩 Kelola Variasi", callback_data=f"admin_edit_variants_{pid}")],
        [InlineKeyboardButton("🗑 Hapus Produk", callback_data=f"admin_delete_{pid}")],
        [InlineKeyboardButton("⬅️ Kembali ke Kelola Produk", callback_data="admin_manage_products")],
    ])

def get_admin_variant_keyboard(pid):
    keyboard = []
    for variant_name, variant_data in PRODUK[pid]["variants"].items():
        label = f"{variant_name} • {format_rupiah(variant_data['price'])} • Stok {variant_data['stock']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"admin_variant_detail_{pid}_{variant_name}")])
    keyboard.append([
        InlineKeyboardButton("➕ Tambah/Update Variasi", callback_data=f"admin_variant_add_{pid}"),
        InlineKeyboardButton("📘 Format Variasi", callback_data=f"admin_variant_guide_{pid}")
    ])
    keyboard.append([InlineKeyboardButton("⬅️ Detail Produk", callback_data=f"admin_product_{pid}")])
    return InlineKeyboardMarkup(keyboard)

def render_admin_variant_detail(pid, variant_name):
    variant = PRODUK[pid]["variants"][variant_name]
    return (
        "🧩 *DETAIL VARIASI* \n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Produk : *{PRODUK[pid]['nama']}*\n"
        f"Varian : *{variant_name}*\n"
        f"Harga  : `{format_rupiah(variant['price'])}`\n"
        f"Stok   : `{variant['stock']}`\n"
        f"Link   : `{variant['link_download']}`"
    )

def get_admin_variant_detail_keyboard(pid, variant_name):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Edit Varian Ini", callback_data=f"admin_variant_edit_{pid}_{variant_name}")],
        [InlineKeyboardButton("🗑 Hapus Varian Ini", callback_data=f"admin_variant_delete_{pid}_{variant_name}")],
        [InlineKeyboardButton("⬅️ Kembali ke Variasi", callback_data=f"admin_edit_variants_{pid}")],
    ])

def get_admin_dashboard_text():
    total_user = len(USERS)
    total_produk = len(PRODUK)
    total_trx = len(TRANSAKSI)
    pending = sum(1 for t in TRANSAKSI.values() if t['status'] == 'pending')
    waiting_admin = sum(1 for t in TRANSAKSI.values() if t['status'] == 'waiting_admin')
    success = sum(1 for t in TRANSAKSI.values() if t['status'] == 'success')
    rejected = sum(1 for t in TRANSAKSI.values() if t['status'] == 'rejected')
    total_saldo = sum(u['saldo'] for u in USERS.values())

    return (
        "🛠 *PANEL ADMIN*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Total User        : *{total_user}*\n"
        f"📦 Total Produk      : *{total_produk}*\n"
        f"🧾 Total Transaksi   : *{total_trx}*\n"
        f"🟡 Pending           : *{pending}*\n"
        f"⏳ Waiting Admin     : *{waiting_admin}*\n"
        f"✅ Success           : *{success}*\n"
        f"❌ Rejected          : *{rejected}*\n"
        f"💰 Total Saldo User  : *{format_rupiah(total_saldo)}*"
    )

def clear_admin_input_state(context):
    context.user_data.pop("admin_action", None)
    context.user_data.pop("admin_pid", None)
    context.user_data.pop("admin_variant", None)

def get_next_product_id():
    used_numbers = []
    for pid in PRODUK.keys():
        if pid.startswith("p") and pid[1:].isdigit():
            used_numbers.append(int(pid[1:]))
    next_num = max(used_numbers, default=0) + 1
    return f"p{next_num}"

def parse_admin_product_input(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("Format produk minimal 2 baris.")
    header = [part.strip() for part in lines[0].split("|")]
    if len(header) != 4:
        raise ValueError("Baris pertama wajib: kode|nama|deskripsi|sold")
    pid, nama, desc, sold = header
    if not pid:
        pid = get_next_product_id()
    if pid in PRODUK:
        raise ValueError(f"Kode produk `{pid}` sudah dipakai.")
    if not sold.isdigit():
        raise ValueError("Nilai sold harus angka bulat.")

    variants = {}
    for row in lines[1:]:
        parts = [part.strip() for part in row.split("|")]
        if len(parts) != 4:
            raise ValueError("Setiap varian wajib: nama_varian|harga|stok|link_download")
        v_name, price, stock, link = parts
        if not price.isdigit() or not stock.isdigit():
            raise ValueError("Harga dan stok varian harus angka.")
        variants[v_name] = {"price": int(price), "stock": int(stock), "link_download": link}

    return pid, {
        "nama": nama,
        "sold": int(sold),
        "desc": desc,
        "variants": variants
    }

def parse_basic_update_input(text, pid):
    product = PRODUK[pid]
    for raw in text.splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        key, value = [x.strip() for x in line.split("=", 1)]
        if key == "nama":
            product["nama"] = value
        elif key == "desc":
            product["desc"] = value
        elif key == "sold":
            if not value.isdigit():
                raise ValueError("sold harus angka bulat.")
            product["sold"] = int(value)
    return product

def parse_variant_upsert_input(text):
    parts = [part.strip() for part in text.split("|")]
    if len(parts) != 4:
        raise ValueError("Format varian wajib: nama_varian|harga|stok|link_download")
    name, price, stock, link = parts
    if not price.isdigit() or not stock.isdigit():
        raise ValueError("Harga dan stok varian harus angka.")
    return name, {"price": int(price), "stock": int(stock), "link_download": link}

async def show_catalog_from_message(message):
    await send_banner_message(message, render_catalog_text(1), reply_markup=get_catalog_keyboard(1), parse_mode=None)

# --- HANDLER UTAMA ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user)
    clear_admin_input_state(context)

    pesan = (
        f"Selamat datang, *{user.first_name}*! 🤖\n\n"
        "Saya adalah asisten virtual dari *Toko Digital Premium*.\n"
        "Kami menyediakan berbagai akun premium, tools AI, dan layanan digital siap pakai.\n\n"
        "🔹 *Layanan Otomatis 24/7*\n"
        "🔹 *Katalog Profesional & Rapi*\n"
        "🔹 *Aman & Terpercaya*\n\n"
        "Silakan gunakan menu di bawah layar untuk menelusuri layanan kami."
    )

    if update.message:
        await send_banner_message(update.message, pesan, reply_markup=get_main_menu(user.id))

async def handle_admin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, text: str):
    action = context.user_data.get("admin_action")
    if user_id != ADMIN_ID or not action:
        return False

    if text == "/cancel":
        clear_admin_input_state(context)
        await send_banner_message(update.message, "✅ Input admin dibatalkan.")
        return True

    try:
        if action == "add_product":
            pid, new_product = parse_admin_product_input(text)
            PRODUK[pid] = new_product
            await send_to_db("upsert_product", {
                "pid": pid, "nama": new_product["nama"], "desc": new_product["desc"], 
                "sold": new_product["sold"], "variants": new_product["variants"]
            })
            clear_admin_input_state(context)
            await send_banner_message(update.message, f"✅ Produk berhasil ditambahkan.\n\nKode: `{pid}`\nNama: *{new_product['nama']}*", reply_markup=get_admin_product_detail_keyboard(pid), parse_mode='Markdown')
            return True

        if action == "edit_basic":
            pid = context.user_data["admin_pid"]
            parse_basic_update_input(text, pid)
            await send_to_db("upsert_product", {
                "pid": pid, "nama": PRODUK[pid]["nama"], "desc": PRODUK[pid]["desc"], 
                "sold": PRODUK[pid]["sold"], "variants": PRODUK[pid]["variants"]
            })
            clear_admin_input_state(context)
            await send_banner_message(update.message, "✅ Info dasar produk berhasil diperbarui.", reply_markup=get_admin_product_detail_keyboard(pid), parse_mode='Markdown')
            await send_banner_message(update.message, render_admin_product_detail(pid), reply_markup=get_admin_product_detail_keyboard(pid), parse_mode='Markdown')
            return True

        if action == "variant_upsert":
            pid = context.user_data["admin_pid"]
            variant_name, variant_data = parse_variant_upsert_input(text)
            PRODUK[pid]["variants"][variant_name] = variant_data
            await send_to_db("upsert_product", {
                "pid": pid, "nama": PRODUK[pid]["nama"], "desc": PRODUK[pid]["desc"], 
                "sold": PRODUK[pid]["sold"], "variants": PRODUK[pid]["variants"]
            })
            clear_admin_input_state(context)
            await send_banner_message(update.message, f"✅ Varian *{variant_name}* berhasil disimpan untuk produk *{PRODUK[pid]['nama']}*.", reply_markup=get_admin_variant_keyboard(pid), parse_mode='Markdown')
            return True

    except Exception as e:
        await send_banner_message(update.message, f"❌ Input tidak valid.\n\nAlasan: `{str(e)}`\n\nKirim ulang sesuai format atau kirim `/cancel` untuk batal.", parse_mode='Markdown')
        return True

    return False

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user = update.effective_user
    user_id = user.id
    await ensure_user(user)

    if await handle_admin_text_input(update, context, user_id, text):
        return

    if text == "🛒 Katalog Produk":
        loading_msg = await update.message.reply_text("⏳ _Loading..._", parse_mode='Markdown')
        await sync_katalog_realtime()
        await loading_msg.delete()
        await show_catalog_from_message(update.message)

    elif text == "👤 Profil & Akun":
        loading_msg = await update.message.reply_text("⏳ _Loading..._", parse_mode='Markdown')
        await sync_user_data(user_id)
        await loading_msg.delete()
        
        profil = USERS[user_id]
        pesan = (
            "👤 *INFORMASI AKUN*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 *ID Pengguna:* `{user_id}`\n"
            f"👤 *Nama Akun:* {profil['nama']}\n"
            f"💳 *Sisa Saldo:* `{format_rupiah(profil['saldo'])}`\n"
            f"🛍️ *Total Beli:* {profil['total_beli']} Produk\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "💡 _Pastikan saldo Anda mencukupi untuk melakukan pembelian instan._"
        )
        keyboard = [[InlineKeyboardButton("💳 Isi Saldo Sekarang", callback_data="menu_deposit")]]
        await send_banner_message(update.message, pesan, reply_markup=InlineKeyboardMarkup(keyboard))

    elif text == "💳 Deposit Saldo":
        pesan = (
            "💳 *ISI SALDO (DEPOSIT)*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Silakan pilih nominal deposit yang Anda inginkan."
        )
        keyboard = [
            [InlineKeyboardButton("Rp 20.000", callback_data="depo_20000"), InlineKeyboardButton("Rp 50.000", callback_data="depo_50000")],
            [InlineKeyboardButton("Rp 100.000", callback_data="depo_100000"), InlineKeyboardButton("Rp 200.000", callback_data="depo_200000")],
            [InlineKeyboardButton("Rp 500.000", callback_data="depo_500000")]
        ]
        await send_banner_message(update.message, pesan, reply_markup=InlineKeyboardMarkup(keyboard))

    elif text == "📜 Riwayat Beli":
        loading_msg = await update.message.reply_text("⏳ _Loading..._", parse_mode='Markdown')
        await sync_user_data(user_id)
        await loading_msg.delete()
        
        riwayat = USERS[user_id].get('riwayat', [])
        if not riwayat:
            pesan = "📜 *RIWAYAT TRANSAKSI*\n━━━━━━━━━━━━━━━━━━━━━━\nBelum ada riwayat pembelian produk."
        else:
            pesan = "📜 *RIWAYAT TRANSAKSI TERAKHIR*\n━━━━━━━━━━━━━━━━━━━━━━\n"
            for item in reversed(riwayat[-5:]):
                pesan += (
                    f"🗓️ `{item['waktu']}`\n"
                    f"📦 {item.get('nama_produk', 'Produk Digital')}\n"
                    f"🧩 Varian: {item.get('varian', '-')}\n"
                    f"💵 `{format_rupiah(item['harga'])}`\n\n"
                )
        await send_banner_message(update.message, pesan)

    elif text == "📞 Hubungi CS":
        pesan = (
            "📞 *HUBUNGI CUSTOMER SERVICE*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Jika Anda membutuhkan bantuan, silakan hubungi admin kami melalui tautan berikut:\n\n"
            "👉 [Klik di sini untuk Chat Admin](https://t.me/niskaladigital)"
        )
        await send_banner_message(update.message, pesan, parse_mode='Markdown')

    elif text == "🛠 Panel Admin":
        if user_id != ADMIN_ID:
            await send_banner_message(update.message, "❌ Menu ini hanya untuk admin.")
            return
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Dashboard", callback_data="admin_dashboard")],
            [InlineKeyboardButton("📦 Kelola Produk", callback_data="admin_manage_products")],
            [InlineKeyboardButton("🧾 Pending Transaksi", callback_data="admin_pending")]
        ])
        await send_banner_message(update.message, get_admin_dashboard_text(), reply_markup=keyboard)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user = query.from_user
    user_id = user.id
    await ensure_user(user)

    if data == "go_home":
        text = (
            "🏠 *MENU UTAMA*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Silakan pilih menu dari keyboard di bawah layar."
        )
        await append_with_banner(query, context, text, parse_mode='Markdown')

    elif data.startswith("catalog_page_"):
        await sync_katalog_realtime()
        page = int(data.split("_")[-1])
        await append_with_banner(query, context, render_catalog_text(page), reply_markup=get_catalog_keyboard(page), parse_mode=None)

    elif data == "menu_katalog":
        await sync_katalog_realtime()
        await append_with_banner(query, context, render_catalog_text(1), reply_markup=get_catalog_keyboard(1), parse_mode=None)

    elif data.startswith("detail_"):
        await sync_katalog_realtime()
        pid = data.split("_", 1)[1]
        if pid not in PRODUK:
            return await query.answer("Produk tidak ditemukan.", show_alert=True)
        await append_with_banner(query, context, render_product_detail(pid), reply_markup=get_variant_keyboard(pid), parse_mode=None)

    elif data.startswith("var_"):
        _, pid, variant_name = data.split("_", 2)
        
        await sync_katalog_realtime()
        product = PRODUK.get(pid)
        if not product:
            return await query.answer("Produk tidak ditemukan!", show_alert=True)

        variant = product['variants'].get(variant_name)
        if not variant:
            return await query.answer("Varian tidak ditemukan!", show_alert=True)

        if variant['stock'] <= 0:
            return await query.answer("Stok varian habis!", show_alert=True)

        pesan = (
            "🧾 *DETAIL PEMBELIAN*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Produk : *{product['nama']}*\n"
            f"🧩 Varian : *{variant_name}*\n"
            f"💵 Harga  : `{format_rupiah(variant['price'])}`\n"
            f"📦 Stok   : *{variant['stock']}*\n\n"
            "Silakan pilih metode pembayaran Anda:"
        )
        keyboard = [
            [InlineKeyboardButton("💳 Potong Saldo Akun (Instan)", callback_data=f"method_prod_saldo_{pid}_{variant_name}")],
            [InlineKeyboardButton("📱 QRIS Niskala Digital", callback_data=f"method_prod_qris_{pid}_{variant_name}")],
            [InlineKeyboardButton("⬅️ Kembali ke Detail", callback_data=f"detail_{pid}")]
        ]
        await append_with_banner(query, context, pesan, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "menu_deposit":
        pesan = (
            "💳 *ISI SALDO (DEPOSIT)*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Silakan pilih nominal deposit yang Anda inginkan."
        )
        keyboard = [
            [InlineKeyboardButton("Rp 20.000", callback_data="depo_20000"), InlineKeyboardButton("Rp 50.000", callback_data="depo_50000")],
            [InlineKeyboardButton("Rp 100.000", callback_data="depo_100000"), InlineKeyboardButton("Rp 200.000", callback_data="depo_200000")],
            [InlineKeyboardButton("Rp 500.000", callback_data="depo_500000")]
        ]
        await append_with_banner(query, context, pesan, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("depo_"):
        nominal = int(data.split("_")[1])
        pesan = (
            "💳 *PILIH METODE PEMBAYARAN*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Nominal Deposit: `{format_rupiah(nominal)}`\n\n"
            "Silakan pilih metode pembayaran yang Anda inginkan:"
        )
        keyboard = [
            [InlineKeyboardButton("📱 QRIS Niskala Digital", callback_data=f"method_depo_qris_{nominal}")],
            [InlineKeyboardButton("⬅️ Batal", callback_data="menu_deposit")]
        ]
        await append_with_banner(query, context, pesan, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("method_"):
        parts = data.split("_")
        jenis_trx = parts[1]
        metode = parts[2]
        trx_id = ""
        nominal = 0
        waktu = get_timestamp()

        if jenis_trx == "depo":
            nominal = int(parts[3])
            trx_id = f"DEP-{uuid.uuid4().hex[:6].upper()}"
            TRANSAKSI[trx_id] = {
                "trx_id": trx_id, 
                "jenis": "deposit",
                "user_id": user_id,
                "jumlah": nominal,
                "status": "pending",
                "waktu": waktu,
                "metode": "QRIS"
            }
            await send_to_db("add_transaksi", TRANSAKSI[trx_id])

        elif jenis_trx == "prod":
            pid = parts[3]
            variant_name = parts[4]
            product = PRODUK.get(pid)
            if not product:
                return await query.answer("Produk tidak ditemukan!", show_alert=True)
            variant = product['variants'].get(variant_name)
            if not variant:
                return await query.answer("Varian tidak ditemukan!", show_alert=True)
            if variant['stock'] <= 0:
                return await query.answer("Stok varian habis!", show_alert=True)

            nominal = variant['price']

            if metode == "saldo":
                await sync_user_data(user_id)
                saldo_user = USERS[user_id]['saldo']
                if saldo_user >= nominal:
                    USERS[user_id]['saldo'] -= nominal
                    USERS[user_id]['total_beli'] += 1
                    variant['stock'] -= 1
                    product['sold'] += 1
                    USERS[user_id]['riwayat'].append({
                        "waktu": waktu,
                        "nama_produk": product['nama'],
                        "varian": variant_name,
                        "harga": nominal
                    })
                    
                    await send_to_db("buy_product", {
                        "user_id": user_id, "pid": pid, "variant": variant_name, 
                        "harga": nominal, "waktu": waktu, "nama_produk": product['nama'], "metode": "saldo"
                    })

                    pesan_sukses = (
                        "🎉 *PEMBELIAN BERHASIL*\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"Produk : *{product['nama']}*\n"
                        f"Varian : *{variant_name}*\n"
                        "Metode : 💳 Saldo Akun\n"
                        "Status : 🟢 *LUNAS*\n"
                        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "📥 *LINK DOWNLOAD ANDA:*\n"
                        f"`{variant['link_download']}`\n\n"
                        f"_Sisa Saldo Anda: {format_rupiah(USERS[user_id]['saldo'])}_"
                    )
                    
                    # PERBAIKAN: Gunakan edit_caption jika pesan menggunakan gambar banner
                    if query.message.photo:
                        return await query.message.edit_caption(caption=pesan_sukses, parse_mode='Markdown')
                    else:
                        return await query.message.edit_text(text=pesan_sukses, parse_mode='Markdown')

                # --- LOGIKA JIKA SALDO KURANG / KOSONG ---
                kurang = nominal - saldo_user
                
                # Memunculkan Pop-up peringatan di tengah layar
                await query.answer("❌ Saldo tidak cukup / kosong. Silakan deposit dahulu.", show_alert=True)
                
                # Keyboard diarahkan ke Deposit Saldo
                keyboard = [
                    [InlineKeyboardButton("💳 Deposit Saldo", callback_data="menu_deposit")],
                    [InlineKeyboardButton("📱 Langsung via QRIS", callback_data=f"method_prod_qris_{pid}_{variant_name}")],
                    [InlineKeyboardButton("⬅️ Batal", callback_data=f"detail_{pid}")]
                ]
                
                pesan_kurang = (
                    "⚠️ *SALDO TIDAK MENCUKUPI*\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Harga Produk : `{format_rupiah(nominal)}`\n"
                    f"Saldo Anda   : `{format_rupiah(saldo_user)}`\n"
                    f"Kekurangan   : `{format_rupiah(kurang)}`\n\n"
                    "Silakan isi saldo Anda terlebih dahulu dengan menekan tombol di bawah ini."
                )
                
                if query.message.photo:
                    return await query.message.edit_caption(caption=pesan_kurang, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
                else:
                    return await query.message.edit_text(text=pesan_kurang, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

            # Jika metode == "QRIS", lanjut bikin invoice Pending
            trx_id = f"PRD-{uuid.uuid4().hex[:6].upper()}"
            TRANSAKSI[trx_id] = {
                "trx_id": trx_id, 
                "jenis": "produk",
                "pid": pid,
                "variant": variant_name,
                "user_id": user_id,
                "jumlah": nominal,
                "status": "pending",
                "waktu": waktu,
                "metode": "QRIS"
            }
            await send_to_db("add_transaksi", TRANSAKSI[trx_id])

        keyboard = [
            [InlineKeyboardButton("✅ Saya Sudah Transfer", callback_data=f"bayartrx_{trx_id}")],
            [InlineKeyboardButton("❌ Batalkan", callback_data=f"bataltrx_{trx_id}")]
        ]

        deskripsi_trx = "Deposit Saldo" if jenis_trx == "depo" else f"Produk: {PRODUK[TRANSAKSI[trx_id]['pid']]['nama']} | Varian: {TRANSAKSI[trx_id]['variant']}"
        jenis_label = "DEPOSIT" if jenis_trx == "depo" else "PEMBELIAN"

        pesan = (
            f"🧾 *INVOICE {jenis_label}*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"No. Referensi : `{trx_id}`\n"
            f"Item          : {deskripsi_trx}\n"
            "Status        : 🟡 *MENUNGGU PEMBAYARAN*\n"
            "Metode        : 📱 QRIS (Niskala Digital)\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Total Tagihan : `{format_rupiah(nominal)}`\n\n"
            "📱 *Instruksi Pembayaran QRIS:*\n"
            "1. Simpan gambar QRIS ini ke galeri Anda.\n"
            "2. Buka aplikasi m-Banking / e-Wallet.\n"
            "3. Scan / upload QRIS lalu lakukan pembayaran.\n\n"
            "⚠️ _Batas waktu 5 menit. Segera konfirmasi setelah sukses._"
        )
        await query.message.delete()
        
        try:
            if QRIS_URL_DIRECT.startswith("http"):
                await context.bot.send_photo(chat_id=query.message.chat_id, photo=QRIS_URL_DIRECT, caption=pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            else:
                with open(QRIS_URL_DIRECT, 'rb') as qris:
                    await context.bot.send_photo(chat_id=query.message.chat_id, photo=qris, caption=pesan, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Gagal mengirim QRIS: {e}")
            await context.bot.send_message(chat_id=query.message.chat_id, text="⚠️ *Error Sistem:* Gambar QRIS tidak dapat diakses.", parse_mode='Markdown')
            return

        context.job_queue.run_once(pengingat_trx_job, 300, data={'trx_id': trx_id, 'chat_id': query.message.chat_id}, name=f"reminder_{trx_id}")

    elif data.startswith("bayartrx_"):
        trx_id = data.split("_")[1]
        trx = TRANSAKSI.get(trx_id)
        if trx and trx['status'] == 'pending':
            trx['status'] = 'waiting_admin'
            await send_to_db("update_transaksi", {"trx_id": trx_id, "status": "waiting_admin"})

            jenis_str = "Deposit" if trx['jenis'] == "deposit" else "Pembelian"
            deskripsi_trx = "Deposit Saldo" if trx['jenis'] == "deposit" else f"Produk: {PRODUK[trx['pid']]['nama']} | Varian: {trx['variant']}"

            pesan_user = (
                "⏳ *MENUNGGU KONFIRMASI ADMIN*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"No. Referensi : `{trx_id}`\n"
                f"Item          : {deskripsi_trx}\n"
                f"Nominal       : `{format_rupiah(trx['jumlah'])}`\n"
                "Status        : 🟡 *SEDANG DIPROSES*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Pesanan Anda telah diteruskan ke admin untuk pengecekan pembayaran."
            )
            if query.message.photo:
                await query.message.edit_caption(caption=pesan_user, parse_mode='Markdown')
            else:
                await query.message.edit_text(text=pesan_user, parse_mode='Markdown')

            uid = trx['user_id']
            nama_user = USERS[uid]['nama']
            metode_bayar = trx.get('metode', 'Tidak Diketahui')
            pesan_admin = (
                f"🚨 *REQUEST {jenis_str.upper()} BARU* 🚨\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"ID Trx  : `{trx_id}`\n"
                f"User ID : `{uid}`\n"
                f"Nama    : {nama_user}\n"
                f"Item    : {deskripsi_trx}\n"
                f"Nominal : `{format_rupiah(trx['jumlah'])}`\n"
                f"Metode  : *{metode_bayar}*\n"
                f"Waktu   : {trx['waktu']}\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Silakan cek mutasi dan konfirmasi di bawah ini."
            )
            keyboard_admin = [
                [InlineKeyboardButton("✅ Setujui", callback_data=f"adminapprove_{trx_id}")],
                [InlineKeyboardButton("❌ Tolak", callback_data=f"adminreject_{trx_id}")]
            ]
            try:
                await context.bot.send_message(chat_id=ADMIN_ID, text=pesan_admin, reply_markup=InlineKeyboardMarkup(keyboard_admin), parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Gagal kirim notif admin: {e}")
        else:
            await query.answer("Invoice tidak valid atau sudah diproses.", show_alert=True)

    elif data.startswith("adminapprove_"):
        if user_id != ADMIN_ID:
            return await query.answer("Akses Ditolak! Anda bukan Admin.", show_alert=True)
        trx_id = data.split("_")[1]
        trx = TRANSAKSI.get(trx_id)
        if trx and trx['status'] == 'waiting_admin':
            trx['status'] = 'success'
            uid = trx['user_id']

            await send_to_db("update_transaksi", {"trx_id": trx_id, "status": "LUNAS"})

            if trx['jenis'] == 'deposit':
                USERS[uid]['saldo'] += trx['jumlah']
                await send_to_db("deposit_saldo", {"user_id": uid, "jumlah": trx['jumlah']})

                await query.message.edit_text(f"✅ Deposit `{trx_id}` sebesar `{format_rupiah(trx['jumlah'])}` untuk User `{uid}` telah *DISETUJUI*.", parse_mode='Markdown')
                pesan_sukses_user = (
                    "✅ *DEPOSIT BERHASIL DIPROSES*\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"No. Referensi : `{trx_id}`\n"
                    f"Nominal       : `{format_rupiah(trx['jumlah'])}`\n"
                    "Status        : 🟢 *SUKSES*\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Saldo Anda saat ini: `{format_rupiah(USERS[uid]['saldo'])}`"
                )
                try:
                    await context.bot.send_message(chat_id=uid, text=pesan_sukses_user, parse_mode='Markdown')
                except Exception:
                    pass

            elif trx['jenis'] == 'produk':
                pid = trx['pid']
                variant_name = trx['variant']
                product = PRODUK[pid]
                variant = product['variants'][variant_name]
                if variant['stock'] > 0:
                    variant['stock'] -= 1
                product['sold'] += 1
                USERS[uid]['total_beli'] += 1
                USERS[uid]['riwayat'].append({
                    "waktu": trx['waktu'],
                    "nama_produk": product['nama'],
                    "varian": variant_name,
                    "harga": trx['jumlah']
                })

                await send_to_db("buy_product", {
                    "user_id": uid, "pid": pid, "variant": variant_name, 
                    "harga": trx['jumlah'], "waktu": trx['waktu'], "nama_produk": product['nama'], "metode": "QRIS"
                })

                await query.message.edit_text(f"✅ Pembelian produk `{trx_id}` ({product['nama']} - {variant_name}) untuk User `{uid}` telah *DISETUJUI*.", parse_mode='Markdown')
                pesan_sukses_user = (
                    "🎉 *PEMBELIAN BERHASIL DIPROSES*\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"No. Ref : `{trx_id}`\n"
                    f"Produk  : *{product['nama']}*\n"
                    f"Varian  : *{variant_name}*\n"
                    "Status  : 🟢 *LUNAS*\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "📥 *LINK DOWNLOAD ANDA:*\n"
                    f"`{variant['link_download']}`"
                )
                try:
                    await context.bot.send_message(chat_id=uid, text=pesan_sukses_user, parse_mode='Markdown')
                except Exception:
                    pass

    elif data.startswith("adminreject_"):
        if user_id != ADMIN_ID:
            return await query.answer("Akses Ditolak! Anda bukan Admin.", show_alert=True)
        trx_id = data.split("_")[1]
        trx = TRANSAKSI.get(trx_id)
        if trx and trx['status'] == 'waiting_admin':
            trx['status'] = 'rejected'
            await send_to_db("update_transaksi", {"trx_id": trx_id, "status": "rejected"})

            uid = trx['user_id']
            jenis_str = "Deposit" if trx['jenis'] == "deposit" else "Pembelian"
            await query.message.edit_text(f"❌ {jenis_str} `{trx_id}` untuk User `{uid}` telah *DITOLAK*.", parse_mode='Markdown')
            pesan_gagal_user = (
                "❌ *TRANSAKSI DITOLAK ADMIN*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"No. Referensi : `{trx_id}`\n"
                "Status        : 🔴 *GAGAL*\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Pastikan dana sudah masuk sesuai nominal atau hubungi admin jika terjadi kesalahan."
            )
            try:
                await context.bot.send_message(chat_id=uid, text=pesan_gagal_user, parse_mode='Markdown')
            except Exception:
                pass

    elif data.startswith("bataltrx_"):
        trx_id = data.split("_")[1]
        if trx_id in TRANSAKSI and TRANSAKSI[trx_id]['status'] == 'pending':
            TRANSAKSI[trx_id]['status'] = 'cancelled'
            await send_to_db("update_transaksi", {"trx_id": trx_id, "status": "cancelled"})

            if query.message.photo:
                await query.message.edit_caption(caption=f"❌ Invoice `{trx_id}` berhasil dibatalkan.", parse_mode='Markdown')
            else:
                await query.message.edit_text(text=f"❌ Invoice `{trx_id}` berhasil dibatalkan.", parse_mode='Markdown')

    elif data == "admin_dashboard":
        if user_id != ADMIN_ID:
            return await query.answer("Akses Ditolak!", show_alert=True)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 Kelola Produk", callback_data="admin_manage_products")],
            [InlineKeyboardButton("🧾 Pending Transaksi", callback_data="admin_pending")],
            [InlineKeyboardButton("🔄 Refresh", callback_data="admin_dashboard")]
        ])
        await append_with_banner(query, context, get_admin_dashboard_text(), reply_markup=keyboard)

    elif data == "admin_manage_products":
        if user_id != ADMIN_ID:
            return await query.answer("Akses Ditolak!", show_alert=True)
        clear_admin_input_state(context)
        await append_with_banner(query, context, render_admin_product_list(1), reply_markup=get_admin_product_list_keyboard(1), parse_mode='Markdown')

    elif data.startswith("admin_manage_products_page_"):
        if user_id != ADMIN_ID:
            return await query.answer("Akses Ditolak!", show_alert=True)
        page = int(data.split("_")[-1])
        await append_with_banner(query, context, render_admin_product_list(page), reply_markup=get_admin_product_list_keyboard(page), parse_mode='Markdown')

    elif data == "admin_product_guide":
        if user_id != ADMIN_ID:
            return await query.answer("Akses Ditolak!", show_alert=True)
        guide = (
            "📘 *PANDUAN KELOLA PRODUK*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "*1. Tambah produk baru*\n"
            "Tekan tombol *Tambah Produk*, lalu kirim format berikut:\n\n"
            "`kode|nama|deskripsi|sold`\n"
            "`varian_1|harga|stok|link_download`\n"
            "`varian_2|harga|stok|link_download`\n\n"
            "Contoh:\n"
            "`p16|NETFLIX PREMIUM|Private Account|1200`\n"
            "`1 Bulan|25000|8|https://linkanda.com/netflix`\n"
            "`3 Bulan|65000|4|https://linkanda.com/netflix3`\n\n"
            "*2. Edit info dasar*\n"
            "Masuk ke detail produk → *Edit Info Dasar* lalu kirim:\n"
            "`nama=...`\n`desc=...`\n`sold=...`\n\n"
            "*3. Kelola varian*\n"
            "Masuk ke *Kelola Variasi* untuk tambah, edit, atau hapus varian.\n\n"
            "Kirim `/cancel` kapan saja untuk membatalkan mode input admin."
        )
        await append_with_banner(query, context, guide, parse_mode='Markdown')

    elif data == "admin_add_product":
        if user_id != ADMIN_ID:
            return await query.answer("Akses Ditolak!", show_alert=True)
        context.user_data["admin_action"] = "add_product"
        context.user_data.pop("admin_pid", None)
        context.user_data.pop("admin_variant", None)
        text = (
            "➕ *TAMBAH PRODUK BARU*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Silakan kirim data produk dengan format berikut:\n\n"
            "`kode|nama|deskripsi|sold`\n"
            "`varian_1|harga|stok|link_download`\n"
            "`varian_2|harga|stok|link_download`\n\n"
            "Catatan:\n"
            "• `kode` boleh dikosongkan, nanti bot buat otomatis.\n"
            "• Setiap varian wajib 1 baris.\n"
            "• `sold`, `harga`, dan `stok` harus angka.\n\n"
            "Kirim `/cancel` untuk membatalkan."
        )
        await append_with_banner(query, context, text, parse_mode='Markdown')

    elif data.startswith("admin_product_"):
        if user_id != ADMIN_ID:
            return await query.answer("Akses Ditolak!", show_alert=True)
        pid = data.split("_")[-1]
        if pid not in PRODUK:
            return await query.answer("Produk tidak ditemukan!", show_alert=True)
        clear_admin_input_state(context)
        await append_with_banner(query, context, render_admin_product_detail(pid), reply_markup=get_admin_product_detail_keyboard(pid), parse_mode='Markdown')

    elif data.startswith("admin_edit_basic_"):
        if user_id != ADMIN_ID:
            return await query.answer("Akses Ditolak!", show_alert=True)
        pid = data.split("_")[-1]
        context.user_data["admin_action"] = "edit_basic"
        context.user_data["admin_pid"] = pid
        text = (
            f"✏️ *EDIT INFO DASAR — {PRODUK[pid]['nama']}*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Kirim field yang ingin diubah dengan format:\n\n"
            "`nama=Nama Baru`\n"
            "`desc=Deskripsi Baru`\n"
            "`sold=12345`\n\n"
            "Anda boleh kirim satu field saja atau beberapa field sekaligus.\n"
            "Kirim `/cancel` untuk membatalkan."
        )
        await append_with_banner(query, context, text, parse_mode='Markdown')

    elif data.startswith("admin_edit_variants_"):
        if user_id != ADMIN_ID:
            return await query.answer("Akses Ditolak!", show_alert=True)
        pid = data.split("_")[-1]
        clear_admin_input_state(context)
        text = (
            f"🧩 *KELOLA VARIASI — {PRODUK[pid]['nama']}*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Pilih varian untuk lihat detail, edit, atau hapus.\n"
            "Gunakan tombol *Tambah/Update Variasi* untuk menambah varian baru."
        )
        await append_with_banner(query, context, text, reply_markup=get_admin_variant_keyboard(pid), parse_mode='Markdown')

    elif data.startswith("admin_variant_guide_"):
        if user_id != ADMIN_ID:
            return await query.answer("Akses Ditolak!", show_alert=True)
        pid = data.split("_")[-1]
        text = (
            f"📘 *FORMAT VARIASI — {PRODUK[pid]['nama']}*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Untuk tambah atau update varian, kirim format berikut:\n\n"
            "`nama_varian|harga|stok|link_download`\n\n"
            "Contoh:\n"
            "`Member|8000|61|https://linkanda.com/canva-member`\n\n"
            "Jika nama varian sudah ada, data akan diperbarui.\n"
            "Kirim `/cancel` untuk membatalkan."
        )
        await append_with_banner(query, context, text, parse_mode='Markdown')

    elif data.startswith("admin_variant_add_"):
        if user_id != ADMIN_ID:
            return await query.answer("Akses Ditolak!", show_alert=True)
        pid = data.split("_")[-1]
        context.user_data["admin_action"] = "variant_upsert"
        context.user_data["admin_pid"] = pid
        context.user_data.pop("admin_variant", None)
        text = (
            f"➕ *TAMBAH / UPDATE VARIASI — {PRODUK[pid]['nama']}*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Kirim data varian dengan format:\n\n"
            "`nama_varian|harga|stok|link_download`\n\n"
            "Jika nama varian sudah ada, bot akan memperbarui data lama.\n"
            "Kirim `/cancel` untuk membatalkan."
        )
        await append_with_banner(query, context, text, parse_mode='Markdown')

    elif data.startswith("admin_variant_detail_"):
        if user_id != ADMIN_ID:
            return await query.answer("Akses Ditolak!", show_alert=True)
        _, _, _, pid, variant_name = data.split("_", 4)
        if pid not in PRODUK or variant_name not in PRODUK[pid]["variants"]:
            return await query.answer("Varian tidak ditemukan!", show_alert=True)
        await append_with_banner(query, context, render_admin_variant_detail(pid, variant_name), reply_markup=get_admin_variant_detail_keyboard(pid, variant_name), parse_mode='Markdown')

    elif data.startswith("admin_variant_edit_"):
        if user_id != ADMIN_ID:
            return await query.answer("Akses Ditolak!", show_alert=True)
        _, _, _, pid, variant_name = data.split("_", 4)
        if pid not in PRODUK or variant_name not in PRODUK[pid]["variants"]:
            return await query.answer("Varian tidak ditemukan!", show_alert=True)
        context.user_data["admin_action"] = "variant_upsert"
        context.user_data["admin_pid"] = pid
        context.user_data["admin_variant"] = variant_name
        current = PRODUK[pid]["variants"][variant_name]
        text = (
            f"✏️ *EDIT VARIASI — {variant_name}*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Kirim ulang data varian dengan format:\n\n"
            "`nama_varian|harga|stok|link_download`\n\n"
            "Data saat ini:\n"
            f"`{variant_name}|{current['price']}|{current['stock']}|{current['link_download']}`\n\n"
            "Kirim `/cancel` untuk membatalkan."
        )
        await append_with_banner(query, context, text, parse_mode='Markdown')

    elif data.startswith("admin_variant_delete_"):
        if user_id != ADMIN_ID:
            return await query.answer("Akses Ditolak!", show_alert=True)
        _, _, _, pid, variant_name = data.split("_", 4)
        if pid not in PRODUK or variant_name not in PRODUK[pid]["variants"]:
            return await query.answer("Varian tidak ditemukan!", show_alert=True)
        if len(PRODUK[pid]["variants"]) == 1:
            return await query.answer("Produk wajib memiliki minimal 1 varian.", show_alert=True)
        del PRODUK[pid]["variants"][variant_name]
        await send_to_db("delete_variant", {"pid": pid, "variant": variant_name})
        await append_with_banner(query, context, f"✅ Varian *{variant_name}* berhasil dihapus dari produk *{PRODUK[pid]['nama']}*.", reply_markup=get_admin_variant_keyboard(pid), parse_mode='Markdown')

    elif data.startswith("admin_delete_"):
        if user_id != ADMIN_ID:
            return await query.answer("Akses Ditolak!", show_alert=True)
        pid = data.split("_")[-1]
        if pid not in PRODUK:
            return await query.answer("Produk tidak ditemukan!", show_alert=True)
        nama = PRODUK[pid]["nama"]
        del PRODUK[pid]
        await send_to_db("delete_product", {"pid": pid})
        clear_admin_input_state(context)
        await append_with_banner(query, context, f"✅ Produk *{nama}* dengan kode `{pid}` berhasil dihapus.", reply_markup=get_admin_product_list_keyboard(1), parse_mode='Markdown')

    elif data == "admin_pending":
        if user_id != ADMIN_ID:
            return await query.answer("Akses Ditolak!", show_alert=True)
        waiting = [f"• {tid} | {t['jenis']} | {format_rupiah(t['jumlah'])}" for tid, t in TRANSAKSI.items() if t['status'] == 'waiting_admin']
        pending = [f"• {tid} | {t['jenis']} | {format_rupiah(t['jumlah'])}" for tid, t in TRANSAKSI.items() if t['status'] == 'pending']
        text = (
            "🧾 *TRANSAKSI ADMIN*\n━━━━━━━━━━━━━━━━━━━━━━\n"
            "*Waiting Admin:*\n" + ("\n".join(waiting) if waiting else "Tidak ada") + "\n\n"
            "*Pending Invoice:*\n" + ("\n".join(pending) if pending else "Tidak ada")
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Dashboard", callback_data="admin_dashboard")]])
        await append_with_banner(query, context, text, reply_markup=keyboard)


async def pengingat_trx_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    trx_id = job.data['trx_id']
    chat_id = job.data['chat_id']
    trx = TRANSAKSI.get(trx_id)
    if trx and trx['status'] == 'pending':
        deskripsi = "Deposit Saldo" if trx['jenis'] == 'deposit' else "Pembelian Produk"
        pesan_pengingat = (
            "⏰ *PENGINGAT PEMBAYARAN* ⏰\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Halo Kak! Invoice `{trx_id}` untuk *{deskripsi}* sebesar `{format_rupiah(trx['jumlah'])}` menunggu pembayaran Anda.\n\n"
            "Jika Anda sudah transfer, segera klik tombol *Saya Sudah Transfer*."
        )
        keyboard = [
            [InlineKeyboardButton("✅ Saya Sudah Transfer", callback_data=f"bayartrx_{trx_id}")],
            [InlineKeyboardButton("❌ Batalkan", callback_data=f"bataltrx_{trx_id}")]
        ]
        try:
            await context.bot.send_message(chat_id=chat_id, text=pesan_pengingat, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Gagal mengirim pengingat: {e}")

def main():
    load_data_from_db()
    logger.info('Menyalakan bot Telegram...')
    logger.info(f'ADMIN_ID aktif: {ADMIN_ID}')
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    PORT = int(os.environ.get('PORT', '8443'))
    WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '').strip()

    if WEBHOOK_URL:
        # Menghapus garis miring (/) di akhir URL jika tidak sengaja tertulis
        clean_webhook_url = WEBHOOK_URL.rstrip('/')
        
        print(f"Mulai mode WEBHOOK di port {PORT}...")
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN, 
            webhook_url=f"{clean_webhook_url}/{TOKEN}" 
        )
    else:
        print("Bot Toko Digital Premium sedang berjalan dengan mode POLLING...")
        application.run_polling()

if __name__ == '__main__':
    main()
