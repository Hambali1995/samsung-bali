# -*- coding: utf-8 -*-
import os, json, requests, threading, re, logging
from datetime import datetime, timedelta
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
GREEN_API_ID = os.getenv("GREEN_API_ID", "710722705231")
GREEN_API_TOKEN = os.getenv("GREEN_API_TOKEN")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS") or os.getenv("TELEGRAM_ADMIN_ID") or ""
if ADMIN_IDS_STR:
    try:
        ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.replace(";",",").split(",") if x.strip().isdigit()]
    except:
        ADMIN_IDS = [7962377902, 8538844365, 8877282096]
else:
    ADMIN_IDS = [7962377902, 8538844365, 8877282096]

DB_FILE = "bot_database.json"
DB_FILE_PERSISTENT = "/data/bot_database.json"
WA_HISTORY_FILE = "wa_history.json"
WA_HISTORY_PERSISTENT = "/data/wa_history.json"
PORT = int(os.getenv("PORT", 8080))
RAILWAY_URL = "https://samsung-bali-production.up.railway.app"

# ========== SUPABASE / PASADATA ==========
# Tabel: bot_data
# Kolom yang dipakai: id (int8), data_json (jsonb)
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_TABLE = "bot_data"

def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

def supabase_enabled():
    return bool(SUPABASE_URL and SUPABASE_KEY)

def load_db_from_supabase():
    """Ambil database utama dari tabel bot_data.data_json."""
    if not supabase_enabled():
        return None
    try:
        url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
        params = {"select": "id,data_json", "order": "id.asc", "limit": "1"}
        r = requests.get(url, headers=supabase_headers(), params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        payload = rows[0].get("data_json")
        if not payload:
            return None
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            logger.error("Supabase data_json bukan object JSON")
            return None
        return payload
    except Exception as e:
        logger.error(f"Supabase LOAD ERROR: {type(e).__name__}: {e}")
        return None

def save_db_to_supabase(data):
    """Simpan seluruh state bot ke satu baris data_json."""
    if not supabase_enabled():
        return False
    try:
        url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_TABLE}"
        params = {"select": "id", "order": "id.asc", "limit": "1"}
        r = requests.get(url, headers=supabase_headers(), params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()

        body = {"data_json": data}
        if rows:
            row_id = rows[0]["id"]
            r = requests.patch(
                f"{url}?id=eq.{row_id}",
                headers=supabase_headers(),
                json=body,
                timeout=15,
            )
        else:
            r = requests.post(url, headers=supabase_headers(), json=body, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Supabase SAVE ERROR: {type(e).__name__}: {e}")
        return False


# ========== TABEL BLACKLIST TERPISAH ==========
BLACKLIST_TABLE = "blacklist"

def load_blacklist_from_supabase():
    """Ambil semua nomor blacklist dari tabel khusus blacklist"""
    if not supabase_enabled():
        return None
    try:
        url = f"{SUPABASE_URL}/rest/v1/{BLACKLIST_TABLE}"
        params = {"select": "number", "order": "id.asc", "limit": "10000"}
        r = requests.get(url, headers=supabase_headers(), params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()
        numbers = [row.get("number") for row in rows if row.get("number")]
        return numbers
    except Exception as e:
        logger.error(f"BLACKLIST LOAD ERROR: {e}")
        return None

def add_blacklist_to_supabase(number):
    """Tambah 1 nomor ke tabel blacklist"""
    if not supabase_enabled():
        return False
    try:
        url = f"{SUPABASE_URL}/rest/v1/{BLACKLIST_TABLE}"
        body = {"number": number}
        r = requests.post(url, headers=supabase_headers(), json=body, timeout=15)
        if r.status_code == 409:  # duplicate
            return True
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"BLACKLIST ADD ERROR {number}: {e}")
        return False

def remove_blacklist_from_supabase(number):
    """Hapus nomor dari tabel blacklist"""
    if not supabase_enabled():
        return False
    try:
        url = f"{SUPABASE_URL}/rest/v1/{BLACKLIST_TABLE}"
        params = {"number": f"eq.{number}"}
        r = requests.delete(url, headers=supabase_headers(), params=params, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"BLACKLIST REMOVE ERROR {number}: {e}")
        return False

def get_all_blacklist():
    """Ambil blacklist dari tabel khusus dulu, fallback ke data_json lama"""
    # Coba dari tabel baru
    bl_new = load_blacklist_from_supabase()
    if bl_new is not None:
        return bl_new
    # Fallback ke data lama di db global
    return db.get("blacklist", [])


REKENING_TEXT = """
💳 TOP UP SALDO 

🏦 SEABANK
   901040978290 - HAMBALI

💰 DANA
   083824101264 - HAMBALI

💳 GOPAY
   083824101264 - HAMBALI

📸 Kirim foto bukti transfer di sini ya bos!
"""


PAKET_TAMBAH = {
    "1minggu": {"nama": "1 MINGGU", "harga": 25000, "hari": 7, "kuota": 2},
    "2minggu": {"nama": "2 MINGGU", "harga": 50000, "hari": 14, "kuota": 3},
    "3minggu": {"nama": "3 MINGGU", "harga": 75000, "hari": 21, "kuota": 3},
    "1bulan": {"nama": "1 BULAN", "harga": 100000, "hari": 30, "kuota": 3},
    "2bulan": {"nama": "2 BULAN", "harga": 180000, "hari": 60, "kuota": 3},
    "6bulan": {"nama": "6 BULAN", "harga": 500000, "hari": 180, "kuota": 4},
    "unlimited": {"nama": "UNLIMITED", "harga": 2000000, "hari": 3650, "kuota": 6},
}
PAKET_CARI = {
    "1minggu": {"nama": "1 MINGGU", "harga": 15000, "hari": 7},
    "2minggu": {"nama": "2 MINGGU", "harga": 25000, "hari": 14},
    "3minggu": {"nama": "3 MINGGU", "harga": 35000, "hari": 21},
    "1bulan": {"nama": "1 BULAN", "harga": 50000, "hari": 30},
    "2bulan": {"nama": "2 BULAN", "harga": 80000, "hari": 60},
    "6bulan": {"nama": "6 BULAN", "harga": 250000, "hari": 180},
    "unlimited": {"nama": "UNLIMITED", "harga": 1000000, "hari": 3650},
}



LIST_PROVINSI = [
    {"id": "11", "nama": "ACEH"}, {"id": "12", "nama": "SUMATERA UTARA"}, {"id": "13", "nama": "SUMATERA BARAT"},
    {"id": "14", "nama": "RIAU"}, {"id": "15", "nama": "JAMBI"}, {"id": "16", "nama": "SUMATERA SELATAN"},
    {"id": "17", "nama": "BENGKULU"}, {"id": "18", "nama": "LAMPUNG"}, {"id": "19", "nama": "KEP. BANGKA BELITUNG"},
    {"id": "21", "nama": "KEP. RIAU"}, {"id": "31", "nama": "DKI JAKARTA"}, {"id": "32", "nama": "JAWA BARAT"},
    {"id": "33", "nama": "JAWA TENGAH"}, {"id": "34", "nama": "DI YOGYAKARTA"}, {"id": "35", "nama": "JAWA TIMUR"},
    {"id": "36", "nama": "BANTEN"}, {"id": "51", "nama": "BALI"}, {"id": "52", "nama": "NUSA TENGGARA BARAT"},
    {"id": "53", "nama": "NUSA TENGGARA TIMUR"}, {"id": "61", "nama": "KALIMANTAN BARAT"},
    {"id": "62", "nama": "KALIMANTAN TENGAH"}, {"id": "63", "nama": "KALIMANTAN SELATAN"},
    {"id": "64", "nama": "KALIMANTAN TIMUR"}, {"id": "65", "nama": "KALIMANTAN UTARA"},
    {"id": "71", "nama": "SULAWESI UTARA"}, {"id": "72", "nama": "SULAWESI TENGAH"}, {"id": "73", "nama": "SULAWESI SELATAN"},
    {"id": "74", "nama": "SULAWESI TENGGARA"}, {"id": "75", "nama": "GORONTALO"}, {"id": "76", "nama": "SULAWESI BARAT"},
    {"id": "81", "nama": "MALUKU"}, {"id": "82", "nama": "MALUKU UTARA"}, {"id": "91", "nama": "PAPUA"},
    {"id": "92", "nama": "PAPUA BARAT"}, {"id": "93", "nama": "PAPUA SELATAN"}, {"id": "94", "nama": "PAPUA TENGAH"},
    {"id": "95", "nama": "PAPUA PEGUNGAN"}, {"id": "96", "nama": "PAPUA BARAT DAYA"},
]

def load_db():
    # PasaData/Supabase adalah database utama jika env tersedia.
    remote = load_db_from_supabase()
    if remote is not None:
        data = remote
        if "langganan" not in data: data["langganan"] = {}
        if "langganan_cari" not in data: data["langganan_cari"] = {}
        if "blacklist" not in data: data["blacklist"] = []
        if "pending_hapus_kota" not in data: data["pending_hapus_kota"] = []
        if "user_info" not in data: data["user_info"] = {}
        for uid, subs in data["langganan"].items():
            if isinstance(subs, list):
                for item in subs:
                    if item.get("expire"):
                        try: item["expire"] = datetime.fromisoformat(item["expire"])
                        except: item["expire"] = None
            elif isinstance(subs, dict):
                if subs.get("expire"):
                    try: subs["expire"] = datetime.fromisoformat(subs["expire"])
                    except: subs["expire"] = None
                data["langganan"][uid] = [subs]
        return data

    # Fallback hanya jika Supabase belum dikonfigurasi/gagal.
    for p in [DB_FILE_PERSISTENT, DB_FILE]:
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "langganan" not in data: data["langganan"] = {}
                if "langganan_cari" not in data: data["langganan_cari"] = {}
                if "blacklist" not in data: data["blacklist"] = []
                if "pending_hapus_kota" not in data: data["pending_hapus_kota"] = []
                if "user_info" not in data: data["user_info"] = {}
                for uid, subs in data["langganan"].items():
                    if isinstance(subs, list):
                        for item in subs:
                            if item.get("expire"):
                                try: item["expire"] = datetime.fromisoformat(item["expire"])
                                except: item["expire"] = None
                    elif isinstance(subs, dict):
                        if subs.get("expire"):
                            try: subs["expire"] = datetime.fromisoformat(subs["expire"])
                            except: subs["expire"] = None
                        data["langganan"][uid] = [subs]
                return data
        except Exception as e:
            logger.error(f"Local DB LOAD ERROR: {e}")
            continue
    return {"user_info": {}, "langganan": {}, "langganan_cari": {}, "blacklist": [], "pending_hapus_kota": []}

def save_db():
    # Buat salinan serializable tanpa merusak datetime yang dipakai runtime.
    tmp = json.loads(json.dumps(db, default=str))

    # Simpan ke PasaData/Supabase sebagai sumber utama.
    remote_ok = save_db_to_supabase(tmp)

    # JSON lokal tetap disimpan sebagai backup/fallback.
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(tmp, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Local DB SAVE ERROR: {e}")
    try:
        os.makedirs(os.path.dirname(DB_FILE_PERSISTENT), exist_ok=True)
        with open(DB_FILE_PERSISTENT, "w", encoding="utf-8") as f:
            json.dump(tmp, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Persistent DB SAVE ERROR: {e}")

    if supabase_enabled() and not remote_ok:
        logger.warning("⚠️ Supabase gagal disimpan; JSON lokal masih tersimpan sebagai backup")
    return remote_ok if supabase_enabled() else True

def normalize_number(num):
    clean=''.join(filter(str.isdigit, str(num)))
    if not clean: return None
    if clean.startswith("62"):
        clean="0"+clean[2:]
    if clean.startswith("8"):
        clean="0"+clean
    return clean if len(clean)>=9 else None


# === RESET BLACKLIST LAMA ===
# Hapus 29 nomor lama dari data_json biar bersih
def reset_old_blacklist():
    try:
        if "blacklist" in db and db["blacklist"]:
            db["blacklist"] = []
            save_db()
            logger.info("✅ Old blacklist (29 nomor) di data_json sudah dihapus")
    except Exception as e:
        logger.error(f"reset old blacklist error: {e}")

# Jalankan reset sekali saat bot start
reset_old_blacklist()

def get_all_blacklist():
    """Ambil blacklist HANYA dari tabel khusus blacklist (yang baru)"""
    bl_new = load_blacklist_from_supabase()
    if bl_new is not None:
        return bl_new
    return []  # Jika tabel baru kosong, return kosong (29 lama sudah dihapus)



def add_blacklist(num):
    clean=normalize_number(num)
    if not clean: return False, None
    # Cek di tabel baru
    existing = get_all_blacklist()
    if clean in existing:
        return False, clean
    alt="62"+clean[1:] if clean.startswith("0") else clean
    if alt in existing:
        return False, clean
    # Simpan ke tabel khusus (yang baru)
    ok = add_blacklist_to_supabase(clean)
    # Tetap simpan di local db sebagai backup tapi tabel baru yang utama
    if "blacklist" not in db:
        db["blacklist"]=[]
    if clean not in db["blacklist"]:
        db["blacklist"].append(clean)
        db["blacklist"]=list(dict.fromkeys(db["blacklist"]))
        save_db()
    return ok, clean

def get_all_blacklist_for_cek():
    return get_all_blacklist()


def load_wa_history():
    for p in [WA_HISTORY_PERSISTENT, WA_HISTORY_FILE]:
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
        except: continue
    return []

def save_wa_history(h):
    try:
        if len(h)>10000: h=h[-10000:]
        with open(WA_HISTORY_FILE, "w", encoding="utf-8") as f: json.dump(h,f,indent=2,ensure_ascii=False)
        try:
            os.makedirs(os.path.dirname(WA_HISTORY_PERSISTENT), exist_ok=True)
            with open(WA_HISTORY_PERSISTENT, "w", encoding="utf-8") as f: json.dump(h,f,indent=2,ensure_ascii=False)
        except: pass
    except: pass

db = load_db()
# Jika Supabase aktif tetapi tabel masih kosong, seed dari DB yang sedang dipakai.
if supabase_enabled() and load_db_from_supabase() is None:
    save_db_to_supabase(json.loads(json.dumps(db, default=str)))
flask_app = Flask(__name__)

# Fail fast with a clear configuration error instead of an obscure Telegram crash.
if not TOKEN:
    raise RuntimeError(
        "TOKEN/BOT_TOKEN/TELEGRAM_BOT_TOKEN belum diatur. "
        "Tambahkan environment variable TOKEN di deployment."
    )

def check_location_match(text_upper, kotas):
    """KOTA/KABUPATEN dan KECAMATAN wajib muncul pada baris terpisah.
    Urutannya wajib: baris KOTA/KABUPATEN lalu baris KECAMATAN.
    Contoh valid:
        SERANG
        CIPOCOK JAYA
    Contoh tidak valid:
        SERANG
    """
    # Pertahankan struktur ENTER/baris dari pesan WhatsApp.
    lines = [line.strip() for line in text_upper.splitlines() if line.strip()]

    for k in kotas:
        parts = [p.strip() for p in k.split("|")]
        if len(parts) < 3:
            continue

        kab_clean = parts[1].upper().replace("KABUPATEN ", "").replace("KOTA ", "").strip()
        kec_clean = parts[2].upper().strip()

        # "Semua Kecamatan" tidak boleh membuat kota saja lolos.
        if kec_clean == "SEMUA KECAMATAN":
            continue

        if len(kab_clean) < 3 or len(kec_clean) < 3:
            continue

        # Cari KOTA/KABUPATEN dan KECAMATAN pada BARIS YANG BERBEDA.
        # Tidak menerima "SERANG CIPOCOK JAYA" dalam satu baris.
        kota_line_indexes = [
            i for i, line in enumerate(lines)
            if kab_clean in line
        ]

        for kota_idx in kota_line_indexes:
            # Kecamatan harus berada pada baris setelah baris kota.
            for kec_idx in range(kota_idx + 1, len(lines)):
                if kec_clean in lines[kec_idx]:
                    return True, f"{parts[1]} | {parts[2]}"

    return False, ""

def send_tg_message(chat_id, text, wa_number=None):
    if not TOKEN:
        logger.error("TG ERROR: TOKEN kosong")
        return False
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        reply_markup = None
        if wa_number:
            # Format nomor untuk link WA
            clean = ''.join(filter(str.isdigit, wa_number))
            if clean.startswith("0"):
                clean = "62" + clean[1:]
            elif not clean.startswith("62"):
                clean = "62" + clean
            if len(clean) >= 10:
                reply_markup = {"inline_keyboard": [[
                    {"text": "💬 Chat Pengirim di WA", "url": f"https://wa.me/{clean}"}
                ]]}

        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
            "parse_mode": "Markdown"
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        r = requests.post(url, json=payload, timeout=10)
        if not r.ok:
            logger.error(f"TG SEND ERROR chat_id={chat_id} status={r.status_code} body={r.text[:500]}")
            # Coba kirim tanpa parse_mode
            if "parse_mode" in payload:
                del payload["parse_mode"]
                r2 = requests.post(url, json=payload, timeout=10)
                if r2.ok:
                    return True
            return False
        return True
    except Exception as e:
        logger.error(f"TG SEND EXCEPTION chat_id={chat_id}: {type(e).__name__}: {e}")
        return False

@flask_app.route("/whatsapp-webhook", methods=["POST"])
def whatsapp_webhook():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return "ok", 200
        
        # AMBIL DATA DARI GREEN API FORMAT
        sender_data = data.get("senderData", {}) or {}
        message_data = data.get("messageData", {}) or {}
        
        sender_number = ""
        if sender_data.get("sender"):
            sender_number = sender_data.get("sender")
        elif sender_data.get("chatId"):
            sender_number = sender_data.get("chatId")
        elif message_data.get("sender"):
            sender_number = message_data.get("sender")
        
        # Clean nomor
        if "@" in sender_number:
            sender_number = sender_number.split("@")[0]
        sender_number = ''.join(filter(str.isdigit, sender_number))
        
        # Format nomor (0xxx)
        if sender_number.startswith("62"):
            sender_number_formatted = "0" + sender_number[2:]
        else:
            sender_number_formatted = sender_number
        
        group_name = sender_data.get("chatName") or sender_data.get("chatId") or "Grup WA"
        sender_name = sender_data.get("senderName") or sender_data.get("senderContactName") or "Pengirim WA"
        
        # Ambil teks pesan
        text = ""
        ttype = data.get("typeMessage", "")
        
        # Coba ambil dari berbagai format
        if ttype == "textMessage":
            text = message_data.get("textMessageData", {}).get("textMessage", "")
        elif ttype == "extendedTextMessage":
            text = message_data.get("extendedTextMessageData", {}).get("text", "")
        elif ttype == "imageMessage":
            text = message_data.get("imageMessageData", {}).get("caption", "")
        elif ttype == "documentMessage":
            text = message_data.get("documentMessageData", {}).get("caption", "")
        elif ttype == "audioMessage":
            text = "🎵 *Pesan Suara*"
        elif ttype == "videoMessage":
            text = "🎬 *Pesan Video*"
        else:
            if "textMessageData" in message_data:
                text = message_data["textMessageData"].get("textMessage", "")
            elif "extendedTextMessageData" in message_data:
                text = message_data["extendedTextMessageData"].get("text", "")
            elif "caption" in message_data:
                text = message_data.get("caption", "")
        
        if not text:
            return "ok", 200
        
        # Bersihkan text dari HTML/XML tags
        clean_text = re.sub(r'<[^>]+>', '', text)
        text_upper = clean_text.upper()
        
        # Simpan ke history
        try:
            history = load_wa_history()
            history.append({
                "group": group_name,
                "sender": sender_name,
                "number": sender_number_formatted,
                "text": clean_text,
                "time": datetime.now().isoformat()
            })
            save_wa_history(history)
        except Exception as e:
            logger.error(f"History save error: {e}")
        
        # Load fresh DB dari PasaData/Supabase supaya perubahan user/langganan
        # langsung dipakai oleh webhook WhatsApp.
        fresh_db = load_db_from_supabase()
        if fresh_db is None:
            try:
                with open(DB_FILE_PERSISTENT if os.path.exists(DB_FILE_PERSISTENT) else DB_FILE, "r", encoding="utf-8") as f:
                    fresh_db = json.load(f)
            except:
                fresh_db = {
                    "user_info": db.get("user_info", {}),
                    "langganan": db.get("langganan", {}),
                    "langganan_cari": db.get("langganan_cari", {}),
                    "blacklist": db.get("blacklist", [])
                }
        
        # Cek blacklist
        is_blacklisted = sender_number_formatted in fresh_db.get("blacklist", [])
        if is_blacklisted:
            return "ok", 200
        
        # Proses setiap user
        matched_users = []
        now = datetime.now()
        
        for uid_str, uinfo in fresh_db.get("user_info", {}).items():
            try:
                # Perbaiki paket lama yang belum mempunyai nama kota sebelum pencocokan WA.
                repair_subscription_cities(int(uid_str), fresh_db)
                uinfo = fresh_db.get("user_info", {}).get(uid_str, uinfo)
                uid_int = int(uid_str)
                
                # Cek lokasi dan keyword
                kotas = uinfo.get("kotas", [])
                custom_keywords = uinfo.get("custom_keywords", [])
                
                # Cek kecocokan wilayah
                is_match, matched_location = check_location_match(text_upper, kotas)
                
                # Cek kecocokan keyword
                is_keyword_match = False
                matched_keyword = ""
                for kw in custom_keywords:
                    if kw.upper() in text_upper:
                        is_keyword_match = True
                        matched_keyword = kw
                        break
                
                # === LOGIKA CHECK EXPIRED PER KOTA ===
                # Ambil langganan user
                active_kota_names = []
                user_subs = fresh_db.get("langganan", {}).get(uid_str, [])
                
                # Handle format data (bisa list atau dict)
                if isinstance(user_subs, dict):
                    user_subs = [user_subs] # Convert legacy
                
                if user_subs:
                    for s in user_subs:
                        # Konversi string ke datetime
                        exp_str = s.get("expire", "")
                        if isinstance(exp_str, str):
                            try: 
                                exp = datetime.fromisoformat(exp_str)
                            except: 
                                exp = None
                        else:
                            exp = exp_str # Sudah object datetime
                            
                        # Jika belum expired
                        if exp and exp > now:
                            # Ambil nama kota dari paket atau nama kota
                            # (Asumsi s["kota"] harus disimpan saat top up)
                            kota_name = s.get("kota", "Paket Aktif")
                            active_kota_names.append(kota_name.upper())
                
                # Cek apakah kota yang match ada di list aktif user
                if is_match:
                    # Parse nama kota dari hasil match
                    matched_kota_name = ""
                    if "|" in matched_location:
                        matched_kota_name = matched_location.split("|")[0].strip().upper()
                    else:
                        matched_kota_name = matched_location.upper()
                    
                    # Cek apakah kota user ada di daftar aktif
                    is_kota_expired = True # Default expired
                    for ak in active_kota_names:
                        if ak in matched_kota_name or matched_kota_name in ak:
                            is_kota_expired = False 
                            break
                    
                    # Jika langganan kota expired/tidak aktif, jangan kirim.
                    # Keyword tidak boleh melewati pengecekan langganan kota.
                    if is_kota_expired:
                        continue
                
                # ============================================================
                # HARD GATE: WAJIB KOTA/KABUPATEN + KECAMATAN
                # Custom keyword TIDAK PERNAH boleh menjadi jalan pintas.
                # Jika salah satu dari KOTA/KABUPATEN atau KECAMATAN tidak ada,
                # pesan WA HARUS DITOLAK.
                # ============================================================
                if not is_match:
                    logger.info(
                        f"⛔ SKIP uid={uid_str}: lokasi tidak lengkap "
                        f"(wajib KOTA/KABUPATEN + KECAMATAN)"
                    )
                    continue

                # Hanya setelah hard gate lokasi lolos, pesan boleh diteruskan.
                if is_match:
                    matched_users.append(uid_int)
                    
                    match_type = f"📍 {matched_location}"
                    if is_keyword_match:
                        match_type += f" | 🔑 {matched_keyword}"
                    
                    notes = ""
                    
                    msg = f"""Kota : {matched_location}
Grup : {group_name}
Pengirim : {sender_name}
No WhatsApp : {sender_number_formatted}
━━━━━━━━━━━━━━━━━━━

ISI PESAN
{clean_text}
━━━━━━━━━━━━━━━━━━━
⚠️ Perhatian : untuk tetap waspada dan hati-hati disarankan untuk rekber, terimakasih.sumber: https://t.me/Aakiwkiw_bot 🙏"""
                    
                    # Kirim ke user
                    success = send_tg_message(uid_int, msg, wa_number=sender_number_formatted)
                    if success:
                        logger.info(f"✅ Notifikasi terkirim ke {uid_int} untuk match {match_type}")
                    else:
                        logger.error(f"❌ Gagal kirim ke {uid_int}")
                    
            except Exception as e:
                logger.error(f"WA USER PROCESS ERROR uid={uid_str}: {type(e).__name__}: {e}")
        
        logger.info(f"Total matched users: {len(matched_users)}")
        return "ok", 200
        
    except Exception as e:
        logger.error(f"Webhook error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return "ok", 200

@flask_app.route("/whatsapp-webhook", methods=["GET"])
def whatsapp_webhook_get():
    return {
        "status": "ok",
        "message": "Webhook WhatsApp aktif",
        "config": {
            "green_api_id": GREEN_API_ID,
            "webhook_url": f"{RAILWAY_URL}/whatsapp-webhook"
        }
    }, 200

# ========== FUNGSI BANTUAN BARU ==========
def is_admin(uid): return uid in ADMIN_IDS

# UBAH FUNGSI CEK EXPIRED PER KOTA
def is_active_tambah(uid, kota_name=None):
    if is_admin(uid): return True
    subs = db["langganan"].get(str(uid), [])
    if isinstance(subs, dict): subs = [subs]
    now = datetime.now()
    if not subs: return False
    for s in subs:
        exp = s.get("expire")
        if isinstance(exp, str):
            try: exp = datetime.fromisoformat(exp)
            except: exp = None
        if exp and exp > now:
            q=s.get("kuota",0)
            u=s.get("used_kuota",0)
            if isinstance(u,bool): u=1 if u else 0
            if q - u >0:
                if kota_name:
                    if s.get("kota","").upper() == kota_name.upper():
                        return True
                else:
                    return True
    if kota_name is None:
        for s in subs:
            exp = s.get("expire")
            if isinstance(exp, str):
                try: exp = datetime.fromisoformat(exp)
                except: exp = None
            if exp and exp > now:
                q=s.get("kuota",0)
                u=s.get("used_kuota",0)
                if isinstance(u,bool): u=1 if u else 0
                if q - u >0:
                    return True
    return False

    
    # Jika nama kota dikasih, cek spesifik expired kota itu
    if kota_name:
        for s in subs:
            if s.get("kota", "").upper() == kota_name.upper():
                exp = s.get("expire")
                if isinstance(exp, str):
                    try: exp = datetime.fromisoformat(exp)
                    except: exp = None
                return exp and exp > now
        return False # Kota tidak ditemukan di list
    else:
        # Cek apakah user punya minimal 1 langganan aktif
        for s in subs:
            exp = s.get("expire")
            if isinstance(exp, str):
                try: exp = datetime.fromisoformat(exp)
                except: exp = None
            if exp and exp > now:
                return True
        return False

def is_active_cari(uid):
    if is_admin(uid): return True
    sub = db["langganan_cari"].get(str(uid))
    return sub and not is_expired(sub)

def is_expired(sub):
    if not sub: return True
    exp = sub.get("expire")
    if isinstance(exp, str):
        try: exp = datetime.fromisoformat(exp)
        except: return True
    return not exp or exp < datetime.now()

def is_user_id_aktif(uid):
    return is_active_tambah(uid) or is_active_cari(uid)

FORBIDDEN_GEO_EXTRA = {"kabupaten","kota","kecamatan","provinsi","kelurahan","desa"}

def _norm_kw(s): return s.strip().lower()

def is_geo_forbidden(keyword):
    kw = _norm_kw(keyword)
    if len(kw) < 3: return True, "Keyword terlalu pendek"
    for p in LIST_PROVINSI:
        nama = p["nama"].lower()
        if kw == nama or kw == nama.replace(" ",""):
            return True, f"'{keyword}' adalah nama PROVINSI ({p['nama']})"
        if len(kw) >= 4 and (kw in nama or nama in kw):
            if len(kw) >= 4:
                return True, f"'{keyword}' mengandung nama PROVINSI ({p['nama']})"
    for bad in FORBIDDEN_GEO_EXTRA:
        if bad in kw:
            return True, f"Keyword tidak boleh mengandung kata '{bad}' (geografis)"
    return False, ""


def kb_back_main_only():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]
    ])

def kb_main(uid):
    keyboard=[
        [InlineKeyboardButton("👤 PROFIL", callback_data="menu_profil"), InlineKeyboardButton("📊 CEK STATUS", callback_data="menu_status")],
        [InlineKeyboardButton("🌍 TAMBAH KOTA", callback_data="menu_tambah_kota"), InlineKeyboardButton("🌠 WILAYAH DIPILIH", callback_data="menu_wilayah")],
        [InlineKeyboardButton("🔎 CARI DATA LAIN", callback_data="menu_cari_data"), InlineKeyboardButton("🚫 NO BLACKLIST", callback_data="menu_blacklist")],
        [InlineKeyboardButton("🧑‍💻 HUBUNGI ADMIN", callback_data="menu_hubungi_admin")],
    ]
    if is_admin(uid):
        keyboard.append([InlineKeyboardButton("🧭 PANEL ADMIN", callback_data="admin_menu")])
    return InlineKeyboardMarkup(keyboard)

def kb_wilayah_dipilih():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ HAPUS SEMUA KOTA SAYA", callback_data="hapus_semua_kota")],
        [InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]
    ])

def kb_blacklist_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 CARI NO BLACKLIST", callback_data="cari_blacklist")],
        [InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]
    ])

def kb_hubungi_admin():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 CHAT DI TELEGRAM", url="https://t.me/Hambali1995")],
        [InlineKeyboardButton("📱 CHAT DI WHATSAPP", url="https://wa.me/6283160776091")],
        [InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]
    ])

def kb_cari_data_lain():
    # Ini untuk pesan awal / tidak ditemukan - tetap ada tombol cari lagi
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 CARI DATA LAGI", callback_data="menu_cari_data")],
        [InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]
    ])

def kb_hasil_cari(clean_number=None):
    # Tombol untuk SETIAP hasil - TANPA CARI DATA LAGI (sesuai request)
    rows=[]
    if clean_number:
        rows.append([InlineKeyboardButton("💬 Chat di WA", url=f"https://wa.me/{clean_number}")])
    rows.append([InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)

def kb_hasil_cari_selesai():
    # Tombol untuk pesan SELESAI - ADA CARI DATA LAGI biar bisa klik lagi
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 CARI DATA LAGI", callback_data="menu_cari_data")],
        [InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]
    ])

def kb_admin_panel():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 STATUS USER AKTIF", callback_data="admin_status_user")],
        [InlineKeyboardButton("👤 CEK USER AKTIF (SIMPLE)", callback_data="admin_cek_aktif")],
        [InlineKeyboardButton("➕ TAMBAH BLACKLIST", callback_data="admin_tambah_blacklist")],
        [InlineKeyboardButton("➖ HAPUS BLACKLIST", callback_data="admin_hapus_blacklist")],
        [InlineKeyboardButton("🗑️ HAPUS ID USER", callback_data="admin_hapus_list")],
        [InlineKeyboardButton("📦 HAPUS PAKET USER", callback_data="admin_hapus_paket")],
        [InlineKeyboardButton("📢 BROADCAST", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]
    ])

def kb_provinsi():
    buttons=[]
    for p in LIST_PROVINSI:
        buttons.append([InlineKeyboardButton(f"{p['nama'].upper()}", callback_data=f"prov_{p['id']}_{p['nama']}")])
    buttons.append([InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")])
    return InlineKeyboardMarkup(buttons)

def build_kec_keyboard(kota_nama, kec_list, selected, prov_id, prov_nama):
    buttons=[]
    buttons.append([InlineKeyboardButton(f"✅ PILIH SEMUA KECAMATAN DI {kota_nama.upper()}", callback_data=f"kec_ALL_Semua Kecamatan")])
    for kec in kec_list:
        name=kec.get("name","")
        if not name:
            continue
        icon="✅" if name in selected else "◻️"
        safe_name = name[:30]
        buttons.append([InlineKeyboardButton(f"{icon} {name.upper()}", callback_data=f"kec_toggle_{kec.get('id','0')}_{safe_name}")])
    if selected:
        buttons.append([InlineKeyboardButton(f"💾 SIMPAN {len(selected)} KECAMATAN ✅", callback_data="kec_save")])
        buttons.append([InlineKeyboardButton(f"🗑️ HAPUS PILIHAN ({len(selected)})", callback_data="kec_clear")])
    buttons.append([InlineKeyboardButton(f"⬅️ KEMBALI KE KOTA", callback_data=f"prov_{prov_id}_{prov_nama}")])
    buttons.append([InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")])
    return InlineKeyboardMarkup(buttons)

def kb_paket_tambah(is_admin_user=False):
    buttons=[]
    buttons.append([InlineKeyboardButton("⏰ 1 MINGGU - Rp 25.000 (2x)", callback_data="paket_tambah_1minggu")])
    buttons.append([InlineKeyboardButton("⏰ 2 MINGGU - Rp 50.000 (3x)", callback_data="paket_tambah_2minggu")])
    buttons.append([InlineKeyboardButton("⏰ 3 MINGGU - Rp 75.000 (3x)", callback_data="paket_tambah_3minggu")])
    buttons.append([InlineKeyboardButton("📅 1 BULAN - Rp 100.000 (3x)", callback_data="paket_tambah_1bulan")])
    buttons.append([InlineKeyboardButton("📅 2 BULAN - Rp 180.000 (3x)", callback_data="paket_tambah_2bulan")])
    buttons.append([InlineKeyboardButton("📅 6 BULAN - Rp 500.000 (4x)", callback_data="paket_tambah_6bulan")])
    buttons.append([InlineKeyboardButton("♾️ UNLIMITED - Rp 2.000.000 (6x)", callback_data="paket_tambah_unlimited")])
    buttons.append([InlineKeyboardButton("📦 PAKET YANG DI PILIH", callback_data="menu_status")])
    buttons.append([InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")])
    if is_admin_user:
        buttons.insert(0, [InlineKeyboardButton("🌍 LANGSUNG PILIH PROVINSI (ADMIN)", callback_data="admin_langsung_provinsi")])
    return InlineKeyboardMarkup(buttons)

def kb_paket_cari():
    buttons=[]
    buttons.append([InlineKeyboardButton("⏰ 1 MINGGU - Rp 15.000", callback_data="paket_cari_1minggu")])
    buttons.append([InlineKeyboardButton("⏰ 2 MINGGU - Rp 25.000", callback_data="paket_cari_2minggu")])
    buttons.append([InlineKeyboardButton("⏰ 3 MINGGU - Rp 35.000", callback_data="paket_cari_3minggu")])
    buttons.append([InlineKeyboardButton("📅 1 BULAN - Rp 50.000", callback_data="paket_cari_1bulan")])
    buttons.append([InlineKeyboardButton("📅 2 BULAN - Rp 80.000", callback_data="paket_cari_2bulan")])
    buttons.append([InlineKeyboardButton("📅 6 BULAN - Rp 250.000", callback_data="paket_cari_6bulan")])
    buttons.append([InlineKeyboardButton("♾️ UNLIMITED - Rp 1.000.000", callback_data="paket_cari_unlimited")])
    buttons.append([InlineKeyboardButton("📦 PAKET YANG DI PILIH", callback_data="menu_status")])
    buttons.append([InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")])
    return InlineKeyboardMarkup(buttons)


def get_kota(prov_id):
    try:
        r=requests.get(f"https://www.emsifa.com/api-wilayah-indonesia/api/regencies/{prov_id}.json",timeout=15)
        r.raise_for_status()
        data=r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"get_kota error {prov_id}: {e}")
        return []

def get_kecamatan(kota_id):
    try:
        r=requests.get(f"https://www.emsifa.com/api-wilayah-indonesia/api/districts/{kota_id}.json",timeout=15)
        r.raise_for_status()
        data=r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"get_kecamatan error {kota_id}: {e}")
        return []

def _unique_selected_cities(uid):
    entries = db.get("user_info", {}).get(str(uid), {}).get("kotas", [])
    cities = []
    for entry in entries:
        parts = [x.strip() for x in str(entry).split("|")]
        if len(parts) >= 2 and parts[1] and parts[1] not in cities:
            cities.append(parts[1])
    return cities

def repair_subscription_cities(uid, state=None):
    """Perbaiki paket lama yang belum mempunyai nama kota dari wilayah user."""
    state = db if state is None else state
    subs = state.get("langganan", {}).get(str(uid), [])
    if isinstance(subs, dict):
        subs = [subs]
        state["langganan"][str(uid)] = subs
    if not subs:
        return False
    selected_entries = state.get("user_info", {}).get(str(uid), {}).get("kotas", [])
    selected_cities = []
    for entry in selected_entries:
        parts = [x.strip() for x in str(entry).split("|")]
        if len(parts) >= 2 and parts[1] and parts[1] not in selected_cities:
            selected_cities.append(parts[1])
    if not selected_cities:
        return False
    placeholders = {"", "TIDAK ADA KOTA", "UMUM", "TIDAK DIKETAHUI", "PAKET AKTIF"}
    valid_cities = [str(x.get("kota", "")).strip() for x in subs if str(x.get("kota", "")).strip().upper() not in placeholders]
    changed = False
    for sub in subs:
        kota = str(sub.get("kota", "")).strip()
        if kota.upper() not in placeholders:
            continue
        target = next((c for c in selected_cities if c.upper() not in {v.upper() for v in valid_cities}), None)
        if target:
            sub["kota"] = target
            valid_cities.append(target)
            changed = True
    return changed

def assign_subscription_to_selected_city(uid, kota):
    """Hubungkan paket TAMBAH KOTA aktif yang belum dipakai ke kota yang dipilih."""
    subs = db.get("langganan", {}).get(str(uid), [])
    if isinstance(subs, dict):
        subs = [subs]
        db["langganan"][str(uid)] = subs
    now = datetime.now()
    kota = str(kota).strip()
    if not kota or not subs:
        return False
    for sub in subs:
        existing = str(sub.get("kota", "")).strip()
        exp = sub.get("expire")
        if isinstance(exp, str):
            try: exp = datetime.fromisoformat(exp)
            except: exp = None
        if existing and existing.upper() == kota.upper() and exp and exp > now:
            return False
    for sub in subs:
        exp = sub.get("expire")
        if isinstance(exp, str):
            try: exp = datetime.fromisoformat(exp)
            except: exp = None
        if exp and exp > now and not sub.get("used", False):
            sub["kota"] = kota
            sub["used"] = True
            return True
    for sub in subs:
        existing = str(sub.get("kota", "")).strip().upper()
        if existing in {"", "TIDAK ADA KOTA", "UMUM", "TIDAK DIKETAHUI", "PAKET AKTIF"}:
            exp = sub.get("expire")
            if isinstance(exp, str):
                try: exp = datetime.fromisoformat(exp)
                except: exp = None
            if exp and exp > now:
                sub["kota"] = kota
                return True
    return False


async def get_status_text(uid):
    repaired = repair_subscription_cities(uid)
    if repaired:
        save_db()
    user_data=db["user_info"].get(str(uid),{})
    kotas=user_data.get("kotas",[])
    subs = db["langganan"].get(str(uid), [])
    if isinstance(subs, dict): subs = [subs]
    sub_cari=db["langganan_cari"].get(str(uid))
    saldo = user_data.get("saldo", 0)
    wilayah_list = []
    for k in kotas:
        parts=[p.strip() for p in str(k).split("|")]
        if len(parts)>=2:
            wilayah_list.append(parts[1])
        else:
            wilayah_list.append(str(k))
    wilayah_str = ", ".join(wilayah_list[:10]) if wilayah_list else "Belum pilih"
    if len(wilayah_list)>10:
        wilayah_str += f" +{len(wilayah_list)-10} lainnya"
    total_kuota=0
    used_kuota=0
    active_kuota=0
    now=datetime.now()
    for s in subs:
        exp=s.get("expire")
        if isinstance(exp,str):
            try: exp=datetime.fromisoformat(exp)
            except: exp=None
        if exp and exp>now:
            q=s.get("kuota", s.get("quota", 0))
            u=s.get("used_kuota", s.get("used", 0))
            if isinstance(u, bool):
                u=1 if u else 0
            total_kuota+=q
            used_kuota+=u if isinstance(u,int) else 0
            active_kuota+=max(0, q - (u if isinstance(u,int) else 0))
    if is_admin(uid):
        txt = f"""📊 STATUS USER
💰 Saldo : Rp {saldo:,}
📦 Langganan : 👑 ADMIN UNLIMITED
🌍 Wilayah : {wilayah_str}
📊 Status : 🟢 Aktif
⏰ Expired : Tidak terbatas
🎟️ Kuota Pilih Kota : ♾️ UNLIMITED"""
    else:
        if subs:
            active_kota=[]
            for s in subs:
                exp=s.get("expire")
                if isinstance(exp,str):
                    try: exp=datetime.fromisoformat(exp)
                    except: exp=None
                if exp and exp>now:
                    k=s.get("kota", "-")
                    q=s.get("kuota",0)
                    u=s.get("used_kuota",0)
                    if isinstance(u,bool): u=1 if u else 0
                    sisa = q - (u if isinstance(u,int) else 0)
                    active_kota.append(f"{s.get('paket','-')} ({k} - sisa {sisa}/{q} s/d {exp.strftime('%d/%m/%Y')})")
            if active_kota:
                langganan_str = ", ".join(active_kota)
                status_icon = "🟢 Aktif"
                exp_str = f"Sisa kuota {active_kuota} dari {total_kuota}"
            else:
                langganan_str = "Expired / Kuota habis"
                status_icon = "🔴 Tidak aktif"
                exp_str = "Habis"
        else:
            langganan_str = "Belum ada"
            status_icon = "🔴 Tidak aktif"
            exp_str = "-"
        if sub_cari and not is_expired(sub_cari):
            exp=sub_cari.get("expire")
            if isinstance(exp,str):
                try: exp=datetime.fromisoformat(exp)
                except: exp=None
            paket=PAKET_CARI.get(sub_cari.get("paket"),{}).get("nama","-")
            exp_cari_str=exp.strftime("%d/%m/%Y") if exp else "-"
            cari_str = f"{paket} (s/d {exp_cari_str})"
        else:
            cari_str = "Belum ada"
        txt = f"""📊 STATUS USER
💰 Saldo : Rp {saldo:,}
📦 Langganan Tambah Kota :
{langganan_str}
🌍 Wilayah : {wilayah_str}
🔎 Cari Data : {cari_str}
📊 Status : {status_icon}
⏰ Kuota : {exp_str}

🏙️ Total Wilayah : {len(kotas)} kota"""
    return txt


async def get_profil_text(uid, user_obj=None):
    user_data=db["user_info"].get(str(uid),{})
    kotas=user_data.get("kotas",[])
    subs = db["langganan"].get(str(uid), [])
    if isinstance(subs, dict): subs = [subs]
    
    nama=user_obj.full_name if user_obj else user_data.get("nama","-")
    username=f"@{user_obj.username}" if user_obj and user_obj.username else user_data.get("username","-")
    
    if is_admin(uid): 
        paket="👑 ADMIN UNLIMITED"
    else:
        paket = "❌ Belum ada"
        if subs:
            p_list = []
            for s in subs:
                kota = s.get("kota", "-")
                p_list.append(f"{kota} ({PAKET_TAMBAH.get(s.get('paket'),{}).get('nama','-')})")
            paket = ", ".join(p_list)
                
    return f"👤 PROFIL USER \n\n🆔 ID: {uid}\n👨 Nama: {nama}\n📱 Username: {username}\n🎁 Paket: {paket}\n📍 Wilayah: {len(kotas)} tersimpan\n\n💡 Gunakan menu di bawah untuk atur bot!"


async def start(update,context):
    uid=update.effective_user.id
    nama=update.effective_user.full_name
    username=update.effective_user.username or "-"
    if str(uid) not in db["user_info"]: db["user_info"][str(uid)]={"nama":nama,"username":username,"kotas":[],"custom_keywords":[],"saldo":0}
    else:
        db["user_info"][str(uid)]["nama"]=nama; db["user_info"][str(uid)]["username"]=username
        if "custom_keywords" not in db["user_info"][str(uid)]: db["user_info"][str(uid)]["custom_keywords"]=[]
        if "kotas" not in db["user_info"][str(uid)]: db["user_info"][str(uid)]["kotas"]=[]
        if "saldo" not in db["user_info"][str(uid)]: db["user_info"][str(uid)]["saldo"]=0
    save_db()
    txt = """🟢 MODE DI AKTIFKAN
━━━━━━━━━━━━━━━
👋 Selamat datang, SAHABAT JHT! Gimana kabarnya nih, saya berharap kabar baik-baik saja yah, tetap semangat dan jangan lupa bersyukur. Silahkan pilih menu di bawah ini : 👇
1. PROFIL
2. CEK STATUS
3. TAMBAH KOTA
4. WILAYAH DIPILIH
5. CARI DATA LAIN
6. NO BLACKLIST
7. HUBUNGI ADMIN"""
    await update.message.reply_text(txt, reply_markup=kb_main(uid))


async def cb_handler(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; data=q.data
    if data=="noop": return
    if data=="back_main":
        try: await q.message.delete()
        except: pass
        txt = """🟢 MODE DI AKTIFKAN
━━━━━━━━━━━━━━━
👋 Selamat datang, SAHABAT JHT! Gimana kabarnya nih, saya berharap kabar baik-baik saja yah, tetap semangat dan jangan lupa bersyukur. Silahkan pilih menu di bawah ini : 👇"""
        await context.bot.send_message(chat_id=uid, text=txt, reply_markup=kb_main(uid))
        return
    if data=="menu_profil":
        try: await q.message.delete()
        except: pass
        txt=await get_profil_text(uid, q.from_user)
        await context.bot.send_message(chat_id=uid, text=txt, reply_markup=kb_back_main_only())
        return
    if data=="menu_status":
        try: await q.message.delete()
        except: pass
        txt=await get_status_text(uid)
        await context.bot.send_message(chat_id=uid, text=txt, reply_markup=kb_back_main_only())
        return
    if data=="menu_tambah_kota":
        try: await q.message.delete()
        except: pass
        if is_admin(uid):
            txt="""🌍 TAMBAH KOTA - PILIH PAKET (ADMIN)

Kamu admin (Unlimited), bisa langsung pilih provinsi atau beli paket lagi.

💎 Paket tersedia:
⏰ 1 Minggu - Rp 25.000 (2x)
⏰ 2 Minggu - Rp 50.000 (3x)
⏰ 3 Minggu - Rp 75.000 (3x)
📅 1 Bulan - Rp 100.000 (3x)
📅 2 Bulan - Rp 180.000 (3x)
📅 6 Bulan - Rp 500.000 (4x)
♾️ Unlimited - Rp 2.000.000 (6x)

Klik paket untuk Top Up, atau langsung pilih provinsi (Admin Unlimited):"""
            await context.bot.send_message(chat_id=uid, text=txt, reply_markup=kb_paket_tambah(is_admin_user=True))
        else:
            subs=db["langganan"].get(str(uid),[])
            if isinstance(subs, dict): subs=[subs]
            now=datetime.now()
            active=False
            sisa=0
            for s in subs:
                exp=s.get("expire")
                if isinstance(exp,str):
                    try: exp=datetime.fromisoformat(exp)
                    except: exp=None
                if exp and exp>now:
                    qq=s.get("kuota",0)
                    uu=s.get("used_kuota",0)
                    if isinstance(uu,bool): uu=1 if uu else 0
                    if qq - uu >0:
                        active=True
                        sisa+= qq - uu
            if active:
                txt=f"""🌍 TAMBAH KOTA

✅ Kuota aktif: {sisa}x pilih provinsi tersisa
Silahkan pilih provinsi atau beli paket lagi.

💎 Paket tersedia:
⏰ 1 Minggu - Rp 25.000 (2x)
⏰ 2 Minggu - Rp 50.000 (3x)
⏰ 3 Minggu - Rp 75.000 (3x)
📅 1 Bulan - Rp 100.000 (3x)
📅 2 Bulan - Rp 180.000 (3x)
📅 6 Bulan - Rp 500.000 (4x)
♾️ Unlimited - Rp 2.000.000 (6x)"""
                await context.bot.send_message(chat_id=uid, text=txt, reply_markup=kb_paket_tambah(is_admin_user=False))
            else:
                txt="""🌍 TAMBAH KOTA - PILIH PAKET

Kuota habis / belum ada paket aktif. Silahkan pilih paket:

💎 Paket tersedia:
⏰ 1 Minggu - Rp 25.000 (2x)
⏰ 2 Minggu - Rp 50.000 (3x)
⏰ 3 Minggu - Rp 75.000 (3x)
📅 1 Bulan - Rp 100.000 (3x)
📅 2 Bulan - Rp 180.000 (3x)
📅 6 Bulan - Rp 500.000 (4x)
♾️ Unlimited - Rp 2.000.000 (6x)"""
                await context.bot.send_message(chat_id=uid, text=txt, reply_markup=kb_paket_tambah(is_admin_user=False))
        return
    if data=="admin_langsung_provinsi":
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text="🌍 SILAHKAN PILIH PROVINSI:", reply_markup=kb_provinsi())
        return
    if data.startswith("paket_tambah_"):
        paket_key=data.replace("paket_tambah_","")
        paket=PAKET_TAMBAH.get(paket_key)
        if not paket:
            await q.answer("Paket tidak ditemukan", show_alert=True); return
        context.user_data["pending_paket_tambah"]=paket_key
        txt=f"""💳 TOP UP TAMBAH KOTA

🎁 Paket: {paket['nama']}
💰 Harga: Rp {paket['harga']:,}
⏰ Durasi: {paket['hari']} hari
🎟️ Kuota: {paket['kuota']}x pilih provinsi

{REKENING_TEXT}

Kirim foto bukti transfer + ketik kota yang ingin diambil (contoh: Bandung)"""
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text=txt, reply_markup=kb_back_main_only())
        return
    if data.startswith("paket_cari_"):
        paket_key=data.replace("paket_cari_","")
        paket=PAKET_CARI.get(paket_key)
        if not paket:
            await q.answer("Paket tidak ditemukan", show_alert=True); return
        context.user_data["pending_paket_cari"]=paket_key
        txt=f"""💳 TOP UP CARI DATA LAIN

🎁 Paket: {paket['nama']}
💰 Harga: Rp {paket['harga']:,}
⏰ Durasi: {paket['hari']} hari

{REKENING_TEXT}

Kirim foto bukti transfer di sini"""
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text=txt, reply_markup=kb_back_main_only())
        return
    if data=="menu_wilayah":
        try: await q.message.delete()
        except: pass
        user_data=db["user_info"].get(str(uid),{})
        kotas=user_data.get("kotas",[])
        if not kotas:
            txt="🌠 WILAYAH DIPILIH\n\n❌ Belum ada wilayah dipilih"
        else:
            txt="🌠 WILAYAH DIPILIH\n\n"
            for i,k in enumerate(kotas[:30],1):
                parts=[p.strip() for p in k.split("|")]
                if len(parts)>=3:
                    txt+=f"{i}. {parts[0]} > {parts[1]} > {parts[2]}\n"
                else:
                    txt+=f"{i}. {k}\n"
            if len(kotas)>30:
                txt+=f"\n... dan {len(kotas)-30} lainnya"
        await context.bot.send_message(chat_id=uid, text=txt, reply_markup=kb_wilayah_dipilih())
        return
    if data=="hapus_semua_kota":
        try: await q.message.delete()
        except: pass
        if str(uid) in db["user_info"]:
            db["user_info"][str(uid)]["kotas"]=[]
        save_db()
        await context.bot.send_message(chat_id=uid, text="✅ Semua kota berhasil dihapus!", reply_markup=kb_back_main_only())
        return
    if data=="menu_cari_data":
        try: await q.message.delete()
        except: pass
        sub_cari=db["langganan_cari"].get(str(uid))
        if not is_admin(uid) and (not sub_cari or is_expired(sub_cari)):
            txt="""🔎 CARI DATA LAIN - PILIH PAKET

Paket belum aktif / expired. Silahkan pilih paket:

💎 Paket tersedia:
⏰ 1 Minggu - Rp 15.000
⏰ 2 Minggu - Rp 25.000
⏰ 3 Minggu - Rp 35.000
📅 1 Bulan - Rp 50.000
📅 2 Bulan - Rp 80.000
📅 6 Bulan - Rp 250.000
♾️ Unlimited - Rp 1.000.000"""
            await context.bot.send_message(chat_id=uid, text=txt, reply_markup=kb_paket_cari())
            return
        context.user_data["awaiting_cari_data"]=True
        txt="""🔎 CARI DATA LAINNYA

📍 MASUKAN NAMA KOTA
💡 Contoh: BANDUNG

✍️ Ketik kota yang mau dicari
Bot akan cari di history WA yang dishare pengirim!

❌ Ketik /batal untuk batal."""
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ BATAL", callback_data="batal_cari")],
            [InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]
        ])
        await context.bot.send_message(chat_id=uid, text=txt, reply_markup=kb)
        return

    if data=="batal_cari":
        context.user_data.pop("awaiting_cari_data", None)
        context.user_data.pop("awaiting_cari_lainnya", None)
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text="❌ Pencarian dibatalkan", reply_markup=kb_main(uid))
        return
    if data=="menu_blacklist":
        try:
            await q.message.delete()
        except:
            pass
        bl = get_all_blacklist()
        # Pastikan bl adalah list
        if not isinstance(bl, list):
            bl = list(bl) if bl else []
        total = len(bl)
        if total == 0:
            try:
                await context.bot.send_message(chat_id=uid, text="🚫 NO BLACKLIST\n\n📭 Belum ada nomor blacklist\n📊 Total: 0 nomor", reply_markup=kb_blacklist_menu())
            except:
                await context.bot.send_message(chat_id=uid, text="🚫 NO BLACKLIST\nTotal: 0 nomor", reply_markup=kb_back_main_only())
            return
        
        # Kirim SEMUA nomor - tanpa keyboard dulu biar anti error
        header = f"🚫 NO BLACKLIST - SEMUA NOMOR ({total} nomor)\n\n"
        msg = header
        for i, nomor in enumerate(bl, 1):
            msg += f"{i}. {nomor}\n"
            # Telegram limit 4000 char, split jika kepanjangan
            if len(msg) > 3500:
                try:
                    await context.bot.send_message(chat_id=uid, text=msg)
                except Exception as e:
                    logger.error(f"send chunk error: {e}")
                    # coba tanpa markdown
                    try:
                        await context.bot.send_message(chat_id=uid, text=msg, parse_mode=None)
                    except:
                        pass
                msg = ""
        
        # Kirim sisa + tombol
        if msg:
            try:
                await context.bot.send_message(chat_id=uid, text=msg, reply_markup=kb_blacklist_menu())
            except:
                try:
                    await context.bot.send_message(chat_id=uid, text=msg, reply_markup=kb_back_main_only())
                except:
                    await context.bot.send_message(chat_id=uid, text=msg)
        else:
            # Jika semua sudah terkirim di loop, kirim tombol terpisah
            try:
                await context.bot.send_message(chat_id=uid, text=f"✅ Selesai menampilkan {total} nomor blacklist", reply_markup=kb_blacklist_menu())
            except:
                await context.bot.send_message(chat_id=uid, text=f"✅ Selesai {total} nomor", reply_markup=kb_back_main_only())
        return
    if data.startswith("blacklist_page_"):
        # Redirect ke menu_blacklist (tampilkan semua)
        try:
            await q.message.delete()
        except:
            pass
        bl = get_all_blacklist()
        total = len(bl)
        header = f"🚫 NO BLACKLIST - SEMUA NOMOR ({total} nomor)\n\n"
        msg = header
        for i, nomor in enumerate(bl, 1):
            msg += f"{i}. {nomor}\n"
            if len(msg) > 3500:
                await context.bot.send_message(chat_id=uid, text=msg)
                msg = ""
        if msg:
            await context.bot.send_message(chat_id=uid, text=msg, reply_markup=kb_blacklist_menu())
        return

    if data=="cari_blacklist":
        try: await q.message.delete()
        except: pass
        context.user_data["awaiting_cek_blacklist"]=True
        await context.bot.send_message(chat_id=uid, text="🔍 CARI NO BLACKLIST\n\nKetik nomor yang ingin dicek:\nContoh: 083123456789", reply_markup=kb_back_main_only())
        return
    if data=="menu_hubungi_admin":
        try: await q.message.delete()
        except: pass
        txt="📞 HUBUNGI ADMIN\nJika membutuhkan bantuan, silakan hubungi Admin:\n👤 Telegram @Hambali1995\n📱 WhatsApp 083160776091"
        await context.bot.send_message(chat_id=uid, text=txt, reply_markup=kb_hubungi_admin())
        return
    if data=="admin_menu":
        if not is_admin(uid): return
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text="🧭 PANEL ADMIN\n\nPilih menu admin:", reply_markup=kb_admin_panel())
        return
    if data=="admin_status_user":
        if not is_admin(uid): return
        try: await q.message.delete()
        except: pass
        # Menampilkan SEMUA user yang sudah memilih paket (aktif maupun expired)
        now=datetime.now()
        text_out="📊 STATUS USER AKTIF - SEMUA PEMILIH PAKET\n━━━━━━━━━━━━━━\n\n"
        count=0
        for uid_str, subs in db.get("langganan", {}).items():
            if isinstance(subs, dict): subs=[subs]
            if not subs:
                continue
            info=db["user_info"].get(uid_str,{})
            nama=info.get("nama","-")
            kotas=info.get("kotas",[])
            paket_info=[]
            for s in subs:
                exp=s.get("expire")
                if isinstance(exp,str):
                    try: exp=datetime.fromisoformat(exp)
                    except: exp=None
                if exp:
                    exp_str=exp.strftime("%d/%m/%Y") if isinstance(exp, datetime) else str(exp)[:10]
                    is_active = exp>now if isinstance(exp, datetime) else True
                    status_icon = "✅" if is_active else "⏰ EXPIRED"
                    qq=s.get("kuota",0)
                    uu=s.get("used_kuota",0)
                    if isinstance(uu,bool): uu=1 if uu else 0
                    paket_info.append(f"{s.get('paket','-')} ({qq-uu}/{qq}) {status_icon} exp {exp_str}")
                else:
                    paket_info.append(f"{s.get('paket','-')} (no expire)")
            count+=1
            text_out+=f"{count}. 👤 NAMA: {nama}\n"
            text_out+=f"   🆔 ID: {uid_str}\n"
            text_out+=f"   📦 PAKET: {', '.join(paket_info)}\n"
            text_out+=f"   🌍 KOTA/KEC: \n"
            for k in kotas[:10]:
                parts=[p.strip() for p in str(k).split("|")]
                if len(parts)>=3:
                    text_out+=f"      - {parts[1]} | {parts[2]} (Prov: {parts[0]})\n"
                else:
                    text_out+=f"      - {k}\n"
            if len(kotas)>10:
                text_out+=f"      ... +{len(kotas)-10} lainnya\n"
            text_out+=f"\n"
            if len(text_out)>3500:
                text_out+=f"\n... masih ada {len(db.get('langganan',{}))-count} user lainnya (kepotong limit Telegram)"
                break
        if count==0:
            text_out+="❌ Belum ada user yang memilih paket"
        text_out+=f"\nTotal user pilih paket: {count}"
        await context.bot.send_message(chat_id=uid, text=text_out, reply_markup=kb_admin_panel())
        return
    if data=="admin_hapus_paket":
        if not is_admin(uid): return
        buttons=[]
        for uid_str, subs in list(db.get("langganan", {}).items())[:30]:
            info=db["user_info"].get(uid_str,{})
            nama=info.get("nama","-")[:10]
            # hitung paket aktif
            now=datetime.now()
            aktif=0
            for s in (subs if isinstance(subs,list) else [subs]):
                exp=s.get("expire")
                if isinstance(exp,str):
                    try: exp=datetime.fromisoformat(exp)
                    except: continue
                if exp and exp>now:
                    aktif+=1
            buttons.append([InlineKeyboardButton(f"📦 {uid_str} | {nama} ({aktif} paket)", callback_data=f"admin_del_paket_{uid_str}")])
        buttons.append([InlineKeyboardButton("🗑️ HAPUS SEMUA PAKET EXPIRED", callback_data="admin_del_all_expired")])
        buttons.append([InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")])
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text="📦 HAPUS PAKET USER\nPilih user yang paketnya akan dihapus:", reply_markup=InlineKeyboardMarkup(buttons))
        return
    if data.startswith("admin_del_paket_"):
        target=data.replace("admin_del_paket_","")
        if target not in db["langganan"]:
            await q.answer("User tidak punya paket", show_alert=True); return
        kb=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🗑️ HAPUS SEMUA PAKET {target}", callback_data=f"admin_confirm_del_paket_all_{target}")],
            [InlineKeyboardButton("📋 HAPUS PER PAKET", callback_data=f"admin_list_paket_{target}")],
            [InlineKeyboardButton("❌ BATAL", callback_data="admin_hapus_paket")]
        ])
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text=f"⚠️ HAPUS PAKET USER {target}\nPilih aksi:", reply_markup=kb)
        return
    if data.startswith("admin_list_paket_"):
        target=data.replace("admin_list_paket_","")
        subs=db["langganan"].get(target,[])
        if isinstance(subs, dict): subs=[subs]
        buttons=[]
        for idx,s in enumerate(subs):
            exp=s.get("expire")
            exp_str=exp.strftime("%d/%m/%Y") if isinstance(exp,datetime) else str(exp)[:10]
            buttons.append([InlineKeyboardButton(f"{idx+1}. {s.get('paket','-')} | {s.get('kota','-')} exp {exp_str}", callback_data=f"admin_del_paket_item_{target}_{idx}")])
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text=f"📋 PAKET USER {target}:", reply_markup=InlineKeyboardMarkup(buttons))
        return
    if data.startswith("admin_del_paket_item_"):
        parts=data.replace("admin_del_paket_item_","").split("_")
        target="_".join(parts[:-1]) if len(parts)>2 else parts[0]
        # Actually target is first part, idx is last
        # Since target may contain no underscore (numeric), we parse differently
        # data format: admin_del_paket_item_{uid}_{idx}
        # uid is numeric, idx is last
        try:
            idx=int(parts[-1])
            target_id=parts[-2] if len(parts)>=2 else target
            # For numeric uid, target_id is uid
            # Reconstruct target
            # If uid contains underscore? uid is numeric, so simple
            target_uid="_".join(parts[:-1]) if len(parts)>2 else parts[0]
            # But our earlier join for target with underscore for id that is numeric won't have underscore, so we can use:
            # Actually we need to split from right
            full=data.replace("admin_del_paket_item_","")
            # full = uid_idx
            # split at last underscore
            last_us=full.rfind("_")
            target_uid=full[:last_us]
            idx=int(full[last_us+1:])
        except:
            await q.answer("Format salah", show_alert=True); return
        subs=db["langganan"].get(target_uid,[])
        if isinstance(subs, dict): subs=[subs]
        if 0<=idx<len(subs):
            removed=subs.pop(idx)
            if not subs:
                del db["langganan"][target_uid]
            else:
                db["langganan"][target_uid]=subs
            save_db()
            await context.bot.send_message(chat_id=uid, text=f"✅ Paket {removed.get('paket','-')} user {target_uid} dihapus", reply_markup=kb_admin_panel())
        else:
            await q.answer("Index tidak valid", show_alert=True)
        try: await q.message.delete()
        except: pass
        return
    if data.startswith("admin_confirm_del_paket_all_"):
        target=data.replace("admin_confirm_del_paket_all_","")
        if target in db["langganan"]:
            del db["langganan"][target]
            save_db()
            await context.bot.send_message(chat_id=uid, text=f"✅ Semua paket user {target} dihapus", reply_markup=kb_admin_panel())
        try: await q.message.delete()
        except: pass
        return
    if data=="admin_del_all_expired":
        now=datetime.now()
        removed=0
        to_del=[]
        for uid_str, subs in db.get("langganan", {}).items():
            if isinstance(subs, dict): subs=[subs]
            active=False
            for s in subs:
                exp=s.get("expire")
                if isinstance(exp,str):
                    try: exp=datetime.fromisoformat(exp)
                    except: continue
                if exp and exp>now:
                    active=True
                    break
            if not active:
                to_del.append(uid_str)
        for uid_str in to_del:
            del db["langganan"][uid_str]
            removed+=1
        save_db()
        await context.bot.send_message(chat_id=uid, text=f"✅ {removed} user expired dihapus paketnya", reply_markup=kb_admin_panel())
        try: await q.message.delete()
        except: pass
        return
    if data=="admin_set_webhook":
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text=f"🔧 WEBHOOK: {RAILWAY_URL}/whatsapp-webhook", reply_markup=kb_admin_panel())
        return
    if data=="admin_broadcast":
        if not is_admin(uid): return
        context.user_data["awaiting_broadcast"]=True
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text="📢 MODE BROADCAST\nKirim pesan broadcast\nKetik /batal untuk batal", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batal",callback_data="back_main")]]))
        return
    if data=="admin_cek_aktif":
        aktif=[]
        now = datetime.now()
        for uid_str, subs in db["langganan"].items():
            if isinstance(subs, dict): subs = [subs]
            is_active = False
            kota_list_str = ""
            for s in subs:
                exp = s.get("expire")
                if isinstance(exp, str):
                    try: exp=datetime.fromisoformat(exp)
                    except: continue
                if exp and isinstance(exp,datetime) and exp>now:
                    is_active = True
                    qq=s.get("kuota",0)
                    uu=s.get("used_kuota",0)
                    kota_list_str += f"{s.get('kota','')} (sisa {qq-uu}/{qq} {exp.strftime('%d/%m')}), "
            if is_active:
                info=db["user_info"].get(uid_str,{})
                aktif.append(f"🆔 {uid_str}\n👤 {info.get('nama','-')}\n🏙️ Aktif: {kota_list_str[:-2]}\n---")
        txt="❌ Tidak ada user aktif" if not aktif else "✅ ID AKTIF - KOTA DIPILIH\n\n" + "\n".join(aktif[:20])
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text=txt, reply_markup=kb_admin_panel())
        return
    if data=="admin_hapus_list":
        buttons=[]
        for uid_str in list(db["langganan"].keys())[:30]:
            info=db["user_info"].get(uid_str,{}); buttons.append([InlineKeyboardButton(f"🗑️ {uid_str} | {info.get('nama','-')[:12]}",callback_data=f"admin_del_{uid_str}")])
        buttons.append([InlineKeyboardButton("🏠 KEMBALI MENU UTAMA",callback_data="back_main")])
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text="🗑️ HAPUS ID USER", reply_markup=InlineKeyboardMarkup(buttons))
        return
    if data.startswith("admin_del_"):
        target=data.replace("admin_del_",""); kb=InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ YA HAPUS {target}",callback_data=f"admin_confirm_del_{target}"),InlineKeyboardButton("❌ BATAL",callback_data="admin_hapus_list")]])
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text=f"⚠️ YAKIN HAPUS ID {target}?", reply_markup=kb)
        return
    if data.startswith("admin_confirm_del_"):
        target=data.replace("admin_confirm_del_","")
        if target in db["user_info"]: db["user_info"][target]["kotas"]=[]
        if target in db["langganan"]: del db["langganan"][target]
        if target in db["langganan_cari"]: del db["langganan_cari"][target]
        save_db()
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text=f"✅ ID {target} berhasil dihapus", reply_markup=kb_admin_panel())
        return
    if data=="admin_tambah_blacklist":
        if not is_admin(uid): return
        context.user_data["awaiting_tambah_blacklist"]=True
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text="➕ TAMBAH BLACKLIST BULK\n\nKirim nomor sekaligus, contoh:\n/Adds\n081223455666\n089737388383\n087876273838\n089828828288\n\nAtau tanpa /Adds, langsung nomor per baris / koma:\n0812..., 0813...\n\nSistem otomatis masuk ke NO BLACKLIST", reply_markup=kb_back_main_only())
        return
    if data=="admin_hapus_blacklist":
        if not is_admin(uid): return
        context.user_data["awaiting_hapus_blacklist"]=True
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text="➖ HAPUS NO BLACKLIST\n\nKirim nomor yang ingin dihapus dari blacklist (bisa bulk):", reply_markup=kb_back_main_only())
        return
    if data=="admin_blacklist_menu":
        if not is_admin(uid): return
        bl = db.get("blacklist",[])
        txt=f"🚫 KELOLA BLACKLIST\n\n📊 Total: {len(bl)} nomor"
        kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ TAMBAH BLACKLIST (BULK)", callback_data="admin_tambah_blacklist"), InlineKeyboardButton("➖ HAPUS BLACKLIST", callback_data="admin_hapus_blacklist")],
            [InlineKeyboardButton("📋 LIHAT BLACKLIST", callback_data="menu_blacklist")],
            [InlineKeyboardButton("⬅️ Kembali", callback_data="admin_menu")]
        ])
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text=txt, reply_markup=kb)
        return


    if data.startswith("prov_"):
        _, prov_id, prov_nama = data.split("_",2)
        context.user_data["prov_id"]=prov_id; context.user_data["prov_nama"]=prov_nama
        kota_list=get_kota(prov_id)
        if not kota_list:
            await q.message.edit_text("❌ Gagal ambil data kota, coba lagi.")
            return
        buttons=[]
        for k in kota_list:
            buttons.append([InlineKeyboardButton(f"{k['name'].upper()}", callback_data=f"kota_{k['id']}_{k['name']}")])
        buttons.append([InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")])
        await q.message.edit_text(f"🌍 Provinsi *{prov_nama.upper()}*\nPilih Kota/Kabupaten:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data.startswith("kota_"):
        _, kota_id, kota_nama = data.split("_", 2)
        context.user_data["kota_id"] = kota_id
        context.user_data["kota_nama"] = kota_nama
        context.user_data["selected_kec"] = []
        context.user_data["kec_list"] = get_kecamatan(kota_id)
        kec_list = context.user_data["kec_list"]
        if not kec_list:
            await q.message.edit_text("❌ Gagal ambil kecamatan, coba lagi"); return
        kb = build_kec_keyboard(kota_nama, kec_list, [], context.user_data["prov_id"], context.user_data["prov_nama"])
        await q.message.edit_text(f" {context.user_data['prov_nama']} > *{kota_nama}*\n\n✅ Pilih lebih dari 1 kecamatan bos!\nCentang beberapa, lalu klik SIMPAN:\n\n🔸 = Belum dipilih\n✅ = Sudah dipilih\n\nDipilih: 0 kecamatan", parse_mode="Markdown", reply_markup=kb)
        return
    if data.startswith("kec_toggle_"):
        _, _, kec_id, kec_nama = data.split("_", 3)
        selected = context.user_data.get("selected_kec", [])
        if kec_nama in selected: selected.remove(kec_nama)
        else: selected.append(kec_nama)
        context.user_data["selected_kec"] = selected
        kec_list = context.user_data.get("kec_list", [])
        kota_nama = context.user_data.get("kota_nama", "")
        prov_id = context.user_data.get("prov_id", "")
        prov_nama = context.user_data.get("prov_nama", "")
        kb = build_kec_keyboard(kota_nama, kec_list, selected, prov_id, prov_nama)
        await q.message.edit_text(f" {prov_nama} > *{kota_nama}*\n\nDipilih: {len(selected)} kecamatan", parse_mode="Markdown", reply_markup=kb)
        return
    if data=="kec_clear":
        context.user_data["selected_kec"] = []
        kec_list = context.user_data.get("kec_list", [])
        kota_nama = context.user_data.get("kota_nama", "")
        prov_id = context.user_data.get("prov_id", "")
        prov_nama = context.user_data.get("prov_nama", "")
        kb = build_kec_keyboard(kota_nama, kec_list, [], prov_id, prov_nama)
        await q.message.edit_text(f" {prov_nama} > *{kota_nama}*\n\nDipilih: 0 kecamatan", parse_mode="Markdown", reply_markup=kb)
        return
    if data=="kec_save":
        selected = context.user_data.get("selected_kec", [])
        if not selected: await q.answer("❌ Belum pilih!", show_alert=True); return
        prov = context.user_data.get("prov_nama")
        kota = context.user_data.get("kota_nama")
        if str(uid) not in db["user_info"]: db["user_info"][str(uid)] = {}
        if "kotas" not in db["user_info"][str(uid)]: db["user_info"][str(uid)]["kotas"] = []
        added=[]
        for kec_nama in selected:
            entry = f"{prov} | {kota} | {kec_nama}"
            if entry not in db["user_info"][str(uid)]["kotas"]:
                db["user_info"][str(uid)]["kotas"].append(entry); added.append(entry)
        if added:
            # Kaitkan paket aktif dengan KOTA yang dipilih.
            if not is_admin(uid):
                assign_subscription_to_selected_city(uid, kota)
            save_db()
            # Notify admin
            try:
                ui = db["user_info"].get(str(uid), {})
                nm = ui.get("nama", getattr(q.from_user, "first_name", "-"))
                for aid in ADMIN_IDS:
                    if aid != uid:
                        try: await context.bot.send_message(chat_id=aid, text=f"🔔 USER PILIH KOTA\n👤 {nm}\n🆔 {uid}\n🌍 {prov} | {kota}\n📍 {', '.join(selected[:10])}\n⏰ {datetime.now().strftime('%d/%m %H:%M')}")
                        except: pass
            except Exception as e: logger.error(f"notify admin error {e}")
            await q.message.edit_text(f"✅ Berhasil {len(added)} kecamatan", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]]))
        else: await q.message.edit_text(f"⚠️ Sudah ada semua!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_main")]]))
        context.user_data["selected_kec"] = []
        return
    if data.startswith("kec_ALL"):
        prov = context.user_data.get("prov_nama"); kota = context.user_data.get("kota_nama")
        kec_list = context.user_data.get("kec_list", [])
        if not kec_list:
            await q.message.edit_text("❌ Daftar kecamatan tidak tersedia, coba lagi.")
            return

        if str(uid) not in db["user_info"]:
            db["user_info"][str(uid)] = {}
        if "kotas" not in db["user_info"][str(uid)]:
            db["user_info"][str(uid)]["kotas"] = []

        added = 0
        for kec in kec_list:
            kec_nama = str(kec.get("name", "")).strip()
            if not kec_nama:
                continue
            entry = f"{prov} | {kota} | {kec_nama}"
            if entry not in db["user_info"][str(uid)]["kotas"]:
                db["user_info"][str(uid)]["kotas"].append(entry)
                added += 1

        if added:
            if not is_admin(uid):
                assign_subscription_to_selected_city(uid, kota)
            save_db()
            try:
                ui = db["user_info"].get(str(uid), {})
                nm = ui.get("nama", getattr(q.from_user, "first_name", "-"))
                for aid in ADMIN_IDS:
                    if aid != uid:
                        try: await context.bot.send_message(chat_id=aid, text=f"🔔 USER PILIH KOTA (ALL)\n👤 {nm}\n🆔 {uid}\n🌍 {prov} | {kota}\n📦 {added} kecamatan\n⏰ {datetime.now().strftime('%d/%m %H:%M')}")
                        except: pass
            except Exception as e: logger.error(f"notify admin all error {e}")
            await q.message.edit_text(
                f"✅ Berhasil menambahkan {added} kecamatan di {kota}.\n"
                f"📍 Pesan WA tetap wajib mengandung KOTA + KECAMATAN.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]])
            )
        else:
            await q.message.edit_text(
                f"⚠️ Semua kecamatan di {kota} sudah ada.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_main")]])
            )
        return
    if data.startswith("kec_") and not data.startswith("kec_toggle_") and not data.startswith("kec_save") and not data.startswith("kec_clear") and not data.startswith("kec_ALL"):
        _, kec_id, kec_nama = data.split("_", 2)
        prov = context.user_data.get("prov_nama"); kota = context.user_data.get("kota_nama")
        entry = f"{prov} | {kota} | {kec_nama}"
        if str(uid) not in db["user_info"]: db["user_info"][str(uid)] = {}
        if "kotas" not in db["user_info"][str(uid)]: db["user_info"][str(uid)]["kotas"] = []
        if entry not in db["user_info"][str(uid)]["kotas"]:
            db["user_info"][str(uid)]["kotas"].append(entry)
            if not is_admin(uid):
                assign_subscription_to_selected_city(uid, kota)
            save_db()
            try:
                ui = db["user_info"].get(str(uid), {})
                nm = ui.get("nama", getattr(q.from_user, "first_name", "-"))
                for aid in ADMIN_IDS:
                    if aid != uid:
                        try: await context.bot.send_message(chat_id=aid, text=f"🔔 USER PILIH KOTA\n👤 {nm}\n🆔 {uid}\n🌍 {entry}\n⏰ {datetime.now().strftime('%d/%m %H:%M')}")
                        except: pass
            except Exception as e: logger.error(f"notify admin single error {e}")
            await q.message.edit_text(f"✅ Berhasil:\n {entry}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]]))
        else: 
            await q.message.edit_text(f"⚠️ Sudah ada:\n {entry}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]]))
        return
    if data.startswith("topup_"):
        paket_type = data.replace("topup_","")
        context.user_data["paket_type"]=paket_type
        if paket_type=="tambah":
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("1 MINGGU - 50K",callback_data="paket_tambah_1minggu")],[InlineKeyboardButton("1 BULAN - 150K",callback_data="paket_tambah_1bulan")],[InlineKeyboardButton("2 BULAN - 250K",callback_data="paket_tambah_2bulan")],[InlineKeyboardButton("⬅️ Kembali",callback_data="back_main")]])
            await q.message.delete(); await context.bot.send_message(chat_id=uid,text=f"{REKENING_TEXT}\n\n💳 PILIH PAKET TAMBAH KOTA\n*Sebutkan Nama Kota saat transfer!*",reply_markup=kb)
        else:
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("1 MINGGU - 15K",callback_data="paket_cari_1minggu")],[InlineKeyboardButton("1 BULAN - 50K",callback_data="paket_cari_1bulan")],[InlineKeyboardButton("2 BULAN - 100K",callback_data="paket_cari_2bulan")],[InlineKeyboardButton("⬅️ Kembali",callback_data="back_main")]])
            await q.message.delete(); await context.bot.send_message(chat_id=uid,text=f"{REKENING_TEXT}\n\n🔍 PILIH PAKET CARI DATA",reply_markup=kb)
        return
    if data.startswith("paket_"):
        _, ptype, pkey = data.split("_",2)
        context.user_data["paket_pilih"]=pkey; context.user_data["paket_type"]=ptype
        if ptype=="tambah": p=PAKET_TAMBAH.get(pkey,PAKET_TAMBAH["1minggu"])
        else: p=PAKET_CARI.get(pkey,PAKET_CARI["1minggu"])
        text = f"{REKENING_TEXT}\n\n🎁 PAKET DIPILIH: {p['nama']} - Rp {p['harga']:,}\n\n*⚠️ PENTING!*\nKetik NAMA KOTA yang mau diaktifkan di caption foto transfer!\n\nSetelah transfer, kirim foto buktinya disini ya! 📸"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data=f"topup_{ptype}")]])
        await q.message.delete()
        await context.bot.send_message(chat_id=uid, text=text, reply_markup=kb)
        return
    if data.startswith("acc_") or data.startswith("dec_"):
        if not is_admin(uid): return
        parts=data.split("_"); action=parts[0]; ptype=parts[1]; target_uid=parts[2]; pkey=parts[3] if len(parts)>3 else "1minggu"
        target_uid=int(target_uid)
        
        # ===== LOGIKA AKTIVASI PER KOTA BARU =====
        if action=="acc":
            if ptype=="tambah":
                p=PAKET_TAMBAH.get(pkey,PAKET_TAMBAH["1minggu"])
                expire=datetime.now()+timedelta(days=p["hari"])
                
                # Ambil nama kota dari caption foto
                caption = q.message.caption or ""
                lines = caption.split("\n")
                kota_target = "Umum"
                for line in lines:
                    if "Kota:" in line:
                        kota_target = line.replace("Kota:", "").strip()
                        break

                # Jika bukti transfer tidak menyebut kota dengan benar, gunakan KOTA
                # yang sudah dipilih user di menu TAMBAH KOTA.
                placeholder = kota_target.strip().upper() in {"", "TIDAK ADA KOTA", "UMUM", "TIDAK DIKETAHUI"}
                if placeholder:
                    selected_cities = _unique_selected_cities(target_uid)
                    if selected_cities:
                        existing = {str(x.get("kota", "")).strip().upper() for x in db.get("langganan", {}).get(str(target_uid), [])}
                        kota_target = next((c for c in selected_cities if c.upper() not in existing), selected_cities[0])
                
                # Simpan ke database list
                if str(target_uid) not in db["langganan"]: 
                    db["langganan"][str(target_uid)] = []
                elif isinstance(db["langganan"][str(target_uid)], dict):
                    # Legacy migration
                    old_val = db["langganan"][str(target_uid)]
                    db["langganan"][str(target_uid)] = [old_val]
                
                # Cegah duplikat kota
                city_exists = False
                for s in db["langganan"][str(target_uid)]:
                    if s.get("kota", "").upper() == kota_target.upper():
                        s["expire"] = expire
                        s["paket"] = pkey
                        s["used"] = False # Reset pemakaian
                        city_exists = True
                        break
                
                if not city_exists:
                    db["langganan"][str(target_uid)].append({
                        "kota": kota_target,
                        "paket": pkey,
                        "expire": expire,
                        "used": False # Awalnya BELUM dipakai
                    })
                    
                save_db()
                await q.message.edit_caption(caption=(q.message.caption or "")+f"\n\n✅ DISETUJUI - Aktif sampai {expire.strftime('%d/%m/%Y')}",reply_markup=None)
                await context.bot.send_message(chat_id=target_uid,text=f"✅ TOP UP DISETUJUI ✅\n\n📦 Paket {p['nama']} untuk kota {kota_target} aktif sampai {expire.strftime('%d/%m/%Y')}\n🎉 Sekarang kamu bisa gunakan 1x kesempatan untuk 🌍 TAMBAH KOTA!",reply_markup=kb_main(target_uid))
            else:
                p=PAKET_CARI.get(pkey,PAKET_CARI["1minggu"])
                expire=datetime.now()+timedelta(days=p["hari"])
                db["langganan_cari"][str(target_uid)]={"paket":pkey,"expire":expire}
                save_db()
                await q.message.edit_caption(caption=(q.message.caption or "")+f"\n\n✅ DISETUJUI - Aktif sampai {expire.strftime('%d/%m/%Y')}",reply_markup=None)
                await context.bot.send_message(chat_id=target_uid,text=f"✅ TOP UP CARI DATA DISETUJUI ✅\n\n📦 Paket {p['nama']} aktif sampai {expire.strftime('%d/%m/%Y')}\n🎉 Sekarang bisa pakai 🔍 CARI DATA LAINNYA!",reply_markup=kb_main(target_uid))
        else:
            await q.message.edit_caption(caption=q.message.caption+"\n\n❌ DITOLAK",reply_markup=None)
            await context.bot.send_message(chat_id=target_uid,text="❌ Top Up DITOLAK admin. Hubungi @Hambali1995")

async def text_handler(update,context):
    uid=update.effective_user.id
    text=update.message.text.strip()
    # Handle /batal command untuk cancel cari data
    if text.lower() in ["/batal", "batal"]:
        if context.user_data.get("awaiting_cari_data") or context.user_data.get("awaiting_cari_lainnya"):
            context.user_data.pop("awaiting_cari_data", None)
            context.user_data.pop("awaiting_cari_lainnya", None)
            await update.message.reply_text("❌ Pencarian dibatalkan", reply_markup=kb_main(uid))
            return


    # === TAMBAH BLACKLIST BULK - support /Adds ===
    if text.upper().startswith("/ADDS") or text.upper().startswith("/ADD "):
        if not is_admin(uid):
            await update.message.reply_text("❌ Hanya admin!", reply_markup=kb_back_main_only()); return
        # Parse numbers after /Adds
        # Format:
        # /Adds
        # 081...
        # 089...
        # Also support /Add 081... or /Adds 081..., 082...
        raw = text
        # Remove command prefix
        if raw.upper().startswith("/ADDS"):
            raw = raw[5:].strip()
        elif raw.upper().startswith("/ADD "):
            raw = raw[4:].strip()
        # If raw empty, check if next lines contain numbers - but this handler only gets one message, so we expect all numbers in same message
        # Split by newline, comma, space
        nums = re.split(r'[\n,\s]+', raw)
        # Also include lines that may be in original text after first line (text contains newlines)
        # text variable already contains newlines
        # So split again
        all_nums = []
        for token in nums:
            clean=''.join(filter(str.isdigit, token))
            if clean.startswith("62"): clean="0"+clean[2:]
            if len(clean)>=8:
                all_nums.append(clean)
        if not all_nums:
            await update.message.reply_text("❌ Tidak ada nomor valid ditemukan. Contoh:\n/Adds\n081223455666\n089737388383", reply_markup=kb_back_main_only()); return
        added=0
        for clean in all_nums:
            if clean not in db.get("blacklist",[]):
                db["blacklist"].append(clean); added+=1
        save_db()
        await update.message.reply_text(f"✅ Berhasil tambah {added} nomor ke BLACKLIST (dari {len(all_nums)} input)\n📊 Total sekarang: {len(db.get('blacklist',[]))} nomor\n\nOtomatis masuk ke NO BLACKLIST ✅", reply_markup=kb_admin_panel())
        return

    if context.user_data.get("awaiting_cari_data"):
        context.user_data["awaiting_cari_data"]=False
        query=text.strip().upper()
        if query=="/BATAL" or query=="BATAL":
            await update.message.reply_text("❌ Pencarian dibatalkan", reply_markup=kb_back_main_only()); return
        if not is_active_cari(uid) and not is_admin(uid):
            await update.message.reply_text("🔒 FITUR TERKUNCI 🔒\nSilahkan hubungi admin!", reply_markup=kb_back_main_only()); return
        history=load_wa_history()
        hasil=[]
        # Cari SEMUA pengirim yang mengandung kota tersebut (tidak dibatasi 20, tapi max 50 biar tidak spam berlebihan)
        for h in reversed(history[-10000:]):
            txt_upper = (h.get("text","") + " " + h.get("group","")).upper()
            if query in txt_upper:
                hasil.append(h)
        # Batasi maksimal 50 hasil biar tidak kebanyakan spam, tapi semua ditampilkan jika di bawah 50
        max_tampil = 50
        if len(hasil) > max_tampil:
            hasil = hasil[:max_tampil]
        if not hasil:
            await update.message.reply_text(f"❌ Data '{text}' tidak ditemukan\nCoba keyword lain!", reply_markup=kb_cari_data_lain())
        else:
            await update.message.reply_text(f"🔎 HASIL: {text}\n📊 Ditemukan {len(hasil)} pengirim untuk kota {text.upper()}\n⏳ Akan dikirim satu per satu (jeda 1 detik)...", reply_markup=kb_back_main_only())
            import asyncio
            for h in hasil:
                nomor=h.get("number","")
                clean=''.join(filter(str.isdigit,nomor))
                if clean.startswith("0"): clean="62"+clean[1:]
                msg=f"""Kota : {text.upper()}
Grup : {h.get('group')}
Pengirim : {h.get('sender')}
No WhatsApp : {h.get('number')}
━━━━━━━━━━━━━━━━━━━

ISI PESAN
{h.get('text')}
━━━━━━━━━━━━━━━━━━━
⚠️ Perhatian : untuk tetap waspada dan hati-hati disarankan untuk rekber, terimakasih.Sumber: https://t.me/Aakiwkiw_bot 🙏"""
                try:
                    if clean:
                        kb=kb_hasil_cari(clean)
                        await context.bot.send_message(chat_id=uid, text=msg, reply_markup=kb)
                    else:
                        await context.bot.send_message(chat_id=uid, text=msg, reply_markup=kb_hasil_cari(None))
                except Exception as e:
                    logger.error(f"send hasil cari error: {e}")
                await asyncio.sleep(1)
            await update.message.reply_text(f"✅ Selesai menampilkan {len(hasil)} data untuk {text.upper()}", reply_markup=kb_hasil_cari_selesai())
        return

    if context.user_data.get("awaiting_cek_blacklist"):
        context.user_data["awaiting_cek_blacklist"]=False
        clean=''.join(filter(str.isdigit,text))
        if clean.startswith("62"): clean="0"+clean[2:]
        if len(clean)>=8:
            if clean in get_all_blacklist():
                await update.message.reply_text(f"🚫 Nomor {clean} ADA di BLACKLIST\n📊 Total: {len(db.get('blacklist',[]))} nomor", reply_markup=kb_blacklist_menu())
            else:
                await update.message.reply_text(f"✅ Nomor {clean} BELUM ADA di blacklist\n📊 Total: {len(db.get('blacklist',[]))} nomor", reply_markup=kb_blacklist_menu())
        else:
            await update.message.reply_text("❌ Format salah\nContoh: 083123456789", reply_markup=kb_back_main_only())
        return

    if context.user_data.get("awaiting_tambah_blacklist"):
        context.user_data["awaiting_tambah_blacklist"]=False
        # Support bulk with or without /Adds prefix
        raw=text
        if raw.upper().startswith("/ADDS"):
            raw=raw[5:]
        elif raw.upper().startswith("/ADD"):
            raw=raw[4:]
        nums = re.split(r'[\n,\s]+', raw)
        added=0
        invalid=0
        for n in nums:
            clean=''.join(filter(str.isdigit,n))
            if not clean: continue
            if clean.startswith("62"): clean="0"+clean[2:]
            if len(clean)>=8:
                if clean not in db.get("blacklist",[]):
                    db["blacklist"].append(clean); added+=1
            else:
                if clean: invalid+=1
        save_db()
        await update.message.reply_text(f"✅ Berhasil tambah {added} nomor ke BLACKLIST (bulk)\n📊 Total sekarang: {len(db.get('blacklist',[]))} nomor\n\nOtomatis masuk ke menu NO BLACKLIST ✅", reply_markup=kb_admin_panel())
        return

    if context.user_data.get("awaiting_hapus_blacklist"):
        context.user_data["awaiting_hapus_blacklist"]=False
        nums = re.split(r'[\n,\s]+', text)
        removed=0
        for n in nums:
            clean=''.join(filter(str.isdigit,n))
            if clean.startswith("62"): clean="0"+clean[2:]
            if clean in get_all_blacklist():
                db["blacklist"].remove(clean); removed+=1
        save_db()
        await update.message.reply_text(f"✅ Berhasil hapus {removed} nomor dari BLACKLIST\n📊 Total: {len(db.get('blacklist',[]))} nomor", reply_markup=kb_admin_panel())
        return

    if any(x in text for x in ["HAPUS KOTA SAYA", "TOP UP SALDO", "PILIH KEYWORD", "BANTUAN"]):
        await update.message.reply_text("ℹ️ Menu tersebut sudah dihapus. Silahkan gunakan menu baru:", reply_markup=kb_main(uid))
        return

    if context.user_data.get("awaiting_broadcast"):
        if text.lower()=="/batal":
            context.user_data["awaiting_broadcast"]=False
            await update.message.reply_text("❌ Broadcast dibatalkan",reply_markup=kb_back_main_only()); return
        count=0
        for uid_str in db.get("user_info",{}).keys():
            try:
                await context.bot.send_message(chat_id=int(uid_str),text=f"📢 BROADCAST\n\n{text}")
                count+=1
            except: pass
        context.user_data["awaiting_broadcast"]=False
        await update.message.reply_text(f"✅ Broadcast terkirim ke {count} user",reply_markup=kb_back_main_only()); return

    if context.user_data.get("awaiting_cari_lainnya"):
        if not is_active_cari(uid) and not is_admin(uid):
            await update.message.reply_text("🔒 FITUR TERKUNCI 🔒\nSilahkan TOP UP CARI DATA LAINNYA dulu bos!", reply_markup=kb_back_main_only())
            context.user_data["awaiting_cari_lainnya"]=False
            return
        query=text.strip().upper()
        if query=="/BATAL":
            context.user_data["awaiting_cari_lainnya"]=False
            await update.message.reply_text("❌ Pencarian dibatalkan", reply_markup=kb_back_main_only()); return
        history=load_wa_history()
        hasil=[]
        for h in reversed(history[-10000:]):
            txt_upper = (h.get("text","") + " " + h.get("group","")).upper()
            if query in txt_upper:
                hasil.append(h)
        max_tampil = 50
        if len(hasil) > max_tampil:
            hasil = hasil[:max_tampil]
        if not hasil:
            await update.message.reply_text(f"❌ Data '{text}' tidak ditemukan di history WA\nCoba keyword lain bos!", reply_markup=kb_cari_data_lain())
        else:
            await update.message.reply_text(f"🔎 HASIL: {text}\n📊 Ditemukan {len(hasil)} pengirim untuk kota {text.upper()}\n⏳ Akan dikirim satu per satu (jeda 1 detik)...", reply_markup=kb_back_main_only())
            import asyncio
            for h in hasil:
                nomor=h.get("number","")
                clean=''.join(filter(str.isdigit,nomor))
                if clean.startswith("0"): clean="62"+clean[1:]
                msg=f"""Kota : {text.upper()}
Grup : {h.get('group')}
Pengirim : {h.get('sender')}
No WhatsApp : {h.get('number')}
━━━━━━━━━━━━━━━━━━━
ISI PESAN
{h.get('text')}
━━━━━━━━━━━━━━━━━━━
⚠️ Perhatian : untuk tetap waspada dan hati-hati disarankan untuk rekber, terimakasih.Sumber: https://t.me/Aakiwkiw_bot 🙏"""
                try:
                    if clean:
                        kb=kb_hasil_cari(clean)
                        await context.bot.send_message(chat_id=uid, text=msg, reply_markup=kb)
                    else:
                        await context.bot.send_message(chat_id=uid, text=msg, reply_markup=kb_hasil_cari(None))
                except Exception as e:
                    logger.error(f"send hasil cari lainnya error: {e}")
                await asyncio.sleep(1)
            await update.message.reply_text(f"✅ Selesai menampilkan {len(hasil)} data untuk {text.upper()}", reply_markup=kb_hasil_cari_selesai())
        context.user_data["awaiting_cari_lainnya"]=False
        return

    await update.message.reply_text("ℹ️ Gunakan menu di bawah:", reply_markup=kb_main(uid))



async def foto_handler(update,context):
    uid=update.effective_user.id
    if not update.message.photo: return
    paket_key=context.user_data.get("paket_pilih","1minggu"); paket_type=context.user_data.get("paket_type","tambah")
    if paket_type=="tambah": p=PAKET_TAMBAH.get(paket_key,PAKET_TAMBAH["1minggu"])
    else: p=PAKET_CARI.get(paket_key,PAKET_CARI["1minggu"])
    file_id=update.message.photo[-1].file_id
    
    # Ambil kota dari caption foto user
    caption_user = update.message.caption or ""
    kota_dicetak = "Tidak ada kota"
    if caption_user:
         # Ambil kata pertama sebagai nama kota
        kota_dicetak = caption_user.strip().split()[0]
    
    caption_admin = f"💳 BUKTI TOP UP {paket_type.upper()} MASUK\n🆔 ID: {uid}\n🎁 Paket: {p['nama']} - Rp {p['harga']:,}\n📍 Kota: {kota_dicetak}"
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ SETUJU",callback_data=f"acc_{paket_type}_{uid}_{paket_key}"),InlineKeyboardButton("❌ TOLAK",callback_data=f"dec_{paket_type}_{uid}_{paket_key}")]])
    for admin_id in ADMIN_IDS:
        try: await context.bot.send_photo(chat_id=admin_id,photo=file_id,caption=caption_admin,reply_markup=kb)
        except: pass
    await update.message.reply_text("✅ 📸 Bukti terkirim ke Admin!\n⏳ Menunggu persetujuan (max 1x24 jam)\n🔔 Nanti ada notifikasi otomatis!",reply_markup=kb_main(uid))

async def cmd_profil(update,context):
    uid=update.effective_user.id; txt=await get_profil_text(uid,update.effective_user); await update.message.reply_text(txt,reply_markup=kb_main(uid))

async def cmd_status(update,context):
    uid=update.effective_user.id; txt=await get_status_text(uid); await update.message.reply_text(txt,reply_markup=kb_main(uid))

async def cmd_cek(update,context):
    uid=update.effective_user.id
    text=update.message.text.strip()
    if text.lower().startswith("/cek "):
        number=text[5:].strip()
    else:
        if context.args:
            number=" ".join(context.args)
        else:
            await update.message.reply_text("🔍 Format: /cek 083123456789",reply_markup=kb_main(uid)); return
    clean=''.join(filter(str.isdigit,number))
    if clean.startswith("62"): clean="0"+clean[2:]
    if len(clean)>=8:
        if clean in get_all_blacklist():
            await update.message.reply_text(f"🚫 Nomor {clean} ADA di BLACKLIST kami 🚫\n📊 Total: {len(db.get('blacklist',[]))} nomor",reply_markup=kb_main(uid))
        else:
            await update.message.reply_text(f"✅ Nomor {clean} BELUM ADA di database\n📊 Total: {len(db.get('blacklist',[]))} nomor",reply_markup=kb_main(uid))
    else:
        await update.message.reply_text("❌ Format salah\nContoh: /cek 083123456789",reply_markup=kb_main(uid))

async def cmd_backup(update,context):
    uid=update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Hanya admin",reply_markup=kb_main(uid)); return
    try:
        with open(DB_FILE,"r",encoding="utf-8") as f:
            data=json.load(f)
        txt=f"💾 BACKUP DB\n👤 User: {len(data.get('user_info',{}))}\n🎁 Tambah: {len(data.get('langganan',{}))}\n🔎 Cari: {len(data.get('langganan_cari',{}))}\n🚫 Blacklist: {len(data.get('blacklist',[]))}"
        await update.message.reply_text(txt,reply_markup=kb_main(uid))
        await context.bot.send_document(chat_id=uid, document=open(DB_FILE,"rb"), filename="bot_database.json")
        if os.path.exists(DB_FILE_PERSISTENT):
            await context.bot.send_document(chat_id=uid, document=open(DB_FILE_PERSISTENT,"rb"), filename="bot_database_persistent.json")
    except Exception as e:
        await update.message.reply_text(f"❌ Backup fail: {e}",reply_markup=kb_main(uid))

async def cmd_test_location(update, context):
    """Test apakah teks akan match dengan wilayah user"""
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Hanya admin", reply_markup=kb_main(uid))
        return
    
    if not context.args:
        await update.message.reply_text(
            "🔎 Format: /testlokasi [teks]\n\n"
            "Contoh: /testlokasi Bandung\n"
            "Bot akan mengecek apakah teks tersebut match dengan wilayah user",
            reply_markup=kb_main(uid)
        )
        return
    
    test_text = " ".join(context.args).upper()
    results = []
    
    for uid_str, uinfo in db.get("user_info", {}).items():
        kotas = uinfo.get("kotas", [])
        is_match, matched = check_location_match(test_text, kotas)
        if is_match:
            results.append(f"🆔 {uid_str} - {uinfo.get('nama', '-')} -> {matched}")
    
    if results:
        await update.message.reply_text(
            f"✅ Match ditemukan untuk '{test_text}':\n\n" + "\n".join(results[:20]),
            reply_markup=kb_main(uid)
        )
    else:
        await update.message.reply_text(
            f"❌ Tidak ada match untuk '{test_text}'",
            reply_markup=kb_main(uid)
        )


async def contact_handler(update, context):
    """Handler untuk share contact / nomor telepon"""
    uid = update.effective_user.id
    try:
        contact = update.message.contact
        if contact:
            phone = contact.phone_number
            # bersihkan nomor
            clean = ''.join(filter(str.isdigit, phone))
            if clean.startswith("62"):
                clean = "0" + clean[2:]
            await update.message.reply_text(
                f"📞 Nomor terdeteksi: {clean}\nGunakan /cek {clean} untuk cek blacklist",
                reply_markup=kb_main(uid)
            )
        else:
            await update.message.reply_text("❌ Tidak ada kontak terdeteksi", reply_markup=kb_main(uid))
    except Exception as e:
        logger.error(f"contact_handler error: {e}")
        await update.message.reply_text("❌ Gagal proses kontak", reply_markup=kb_main(uid))


# ========== SETUP APPLICATION ==========
application = Application.builder().token(TOKEN).build()

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("profil", cmd_profil))
application.add_handler(CommandHandler("status", cmd_status))
application.add_handler(CommandHandler("cek", cmd_cek))
application.add_handler(CommandHandler("backup", cmd_backup))
application.add_handler(CommandHandler("testlokasi", cmd_test_location))
application.add_handler(CallbackQueryHandler(cb_handler))
application.add_handler(MessageHandler(filters.CONTACT, contact_handler))
application.add_handler(MessageHandler(filters.PHOTO, foto_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

import asyncio
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

async def setup_webhook():
    try:
        await application.initialize()
        await application.start()
        url = RAILWAY_URL if RAILWAY_URL.startswith("http") else f"https://{RAILWAY_URL}"
        webhook_url = f"{url}/{TOKEN}"
        logger.info(f"Setting webhook to {webhook_url}")
        await application.bot.set_webhook(url=webhook_url, drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"setup_webhook error: {e}")

# Setup webhook saat startup, bukan saat import di Railway
try:
    if TOKEN and "123:ABC" not in TOKEN:
        loop.run_until_complete(setup_webhook())
except Exception as e:
    logger.warning(f"Webhook setup skipped: {e}")

@flask_app.route("/")
def index():
    return "Bot Active - Webhook Mode - OK"

@flask_app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return "ok", 200
        update = Update.de_json(data, application.bot)
        try:
            loop.run_until_complete(application.process_update(update))
        except Exception as e:
            logger.exception(f"Telegram process_update error: {e}")
        return "ok", 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "ok", 200

if __name__ == "__main__":
    logger.info(f"🚀 WEBHOOK MODE - Port {PORT}")
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
