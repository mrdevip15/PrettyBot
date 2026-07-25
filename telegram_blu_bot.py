# ==============================================================================
#  PRETTY PET SALON - TELEGRAM BOT AUTO-PAYMENT (AUTO FLASH SALE TGL 25 & TGL KEMBAR)
# ==============================================================================
import os
import re
import csv
import json
import time
import hmac
import random
import hashlib
import imaplib
import email
import asyncio
import threading
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, Application, filters

def load_env_file(filepath=".env"):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ[k.strip()] = v.strip().strip('"').strip("'")
            print("✅ Berhasil memuat kredensial dari file .env!")
        except Exception as e:
            print(f"⚠️ Gagal membaca .env: {e}")

load_env_file(".env")

SECRET_KEY = b"PpS4L0N2o24K3y"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "MASUKKAN_TOKEN_BOT_TELEGRAM_KAMU")
BLU_ACCOUNT_NUMBER = os.environ.get("BLU_ACCOUNT_NUMBER", "0089 2056 9145")
BLU_ACCOUNT_NAME = os.environ.get("BLU_ACCOUNT_NAME", "MUHAMMAD HIDAYAT")
GMAIL_USER = os.environ.get("GMAIL_USER", "emailkamu@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "abcd efgh ijkl mnop")
IMAP_SERVER = os.environ.get("IMAP_SERVER", "imap.gmail.com")

ADMIN_TELEGRAM_ID = os.environ.get("ADMIN_TELEGRAM_ID", "7814906855")

# CONFIGURATION FLASH SALE & MINIMUM TRANSFER
FLASH_SALE_DISCOUNT = 0.40      # Diskon 40%
MINIMUM_TRANSFER = 10000        # Batas minimum transfer bank (Rp 10.000)
REFERRAL_BONUS = 1000           # Bonus saldo referral Rp 1.000 untuk pengajak

DB_FILE = "pending_orders.json"
HISTORY_FILE = "transaction_history.json"
BALANCE_FILE = "user_balances.json"
FLASH_CLAIMS_FILE = "flash_sale_claims.json"
USERS_FILE = "registered_users.json"
COUPONS_FILE = "coupons.json"

PROCESSED_MAIL_IDS = set()
CANCELLED_INVOICES = {}
BOT_USERNAME = "PrettyPetSalon_bot"

def is_flash_sale_active() -> bool:
    """Otomatis aktif pada tanggal 25 dan tanggal kembar (1.1, 2.2, dst) setiap bulan"""
    now = datetime.now()
    return now.day == 25 or now.day == now.month

def fmt_idr(val: int) -> str:
    """Helper untuk format angka ke Rupiah exact Indonesia tanpa ,00 (cth: Rp 10.421)"""
    return f"Rp {val:,}".replace(",", ".")

# --- DATABASE USERS & REFERRAL ---
def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_users(users):
    try:
        with open(USERS_FILE, "w") as f:
            json.dump(users, f, indent=2)
    except Exception as e:
        print(f"Error save users: {e}")

def register_user(user_id: int, username: str, full_name: str, referred_by: int = None) -> tuple[dict, bool]:
    users = load_users()
    uid_str = str(user_id)
    is_new = uid_str not in users

    if is_new:
        ref_id = referred_by if referred_by and str(referred_by) != uid_str and str(referred_by) in users else None
        users[uid_str] = {
            "username": username or "",
            "full_name": full_name or "",
            "referred_by": ref_id,
            "has_paid_first_tx": False,
            "joined_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_users(users)
    else:
        # Update username / full_name if changed
        users[uid_str]["username"] = username or users[uid_str].get("username", "")
        users[uid_str]["full_name"] = full_name or users[uid_str].get("full_name", "")
        save_users(users)

    return users[uid_str], is_new

def trigger_referral_reward_if_eligible(user_id: int, app: Application, loop):
    users = load_users()
    uid_str = str(user_id)
    if uid_str not in users:
        return

    user_info = users[uid_str]
    if not user_info.get("has_paid_first_tx") and user_info.get("referred_by"):
        referrer_id = user_info["referred_by"]
        user_info["has_paid_first_tx"] = True
        save_users(users)

        # Award bonus to referrer
        new_bal = add_user_balance(referrer_id, REFERRAL_BONUS)

        # Notify referrer
        msg = (
            f"🎉 *BONUS REFERRAL MASUK!*\n\n"
            f"Teman yang Anda ajak (ID: `{user_id}`) telah melakukan transaksi pertamanya!\n"
            f"💵 *Bonus*: `{fmt_idr(REFERRAL_BONUS)}` telah ditambahkan ke saldo Anda.\n"
            f"💰 *Total Saldo Sekarang*: `{fmt_idr(new_bal)}`"
        )
        try:
            asyncio.run_coroutine_threadsafe(
                app.bot.send_message(chat_id=referrer_id, text=msg, parse_mode="Markdown"),
                loop
            )
        except Exception as e:
            print(f"Error notifying referrer: {e}")
    else:
        user_info["has_paid_first_tx"] = True
        save_users(users)

# --- DATABASE COUPONS ---
def load_coupons():
    if os.path.exists(COUPONS_FILE):
        try:
            with open(COUPONS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_coupons(coupons):
    try:
        with open(COUPONS_FILE, "w") as f:
            json.dump(coupons, f, indent=2)
    except Exception as e:
        print(f"Error save coupons: {e}")

# --- DATABASE FLASH SALE CLAIMS ---
def load_flash_claims():
    if os.path.exists(FLASH_CLAIMS_FILE):
        try:
            with open(FLASH_CLAIMS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_flash_claims(claims_set):
    try:
        with open(FLASH_CLAIMS_FILE, "w") as f:
            json.dump(list(claims_set), f, indent=2)
    except Exception as e:
        print(f"Error save flash claims: {e}")

def has_user_claimed_flash_sale(user_id: int) -> bool:
    claims = load_flash_claims()
    current_key = f"{user_id}_{datetime.now().strftime('%Y-%m-%d')}"
    return current_key in claims

def mark_user_claimed_flash_sale(user_id: int):
    claims = load_flash_claims()
    current_key = f"{user_id}_{datetime.now().strftime('%Y-%m-%d')}"
    claims.add(current_key)
    save_flash_claims(claims)

# --- DATABASE SALDO USER ---
def load_balances():
    if os.path.exists(BALANCE_FILE):
        try:
            with open(BALANCE_FILE, "r") as f:
                data = json.load(f)
                return {int(k): int(v) for k, v in data.items()}
        except Exception:
            return {}
    return {}

def save_balances(balances):
    try:
        with open(BALANCE_FILE, "w") as f:
            json.dump({str(k): v for k, v in balances.items()}, f, indent=2)
    except Exception as e:
        print(f"Error save balances: {e}")

def get_user_balance(user_id: int) -> int:
    balances = load_balances()
    return balances.get(user_id, 0)

def add_user_balance(user_id: int, amount: int) -> int:
    balances = load_balances()
    current = balances.get(user_id, 0)
    new_bal = current + amount
    balances[user_id] = new_bal
    save_balances(balances)
    return new_bal

def deduct_user_balance(user_id: int, amount: int) -> bool:
    balances = load_balances()
    current = balances.get(user_id, 0)
    if current >= amount:
        balances[user_id] = current - amount
        save_balances(balances)
        return True
    return False

# --- DATABASE ORDERS & HISTORY ---
def load_pending_orders():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                data = json.load(f)
                return {int(k): v for k, v in data.items()}
        except Exception:
            return {}
    return {}

def save_pending_orders(invoices):
    try:
        with open(DB_FILE, "w") as f:
            json.dump({str(k): v for k, v in invoices.items()}, f)
    except Exception as e:
        print(f"Error save json: {e}")

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def add_history_record(amount: int, pkg_name: str, token: str, chat_id: int, recipient_info: str = None):
    records = load_history()
    new_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "amount": amount,
        "pkg_name": pkg_name,
        "token": token,
        "chat_id": chat_id,
        "recipient": recipient_info or "Self"
    }
    records.append(new_entry)
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(records, f, indent=2)
    except Exception as e:
        print(f"Error save history: {e}")

PENDING_INVOICES = load_pending_orders()

PACKAGES = {
    "pkg_1": {"name": "100 Pet Points", "base_price": 3000, "id": 1},
    "pkg_2": {"name": "210 Pet Points", "base_price": 6000, "id": 2},
    "pkg_3": {"name": "460 Pet Points", "base_price": 12000, "id": 3},
    "pkg_4": {"name": "1.250 Pet Points", "base_price": 30000, "id": 4},
}

def get_package_price_for_user(user_id: int, base_price: int, coupon_percent: int = 0) -> tuple[int, bool]:
    """Mengembalikan tuple: (harga_akhir, is_flash_sale_applied)"""
    price = base_price
    is_flash = False
    if is_flash_sale_active() and not has_user_claimed_flash_sale(user_id):
        price = int(price * (1 - FLASH_SALE_DISCOUNT))
        is_flash = True
    
    if coupon_percent > 0:
        price = int(price * (1 - (coupon_percent / 100)))

    return max(price, 0), is_flash

def generate_token(package_id: int) -> str:
    nonce = os.urandom(2)
    data = bytes([package_id]) + nonce
    h = hmac.new(SECRET_KEY, data, hashlib.sha256).digest()
    token_bytes = data + h[:3]
    
    chars = []
    for b in token_bytes:
        high = (b & 0xF0) >> 4
        low = b & 0x0F
        chars.append(chr(65 + high))
        chars.append(chr(65 + low))
    
    raw = "".join(chars)
    return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"

def extract_amount_from_blu_email(email_body: str) -> list[int]:
    found_amounts = []
    clean_text = re.sub(r'<[^>]+>', ' ', email_body)
    matches = re.findall(r'Rp\s*([\d\.]+)', clean_text, re.IGNORECASE)
    for m in matches:
        clean_str = m.replace('.', '').strip()
        if clean_str.isdigit():
            val = int(clean_str)
            if val >= 1000:
                found_amounts.append(val)
    return found_amounts

# --- ADMIN CHECK & DECORATOR ---
def is_user_admin(user_id: int) -> bool:
    if not ADMIN_TELEGRAM_ID:
        return False
    return str(user_id).strip() == str(ADMIN_TELEGRAM_ID).strip()

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if not is_user_admin(user_id):
            if update.message:
                await update.message.reply_text("⛔ *AKSES DITOLAK*: Perintah ini khusus Admin!", parse_mode="Markdown")
            elif update.callback_query:
                await update.callback_query.answer("⛔ Akses ditolak: Khusus Admin!", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

# --- EMAIL LISTENER & EXPIRED INVOICE CLEANUP ---
def check_gmail_for_blu_transfers(app: Application, loop):
    print("📧 Service Email Listener (blu BCA) & Auto-Expiry Worker Aktif...")
    last_auto_broadcast_date = ""

    while True:
        # 1. AUTO-EXPIRY CHECK (30 MENIT)
        try:
            now_t = time.time()
            expired_amts = []
            for amt, inv in list(PENDING_INVOICES.items()):
                if now_t - inv.get("timestamp", now_t) > 1800:  # 30 menit
                    expired_amts.append(amt)

            for amt in expired_amts:
                inv = PENDING_INVOICES.pop(amt, None)
                if inv:
                    CANCELLED_INVOICES[amt] = inv
                    save_pending_orders(PENDING_INVOICES)
                    print(f"⏳ [ORDER EXPIRED] Nominal {fmt_idr(amt)} dibatalkan otomatis (30 menit expired).")
                    if "message_id" in inv and inv["message_id"]:
                        exp_text = (
                            f"⚠️ *PESANAN KEDALUWARSA (EXPIRED)*\n\n"
                            f"Pesanan sebesar `{fmt_idr(amt)}` telah dibatalkan otomatis karena tidak ada transfer yang terdeteksi dalam 30 menit.\n\n"
                            f"*(Silakan buat pesanan baru jika Anda masih ingin membeli)*"
                        )
                        asyncio.run_coroutine_threadsafe(
                            app.bot.edit_message_text(
                                chat_id=inv["chat_id"],
                                message_id=inv["message_id"],
                                text=exp_text,
                                parse_mode="Markdown"
                            ),
                            loop
                        )
        except Exception as e:
            print(f"Warning cleanup: {e}")

        # 2. AUTO FLASH SALE BROADCAST AT 08:00 WIB
        try:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            if is_flash_sale_active() and now.hour == 8 and today_str != last_auto_broadcast_date:
                last_auto_broadcast_date = today_str
                users = load_users()
                broadcast_msg = (
                    f"⚡ *[FLASH SALE DISKON 40% HARI INI AKTIF!]* ⚡\n\n"
                    f"Hari ini adalah hari Flash Sale! Dapatkan diskon 40% untuk pembelian paket Pet Points di PrettyBot.\n\n"
                    f"👉 Buka bot sekarang untuk klaim diskon 40% Anda!"
                )
                for uid in users:
                    try:
                        asyncio.run_coroutine_threadsafe(
                            app.bot.send_message(chat_id=int(uid), text=broadcast_msg, parse_mode="Markdown"),
                            loop
                        )
                    except Exception:
                        pass
        except Exception as e:
            print(f"Warning auto broadcast: {e}")

        # 3. IMAP GMAIL CHECK
        try:
            mail = imaplib.IMAP4_SSL(IMAP_SERVER)
            mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            mail.select("inbox")

            status, messages = mail.search(None, '(FROM "receipts@blubybcadigital.id")')
            if status == "OK" and messages[0]:
                email_ids = messages[0].split()
                recent_ids = email_ids[-5:]
                
                for mail_id in recent_ids:
                    if mail_id in PROCESSED_MAIL_IDS:
                        continue
                    PROCESSED_MAIL_IDS.add(mail_id)

                    _, msg_data = mail.fetch(mail_id, '(RFC822)')
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            body = ""
                            if msg.is_multipart():
                                for part in msg.walk():
                                    if part.get_content_type() in ["text/plain", "text/html"]:
                                        payload = part.get_payload(decode=True)
                                        if payload:
                                            body += payload.decode('utf-8', errors='ignore')
                            else:
                                payload = msg.get_payload(decode=True)
                                if payload:
                                    body = payload.decode('utf-8', errors='ignore')

                            detected_amounts = extract_amount_from_blu_email(body)
                            if detected_amounts:
                                print(f"📩 [EMAIL BARU MASUK] Nominal: {fmt_idr(detected_amounts[0])} | Active Orders: {list(PENDING_INVOICES.keys())}")

                            for amt in detected_amounts:
                                inv = None
                                was_cancelled = False
                                
                                if amt in PENDING_INVOICES:
                                    inv = PENDING_INVOICES.pop(amt)
                                    save_pending_orders(PENDING_INVOICES)
                                elif amt in CANCELLED_INVOICES:
                                    inv = CANCELLED_INVOICES.pop(amt)
                                    was_cancelled = True

                                if inv:
                                    chat_id = inv["chat_id"]

                                    # Trigger Referral Bonus if applicable
                                    trigger_referral_reward_if_eligible(chat_id, app, loop)

                                    # CASE A: TOP-UP SALDO
                                    if inv.get("is_topup"):
                                        total_bal = add_user_balance(chat_id, amt)
                                        add_history_record(amt, "Top-Up Saldo Bot", "BALANCE_CREDIT", chat_id)

                                        if "message_id" in inv and inv["message_id"]:
                                            lunas_text = (
                                                f"✅ *TOP-UP SALDO SUKSES VIA BLU BCA*\n\n"
                                                f"💵 *Nominal Transfer*: {fmt_idr(amt)}\n"
                                                f"💰 *Total Saldo Sekarang*: {fmt_idr(total_bal)}\n"
                                                f"⏰ *Waktu*: {datetime.now().strftime('%H:%M:%S WIB')}"
                                            )
                                            asyncio.run_coroutine_threadsafe(
                                                app.bot.edit_message_text(
                                                    chat_id=chat_id,
                                                    message_id=inv["message_id"],
                                                    text=lunas_text,
                                                    parse_mode="Markdown"
                                                ),
                                                loop
                                            )
                                        success_topup = f"🎉 *TOP-UP BERHASIL!*\n\nNominal {fmt_idr(amt)} telah ditambahkan ke Saldo Bot Anda.\n💰 *Saldo Saat Ini*: {fmt_idr(total_bal)}"
                                        asyncio.run_coroutine_threadsafe(
                                            app.bot.send_message(chat_id=chat_id, text=success_topup, parse_mode="Markdown"),
                                            loop
                                        )
                                        print(f"✅ [TOP-UP LUNAS!] User {chat_id} +{fmt_idr(amt)} -> Saldo: {fmt_idr(total_bal)}")
                                        continue

                                    # CASE B: VOUCHER PURCHASE
                                    package_cost = inv["package_cost"]
                                    overpay = inv.get("overpay", 0)
                                    is_flash = inv.get("is_flash_sale", False)
                                    gift_to = inv.get("gift_to")
                                    token = generate_token(inv["pkg_id"])

                                    if is_flash:
                                        mark_user_claimed_flash_sale(chat_id)
                                    
                                    add_history_record(package_cost, inv["pkg_name"], token, chat_id, gift_to or "Self")

                                    new_balance_text = ""
                                    if overpay > 0:
                                        total_bal = add_user_balance(chat_id, overpay)
                                        new_balance_text = f"\n\n💵 *Sisa Kelebihan {fmt_idr(overpay)} Ditambahkan ke Saldo Anda!*\n💰 *Total Saldo Sekarang*: {fmt_idr(total_bal)}"

                                    if "message_id" in inv and inv["message_id"]:
                                        lunas_invoice_text = (
                                            f"✅ *PEMBAYARAN TERVERIFIKASI LUNAS VIA BLU BCA*\n\n"
                                            f"📦 *Paket*: {inv['pkg_name']}\n"
                                            f"💰 *Nominal Transfer*: {fmt_idr(amt)}\n"
                                            f"⏰ *Waktu Lunas*: {datetime.now().strftime('%H:%M:%S WIB')}"
                                            f"{new_balance_text}\n\n"
                                            f"*(Kode Voucher Anda telah dikirim di bawah)*"
                                        )
                                        asyncio.run_coroutine_threadsafe(
                                            app.bot.edit_message_text(
                                                chat_id=chat_id,
                                                message_id=inv["message_id"],
                                                text=lunas_invoice_text,
                                                parse_mode="Markdown"
                                            ),
                                            loop
                                        )

                                    prefix = "🎉 *PEMBAYARAN TERDETEKSI! (Sempat Dibatalkan)*\n\n" if was_cancelled else "🎉 *PEMBAYARAN DITERIMA VIA BLU BCA!*\n\n"
                                    
                                    if gift_to:
                                        success_msg = (
                                            f"{prefix}"
                                            f"💵 *Nominal Masuk*: {fmt_idr(amt)}\n"
                                            f"📦 *Paket*: {inv['pkg_name']}\n"
                                            f"🎁 *Hadiah Untuk*: `{gift_to}`\n"
                                            f"{new_balance_text}\n\n"
                                            f"🔑 *Kode Voucher Hadiah (Tap untuk Copy)*:\n"
                                            f"`{token}`\n\n"
                                            f"📌 Silakan bagikan kode voucher ini kepada {gift_to}!"
                                        )
                                    else:
                                        success_msg = (
                                            f"{prefix}"
                                            f"💵 *Nominal Masuk*: {fmt_idr(amt)}\n"
                                            f"📦 *Paket*: {inv['pkg_name']}\n"
                                            f"{new_balance_text}\n\n"
                                            f"🔑 *Kode Voucher Anda (Tap untuk Copy)*:\n"
                                            f"`{token}`\n\n"
                                            f"📌 *Cara Redeem di Game*:\n"
                                            f"1. Buka Game Pretty Pet Salon -> Store\n"
                                            f"2. Tap Paket *{inv['pkg_name']}*\n"
                                            f"3. Tempel Kode di atas -> Tap *Redeem*"
                                        )

                                    asyncio.run_coroutine_threadsafe(
                                        app.bot.send_message(chat_id=chat_id, text=success_msg, parse_mode="Markdown"),
                                        loop
                                    )
                                    print(f"✅ [LUNAS!] Match {fmt_idr(amt)} -> Token {token} terkirim & Saldo +{fmt_idr(overpay)}!")

            mail.logout()
        except Exception as e:
            print(f"⚠️ Warning IMAP: {e}")

        time.sleep(8)

# --- KEYBOARD MENU UTAMA ---
def get_main_keyboard(user_id: int):
    is_admin = is_user_admin(user_id)
    user_bal = get_user_balance(user_id)

    buttons = []
    for key, item in PACKAGES.items():
        price, is_flash = get_package_price_for_user(user_id, item["base_price"])
        if is_flash:
            label = f"⚡ {item['name']} - {fmt_idr(price)} (Diskon 40%)"
        else:
            label = f"💎 {item['name']} - {fmt_idr(price)}"
        buttons.append([InlineKeyboardButton(label, callback_data=key)])

    buttons.append([
        InlineKeyboardButton("📥 Top-Up Saldo Bot", callback_data="topup_menu"),
        InlineKeyboardButton(f"💰 Saldo: {fmt_idr(user_bal)}", callback_data="my_balance")
    ])
    buttons.append([InlineKeyboardButton("📜 Riwayat Pembelian Saya", callback_data="user_history")])

    # KETAT: Tombol admin HANYA ditambahkan jika user adalah ADMIN
    if is_admin:
        buttons.append([InlineKeyboardButton("👑 PANEL ADMIN (Omzet & Report)", callback_data="admin_panel")])
        
    return InlineKeyboardMarkup(buttons)

# --- COMMAND HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    # Handle Referral code e.g. /start ref_123456
    referred_by = None
    if context.args and len(context.args) > 0:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referred_by = int(arg.replace("ref_", "").strip())
            except Exception:
                pass

    register_user(user_id, user.username, user.full_name, referred_by)

    user_bal = get_user_balance(user_id)
    has_claimed = has_user_claimed_flash_sale(user_id)
    flash_active = is_flash_sale_active()
    
    if flash_active and not has_claimed:
        sale_banner = "⚡ *[FLASH SALE HARI INI - DISKON 40%!]* ⚡\n*(Kesempatan Diskon 40% hanya berlaku 1x per event Flash Sale!)*\n\n"
    elif flash_active and has_claimed:
        sale_banner = "ℹ️ *Anda telah menggunakan hak kuota 1x Flash Sale hari ini. Harga yang tampil adalah harga normal.*\n\n"
    else:
        sale_banner = ""

    welcome_text = (
        f"👑 *Pretty Pet Salon - Auto Payment (blu BCA)*\n\n"
        f"{sale_banner}"
        f"💰 *Saldo Anda*: `{fmt_idr(user_bal)}`\n"
        f"*(Jika Anda beli paket saat saldo cukup, voucher akan dikirim INSTANT!)*\n\n"
        f"Silakan pilih paket Pet Points yang ingin dibeli:"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))

async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_bal = get_user_balance(user.id)
    has_claimed = has_user_claimed_flash_sale(user.id)
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user.id}"

    msg = (
        f"👤 *INFORMASI AKUN TELEGRAM ANDA*:\n\n"
        f"• *Nama*: {user.full_name}\n"
        f"• *Username*: @{user.username if user.username else 'Tidak Ada'}\n"
        f"• *ID TELEGRAM*: `{user.id}`\n"
        f"• *SALDO BOT*: `{fmt_idr(user_bal)}`\n"
        f"• *FLASH SALE*: {'`Sudah Diklaim Hari Ini`' if has_claimed else ('`Tersedia (Aktif Hari Ini)`' if is_flash_sale_active() else '`Tersedia Tiap Tgl Kembar & Tgl 25`')}\n\n"
        f"🔗 *LINK REFERRAL ANDA*:\n"
        f"`{ref_link}`\n"
        f"*(Dapatkan bonus `{fmt_idr(REFERRAL_BONUS)}` Saldo Bot tiap kali teman yang Anda undang melakukan transaksi pertamanya!)*"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def topup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await render_topup_menu(update, update.effective_user.id)

async def render_topup_menu(update_or_query, user_id: int):
    user_bal = get_user_balance(user_id)
    text = (
        f"📥 *TOP-UP SALDO BOT VIA BLU BCA*\n\n"
        f"💰 *Saldo Saat Ini*: `{fmt_idr(user_bal)}`\n\n"
        f"Pilih nominal top-up saldo yang diinginkan:\n"
        f"*(Uang yang di-top-up dapat digunakan untuk beli voucher secara INSTANT tanpa transfer bank lagi)*"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 Rp 10.000", callback_data="topup_10000"), InlineKeyboardButton("💵 Rp 25.000", callback_data="topup_25000")],
        [InlineKeyboardButton("💵 Rp 50.000", callback_data="topup_50000"), InlineKeyboardButton("💵 Rp 100.000", callback_data="topup_100000")],
        [InlineKeyboardButton("✏️ Nominal Custom", callback_data="topup_custom")],
        [InlineKeyboardButton("🔙 Kembali ke Menu Utama", callback_data="start_menu")]
    ])
    if hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update_or_query.message.reply_text(text=text, parse_mode="Markdown", reply_markup=keyboard)

async def render_user_history(update_or_query, user_id: int):
    history = load_history()
    user_records = [item for item in history if item.get("chat_id") == user_id]

    if not user_records:
        text = "📜 *Riwayat Pembelian Anda Masih Kosong.*\n\nBelum ada kode voucher yang Anda beli dari bot ini."
    else:
        lines = ["📜 *RIWAYAT PEMBELIAN VOUCHER ANDA*:\n"]
        recent = user_records[-10:]
        for i, item in enumerate(reversed(recent), 1):
            amt_str = fmt_idr(item['amount']) if item['amount'] > 0 else "Potong Saldo / Free"
            rec_info = f" (🎁 Gift to: {item['recipient']})" if item.get("recipient") and item['recipient'] != "Self" else ""
            lines.append(
                f"{i}. *{item['timestamp']}*\n"
                f"   📦 Paket: {item['pkg_name']}{rec_info}\n"
                f"   💵 Total: {amt_str}\n"
                f"   🔑 Voucher: `{item['token']}`\n"
            )
        lines.append("💡 *Tips*: Tap kode voucher di atas untuk meng-copy secara otomatis.")
        text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali ke Menu Utama", callback_data="start_menu")]])

    if hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update_or_query.message.reply_text(text=text, parse_mode="Markdown", reply_markup=keyboard)

# --- ADMIN-ONLY HANDLERS ---
@admin_only
async def admin_command_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await render_admin_panel(update, update.effective_user.id)

async def render_admin_panel(update_or_query, user_id: int):
    if not is_user_admin(user_id):
        text = "⛔ *AKSES DITOLAK*: Anda bukan Admin."
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="start_menu")]])
        if hasattr(update_or_query, "edit_message_text"):
            await update_or_query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=keyboard)
        return

    history = load_history()
    total_tx = len(history)
    total_omzet = sum([item.get("amount", 0) for item in history])
    claims = load_flash_claims()
    users = load_users()
    coupons = load_coupons()
    
    recent = history[-5:]
    recent_lines = []
    for item in reversed(recent):
        recent_lines.append(f"• {item['timestamp']} | {fmt_idr(item['amount'])} ({item['pkg_name']}) -> `{item['token']}`")

    text = (
        f"👑 *PANEL LAPORAN ADMIN BOT*\n\n"
        f"📊 *STATISTIK PENJUALAN*:\n"
        f"• Total Pengguna Terdaftar: *{len(users)} User*\n"
        f"• Total Transaksi Lunas: *{total_tx} Transaksi*\n"
        f"• Total Omzet Masuk: *{fmt_idr(total_omzet)}*\n"
        f"• Status Flash Sale Hari Ini: *{'AKTIF (Tgl Kembar / Tgl 25)' if is_flash_sale_active() else 'NONAKTIF'}*\n"
        f"• Total Klaim Flash Sale: *{len(claims)} Record*\n"
        f"• Kupon Promo Aktif: *{len(coupons)} Kode*\n\n"
        f"📜 *5 TRANSAKSI TERAKHIR*:\n" + ("\n".join(recent_lines) if recent_lines else "Belum ada transaksi.") + "\n\n"
        f"🛠️ *Perintah Khusus Admin*:\n"
        f"• `/broadcast <pesan>` - Kirim pesan ke semua user\n"
        f"• `/export` - Download laporan transaksi CSV\n"
        f"• `/createcoupon <KODE> <%DISKON> <MAX_KLAIM>` - Buat kupon promo\n"
        f"• `/gen <100/210/460/1250>` - Generate voucher gratis"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh Data", callback_data="admin_panel")],
        [InlineKeyboardButton("🔙 Kembali ke Menu Utama", callback_data="start_menu")]
    ])

    if hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=keyboard)

@admin_only
async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Penggunaan: `/broadcast Pesan pengumuman di sini...`", parse_mode="Markdown")
        return

    broadcast_text = " ".join(context.args)
    users = load_users()
    success_count = 0
    fail_count = 0

    msg = await update.message.reply_text(f"⏳ Sedang menyiarkan pesan ke {len(users)} pengguna...")

    for uid_str in users:
        try:
            await context.bot.send_message(
                chat_id=int(uid_str),
                text=f"📢 *PENGUMUMAN DARI BOT*:\n\n{broadcast_text}",
                parse_mode="Markdown"
            )
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail_count += 1

    await msg.edit_text(
        f"✅ *BROADCAST SELESAI!*\n\n"
        f"• *Berhasil Terkirim*: {success_count} User\n"
        f"• *Gagal/Blocked*: {fail_count} User",
        parse_mode="Markdown"
    )

@admin_only
async def admin_export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = load_history()
    if not history:
        await update.message.reply_text("📜 Riwayat transaksi masih kosong!")
        return

    csv_filepath = "laporan_transaksi.csv"
    with open(csv_filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Timestamp", "Chat ID", "Package Name", "Amount (IDR)", "Voucher Token", "Recipient"])
        for item in history:
            writer.writerow([
                item.get("timestamp", ""),
                item.get("chat_id", ""),
                item.get("pkg_name", ""),
                item.get("amount", 0),
                item.get("token", ""),
                item.get("recipient", "Self")
            ])

    await update.message.reply_document(
        document=open(csv_filepath, "rb"),
        filename="laporan_transaksi.csv",
        caption="📊 *Laporan Riwayat Transaksi Lunas PrettyBot*",
        parse_mode="Markdown"
    )

@admin_only
async def admin_create_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Usage: /createcoupon PROMO10 10 50
    if len(context.args) < 3:
        await update.message.reply_text("Penggunaan: `/createcoupon <KODE> <%DISKON> <MAX_KLAIM>`\nContoh: `/createcoupon DISKON10 10 50`", parse_mode="Markdown")
        return

    code = context.args[0].upper().strip()
    try:
        disc = int(context.args[1])
        max_claims = int(context.args[2])
    except ValueError:
        await update.message.reply_text("Error: Diskon (%) dan Max Klaim harus berupa angka bulat!")
        return

    coupons = load_coupons()
    coupons[code] = {
        "discount_percent": disc,
        "max_claims": max_claims,
        "used_count": 0,
        "users_claimed": []
    }
    save_coupons(coupons)

    await update.message.reply_text(
        f"✅ *BERHASIL MEMBUAT KUPON PROMO!*\n\n"
        f"• *Kode Promo*: `{code}`\n"
        f"• *Diskon*: `{disc}%`\n"
        f"• *Maksimal Klaim*: `{max_claims} User`",
        parse_mode="Markdown"
    )

@admin_only
async def admin_gen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        if not context.args:
            await update.message.reply_text("Penggunaan: `/gen 100` atau `/gen 210` atau `/gen 460` atau `/gen 1250`", parse_mode="Markdown")
            return
        
        pts = int(context.args[0])
        pkg_map = {100: 1, 210: 2, 460: 3, 1250: 4}
        if pts not in pkg_map:
            await update.message.reply_text("Paket tidak ditemukan! Pilih: 100, 210, 460, atau 1250")
            return
        
        pkg_id = pkg_map[pts]
        token = generate_token(pkg_id)
        
        add_history_record(0, f"Manual Gen {pts} PP", token, user_id)

        msg = f"🔑 *KODE VOUCHER MANUAL ADMIN ({pts} PP)*:\n`{token}`"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# --- TEXT MESSAGE HANDLER (FOR INTERACTIVE INPUTS) ---
async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    awaiting = context.user_data.get("awaiting_input")
    text = update.message.text.strip()

    if awaiting == "custom_topup":
        context.user_data["awaiting_input"] = None
        if not text.isdigit() or int(text) < 10000:
            await update.message.reply_text("⚠️ Nominal top-up minimal adalah Rp 10.000 dan harus berupa angka murni!")
            return
        await process_topup_order(update, context, int(text))

    elif awaiting == "coupon_code":
        context.user_data["awaiting_input"] = None
        coupons = load_coupons()
        code = text.upper()

        if code not in coupons:
            await update.message.reply_text("❌ Kode promo tidak ditemukan atau salah!")
            return
        
        c_info = coupons[code]
        if c_info["used_count"] >= c_info["max_claims"]:
            await update.message.reply_text("❌ Kuota klaim untuk kode promo ini telah habis!")
            return
        
        if str(user_id) in c_info.get("users_claimed", []):
            await update.message.reply_text("⚠️ Anda sudah pernah menggunakan kode promo ini!")
            return

        context.user_data["applied_coupon"] = {
            "code": code,
            "discount_percent": c_info["discount_percent"]
        }
        await update.message.reply_text(f"🎉 *KODE PROMO BERHASIL DIPASANG!* (Diskon {c_info['discount_percent']}%)\n\nSilakan pilih paket yang ingin Anda beli:", parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))

    elif awaiting == "gift_recipient":
        context.user_data["awaiting_input"] = None
        pkg_key = context.user_data.get("pending_gift_pkg")
        if not pkg_key or pkg_key not in PACKAGES:
            await update.message.reply_text("Terjadi kesalahan. Silakan pilih paket kembali.")
            return

        context.user_data["gift_recipient"] = text
        await process_package_order(update, context, pkg_key, gift_recipient=text)

# --- PROCESS TOPUP & PACKAGES ---
async def process_topup_order(update_or_query, context: ContextTypes.DEFAULT_TYPE, amount: int):
    user_id = update_or_query.effective_user.id
    unique_digit = random.randint(100, 999)
    exact_amount = amount + unique_digit

    msg_id = update_or_query.message.message_id if hasattr(update_or_query, "message") and update_or_query.message else None

    PENDING_INVOICES[exact_amount] = {
        "chat_id": user_id,
        "message_id": msg_id,
        "is_topup": True,
        "topup_amount": exact_amount,
        "timestamp": time.time()
    }
    save_pending_orders(PENDING_INVOICES)

    clean_amount = str(exact_amount)
    formatted_display = f"Rp {exact_amount:,},00".replace(",", ".")

    invoice_text = (
        f"📥 *PESANAN TOP-UP SALDO BOT*\n\n"
        f"💰 *NOMINAL TOP-UP PAS*:\n"
        f"👉 `{clean_amount}` 👈 *(Tap angka untuk copy {formatted_display})*\n\n"
        f"⚠️ *PENTING*: Transfer **HARUS PAS SAMPAI 3 DIGIT TERAKHIR** (`{clean_amount}`) "
        f"agar nominal 100% otomatis ditambahkan ke Saldo Bot Anda!\n\n"
        f"🏦 *REKENING TUJUAN (blu by BCA)*:\n"
        f"• No. Rekening: `{BLU_ACCOUNT_NUMBER}`\n"
        f"• Atas Nama: **{BLU_ACCOUNT_NAME}**\n\n"
        f"⏳ *Status*: Menunggu email `receipts@blubybcadigital.id` masuk...\n"
        f"*(Nominal Rp {exact_amount:,} akan 100% masuk menjadi saldo Anda)*".replace(",", ".")
    )
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batal Top-Up", callback_data="cancel_order")]])

    if hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text=invoice_text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update_or_query.message.reply_text(text=invoice_text, parse_mode="Markdown", reply_markup=keyboard)

async def process_package_order(update_or_query, context: ContextTypes.DEFAULT_TYPE, pkg_key: str, gift_recipient: str = None):
    user_id = update_or_query.effective_user.id
    pkg_info = PACKAGES[pkg_key]
    
    coupon_info = context.user_data.get("applied_coupon", {})
    coupon_disc = coupon_info.get("discount_percent", 0)

    item_price, is_flash = get_package_price_for_user(user_id, pkg_info["base_price"], coupon_disc)
    user_bal = get_user_balance(user_id)

    # 1. POTONG SALDO USER TERLEBIH DAHULU (JIKA CUKUP)
    if user_bal >= item_price:
        deduct_user_balance(user_id, item_price)
        rem_bal = get_user_balance(user_id)
        token = generate_token(pkg_info["id"])
        
        if is_flash:
            mark_user_claimed_flash_sale(user_id)

        # Mark coupon used if applied
        if coupon_info.get("code"):
            coupons = load_coupons()
            code = coupon_info["code"]
            if code in coupons:
                coupons[code]["used_count"] += 1
                if "users_claimed" not in coupons[code]:
                    coupons[code]["users_claimed"] = []
                coupons[code]["users_claimed"].append(str(user_id))
                save_coupons(coupons)
            context.user_data["applied_coupon"] = None

        trigger_referral_reward_if_eligible(user_id, context.application, asyncio.get_running_loop())
        add_history_record(item_price, pkg_info["name"], token, user_id, gift_recipient or "Self")

        gift_tag = f"\n🎁 *Dikirim Sebagai Hadiah Ke*: `{gift_recipient}`" if gift_recipient else ""

        instant_text = (
            f"🎉 *PEMBAYARAN SUKSES VIA SALDO BOT!*\n\n"
            f"📦 *Paket*: {pkg_info['name']}{gift_tag}\n"
            f"💰 *Harga*: {fmt_idr(item_price)} {'*(Diskon Flash Sale/Kupon Applied)*' if is_flash or coupon_disc > 0 else ''}\n"
            f"💳 *Sisa Saldo Anda*: {fmt_idr(rem_bal)}\n\n"
            f"🔑 *Kode Voucher (Tap untuk Copy)*:\n"
            f"`{token}`\n\n"
            f"📌 *Cara Redeem di Game*:\n"
            f"1. Buka Game Pretty Pet Salon -> Store\n"
            f"2. Tap Paket *{pkg_info['name']}*\n"
            f"3. Tempel Kode di atas -> Tap *Redeem*"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali ke Menu Utama", callback_data="start_menu")]])
        if hasattr(update_or_query, "edit_message_text"):
            await update_or_query.edit_message_text(text=instant_text, parse_mode="Markdown", reply_markup=keyboard)
        else:
            await update_or_query.message.reply_text(text=instant_text, parse_mode="Markdown", reply_markup=keyboard)
        return

    # 2. BUAT INVOICE TRANSFER JIKA SALDO TIDAK CUKUP
    needed_target = max(item_price, MINIMUM_TRANSFER)
    unique_digit = random.randint(100, 999)
    exact_amount = needed_target + unique_digit
    overpay = exact_amount - item_price

    msg_id = update_or_query.message.message_id if hasattr(update_or_query, "message") and update_or_query.message else None

    PENDING_INVOICES[exact_amount] = {
        "chat_id": user_id,
        "message_id": msg_id,
        "pkg_id": pkg_info["id"],
        "pkg_name": pkg_info["name"],
        "package_cost": item_price,
        "overpay": overpay,
        "is_flash_sale": is_flash,
        "gift_to": gift_recipient,
        "timestamp": time.time()
    }
    save_pending_orders(PENDING_INVOICES)

    print(f"🛒 [ORDER DIBUAT] User {user_id} memesan {pkg_info['name']} -> TF Required: {fmt_idr(exact_amount)} (Flash Sale: {is_flash})")

    overpay_info = ""
    if overpay > 0:
        overpay_info = (
            f"💡 *INFO SALDO & MINIMAL TF*:\n"
            f"Karena minimal transfer bank adalah *Rp 10.000*, Anda harus transfer sebesar `{fmt_idr(exact_amount)}`.\n"
            f"Sisa kembalian sebesar *{fmt_idr(overpay)}* akan **OTOMATIS MASUK KE SALDO BOT ANDA** untuk pembelian berikutnya!\n\n"
        )

    sale_tag = "⚡ *[FLASH SALE DISKON 40%]* ⚡\n" if is_flash else ""
    coupon_tag = f"🏷️ *Kupon Promo ({coupon_disc}% OFF Applied)*\n" if coupon_disc > 0 else ""
    gift_info = f"🎁 *Hadiah Untuk*: `{gift_recipient}`\n" if gift_recipient else ""

    clean_amount = str(exact_amount) 
    formatted_display = f"Rp {exact_amount:,},00".replace(",", ".")

    invoice_text = (
        f"🛒 *PESANAN DIBUAT (MENUNGGU PEMBAYARAN)*\n"
        f"{sale_tag}{coupon_tag}{gift_info}"
        f"📦 *Paket*: {pkg_info['name']}\n"
        f"🏷️ *Harga Paket*: {fmt_idr(item_price)}\n\n"
        f"💰 *NOMINAL TRANSFER PAS*:\n"
        f"👉 `{clean_amount}` 👈 *(Tap angka untuk copy {formatted_display})*\n\n"
        f"{overpay_info}"
        f"⚠️ *PENTING*: Transfer **HARUS PAS SAMPAI 3 DIGIT TERAKHIR** (`{clean_amount}`) "
        f"agar email notifikasi blu BCA terdeteksi 100% otomatis oleh sistem!\n\n"
        f"🏦 *REKENING TUJUAN (blu by BCA)*:\n"
        f"• No. Rekening: `{BLU_ACCOUNT_NUMBER}`\n"
        f"• Atas Nama: **{BLU_ACCOUNT_NAME}**\n\n"
        f"⏳ *Status*: Menunggu email `receipts@blubybcadigital.id` masuk...\n"
        f"*(Begitu transfer masuk, Bot akan mengirimkan kode voucher secara otomatis)*"
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Batal Pesanan", callback_data="cancel_order")]
    ])
    if hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text=invoice_text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update_or_query.message.reply_text(text=invoice_text, parse_mode="Markdown", reply_markup=keyboard)

# --- CALLBACK QUERY HANDLER ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.message.chat_id
    pkg_key = query.data

    if pkg_key in PACKAGES:
        # Prompt option: Self or Gift
        pkg_info = PACKAGES[pkg_key]
        text = f"📦 Anda memilih paket *{pkg_info['name']}*.\nApakah paket ini untuk Anda sendiri atau hadiah teman?"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Beli untuk Diri Sendiri", callback_data=f"buy_self_{pkg_key}")],
            [InlineKeyboardButton("🎁 Kirim Hadiah ke Teman", callback_data=f"buy_gift_{pkg_key}")],
            [InlineKeyboardButton("🔙 Kembali", callback_data="start_menu")]
        ])
        await query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=keyboard)

    elif pkg_key.startswith("buy_self_"):
        real_key = pkg_key.replace("buy_self_", "")
        await process_package_order(query, context, real_key)

    elif pkg_key.startswith("buy_gift_"):
        real_key = pkg_key.replace("buy_gift_", "")
        context.user_data["awaiting_input"] = "gift_recipient"
        context.user_data["pending_gift_pkg"] = real_key
        await query.edit_message_text(
            text="🎁 *PEMBELIAN HADIAH VOUCHER*\n\nSilakan ketik Username (contoh: `@username`) atau Nama teman yang akan Anda beri hadiah:",
            parse_mode="Markdown"
        )

    elif pkg_key == "topup_menu":
        await render_topup_menu(query, user_id)

    elif pkg_key.startswith("topup_"):
        val_str = pkg_key.replace("topup_", "")
        if val_str == "custom":
            context.user_data["awaiting_input"] = "custom_topup"
            await query.edit_message_text(
                text="✏️ *INPUT NOMINAL TOP-UP CUSTOM*\n\nSilakan ketik nominal top-up yang Anda inginkan (minimal Rp 10.000, berupa angka tanpa titik/koma):",
                parse_mode="Markdown"
            )
        else:
            amt = int(val_str)
            await process_topup_order(query, context, amt)

    elif pkg_key == "cancel_order":
        for amt, inv in list(PENDING_INVOICES.items()):
            if inv["chat_id"] == query.message.chat_id:
                CANCELLED_INVOICES[amt] = PENDING_INVOICES.pop(amt)
                save_pending_orders(PENDING_INVOICES)
                break

        cancel_text = "❌ *PESANAN DIBATALKAN.*\n\n(Catatan: Jika Anda sebenarnya sudah terlanjur transfer, sistem blu BCA kami akan TETAP otomatis mendeteksi, mengirim voucher/top-up saldo Anda)."
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali ke Menu Utama", callback_data="start_menu")]])
        await query.edit_message_text(text=cancel_text, parse_mode="Markdown", reply_markup=keyboard)

    elif pkg_key == "user_history":
        await render_user_history(query, query.message.chat_id)

    elif pkg_key == "my_balance":
        user_bal = get_user_balance(user_id)
        bal_text = (
            f"💰 *SALDO BOT ANDA*: `{fmt_idr(user_bal)}`\n\n"
            f"📌 *Informasi Saldo*:\n"
            f"• Saldo otomatis bertambah jika Anda melakukan Top-Up atau transfer lebih dari harga paket.\n"
            f"• Saldo dapat digunakan untuk otomatis memotong harga saat beli voucher berikutnya secara INSTANT tanpa perlu transfer bank lagi!"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Top-Up Saldo Sekarang", callback_data="topup_menu")],
            [InlineKeyboardButton("🔙 Kembali ke Menu Utama", callback_data="start_menu")]
        ])
        await query.edit_message_text(text=bal_text, parse_mode="Markdown", reply_markup=keyboard)

    elif pkg_key == "admin_panel":
        await render_admin_panel(query, query.message.chat_id)

    elif pkg_key == "start_menu":
        user_bal = get_user_balance(user_id)
        has_claimed = has_user_claimed_flash_sale(user_id)
        flash_active = is_flash_sale_active()
        
        if flash_active and not has_claimed:
            sale_banner = "⚡ *[FLASH SALE HARI INI - DISKON 40%!]* ⚡\n\n"
        else:
            sale_banner = ""

        welcome_text = (
            f"👑 *Pretty Pet Salon - Auto Payment (blu BCA)*\n\n"
            f"{sale_banner}"
            f"💰 *Saldo Anda*: `{fmt_idr(user_bal)}`\n\n"
            f"Silakan pilih paket Pet Points yang ingin dibeli:"
        )
        await query.edit_message_text(welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))

# --- POST INIT & MAIN ---
async def post_init(application: Application) -> None:
    # 1. PERINTAH BOT DEFAULT UNTUK USER BIASA
    user_commands = [
        BotCommand("start", "👑 Beli Pet Points & Menu Utama"),
        BotCommand("topup", "📥 Top-Up Saldo Bot"),
        BotCommand("riwayat", "📜 Lihat Riwayat Pembelian Saya"),
        BotCommand("myid", "👤 Cek ID, Saldo & Link Referral"),
    ]
    await application.bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())

    # 2. PERINTAH BOT KHUSUS UNTUK ADMIN (ISOLASI ADMIN TELEGRAM)
    if ADMIN_TELEGRAM_ID and str(ADMIN_TELEGRAM_ID).isdigit():
        admin_commands = [
            BotCommand("start", "👑 Beli Pet Points & Menu Utama"),
            BotCommand("topup", "📥 Top-Up Saldo Bot"),
            BotCommand("riwayat", "📜 Lihat Riwayat Pembelian"),
            BotCommand("myid", "👤 Cek ID & Saldo"),
            BotCommand("admin", "👑 Panel Laporan Admin"),
            BotCommand("gen", "🔑 Generate Voucher Manual"),
            BotCommand("broadcast", "📢 Kirim Broadcast ke Semua User"),
            BotCommand("export", "📊 Export Laporan Transaksi CSV"),
            BotCommand("createcoupon", "🏷️ Buat Kode Promo Baru"),
        ]
        try:
            await application.bot.set_my_commands(
                admin_commands,
                scope=BotCommandScopeChat(chat_id=int(ADMIN_TELEGRAM_ID))
            )
            print("👑 Berhasil mendaftarkan Menu Perintah Khusus Admin di Telegram!")
        except Exception as e:
            print(f"⚠️ Warning set admin scope commands: {e}")

    print("✅ Berhasil mendaftarkan Menu Bot Interaktif di Telegram!")

    loop = asyncio.get_running_loop()
    email_thread = threading.Thread(target=check_gmail_for_blu_transfers, args=(application, loop), daemon=True)
    email_thread.start()

def main():
    if TELEGRAM_BOT_TOKEN == "MASUKKAN_TOKEN_BOT_TELEGRAM_KAMU" or not TELEGRAM_BOT_TOKEN:
        print("ERROR: Harap isi TELEGRAM_BOT_TOKEN di .env!")
        return

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # User Command Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid_command))
    app.add_handler(CommandHandler("topup", topup_command))
    app.add_handler(CommandHandler("riwayat", riwayat_command := lambda u, c: render_user_history(u, u.effective_user.id)))
    app.add_handler(CommandHandler("history", riwayat_command))

    # Admin Command Handlers (Isolasi & Guard Admin Only)
    app.add_handler(CommandHandler("admin", admin_command_panel))
    app.add_handler(CommandHandler("gen", admin_gen))
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(CommandHandler("export", admin_export_csv))
    app.add_handler(CommandHandler("createcoupon", admin_create_coupon))

    # Text Input Handler for interactive inputs
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))

    # Callback Query Handler
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🤖 Bot Telegram Auto-Payment (blu BCA & Webhook Auto-Deploy) Aktif...")
    app.run_polling()

if __name__ == "__main__":
    main()