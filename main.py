# -*- coding: utf-8 -*-
import os, json, requests, threading
from datetime import datetime, timedelta
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

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
    "1minggu": {"nama": "1 MINGGU", "harga": 50000, "hari": 7},
    "1bulan": {"nama": "1 BULAN", "harga": 150000, "hari": 30},
    "2bulan": {"nama": "2 BULAN", "harga": 250000, "hari": 60},
}
PAKET_CARI = {
    "1minggu": {"nama": "1 MINGGU", "harga": 15000, "hari": 7},
    "1bulan": {"nama": "1 BULAN", "harga": 50000, "hari": 30},
    "2bulan": {"nama": "2 BULAN", "harga": 100000, "hari": 60},
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
    for p in [DB_FILE_PERSISTENT, DB_FILE]:
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for key in ["langganan", "langganan_cari"]:
                        for k,v in data.get(key, {}).items():
                            if v.get("expire"):
                                try: v["expire"] = datetime.fromisoformat(v["expire"])
                                except: v["expire"] = None
                    if "blacklist" not in data: data["blacklist"] = []
                    if "pending_hapus_kota" not in data: data["pending_hapus_kota"] = []
                    if "user_info" not in data: data["user_info"] = {}
                    if "langganan" not in data: data["langganan"] = {}
                    if "langganan_cari" not in data: data["langganan_cari"] = {}
                    return data
        except: continue
    return {"user_info": {}, "langganan": {}, "langganan_cari": {}, "blacklist": [], "pending_hapus_kota": []}

def save_db():
    tmp = json.loads(json.dumps(db, default=str))
    for key in ["langganan", "langganan_cari"]:
        for k,v in db.get(key, {}).items():
            if v.get("expire") and isinstance(v["expire"], datetime):
                tmp[key][k]["expire"] = v["expire"].isoformat()
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(tmp, f, indent=2, ensure_ascii=False)
    except: pass
    try:
        os.makedirs(os.path.dirname(DB_FILE_PERSISTENT), exist_ok=True)
        with open(DB_FILE_PERSISTENT, "w", encoding="utf-8") as f:
            json.dump(tmp, f, indent=2, ensure_ascii=False)
    except: pass

def normalize_number(num):
    clean=''.join(filter(str.isdigit, str(num)))
    if not clean: return None
    if clean.startswith("62"):
        clean="0"+clean[2:]
    if clean.startswith("8"):
        clean="0"+clean
    return clean if len(clean)>=9 else None

def add_blacklist(num):
    clean=normalize_number(num)
    if not clean: return False, None
    if "blacklist" not in db: db["blacklist"]=[]
    if clean in db["blacklist"]:
        return False, clean
    alt="62"+clean[1:] if clean.startswith("0") else clean
    if alt in db["blacklist"]:
        return False, clean
    db["blacklist"].append(clean)
    db["blacklist"]=list(dict.fromkeys(db["blacklist"]))
    save_db()
    return True, clean

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
flask_app = Flask(__name__)

def send_tg_message(chat_id, text, wa_number=None):
    if not TOKEN: return
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        reply_markup=None
        if wa_number:
            clean = wa_number.replace("0","62",1) if wa_number.startswith("0") else wa_number
            clean=''.join(filter(str.isdigit,clean))
            if clean: reply_markup={"inline_keyboard": [[{"text":"💬 Chat Pengirim di WA","url":f"https://wa.me/{clean}"}]]}
        payload={"chat_id":chat_id,"text":text,"disable_web_page_preview":True}
        if reply_markup: payload["reply_markup"]=reply_markup
        requests.post(url, json=payload, timeout=10)
    except: pass

@flask_app.route("/", methods=["GET"])
def home(): return f"🤖 BOT OK ✅ - Green {GREEN_API_ID} - RUNNING 🚀 - Data: {len(db.get('user_info',{}))} user 👤"

@flask_app.route("/whatsapp-webhook", methods=["POST"])
def whatsapp_webhook():
    try:
        data=request.get_json(force=True,silent=True)
        if not data: return "ok",200
        sender_data=data.get("senderData",{}); message_data=data.get("messageData",{})
        text=""; ttype=data.get("typeMessage","")
        if ttype=="textMessage": text=message_data.get("textMessageData",{}).get("textMessage","")
        elif ttype=="extendedTextMessage": text=message_data.get("extendedTextMessageData",{}).get("text","")
        if not text: text=message_data.get("textMessageData",{}).get("textMessage","") or message_data.get("extendedTextMessageData",{}).get("text","") or ""
        if not text: return "ok",200
        group_name=sender_data.get("chatName") or "Grup WA"
        sender_name=sender_data.get("senderName") or sender_data.get("senderContactName") or "Pengirim WA"
        sender_raw=sender_data.get("sender") or message_data.get("sender") or sender_data.get("senderContactId") or ""
        sender_number=sender_raw.split("@")[0] if "@" in sender_raw else sender_raw
        if sender_number.startswith("62"): sender_number_formatted="0"+sender_number[2:]
        else: sender_number_formatted=sender_number
        if not sender_number_formatted: sender_number_formatted=sender_data.get("chatId","").split("@")[0]
        try:
            history=load_wa_history()
            history.append({"group":group_name,"sender":sender_name,"number":sender_number_formatted,"text":text,"time":datetime.now().isoformat()})
            save_wa_history(history)
        except: pass
        try:
            with open(DB_FILE_PERSISTENT if os.path.exists(DB_FILE_PERSISTENT) else DB_FILE,"r",encoding="utf-8") as f: fresh_db=json.load(f)
        except: fresh_db={"user_info":db.get("user_info",{}),"langganan":db.get("langganan",{}),"langganan_cari":db.get("langganan_cari",{}),"blacklist":db.get("blacklist",[])}
        text_upper=text.upper()
        for uid_str,uinfo in fresh_db.get("user_info",{}).items():
            try:
                uid_int=int(uid_str)
                is_blacklisted = sender_number_formatted in fresh_db.get("blacklist",[])
                if is_blacklisted: continue
                kotas=uinfo.get("kotas",[]); cocok_wilayah=False; matched=""
                for k in kotas:
                    parts=[p.strip() for p in k.split("|")]
                    if len(parts)<3: continue
                    kab_clean=parts[1].upper().replace("KABUPATEN ","").replace("KOTA ","").strip()
                    kec_clean=parts[2].upper().strip()
                    if len(kab_clean)<3 or len(kec_clean)<3: continue
                    if kab_clean in text_upper and kec_clean in text_upper:
                        cocok_wilayah=True; matched=parts[1]+" | "+parts[2]; break
                custom_keywords=uinfo.get("custom_keywords",[]); cocok_keyword=False; matched_keyword=""
                for kw in custom_keywords:
                    if kw.upper() in text_upper:
                        cocok_keyword=True; matched_keyword=kw; break
                if cocok_wilayah or cocok_keyword:
                    if cocok_wilayah:
                        sub=fresh_db.get("langganan",{}).get(uid_str)
                        if sub:
                            exp=sub.get("expire")
                            if isinstance(exp,str):
                                try: exp=datetime.fromisoformat(exp)
                                except: exp=None
                            if exp and isinstance(exp,datetime) and exp<datetime.now():
                                if uid_int not in ADMIN_IDS and not cocok_keyword:
                                    continue
                    kota_masuk = matched if matched else f"KEYWORD: {matched_keyword}"
                    # EXTRACT PLAIN TEXT - TIDAK HTML
                    notes="\n\n━━━━━━━━━━━━━━━━\n⚠️ *PERHATIAN :*\nTETAP WASPADA DALAM BERTRANSAKSI,UNTUK LEBIH AMAN DISARANKAN GUNAKAN REKBER(REKENING BERSAMA)\nTERIMAKASIH BANYAK ATAS PERHATIANNYA.🙏"
                    # Bersihkan text dari html tag jika ada
                    clean_text = text
                    # remove possible html tags
                    import re
                    clean_text = re.sub(r'<[^>]+>', '', clean_text)
                    msg=f"🤖 *INFO SEDULURAN BOT* 🤖\n\n🎰 GRUP: {group_name}\n👤 PENGIRIM: {sender_name}\n📱 NOMOR: {sender_number_formatted}\n\n{clean_text}{notes}"
                    send_tg_message(uid_int,msg,wa_number=sender_number_formatted)
            except: pass
        return "ok",200
    except Exception as e:
        print(f"Webhook err {e}")
        return "ok",200

def is_admin(uid): return uid in ADMIN_IDS
def is_expired(sub):
    if not sub: return True
    if sub.get("used", False):
        return True
    exp=sub.get("expire")
    if isinstance(exp,str):
        try: exp=datetime.fromisoformat(exp)
        except: return True
    return not exp or exp<datetime.now()
def is_active_tambah(uid):
    if is_admin(uid): return True
    sub=db["langganan"].get(str(uid))
    return sub and not is_expired(sub)
def is_active_cari(uid):
    if is_admin(uid): return True
    sub=db["langganan_cari"].get(str(uid))
    return sub and not is_expired(sub)

def is_user_id_aktif(uid):
    # USER ID AKTIF jika langganan TAMBAH KOTA atau CARI DATA masih aktif (atau admin)
    return is_active_tambah(uid) or is_active_cari(uid)

# === VALIDASI KEYWORD DILARANG PROVINSI / KOTA / KECAMATAN ===
FORBIDDEN_GEO_EXTRA = {
    "kabupaten","kota","kecamatan","provinsi","kelurahan","desa",
}

def _norm_kw(s):
    return s.strip().lower()

def is_geo_forbidden(keyword):
    kw = _norm_kw(keyword)
    if len(kw) < 3:
        return True, "Keyword terlalu pendek"
    # 1. Cek provinsi exact / contains
    for p in LIST_PROVINSI:
        nama = p["nama"].lower()
        if kw == nama or kw == nama.replace(" ",""):
            return True, f"'{keyword}' adalah nama PROVINSI ({p['nama']})"
        # jika keyword ada di dalam nama provinsi yang panjang, misal user ketik JAWA -> blokir
        if len(kw) >= 4 and (kw in nama or nama in kw):
            # hindari false positive untuk kata umum pendek, tapi untuk geo kita blokir
            if len(kw) >= 4:
                return True, f"'{keyword}' mengandung nama PROVINSI ({p['nama']})"
    # 2. Kata kunci umum geo
    for bad in FORBIDDEN_GEO_EXTRA:
        if bad in kw:
            return True, f"Keyword tidak boleh mengandung kata '{bad}' (geografis)"
    # 3. Cek apakah keyword adalah nama kota/kabupaten/kecamatan via API (cached check)
    # Kita lakukan pengecekan ringan: jika keyword cocok persis dengan data emsifa
    # Untuk performa, kita coba cek ke cache yang ada di memory (jika ada)
    # Jika tidak ada, kita skip heavy check, tapi tetap blokir pola yang mirip geo
    # Pola: jika keyword hanya 1-2 kata dan semuanya huruf dan umum seperti nama daerah, kita anggap geo jika user mencoba mengetik nama daerah
    # Solusi tegas: larang jika keyword hanya berupa nama daerah (kita cek via API hanya jika diperlukan)
    # Untuk akurasi, kita bisa coba fetch cepat (optional) - diabaikan jika offline
    try:
        # quick local list of kota besar untuk cegah bypass
        kota_besar = {"jakarta","bandung","surabaya","medan","bekasi","semarang","tangerang","depok","bogor","malang","yogyakarta","solo","palembang","makassar","batam","pekanbaru","padang","denpasar","samarinda","balikpapan","banjarmasin","manado","malang","cirebon","tasikmalaya","serang","lampung","jambi","bengkulu","pontianak","palangkaraya","kendari","palu","ambon","jayapura","mataram","kupang","banda aceh"}
        if kw in kota_besar:
            return True, f"'{keyword}' adalah nama KOTA/KABUPATEN (tidak boleh)"
    except:
        pass
    return False, ""


def kb_main(uid):
    keyboard=[
        ["👤 PROFIL ","🌍 TAMBAH KOTA "],
        ["🌠 WILAYAH DIPILIH ","🗑️ HAPUS KOTA SAYA "],
        ["📊 STATUS LANGGANAN ","💳 TOP UP SALDO "],
        ["🔎 CARI DATA LAINNYA ","🚀 PILIH KEYWORD "],
        ["🚫 DAFTAR BLACKLIST ","❓ BANTUAN "],
        ["🧑‍💻 HUBUNGI ADMIN "],
    ]
    if is_admin(uid): keyboard.append(["🧭 PANEL ADMIN "])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

def kb_provinsi():
    buttons=[]; row=[]
    for p in LIST_PROVINSI:
        row.append(InlineKeyboardButton(f" {p['nama'].title()}", callback_data=f"prov_{p['id']}_{p['nama']}"))
        if len(row)==2: buttons.append(row); row=[]
    if row: buttons.append(row)
    buttons.append([InlineKeyboardButton("⬅️ KEMBALI KE MENU 🏠", callback_data="back_main")])
    return InlineKeyboardMarkup(buttons)

def get_kota(prov_id):
    try: r=requests.get(f"https://www.emsifa.com/api-wilayah-indonesia/api/regencies/{prov_id}.json",timeout=10); return r.json()
    except: return []
def get_kecamatan(kota_id):
    try: r=requests.get(f"https://www.emsifa.com/api-wilayah-indonesia/api/districts/{kota_id}.json",timeout=10); return r.json()
    except: return []

async def get_status_text(uid):
    user_data=db["user_info"].get(str(uid),{}); jml=len(user_data.get("kotas",[])); sub=db["langganan"].get(str(uid)); sub_cari=db["langganan_cari"].get(str(uid))
    if is_admin(uid):
        txt="📊 STATUS LANGGANAN \n\n👑 Status: ADMIN UNLIMITED ♾️\n🏙️ Wilayah: "+str(jml)+" kota\n⏰ Exp: Tidak terbatas\n\n✅ Semua fitur aktif!"
    else:
        if sub and not is_expired(sub):
            exp=sub.get("expire")
            if isinstance(exp,str):
                try: exp=datetime.fromisoformat(exp)
                except: exp=None
            paket=PAKET_TAMBAH.get(sub.get("paket"),{}).get("nama","-"); exp_str=exp.strftime("%d/%m/%Y %H:%M") if exp else "-"
            txt=f"📊 STATUS LANGGANAN \n\n📦 Paket TAMBAH KOTA: {paket}\n⏰ Exp: {exp_str}\n🏙️ Wilayah: {jml} kota\n"
        else:
            txt=f"📊 STATUS LANGGANAN \n\n❌ Paket TAMBAH KOTA: Belum ada\n🏙️ Wilayah: {jml} kota\n💡 Silahkan TOP UP untuk buka fitur!\n"
        if sub_cari and not is_expired(sub_cari):
            exp=sub_cari.get("expire")
            if isinstance(exp,str):
                try: exp=datetime.fromisoformat(exp)
                except: exp=None
            paket=PAKET_CARI.get(sub_cari.get("paket"),{}).get("nama","-"); exp_str=exp.strftime("%d/%m/%Y %H:%M") if exp else "-"
            txt+=f"\n🔎 Paket CARI DATA: {paket}\n⏰ Exp: {exp_str}"
        else:
            txt+=f"\n🔎 Paket CARI DATA: Belum ada"
    return txt

async def get_profil_text(uid, user_obj=None):
    user_data=db["user_info"].get(str(uid),{}); kotas=user_data.get("kotas",[]); sub=db["langganan"].get(str(uid))
    nama=user_obj.full_name if user_obj else user_data.get("nama","-"); username=f"@{user_obj.username}" if user_obj and user_obj.username else user_data.get("username","-")
    if is_admin(uid): paket="👑 ADMIN UNLIMITED"
    elif sub: paket=PAKET_TAMBAH.get(sub.get("paket"),{}).get("nama","-")
    else: paket="❌ Belum ada"
    return f"👤 PROFIL USER \n\n🆔 ID: {uid}\n👨 Nama: {nama}\n📱 Username: {username}\n📦 Paket: {paket}\n📍 Wilayah: {len(kotas)} tersimpan\n\n💡 Gunakan menu di bawah untuk atur bot!"

async def start(update,context):
    uid=update.effective_user.id; nama=update.effective_user.full_name; username=update.effective_user.username or "-"
    if str(uid) not in db["user_info"]: db["user_info"][str(uid)]={"nama":nama,"username":username,"kotas":[],"custom_keywords":[]}
    else:
        db["user_info"][str(uid)]["nama"]=nama; db["user_info"][str(uid)]["username"]=username
        if "custom_keywords" not in db["user_info"][str(uid)]: db["user_info"][str(uid)]["custom_keywords"]=[]
        if "kotas" not in db["user_info"][str(uid)]: db["user_info"][str(uid)]["kotas"]=[]
    save_db()
    kotas=db["user_info"].get(str(uid),{}).get("kotas",[]); sub=db["langganan"].get(str(uid))
    wilayah_text=""
    if kotas:
        for k in kotas[:10]:
            parts=[p.strip() for p in k.split("|")]
            wilayah_text+=f"🏙️ {parts[0]} > {parts[1]} > {parts[2]}\n" if len(parts)>=3 else f" {k}\n"
    else: wilayah_text="❌ Belum ada wilayah dipilih\n"
    if is_admin(uid): lang_text="👑 Langganan Aktif: UNLIMITED (ADMIN) ♾️"
    elif sub and not is_expired(sub):
        exp=sub.get("expire")
        if isinstance(exp,str):
            try: exp=datetime.fromisoformat(exp)
            except: exp=None
        lang_text=f"✅ Langganan Aktif hingga: {exp.strftime('%d/%m/%Y')}" if exp else "Belum ada langganan"
    else: lang_text="❌ Belum ada langganan - Silahkan 💳 TOP UP"
    txt=f"👋 SELAMAT DATANG SAHABAT, TERIMAKASIH SUDAH BERGABUNG! 🙏\n\n📍 WILAYAH DIPILIH:\n{wilayah_text}\n{lang_text}\n\n👇 Silahlan Pilih menu di bawah ini"
    await update.message.reply_text(txt, reply_markup=kb_main(uid))

def build_kec_keyboard(kota_nama, kec_list, selected, prov_id, prov_nama):
    buttons=[]
    buttons.append([InlineKeyboardButton(f"✅ PILIH SEMUA KECAMATAN DI {kota_nama}", callback_data=f"kec_ALL_Semua Kecamatan")])
    row=[]
    for kec in kec_list:
        name=kec["name"]
        icon="✅" if name in selected else "🔸"
        row.append(InlineKeyboardButton(f"{icon} {name}", callback_data=f"kec_toggle_{kec['id']}_{name}"))
        if len(row)==2:
            buttons.append(row); row=[]
    if row: buttons.append(row)
    if selected:
        buttons.append([InlineKeyboardButton(f"💾 SIMPAN {len(selected)} KECAMATAN ✅", callback_data="kec_save")])
        buttons.append([InlineKeyboardButton(f"🗑️ HAPUS PILIHAN ({len(selected)})", callback_data="kec_clear")])
    buttons.append([InlineKeyboardButton("⬅️ Kembali", callback_data=f"prov_{prov_id}_{prov_nama}")])
    return InlineKeyboardMarkup(buttons)

async def cb_handler(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; data=q.data
    if data=="noop": return
    if data=="back_main":
        await q.message.delete(); await context.bot.send_message(chat_id=uid,text="🏠 MENU UTAMA 🏠",reply_markup=kb_main(uid)); return
    if data=="admin_menu":
        if not is_admin(uid): return
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("📊 CEK ID AKTIF",callback_data="admin_cek_aktif")],[InlineKeyboardButton("🗑️ HAPUS ID USER",callback_data="admin_hapus_list")],[InlineKeyboardButton("📢 BROADCAST",callback_data="admin_broadcast")],[InlineKeyboardButton("🚫 KELOLA BLACKLIST",callback_data="admin_blacklist_menu")],[InlineKeyboardButton("🔧 SET WEBHOOK WA",callback_data="admin_set_webhook")],[InlineKeyboardButton("⬅️ Kembali",callback_data="back_main")]])
        await q.message.delete(); await context.bot.send_message(chat_id=uid,text="🧭 ADMIN PANEL ",reply_markup=kb); return
    if data=="admin_set_webhook":
        await q.message.delete(); await context.bot.send_message(chat_id=uid,text=f"🔧 WEBHOOK: {RAILWAY_URL}/whatsapp-webhook",reply_markup=kb_main(uid)); return
    if data=="admin_broadcast":
        if not is_admin(uid): return
        context.user_data["awaiting_broadcast"]=True
        await q.message.delete(); await context.bot.send_message(chat_id=uid,text="📢 MODE BROADCAST\nKirim pesan broadcast\nKetik /batal untuk batal",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Batal",callback_data="admin_menu")]])); return
    if data=="admin_cek_aktif":
        aktif=[]
        for uid_str,sub in db["langganan"].items():
            exp=sub.get("expire")
            if isinstance(exp,str):
                try: exp=datetime.fromisoformat(exp)
                except: continue
            if exp and isinstance(exp,datetime) and exp>datetime.now():
                info=db["user_info"].get(uid_str,{})
                kotas=info.get('kotas',[])
                kota_list=[]
                for k in kotas[:5]:
                    parts=[p.strip() for p in k.split("|")]
                    if len(parts)>=2:
                        kota_list.append(parts[1])
                    else:
                        kota_list.append(k)
                kota_str=", ".join(kota_list) if kota_list else "Belum pilih"
                aktif.append(f"🆔 {uid_str}\n👤 {info.get('nama','-')}\n🏙️ Kota: {kota_str}\n⏰ Exp: {exp.strftime('%d/%m/%Y')}\n---")
        txt="❌ Tidak ada user aktif" if not aktif else "✅ ID AKTIF - KOTA DIPILIH\n\n" + "\n".join(aktif[:20])
        await q.message.delete(); await context.bot.send_message(chat_id=uid,text=txt,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali",callback_data="admin_menu")]])); return
    if data=="admin_hapus_list":
        buttons=[]
        for uid_str in list(db["langganan"].keys())[:30]:
            info=db["user_info"].get(uid_str,{}); buttons.append([InlineKeyboardButton(f"🗑️ {uid_str} | {info.get('nama','-')[:12]}",callback_data=f"admin_del_{uid_str}")])
        buttons.append([InlineKeyboardButton("⬅️ Kembali",callback_data="admin_menu")])
        await q.message.delete(); await context.bot.send_message(chat_id=uid,text="🗑️ HAPUS ID USER",reply_markup=InlineKeyboardMarkup(buttons)); return
    if data.startswith("admin_del_"):
        target=data.replace("admin_del_",""); kb=InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ YA HAPUS {target}",callback_data=f"admin_confirm_del_{target}"),InlineKeyboardButton("❌ BATAL",callback_data="admin_hapus_list")]])
        await q.message.delete(); await context.bot.send_message(chat_id=uid,text=f"⚠️ YAKIN HAPUS ID {target}?",reply_markup=kb); return
    if data.startswith("admin_confirm_del_"):
        target=data.replace("admin_confirm_del_","")
        if target in db["user_info"]: db["user_info"][target]["kotas"]=[]
        if target in db["langganan"]: del db["langganan"][target]
        if target in db["langganan_cari"]: del db["langganan_cari"][target]
        save_db(); await q.message.delete(); await context.bot.send_message(chat_id=uid,text=f"✅ BERHASIL HAPUS {target}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali",callback_data="admin_hapus_list")]])); return
    if data=="admin_blacklist_menu":
        bl=db.get("blacklist",[]); 
        if bl:
            txt=f"🚫 BLACKLIST ({len(bl)} nomor)\n\n"+"\n".join([f"🚫 {n}" for n in bl[:30]])
        else:
            txt="🚫 Blacklist kosong - Belum ada nomor"
        txt+="\n\n💡 Cara tambah/hapus:\n/Add 083123456789\n/Delete 083123456789\nAtau kirim nomor langsung"
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("📋 Lihat Semua",callback_data="admin_blacklist_all")],[InlineKeyboardButton("⬅️ Kembali",callback_data="admin_menu")]])
        await q.message.delete(); await context.bot.send_message(chat_id=uid,text=txt,reply_markup=kb); context.user_data["awaiting_admin_blacklist"]=True; return
    if data=="admin_blacklist_all":
        bl=db.get("blacklist",[]); txt="🚫 BLACKLIST:\n"+"\n".join([f"🚫 {n}" for n in bl]) if bl else "🚫 Kosong"
        if len(txt)>4000: txt=txt[:4000]
        await q.message.delete(); await context.bot.send_message(chat_id=uid,text=txt,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali",callback_data="admin_blacklist_menu")]])); return
    if data.startswith("hapuskota_"):
        idx=int(data.replace("hapuskota_","")); kotas=db["user_info"].get(str(uid),{}).get("kotas",[])
        if idx<0 or idx>=len(kotas): await q.answer("❌ Tidak ditemukan",show_alert=True); return
        hapus=kotas[idx]
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ YA HAPUS",callback_data=f"confirmhapus_{idx}"),InlineKeyboardButton("❌ BATAL",callback_data="back_main")]])
        await q.message.edit_text(f"⚠️ Yakin hapus?\n {hapus}",reply_markup=kb); return
    if data.startswith("confirmhapus_"):
        idx=int(data.replace("confirmhapus_","")); kotas=db["user_info"].get(str(uid),{}).get("kotas",[])
        if 0<=idx<len(kotas):
            hapus=kotas.pop(idx); save_db()
            await q.message.edit_text(f"✅ Berhasil hapus:\n {hapus}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu",callback_data="back_main")]]))
        return
    if data=="tambah_kota":
        if not is_active_tambah(uid) and not is_admin(uid):
            await q.message.delete()
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("💳 TOP UP SEKARANG",callback_data="topup_tambah")],[InlineKeyboardButton("⬅️ Kembali",callback_data="back_main")]])
            await context.bot.send_message(chat_id=uid,text="🔒 FITUR TAMBAH KOTA TERKUNCI \n\nSilahkan TOP UP dulu bos untuk buka fitur ini!",reply_markup=kb); return
        await q.message.edit_text(" PILIH PROVINSI \nPilih provinsi untuk tambah kota:",reply_markup=kb_provinsi()); return
    if data.startswith("prov_"):
        _, prov_id, prov_nama = data.split("_",2)
        context.user_data["prov_id"]=prov_id; context.user_data["prov_nama"]=prov_nama
        kota_list=get_kota(prov_id)
        if not kota_list:
            await q.message.edit_text("❌ Gagal ambil data kota, coba lagi.")
            return
        buttons=[]; row=[]
        for k in kota_list:
            row.append(InlineKeyboardButton(f" {k['name']}", callback_data=f"kota_{k['id']}_{k['name']}"))
            if len(row)==2: buttons.append(row); row=[]
        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("⬅️ Kembali", callback_data="tambah_kota")])
        await q.message.edit_text(f" Provinsi *{prov_nama}*\nPilih Kota/Kabupaten:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
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
        await q.message.edit_text(f" {context.user_data['prov_nama']} > *{kota_nama}*\n\n✅ Pilih lebih dari 1 kecamatan bos!\nCentang beberapa, lalu klik SIMPAN:\n\n = Belum dipilih\n✅ = Sudah dipilih\n\nDipilih: 0 kecamatan", parse_mode="Markdown", reply_markup=kb)
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
            if str(uid) in db["langganan"] and not is_admin(uid): db["langganan"][str(uid)]["used"]=True
            save_db()
            await q.message.edit_text(f"✅ Berhasil {len(added)} kecamatan", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_main")]]))
        else: await q.message.edit_text("⚠️ Sudah ada semua!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_main")]]))
        context.user_data["selected_kec"] = []
        return
    if data.startswith("kec_ALL"):
        prov = context.user_data.get("prov_nama"); kota = context.user_data.get("kota_nama")
        entry = f"{prov} | {kota} | Semua Kecamatan"
        if str(uid) not in db["user_info"]: db["user_info"][str(uid)] = {}
        if "kotas" not in db["user_info"][str(uid)]: db["user_info"][str(uid)]["kotas"] = []
        if entry not in db["user_info"][str(uid)]["kotas"]:
            db["user_info"][str(uid)]["kotas"].append(entry)
            if str(uid) in db["langganan"] and not is_admin(uid): db["langganan"][str(uid)]["used"]=True
            save_db(); await q.message.edit_text(f"✅ Berhasil:\n {entry}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_main")]]))
        else: await q.message.edit_text(f"⚠️ Sudah ada:\n {entry}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_main")]]))
        return
    if data.startswith("kec_"):
        _, kec_id, kec_nama = data.split("_", 2)
        prov = context.user_data.get("prov_nama"); kota = context.user_data.get("kota_nama")
        entry = f"{prov} | {kota} | {kec_nama}"
        if str(uid) not in db["user_info"]: db["user_info"][str(uid)] = {}
        if "kotas" not in db["user_info"][str(uid)]: db["user_info"][str(uid)]["kotas"] = []
        if entry not in db["user_info"][str(uid)]["kotas"]:
            db["user_info"][str(uid)]["kotas"].append(entry)
            if str(uid) in db["langganan"] and not is_admin(uid): db["langganan"][str(uid)]["used"]=True
            save_db(); await q.message.edit_text(f"✅ Berhasil:\n {entry}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_main")]]))
        else: await q.message.edit_text(f"⚠️ Sudah ada:\n {entry}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_main")]]))
        return
    if data.startswith("topup_"):
        paket_type = data.replace("topup_","")
        context.user_data["paket_type"]=paket_type
        if paket_type=="tambah":
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("1 MINGGU - 50K",callback_data="paket_tambah_1minggu")],[InlineKeyboardButton("1 BULAN - 150K",callback_data="paket_tambah_1bulan")],[InlineKeyboardButton("2 BULAN - 250K",callback_data="paket_tambah_2bulan")],[InlineKeyboardButton("⬅️ Kembali",callback_data="back_main")]])
            await q.message.delete(); await context.bot.send_message(chat_id=uid,text=f"{REKENING_TEXT}\n\n💳 PILIH PAKET TAMBAH KOTA",reply_markup=kb)
        else:
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("1 MINGGU - 15K",callback_data="paket_cari_1minggu")],[InlineKeyboardButton("1 BULAN - 50K",callback_data="paket_cari_1bulan")],[InlineKeyboardButton("2 BULAN - 100K",callback_data="paket_cari_2bulan")],[InlineKeyboardButton("⬅️ Kembali",callback_data="back_main")]])
            await q.message.delete(); await context.bot.send_message(chat_id=uid,text=f"{REKENING_TEXT}\n\n🔍 PILIH PAKET CARI DATA",reply_markup=kb)
        return
    if data.startswith("paket_"):
        _, ptype, pkey = data.split("_",2)
        context.user_data["paket_pilih"]=pkey; context.user_data["paket_type"]=ptype
        if ptype=="tambah": p=PAKET_TAMBAH.get(pkey,PAKET_TAMBAH["1minggu"])
        else: p=PAKET_CARI.get(pkey,PAKET_CARI["1minggu"])
        text = f"{REKENING_TEXT}\n\n📦 PAKET DIPILIH: {p['nama']} - Rp {p['harga']:,}\n\nSetelah transfer paket {p['nama']}, kirim foto buktinya disini ya bos! 📸"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Kembali", callback_data=f"topup_{ptype}")]])
        await q.message.delete()
        await context.bot.send_message(chat_id=uid, text=text, reply_markup=kb)
        return
    if data.startswith("acc_") or data.startswith("dec_"):
        if not is_admin(uid): return
        parts=data.split("_"); action=parts[0]; ptype=parts[1]; target_uid=parts[2]; pkey=parts[3] if len(parts)>3 else "1minggu"
        target_uid=int(target_uid)
        if action=="acc":
            if ptype=="tambah":
                p=PAKET_TAMBAH.get(pkey,PAKET_TAMBAH["1minggu"])
                expire=datetime.now()+timedelta(days=p["hari"])
                db["langganan"][str(target_uid)]={"paket":pkey,"expire":expire}
                save_db()
                await q.message.edit_caption(caption=q.message.caption+f"\n\n✅ DISETUJUI - Aktif sampai {expire.strftime('%d/%m/%Y')}",reply_markup=None)
                await context.bot.send_message(chat_id=target_uid,text=f"✅ TOP UP DISETUJUI ✅\n\n📦 Paket {p['nama']} aktif sampai {expire.strftime('%d/%m/%Y')}\n🎉 Sekarang kamu bisa pakai fitur ➕ TAMBAH KOTA!",reply_markup=kb_main(target_uid))
            else:
                p=PAKET_CARI.get(pkey,PAKET_CARI["1minggu"])
                expire=datetime.now()+timedelta(days=p["hari"])
                db["langganan_cari"][str(target_uid)]={"paket":pkey,"expire":expire}
                save_db()
                await q.message.edit_caption(caption=q.message.caption+f"\n\n✅ DISETUJUI - Aktif sampai {expire.strftime('%d/%m/%Y')}",reply_markup=None)
                await context.bot.send_message(chat_id=target_uid,text=f"✅ TOP UP CARI DATA DISETUJUI ✅\n\n📦 Paket {p['nama']} aktif sampai {expire.strftime('%d/%m/%Y')}\n🎉 Sekarang bisa pakai 🔍 CARI DATA LAINNYA!",reply_markup=kb_main(target_uid))
        else:
            await q.message.edit_caption(caption=q.message.caption+"\n\n❌ DITOLAK",reply_markup=None)
            await context.bot.send_message(chat_id=target_uid,text="❌ Top Up DITOLAK admin. Hubungi @Hambali1995")
        return

async def text_handler(update,context):
    uid=update.effective_user.id
    text=update.message.text.strip()
    # ADMIN BLACKLIST COMMANDS
    if text.upper().startswith("/ADD "):
        if not is_admin(uid):
            await update.message.reply_text("❌ Hanya admin!", reply_markup=kb_main(uid)); return
        num=text[5:].strip()
        clean=''.join(filter(str.isdigit,num))
        if clean.startswith("62"): clean="0"+clean[2:]
        if len(clean)>=8:
            if "blacklist" not in db: db["blacklist"]=[]
            if clean not in db["blacklist"]:
                db["blacklist"].append(clean)
                save_db()
                await update.message.reply_text(f"✅ Nomor {clean} berhasil ditambahkan ke BLACKLIST 🚫\n📊 Total: {len(db['blacklist'])} nomor", reply_markup=kb_main(uid))
            else:
                await update.message.reply_text(f"⚠️ Nomor {clean} sudah ada di BLACKLIST", reply_markup=kb_main(uid))
        else:
            await update.message.reply_text("❌ Format salah. Contoh: /Add 083123456789", reply_markup=kb_main(uid))
        return
    if text.upper().startswith("/DELETE "):
        if not is_admin(uid):
            await update.message.reply_text("❌ Hanya admin!", reply_markup=kb_main(uid)); return
        num=text[8:].strip()
        clean=''.join(filter(str.isdigit,num))
        if clean.startswith("62"): clean="0"+clean[2:]
        if clean in db.get("blacklist",[]):
            db["blacklist"].remove(clean)
            save_db()
            await update.message.reply_text(f"✅ Nomor {clean} dihapus dari BLACKLIST\n📊 Total: {len(db['blacklist'])} nomor", reply_markup=kb_main(uid))
        else:
            await update.message.reply_text(f"❌ Nomor {clean} tidak ada di BLACKLIST", reply_markup=kb_main(uid))
        return
    if context.user_data.get("awaiting_admin_blacklist"):
        clean=''.join(filter(str.isdigit,text))
        if len(clean)>=8:
            if clean.startswith("62"): clean="0"+clean[2:]
            if clean not in db.get("blacklist",[]):
                db["blacklist"].append(clean)
                save_db()
                bl_count=len(db.get("blacklist",[]))
                await update.message.reply_text(f"✅ Nomor {clean} ditambahkan ke BLACKLIST 🚫\n📊 Total: {bl_count}", reply_markup=kb_main(uid))
            else:
                await update.message.reply_text(f"⚠️ Nomor {clean} sudah ada di BLACKLIST", reply_markup=kb_main(uid))
            return
    if context.user_data.get("awaiting_broadcast"):
        if text.lower()=="/batal":
            context.user_data["awaiting_broadcast"]=False
            await update.message.reply_text("❌ Broadcast dibatalkan",reply_markup=kb_main(uid)); return
        count=0
        for uid_str in db.get("user_info",{}).keys():
            try:
                await context.bot.send_message(chat_id=int(uid_str),text=f"📢 BROADCAST\n\n{text}")
                count+=1
            except: pass
        context.user_data["awaiting_broadcast"]=False
        await update.message.reply_text(f"✅ Broadcast terkirim ke {count} user",reply_markup=kb_main(uid)); return
    # CARI DATA LAINNYA
    if context.user_data.get("awaiting_cari_lainnya"):
        if not is_active_cari(uid) and not is_admin(uid):
            await update.message.reply_text("🔒 FITUR TERKUNCI 🔒\nSilahkan TOP UP CARI DATA LAINNYA dulu bos!", reply_markup=kb_main(uid))
            context.user_data["awaiting_cari_lainnya"]=False
            return
        query=text.strip().upper()
        if query=="/BATAL":
            context.user_data["awaiting_cari_lainnya"]=False
            await update.message.reply_text("❌ Pencarian dibatalkan", reply_markup=kb_main(uid)); return
        history=load_wa_history()
        hasil=[]
        for h in reversed(history[-5000:]):
            if query in h.get("text","").upper() or query in h.get("group","").upper():
                hasil.append(h)
                if len(hasil)>=20: break
        if not hasil:
            await update.message.reply_text(f"❌ Data '{text}' tidak ditemukan di history WA\nCoba keyword lain bos!", reply_markup=kb_main(uid))
        else:
            txt=f"🔎 HASIL PENCARIAN: {text}\n📊 Ditemukan {len(hasil)} data\n\n"
            for i,h in enumerate(hasil[:10],1):
                txt+=f"{i}. 📍 {h.get('group','-')}\n👤 {h.get('sender','-')} - {h.get('number','-')}\n💬 {h.get('text','')[:150]}...\n⏰ {h.get('time','')[:19]}\n---\n"
            await update.message.reply_text(txt, reply_markup=kb_main(uid))
            for h in hasil[:5]:
                nomor=h.get("number","")
                clean=''.join(filter(str.isdigit,nomor))
                if clean.startswith("0"): clean="62"+clean[1:]
                msg=f"🏙️ KOTA: {text.upper()}\n👥 Grup: {h.get('group')}\n👤 Pengirim: {h.get('sender')}\n📱 Nomor: {h.get('number')}\n\n💬 Pesan:\n{h.get('text')}"
                try:
                    if clean:
                        kb=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Chat di WA", url=f"https://wa.me/{clean}")]])
                        await context.bot.send_message(chat_id=uid, text=msg, reply_markup=kb)
                    else:
                        await context.bot.send_message(chat_id=uid, text=msg)
                except: pass
        context.user_data["awaiting_cari_lainnya"]=False
        return
    if context.user_data.get("awaiting_keyword_gratis"):
        if text.lower()=="/batal":
            context.user_data["awaiting_keyword_gratis"]=False
            await update.message.reply_text("❌ Dibatalkan",reply_markup=kb_main(uid)); return
        # cek aktif dulu (double security)
        if not is_user_id_aktif(uid):
            context.user_data["awaiting_keyword_gratis"]=False
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("💳 TOP UP SEKARANG",callback_data="topup_tambah")],[InlineKeyboardButton("🏠 Kembali",callback_data="back_main")]])
            await update.message.reply_text("🔒 FITUR PILIH KEYWORD TERKUNCI 🔒\n\n❌ ID kamu BELUM AKTIF!\nSilahkan TOP UP dulu untuk jadi USER ID AKTIF, baru bisa pakai 🚀 PILIH KEYWORD!",reply_markup=kb); return
        # validasi geo
        forbidden, reason = is_geo_forbidden(text)
        if forbidden:
            await update.message.reply_text(f"🚫 KEYWORD DITOLAK!\n\n{reason}\n\n❌ Tidak boleh pakai nama PROVINSI / KOTA-KABUPATEN / KECAMATAN!\n✅ Harus pakai keyword lain contoh: 'Link Dana kaget', 'Garansi', 'Info loker', dll\n\n✍️ Coba ketik keyword lain atau /batal",reply_markup=kb_main(uid))
            return  # tetap awaiting, suruh input ulang
        if str(uid) not in db["user_info"]: db["user_info"][str(uid)]={"kotas":[],"custom_keywords":[]}
        if "custom_keywords" not in db["user_info"][str(uid)]: db["user_info"][str(uid)]["custom_keywords"]=[]
        kw=text.strip()
        if len(kw) < 3:
            await update.message.reply_text("❌ Keyword minimal 3 huruf! Coba lagi.",reply_markup=kb_main(uid)); return
        if kw and kw not in db["user_info"][str(uid)]["custom_keywords"]:
            db["user_info"][str(uid)]["custom_keywords"].append(kw)
            save_db()
            await update.message.reply_text(f"✅ Keyword '{kw}' ditambahkan!\n📌 Keyword kamu: {', '.join(db['user_info'][str(uid)]['custom_keywords'])}",reply_markup=kb_main(uid))
        else:
            await update.message.reply_text(f"⚠️ Keyword '{kw}' sudah ada",reply_markup=kb_main(uid))
        context.user_data["awaiting_keyword_gratis"]=False
        return
    if "PROFIL" in text:
        txt=await get_profil_text(uid,update.effective_user)
        await update.message.reply_text(txt,reply_markup=kb_main(uid))
    elif "TAMBAH KOTA" in text:
        if not is_active_tambah(uid) and not is_admin(uid):
            sub=db["langganan"].get(str(uid))
            msg="🔒 JATAH HABIS - TOP UP LAGI!" if sub and sub.get("used") else "🔒 FITUR TERKUNCI - TOP UP DULU!"
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("💳 TOP UP SEKARANG",callback_data="topup_tambah")],[InlineKeyboardButton("🏠 Kembali",callback_data="back_main")]])
            await update.message.reply_text(msg,reply_markup=kb); return
        await update.message.reply_text("📍 PILIH PROVINSI ",reply_markup=kb_provinsi())
    elif "WILAYAH DIPILIH" in text:
        kotas=db["user_info"].get(str(uid),{}).get("kotas",[])
        if not kotas:
            await update.message.reply_text("❌ Belum ada wilayah dipilih\nSilahkan 🌍 TAMBAH KOTA dulu!",reply_markup=kb_main(uid))
        else:
            txt=f" WILAYAH DIPILIH \n📊 Total {len(kotas)} wilayah\n\n"
            buttons=[]
            for i,k in enumerate(kotas[:20]):
                txt+=f"{i+1}. 🏙️ {k}\n"
                buttons.append([InlineKeyboardButton(f"🗑️ Hapus {k[:30]}",callback_data=f"hapuskota_{i}")])
            buttons.append([InlineKeyboardButton("🏠 Kembali ke Menu",callback_data="back_main")])
            await update.message.reply_text(txt,reply_markup=InlineKeyboardMarkup(buttons))
    elif "HAPUS KOTA" in text:
        kotas=db["user_info"].get(str(uid),{}).get("kotas",[])
        if not kotas:
            await update.message.reply_text("❌ Belum ada wilayah",reply_markup=kb_main(uid))
        else:
            buttons=[]
            for i,k in enumerate(kotas[:20]):
                buttons.append([InlineKeyboardButton(f"🗑️ {k[:40]}",callback_data=f"hapuskota_{i}")])
            buttons.append([InlineKeyboardButton("🏠 Kembali",callback_data="back_main")])
            await update.message.reply_text(f"🗑️ HAPUS KOTA SAYA\nPilih yang mau dihapus:",reply_markup=InlineKeyboardMarkup(buttons))
    elif "STATUS" in text:
        txt=await get_status_text(uid)
        await update.message.reply_text(txt,reply_markup=kb_main(uid))
    elif "TOP UP" in text:
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("🌍 TAMBAH KOTA - 50K/150K/250K",callback_data="topup_tambah")],[InlineKeyboardButton("🔍 CARI DATA - 15K/50K/100K",callback_data="topup_cari")],[InlineKeyboardButton("🏠 Kembali",callback_data="back_main")]])
        await update.message.reply_text(f"{REKENING_TEXT}\n\n💳 PILIH JENIS TOP UP:",reply_markup=kb)
    elif "CARI DATA LAINNYA" in text:
        if not is_active_cari(uid) and not is_admin(uid):
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("💳 TOP UP CARI DATA",callback_data="topup_cari")]])
            await update.message.reply_text("🔒 FITUR CARI DATA TERKUNCI 🔒\nSilahkan TOP UP CARI DATA LAINNYA dulu!\n\nPaket:\n1 Minggu 15K\n1 Bulan 50K\n2 Bulan 100K",reply_markup=kb); return
        context.user_data["awaiting_cari_lainnya"]=True
        await update.message.reply_text("🔎 CARI DATA LAINNYA \n\n📍 MASUKAN NAMA KOTA\n💡 Contoh: BANDUNG\n\n✍️ Ketik kota yang mau dicari\nBot akan cari di history WA yang dishare pengirim!\n\n❌ Ketik /batal untuk batal.",reply_markup=kb_main(uid))
    elif "KEYWORD" in text:
        # WAJIB USER ID AKTIF dulu
        if not is_user_id_aktif(uid):
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("💳 TOP UP SEKARANG",callback_data="topup_tambah")],[InlineKeyboardButton("🔍 TOP UP CARI DATA",callback_data="topup_cari")],[InlineKeyboardButton("🏠 Kembali",callback_data="back_main")]])
            await update.message.reply_text("🔒 AKSES DITOLAK - ID BELUM AKTIF 🔒\n\n🚀 Menu PILIH KEYWORD hanya untuk USER ID AKTIF!\n\n❌ ID kamu belum aktif.\n💡 Silahkan TOP UP paket TAMBAH KOTA atau CARI DATA dulu untuk aktivasi!\n\nSetelah aktif, kamu baru bisa pakai keyword custom.",reply_markup=kb); return
        context.user_data["awaiting_keyword_gratis"]=True
        await update.message.reply_text("🔎 PILIH KEYWORD LAINNYA \n\n✍️ Silahkan ketikan keyword yang Anda pilih\n💡 Contoh: Link Dana kaget, Promo, Loker, dll\n\n⚠️ PERATURAN:\n❌ DILARANG ketik nama PROVINSI\n❌ DILARANG ketik nama KOTA/KABUPATEN\n❌ DILARANG ketik nama KECAMATAN\n✅ HARUS keyword lainnya\n\n🔔 Nanti otomatis dapat notifikasi jika ada pengirim sebar keyword itu!\n\n📌 Keyword kamu saat ini: "+ ", ".join(db["user_info"].get(str(uid),{}).get("custom_keywords",[])) + "\n\n❌ Ketik /batal untuk batal.",reply_markup=kb_main(uid))
    elif "BLACKLIST" in text:
        bl=db.get("blacklist",[])
        if not bl:
            txt="🚫 DAFTAR BLACKLIST \n\n❌ Belum ada nomor blacklist\n\n✅ Semua nomor aman!"
        else:
            txt=f"🚫 DAFTAR BLACKLIST \n📊 Total {len(bl)} nomor\n\n"
            for i,n in enumerate(bl[:100],1):
                txt+=f"{i}. 🚫 {n}\n"
            if len(bl)>100:
                txt+=f"\n... dan {len(bl)-100} nomor lainnya\nKetik /cek untuk cek spesifik"
        txt+="\n\n🔎 Untuk cek nomor spesifik ketik:\n/cek 083123456789\n\n⬅️ Ketik /batal untuk kembali"
        await update.message.reply_text(txt,reply_markup=kb_main(uid))
        context.user_data["awaiting_blacklist_check"]=True
    elif "BANTUAN" in text:
        await update.message.reply_text("❓ BANTUAN - PANDUAN BOT \n\n1️⃣ 💳 Top up 🌍 TAMBAH KOTA untuk filter wilayah\n2️⃣ 🔎 Top up CARI DATA LAINNYA untuk search history WA\n3️⃣ 🔎 PILIH KEYWORD gratis untuk keyword custom\n4️⃣ 🚫 DAFTAR BLACKLIST untuk cek nomor penipu\n\n👨‍💼 Hubungi Admin: @Hambali1995\n💡 Bot akan notif otomatis jika ada data sesuai wilayah!",reply_markup=kb_main(uid))
    elif "HUBUNGI ADMIN" in text:
        await update.message.reply_text("🧑‍💻 HUBUNGI ADMIN \n\n📱 Telegram: @Hambali1995\n⏰ Fast respon 1x24 jam",reply_markup=kb_main(uid))
    elif "PANEL ADMIN" in text:
        if not is_admin(uid): return
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("📊 CEK ID AKTIF",callback_data="admin_cek_aktif")],[InlineKeyboardButton("🗑️ HAPUS ID USER",callback_data="admin_hapus_list")],[InlineKeyboardButton("📢 BROADCAST",callback_data="admin_broadcast")],[InlineKeyboardButton("🚫 KELOLA BLACKLIST",callback_data="admin_blacklist_menu")],[InlineKeyboardButton("🔧 SET WEBHOOK WA",callback_data="admin_set_webhook")]])
        await update.message.reply_text("🧭 PANEL ADMIN \nPilih menu admin:",reply_markup=kb)

async def contact_handler(update,context):
    uid=update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Hanya admin bisa tambah blacklist lewat kontak!", reply_markup=kb_main(uid)); return
    contact=update.message.contact
    if contact and contact.phone_number:
        ok,clean=add_blacklist(contact.phone_number)
        if ok:
            await update.message.reply_text(f"✅ Nomor {clean} ({contact.first_name}) ditambahkan ke BLACKLIST 🚫\n📊 Total: {len(db.get('blacklist',[]))}", reply_markup=kb_main(uid))
        else:
            await update.message.reply_text(f"⚠️ Nomor {clean} sudah ada di BLACKLIST (anti duplikat)\n📊 Total: {len(db.get('blacklist',[]))}", reply_markup=kb_main(uid))
    else:
        await update.message.reply_text("❌ Gagal baca kontak", reply_markup=kb_main(uid))

async def foto_handler(update,context):
    uid=update.effective_user.id
    if not update.message.photo: return
    paket_key=context.user_data.get("paket_pilih","1minggu"); paket_type=context.user_data.get("paket_type","tambah")
    if paket_type=="tambah": p=PAKET_TAMBAH.get(paket_key,PAKET_TAMBAH["1minggu"])
    else: p=PAKET_CARI.get(paket_key,PAKET_CARI["1minggu"])
    file_id=update.message.photo[-1].file_id
    caption_user=f"💳 BUKTI TOP UP {paket_type.upper()} MASUK\n🆔 ID: {uid}\n📦 Paket: {p['nama']} - Rp {p['harga']:,}"
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ SETUJU",callback_data=f"acc_{paket_type}_{uid}_{paket_key}"),InlineKeyboardButton("❌ TOLAK",callback_data=f"dec_{paket_type}_{uid}_{paket_key}")]])
    for admin_id in ADMIN_IDS:
        try: await context.bot.send_photo(chat_id=admin_id,photo=file_id,caption=caption_user,reply_markup=kb)
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
        if clean in db.get("blacklist",[]):
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
        txt=f"💾 BACKUP DB\n👤 User: {len(data.get('user_info',{}))}\n📦 Tambah: {len(data.get('langganan',{}))}\n🔎 Cari: {len(data.get('langganan_cari',{}))}\n🚫 Blacklist: {len(data.get('blacklist',[]))}"
        await update.message.reply_text(txt,reply_markup=kb_main(uid))
        await context.bot.send_document(chat_id=uid, document=open(DB_FILE,"rb"), filename="bot_database.json")
        if os.path.exists(DB_FILE_PERSISTENT):
            await context.bot.send_document(chat_id=uid, document=open(DB_FILE_PERSISTENT,"rb"), filename="bot_database_persistent.json")
    except Exception as e:
        await update.message.reply_text(f"❌ Backup fail: {e}",reply_markup=kb_main(uid))

# ========== FIX RAILWAY WEBHOOK MODE ==========
flask_app = Flask(__name__)
application = Application.builder().token(TOKEN).build()

application.add_handler(CommandHandler("start",start))
application.add_handler(CommandHandler("profil",cmd_profil))
application.add_handler(CommandHandler("status",cmd_status))
application.add_handler(CommandHandler("cek", cmd_cek))
application.add_handler(CommandHandler("backup", cmd_backup))
application.add_handler(CallbackQueryHandler(cb_handler))
application.add_handler(MessageHandler(filters.CONTACT,contact_handler))
application.add_handler(MessageHandler(filters.PHOTO,foto_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_handler))

import asyncio
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

async def setup_webhook():
    await application.initialize()
    await application.start()
    url = RAILWAY_URL if RAILWAY_URL.startswith("http") else f"https://{RAILWAY_URL}"
    webhook_url = f"{url}/{TOKEN}"
    print(f"Setting webhook to {webhook_url}")
    await application.bot.set_webhook(url=webhook_url, drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

loop.run_until_complete(setup_webhook())

@flask_app.route("/")
def index():
    return "Bot Active - Webhook Mode"

@flask_app.route(f"/{TOKEN}", methods=["POST"])
def telegram_webhook():
    try:
        data = request.get_json(force=True)
        if not data:
            return "ok"
        update = Update.de_json(data, application.bot)
        # FIX: pakai run_until_complete biar pasti keproses di Railway
        try:
            loop.run_until_complete(application.process_update(update))
        except:
            # fallback kalau loop udah running
            import asyncio
            asyncio.run(application.process_update(update))
        return "ok"
    except Exception as e:
        print(f"Webhook error: {e}")
        return "ok"

# Biarkan route Green API kamu yang lama tetap ada
# @flask_app.route("/waWebhook" ...) jangan dihapus

if __name__ == "__main__":
    print(f"🚀 WEBHOOK MODE - Port {PORT}")
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
