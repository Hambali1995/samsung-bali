# -*- coding: utf-8 -*-
import os, json, requests, threading, re, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
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



# ========== TABEL PERMANEN USER PACKAGES ANTI HILANG ==========
USER_PACKAGES_TABLE = "user_packages"
USER_MENUS_TABLE = "user_menus"
def save_user_package_permanent(user_id, paket_key, tipe="tambah"):
    if not supabase_enabled():
        return False
    try:
        info = PAKET_TAMBAH.get(paket_key) if tipe=="tambah" else PAKET_CARI.get(paket_key)
        if not info: info={}
        body={"user_id":str(user_id),"paket_key":paket_key,"paket_nama":info.get("nama",paket_key),"tipe":tipe,"harga":info.get("harga"),"hari":info.get("hari"),"kuota":info.get("kuota"),"status":"aktif"}
        if info.get("hari"):
            from datetime import timedelta
            body["expire"]=(datetime.now()+timedelta(days=info["hari"])).isoformat()
        url=f"{SUPABASE_URL}/rest/v1/{USER_PACKAGES_TABLE}"
        r=requests.post(f"{url}?on_conflict=user_id,paket_key,tipe", headers={**supabase_headers(),"Prefer":"resolution=merge-duplicates,return=representation"}, json=body, timeout=15)
        return r.status_code in [200,201]
    except Exception as e:
        logger.error(f"save pkg permanen error: {e}")
        return False

def load_all_packages_permanent():
    if not supabase_enabled():
        return {"tambah":{},"cari":{}}
    try:
        url=f"{SUPABASE_URL}/rest/v1/{USER_PACKAGES_TABLE}"
        r=requests.get(url, headers=supabase_headers(), params={"select":"*","status":"eq.aktif","limit":"10000"}, timeout=15)
        r.raise_for_status()
        rows=r.json()
        res_t, res_c = {}, {}
        for row in rows:
            uid=str(row["user_id"])
            target=res_t if row.get("tipe")=="tambah" else res_c
            target.setdefault(uid, []).append({"paket":row.get("paket_key"),"nama":row.get("paket_nama"),"expire":row.get("expire")})
        return {"tambah":res_t,"cari":res_c}
    except Exception as e:
        logger.warning(f"load pkg permanen error: {e}")
        return {"tambah":{},"cari":{}}

def load_db_with_permanent_fallback():
    remote=load_db_from_supabase()
    perm=load_all_packages_permanent()
    if perm and (perm.get("tambah") or perm.get("cari")):
        if not remote or (len(remote.get("langganan",{}))==0 and len(perm.get("tambah",{}))>0):
            if not remote:
                remote={"user_info":{},"langganan":{},"langganan_cari":{},"blacklist":[],"pantauan":[],"pending_hapus_kota":[]}
            for uid,pkgs in perm.get("tambah",{}).items():
                remote.setdefault("langganan",{})[uid]=pkgs
            for uid,pkgs in perm.get("cari",{}).items():
                remote.setdefault("langganan_cari",{})[uid]=pkgs
    return remote


# ========== PANTAUAN PERMANEN V5 - FILE ADALAH RAJA, ANTI 0 ==========
PANTAUAN_TABLE = "pantauan"
BLACKLIST_TABLE = PANTAUAN_TABLE
PANTAUAN_FILE = "pantauan_backup.json"
PANTAUAN_FILE_PERSISTENT = "/data/pantauan_backup.json"

def save_pantauan_backup(numbers):
    try:
        clean=[]
        for n in numbers:
            s=str(n).strip()
            if s and s not in clean:
                clean.append(s)
        import json, os
        with open(PANTAUAN_FILE, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2, ensure_ascii=False)
        os.makedirs(os.path.dirname(PANTAUAN_FILE_PERSISTENT), exist_ok=True)
        with open(PANTAUAN_FILE_PERSISTENT, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2, ensure_ascii=False)
        logger.info(f"✅ Pantauan backup saved {len(clean)} -> {PANTAUAN_FILE_PERSISTENT}")
        return True
    except Exception as e:
        logger.error(f"save backup err: {e}")
        return False

def load_pantauan_backup():
    import os, json
    for p in [PANTAUAN_FILE_PERSISTENT, PANTAUAN_FILE]:
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data=json.load(f)
                    if isinstance(data, list):
                        return [str(x).strip() for x in data if str(x).strip()]
        except:
            continue
    return []

def load_pantauan_from_supabase():
    if not supabase_enabled():
        return None
    try:
        url=f"{SUPABASE_URL}/rest/v1/{PANTAUAN_TABLE}"
        r=requests.get(url, headers=supabase_headers(), params={"select":"number","order":"id.asc","limit":"10000"}, timeout=15)
        if r.status_code!=200:
            logger.warning(f"pantauan load fail {r.status_code}: {r.text[:200]}")
            return None
        rows=r.json()
        return [str(row.get("number")).strip() for row in rows if row.get("number")]
    except Exception as e:
        logger.warning(f"pantauan load warn: {e}")
        return None

def add_pantauan_to_supabase(number):
    clean=str(number).strip()
    # FILE FIRST
    try:
        cur=load_pantauan_backup()
        if clean not in cur:
            cur.append(clean)
            save_pantauan_backup(cur)
    except Exception as e:
        logger.error(f"file save fail {clean}: {e}")
    # MEMORY
    try:
        db.setdefault("pantauan", [])
        if clean not in db["pantauan"]:
            db["pantauan"].append(clean)
    except:
        pass
    # SUPABASE TRY (secondary)
    if supabase_enabled():
        try:
            url=f"{SUPABASE_URL}/rest/v1/{PANTAUAN_TABLE}"
            body={"number":clean}
            r=requests.post(f"{url}?on_conflict=number", headers={**supabase_headers(),"Prefer":"resolution=merge-duplicates"}, json=body, timeout=15)
            logger.info(f"Supabase add pantauan {clean}: {r.status_code}")
        except Exception as e:
            logger.warning(f"Supabase add fail {clean}: {e} (file tetap aman)")
    return True

def remove_pantauan_from_supabase(number):
    clean=str(number).strip()
    try:
        cur=load_pantauan_backup()
        if clean in cur:
            cur.remove(clean)
            save_pantauan_backup(cur)
    except:
        pass
    try:
        if clean in db.get("pantauan",[]):
            db["pantauan"].remove(clean)
    except:
        pass
    if supabase_enabled():
        try:
            url=f"{SUPABASE_URL}/rest/v1/{PANTAUAN_TABLE}"
            requests.delete(url, headers=supabase_headers(), params={"number":f"eq.{clean}"}, timeout=10)
        except:
            pass
    return True

def get_all_pantauan():
    # FILE FIRST = ANTI 0
    combined=[]
    try:
        combined.extend(load_pantauan_backup())
    except:
        pass
    try:
        combined.extend(db.get("pantauan",[]) or [])
        combined.extend(db.get("blacklist",[]) or [])
    except:
        pass
    try:
        sup=load_pantauan_from_supabase()
        if sup:
            combined.extend(sup)
    except:
        pass
    # dedup
    out=[]
    for n in combined:
        s=str(n).strip()
        if s and s not in out:
            out.append(s)
    return out

def load_blacklist_from_supabase():
    return load_pantauan_from_supabase()
def get_all_blacklist_for_cek():
    return get_all_pantauan()


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
    # Load remote first dengan fallback permanen
    remote = load_db_with_permanent_fallback()
    # Load local persistent as backup for pantauan
    local_persistent = None
    for p in [DB_FILE_PERSISTENT, DB_FILE]:
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    local_persistent = json.load(f)
                    break
        except:
            continue

    if remote is not None:
        data = remote
        if "langganan" not in data: data["langganan"] = {}
        if "langganan_cari" not in data: data["langganan_cari"] = {}
        if "blacklist" not in data: data["blacklist"] = []
        if "pantauan" not in data: data["pantauan"] = []
        if "pending_hapus_kota" not in data: data["pending_hapus_kota"] = []
        if "user_info" not in data: data["user_info"] = {}
        # MERGE pantauan dari file lokal jika remote kosong / quota error
        if local_persistent:
            for key in ["pantauan", "blacklist"]:
                if key in local_persistent:
                    merged = list(data.get(key, [])) + list(local_persistent.get(key, []))
                    data[key] = list(dict.fromkeys(merged))
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
    if len(db.get("user_info",{}))==0 and len(db.get("langganan",{}))==0 and len(db.get("langganan_cari",{}))==0:
        try:
            old=load_db_from_supabase()
            if old and (len(old.get("user_info",{}))>0 or len(old.get("langganan",{}))>0):
                logger.warning("⛔ SAVE DITAHAN! DB kosong tapi Supabase ada isinya")
                return False
        except:
            pass
    tmp = json.loads(json.dumps(db, default=str))
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
    try:
        if supabase_enabled():
            for uid,pkgs in db.get("langganan",{}).items():
                if isinstance(pkgs,list):
                    for p in pkgs:
                        k=p.get("paket") if isinstance(p,dict) else str(p)
                        if k: save_user_package_permanent(uid,k,"tambah")
            for uid,pkgs in db.get("langganan_cari",{}).items():
                if isinstance(pkgs,list):
                    for p in pkgs:
                        k=p.get("paket") if isinstance(p,dict) else str(p)
                        if k: save_user_package_permanent(uid,k,"cari")
                elif isinstance(pkgs,dict):
                    k=pkgs.get("paket")
                    if k: save_user_package_permanent(uid,k,"cari")
    except Exception as e:
        logger.warning(f"sync permanen warn: {e}")
    global _last_big_save
    try:
        _last_big_save
    except NameError:
        _last_big_save = 0
    import time as _time
    now = _time.time()
    if now - _last_big_save > 600:
        remote_ok = save_db_to_supabase(tmp)
        if remote_ok:
            _last_big_save = now
        return remote_ok
    else:
        return True

_last_big_save = 0


def save_db_async():
    """Simpan DB di background biar notifikasi nggak lambat - FIX untuk notif 1 detik"""
    def _save():
        try:
            save_db()
        except Exception as e:
            logger.error(f"save_db_async error: {e}")
    threading.Thread(target=_save, daemon=True).start()


def normalize_number(num):
    clean=''.join(filter(str.isdigit, str(num)))
    if not clean: return None
    if clean.startswith("62"):
        clean="0"+clean[2:]
    if clean.startswith("8"):
        clean="0"+clean
    return clean if len(clean)>=9 else None


def reset_old_blacklist():
    logger.info("reset_old_blacklist DIMATIKAN")
    return

def get_all_blacklist_for_cek():
    return get_all_pantauan()


WA_HISTORY_TABLE = "wa_history"
def load_wa_history_from_supabase():
    if not supabase_enabled():
        return None
    try:
        url = f"{SUPABASE_URL}/rest/v1/{WA_HISTORY_TABLE}"
        r = requests.get(url, headers=supabase_headers(), params={"select": "data_json", "order": "id.desc", "limit": "1"}, timeout=15)
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        payload = rows[0].get("data_json")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if isinstance(payload, list):
            return payload
        return None
    except Exception as e:
        logger.warning(f"WA history load supabase error: {e}")
        return None

def save_wa_history_to_supabase(h):
    if not supabase_enabled():
        return False
    try:
        url = f"{SUPABASE_URL}/rest/v1/{WA_HISTORY_TABLE}"
        body = {"data_json": h[-10000:] if len(h)>10000 else h}
        r = requests.get(url, headers=supabase_headers(), params={"select": "id", "limit": "1"}, timeout=15)
        r.raise_for_status()
        rows = r.json()
        if rows:
            row_id = rows[0]["id"]
            r = requests.patch(f"{url}?id=eq.{row_id}", headers=supabase_headers(), json=body, timeout=15)
        else:
            r = requests.post(url, headers=supabase_headers(), json=body, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.warning(f"WA history save supabase error: {e}")
        return False

def load_wa_history():
    sup = load_wa_history_from_supabase()
    if sup is not None and len(sup) > 0:
        try:
            with open(WA_HISTORY_FILE, "w", encoding="utf-8") as f: json.dump(sup,f,indent=2,ensure_ascii=False)
            os.makedirs(os.path.dirname(WA_HISTORY_PERSISTENT), exist_ok=True)
            with open(WA_HISTORY_PERSISTENT, "w", encoding="utf-8") as f: json.dump(sup,f,indent=2,ensure_ascii=False)
        except: pass
        return sup
    for p in [WA_HISTORY_PERSISTENT, WA_HISTORY_FILE]:
        try:
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data:
                        return data
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
        def _bg():
            save_wa_history_to_supabase(h)
        threading.Thread(target=_bg, daemon=True).start()
    except: pass


db = load_db()
# FIX: auto-seed dimatikan
# if supabase_enabled() and load_db_from_supabase() is None:
#     save_db_to_supabase(json.loads(json.dumps(db, default=str)))
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

# Worker khusus agar pengiriman Telegram ke banyak user berjalan paralel.
# Ini mencegah user terakhir menunggu kiriman user sebelumnya selesai.
NOTIFY_EXECUTOR = ThreadPoolExecutor(max_workers=20)

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
        
        # Simpan history di background agar tidak memperlambat notifikasi.
        history_item = {
            "group": group_name,
            "sender": sender_name,
            "number": sender_number_formatted,
            "text": clean_text,
            "time": datetime.now().isoformat()
        }
        def _save_history_async(item):
            try:
                history = load_wa_history()
                history.append(item)
                save_wa_history(history)
            except Exception as e:
                logger.error(f"History save error: {e}")
        threading.Thread(target=_save_history_async, args=(history_item,), daemon=True).start()

        # HOT PATH: gunakan state RAM. Jangan request Supabase untuk setiap pesan WA.
        # DB tetap diperbarui saat menu/admin menyimpan perubahan.
        fresh_db = db

        # Cek NO PANTAUAN dari data lokal/backup tanpa request jaringan.
        pantauan_list = set(
            list(fresh_db.get("blacklist", []) or []) +
            list(fresh_db.get("pantauan", []) or [])
        )
        try:
            pantauan_list.update(load_pantauan_backup() or [])
        except Exception:
            pass
        cek_numbers = [sender_number_formatted, sender_number]
        if sender_number_formatted.startswith("0"):
            cek_numbers.append("62" + sender_number_formatted[1:])
        if sender_number.startswith("62"):
            cek_numbers.append("0" + sender_number[2:])
        is_pantauan = any(n in pantauan_list for n in cek_numbers)
        if is_pantauan:
            logger.info(f"🚫 NO PANTAUAN {sender_number_formatted} - TIDAK di-forward")
            return "ok", 200
        
        # Proses setiap user
        matched_users = []
        notify_jobs = []
        now = datetime.now()
        
        for uid_str, uinfo in fresh_db.get("user_info", {}).items():
            try:
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
                
                # === LOGIKA CHECK EXPIRED PER KOTA - FIX V3 ===
                user_subs = fresh_db.get("langganan", {}).get(uid_str, [])
                if isinstance(user_subs, dict):
                    user_subs = [user_subs]
                
                has_active_paket = False
                if is_admin(int(uid_str)) if str(uid_str).isdigit() else False:
                    has_active_paket = True
                else:
                    for s in user_subs:
                        exp_str = s.get("expire", "")
                        if isinstance(exp_str, str):
                            try:
                                exp = datetime.fromisoformat(exp_str)
                            except:
                                exp = None
                        else:
                            exp = exp_str
                        if exp and exp > now:
                            q = s.get("kuota", s.get("quota", 1))
                            u = s.get("used_kuota", 0)
                            # jika masih ada kuota atau unlimited
                            if (q - u) > 0 or q >= 6:  # 6 = unlimited
                                has_active_paket = True
                                break
                            # fallback: jika kota sudah di-assign, tetap anggap aktif
                            if s.get("kota","").upper() not in ["", "BELUM DIPILIH", "TIDAK ADA KOTA"]:
                                has_active_paket = True
                                break
                
                # Jika ada match wilayah, wajib punya paket aktif
                if is_match and not has_active_paket:
                    # cek apakah user ini admin (admin bypass)
                    try:
                        if int(uid_str) not in ADMIN_IDS:
                            continue
                    except:
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
                    
                    msg = f"""Grup : {group_name}
Pengirim : {sender_name}
No WhatsApp : {sender_number_formatted}
━━━━━━━━━━━━━━━━━━━
{clean_text}
━━━━━━━━━━━━━━━━━━━
⚠️ Perhatian : untuk tetap waspada dan berhati-hati disarankan untuk rekber, terimakasih.sumber: https://t.me/Aakiwkiw_bot 🙏"""
                    
                    # Masukkan ke antrean paralel. Jangan tunggu request Telegram
                    # user sebelumnya selesai.
                    notify_jobs.append((uid_int, msg, sender_number_formatted, match_type))
                    
            except Exception as e:
                logger.error(f"WA USER PROCESS ERROR uid={uid_str}: {type(e).__name__}: {e}")
        
        # Kirim ke semua user secara paralel supaya tidak antre satu-per-satu.
        futures = {
            NOTIFY_EXECUTOR.submit(send_tg_message, uid, msg, wa): (uid, match)
            for uid, msg, wa, match in notify_jobs
        }
        for future in as_completed(futures):
            uid, match = futures[future]
            try:
                success = future.result()
                if success:
                    logger.info(f"✅ Notifikasi terkirim ke {uid} untuk match {match}")
                else:
                    logger.error(f"❌ Gagal kirim ke {uid}")
            except Exception as e:
                logger.error(f"❌ Worker Telegram error ke {uid}: {e}")

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
            q = s.get("kuota", 1)
            u = s.get("used_kuota", 0)
            if s.get("used") == True and u == 0:
                u = 1
            if q - u > 0:
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
    if is_admin(uid):
        return True
    sub = db.get("langganan_cari", {}).get(str(uid))
    if not sub:
        return False
    if isinstance(sub, list):
        for s in sub:
            if not is_expired(s):
                return True
        return False
    return sub and not is_expired(sub)

def is_expired(sub):
    if not sub:
        return True
    if isinstance(sub, list):
        for item in sub:
            if isinstance(item, dict):
                exp = item.get("expire")
                if isinstance(exp, str):
                    try:
                        exp = datetime.fromisoformat(exp)
                    except:
                        continue
                if exp and exp > datetime.now():
                    return False
        return True
    if isinstance(sub, dict):
        exp = sub.get("expire")
        if isinstance(exp, str):
            try:
                exp = datetime.fromisoformat(exp)
            except:
                return True
        return not exp or exp < datetime.now()
    return True

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
        [InlineKeyboardButton("🔎 CARI DATA LAIN", callback_data="menu_cari_data"), InlineKeyboardButton("📵 NO PANTAUAN", callback_data="menu_pantauan")],
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

def kb_pantauan_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 CARI NO PANTAUAN", callback_data="cari_pantauan")],
        [InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]
    ])

def kb_blacklist_menu():
    return kb_pantauan_menu()

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
    # Hanya tombol Chat di WA - KEMBALI MENU UTAMA dihilangkan
    rows=[]
    if clean_number:
        rows.append([InlineKeyboardButton("💬 Chat di WA", url=f"https://wa.me/{clean_number}")])
    return InlineKeyboardMarkup(rows)

def kb_hasil_cari_selesai():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 CARI DATA LAGI", callback_data="menu_cari_data")],
        [InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]
    ])

def kb_lanjut_tambah_kota():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 LANJUT TAMBAH KOTA", callback_data="menu_tambah_kota")],
        [InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]
    ])

def kb_lanjut_cari_data():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 LANJUT CARI DATA LAIN", callback_data="menu_cari_data")],
        [InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]
    ])

def kb_admin_panel():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 TAMPILKAN ID USER & USERNAME (START)", callback_data="admin_tampilkan_user_start")],
        [InlineKeyboardButton("🔍 CARI USER BY USERNAME", callback_data="admin_cari_user")],
        [InlineKeyboardButton("📊 STATUS USER AKTIF", callback_data="admin_status_user")],
        [InlineKeyboardButton("👤 CEK USER AKTIF (SIMPLE)", callback_data="admin_cek_aktif")],
        [InlineKeyboardButton("➕ TAMBAH NO PANTAUAN", callback_data="admin_tambah_pantauan")],
        [InlineKeyboardButton("➖ HAPUS NO PANTAUAN", callback_data="admin_hapus_pantauan")],
        [InlineKeyboardButton("🗑️ HAPUS SEMUA NO PANTAUAN", callback_data="admin_hapus_semua_pantauan")],
        [InlineKeyboardButton("🗑️ HAPUS ID USER", callback_data="admin_hapus_list")],
        [InlineKeyboardButton("📦 HAPUS PAKET USER", callback_data="admin_hapus_paket")],
        [InlineKeyboardButton("📦 TAMBAH PAKET USER", callback_data="admin_tambah_paket_user")],
        [InlineKeyboardButton("➕ TAMBAH ADMIN", callback_data="admin_tambah_admin")],
        [InlineKeyboardButton("➖ HAPUS ADMIN", callback_data="admin_hapus_admin")],
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
    subs = db.get("langganan", {}).get(str(uid), [])
    if isinstance(subs, dict):
        subs = [subs]
        db["langganan"][str(uid)] = subs
    now = datetime.now()
    kota = str(kota).strip()
    if not kota or not subs:
        return False
    # 1. Jika kota sudah pernah di-assign dan masih aktif, return True
    for sub in subs:
        if str(sub.get("kota","")).upper() == kota.upper():
            exp = sub.get("expire")
            if isinstance(exp, str):
                try: exp = datetime.fromisoformat(exp)
                except: exp = None
            if exp and exp > now:
                return True
    # 2. Cari slot yang masih BELUM DIPILIH / kosong dan masih aktif -> pakai itu
    for sub in subs:
        exp = sub.get("expire")
        if isinstance(exp, str):
            try: exp = datetime.fromisoformat(exp)
            except: exp = None
        if exp and exp > now:
            existing = str(sub.get("kota","")).upper()
            if existing in ["", "BELUM DIPILIH", "TIDAK ADA KOTA", "UMUM", "TIDAK DIKETAHUI", "PAKET AKTIF", "BELUM ADA"]:
                q = sub.get("kuota", sub.get("quota", 1))
                u = sub.get("used_kuota", 0)
                if u < q or q >= 6:
                    sub["used_kuota"] = u + 1 if existing not in ["BELUM DIPILIH"] else u
                    # untuk BELUM DIPILIH, jangan habiskan kuota dulu, cuma assign kota pertama
                    if existing == "BELUM DIPILIH" and u == 0:
                        sub["used_kuota"] = 0
                    sub["kota"] = kota
                    sub["used"] = True if existing != "BELUM DIPILIH" else False
                    # khusus BELUM DIPILIH -> tetap aktif, tapi tandai kota
                    if existing == "BELUM DIPILIH":
                        # buat entry baru untuk track kota ini agar tidak overwrite paket utama
                        pass
                    return True
    # 3. Jika ada paket dengan kuota sisa, assign
    for sub in subs:
        exp = sub.get("expire")
        if isinstance(exp, str):
            try: exp = datetime.fromisoformat(exp)
            except: exp = None
        if exp and exp > now:
            q = sub.get("kuota", sub.get("quota", 1))
            u = sub.get("used_kuota", 0)
            if u < q:
                sub["used_kuota"] = u + 1
                sub["kota"] = kota
                sub["used"] = True
                return True
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
    user_data=db["user_info"].get(str(uid),{})
    kotas=user_data.get("kotas",[])
    subs = db["langganan"].get(str(uid), [])
    if isinstance(subs, dict): subs = [subs]
    wilayah_list = []
    for k in kotas:
        parts=[p.strip() for p in str(k).split("|")]
        if len(parts)>=2:
            wilayah_list.append(parts[1])
        else:
            wilayah_list.append(str(k))
    wilayah_str = ", ".join(wilayah_list[:10]) if wilayah_list else "Belum pilih"
    now=datetime.now()
    sisa_total=0
    active_list=[]
    for s in subs:
        exp=s.get("expire")
        if isinstance(exp,str):
            try: exp=datetime.fromisoformat(exp)
            except: exp=None
        if exp and exp>now:
            q=s.get("kuota",1)
            u=s.get("used_kuota",0)
            if s.get("used")==True and u==0:
                u=1
            sisa = q - u
            if sisa>0:
                sisa_total+=sisa
                active_list.append(s.get('paket') + f" Sisa {sisa}/{q} Exp {exp.strftime('%d/%m/%Y')}")
    if is_admin(uid):
        txt = f"""📊 STATUS USER
Wilayah : {wilayah_str}
Kuota : UNLIMITED (ADMIN)"""
    else:
        if active_list:
            lang_str = chr(10).join(active_list)
            txt = f"""📊 STATUS USER
Paket Aktif:
{lang_str}

Wilayah : {wilayah_str}
SISA KUOTA: {sisa_total} kota lagi
Total Kota : {len(kotas)}

Aturan: 1x Top Up = 1 Kota, setelah simpan kecamatan menu terkunci lagi"""
        else:
            txt = f"""📊 STATUS USER
Paket : Kuota Habis - Harus Top Up lagi
Wilayah : {wilayah_str}
SISA KUOTA: 0 (1x Top Up = 1 Kota)
Total Kota : {len(kotas)}"""
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
    # FIX STATUS & INVITE DATE
    user_info = db.get("user_info", {}).get(str(uid), {})
    first_join = user_info.get("first_join")
    if not first_join:
        first_join = datetime.now().isoformat()
        db["user_info"][str(uid)]["first_join"] = first_join
        save_db()
    invite_date = first_join
    has_paket = is_active_tambah(uid) or is_active_cari(uid)
    if has_paket:
        status_text = "🟢 Active"
        # ambil paket untuk display
        all_pkgs = []
        subs = db.get("langganan", {}).get(str(uid), [])
        if isinstance(subs, dict):
            subs = [subs]
        all_pkgs.extend(subs if isinstance(subs, list) else [])
        subs_c = db.get("langganan_cari", {}).get(str(uid))
        if isinstance(subs_c, dict):
            all_pkgs.append(subs_c)
        elif isinstance(subs_c, list):
            all_pkgs.extend(subs_c)
        paket_text = ", ".join([p.get("nama", p.get("paket","")) for p in all_pkgs[:3] if isinstance(p, dict)]) or ["ada paket aktif"]
    else:
        status_text = "🔴 belum aktif segera aktifkan untuk memilih paket"
        paket_text = "belum ada paket"
    txt = f"""╭ ───┈ " ⭐⭐⭐⭐⭐" ── ⬦ ׁ
├   ➖ SEDULURAN BOT ➖
╰─┈꯭─꯭──꯭─꯭─꯭──꯭─╌─꯭─꯭─꯭─꯭──꯭──꯭
━━━━━━━━━━━━ ▪️▪️▪️
👤 Username: {username}
🆔 User ID : {uid}
📅 Invite : {invite_date}
📲 Status : {status_text}
📥 Paket : {paket_text}
━━━━━━━━━━━━━▪️▪️▪️ 

🎉 Selamat datang {nama}, untuk mengaktifkan fitur ini silahkan pilih paket yang sudah ada, tetap semangat dan jangan lupa bersyukur untuk hari ini. 🔥
━━━━━━━━━━━━━▪️▪️▪️
📝 BERKAH BERKAH BERKAH ..

Gunakan tombol di bawah ini :👇"""
    await update.message.reply_text(txt, reply_markup=kb_main(uid))


async def cb_handler(update,context):
    q=update.callback_query; await q.answer(); uid=q.from_user.id; data=q.data
    if data=="noop": return
    if data=="back_main":
        try: await q.message.delete()
        except: pass
        txt = """🟢 MODE ON MASIH AKTIF
━━━━━━━━━━━━━━━━━━━━━
Silahkan pilih menu lagi yah gaesss
Tetap semangat jangan mengeluh yah karena di dunia ini hanya sementara, kalau lelah jangan lupa istirahat jaga kesehatan yahh..
Silahkan lanjut pilih menunya lagi ."""
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

Kamu admin (Unlimited), bisa langsung pilih provinsi atau beli paket lagi."""
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
                    q=s.get("kuota",1)
                    u=s.get("used_kuota",0)
                    if s.get("used")==True and u==0:
                        u=1
                    if q - u > 0:
                        active=True
                        sisa+= q - u
            if active:
                txt=f"""🌍 TAMBAH KOTA - LANJUT TAMBAH KOTA

✅ Kuota aktif: {sisa} kota tersisa
Paket sudah di-ACC, silahkan pilih provinsi:

👇 Klik provinsi di bawah:"""
                await context.bot.send_message(chat_id=uid, text=txt, reply_markup=kb_provinsi())
            else:
                txt="""🌍 TAMBAH KOTA - PILIH PAKET

🔒 Kuota habis / belum ada paket aktif.
1x Top Up = 1x Tambah Kota

Silahkan pilih paket untuk Top Up lagi:"""
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
        context.user_data.pop("pending_paket_cari", None)
        context.user_data["pending_paket_tambah"]=paket_key
        context.user_data["paket_type"]="tambah"
        context.user_data["paket_pilih"]=paket_key
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
        context.user_data.pop("pending_paket_tambah", None)
        context.user_data["pending_paket_cari"]=paket_key
        context.user_data["paket_type"]="cari"
        context.user_data["paket_pilih"]=paket_key
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
        try:
            await q.message.delete()
        except:
            pass
        if not is_active_cari(uid):
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
    if data=="menu_pantauan" or data=="menu_blacklist":
        try:
            await q.message.delete()
        except:
            pass
        bl = get_all_pantauan()
        # Pastikan bl adalah list
        if not isinstance(bl, list):
            bl = list(bl) if bl else []
        total = len(bl)
        if total == 0:
            try:
                await context.bot.send_message(chat_id=uid, text="📵 NO PANTAUAN\n\n📭 Belum ada nomor pantauan\n📊 Total: 0 nomor", reply_markup=kb_blacklist_menu())
            except:
                await context.bot.send_message(chat_id=uid, text="📵 NO PANTAUAN\nTotal: 0 nomor", reply_markup=kb_back_main_only())
            return
        
        # Kirim SEMUA nomor - tanpa keyboard dulu biar anti error
        header = f"📵 NO PANTAUAN - SEMUA NOMOR ({total} nomor)\n\n"
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
                await context.bot.send_message(chat_id=uid, text=f"✅ Selesai menampilkan {total} nomor pantauan", reply_markup=kb_blacklist_menu())
            except:
                await context.bot.send_message(chat_id=uid, text=f"✅ Selesai {total} nomor", reply_markup=kb_back_main_only())
        return
    if data.startswith("pantauan_page_") or data.startswith("blacklist_page_"):
        # Redirect ke menu_blacklist (tampilkan semua)
        try:
            await q.message.delete()
        except:
            pass
        bl = get_all_pantauan()
        total = len(bl)
        header = f"📵 NO PANTAUAN - SEMUA NOMOR ({total} nomor)\n\n"
        msg = header
        for i, nomor in enumerate(bl, 1):
            msg += f"{i}. {nomor}\n"
            if len(msg) > 3500:
                await context.bot.send_message(chat_id=uid, text=msg)
                msg = ""
        if msg:
            await context.bot.send_message(chat_id=uid, text=msg, reply_markup=kb_blacklist_menu())
        return

    if data=="cari_pantauan" or data=="cari_blacklist":
        try: await q.message.delete()
        except: pass
        context.user_data["awaiting_cek_pantauan"]=True
        await context.bot.send_message(chat_id=uid, text="🔍 CARI NO PANTAUAN\n\nKetik nomor yang ingin dicek:\nContoh: 083123456789", reply_markup=kb_back_main_only())
        return
    if data=="menu_hubungi_admin":
        try: await q.message.delete()
        except: pass
        txt="📞 HUBUNGI ADMIN\nJika membutuhkan bantuan, silakan hubungi Admin:\n👤 Telegram @Hambali1995\n📱 WhatsApp 083160776091"
        await context.bot.send_message(chat_id=uid, text=txt, reply_markup=kb_hubungi_admin())
        return

    if data=="admin_tambah_paket_user":
        if not is_admin(uid):
            return
        try:
            await q.message.delete()
        except:
            pass
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌍 1. TAMBAH KOTA", callback_data="admin_pilih_paket_tambah_kota")],
            [InlineKeyboardButton("🔎 2. CARI DATA LAIN", callback_data="admin_pilih_paket_cari_data")],
            [InlineKeyboardButton("🏠 KEMBALI MENU ADMIN", callback_data="admin_menu")]
        ])
        await context.bot.send_message(chat_id=uid, text="📦 TAMBAH PAKET USER\n\nPilih paket:", reply_markup=kb)
        return

    if data=="admin_pilih_paket_tambah_kota":
        if not is_admin(uid):
            return
        try:
            await q.message.delete()
        except:
            pass
        context.user_data['admin_paket_type'] = 'tambah_kota'
        context.user_data['mode'] = 'admin_tambah_kota_id'
        await context.bot.send_message(chat_id=uid, text="🌍 AKTIFKAN TAMBAH KOTA\nKirim ID USER:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ BATAL", callback_data="admin_menu")]]))
        return

    if data=="admin_pilih_paket_cari_data":
        if not is_admin(uid):
            return
        try:
            await q.message.delete()
        except:
            pass
        context.user_data['admin_paket_type'] = 'cari_data'
        context.user_data['mode'] = 'admin_tambah_cari_id'
        await context.bot.send_message(chat_id=uid, text="🔎 AKTIFKAN CARI DATA LAIN\nKirim ID USER:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ BATAL", callback_data="admin_menu")]]))
        return

    if data.startswith("admin_hari_"):
        if not is_admin(uid):
            return
        try:
            hari = int(data.replace("admin_hari_", ""))
            context.user_data['admin_hari'] = hari
            context.user_data['mode'] = 'admin_tambah_kota_kuota'
            await q.message.delete()
            await context.bot.send_message(chat_id=uid, text=f"Durasi {hari} HARI\nKirim kuota (contoh 3):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("3x", callback_data="admin_kuota_3"), InlineKeyboardButton("5x", callback_data="admin_kuota_5")]]))
        except:
            pass
        return

    if data.startswith("admin_kuota_"):
        if not is_admin(uid):
            return
        try:
            kuota = int(data.replace("admin_kuota_", ""))
            id_user = context.user_data.get('admin_target_id')
            hari = context.user_data.get('admin_hari', 30)
            from datetime import timedelta as _td
            expire = datetime.now() + _td(days=hari)
            db.setdefault("langganan", {})
            if id_user not in db["langganan"]:
                db["langganan"][id_user] = []
            elif isinstance(db["langganan"][id_user], dict):
                db["langganan"][id_user] = [db["langganan"][id_user]]
            db["langganan"][id_user] = [p for p in db["langganan"][id_user] if not (isinstance(p, dict) and p.get("kota")=="BELUM DIPILIH")]
            db["langganan"][id_user].append({
                "kota": "BELUM DIPILIH",
                "paket": f"{hari}hari_{kuota}x",
                "nama": f"{hari} HARI - {kuota}x PILIH PROVINSI",
                "expire": expire,
                "kuota": kuota,
                "quota": kuota,
                "used_kuota": 0,
                "used": False,
                "hari": hari
            })
            save_db()
            save_user_package_permanent(id_user, f"{hari}hari_{kuota}x", "tambah")
            try:
                await context.bot.send_message(chat_id=int(id_user), text=f"🎉 PAKET TAMBAH KOTA AKTIF! {hari} HARI {kuota}x", reply_markup=kb_main(int(id_user)))
            except:
                pass
            await q.message.delete()
            await context.bot.send_message(chat_id=uid, text=f"✅ TAMBAH KOTA {id_user} {hari} HARI {kuota}x", reply_markup=kb_admin_panel())
            context.user_data['mode'] = None
            context.user_data.pop('admin_target_id', None)
            context.user_data.pop('admin_hari', None)
        except Exception as e:
            await context.bot.send_message(chat_id=uid, text=f"❌ Error: {e}")
        return

    if data.startswith("admin_cari_hari_"):
        if not is_admin(uid):
            return
        try:
            hari = int(data.replace("admin_cari_hari_", ""))
            id_user = context.user_data.get('admin_target_id')
            from datetime import timedelta as _td2
            expire = datetime.now() + _td2(days=hari)
            db.setdefault("langganan_cari", {})
            db["langganan_cari"][id_user] = {"paket": f"cari_{hari}hari", "nama": f"CARI DATA LAIN {hari} HARI", "expire": expire, "hari": hari}
            save_db()
            save_user_package_permanent(id_user, f"cari_{hari}hari", "cari")
            try:
                await context.bot.send_message(chat_id=int(id_user), text=f"🎉 CARI DATA LAIN AKTIF {hari} HARI", reply_markup=kb_main(int(id_user)))
            except:
                pass
            await q.message.delete()
            await context.bot.send_message(chat_id=uid, text=f"✅ CARI DATA LAIN {id_user} {hari} HARI", reply_markup=kb_admin_panel())
            context.user_data['mode'] = None
            context.user_data.pop('admin_target_id', None)
        except Exception as e:
            await context.bot.send_message(chat_id=uid, text=f"❌ Error: {e}")
        return


    if data=="admin_menu":
        if not is_admin(uid): return
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text="🧭 PANEL ADMIN\n\nPilih menu admin:", reply_markup=kb_admin_panel())
        return

    if data=="admin_tampilkan_user_start":
        if not is_admin(uid): return
        try: await q.message.delete()
        except: pass
        user_list = db.get("user_info", {})
        if not user_list:
            await context.bot.send_message(chat_id=uid, text="❌ Belum ada user yang klik START", reply_markup=kb_admin_panel())
            return
        
        # Format: ID | Username | Nama | Jumlah Kota
        text_out = f"👥 DAFTAR USER YANG SUDAH KLIK START\n"
        text_out += f"📊 Total: {len(user_list)} user\n"
        text_out += f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        # Buat file txt lengkap untuk export
        full_export = "DAFTAR USER START\n"
        full_export += f"Total: {len(user_list)} user\n"
        full_export += f"Export: {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
        full_export += "="*40 + "\n"
        
        count = 0
        preview_lines = []
        for uid_str, info in list(user_list.items())[:50]:  # preview 50 pertama di chat
            count += 1
            nama = info.get("nama","-").replace("\n"," ")
            username = info.get("username","-")
            if username != "-" and not username.startswith("@"):
                username = f"@{username}"
            kotas = len(info.get("kotas",[]))
            
            preview_lines.append(f"{count}. 🆔 {uid_str}\n   👤 {nama}\n   🔗 {username} | Kota:{kotas}")
            full_export += f"{count}. ID: {uid_str} | Username: {username} | Nama: {nama} | Kota: {kotas}\n"
        
        text_out += "\n\n".join(preview_lines)
        if len(user_list) > 50:
            text_out += f"\n\n... dan {len(user_list)-50} user lainnya\n"
            text_out += f"\n📄 File lengkap akan dikirim di bawah."
        
        # Kirim preview
        # Potong jika kepanjangan > 4000 char
        if len(text_out) > 4000:
            text_out = text_out[:3900] + "\n\n... (terpotong, cek file txt)"
        
        await context.bot.send_message(chat_id=uid, text=text_out, reply_markup=kb_admin_panel())
        
        # Kirim file txt lengkap
        try:
            # Buat file untuk semua user
            all_lines = []
            for i, (uid_str, info) in enumerate(user_list.items(), 1):
                nama = info.get("nama","-")
                username = info.get("username","-")
                if username != "-" and not username.startswith("@"):
                    username_display = f"@{username}"
                else:
                    username_display = username
                all_lines.append(f"{uid_str} | {username_display} | {nama}")
            
            txt_content = "\n".join(all_lines)
            import io
            bio = io.BytesIO(txt_content.encode('utf-8'))
            bio.name = f"user_start_{len(user_list)}_user.txt"
            await context.bot.send_document(chat_id=uid, document=bio, filename=bio.name, caption=f"📄 Export lengkap {len(user_list)} user yang sudah START\nFormat: ID | USERNAME | NAMA")
            
            # Kirim juga versi CSV
            csv_content = "id_user,username,nama,jumlah_kota,first_seen\n"
            for uid_str, info in user_list.items():
                nama = info.get("nama","-").replace(","," ")
                username = info.get("username","-").replace(","," ")
                kotas = len(info.get("kotas",[]))
                csv_content += f"{uid_str},{username},{nama},{kotas},-\n"
            csv_bio = io.BytesIO(csv_content.encode('utf-8'))
            csv_bio.name = f"user_start_{len(user_list)}_user.csv"
            await context.bot.send_document(chat_id=uid, document=csv_bio, filename=csv_bio.name, caption="📊 Versi CSV untuk Excel/Sheets")
            
        except Exception as e:
            logger.error(f"export user start error {e}")
        
        return

    if data=="admin_tambah_admin":
        if not is_admin(uid): return
        try: await q.message.delete()
        except: pass
        context.user_data['mode'] = 'tambah_admin'

    if data=="admin_cari_user":
        if not is_admin(uid): return
        try: await q.message.delete()
        except: pass
        context.user_data['mode'] = 'cari_user'
        await context.bot.send_message(
            chat_id=uid,
            text="🔍 *CARI USER BY USERNAME / ID / NAMA*\n\n"
                 "Kirim username, ID, atau nama yang mau dicari.\n\n"
                 "Contoh:\n"
                 "`@hambali`\n"
                 "`hambali`\n"
                 "`7962377902`\n"
                 "`Budi`\n\n"
                 "Bot akan cari yang mirip-mirip juga (fuzzy search).",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ BATAL", callback_data="admin_menu")]])
        )
        return

    if data=="admin_hapus_admin":
        if not is_admin(uid): return
        try: await q.message.delete()
        except: pass
        daftar = "\n".join([f"`{x}`" for x in ADMIN_IDS])
        context.user_data['mode'] = 'hapus_admin'
        await context.bot.send_message(chat_id=uid, text=f"➖ *HAPUS ADMIN*\n\nDaftar admin:\n{daftar}\n\nKirim ID yang mau dihapus.", parse_mode='Markdown', reply_markup=kb_admin_panel())
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
    if data=="admin_tambah_pantauan" or data=="admin_tambah_blacklist":
        if not is_admin(uid): return
        context.user_data["awaiting_tambah_pantauan"]=True
        try: await q.message.delete()
        except: pass
        await context.bot.send_message(chat_id=uid, text="➕ TAMBAH NO PANTAUAN BULK\n\nKirim nomor sekaligus, contoh:\n/Adds\n081223455666\n089737388383\n087876273838\n089828828288\n\nAtau tanpa /Adds, langsung nomor per baris / koma:\n0812..., 0813...\n\nSistem otomatis masuk ke NO PANTAUAN", reply_markup=kb_back_main_only())
        return
    if data=="admin_hapus_pantauan" or data=="admin_hapus_blacklist":
        if not is_admin(uid): return
        context.user_data["awaiting_hapus_pantauan"]=True
        try: await q.message.delete()
        except: pass
        all_nums = get_all_pantauan()
        preview = "\n".join(all_nums[:20])
        if len(all_nums)>20: preview+=f"\n... dan {len(all_nums)-20} lainnya"
        if not all_nums: preview="(kosong)"
        txt = f"➖ HAPUS NO PANTAUAN\n\n📊 Total: {len(all_nums)} nomor\n\nDaftar:\n{preview}\n\nKirim nomor yang ingin dihapus (bulk, pisah baris/koma):"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ HAPUS SEMUA NO PANTAUAN", callback_data="admin_hapus_semua_pantauan")],[InlineKeyboardButton("🏠 KEMBALI", callback_data="admin_menu")]])
        await context.bot.send_message(chat_id=uid, text=txt, reply_markup=kb)
        return
    if data=="admin_hapus_semua_pantauan":
        if not is_admin(uid): return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ YA, HAPUS SEMUA", callback_data="admin_confirm_hapus_semua_pantauan")],[InlineKeyboardButton("❌ BATAL", callback_data="admin_menu")]])
        await q.message.edit_text(f"⚠️ KONFIRMASI HAPUS SEMUA\n\n📊 Total: {len(get_all_pantauan())} nomor akan dihapus permanen!\nYakin?", reply_markup=kb)
        return
    if data=="admin_confirm_hapus_semua_pantauan":
        if not is_admin(uid): return
        all_nums=get_all_pantauan(); count=len(all_nums)
        for num in all_nums:
            try: remove_pantauan_from_supabase(num)
            except: pass
        db["pantauan"]=[]; db["blacklist"]=[]
        try: save_pantauan_backup([])
        except: pass
        save_db()
        try: await q.message.edit_text(f"✅ Berhasil hapus SEMUA {count} nomor pantauan!\n📊 Sekarang: 0 nomor", reply_markup=kb_admin_panel())
        except: await context.bot.send_message(chat_id=uid, text=f"✅ Berhasil hapus SEMUA {count} nomor!", reply_markup=kb_admin_panel())
        return
    if data=="admin_blacklist_menu":
        if not is_admin(uid): return
        bl = db.get("blacklist",[])
        txt=f"🚫 KELOLA NO PANTAUAN\n\n📊 Total: {len(bl)} nomor"
        kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ TAMBAH NO PANTAUAN (BULK)", callback_data="admin_tambah_blacklist"), InlineKeyboardButton("➖ HAPUS NO PANTAUAN", callback_data="admin_hapus_blacklist")],
            [InlineKeyboardButton("📋 LIHAT PANTAUAN", callback_data="menu_blacklist")],
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
            if not is_admin(uid):
                assign_subscription_to_selected_city(uid, kota)
            # FIX NOTIF CEPAT 1 DETIK - edit & notif dulu, save belakangan
            try:
                await q.message.edit_text(f"✅ Berhasil {len(added)} kecamatan di {kota} - {prov}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]]))
            except: pass
            try:
                ui = db["user_info"].get(str(uid), {})
                nm = ui.get("nama", getattr(q.from_user, "first_name", "-"))
                if is_admin(uid):
                    notif_text = f"✅ ADMIN PILIH KOTA\n👤 {nm} (ADMIN)\n🆔 {uid}\n🌍 {prov} | {kota}\n📍 {', '.join(selected[:10])}\n📊 {len(added)} kec\n⏰ {datetime.now().strftime('%d/%m %H:%M')}"
                    await context.bot.send_message(chat_id=uid, text=notif_text)
                else:
                    notif_text = f"✅ BERHASIL TAMBAH KOTA\n🌍 {prov} | {kota}\n📍 {', '.join(selected[:10])}\n📊 {len(added)} kecamatan ditambahkan\n⏰ {datetime.now().strftime('%d/%m %H:%M')}"
                    await context.bot.send_message(chat_id=uid, text=notif_text)
            except Exception as e: logger.error(f"notify error {e}")
            save_db_async()
        else:
            await q.message.edit_text(f"⚠️ Sudah ada semua!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_main")]]))
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
            # FIX CEPAT 1 DETIK
            try:
                await q.message.edit_text(
                    f"✅ Berhasil menambahkan {added} kecamatan di {kota}.\n"
                    f"📍 Pesan WA tetap wajib mengandung KOTA + KECAMATAN.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 KEMBALI MENU UTAMA", callback_data="back_main")]])
                )
            except: pass
            try:
                ui = db["user_info"].get(str(uid), {})
                nm = ui.get("nama", getattr(q.from_user, "first_name", "-"))
                if is_admin(uid):
                    notif_text = f"✅ ADMIN PILIH KOTA (ALL)\n👤 {nm} (ADMIN)\n🆔 {uid}\n🌍 {prov} | {kota}\n📦 {added} kec SEMUA\n⏰ {datetime.now().strftime('%d/%m %H:%M')}"
                    await context.bot.send_message(chat_id=uid, text=notif_text)
                else:
                    notif_text = f"✅ BERHASIL TAMBAH KOTA (ALL)\n🌍 {prov} | {kota}\n📦 {added} kecamatan ditambahkan SEMUA\n⏰ {datetime.now().strftime('%d/%m %H:%M')}"
                    await context.bot.send_message(chat_id=uid, text=notif_text)
            except Exception as e: logger.error(f"notify admin all error {e}")
            save_db_async()
        else:
            await q.message.edit_text(
                f"⚠️ Semua kecamatan di {kota} sudah ada.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_main")]])
            )
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
                if is_admin(uid):
                    notif_text = f"✅ ADMIN PILIH KOTA (ALL)\n👤 {nm} (ADMIN)\n🆔 {uid}\n🌍 {prov} | {kota}\n📦 {added} kec SEMUA\n⏰ {datetime.now().strftime('%d/%m %H:%M')}"
                    try: await context.bot.send_message(chat_id=uid, text=notif_text)
                    except: pass
                else:
                    notif_text = f"✅ BERHASIL TAMBAH KOTA (ALL)\n🌍 {prov} | {kota}\n📦 {added} kecamatan ditambahkan SEMUA\n⏰ {datetime.now().strftime('%d/%m %H:%M')}"
                    try: await context.bot.send_message(chat_id=uid, text=notif_text)
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
        if not is_admin(uid):
            try:
                await q.answer("❌ Hanya admin", show_alert=True)
            except:
                pass
            return
        try:
            await q.answer("⏳ Proses...")
        except:
            pass
        try:
            parts_raw = data.split("_")
            action = parts_raw[0]
            ptype = parts_raw[1]
            remaining = "_".join(parts_raw[2:])
            import re as _re
            m = _re.match(r"^(\d+)_?(.*)$", remaining)
            if m:
                target_uid_str = m.group(1)
                pkey = m.group(2) if m.group(2) else "1bulan"
            else:
                target_uid_str = parts_raw[2]
                pkey = "_".join(parts_raw[3:]) if len(parts_raw)>3 else "1bulan"
            try:
                target_uid_int = int(target_uid_str)
            except:
                target_uid_int = int(''.join(filter(str.isdigit, target_uid_str)) or 0)
            target_uid_str = str(target_uid_int)
            if action == "acc":
                if ptype == "tambah":
                    from datetime import timedelta as _td
                    p = PAKET_TAMBAH.get(pkey, PAKET_TAMBAH.get("1bulan", {"nama":"1 BULAN","hari":30,"kuota":3}))
                    expire = datetime.now() + _td(days=p.get("hari",30))
                    caption = q.message.caption or q.message.text or ""
                    kota_target = "BELUM DIPILIH"
                    for line in caption.split("\n"):
                        if "Kota:" in line:
                            kota_target = line.replace("Kota:", "").strip() or "BELUM DIPILIH"
                            break
                    db.setdefault("langganan", {})
                    if target_uid_str not in db["langganan"]:
                        db["langganan"][target_uid_str] = []
                    elif isinstance(db["langganan"][target_uid_str], dict):
                        db["langganan"][target_uid_str] = [db["langganan"][target_uid_str]]
                    db["langganan"][target_uid_str].append({
                        "kota": kota_target,
                        "paket": pkey,
                        "nama": p.get("nama", pkey),
                        "expire": expire,
                        "kuota": p.get("kuota",3),
                        "quota": p.get("kuota",3),
                        "used_kuota": 0,
                        "used": False,
                        "hari": p.get("hari",30)
                    })
                    save_db()
                    try:
                        save_user_package_permanent(target_uid_str, pkey, "tambah")
                    except:
                        pass
                    try:
                        if getattr(q.message, 'photo', None):
                            await q.message.edit_caption(caption=(q.message.caption or "")+f"\n\n✅ DISETUJUI - {p.get('nama')} sampai {expire.strftime('%d/%m/%Y')}", reply_markup=None)
                        else:
                            await q.message.edit_text(text=(q.message.text or "")+f"\n\n✅ DISETUJUI", reply_markup=None)
                    except Exception as e:
                        logger.warning(f"edit fail: {e}")
                    try:
                        notif_tambah = (
                            f"🎉 PAKET AKTIF - TAMBAH KOTA ✅\n"
                            f"━━━━━━━━━━━━━━━━━━━━━\n"
                            f"Halo! Paket kamu sudah di-AKTIFKAN admin 🤩\n\n"
                            f"🎁 Paket: {p.get('nama')} ({pkey})\n"
                            f"💰 Harga: Rp {p.get('harga',0):,}\n"
                            f"⏰ Durasi: {p.get('hari')} hari\n"
                            f"🎟️ Kuota: {p.get('kuota')}x Tambah Kota\n"
                            f"📅 Aktif Sampai: {expire.strftime('%d/%m/%Y %H:%M')} WIB\n"
                            f"🏙️ Kota: {kota_target}\n"
                            f"━━━━━━━━━━━━━━━━━━━━━\n"
                            f"✅ Sekarang kamu bisa langsung pilih provinsi untuk tambah kota!"
                        )
                        # Tombol langsung action sesuai paket
                        kb_notif_tambah = InlineKeyboardMarkup([
                            [InlineKeyboardButton("🌍 PILIH PROVINSI SEKARANG", callback_data="menu_tambah_kota")],
                            [InlineKeyboardButton("📊 CEK STATUS PAKET", callback_data="menu_status")],
                            [InlineKeyboardButton("🏠 MENU UTAMA", callback_data="back_main")]
                        ])
                        await context.bot.send_message(chat_id=target_uid_int, text=notif_tambah, reply_markup=kb_notif_tambah)
                    except Exception as e:
                        logger.warning(f"notif tambah fail {target_uid_str}: {e}")
                        try:
                            await context.bot.send_message(chat_id=target_uid_int, text=f"✅ TOP UP DISETUJUI ✅\nPaket {p.get('nama')} sampai {expire.strftime('%d/%m/%Y')}", reply_markup=kb_main(target_uid_int))
                        except:
                            pass
                else:
                    from datetime import timedelta as _td2
                    p = PAKET_CARI.get(pkey, PAKET_CARI.get("1bulan", {"nama":"CARI 1 BULAN","hari":30}))
                    expire = datetime.now() + _td2(days=p.get("hari",30))
                    db.setdefault("langganan_cari", {})
                    db["langganan_cari"][target_uid_str] = {"paket": pkey, "nama": p.get("nama", pkey), "expire": expire, "hari": p.get("hari",30)}
                    save_db()
                    try:
                        save_user_package_permanent(target_uid_str, pkey, "cari")
                    except:
                        pass
                    try:
                        if getattr(q.message, 'photo', None):
                            await q.message.edit_caption(caption=(q.message.caption or "")+f"\n\n✅ DISETUJUI CARI - {p.get('nama')}", reply_markup=None)
                        else:
                            await q.message.edit_text(text=(q.message.text or "")+f"\n\n✅ DISETUJUI CARI", reply_markup=None)
                    except:
                        pass
                    try:
                        notif_cari = (
                            f"🔎 PAKET AKTIF - CARI DATA LAIN ✅\n"
                            f"━━━━━━━━━━━━━━━━━━━━━\n"
                            f"Halo! Paket CARI kamu sudah di-AKTIFKAN admin 🤩\n\n"
                            f"🎁 Paket: {p.get('nama')} ({pkey})\n"
                            f"💰 Harga: Rp {p.get('harga',0):,}\n"
                            f"⏰ Durasi: {p.get('hari')} hari\n"
                            f"📅 Aktif Sampai: {expire.strftime('%d/%m/%Y %H:%M')} WIB\n"
                            f"━━━━━━━━━━━━━━━━━━━━━\n"
                            f"✅ Sekarang kamu bisa pakai fitur CARI DATA LAIN!\n"
                            f"Ketik nama kota yang mau dicari ya."
                        )
                        kb_notif_cari = InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔎 MULAI CARI DATA", callback_data="menu_cari_data")],
                            [InlineKeyboardButton("📊 CEK STATUS PAKET", callback_data="menu_status")],
                            [InlineKeyboardButton("🏠 MENU UTAMA", callback_data="back_main")]
                        ])
                        await context.bot.send_message(chat_id=target_uid_int, text=notif_cari, reply_markup=kb_notif_cari)
                    except Exception as e:
                        logger.warning(f"notif cari fail {target_uid_str}: {e}")
                        try:
                            await context.bot.send_message(chat_id=target_uid_int, text=f"✅ CARI DATA DISETUJUI ✅\nPaket {p.get('nama')} sampai {expire.strftime('%d/%m/%Y')}", reply_markup=kb_main(target_uid_int))
                        except:
                            pass
            else:
                try:
                    if getattr(q.message, 'photo', None):
                        await q.message.edit_caption(caption=(q.message.caption or "")+"\n\n❌ DITOLAK", reply_markup=None)
                    else:
                        await q.message.edit_text(text=(q.message.text or "")+"\n\n❌ DITOLAK", reply_markup=None)
                except:
                    pass
                try:
                    await context.bot.send_message(chat_id=target_uid_int, text="❌ Top Up DITOLAK admin.")
                except:
                    pass
        except Exception as e:
            logger.error(f"SETUJU ERROR: {e}", exc_info=True)
            try:
                await q.message.reply_text(f"❌ Error SETUJU/TOLAK: {e}")
            except:
                pass
        return

async def text_handler(update,context):
    uid=update.effective_user.id
    text=update.message.text.strip()

    # ====== MENU ADMIN MANUAL TYPING SUPPORT ======
    raw_up = text.upper()
    norm_low = text.strip().lower()
    
    # Bisa diketik: TAMPILKAN USER / TAMPILKAN ID USER / CEK USER START / LIST USER
    if any(x in raw_up for x in ["TAMPILKAN ID USER", "TAMPILKAN USER", "LIST USER START", "CEK USER START", "DAFTAR USER"]):
        if not is_admin(uid):
            await update.message.reply_text("❌ Hanya admin", reply_markup=kb_main(uid))
            return
        user_list = db.get("user_info", {})
        if not user_list:
            await update.message.reply_text("❌ Belum ada user yang klik START", reply_markup=kb_main(uid))
            return
        text_out = f"👥 DAFTAR USER START\n📊 Total: {len(user_list)} user\n━━━━━━━━━━━━━━\n\n"
        lines = []
        for i, (uid_str, info) in enumerate(list(user_list.items())[:50], 1):
            nama = info.get("nama","-")
            username = info.get("username","-")
            if username != "-" and not username.startswith("@"):
                username = f"@{username}"
            has_p = "✅" if (uid_str in db.get("langganan",{}) or uid_str in db.get("langganan_cari",{})) else "❌"
            lines.append(f"{i}. 🆔 {uid_str} | {username} | {nama} | {has_p} Paket")
        text_out += "\n".join(lines)
        if len(user_list) > 50:
            text_out += f"\n\n... dan {len(user_list)-50} lainnya, file lengkap dikirim."
        await update.message.reply_text(text_out, reply_markup=kb_main(uid))
        
        # kirim file txt juga kalau diketik manual
        try:
            import io
            all_lines = []
            for uid_str, info in user_list.items():
                nama = info.get("nama","-")
                username = info.get("username","-")
                if username != "-" and not username.startswith("@"):
                    username = f"@{username}"
                all_lines.append(f"{uid_str} | {username} | {nama}")
            bio = io.BytesIO("\n".join(all_lines).encode('utf-8'))
            bio.name = f"user_start_{len(user_list)}.txt"
            await context.bot.send_document(chat_id=uid, document=bio, filename=bio.name)
        except Exception as e:
            logger.error(f"export manual error {e}")
        return

    # Manual typing untuk 4 menu sebelumnya
    if raw_up.startswith("TAMBAH ADMIN") or raw_up == "➕ TAMBAH ADMIN" or norm_low == "tambah admin":
        if not is_admin(uid): return
        context.user_data['mode'] = 'tambah_admin'
        await update.message.reply_text("➕ TAMBAH ADMIN\nKirim ID baru:", reply_markup=kb_main(uid))
        return
    if raw_up.startswith("HAPUS ADMIN") or norm_low == "hapus admin":
        if not is_admin(uid): return
        context.user_data['mode'] = 'hapus_admin'
        daftar = "\n".join([f"{x}" for x in ADMIN_IDS])
        await update.message.reply_text(f"➖ HAPUS ADMIN\nDaftar:\n{daftar}\n\nKirim ID yang mau dihapus:", reply_markup=kb_main(uid))
        return
    if "TAMBAH PAKET USER" in raw_up or norm_low in ["tambah paket user", "tambah paket"]:
        if not is_admin(uid): return
        context.user_data['mode'] = 'tambah_paket_user'
        await update.message.reply_text(
            "📦 TAMBAH PAKET USER\n\nSilahkan kirim:\n`ID USER | KOTA | KECAMATAN | PAKET | KUOTA HARI`\nContoh: `123 | Serang | Ciruas | CARI DATA LAIN | 30`",
            parse_mode='Markdown', reply_markup=kb_main(uid)
        )
        return
    if "HAPUS PAKET USER" in raw_up or norm_low in ["hapus paket user", "hapus paket"]:
        if not is_admin(uid): return
        context.user_data['mode'] = 'hapus_paket_user'
        await update.message.reply_text("🗑️ HAPUS PAKET USER\nKirim ID USER:", reply_markup=kb_main(uid))
        return

    # Mode handling untuk tambah/hapus admin & paket (lanjutan)
    mode = context.user_data.get('mode')
    if mode == 'admin_tambah_kota_id':
        try:
            import re as re2
            id_user = re2.sub(r'[^0-9]', '', text)
            if not id_user or len(id_user) < 5:
                await update.message.reply_text("❌ ID tidak valid! Contoh 8877623904")
                return
            context.user_data['admin_target_id'] = id_user
            context.user_data['mode'] = 'admin_tambah_kota_hari'
            await update.message.reply_text(f"✅ ID {id_user}\nKirim durasi (30):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("30 HARI", callback_data="admin_hari_30"), InlineKeyboardButton("60 HARI", callback_data="admin_hari_60")]]))
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        return
    if mode == 'admin_tambah_kota_hari':
        try:
            import re as re2
            hari = int(re2.sub(r'[^0-9]', '', text)) or 30
            context.user_data['admin_hari'] = hari
            context.user_data['mode'] = 'admin_tambah_kota_kuota'
            await update.message.reply_text(f"✅ {hari} HARI\nKirim kuota (3):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("3x", callback_data="admin_kuota_3")]]))
        except:
            await update.message.reply_text("❌ Format salah!")
        return
    if mode == 'admin_tambah_kota_kuota':
        try:
            import re as re2
            kuota = int(re2.sub(r'[^0-9]', '', text)) or 3
            id_user = context.user_data.get('admin_target_id')
            hari = context.user_data.get('admin_hari', 30)
            from datetime import timedelta as _td
            expire = datetime.now() + _td(days=hari)
            db.setdefault("langganan", {})
            if id_user not in db["langganan"]:
                db["langganan"][id_user] = []
            db["langganan"][id_user] = [p for p in db["langganan"][id_user] if not (isinstance(p, dict) and p.get("kota")=="BELUM DIPILIH")]
            db["langganan"][id_user].append({"kota": "BELUM DIPILIH", "paket": f"{hari}hari_{kuota}x", "nama": f"{hari} HARI - {kuota}x", "expire": expire, "kuota": kuota, "quota": kuota, "used_kuota": 0, "used": False, "hari": hari})
            save_db()
            save_user_package_permanent(id_user, f"{hari}hari_{kuota}x", "tambah")
            await update.message.reply_text(f"✅ TAMBAH KOTA {id_user} {hari} HARI {kuota}x", reply_markup=kb_admin_panel())
            # NOTIF KE USER - SESUAI PAKET YANG DIAKTIFKAN ADMIN + BUTTON
            try:
                id_user_int = int(id_user)
                notif = (
                    f"🎉 PAKET AKTIF - TAMBAH KOTA ✅\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Admin telah mengaktifkan paket untuk kamu!\n\n"
                    f"🎁 Paket: {hari} HARI - {kuota}x\n"
                    f"⏰ Durasi: {hari} hari\n"
                    f"🎟️ Kuota: {kuota}x Tambah Kota\n"
                    f"📅 Aktif Sampai: {expire.strftime('%d/%m/%Y %H:%M')} WIB\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Silahkan pilih provinsi sekarang!"
                )
                kb_manual_tambah = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🌍 PILIH PROVINSI SEKARANG", callback_data="menu_tambah_kota")],
                    [InlineKeyboardButton("📊 CEK STATUS", callback_data="menu_status")]
                ])
                await context.bot.send_message(chat_id=id_user_int, text=notif, reply_markup=kb_manual_tambah)
            except Exception as e:
                logger.warning(f"notif admin tambah kota fail: {e}")
            context.user_data['mode'] = None
            context.user_data.pop('admin_target_id', None)
            context.user_data.pop('admin_hari', None)
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        return
    if mode == 'admin_tambah_cari_id':
        try:
            import re as re2
            id_user = re2.sub(r'[^0-9]', '', text)
            context.user_data['admin_target_id'] = id_user
            context.user_data['mode'] = 'admin_tambah_cari_hari'
            await update.message.reply_text(f"✅ ID {id_user}\nKirim durasi:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("30 HARI", callback_data="admin_cari_hari_30")]]))
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        return
    if mode == 'admin_tambah_cari_hari':
        try:
            import re as re2
            hari = int(re2.sub(r'[^0-9]', '', text)) or 30
            id_user = context.user_data.get('admin_target_id')
            from datetime import timedelta as _td2
            expire = datetime.now() + _td2(days=hari)
            db.setdefault("langganan_cari", {})
            db["langganan_cari"][id_user] = {"paket": f"cari_{hari}hari", "nama": f"CARI DATA LAIN {hari} HARI", "expire": expire, "hari": hari}
            save_db()
            save_user_package_permanent(id_user, f"cari_{hari}hari", "cari")
            await update.message.reply_text(f"✅ CARI DATA {id_user} {hari} HARI", reply_markup=kb_admin_panel())
            # NOTIF KE USER - SESUAI PAKET CARI YANG DIAKTIFKAN ADMIN + BUTTON
            try:
                id_user_int = int(id_user)
                notif_cari_admin = (
                    f"🔎 PAKET AKTIF - CARI DATA LAIN ✅\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Admin telah mengaktifkan paket CARI untuk kamu!\n\n"
                    f"🎁 Paket: CARI DATA LAIN {hari} HARI\n"
                    f"⏰ Durasi: {hari} hari\n"
                    f"📅 Aktif Sampai: {expire.strftime('%d/%m/%Y %H:%M')} WIB\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Ketik nama kota untuk mulai cari data!"
                )
                kb_manual_cari = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔎 MULAI CARI DATA", callback_data="menu_cari_data")],
                    [InlineKeyboardButton("📊 CEK STATUS", callback_data="menu_status")]
                ])
                await context.bot.send_message(chat_id=id_user_int, text=notif_cari_admin, reply_markup=kb_manual_cari)
            except Exception as e:
                logger.warning(f"notif admin cari fail: {e}")
            context.user_data['mode'] = None
            context.user_data.pop('admin_target_id', None)
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        return
    if mode == 'tambah_admin':
        try:
            import re as re2
            new_id = int(re2.sub(r'[^0-9]', '', text))
            if new_id not in ADMIN_IDS:
                ADMIN_IDS.append(new_id)
                save_db()
            context.user_data['mode'] = None
            await update.message.reply_text(f"✅ Admin {new_id} berhasil ditambah!", reply_markup=kb_main(uid))
        except:
            await update.message.reply_text("❌ ID harus angka")
        return
    if mode == 'hapus_admin':
        try:
            import re as re2
            del_id = int(re2.sub(r'[^0-9]', '', text))
            if del_id in ADMIN_IDS:
                ADMIN_IDS.remove(del_id)
                save_db()
                await update.message.reply_text(f"✅ Admin {del_id} dihapus!", reply_markup=kb_main(uid))
            else:
                await update.message.reply_text(f"❌ ID {del_id} tidak ada")
            context.user_data['mode'] = None
        except:
            await update.message.reply_text("❌ ID harus angka")
        return
    if mode == 'tambah_paket_user':
        try:
            import re as re2
            parts = [x.strip() for x in text.split("|")]
            if len(parts) < 5:
                await update.message.reply_text("❌ Format salah! Harus: ID | KOTA | KECAMATAN | PAKET | HARI\nContoh: 123 | Serang | Ciruas | CARI DATA LAIN | 30")
                return
            id_user_raw, kota, kecamatan, paket_raw, kuota_raw = parts[0], parts[1], parts[2], parts[3], parts[4]
            id_user = re2.sub(r'[^0-9]', '', id_user_raw)
            kuota_hari = int(re2.sub(r'[^0-9]', '', kuota_raw))
            paket_key = paket_raw.lower().replace(" ", "_")
            wilayah = f"{kecamatan}, {kota}".upper()
            db.setdefault("user_info", {})
            db.setdefault("langganan_cari", {})
            if id_user not in db["user_info"]:
                db["user_info"][id_user] = {"nama": f"User {id_user}", "kotas": []}
            if wilayah not in db["user_info"][id_user].get("kotas", []):
                db["user_info"][id_user].setdefault("kotas", []).append(wilayah)
            from datetime import timedelta as td
            expire = (datetime.now() + td(days=kuota_hari)).isoformat()
            if id_user not in db["langganan_cari"]:
                db["langganan_cari"][id_user] = []
            db["langganan_cari"][id_user].append({
                "paket": paket_key, "nama": paket_raw, "kota": kota, "kecamatan": kecamatan,
                "wilayah": wilayah, "kuota_hari": kuota_hari, "expire": expire,
                "created_by": uid, "created_at": datetime.now().isoformat()
            })
            save_db()
            context.user_data['mode'] = None
            await update.message.reply_text(
                f"✅ PAKET BERHASIL DITAMBAH\n🆔 {id_user}\n📍 {kota} - {kecamatan}\n📦 {paket_raw}\n⏳ {kuota_hari} hari\n📅 {expire[:10]}",
                reply_markup=kb_main(uid)
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Gagal: {e}")
        return
    if mode == 'hapus_paket_user':
        import re as re2
        id_user = re2.sub(r'[^0-9]', '', text)
        if id_user in db.get("langganan_cari", {}):
            del db["langganan_cari"][id_user]
            save_db()
            await update.message.reply_text(f"✅ Paket user {id_user} dihapus!", reply_markup=kb_main(uid))
        else:
            await update.message.reply_text(f"❌ User {id_user} tidak punya paket", reply_markup=kb_main(uid))
        context.user_data['mode'] = None
        return


    
    if mode == 'cari_user' or raw_up.startswith("CARI USER") or raw_up.startswith("CARI @") or (norm_low.startswith("@") and len(norm_low) > 2):
        # Jika mode cari_user aktif, atau user ketik CARI USER xxx, atau langsung @username
        query = ""
        if mode == 'cari_user':
            query = text.strip()
        elif "CARI USER" in raw_up:
            query = text.upper().replace("CARI USER","").strip()
            query = text.replace("cari user","").replace("CARI USER","").strip()
        elif raw_up.startswith("CARI @"):
            query = text.replace("CARI","").strip()
        else:
            query = text.strip()

        # Bersihkan @
        query_clean = query.replace("@","").lower().strip()
        query_clean_id = "".join(filter(str.isdigit, query))
        
        if not query_clean and not query_clean_id:
            await update.message.reply_text("❌ Masukkan username / ID / nama yang valid", reply_markup=kb_main(uid))
            return

        user_list = db.get("user_info", {})
        results = []
        for uid_str, info in user_list.items():
            nama = info.get("nama","-").lower()
            username = info.get("username","-").lower().replace("@","")
            # Match: ID exact, username contains, nama contains, fuzzy
            if query_clean_id and query_clean_id == uid_str:
                results.append((uid_str, info, "ID EXACT"))
            elif query_clean and query_clean in username:
                results.append((uid_str, info, "USERNAME"))
            elif query_clean and query_clean in nama:
                results.append((uid_str, info, "NAMA"))
            elif query_clean and username and (query_clean in username or username in query_clean):
                results.append((uid_str, info, "USERNAME FUZZY"))
        
        # Jika tidak ada, coba cari yang mirip (contains)
        if not results and query_clean:
            for uid_str, info in user_list.items():
                username = info.get("username","-").lower()
                if query_clean[:3] in username and len(query_clean) >=3:
                    results.append((uid_str, info, "MIRIP"))

        if not results:
            await update.message.reply_text(
                f"❌ Tidak ditemukan user dengan keyword `{query}`\n\nTotal user di database: {len(user_list)}",
                parse_mode='Markdown', reply_markup=kb_admin_panel()
            )
            if mode == 'cari_user':
                context.user_data['mode'] = None
            return

        # Format hasil keren
        out = f"🔍 HASIL PENCARIAN: `{query}`\n"
        out += f"📊 Ditemukan: {len(results)} user\n"
        out += f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        for i, (uid_str, info, match_type) in enumerate(results[:20], 1):
            nama = info.get("nama","-")
            username = info.get("username","-")
            if username != "-" and not username.startswith("@"):
                username_disp = f"@{username}"
            else:
                username_disp = username
            kotas = info.get("kotas", [])
            kota_str = f"{len(kotas)} kota" if kotas else "0 kota"
            
            # Cek paket aktif
            paket_aktif = ""
            if uid_str in db.get("langganan", {}):
                paket_aktif = "✅ Ada paket TAMBAH"
            if uid_str in db.get("langganan_cari", {}):
                paket_aktif += " | 🔎 Ada paket CARI" if paket_aktif else "🔎 Ada paket CARI"
            if not paket_aktif:
                paket_aktif = "❌ Belum ada paket"
            
            out += f"{i}. *{match_type}*\n"
            out += f"   🆔 ID: `{uid_str}`\n"
            out += f"   👤 Nama: {nama}\n"
            out += f"   🔗 Username: {username_disp}\n"
            out += f"   🌍 {kota_str}\n"
            out += f"   🔗 Chat: [Klik untuk chat](tg://user?id={uid_str})\n\n"

        if len(results) > 20:
            out += f"_... dan {len(results)-20} user lainnya, cek file txt untuk lengkap_"

        await update.message.reply_text(out, parse_mode='Markdown', reply_markup=kb_admin_panel())

        # Kirim file detail kalau banyak
        if len(results) > 1:
            try:
                import io
                txt = f"HASIL CARI: {query}\nDitemukan: {len(results)} user\n\n"
                for uid_str, info, mtype in results:
                    txt += f"{uid_str} | {info.get('username','-')} | {info.get('nama','-')} | {mtype}\n"
                bio = io.BytesIO(txt.encode('utf-8'))
                bio.name = f"hasil_cari_{query_clean[:10]}.txt"
                await context.bot.send_document(chat_id=uid, document=bio, filename=bio.name)
            except:
                pass

        if mode == 'cari_user':
            context.user_data['mode'] = None
        return


    # === ORIGINAL CODE LANJUTAN ===

    # Handle /batal command untuk cancel cari data
    if text.lower() in ["/batal", "batal"]:
        if context.user_data.get("awaiting_cari_data") or context.user_data.get("awaiting_cari_lainnya"):
            context.user_data.pop("awaiting_cari_data", None)
            context.user_data.pop("awaiting_cari_lainnya", None)
            await update.message.reply_text("❌ Pencarian dibatalkan", reply_markup=kb_main(uid))
            return


    # === TAMBAH NO PANTAUAN ===
    # Mendukung nomor biasa setelah tombol admin, /ADD, dan /ADDS.
    is_add_command = text.upper().startswith("/ADDS") or text.upper().startswith("/ADD ")
    is_awaiting_add = context.user_data.get("awaiting_tambah_pantauan", False)

    if is_add_command or is_awaiting_add:
        if not is_admin(uid):
            await update.message.reply_text("❌ Hanya admin!", reply_markup=kb_back_main_only())
            return

        context.user_data.pop("awaiting_tambah_pantauan", None)

        raw = text
        if raw.upper().startswith("/ADDS"):
            raw = raw[5:].strip()
        elif raw.upper().startswith("/ADD "):
            raw = raw[4:].strip()

        tokens = re.split(r'[\n,;\s]+', raw)
        input_numbers = []
        for token in tokens:
            clean = normalize_number(token)
            if clean and clean not in input_numbers:
                input_numbers.append(clean)

        if not input_numbers:
            await update.message.reply_text(
                "❌ Nomor tidak valid. Kirim nomor seperti:\n081223455666\n089737388383",
                reply_markup=kb_back_main_only()
            )
            return

        existing = set()
        for n in get_all_pantauan():
            clean = normalize_number(n)
            if clean:
                existing.add(clean)

        db.setdefault("pantauan", [])
        db.setdefault("blacklist", [])

        added = 0
        duplicate = 0
        for clean in input_numbers:
            if clean in existing:
                duplicate += 1
                continue

            add_pantauan_to_supabase(clean)
            if clean not in db["pantauan"]:
                db["pantauan"].append(clean)
            if clean not in db["blacklist"]:
                db["blacklist"].append(clean)
            existing.add(clean)
            added += 1

        db["pantauan"] = list(dict.fromkeys(db["pantauan"]))
        db["blacklist"] = list(dict.fromkeys(db["blacklist"]))
        save_pantauan_backup(list(existing))
        save_db()

        # Ambil ulang daftar terbaru lalu tampilkan SEMUA nomor.
        all_numbers = []
        for n in get_all_pantauan():
            clean = normalize_number(n)
            if clean and clean not in all_numbers:
                all_numbers.append(clean)

        if not all_numbers:
            all_numbers = list(existing)

        number_lines = "\n".join(
            f"{i}. {number}" for i, number in enumerate(all_numbers, 1)
        )
        # Hindari pesan Telegram terlalu panjang.
        if len(number_lines) > 3500:
            number_lines = number_lines[:3500] + "\n\n... daftar masih berlanjut."

        result = (
            f"✅ BERHASIL MENAMBAHKAN NO PANTAUAN\n\n"
            f"➕ Nomor baru: {added}\n"
            f"♻️ Sudah ada: {duplicate}\n"
            f"📊 Total NO PANTAUAN: {len(all_numbers)}\n\n"
            f"📱 DAFTAR SEMUA NO PANTAUAN\n"
            f"━━━━━━━━━━━━━━━━━━\n{number_lines}"
        )
        await update.message.reply_text(result, reply_markup=kb_admin_panel())
        return

    # DUMMY to keep original flow - will be unreachable but keep structure
    if False and text.upper().startswith("/ADDS") or text.upper().startswith("/ADD "):
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
        await update.message.reply_text(f"✅ Berhasil tambah {added} nomor ke BLACKLIST (dari {len(all_nums)} input)\n📊 Total sekarang: {len(db.get('blacklist',[]))} nomor\n\nOtomatis masuk ke NO PANTAUAN ✅", reply_markup=kb_admin_panel())
        return

    if context.user_data.get("awaiting_cari_data"):
        context.user_data["awaiting_cari_data"]=False
        query=text.strip().upper()
        if query=="/BATAL" or query=="BATAL":
            await update.message.reply_text("❌ Pencarian dibatalkan", reply_markup=kb_back_main_only()); return
        # ADMIN unlimited
        if is_admin(uid):
            pass
        elif not is_active_cari(uid):
            await update.message.reply_text("🔒 FITUR TERKUNCI 🔒\nSilahkan hubungi admin!", reply_markup=kb_back_main_only()); return
        history=load_wa_history()
        hasil=[]
        for h in reversed(history):
            txt_upper = (h.get("text","") + " " + h.get("group","")).upper()
            if query in txt_upper:
                hasil.append(h)
        # TAMPILKAN SEMUA - tanpa batas 3 per nomor, tanpa batas 50
        if not hasil:
            await update.message.reply_text(f"❌ Data '{text}' tidak ditemukan\nCoba keyword lain!", reply_markup=kb_cari_data_lain())
        else:
            # Hitung pengirim unik
            unique_numbers = len(set([h.get("number","") for h in hasil]))
            await update.message.reply_text(f"🔎 HASIL: {text}\n📊 Ditemukan {len(hasil)} pesan dari {unique_numbers} pengirim untuk kota {text.upper()}\n⏳ Akan dikirim satu per satu (jeda 0.5 detik)...", reply_markup=kb_back_main_only())
            import asyncio
            for h in hasil:
                nomor=h.get("number","")
                clean=''.join(filter(str.isdigit,nomor))
                if clean.startswith("0"): clean="62"+clean[1:]
                msg=f"""Grup : {h.get('group')}
Pengirim : {h.get('sender')}
No WhatsApp : {h.get('number')}
━━━━━━━━━━━━━━━━━━━
{h.get('text')}
━━━━━━━━━━━━━━━━━━━
⚠️ Perhatian : untuk tetap waspada dan berhati-hati disarankan untuk rekber, terimakasih.sumber: https://t.me/Aakiwkiw_bot 🙏"""
                try:
                    if clean:
                        kb=kb_hasil_cari(clean)
                        await context.bot.send_message(chat_id=uid, text=msg, reply_markup=kb)
                    else:
                        await context.bot.send_message(chat_id=uid, text=msg, reply_markup=kb_hasil_cari(None))
                except Exception as e:
                    logger.error(f"send hasil cari error: {e}")
                await asyncio.sleep(0.5)
            await update.message.reply_text(f"✅ Selesai menampilkan {len(hasil)} data untuk {text.upper()}", reply_markup=kb_hasil_cari_selesai())
        return

    if context.user_data.get("awaiting_cek_pantauan"):

        context.user_data["awaiting_cek_pantauan"]=False
        clean=''.join(filter(str.isdigit,text))
        if clean.startswith("62"): clean="0"+clean[2:]
        if len(clean)>=8:
            if clean in get_all_pantauan():
                await update.message.reply_text(f"🚫 Nomor {clean} ADA di BLACKLIST\n📊 Total: {len(db.get('blacklist',[]))} nomor", reply_markup=kb_blacklist_menu())
            else:
                await update.message.reply_text(f"✅ Nomor {clean} BELUM ADA di blacklist\n📊 Total: {len(db.get('blacklist',[]))} nomor", reply_markup=kb_blacklist_menu())
        else:
            await update.message.reply_text("❌ Format salah\nContoh: 083123456789", reply_markup=kb_back_main_only())
        return

    if context.user_data.get("awaiting_tambah_pantauan"):
        context.user_data["awaiting_tambah_pantauan"]=False
        raw=text
        if raw.upper().startswith("/ADDS"):
            raw=raw[5:]
        elif raw.upper().startswith("/ADD"):
            raw=raw[4:]
        nums = re.split(r'[\n,\s]+', raw)
        added=0
        for n in nums:
            clean=''.join(filter(str.isdigit,n))
            if not clean: continue
            if clean.startswith("62"): clean="0"+clean[2:]
            if len(clean)>=8:
                existing = get_all_pantauan()
                if clean not in existing:
                    # Simpan ke Supabase (jika gagal tetap lanjut lokal)
                    add_pantauan_to_supabase(clean)
                    if "pantauan" not in db: db["pantauan"]=[]
                    if "blacklist" not in db: db["blacklist"]=[]
                    if clean not in db["pantauan"]:
                        db["pantauan"].append(clean)
                    if clean not in db["blacklist"]:
                        db["blacklist"].append(clean)
                    added+=1
        save_db()
        total = len(get_all_pantauan())
        await update.message.reply_text(f"✅ Berhasil tambah {added} nomor ke PANTAUAN (bulk)\n📊 Total sekarang: {total} nomor\n\nOtomatis masuk ke menu NO PANTAUAN ✅", reply_markup=kb_admin_panel())
        return

    if context.user_data.get("awaiting_hapus_pantauan"):
        context.user_data["awaiting_hapus_pantauan"]=False
        nums = re.split(r'[\n,\s,;]+', text)
        removed=0; removed_list=[]
        for n in nums:
            clean=''.join(filter(str.isdigit,n))
            if not clean: continue
            if clean.startswith("62"): clean="0"+clean[2:]
            if len(clean)<8: continue
            if clean in get_all_pantauan():
                try: remove_pantauan_from_supabase(clean)
                except: pass
                if clean in db.get("blacklist",[]):
                    try: db["blacklist"].remove(clean)
                    except: pass
                if clean in db.get("pantauan",[]):
                    try: db["pantauan"].remove(clean)
                    except: pass
                try:
                    backup=load_pantauan_backup()
                    if clean in backup:
                        backup.remove(clean); save_pantauan_backup(backup)
                except: pass
                removed+=1; removed_list.append(clean)
        save_db()
        total_now=len(get_all_pantauan())
        txt_removed=", ".join(removed_list[:10])
        if len(removed_list)>10: txt_removed+=f" +{len(removed_list)-10} lainnya"
        await update.message.reply_text(f"✅ Berhasil hapus {removed} nomor:\n{txt_removed}\n\n📊 Total sekarang: {total_now} nomor\n✅ Terhapus dari menu NO PANTAUAN juga!", reply_markup=kb_admin_panel())
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
        # FIX: Tampilkan SEMUA pengirim tanpa batas
        if not hasil:
            await update.message.reply_text(f"❌ Data '{text}' tidak ditemukan di history WA\nCoba keyword lain bos!", reply_markup=kb_cari_data_lain())
        else:
            await update.message.reply_text(f"🔎 HASIL: {text}\n📊 Ditemukan {len(hasil)} pengirim untuk kota {text.upper()}\n⏳ Akan dikirim satu per satu (jeda 0.5 detik)...", reply_markup=kb_back_main_only())
            import asyncio
            for h in hasil:
                nomor=h.get("number","")
                clean=''.join(filter(str.isdigit,nomor))
                if clean.startswith("0"): clean="62"+clean[1:]
                msg=f"""Grup : {h.get('group')}
Pengirim : {h.get('sender')}
No WhatsApp : {h.get('number')}
━━━━━━━━━━━━━━━━━━━
{h.get('text')}
━━━━━━━━━━━━━━━━━━━
⚠️ Perhatian : untuk tetap waspada dan berhati-hati disarankan untuk rekber, terimakasih.sumber: https://t.me/Aakiwkiw_bot 🙏"""
                try:
                    if clean:
                        kb=kb_hasil_cari(clean)
                        await context.bot.send_message(chat_id=uid, text=msg, reply_markup=kb)
                    else:
                        await context.bot.send_message(chat_id=uid, text=msg, reply_markup=kb_hasil_cari(None))
                except Exception as e:
                    logger.error(f"send hasil cari lainnya error: {e}")
                await asyncio.sleep(0.5)
            await update.message.reply_text(f"✅ Selesai menampilkan {len(hasil)} data untuk {text.upper()}", reply_markup=kb_hasil_cari_selesai())
        context.user_data["awaiting_cari_lainnya"]=False
        return

    await update.message.reply_text("ℹ️ Gunakan menu di bawah:", reply_markup=kb_main(uid))



async def foto_handler(update,context):
    uid=update.effective_user.id
    if not update.message.photo: return
    # FIX: baca paket sesuai yang dipilih user - CARI DICEK DULUAN BIAR GAK KETUKER
    paket_key = None
    paket_type = None
    if "pending_paket_cari" in context.user_data:
        paket_key = context.user_data.get("pending_paket_cari")
        paket_type = "cari"
    elif "pending_paket_tambah" in context.user_data:
        paket_key = context.user_data.get("pending_paket_tambah")
        paket_type = "tambah"
    else:
        paket_key = context.user_data.get("paket_pilih","1minggu")
        paket_type = context.user_data.get("paket_type","tambah")
    # Pastikan paket_type konsisten
    if not paket_type:
        paket_type = "cari" if "cari" in str(paket_key).lower() or context.user_data.get("paket_type")=="cari" else "tambah"
    logger.info(f"📸 Foto handler - paket_key={paket_key}, paket_type={paket_type}, user_data={context.user_data}")
    if not paket_key: paket_key="1minggu"
    if not paket_type: paket_type="tambah"
    if paket_type=="tambah": p=PAKET_TAMBAH.get(paket_key,PAKET_TAMBAH["1minggu"])
    else: p=PAKET_CARI.get(paket_key,PAKET_CARI["1minggu"])
    file_id=update.message.photo[-1].file_id
    
    # Ambil kota dari caption foto user
    caption_user = update.message.caption or ""
    kota_dicetak = "Tidak ada kota"
    if caption_user:
         # Ambil kata pertama sebagai nama kota
        kota_dicetak = caption_user.strip().split()[0]
    
    # FIX NOTIF ADMIN FINAL - BEDAIN JELAS
    if paket_type == "cari":
        label_admin = "CARI DATA LAIN"
    else:
        label_admin = "TAMBAH KOTA"
    caption_admin = f"💳 BUKTI TOP UP {label_admin} MASUK\n🆔 ID: {uid}\n🎁 Paket: {p['nama']} - Rp {p['harga']:,}\n📍 Kota: {kota_dicetak}\n🔖 Tipe: {paket_type.upper()}"
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
        if clean in get_all_pantauan():
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



async def cmd_cek_pantauan_debug(update, context):
    uid=update.effective_user.id
    if not is_admin(uid):
        return
    sup = load_pantauan_from_supabase()
    backup = load_pantauan_backup()
    mem = db.get("pantauan", [])
    bl = db.get("blacklist", [])
    all_p = get_all_pantauan()
    import os as _os
    exists1 = _os.path.exists(PANTAUAN_FILE)
    exists2 = _os.path.exists(PANTAUAN_FILE_PERSISTENT)
    txt = f"🔧 DEBUG PANTAUAN V5 FINAL\nSupabase: {len(sup) if sup is not None else 'None/Error'} \nBackup: {len(backup)} \nMemory: {len(mem)} blacklist {len(bl)} gabungan {len(all_p)}"
    await update.message.reply_text(txt, reply_markup=kb_main(uid))


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
application.add_handler(CommandHandler("cekpantauan", cmd_cek_pantauan_debug))
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
    return "Bot Active - Webhook Mode - OK - FINAL_BENER_V2"

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
