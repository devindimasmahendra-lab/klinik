#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# klinik.py - Single-file Flask app untuk sistem klinik USG 4D
# Jalankan: python klinik.py
#
# ======================================================
# CHANGELOG / BUG FIXES (upgraded):
# 1. [CRITICAL] SyntaxError: "init_db():" diperbaiki menjadi "def init_db():"
# 2. [CRITICAL] @login_required (tidak terdefinisi) diganti dengan
#    @role_required('superadmin','admin','dokter','pasien') di:
#    - Route /appointments
#    - Route /api/dashboard-stats
# 3. [CRITICAL] Urutan kode dibenahi: semua helper function
#    (init_db, current_user, log_action, role_required, patient_allowed,
#    get_patient, render_page) dipindah SEBELUM route-route yang
#    membutuhkannya, sehingga tidak ada NameError saat runtime.
# 4. [UPGRADE] Route API pasien (/api/patient_search, /api/patient_by_id,
#    /api/patient_visits) diposisikan setelah helper functions terdefinisi.
# ======================================================


# klinik.py - Single-file Flask app untuk sistem klinik USG 4D
# Jalankan: python klinik.py

import os
import io
import uuid
import base64
import shutil
import sqlite3
import re
import logging
from datetime import datetime, date, timedelta
from functools import wraps
from typing import Optional

from flask import Flask, request, redirect, url_for, render_template_string, session, flash, abort, send_from_directory, send_file, g
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

try:
    import qrcode
    QR_OK = True
except Exception:
    qrcode = None
    QR_OK = False

APP_NAME = 'Klinik USG 4D'
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Konfigurasi Penyimpanan untuk Render (Persistent Disk)
DATA_DIR = os.environ.get('DATA_DIR', BASE_DIR)
if os.environ.get('RENDER'):
    # Gunakan /var/lib/data hanya jika disk sudah dipasang di dashboard Render.
    # Jika belum ada disk terpasang, gunakan folder lokal agar tidak muncul PermissionError.
    render_disk = '/var/lib/data'
    if os.path.exists(render_disk):
        DATA_DIR = render_disk
    else:
        DATA_DIR = os.path.join(BASE_DIR, 'instance_data')

os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, 'usg4d_klinik.db')
UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')
ALLOWED = {'jpg', 'jpeg', 'png', 'pdf', 'mp4', 'mov'}
MAX_MB = 32
DEFAULT_PORT = 5006

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-secret')
app.jinja_env.add_extension('jinja2.ext.do')
app.config['UPLOAD_FOLDER'] = UPLOAD_DIR
app.config['MAX_CONTENT_LENGTH'] = MAX_MB * 1024 * 1024
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)

# Setup Logging for long-term stability
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]',
    handlers=[logging.FileHandler(os.path.join(BASE_DIR, "klinik.log")), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


def hitung_risiko_kehamilan(td_sistolik, td_diastolik, djj):
    def pick_int(val, default):
        if val is None: return default
        nums = re.findall(r'\d+', str(val))
        return int(nums[0]) if nums else default

    try:
        # More robust parsing using regex to extract numbers from strings like "120 mmHg"
        sys = pick_int(td_sistolik, 0)
        dia = pick_int(td_diastolik, 0)
        d = pick_int(djj, 0)
    except Exception as e:
        logger.error(f"Error parsing risk data: {e}")
        return {'status': 'Hijau', 'label': 'Risiko Rendah (Data Tidak Lengkap)', 'color': '#22c55e', 'bg': 'rgba(34,197,94,0.15)'}

    if sys >= 160 or dia >= 110 or (d > 0 and (d < 100 or d > 170)):
        return {'status': 'Merah', 'label': 'Risiko Tinggi (Peringatan Dini)', 'color': '#ef4444', 'bg': 'rgba(239,68,68,0.15)'}
    elif sys >= 140 or dia >= 90 or (0 < d < 110) or d > 160:
        return {'status': 'Kuning', 'label': 'Risiko Sedang (Pantau Lanjut)', 'color': '#f59e0b', 'bg': 'rgba(245,158,11,0.15)'}
    else:
        return {'status': 'Hijau', 'label': 'Risiko Rendah (Normal)', 'color': '#22c55e', 'bg': 'rgba(34,197,94,0.15)'}

def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-4000")  # Gunakan 4MB cache untuk performa query besar
    conn.execute("PRAGMA temp_store=MEMORY") # Simpan tabel temporer di RAM
    return conn

def get_db():
    if 'db' not in g:
        g.db = db()
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db_conn = g.pop('db', None)
    if db_conn is not None:
        db_conn.close()

# [FIX] Pastikan DB ter-inisialisasi untuk semua environment (termasuk Gunicorn/Render)
_db_initialized = False

def auto_seed_if_empty():
    """Jalankan seed otomatis jika tabel patients kosong (untuk Render deploy)."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM patients")
        count = cur.fetchone()[0]
        conn.close()
        if count == 0:
            logger.info("[AUTO-SEED] Database kosong, menjalankan seed data...")
            # Import dan jalankan seed
            import importlib.util
            seed_path = os.path.join(BASE_DIR, 'seed_klinik.py')
            if os.path.exists(seed_path):
                spec = importlib.util.spec_from_file_location("seed_klinik", seed_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.seed()
                logger.info("[AUTO-SEED] Seed data berhasil dimasukkan.")
            else:
                logger.warning(f"[AUTO-SEED] File {seed_path} tidak ditemukan.")
    except Exception as e:
        logger.error(f"[AUTO-SEED] Error: {e}")

@app.before_request
def ensure_db():
    global _db_initialized
    if not _db_initialized:
        init_db()
        auto_seed_if_empty()
        _db_initialized = True

def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def get_milestone_info(usia_kehamilan):
    if not usia_kehamilan: return None
    nums = re.findall(r'\d+', str(usia_kehamilan))
    if not nums: return None
    week = int(nums[0])
    milestones = {
        8: "Bayi Bunda sekarang seukuran buah Beri! Ekor kecilnya sudah mulai hilang.",
        12: "Bayi Bunda seukuran Jeruk Nipis. Ia sudah mulai bisa menggerakkan jari-jarinya.",
        16: "Bayi Bunda seukuran Alpukat. Jantungnya kini memompa sekitar 25 liter darah setiap hari.",
        20: "Bayi Bunda seukuran Pisang. Ia mulai bisa menelan cairan ketuban.",
        24: "Bayi Bunda seukuran Jagung. Paru-parunya mulai berkembang untuk bersiap bernapas.",
        28: "Bayi Bunda seukuran Terong. Ia sudah bisa membuka mata dan berkedip!",
        32: "Bayi Bunda seukuran Labu Kuning. Tulang-tulangnya sudah mulai mengeras.",
        36: "Bayi Bunda seukuran Pepaya. Ia sudah mulai turun ke arah panggul.",
        40: "Bayi Bunda seukuran Semangka. Selamat! Si kecil sudah siap menyapa dunia."
    }
    for w in sorted(milestones.keys(), reverse=True):
        if week >= w: return milestones[w]
    return "Si kecil terus berkembang dengan sehat di dalam perut Bunda."

def hitung_estimasi_tunggu(patient_id):
    conn = get_db()
    td_local = date.today().isoformat()
    # Hitung rata-rata durasi pemeriksaan hari ini
    durasi_row = conn.execute("""
        SELECT AVG(unixepoch(updated_at) - unixepoch(created_at)) / 60 as avg_min 
        FROM soap_records 
        WHERE date(created_at) = ?
    """, (td_local,)).fetchone()
    avg_min = durasi_row[0] if (durasi_row and durasi_row[0]) else 15
    if avg_min < 5: avg_min = 15
    # Hitung jumlah orang di depan pasien ini dalam antrian
    patient = conn.execute("SELECT created_at FROM patients WHERE id=?", (patient_id,)).fetchone()
    if not patient: return 0
    urutan = conn.execute("""
        SELECT COUNT(*) FROM patients 
        WHERE status_antrian = 'menunggu' AND created_at < ? AND deleted = 0
    """, (patient['created_at'],)).fetchone()[0]
    return int(urutan * avg_min)

def today():
    return date.today().strftime('%Y-%m-%d')


def fmt_dt(v: Optional[str]):
    if not v:
        return '-'
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(v[:19], fmt).strftime('%d-%m-%Y %H:%M')
        except Exception:
            pass
    return v


def rupiah(val):
    try:
        return 'Rp{:,.0f}'.format(float(val)).replace(',', '.')
    except Exception:
        return 'Rp0'


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED


def file_badge(ext):
    ext = (ext or '').lower()
    if ext in {'jpg', 'jpeg', 'png'}:
        return '🖼️ Gambar'
    if ext == 'pdf':
        return '📄 PDF'
    if ext in {'mp4', 'mov'}:
        return '🎞️ Video'
    return ext.upper()


def rm_auto():
    return 'RM' + datetime.now().strftime('%y%m%d%H%M%S') + str(uuid.uuid4().hex[:4]).upper()


def get_port():
    try:
        # Render menggunakan variabel lingkungan 'PORT'
        return int(os.environ.get('PORT', os.environ.get('KLINIK_PORT', DEFAULT_PORT)))
    except ValueError:
        return DEFAULT_PORT


def token_auto():
    return uuid.uuid4().hex + uuid.uuid4().hex[:8]


def qr_data_uri(text):
    if not QR_OK:
        return None
    try:
        img = qrcode.make(text)
        bio = io.BytesIO()
        img.save(bio, format='PNG')
        return 'data:image/png;base64,' + base64.b64encode(bio.getvalue()).decode('utf-8')
    except Exception:
        return None





def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL, 
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('superadmin','admin','dokter','pasien')),
            full_name TEXT,
            patient_id INTEGER,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nama_pasien TEXT NOT NULL,
            nomor_rekam_medis TEXT UNIQUE NOT NULL,
            nik TEXT,
            tanggal_lahir TEXT,
            umur TEXT,
            alamat TEXT,
            nomor_hp TEXT,
            golongan_darah TEXT,
            status_perkawinan TEXT,
            pekerjaan TEXT,
            nama_keluarga TEXT,
            jenis_layanan TEXT,
            dokter_tujuan TEXT,
            prioritas TEXT NOT NULL DEFAULT 'Non-urgent',
            status_antrian TEXT NOT NULL DEFAULT 'menunggu',
            access_token TEXT UNIQUE NOT NULL,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS soap_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            doctor_id INTEGER,
            subjective TEXT,
            objective TEXT,
            assessment TEXT,
            plan TEXT,
            kode_icd10 TEXT,
            td_sistolik TEXT,
            td_diastolik TEXT,
            nadi TEXT,
            suhu TEXT,
            rr TEXT,
            informed_consent INTEGER NOT NULL DEFAULT 0,
            usia_kehamilan TEXT,
            detak_jantung_janin TEXT,
            posisi_janin TEXT,
            estimasi_berat_janin TEXT,
            catatan_dokter TEXT,
            rekomendasi_kontrol_ulang TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE,
            FOREIGN KEY (doctor_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            uploader_id INTEGER,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            file_ext TEXT NOT NULL,
            file_size INTEGER NOT NULL DEFAULT 0,
            mime_type TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE,
            FOREIGN KEY (uploader_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS billing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            status_bayar TEXT NOT NULL DEFAULT 'belum_lunas',
            notes TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS soap_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            subjective TEXT,
            objective TEXT,
            assessment TEXT,
            plan TEXT,
            created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
        );
        
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id INTEGER NOT NULL,
            doctor_name TEXT,
            appointment_date TEXT NOT NULL,
            complaint TEXT,
            status TEXT NOT NULL DEFAULT 'terjadwal',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS audit_logs (

            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            action TEXT NOT NULL,
            details TEXT,
            ip_address TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        );
    ''')
    # Create indexes for performance
    cur.execute("CREATE INDEX IF NOT EXISTS idx_patients_rm ON patients(nomor_rekam_medis)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_patients_status ON patients(status_antrian)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_patients_deleted ON patients(deleted)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_patients_created ON patients(created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_soap_patient ON soap_records(patient_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_soap_created ON soap_records(created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_billing_patient ON billing(patient_id)")

    cur.execute('''
        CREATE TABLE IF NOT EXISTS master_options (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT UNIQUE,
            options_text TEXT
        )
    ''')

    default_options = {
        'golongan_darah': 'A,B,AB,O',
        'pekerjaan': 'Karyawan,Wiraswasta,PNS,Pelajar,Mahasiswa,Ibu Rumah Tangga',
        'jenis_layanan': 'Umum,BPJS,Asuransi'
    }

    for category, options_text in default_options.items():
        cur.execute(
            'INSERT OR IGNORE INTO master_options(category, options_text) VALUES(?,?)',
            (category, options_text)
        )

    defaults = [
        ('superadmin', 'admin123', 'superadmin', 'Super Admin'),
        ('admin', 'admin123', 'admin', 'Admin Klinik'),
        ('dokter', 'dokter123', 'dokter', 'Dokter USG'),
    ]
    for u, p, r, n in defaults:
        cur.execute('SELECT id FROM users WHERE username=?', (u,))
        if not cur.fetchone():
            cur.execute('INSERT INTO users (username,password_hash,role,full_name,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                        (u, generate_password_hash(p), r, n, now(), now()))
    cur.execute('SELECT COUNT(*) FROM soap_templates')
    if cur.fetchone()[0] == 0:
        cur.executemany('INSERT INTO soap_templates (title,subjective,objective,assessment,plan,created_at) VALUES (?,?,?,?,?,?)', [
            ('Kontrol normal', 'Pasien datang kontrol rutin.', 'Keadaan umum baik, hasil USG baik.', 'Kehamilan sesuai usia kehamilan.', 'Lanjut vitamin dan kontrol ulang sesuai jadwal.', now()),
            ('Keluhan mual', 'Pasien mual terutama pagi hari.', 'Keadaan umum cukup, evaluasi hidrasi.', 'Keluhan kehamilan trimester awal.', 'Edukasi pola makan dan kontrol ulang.', now()),
            ('SOP USG Kehamilan', 'Keluhan utama, HPHT/usia kehamilan, riwayat kehamilan, dan keluhan penyerta dicatat.', 'Kesadaran/keadaan umum, tanda vital bila ada, temuan USG: janin, DJJ, posisi, plasenta, air ketuban, dan estimasi berat janin.', 'Ringkasan kondisi ibu dan janin sesuai hasil anamnesis serta pemeriksaan USG.', 'Edukasi hasil pemeriksaan, tanda bahaya, terapi/anjuran, dan jadwal kontrol ulang.', now()),
        ])
    # Migrasi tabel yang sudah ada — tambah kolom baru jika belum ada
    for col, typ in [('prioritas','TEXT NOT NULL DEFAULT \'Non-urgent\''), ('kode_icd10','TEXT'), ('td_sistolik','TEXT'), ('td_diastolik','TEXT'), ('nadi','TEXT'), ('suhu','TEXT'), ('rr','TEXT'), ('informed_consent','INTEGER NOT NULL DEFAULT 0')]:
        tbl = 'patients' if col == 'prioritas' else 'soap_records'
        try:
            cur.execute('ALTER TABLE {} ADD COLUMN {}'.format(tbl, col) + ' ' + typ)
        except sqlite3.OperationalError:
            pass  # kolom sudah ada
    # Migrasi kolom antropometri untuk tabel patients
    for col, typ in [('tinggi_badan', 'REAL'), ('berat_badan', 'REAL'), ('lingkar_perut', 'REAL'), ('bmi', 'REAL')]:
        try:
            cur.execute('ALTER TABLE patients ADD COLUMN {} {}'.format(col, typ))
        except sqlite3.OperationalError:
            pass  # kolom sudah ada
    # Migrasi kolom soft-delete
    for col, typ in [('deleted', 'INTEGER NOT NULL DEFAULT 0'), ('deleted_at', 'TEXT'), ('deleted_by', 'INTEGER'), ('restored_at', 'TEXT')]:
        try:
            cur.execute('ALTER TABLE patients ADD COLUMN {} {}'.format(col, typ))
        except sqlite3.OperationalError:
            pass  # kolom sudah ada
    # Migrasi kolom keluarga — relasi ibu & anak
    for col, typ in [('keluarga_id', 'INTEGER'), ('hubungan', 'TEXT')]:
        try:
            cur.execute('ALTER TABLE patients ADD COLUMN {} {}'.format(col, typ))
        except sqlite3.OperationalError:
            pass  # kolom sudah ada
    conn.commit()
    conn.close()


def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE id=? AND active=1', (uid,))
    row = cur.fetchone()
    return row


def log_action(action, details=''):
    user = current_user()
    uid = user['id'] if user else None
    uname = user['username'] if user else 'guest'
    conn = get_db(); cur = conn.cursor()
    cur.execute('INSERT INTO audit_logs (user_id,username,action,details,ip_address,created_at) VALUES (?,?,?,?,?,?)',
                (uid, uname, action, details[:2000], request.headers.get('X-Forwarded-For', request.remote_addr or '-'), now()))
    conn.commit()


def role_required(*roles):
    def dec(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                flash('Silakan login terlebih dahulu.', 'warning')
                return redirect(url_for('login', next=request.path))
            if roles and user['role'] not in roles:
                flash('Akses ditolak.', 'danger')
                return redirect(url_for('dashboard'))
            return fn(*args, **kwargs)
        return wrapper
    return dec


def patient_allowed(patient):
    user = current_user()
    if not user:
        return False
    if user['role'] in ('superadmin', 'admin', 'dokter'):
        return True
    return user['role'] == 'pasien' and user['patient_id'] == patient['id']


def get_patient(pid):
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT * FROM patients WHERE id=?', (pid,))
    row = cur.fetchone()
    return row


@app.route('/api/patient_search')
@role_required('superadmin', 'admin', 'dokter')
def api_patient_search():
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return {'results': []}
    conn = get_db(); cur = conn.cursor()
    like = '%' + q + '%'
    cur.execute("SELECT id,nama_pasien,nomor_rekam_medis,nik,tanggal_lahir,umur,alamat,nomor_hp,golongan_darah,status_perkawinan,pekerjaan,nama_keluarga,jenis_layanan,dokter_tujuan,status_antrian,created_at FROM patients WHERE (nama_pasien LIKE ? OR nomor_rekam_medis LIKE ? OR nik LIKE ? OR nomor_hp LIKE ?) AND COALESCE(deleted, 0) = 0 ORDER BY nama_pasien LIMIT 20", (like, like, like, like))
    rows = [dict(r) for r in cur.fetchall()]
    return {'results': rows}

@app.route('/api/patient_by_id/<int:patient_id>')
@role_required('superadmin', 'admin')
def api_patient_by_id(patient_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id,nama_pasien,nomor_rekam_medis,nik,tanggal_lahir,umur,alamat,nomor_hp,golongan_darah,status_perkawinan,pekerjaan,nama_keluarga,jenis_layanan,dokter_tujuan,status_antrian,created_at FROM patients WHERE id=?", (patient_id,))
    row = cur.fetchone()
    if not row:
        return {'result': None}
    return {'result': dict(row)}

@app.route('/api/fetal_growth/<int:patient_id>')
@role_required('superadmin', 'admin', 'dokter')
def api_fetal_growth(patient_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute('''
        SELECT created_at, detak_jantung_janin, estimasi_berat_janin, usia_kehamilan 
        FROM soap_records 
        WHERE patient_id=? 
        ORDER BY created_at ASC
    ''', (patient_id,))
    rows = cur.fetchall()
    
    labels = []
    djj_data = []
    ebj_data = []
    
    for r in rows:
        labels.append(f"{r['usia_kehamilan'] or '?'} ({fmt_dt(r['created_at'])[:10]})")
        try: 
            djj_val = int(''.join(filter(str.isdigit, str(r['detak_jantung_janin']))))
        except: 
            djj_val = None
        try: 
            ebj_val = int(''.join(filter(str.isdigit, str(r['estimasi_berat_janin']))))
        except: 
            ebj_val = None
        djj_data.append(djj_val)
        ebj_data.append(ebj_val)

    return {'labels': labels, 'djj': djj_data, 'ebj': ebj_data}


@app.route('/api/master_options')
def api_master_options():
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT category, options_text FROM master_options')
    rows = cur.fetchall()
    result = {}
    for r in rows:
        opts = [o.strip() for o in (r['options_text'] or '').split(',') if o.strip()]
        result[r['category']] = opts
    return result


@app.route('/api/patient_visits/<int:patient_id>')
@role_required('superadmin', 'admin')
def api_patient_visits(patient_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM soap_records WHERE patient_id=?', (patient_id,))
    count = cur.fetchone()[0]
    return {'count': count}

@app.route('/api/keluarga_search')
@role_required('superadmin', 'admin')
def api_keluarga_search():
    q = request.args.get('q', '').strip()
    pid = request.args.get('pid', type=int)
    if not q or len(q) < 2:
        return {'results': []}
    conn = get_db(); cur = conn.cursor()
    like = '%' + q + '%'
    cur.execute("SELECT id, nama_pasien, nomor_rekam_medis, hubungan, keluarga_id FROM patients WHERE (nama_pasien LIKE ? OR nomor_rekam_medis LIKE ?) AND COALESCE(deleted, 0) = 0 ORDER BY nama_pasien LIMIT 15", (like, like))
    rows = [dict(r) for r in cur.fetchall()]
    return {'results': rows}

@app.route('/api/keluarga_by_id/<int:patient_id>')
@role_required('superadmin', 'admin')
def api_keluarga_by_id(patient_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id, nama_pasien, nomor_rekam_medis, hubungan, keluarga_id FROM patients WHERE id=? AND deleted=0", (patient_id,))
    row = cur.fetchone()
    if not row: return {'result': None}
    p = dict(row)
    if p['keluarga_id']:
        cur.execute("SELECT id, nama_pasien, nomor_rekam_medis, hubungan FROM patients WHERE keluarga_id=? AND id!=? AND deleted=0 ORDER BY id", (p['keluarga_id'], patient_id))
        p['anggota'] = [dict(r) for r in cur.fetchall()]
    else:
        p['anggota'] = []
    return {'result': p}




def render_page(title, body_tpl, **ctx):
    user = current_user()
    page_ctx = dict(ctx)
    page_ctx['user'] = user
    page_ctx['current_user'] = user
    page_ctx['hitung_estimasi_tunggu'] = hitung_estimasi_tunggu
    page_ctx['hitung_risiko_kehamilan'] = hitung_risiko_kehamilan
    page_ctx['title'] = title
    body = render_template_string(body_tpl, **page_ctx)
    base = '''
    <!doctype html>
    <html lang="id">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
      <title>{{ title }} - {{ app_name }}</title>
      <meta name="theme-color" content="#0f172a">
      <meta name="apple-mobile-web-app-capable" content="yes">
      <meta name="mobile-web-app-capable" content="yes">
      <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
      <script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
      <style>

/* ===== KLINIK USG 4D — FULL RESPONSIVE REDESIGN v3 ===== */
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800;900&display=swap');

/* ---- CSS VARIABLES ---- */
:root {
  --bg:        #07111f;
  --bg-light:  #0e1e35;
  --card:      rgba(255,255,255,0.06);
  --card-hover:rgba(255,255,255,0.10);
  --text:      #e2e8f0;
  --text-muted:#7b93b5;
  --primary:   #22c55e;
  --primary-dk:#16a34a;
  --accent:    #0ea5e9;
  --accent-dk: #0284c7;
  --border:    rgba(148,163,184,0.12);
  --shadow:    0 12px 32px rgba(0,0,0,0.35);
  --r-sm:      10px;
  --r-md:      14px;
  --r-lg:      20px;
  --r-xl:      28px;
  --nav-h:     80px;   /* bottom nav height on mobile */
}
html.light {
  --bg:#f0f5fb; --bg-light:#e4edf7; --card:rgba(255,255,255,0.85);
  --card-hover:rgba(255,255,255,1); --text:#0f172a; --text-muted:#64748b;
  --border:rgba(15,23,42,0.08); --shadow:0 8px 24px rgba(15,23,42,0.10);
}

/* ---- CUSTOM SCROLLBAR ---- */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: rgba(148, 163, 184, 0.2);
  border-radius: 20px;
}
::-webkit-scrollbar-thumb:hover {
  background: var(--primary);
}

/* ---- RESET / BASE ---- */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body {
  font-family: 'Plus Jakarta Sans', system-ui, sans-serif;
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
  background-image:
    radial-gradient(ellipse 60% 40% at 85% 10%, rgba(34,197,94,.12), transparent),
    radial-gradient(ellipse 50% 40% at 15% 90%, rgba(14,165,233,.12), transparent);
  background-attachment: fixed;
}
a { text-decoration: none; color: inherit; }
h1,h2,h3,h4,h5,h6 { font-weight: 700; color: var(--text); }


/* ---- RESPONSIVE UI UPGRADE ---- */
.table-wrap{
  width:100%;
  overflow-x:auto;
  -webkit-overflow-scrolling:touch;
  border-radius:18px;
}
table{
  width:100%;
  border-collapse:separate;
  border-spacing:0;
  min-width:720px;
}
table th{
  position:sticky;
  top:0;
  background:rgba(15,23,42,.95);
  backdrop-filter:blur(12px);
  z-index:2;
}
table th,table td{
  padding:14px 16px;
  border-bottom:1px solid var(--border);
  vertical-align:top;
}
table tr:hover td{
  background:rgba(255,255,255,.03);
}
.action-col{
  position:sticky;
  right:0;
  background:var(--bg);
  min-width:140px;
  z-index:3;
}
.action-buttons{
  display:flex;
  gap:8px;
  flex-wrap:wrap;
  justify-content:flex-end;
}
.patient-header{
  display:flex;
  justify-content:space-between;
  align-items:flex-start;
  gap:16px;
  flex-wrap:wrap;
}
.patient-info-grid{
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
  gap:14px;
  margin-top:14px;
}
.card{
  border-radius:22px !important;
  overflow:hidden;
}
.toolbar{
  display:flex;
  gap:10px;
  flex-wrap:wrap;
  align-items:center;
  justify-content:space-between;
}
@media (max-width:768px){
  .content{
    padding:14px !important;
  }
  .card{
    padding:16px !important;
    border-radius:18px !important;
  }
  .topbar{
    gap:12px !important;
  }
  .toolbar .btn{
    flex:1 1 calc(50% - 8px);
    justify-content:center;
  }
  .patient-header{
    flex-direction:column;
    align-items:flex-start;
  }
  .patient-info-grid{
    grid-template-columns:1fr;
  }
  table{
    min-width:640px;
  }
  .action-col{
    min-width:120px;
  }
}

/* ---- LAYOUT ---- */
.layout {
  display: grid;
  grid-template-columns: 260px 1fr;
  min-height: 100vh;
}

/* ---- SIDEBAR (desktop) ---- */
.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  padding: 1.4rem 1rem;
  border-right: 1px solid var(--border);
  background: rgba(5,12,25,0.65);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  gap: .25rem;
  z-index: 100;
}
.brand {
  display: flex;
  align-items: center;
  gap: .75rem;
  margin-bottom: 1.5rem;
  padding: 0 .4rem;
}
.logo {
  width: 44px; height: 44px;
  border-radius: var(--r-md);
  background: linear-gradient(135deg, var(--primary), var(--accent));
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-weight: 900; font-size: 1.1rem;
  flex-shrink: 0;
}
.nav { display: flex; flex-direction: column; gap: .25rem; flex: 1; }
.nav-title {
  padding: .6rem .6rem .2rem;
  color: var(--text-muted);
  font-size: .68rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: .1em;
}
.nav a {
  padding: .6rem .75rem;
  border-radius: var(--r-md);
  color: var(--text-muted);
  display: flex; align-items: center; gap: .6rem;
  font-size: .88rem; font-weight: 500;
  transition: all .18s ease;
  white-space: nowrap;
}
.nav a:hover { background: var(--card); color: var(--text); }
.nav a.active {
  background: linear-gradient(135deg, rgba(34,197,94,.18), rgba(14,165,233,.18));
  color: var(--text);
  border: 1px solid rgba(34,197,94,.25);
}
.sidebar-foot {
  margin-top: auto;
  padding: .9rem 1rem;
  border-radius: var(--r-lg);
  background: var(--card);
  border: 1px solid var(--border);
}

/* ---- MAIN CONTENT ---- */
.content { padding: 1.5rem; min-width: 0; }
.topbar {
  display: flex; justify-content: space-between; align-items: center;
  gap: .75rem; margin-bottom: 1.5rem; flex-wrap: wrap;
  padding: 1rem 1.25rem;
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--r-xl);
  backdrop-filter: blur(16px);
  animation: slideDown .4s ease both;
}

/* ---- CARDS ---- */
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: var(--r-xl);
  padding: 1.25rem;
  backdrop-filter: blur(16px);
  animation: fadeUp .35s ease both;
}
.stat {
  padding: 1.25rem;
  border-radius: var(--r-xl);
  background: linear-gradient(135deg, rgba(34,197,94,.12), rgba(14,165,233,.12));
  border: 1px solid var(--border);
}
.hero {
  display: flex; justify-content: space-between; align-items: center;
  gap: 1rem; padding: 1.25rem;
  background: linear-gradient(135deg, rgba(34,197,94,.15), rgba(14,165,233,.15));
  border-radius: var(--r-xl); border: 1px solid var(--border);
  flex-wrap: wrap;
}

/* ---- BUTTONS ---- */
.btn {
  appearance: none;
  border-radius: var(--r-md);
  padding: .65rem 1rem;
  font-weight: 700; font-size: .88rem;
  cursor: pointer;
  background: var(--bg-light);
  color: var(--text);
  border: 1px solid var(--border);
  display: inline-flex; align-items: center; gap: .45rem;
  transition: all .18s ease;
  white-space: nowrap;
}
.btn:hover { box-shadow: var(--shadow); transform: translateY(-1px); }
.btn-primary {
  background: linear-gradient(135deg, var(--primary), var(--accent));
  color: #fff; border: none;
}
.btn-primary:hover { filter: brightness(1.1); }
.btn-sm { padding: .45rem .75rem; font-size: .8rem; border-radius: var(--r-sm); }

/* ---- FORMS ---- */
.toolbar, .searchbox { display: flex; gap: .6rem; flex-wrap: wrap; align-items: center; }
.grid  { display: grid; gap: 1rem; }
.g2    { grid-template-columns: repeat(2, minmax(0,1fr)); }
.g3    { grid-template-columns: repeat(3, minmax(0,1fr)); }
.g4    { grid-template-columns: repeat(4, minmax(0,1fr)); }
.form2 { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: .875rem; }
.form3 { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: .875rem; }
label  { display: block; font-size: .8rem; color: var(--text-muted); font-weight: 600; margin-bottom: .375rem; }

.input, .select, .textarea {
  width: 100%; padding: .75rem .9rem;
  border-radius: var(--r-md);
  border: 1px solid var(--border);
  background: rgba(255,255,255,.04);
  color: var(--text); outline: none;
  font-family: inherit; font-size: .9rem;
  transition: all .2s ease;
}
.select {
  appearance: none;
  -webkit-appearance: none;
  -moz-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%237b93b5'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M19 9l-7 7-7-7'%3E%3C/path%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 0.8rem center;
  background-size: 1.1rem;
  padding-right: 2.5rem !important;
  cursor: pointer;
}
.select option { background: var(--bg); color: var(--text); }
.input:focus, .select:focus, .textarea:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px rgba(34,197,94,.18);
  background: rgba(255,255,255,.07);
}
.textarea { min-height: 6.5rem; resize: vertical; }

/* ---- TABLES — fully responsive ---- */
table { width: 100%; border-collapse: collapse; }
th, td { padding: .7rem .6rem; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
th { font-size: .72rem; text-transform: uppercase; color: var(--text-muted); font-weight: 700; }

/* Mobile table scroll */
.table-wrap { width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; border-radius: var(--r-lg); }

/* ---- BADGES ---- */
.badge, .pill {
  display: inline-flex; padding: .3rem .65rem; border-radius: 999px;
  font-size: .72rem; font-weight: 700;
  background: rgba(255,255,255,.07); border: 1px solid var(--border);
}
.menunggu  { background: rgba(245,158,11,.15); color: #f59e0b; border-color: rgba(245,158,11,.2); }
.diperiksa { background: rgba(14,165,233,.15); color: #0ea5e9; border-color: rgba(14,165,233,.2); }
.selesai   { background: rgba(34,197,94,.15);  color: #22c55e; border-color: rgba(34,197,94,.2); }
.paid      { background: rgba(34,197,94,.15);  color: #22c55e; }
.unpaid    { background: rgba(239,68,68,.15);  color: #ef4444; }

/* ---- UTILS ---- */
.muted { color: var(--text-muted); }
.small { font-size: .75rem; }
.empty { text-align: center; color: var(--text-muted); padding: 2rem; }
.mono  { font-family: ui-monospace, SFMono-Regular, monospace; font-size: .85em; }
.wrap  { white-space: pre-wrap; }
.center { text-align: center; }
.authbox { max-width: 980px; margin: 4vh auto; padding: 0 1rem; }
.loginbox { max-width: 460px; }

/* ---- FLASH MESSAGES ---- */
.flash-wrap { display: grid; gap: .6rem; margin-bottom: .875rem; }
.flash {
  padding: .75rem .875rem; border-radius: var(--r-md);
  border: 1px solid var(--border); background: var(--card);
  font-size: .875rem;
}
.flash.success { border-color: rgba(34,197,94,.4); color: #86efac; }
.flash.danger  { border-color: rgba(239,68,68,.4); color: #fca5a5; }
.flash.warning { border-color: rgba(245,158,11,.4); color: #fcd34d; }
.flash.info    { border-color: rgba(14,165,233,.4); color: #7dd3fc; }

/* ---- RISK BADGE ---- */
.risk-badge { padding: 7px 14px; border-radius: 999px; font-weight: 700; font-size: .8rem; display: inline-flex; align-items: center; gap: 6px; }
@keyframes pulseRisk { 0%,100% { box-shadow: 0 0 0 0 rgba(239,68,68,.4); } 70% { box-shadow: 0 0 0 10px rgba(239,68,68,0); } }
.risk-merah { animation: pulseRisk 2s infinite; }

/* ---- ANIMATIONS ---- */
@keyframes fadeUp   { from { opacity:0; transform:translateY(14px); } to { opacity:1; transform:none; } }
@keyframes fadeDown { from { opacity:0; transform:translateY(-12px); } to { opacity:1; transform:none; } }
@keyframes slideDown{ from { opacity:0; transform:translateY(-10px); } to { opacity:1; transform:none; } }
@keyframes fade     { from { opacity:0; transform:translateY(10px);  } to { opacity:1; transform:none; } }
@keyframes scaleIn  { from { opacity:0; transform:scale(.92); } to { opacity:1; transform:scale(1); } }

/* Staggered card animation */
.card:nth-child(1) { animation-delay: .05s; }
.card:nth-child(2) { animation-delay: .10s; }
.card:nth-child(3) { animation-delay: .15s; }
.card:nth-child(4) { animation-delay: .20s; }

/* ======================================================
   MOBILE-FIRST RESPONSIVE  (max-width: 767px)
   ====================================================== */
@media (max-width: 767px) {

  /* Layout: single column, nav goes bottom */
  .layout {
    grid-template-columns: 1fr !important;
    grid-template-rows: 1fr auto;
  }

  /* ---- BOTTOM NAV BAR ---- */
  .sidebar {
    position: fixed !important;
    bottom: 0 !important; top: auto !important;
    left: 0; right: 0;
    width: 100% !important;
    height: var(--nav-h) !important;
    padding: .5rem .75rem env(safe-area-inset-bottom) !important;
    border-right: none !important;
    border-top: 1px solid var(--border);
    background: rgba(5,12,25,.92) !important;
    backdrop-filter: blur(28px) !important;
    -webkit-backdrop-filter: blur(28px) !important;
    flex-direction: row !important;
    align-items: center !important;
    justify-content: space-around !important;
    overflow: visible !important;
    gap: 0 !important;
  }

  /* Hide desktop-only sidebar elements */
  .brand, .sidebar-foot, .nav-title { display: none !important; }

  /* Bottom nav row */
  .nav {
    flex-direction: row !important;
    width: 100% !important;
    justify-content: space-around !important;
    align-items: center !important;
    gap: 0 !important;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .nav::-webkit-scrollbar { display: none; }

  .nav a {
    flex-direction: column !important;
    align-items: center !important;
    justify-content: center !important;
    font-size: .58rem !important;
    font-weight: 600 !important;
    padding: .4rem .5rem !important;
    min-width: 52px !important;
    max-width: 68px !important;
    border-radius: 14px !important;
    border: none !important;
    gap: .2rem !important;
    color: var(--text-muted) !important;
    background: transparent !important;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .nav a .nav-icon { font-size: 1.25rem; line-height: 1; }
  .nav a.active {
    background: linear-gradient(135deg, rgba(34,197,94,.22), rgba(14,165,233,.22)) !important;
    color: #a7f3d0 !important;
    border: 1px solid rgba(34,197,94,.25) !important;
    transform: translateY(-2px);
  }

  /* Content padding — leave room for bottom nav */
  .content {
    padding: .875rem !important;
    padding-bottom: calc(var(--nav-h) + 1.25rem) !important;
    order: -1; /* content above nav in DOM flow */
  }

  /* Topbar mobile */
  .topbar {
    padding: .75rem 1rem !important;
    border-radius: var(--r-lg) !important;
    margin-bottom: 1rem !important;
    flex-direction: row !important;
    align-items: flex-start !important;
  }
  .topbar h2 { font-size: 1.2rem !important; }
  .topbar .flex { display: flex; flex-wrap: wrap; gap: .4rem; }

  /* Cards */
  .card  { border-radius: var(--r-lg) !important; padding: 1rem !important; margin-bottom: .75rem !important; }
  .stat  { border-radius: var(--r-lg) !important; padding: .9rem !important; }

  /* Grid — single col on mobile */
  .g2, .g3, .g4, .grid, .form2, .form3 {
    grid-template-columns: 1fr !important;
    gap: .75rem !important;
  }

  /* Toolbar — stack vertically */
  .toolbar {
    flex-direction: column !important;
    align-items: stretch !important;
    gap: .5rem !important;
  }
  .toolbar .btn, .toolbar select, .toolbar input, .toolbar a.btn {
    width: 100% !important;
    justify-content: center !important;
    text-align: center !important;
  }

  /* Searchbox stays horizontal if possible */
  .searchbox {
    flex-direction: column !important;
    align-items: stretch !important;
  }
  .searchbox .btn, .searchbox .input, .searchbox .select {
    width: 100% !important;
  }

  /* Tables — horizontal scroll container */
  table {
    min-width: 520px !important;
  }
  .card:has(table), div:has(> table) {
    overflow-x: auto !important;
    -webkit-overflow-scrolling: touch !important;
  }

  /* Inputs */
  .input, .select, .textarea {
    font-size: 16px !important; /* prevent iOS zoom */
    padding: .8rem 1rem !important;
    border-radius: var(--r-md) !important;
  }

  /* Buttons */
  .btn { border-radius: var(--r-md) !important; font-size: .85rem !important; }
  .btn-sm { padding: .5rem .75rem !important; font-size: .78rem !important; }

  /* Hero */
  .hero {
    flex-direction: column !important;
    align-items: flex-start !important;
  }

  /* Stat grid 2 cols on mobile */
  .g4.grid {
    grid-template-columns: repeat(2, 1fr) !important;
  }

  /* Flash messages */
  .flash-wrap { margin-bottom: .75rem; }

  /* Badges */
  .badge, .pill { font-size: .68rem !important; padding: .25rem .55rem !important; }
}

/* ---- TABLET (768px - 1023px) ---- */
@media (min-width: 768px) and (max-width: 1023px) {
  .layout { grid-template-columns: 220px 1fr; }
  .sidebar { padding: 1rem .75rem; }
  .nav a { font-size: .82rem; padding: .55rem .7rem; }
  .brand { gap: .5rem; }
  .logo { width: 38px; height: 38px; font-size: 1rem; }
  .g4 { grid-template-columns: repeat(2, 1fr); }
  .g3 { grid-template-columns: repeat(2, 1fr); }
  .content { padding: 1.25rem; }
}

/* ---- DESKTOP (≥1024px) ---- */
@media (min-width: 1024px) {
  .layout { grid-template-columns: 260px 1fr; }
}
@media (min-width: 1400px) {
  .layout { grid-template-columns: 280px 1fr; }
}

/* ---- PRINT ---- */
@media print {
  .sidebar, .topbar, .no-print, .flash-wrap { display: none !important; }
  body { background: #fff !important; color: #000; }
  .layout { display: block; }
  .card { box-shadow: none; border: 1px solid #ddd; background: #fff !important; }
}

/* ======================================================
   LOGIN PAGE — Animated Premium Redesign
   ====================================================== */
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.5rem;
  position: relative;
  overflow: hidden;
  background:
    radial-gradient(ellipse 60% 50% at 20% 20%, rgba(34,197,94,.15), transparent),
    radial-gradient(ellipse 50% 60% at 80% 80%, rgba(14,165,233,.18), transparent),
    #07111f;
}

/* Floating orbs */
.login-orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(60px);
  opacity: .55;
  animation: orbFloat 8s ease-in-out infinite;
  pointer-events: none;
}
.login-orb-1 {
  width: 320px; height: 320px;
  background: radial-gradient(circle, rgba(34,197,94,.35), transparent 70%);
  top: -80px; left: -80px;
  animation-duration: 9s;
}
.login-orb-2 {
  width: 280px; height: 280px;
  background: radial-gradient(circle, rgba(14,165,233,.35), transparent 70%);
  bottom: -60px; right: -60px;
  animation-duration: 11s;
  animation-delay: -4s;
}
.login-orb-3 {
  width: 180px; height: 180px;
  background: radial-gradient(circle, rgba(168,85,247,.25), transparent 70%);
  top: 55%; left: 70%;
  animation-duration: 13s;
  animation-delay: -7s;
}
@keyframes orbFloat {
  0%,100% { transform: translate(0,0) scale(1); }
  33%      { transform: translate(20px,-20px) scale(1.06); }
  66%      { transform: translate(-15px,15px) scale(.96); }
}

/* Grid dots background */
.login-grid {
  position: absolute; inset: 0;
  background-image:
    radial-gradient(rgba(255,255,255,.07) 1px, transparent 1px);
  background-size: 40px 40px;
  mask-image: radial-gradient(ellipse 80% 80% at 50% 50%, #000 40%, transparent 100%);
}

/* Login card */
.login-card {
  position: relative; z-index: 10;
  width: 100%; max-width: 420px;
  background: rgba(255,255,255,.07);
  border: 1px solid rgba(255,255,255,.12);
  border-radius: 32px;
  padding: 2.25rem 2rem;
  backdrop-filter: blur(28px);
  -webkit-backdrop-filter: blur(28px);
  box-shadow:
    0 30px 80px rgba(0,0,0,.4),
    0 0 0 1px rgba(255,255,255,.05) inset;
  animation: loginCardIn .7s cubic-bezier(.16,1,.3,1) both;
}
@keyframes loginCardIn {
  from { opacity:0; transform: translateY(28px) scale(.96); }
  to   { opacity:1; transform: translateY(0) scale(1); }
}

/* Login logo */
.login-logo-wrap {
  display: flex; flex-direction: column; align-items: center;
  margin-bottom: 1.75rem;
  animation: fadeDown .6s .15s ease both;
}
.login-logo {
  width: 76px; height: 76px;
  border-radius: 22px;
  background: linear-gradient(135deg, #22c55e, #0ea5e9);
  display: flex; align-items: center; justify-content: center;
  font-size: 1.8rem; font-weight: 900; color: #fff;
  box-shadow: 0 16px 40px rgba(14,165,233,.35), 0 0 0 8px rgba(34,197,94,.12);
  animation: logoPulse 3s ease-in-out infinite;
}
@keyframes logoPulse {
  0%,100% { box-shadow: 0 16px 40px rgba(14,165,233,.35), 0 0 0 8px rgba(34,197,94,.12); }
  50%      { box-shadow: 0 16px 50px rgba(34,197,94,.45), 0 0 0 14px rgba(34,197,94,.07); }
}
.login-title {
  margin-top: .9rem;
  font-size: 1.6rem; font-weight: 900; color: #fff; letter-spacing: -.5px;
}
.login-sub {
  margin-top: .3rem;
  color: var(--text-muted); font-size: .85rem; font-weight: 500;
}

/* Login form fields */
.login-field {
  margin-bottom: 1rem;
  animation: fadeUp .5s ease both;
}
.login-field:nth-child(1) { animation-delay: .25s; }
.login-field:nth-child(2) { animation-delay: .35s; }
.login-label {
  display: block;
  font-size: .72rem; font-weight: 800; letter-spacing: .08em;
  text-transform: uppercase; color: var(--text-muted);
  margin-bottom: .45rem;
}
.login-input-wrap { position: relative; }
.login-input-icon {
  position: absolute; left: 14px; top: 50%;
  transform: translateY(-50%);
  font-size: 1rem; color: var(--text-muted);
  pointer-events: none;
  transition: color .2s;
}
.login-input {
  width: 100%; padding: 14px 14px 14px 44px;
  border-radius: 16px;
  border: 1.5px solid rgba(255,255,255,.1);
  background: rgba(255,255,255,.04);
  color: #fff; font-size: 15px; font-family: inherit;
  outline: none;
  transition: border-color .2s, background .2s, box-shadow .2s;
}
.login-input::placeholder { color: rgba(255,255,255,.25); }
.login-input:focus {
  border-color: #22c55e;
  background: rgba(255,255,255,.08);
  box-shadow: 0 0 0 4px rgba(34,197,94,.18);
}
.login-input:focus + .login-input-icon,
.login-input-wrap:focus-within .login-input-icon {
  color: #22c55e;
}

/* Login button */
.login-btn-wrap {
  margin-top: 1.5rem;
  animation: fadeUp .5s .45s ease both;
}
.login-btn {
  width: 100%; padding: 15px;
  border: none; border-radius: 16px;
  background: linear-gradient(135deg, #22c55e, #0ea5e9);
  color: #fff; font-weight: 800; font-size: 1rem;
  cursor: pointer;
  box-shadow: 0 12px 32px rgba(14,165,233,.3);
  transition: all .2s ease;
  position: relative; overflow: hidden;
}
.login-btn::after {
  content: '';
  position: absolute; inset: 0;
  background: linear-gradient(135deg, rgba(255,255,255,.15), transparent);
  opacity: 0; transition: opacity .2s;
}
.login-btn:hover { transform: translateY(-2px); box-shadow: 0 18px 40px rgba(14,165,233,.4); }
.login-btn:hover::after { opacity: 1; }
.login-btn:active { transform: translateY(0); }

/* Login footer */
.login-footer {
  text-align: center; margin-top: 1.5rem;
  color: var(--text-muted); font-size: .8rem;
  animation: fadeUp .5s .55s ease both;
}

/* ======================================================
   POST-LOGIN PAGE ENTRANCE ANIMATIONS
   ====================================================== */
/* Topbar slide */
.topbar { animation: slideDown .45s cubic-bezier(.16,1,.3,1) both; }

/* Dashboard stat cards bounce in */
@keyframes statBounce {
  0%   { opacity:0; transform: scale(.85) translateY(10px); }
  60%  { transform: scale(1.03) translateY(-3px); }
  100% { opacity:1; transform: scale(1) translateY(0); }
}
.stat {
  animation: statBounce .5s cubic-bezier(.34,1.56,.64,1) both;
}
.stat:nth-child(1) { animation-delay: .08s; }
.stat:nth-child(2) { animation-delay: .16s; }
.stat:nth-child(3) { animation-delay: .24s; }
.stat:nth-child(4) { animation-delay: .32s; }

/* Welcome banner shimmer */
.hero { position: relative; overflow: hidden; }
.hero::after {
  content:'';
  position: absolute;
  top: 0; left: -100%; width: 60%; height: 100%;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.06), transparent);
  animation: shimmerHero 2.5s .6s ease both;
}
@keyframes shimmerHero {
  from { left: -60%; }
  to   { left: 130%; }
}

/* Nav items stagger on mobile */
@media (max-width: 767px) {
  .nav a:nth-child(1)  { animation: fadeUp .3s .05s ease both; }
  .nav a:nth-child(2)  { animation: fadeUp .3s .10s ease both; }
  .nav a:nth-child(3)  { animation: fadeUp .3s .15s ease both; }
  .nav a:nth-child(4)  { animation: fadeUp .3s .20s ease both; }
  .nav a:nth-child(5)  { animation: fadeUp .3s .25s ease both; }
  .nav a:nth-child(6)  { animation: fadeUp .3s .30s ease both; }
  .nav a:nth-child(7)  { animation: fadeUp .3s .35s ease both; }
  .nav a:nth-child(8)  { animation: fadeUp .3s .40s ease both; }
  .nav a:nth-child(9)  { animation: fadeUp .3s .45s ease both; }
  .nav a:nth-child(10) { animation: fadeUp .3s .50s ease both; }
}

/* Table scroll hint */
.table-scroll-hint {
  display: none;
  font-size: .72rem; color: var(--text-muted);
  text-align: right; margin-bottom: .4rem;
}
@media (max-width: 767px) { .table-scroll-hint { display: block; } }

/* ---- MISC LIGHT THEME overrides ---- */
html.light .sidebar   { background: rgba(240,245,251,.92) !important; }
html.light .login-page{ background: #f0f5fb; }
html.light .login-card{ background: rgba(255,255,255,.9); border-color: rgba(15,23,42,.08); }
html.light .login-input{ background: rgba(15,23,42,.04); border-color: rgba(15,23,42,.15); color:#0f172a; }
html.light .login-input::placeholder { color: rgba(15,23,42,.3); }
html.light .login-title { color: #0f172a; }

/* Light mode: all text colors readable */
html.light .text-white,
html.light .text-slate-100,
html.light .text-slate-200,
html.light .text-slate-300 { color: #0f172a !important; }

html.light .text-slate-400,
html.light .text-slate-500 { color: #475569 !important; }

html.light .text-emerald-100,
html.light .text-emerald-200,
html.light .text-emerald-300 { color: #166534 !important; }

html.light .text-emerald-400,
html.light .text-emerald-500 { color: #16a34a !important; }

html.light .text-sky-400,
html.light .text-blue-400 { color: #0369a1 !important; }

html.light .text-amber-400,
html.light .text-amber-200 { color: #b45309 !important; }

html.light .text-red-200,
html.light .text-red-400 { color: #dc2626 !important; }

html.light .text-cyan-200 { color: #0e7490 !important; }

html.light .bg-slate-700,
html.light .bg-slate-800,
html.light [class*='bg-slate-800/50'],
html.light [class*="bg-slate-900/50"] { background: rgba(226,232,240,.6) !important; }

html.light .border-slate-700,
html.light .border-slate-800,
html.light .border-slate-900 { border-color: rgba(15,23,42,.15) !important; }

html.light .divide-slate-800 > * { border-color: rgba(15,23,42,.1) !important; }

/* Table sticky header light mode */
html.light table th { background: rgba(240,245,251,.95) !important; }


</style>
    </head>
    <body class="font-sans antialiased">
      <script>
        (function(){ if((localStorage.getItem('themeMode')||'dark')==='light') document.documentElement.classList.add('light'); })();
        function themeToggle(){ document.documentElement.classList.toggle('light'); localStorage.setItem('themeMode', document.documentElement.classList.contains('light') ? 'light' : 'dark'); }
        function printPage(){ window.print(); }
        /* Page entrance: fade body in after paint */
        document.body.style.opacity='0';
        document.body.style.transition='opacity .35s ease';
        window.addEventListener('DOMContentLoaded',function(){
          requestAnimationFrame(function(){ document.body.style.opacity='1'; });
        });
      </script>
      {% if user %}
      <div class="layout text-slate-100">
        <aside class="sidebar shadow-lg">
          <div class="brand">
            <div class="logo">USG</div>
            <div>
              <div class="text-xl font-extrabold text-white">{{ app_name }}</div>
              <div class="text-xs text-slate-400">Klinik Arissa</div>
            </div>
          </div>
          <nav class="nav">
            <div class="nav-title">Utama</div>
            <a href="{{ url_for('dashboard') }}" class="{{ 'active' if request.endpoint=='dashboard' else '' }}"><span class="nav-icon">🏠</span><span>Dashboard</span></a>
            {% if user['role'] in ['superadmin','admin','dokter'] %}<a href="{{ url_for('antrian') }}" class="{{ 'active' if request.endpoint=='antrian' else '' }}"><span class="nav-icon">🚶</span><span>Antrian</span></a>{% endif %}
            {% if user['role'] in ['superadmin','admin','dokter'] %}<a href="{{ url_for('antrian_tv') }}" target="_blank" class="{{ 'active' if request.endpoint=='antrian_tv' else '' }}"><span class="nav-icon">📺</span><span>TV Antrian</span></a>{% endif %}
            {% if user['role'] in ['superadmin','admin','dokter'] %}<a href="{{ url_for('patients') }}" class="{{ 'active' if request.endpoint in ['patients','patient_new','patient_detail','patient_history'] else '' }}"><span class="nav-icon">📚</span><span>Rekam Medis</span></a>{% endif %}
            {% if user['role'] in ['superadmin','admin'] %}<a href="{{ url_for('patient_new') }}" class="{{ 'active' if request.endpoint=='patient_new' else '' }}"><span class="nav-icon">➕</span><span>Input Pasien</span></a>{% endif %}
            <div class="nav-title">Operasional</div>
            {% if user['role'] in ['superadmin','admin'] %}<a href="{{ url_for('panduan_admin') }}" class="{{ 'active' if request.endpoint=='panduan_admin' else '' }}"><span class="nav-icon">👩‍💻</span><span>SOP Admin</span></a>{% endif %}
            {% if user['role'] in ['superadmin','dokter'] %}<a href="{{ url_for('panduan_dokter') }}" class="{{ 'active' if request.endpoint=='panduan_dokter' else '' }}"><span class="nav-icon">👨‍⚕️</span><span>SOP Dokter</span></a>{% endif %}
            {% if user['role'] in ['superadmin','admin','dokter'] %}<a href="{{ url_for('soap_templates_page') }}" class="{{ 'active' if request.endpoint=='soap_templates_page' else '' }}"><span class="nav-icon">🧩</span><span>Template</span></a>{% endif %}
            {% if user['role'] in ['superadmin','admin','dokter'] %}<a href="{{ url_for('sop_page') }}" class="{{ 'active' if request.endpoint=='sop_page' else '' }}"><span class="nav-icon">📋</span><span>SOP</span></a>{% endif %}
            {% if user['role'] in ['superadmin','admin','dokter'] %}<a href="{{ url_for('uploads_page') }}" class="{{ 'active' if request.endpoint=='uploads_page' else '' }}"><span class="nav-icon">📁</span><span>Hasil USG</span></a>{% endif %}
            {% if user['role'] in ['superadmin','admin'] %}<a href="{{ url_for('billing_page') }}" class="{{ 'active' if request.endpoint=='billing_page' else '' }}"><span class="nav-icon">💳</span><span>Billing</span></a>{% endif %}
            <div class="nav-title">Sistem</div>
            {% if user['role']=='superadmin' %}<a href="{{ url_for('users_page') }}" class="{{ 'active' if request.endpoint=='users_page' else '' }}"><span class="nav-icon">👥</span><span>User</span></a>{% endif %}
            {% if user['role'] in ['superadmin','admin'] %}<a href="{{ url_for('audit_logs_page') }}" class="{{ 'active' if request.endpoint=='audit_logs_page' else '' }}"><span class="nav-icon">🕵️</span><span>Audit</span></a>{% endif %}
            <a href="{{ url_for('settings') }}" class="{{ 'active' if request.endpoint=='settings' else '' }}"><span class="nav-icon">⚙️</span><span>Settings</span></a>
            <a href="{{ url_for('logout') }}"><span class="nav-icon">🚪</span><span>Logout</span></a>
          </nav>
          <div class="sidebar-foot mt-auto">
            <div class="font-bold text-lg">{{ user['full_name'] or user['username'] }}</div>
            <div class="text-sm text-slate-400">Role: {{ user['role'] }}</div>
            <div class="flex flex-wrap gap-2 mt-4">
              <button class="btn btn-sm bg-slate-700 hover:bg-slate-600 text-white" onclick="themeToggle()">🌓 Mode</button>
              {% if user['role'] in ['superadmin','admin'] %}<a class="btn btn-sm bg-slate-700 hover:bg-slate-600 text-white" href="{{ url_for('backup_db') }}">💾 Backup</a>{% endif %}
            </div>
          </div>
        </aside>
        <main class="content flex-1 p-6">
          <div class="topbar no-print flex justify-between items-center gap-4 mb-6 flex-wrap">
            <div>
              <div class="text-sm text-slate-400">{{ now_label }}</div>
              <h2 class="text-3xl font-bold text-white mt-1">{{ title }}</h2>
            </div>
            <div class="flex flex-wrap gap-3">
              <button class="btn bg-slate-700 hover:bg-slate-600 text-white" onclick="themeToggle()">🌓 Dark/Light</button>
              <button class="btn bg-slate-700 hover:bg-slate-600 text-white" onclick="printPage()">🖨️ Cetak</button>
            </div>
          </div>
          {% with messages = get_flashed_messages(with_categories=true) %}{% if messages %}<div class="flash-wrap mb-4">{% for cat,msg in messages %}<div class="flash p-3 rounded-lg text-sm {% if cat=='success' %}bg-emerald-500/10 border-emerald-500/30 text-emerald-200{% elif cat=='danger' %}bg-red-500/10 border-red-500/30 text-red-200{% elif cat=='warning' %}bg-amber-500/10 border-amber-500/30 text-amber-200{% else %}bg-cyan-500/10 border-cyan-500/30 text-cyan-200{% endif %}">{{ msg }}</div>{% endfor %}</div>{% endif %}{% endwith %}
          {{ body|safe }}
        </main>
      </div>
      {% else %}
      {{ body|safe }}
      {% endif %}
    </body>
    </html>
    '''
    return render_template_string(base, title=title, app_name=APP_NAME, body=body, user=user, now_label=fmt_dt(now()))




@app.route('/appointments')
@role_required('superadmin','admin','dokter','pasien')
def appointments():
    conn = get_db()
    rows = conn.execute("""
        SELECT a.*, p.nama_pasien, p.nomor_rekam_medis
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        ORDER BY a.appointment_date DESC
    """).fetchall()

    body = """
    <div class="card">
      <div class="toolbar">
        <h2>📅 Jadwal Appointment</h2>
        <a class="btn btn-primary" href="{{ url_for('add_appointment') }}">+ Tambah Appointment</a>
      </div>

      <div class="table-wrap"><table class="table">
        <thead>
          <tr>
            <th>Pasien</th>
            <th>RM</th>
            <th>Dokter</th>
            <th>Tanggal</th>
            <th>Keluhan</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
        {% for r in rows %}
          <tr>
            <td>{{ r['nama_pasien'] }}</td>
            <td>{{ r['nomor_rekam_medis'] }}</td>
            <td>{{ r['doctor_name'] or '-' }}</td>
            <td>{{ fmt_dt(r['appointment_date']) }}</td>
            <td>{{ r['complaint'] or '-' }}</td>
            <td><span class="badge">{{ r['status'] }}</span></td>
          </tr>
        {% endfor %}
        </tbody>
      </table></div>
      </div>
    </div>
    """
    return render_page('Appointments', body, rows=rows, fmt_dt=fmt_dt)


@app.route('/appointments/add', methods=['GET', 'POST'])
@role_required('superadmin', 'admin', 'dokter')
def add_appointment():
    conn = get_db()

    if request.method == 'POST':
        patient_id = request.form.get('patient_id')
        doctor_name = request.form.get('doctor_name')
        appointment_date = request.form.get('appointment_date')
        complaint = request.form.get('complaint')

        conn.execute("""
            INSERT INTO appointments
            (patient_id, doctor_name, appointment_date, complaint)
            VALUES (?, ?, ?, ?)
        """, (patient_id, doctor_name, appointment_date, complaint))

        conn.commit()

        flash('Appointment berhasil ditambahkan.', 'success')
        return redirect(url_for('appointments'))

    patients = conn.execute("""
        SELECT id, nama_pasien, nomor_rekam_medis
        FROM patients
        ORDER BY created_at DESC
    """).fetchall()

    body = """
    <div class="card">
      <h2>➕ Tambah Appointment</h2>

      <form method="post" class="grid">
        <div>
          <label>Pasien</label>
          <select class="input" name="patient_id" required>
            <option value="">-- Pilih Pasien --</option>
            {% for p in patients %}
            <option value="{{ p['id'] }}">
              {{ p['nama_pasien'] }} - {{ p['nomor_rekam_medis'] }}
            </option>
            {% endfor %}
          </select>
        </div>

        <div>
          <label>Nama Dokter</label>
          <input class="input" name="doctor_name" placeholder="dr. ..." required>
        </div>

        <div>
          <label>Tanggal Appointment</label>
          <input class="input" type="datetime-local" name="appointment_date" required>
        </div>

        <div>
          <label>Keluhan</label>
          <textarea class="input" name="complaint"></textarea>
        </div>

        <button class="btn btn-primary">💾 Simpan Appointment</button>
      </form>
    </div>
    """
    return render_page('Tambah Appointment', body, patients=patients)


@app.route('/export-patients')
@role_required('superadmin', 'admin')
def export_patients():
    conn = get_db()
    rows = conn.execute("""
        SELECT nama_pasien, nomor_rekam_medis, nomor_hp, alamat, created_at
        FROM patients
        ORDER BY created_at DESC
    """).fetchall()

    import csv
    from io import StringIO

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow(['Nama Pasien', 'No RM', 'No HP', 'Alamat', 'Tanggal Input'])

    for r in rows:
        writer.writerow([
            r['nama_pasien'],
            r['nomor_rekam_medis'],
            r['nomor_hp'],
            r['alamat'],
            r['created_at']
        ])

    mem = io.BytesIO()
    mem.write(output.getvalue().encode('utf-8-sig'))
    mem.seek(0)

    return send_file(
        mem,
        as_attachment=True,
        download_name='data_pasien.csv',
        mimetype='text/csv'
    )


@app.route('/api/dashboard-stats')
@role_required('superadmin','admin','dokter','pasien')
def dashboard_stats_api():
    conn = get_db()

    total_patients = conn.execute(
        "SELECT COUNT(*) FROM patients"
    ).fetchone()[0]

    total_soap = conn.execute(
        "SELECT COUNT(*) FROM soap_records"
    ).fetchone()[0]

    total_appointments = conn.execute(
        "SELECT COUNT(*) FROM appointments"
    ).fetchone()[0]

    return {
        "total_patients": total_patients,
        "total_soap": total_soap,
        "total_appointments": total_appointments,
        "generated_at": now()
    }




@app.route('/')
def index():
    if current_user():
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user():
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        conn = get_db(); cur = conn.cursor()
        cur.execute('SELECT * FROM users WHERE username=? AND active=1', (username,))
        user = cur.fetchone()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']; session['role'] = user['role']
            log_action('LOGIN', 'Login berhasil: ' + username)
            flash('Selamat datang, {}!'.format(user['full_name'] or user['username']), 'success')
            return redirect(request.args.get('next') or url_for('dashboard'))
        log_action('LOGIN_FAILED', 'Gagal login: ' + username)
        flash('Username atau password salah.', 'danger')

    body = '''
    <div class="login-page">
      <!-- Floating animated orbs -->
      <div class="login-orb login-orb-1"></div>
      <div class="login-orb login-orb-2"></div>
      <div class="login-orb login-orb-3"></div>
      <!-- Grid dots -->
      <div class="login-grid"></div>

      <div class="login-card">
        <!-- Logo & header -->
        <div class="login-logo-wrap">
          <div class="login-logo">🔬</div>
          <h1 class="login-title">Klinik Arissa</h1>
          <p class="login-sub">Sistem Informasi Medis Terpadu USG 4D</p>
        </div>

        <!-- Flash messages -->
        {% with messages = get_flashed_messages(with_categories=true) %}
        {% if messages %}
        <div style="margin-bottom:1rem;animation:fadeUp .4s ease both">
          {% for cat,msg in messages %}
          <div style="padding:12px 16px;border-radius:14px;border:1px solid {% if cat=='success' %}rgba(34,197,94,.35){% elif cat=='danger' %}rgba(239,68,68,.35){% else %}rgba(245,158,11,.35){% endif %};background:rgba(0,0,0,.25);color:var(--text);font-size:14px;font-weight:500;display:flex;align-items:center;gap:10px;backdrop-filter:blur(10px)">
            <span>{% if cat=='danger' %}⚠️{% elif cat=='success' %}✅{% else %}ℹ️{% endif %}</span> {{ msg }}
          </div>
          {% endfor %}
        </div>
        {% endif %}
        {% endwith %}

        <!-- Form -->
        <form method="post">
          <div class="login-field">
            <label class="login-label">Username</label>
            <div class="login-input-wrap">
              <input class="login-input" name="username" required autocomplete="username"
                     placeholder="Masukkan username"
                     autofocus>
              <span class="login-input-icon">👤</span>
            </div>
          </div>

          <div class="login-field">
            <label class="login-label">Password</label>
            <div class="login-input-wrap">
              <input class="login-input" type="password" name="password" required
                     autocomplete="current-password"
                     placeholder="••••••••">
              <span class="login-input-icon">🔒</span>
            </div>
          </div>

          <div class="login-btn-wrap">
            <button class="login-btn" type="submit">
              Masuk ke Sistem &nbsp;→
            </button>
          </div>
        </form>

        <div class="login-footer">
          &copy; 2026 Klinik Arissa USG 4D &nbsp;·&nbsp; v3.0
        </div>
      </div>
    </div>
    <script>
      /* Ripple on login button */
      document.querySelector('.login-btn').addEventListener('click', function(e){
        const r = document.createElement('span');
        r.style.cssText = 'position:absolute;border-radius:50%;width:1px;height:1px;background:rgba(255,255,255,.4);transform:scale(0);animation:ripple .6s linear;left:'+e.offsetX+'px;top:'+e.offsetY+'px;pointer-events:none';
        this.appendChild(r);
        setTimeout(()=>r.remove(), 700);
      });
    </script>
    <style>
      @keyframes ripple{ to{ transform:scale(300); opacity:0; } }
    </style>
    '''

    return render_page('Login', body, app_name=APP_NAME)


@app.route('/logout')
@role_required('superadmin', 'admin', 'dokter', 'pasien')
def logout():
    u = current_user()
    if u: log_action('LOGOUT', 'Logout: ' + u['username'])
    session.clear(); flash('Anda telah logout.', 'info')
    return redirect(url_for('login'))


@app.route('/dashboard')
@role_required('superadmin', 'admin', 'dokter', 'pasien')
def dashboard():
    user = current_user()
    conn = get_db(); cur = conn.cursor()
    if user['role'] == 'pasien' and user['patient_id']:
        cur.execute('SELECT * FROM patients WHERE id=?', (user['patient_id'],)); patient = cur.fetchone()
        cur.execute('SELECT COUNT(*) FROM uploads WHERE patient_id=?', (user['patient_id'],)); total_upload = cur.fetchone()[0]
        cur.execute('SELECT * FROM soap_records WHERE patient_id=? ORDER BY created_at DESC LIMIT 5', (user['patient_id'],)); soaps = cur.fetchall()
        body = '''
        <div class="hero card"><div><h3 style="margin:0">Halo, {{ user['full_name'] or user['username'] }}</h3><p class="muted">Dashboard pasien untuk melihat ringkasan pemeriksaan dan link hasil.</p>{% if patient %}<div class="pill-list"><span class="pill">No RM: {{ patient['nomor_rekam_medis'] }}</span><span class="pill">Status: {{ patient['status_antrian'] }}</span><span class="pill">Total hasil: {{ total_upload }}</span></div>{% endif %}</div></div>
        {% if patient %}
        <div class="g2 grid" style="margin-top:16px">
          <div class="card"><h3>Link Hasil</h3><div class="small muted">Gunakan link token unik ini</div><div class="mono wrap" style="margin:10px 0">{{ request.url_root.rstrip('/') }}{{ url_for('patient_result', token=patient['access_token']) }}</div><a class="btn btn-primary" target="_blank" href="{{ url_for('patient_result', token=patient['access_token']) }}">🔗 Buka Hasil</a></div>
          <div class="card"><h3>Riwayat Pemeriksaan Terbaru</h3>{% if soaps %}{% for s in soaps %}<div class="card" style="padding:12px;margin-bottom:12px"><div class="small muted">{{ fmt_dt(s['created_at']) }}</div><div><strong>A:</strong> {{ s['assessment'] or '-' }}</div><div><strong>P:</strong> {{ s['plan'] or '-' }}</div></div>{% endfor %}{% else %}<div class="empty">Belum ada riwayat.</div>{% endif %}</div>
        </div>
        {% else %}<div class="card"><div class="empty">Akun pasien belum ditautkan ke data pasien.</div></div>{% endif %}
        '''
        return render_page('Dashboard Pasien', body, patient=patient, total_upload=total_upload, soaps=soaps, fmt_dt=fmt_dt)
    cur.execute('SELECT COUNT(*) FROM patients WHERE date(created_at)=date(?)', (today(),)); total_today = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM patients WHERE status_antrian='menunggu'"); waiting = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM patients WHERE status_antrian='diperiksa'"); checked = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM patients WHERE status_antrian='selesai'"); finished = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM uploads'); total_uploads = cur.fetchone()[0]

    # Chart Data: Kunjungan 7 Hari Terakhir
    cur.execute('''
        SELECT date(created_at) as d, COUNT(*) as c 
        FROM patients 
        WHERE created_at >= date('now', '-6 days') 
        GROUP BY d 
        ORDER BY d ASC
    ''')
    daily_rows = cur.fetchall()
    daily_labels = []
    daily_values = []
    for i in range(6, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        daily_labels.append(d)
        val = 0
        for r in daily_rows:
            if r['d'] == d:
                val = r['c']
                break
        daily_values.append(val)

    if user['role'] == 'dokter':
        cur.execute("SELECT * FROM patients WHERE status_antrian IN ('menunggu','diperiksa') ORDER BY created_at ASC LIMIT 10")
    else:
        cur.execute('SELECT * FROM patients ORDER BY created_at DESC LIMIT 8')
    patients_rows = cur.fetchall()
    cur.execute('SELECT * FROM audit_logs ORDER BY id DESC LIMIT 8'); audits = cur.fetchall()

    body = r'''
    <div class="space-y-6">
      <!-- Welcome Banner -->
      <div class="card p-6 bg-gradient-to-r from-emerald-600/20 to-cyan-600/20 border-emerald-500/30">
        <div class="flex flex-col md:flex-row justify-between items-center gap-4">
          <div>
            <h1 class="text-3xl font-black text-white">Dashboard Command Center</h1>
            <p class="text-slate-400 font-medium">Halo, {{ user['full_name'] or user['username'] }}. Pantau operasional klinik secara real-time.</p>
          </div>
          <div class="flex gap-3 no-print">
            {% if user['role'] in ['superadmin','admin'] %}
            <a class="btn btn-primary shadow-lg shadow-emerald-500/20" href="{{ url_for('patient_new') }}">
              <span class="text-lg">➕</span> Input Pasien Baru
            </a>
            {% endif %}
            <a class="btn bg-slate-800 border-slate-700 hover:bg-slate-700" href="{{ url_for('antrian') }}">
              <span class="text-lg">🚶</span> Cek Antrian
            </a>
          </div>
        </div>
      </div>

      <!-- Quick Metrics Grid -->
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <div class="stat group hover:scale-[1.02] transition-transform">
          <div class="flex justify-between items-start">
            <div>
              <div class="text-slate-400 text-xs font-bold uppercase tracking-wider mb-1">Pasien Baru Hari Ini</div>
              <div class="text-4xl font-black text-emerald-400">{{ total_today }}</div>
            </div>
            <div class="p-3 rounded-2xl bg-emerald-500/10 text-emerald-500">📈</div>
          </div>
        </div>
        <div class="stat group hover:scale-[1.02] transition-transform border-amber-500/20">
          <div class="flex justify-between items-start">
            <div>
              <div class="text-slate-400 text-xs font-bold uppercase tracking-wider mb-1">Menunggu Antrian</div>
              <div class="text-4xl font-black text-amber-400">{{ waiting }}</div>
            </div>
            <div class="p-3 rounded-2xl bg-amber-500/10 text-amber-500">⏳</div>
          </div>
        </div>
        <div class="stat group hover:scale-[1.02] transition-transform border-cyan-500/20">
          <div class="flex justify-between items-start">
            <div>
              <div class="text-slate-400 text-xs font-bold uppercase tracking-wider mb-1">Sedang Diperiksa</div>
              <div class="text-4xl font-black text-cyan-400">{{ checked }}</div>
            </div>
            <div class="p-3 rounded-2xl bg-cyan-500/10 text-cyan-500">👨‍⚕️</div>
          </div>
        </div>
        <div class="stat group hover:scale-[1.02] transition-transform border-blue-500/20">
          <div class="flex justify-between items-start">
            <div>
              <div class="text-slate-400 text-xs font-bold uppercase tracking-wider mb-1">Selesai Dilayani</div>
              <div class="text-4xl font-black text-blue-400">{{ finished }}</div>
            </div>
            <div class="p-3 rounded-2xl bg-blue-500/10 text-blue-500">✅</div>
          </div>
        </div>
      </div>

      <!-- Charts Section -->
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div class="lg:col-span-2 card p-6">
          <h3 class="text-lg font-bold mb-4 flex items-center gap-2">
            <span class="w-1.5 h-6 bg-emerald-500 rounded-full"></span>
            Tren Kunjungan 7 Hari Terakhir
          </h3>
          <div class="h-[300px]">
            <canvas id="visitTrendChart"></canvas>
          </div>
        </div>
        <div class="card p-6">
          <h3 class="text-lg font-bold mb-4 flex items-center gap-2">
            <span class="w-1.5 h-6 bg-cyan-500 rounded-full"></span>
            Status Antrian Saat Ini
          </h3>
          <div class="h-[300px] flex items-center justify-center">
            <canvas id="statusPieChart"></canvas>
          </div>
        </div>
      </div>

      <!-- Lists Section -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div class="card p-0 overflow-hidden">
          <div class="p-6 border-b border-white/10 flex justify-between items-center bg-white/5">
            <h3 class="text-lg font-bold m-0">📋 Antrian Aktif / Pasien Terbaru</h3>
            <a href="{{ url_for('patients') }}" class="text-xs font-bold text-emerald-400 hover:underline">Lihat Semua →</a>
          </div>
          <div class="table-wrap">
            <table class="w-full text-sm">
              <thead>
                <tr class="bg-white/5 text-slate-400 uppercase text-[10px] tracking-widest">
                  <th class="px-6 py-3 text-left">Pasien</th>
                  <th class="px-6 py-3 text-left">RM</th>
                  <th class="px-6 py-3 text-center">Status</th>
                  <th class="px-6 py-3 text-right">Aksi</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-white/5">
                {% for p in patients_rows %}
                <tr class="hover:bg-white/5 transition-colors">
                  <td class="px-6 py-4">
                    <div class="font-bold text-white">{{ p['nama_pasien'] }}</div>
                    <div class="text-[10px] text-slate-500">{{ fmt_dt(p['created_at']) }}</div>
                  </td>
                  <td class="px-6 py-4 font-mono text-xs text-slate-300">{{ p['nomor_rekam_medis'] }}</td>
                  <td class="px-6 py-4 text-center">
                    <span class="pill {{ p['status_antrian'] }}">{{ p['status_antrian'] }}</span>
                  </td>
                  <td class="px-6 py-4 text-right">
                    <a class="btn btn-sm bg-emerald-500/10 text-emerald-400 border-emerald-500/20 hover:bg-emerald-500/20" href="{{ url_for('patient_detail', patient_id=p['id']) }}">Periksa</a>
                  </td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
          </div>
          {% if not patients_rows %}
          <div class="p-12 text-center text-slate-500 italic">Belum ada pasien terdaftar.</div>
          {% endif %}
        </div>

        <div class="card p-0 overflow-hidden">
          <div class="p-6 border-b border-white/10 flex justify-between items-center bg-white/5">
            <h3 class="text-lg font-bold m-0">🕵️ Audit Log Aktivitas</h3>
            <a href="{{ url_for('audit_logs_page') }}" class="text-xs font-bold text-slate-400 hover:underline">Semua Log →</a>
          </div>
          <div class="table-wrap">
            <table class="w-full text-sm">
              <thead>
                <tr class="bg-white/5 text-slate-400 uppercase text-[10px] tracking-widest">
                  <th class="px-6 py-3 text-left">Waktu</th>
                  <th class="px-6 py-3 text-left">User</th>
                  <th class="px-6 py-3 text-left">Aksi</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-white/5">
                {% for a in audits %}
                <tr class="hover:bg-white/5 transition-colors">
                  <td class="px-6 py-4 text-xs text-slate-400">{{ fmt_dt(a['created_at']) }}</td>
                  <td class="px-6 py-4 font-bold text-white">{{ a['username'] or '-' }}</td>
                  <td class="px-6 py-4">
                    <div class="font-medium text-slate-200">{{ a['action'] }}</div>
                    <div class="text-[10px] text-slate-500 truncate max-w-[150px]">{{ a['details'] or '' }}</div>
                  </td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
          </div>
          {% if not audits %}
          <div class="p-12 text-center text-slate-500 italic">Belum ada log aktivitas.</div>
          {% endif %}
        </div>
      </div>
    </div>

    <script>
      document.addEventListener('DOMContentLoaded', function() {
        // Visit Trend Chart
        const ctxVisit = document.getElementById('visitTrendChart').getContext('2d');
        new Chart(ctxVisit, {
          type: 'line',
          data: {
            labels: {{ daily_labels|tojson }},
            datasets: [{
              label: 'Pasien Baru',
              data: {{ daily_values|tojson }},
              borderColor: '#22c55e',
              backgroundColor: 'rgba(34, 197, 94, 0.1)',
              borderWidth: 3,
              fill: true,
              tension: 0.4,
              pointRadius: 4,
              pointBackgroundColor: '#22c55e'
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
              y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#7b93b5' } },
              x: { grid: { display: false }, ticks: { color: '#7b93b5' } }
            }
          }
        });

        // Status Pie Chart
        const ctxStatus = document.getElementById('statusPieChart').getContext('2d');
        new Chart(ctxStatus, {
          type: 'doughnut',
          data: {
            labels: ['Menunggu', 'Diperiksa', 'Selesai'],
            datasets: [{
              data: [{{ waiting }}, {{ checked }}, {{ finished }}],
              backgroundColor: ['#f59e0b', '#0ea5e9', '#22c55e'],
              borderWidth: 0,
              hoverOffset: 10
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '70%',
            plugins: {
              legend: { position: 'bottom', labels: { color: '#7b93b5', usePointStyle: true, padding: 20 } }
            }
          }
        });
      });
    </script>
    '''
    return render_page('Dashboard', body, total_today=total_today, waiting=waiting, checked=checked, finished=finished, total_uploads=total_uploads, patients_rows=patients_rows, audits=audits, fmt_dt=fmt_dt, daily_labels=daily_labels, daily_values=daily_values)

@app.route('/antrian')
@role_required('superadmin', 'admin', 'dokter')
def antrian():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM patients WHERE status_antrian IN ('menunggu','diperiksa') AND deleted=0 ORDER BY created_at ASC")
    rows = cur.fetchall()
    body = '''
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px">
            <h3 style="margin:0">Daftar Antrian Aktif</h3>
            <span class="badge">{{ rows|length }} pasien dalam antrian</span>
        </div>
        <div class="small muted mb-4">Estimasi waktu tunggu dihitung berdasarkan rata-rata durasi pelayanan hari ini.</div>
        {% if rows %}
        <table>
            <thead>
                <tr><th>No</th><th>Waktu Daftar</th><th>Pasien</th><th>RM</th><th>Estimasi Tunggu</th><th>Status</th><th>Aksi</th></tr>
            </thead>
            <tbody>
                {% for p in rows %}
                {% set est = hitung_estimasi_tunggu(p['id']) %}
                <tr class="{{ 'bg-emerald-500/5' if p['status_antrian']=='diperiksa' else '' }}">
                    <td class="font-bold text-emerald-500 text-lg">#{{ loop.index }}</td>
                    <td class="small">{{ fmt_dt(p['created_at']) }}</td>
                    <td><strong>{{ p['nama_pasien'] }}</strong><div class="small muted">{{ p['nomor_hp'] or '-' }}</div></td>
                    <td class="mono">{{ p['nomor_rekam_medis'] }}</td>
                    <td>
                      {% if p['status_antrian'] == 'diperiksa' %}
                        <span class="text-emerald-400 font-bold">Sedang Diperiksa</span>
                      {% else %}
                        <span class="text-amber-400 font-bold">~{{ est }} menit</span>
                      {% endif %}
                    </td>
                    <td><span class="badge {{ p['status_antrian'] }}">{{ p['status_antrian'] }}</span></td>
                    <td><a class="btn btn-sm btn-primary" href="{{ url_for('patient_detail', patient_id=p['id']) }}">🏥 Periksa</a></td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}<div class="empty">Antrian hari ini sudah kosong atau sudah diselesaikan semua.</div>{% endif %}
    </div>
    '''
    return render_page('Antrian Hari Ini', body, rows=rows, fmt_dt=fmt_dt)

@app.route('/patients/<int:patient_id>/referral')
@role_required('superadmin', 'admin', 'dokter')
def referral_letter(patient_id):
    patient = get_patient(patient_id)
    if not patient: abort(404)
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT * FROM soap_records WHERE patient_id=? ORDER BY created_at DESC LIMIT 1', (patient_id,))
    soap = cur.fetchone()
    body = '''
    <div class="authbox" style="max-width: 800px; margin: 2rem auto; color: black !important;">
      <div class="card" style="background: white !important; border: 2px solid black; padding: 2.5rem; color: black !important; backdrop-filter: none;">
        <div style="text-align: center; border-bottom: 3px double black; padding-bottom: 1rem; margin-bottom: 2rem;">
          <h1 style="margin: 0; font-size: 24px; text-transform: uppercase;">KLINIK ARISSA USG 4D</h1>
          <p style="margin: 5px 0;">Jl. Alamat Klinik No. 123, Kota Anda</p>
          <p style="margin: 0;">Telp: (021) 1234567 • WA: 0812-3456-7890</p>
        </div>
        <div style="text-align: right; margin-bottom: 1.5rem;">{{ today_label }}</div>
        <div style="margin-bottom: 2rem;"><p style="margin: 0;">Hal: <strong>Surat Rujukan Medis</strong></p><p style="margin: 5px 0 0;">Kepada Yth,</p><p style="margin: 0;"><strong>TS Dokter Spesialis Kandungan / RS Besar</strong></p><p style="margin: 0;">Di Tempat</p></div>
        <p>Dengan hormat, Mohon pemeriksaan dan tatalaksana lebih lanjut terhadap pasien berikut:</p>
        <table style="width: 100%; margin: 1.5rem 0; border: none; color: black !important;">
          <tr><td style="width: 180px; padding: 5px 0; border:none">Nama Pasien</td><td style="border:none">: <strong>{{ patient['nama_pasien'] }}</strong></td></tr>
          <tr><td style="padding: 5px 0; border:none">No. Rekam Medis</td><td style="border:none">: {{ patient['nomor_rekam_medis'] }}</td></tr>
          <tr><td style="padding: 5px 0; border:none">Umur</td><td style="border:none">: {{ patient['umur'] }}</td></tr>
          <tr><td style="padding: 5px 0; border:none">Alamat</td><td style="border:none">: {{ patient['alamat'] }}</td></tr>
        </table>
        <div style="margin-bottom: 1.5rem;"><p style="margin-bottom: 5px;"><strong>Hasil Pemeriksaan Terakhir:</strong></p>
          <div style="padding: 1rem; background: #f9f9f9; border: 1px solid #ddd; border-radius: 8px;">
            <p style="margin: 0;"><strong>Diagnosis:</strong> {{ soap['assessment'] if soap else 'Evaluasi Kehamilan' }}</p>
            {% if soap %}<p style="margin: 5px 0 0;"><strong>Tanda Vital:</strong> TD {{ soap['td_sistolik'] }}/{{ soap['td_diastolik'] }} mmHg, DJJ {{ soap['detak_jantung_janin'] }} bpm</p>
            <p style="margin: 5px 0 0;"><strong>Usia Hamil:</strong> {{ soap['usia_kehamilan'] }}</p>{% endif %}
          </div>
        </div>
        <p>Atas bantuan dan kerjasamanya, kami ucapkan terima kasih.</p>
        <div style="margin-top: 4rem; text-align: right;"><p style="margin: 0;">Hormat kami,</p><div style="height: 80px;"></div><p style="margin: 0;">( ________________________ )</p><p style="margin: 0; font-size: 12px; color: #666;">Dokter Pemeriksa / Klinik Arissa</p></div>
        <div class="no-print" style="margin-top: 3rem; text-align: center;"><button class="btn btn-primary" onclick="window.print()">🖨️ Cetak Surat Rujukan</button><a class="btn" href="{{ url_for('patient_detail', patient_id=patient['id']) }}">Kembali</a></div>
      </div>
    </div>
    '''
    return render_template_string('<!doctype html><html><head><title>Surat Rujukan - ' + patient['nama_pasien'] + '</title><script src="https://cdn.tailwindcss.com"></script><style>@media print { .no-print { display: none; } body { background: white; } }</style></head><body class="bg-slate-100">' + body + '</body></html>', 
                                 patient=patient, soap=soap, today_label=datetime.now().strftime("%d %B %Y"))

@app.route('/antrian-old')
@role_required('superadmin', 'admin', 'dokter')
def antrian_old():
    conn = get_db(); cur = conn.cursor()
    # Hanya tampilkan antrian aktif (menunggu/diperiksa) urut dari yang paling lama (FIFO)
    cur.execute("SELECT * FROM patients WHERE status_antrian IN ('menunggu','diperiksa') ORDER BY created_at ASC")
    rows = cur.fetchall()
    body = '''
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px">
            <h3 style="margin:0">Daftar Antrian Aktif</h3>
            <span class="badge">{{ rows|length }} pasien dalam antrian</span>
        </div>
        <div class="small muted mb-4">Pasien muncul berdasarkan urutan waktu pendaftaran (paling awal di atas).</div>
        {% if rows %}
        <table>
            <thead>
                <tr><th>No</th><th>Waktu Daftar</th><th>Pasien</th><th>RM</th><th>Dokter</th><th>Status</th><th>Aksi</th></tr>
            </thead>
            <tbody>
                {% for p in rows %}
                <tr class="{{ 'bg-emerald-500/5' if p['status_antrian']=='diperiksa' else '' }}">
                    <td class="font-bold text-emerald-500 text-lg">#{{ loop.index }}</td>
                    <td class="small">{{ fmt_dt(p['created_at']) }}</td>
                    <td><strong>{{ p['nama_pasien'] }}</strong><div class="small muted">{{ p['nomor_hp'] or '-' }}</div></td>
                    <td class="mono">{{ p['nomor_rekam_medis'] }}</td>
                    <td>{{ p['dokter_tujuan'] or '-' }}</td>
                    <td><span class="badge {{ p['status_antrian'] }}">{{ p['status_antrian'] }}</span></td>
                    <td><a class="btn btn-sm btn-primary" href="{{ url_for('patient_detail', patient_id=p['id']) }}">🏥 Periksa</a></td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% else %}<div class="empty">Antrian hari ini sudah kosong atau sudah diselesaikan semua.</div>{% endif %}
    </div>
    '''
    return render_page('Antrian Hari Ini', body, rows=rows, fmt_dt=fmt_dt)


@app.route('/antrian-tv')
@role_required('superadmin', 'admin', 'dokter')
def antrian_tv():
    conn = get_db(); cur = conn.cursor()
    # Pasien yang sedang diperiksa (Panggilan Utama)
    cur.execute("SELECT nama_pasien, dokter_tujuan FROM patients WHERE status_antrian='diperiksa' ORDER BY updated_at DESC LIMIT 1")
    current = cur.fetchone()
    
    # Daftar Antrian Menunggu (7 Pasien Berikutnya - lebih banyak)
    cur.execute("SELECT nama_pasien, nomor_rekam_medis FROM patients WHERE status_antrian='menunggu' ORDER BY created_at ASC LIMIT 7")
    waiting = cur.fetchall()
    
    # Statistik Cepat
    cur.execute("SELECT COUNT(*) FROM patients WHERE date(created_at) = date('now')")
    total_today = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM patients WHERE status_antrian='selesai' AND date(updated_at)=date('now')")
    done_today = cur.fetchone()[0]

    body_tpl = """
    <!doctype html>
    <html lang="id">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>TV Antrian - {{ app_name }}</title>
      <script src="https://cdn.tailwindcss.com"></script>
      <style>
        @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;700;800;900&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { 
            font-family: 'Plus Jakarta Sans', sans-serif; 
            background: #050f1f;
            min-height: 100vh;
            overflow: hidden;
        }
        /* Animated gradient background */
        .tv-bg {
            position: fixed; inset: 0;
            background: 
                radial-gradient(ellipse 70% 50% at 15% 10%, rgba(34,197,94,.12), transparent),
                radial-gradient(ellipse 50% 60% at 85% 90%, rgba(14,165,233,.12), transparent),
                radial-gradient(ellipse 40% 40% at 50% 50%, rgba(168,85,247,.05), transparent),
                #050f1f;
            z-index: 0;
        }
        /* Animated floating particles */
        .particle {
            position: fixed;
            border-radius: 50%;
            pointer-events: none;
            z-index: 0;
            animation: particleFloat 12s ease-in-out infinite;
        }
        .particle:nth-child(1) { width: 300px; height: 300px; background: radial-gradient(circle, rgba(34,197,94,.08), transparent 70%); top: -100px; left: -100px; animation-duration: 14s; }
        .particle:nth-child(2) { width: 250px; height: 250px; background: radial-gradient(circle, rgba(14,165,233,.08), transparent 70%); bottom: -80px; right: -80px; animation-duration: 18s; }
        .particle:nth-child(3) { width: 180px; height: 180px; background: radial-gradient(circle, rgba(168,85,247,.06), transparent 70%); top: 40%; left: 80%; animation-duration: 16s; }
        @keyframes particleFloat {
            0%,100% { transform: translate(0,0) scale(1); }
            25% { transform: translate(30px,-20px) scale(1.05); }
            50% { transform: translate(-20px,10px) scale(.95); }
            75% { transform: translate(10px,30px) scale(1.02); }
        }
        /* Grid dots overlay */
        .tv-grid {
            position: fixed; inset: 0; z-index: 0;
            background-image: radial-gradient(rgba(255,255,255,.04) 1px, transparent 1px);
            background-size: 50px 50px;
            mask-image: radial-gradient(ellipse 80% 80% at 50% 50%, #000 30%, transparent 100%);
        }
        .tv-content { position: relative; z-index: 1; height: 100vh; display: flex; flex-direction: column; padding: 1.5rem; gap: 1.5rem; }
        
        /* Header */
        .tv-header {
            display: flex; justify-content: space-between; align-items: center;
            padding: 1rem 2rem;
            background: rgba(255,255,255,.04);
            border: 1px solid rgba(255,255,255,.06);
            border-radius: 2rem;
            backdrop-filter: blur(20px);
            box-shadow: 0 20px 60px rgba(0,0,0,.3);
            flex-shrink: 0;
        }
        .tv-brand { display: flex; align-items: center; gap: 1.2rem; }
        .tv-logo {
            width: 56px; height: 56px;
            background: linear-gradient(135deg, #22c55e, #0ea5e9);
            border-radius: 1rem;
            display: flex; align-items: center; justify-content: center;
            font-size: 1.5rem; font-weight: 900; color: #fff;
            box-shadow: 0 10px 30px rgba(34,197,94,.3);
        }
        .tv-clock { 
            font-size: 3.5rem; font-weight: 900; font-family: monospace;
            color: #fff; line-height: 1;
            text-shadow: 0 0 40px rgba(14,165,233,.3);
        }
        .tv-date { color: rgba(255,255,255,.4); font-weight: 700; letter-spacing: .1em; font-size: .8rem; text-align: right; }

        /* Main grid */
        .tv-main { flex: 1; display: grid; grid-template-columns: 1.4fr 1fr; gap: 1.5rem; min-height: 0; }

        /* Left panel - Current patient */
        .tv-current {
            display: flex; flex-direction: column;
            background: linear-gradient(135deg, rgba(34,197,94,.08), rgba(34,197,94,.02));
            border: 1px solid rgba(34,197,94,.15);
            border-radius: 2.5rem;
            padding: 2.5rem;
            backdrop-filter: blur(20px);
            box-shadow: 0 20px 60px rgba(0,0,0,.25);
            position: relative;
            overflow: hidden;
        }
        .tv-current::before {
            content: '';
            position: absolute; top: -50%; left: -50%; width: 200%; height: 200%;
            background: conic-gradient(from 0deg, transparent, rgba(34,197,94,.03), transparent, rgba(14,165,233,.03), transparent);
            animation: rotateBg 20s linear infinite;
        }
        @keyframes rotateBg { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        
        .tv-current-inner { position: relative; z-index: 1; flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 1rem; }
        
        .tv-badge {
            display: inline-flex; align-items: center; gap: .6rem;
            padding: .6rem 1.5rem;
            background: #22c55e; color: #050f1f;
            border-radius: 100px;
            font-weight: 900; font-size: .85rem;
            letter-spacing: .15em;
            text-transform: uppercase;
            box-shadow: 0 8px 30px rgba(34,197,94,.3);
            animation: badgePulse 2s ease-in-out infinite;
        }
        @keyframes badgePulse {
            0%,100% { box-shadow: 0 8px 30px rgba(34,197,94,.3); }
            50% { box-shadow: 0 8px 50px rgba(34,197,94,.6); }
        }
        
        .tv-patient-label { color: rgba(255,255,255,.35); font-weight: 800; font-size: .85rem; letter-spacing: .3em; text-transform: uppercase; margin-top: .5rem; }
        .tv-patient-name {
            font-size: clamp(3rem, 6vw, 6rem);
            font-weight: 900;
            color: #fff;
            line-height: 1.1;
            text-shadow: 0 4px 30px rgba(0,0,0,.3);
        }
        .tv-divider { width: 120px; height: 3px; background: linear-gradient(90deg, #22c55e, #0ea5e9); border-radius: 10px; opacity: .5; margin: .5rem 0; }
        .tv-doctor-label { color: rgba(255,255,255,.35); font-weight: 800; font-size: .8rem; letter-spacing: .3em; text-transform: uppercase; }
        .tv-doctor-name { 
            font-size: 1.8rem; font-weight: 800; 
            background: linear-gradient(135deg, #0ea5e9, #22c55e);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        /* Stats bar */
        .tv-stats {
            display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem;
            padding: 1rem 1.5rem;
            background: rgba(255,255,255,.03);
            border: 1px solid rgba(255,255,255,.06);
            border-radius: 1.5rem;
            margin-top: auto;
            position: relative; z-index: 1;
        }
        .tv-stat { text-align: center; }
        .tv-stat-value { font-size: 2rem; font-weight: 900; line-height: 1.2; }
        .tv-stat-label { font-size: .65rem; color: rgba(255,255,255,.35); font-weight: 700; letter-spacing: .15em; text-transform: uppercase; }

        /* Right panel - Queue list */
        .tv-queue {
            background: rgba(255,255,255,.03);
            border: 1px solid rgba(255,255,255,.06);
            border-radius: 2.5rem;
            padding: 2rem;
            backdrop-filter: blur(20px);
            box-shadow: 0 20px 60px rgba(0,0,0,.25);
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }
        .tv-queue-title {
            display: flex; align-items: center; gap: .8rem;
            font-size: 1.2rem; font-weight: 900; color: #0ea5e9;
            margin-bottom: 1.5rem;
            flex-shrink: 0;
        }
        .tv-queue-title .bar { width: 4px; height: 24px; background: #0ea5e9; border-radius: 10px; box-shadow: 0 0 20px rgba(14,165,233,.4); }

        .tv-queue-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: .6rem; scrollbar-width: thin; scrollbar-color: rgba(255,255,255,.1) transparent; }
        .tv-queue-list::-webkit-scrollbar { width: 4px; }
        .tv-queue-list::-webkit-scrollbar-thumb { background: rgba(255,255,255,.1); border-radius: 10px; }

        .tv-q-item {
            display: flex; align-items: center; gap: 1rem;
            padding: 1rem 1.2rem;
            background: rgba(255,255,255,.04);
            border: 1px solid rgba(255,255,255,.05);
            border-radius: 1.2rem;
            transition: all .3s ease;
            animation: slideIn .4s ease both;
        }
        .tv-q-item:nth-child(1) { animation-delay: .05s; }
        .tv-q-item:nth-child(2) { animation-delay: .10s; }
        .tv-q-item:nth-child(3) { animation-delay: .15s; }
        .tv-q-item:nth-child(4) { animation-delay: .20s; }
        .tv-q-item:nth-child(5) { animation-delay: .25s; }
        .tv-q-item:nth-child(6) { animation-delay: .30s; }
        .tv-q-item:nth-child(7) { animation-delay: .35s; }
        @keyframes slideIn { from { opacity:0; transform:translateX(-20px); } to { opacity:1; transform:none; } }
        
        .tv-q-num {
            width: 48px; height: 48px; flex-shrink: 0;
            display: flex; align-items: center; justify-content: center;
            background: rgba(14,165,233,.12);
            border-radius: 1rem;
            font-size: 1.3rem; font-weight: 900; color: #0ea5e9;
            transition: all .3s;
        }
        .tv-q-item:hover { background: rgba(34,197,94,.08); border-color: rgba(34,197,94,.15); }
        .tv-q-item:hover .tv-q-num { background: #22c55e; color: #050f1f; }
        .tv-q-name { font-size: 1.2rem; font-weight: 800; color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .tv-q-rm { font-size: .75rem; color: rgba(255,255,255,.3); font-family: monospace; margin-top: 2px; }

        .tv-empty {
            flex: 1; display: flex; align-items: center; justify-content: center;
            color: rgba(255,255,255,.2); font-size: 1.5rem; font-style: italic;
        }

        /* Footer */
        .tv-footer {
            text-align: center; padding: .5rem;
            color: rgba(255,255,255,.15); font-size: .6rem;
            font-weight: 700; letter-spacing: .5em; text-transform: uppercase;
            flex-shrink: 0;
            animation: footerPulse 3s ease-in-out infinite;
        }
        @keyframes footerPulse { 0%,100% { opacity: 1; } 50% { opacity: .5; } }

        /* Responsive */
        @media (max-width: 1024px) {
            .tv-main { grid-template-columns: 1fr; }
            .tv-patient-name { font-size: clamp(2rem, 5vw, 4rem); }
            .tv-clock { font-size: 2.5rem; }
            .tv-content { padding: 1rem; gap: 1rem; }
        }
      </style>
    </head>
    <body>
        <div class="particle"></div>
        <div class="particle"></div>
        <div class="particle"></div>
        <div class="tv-grid"></div>
        <div class="tv-bg"></div>
        
        <div class="tv-content">
            <!-- Header -->
            <header class="tv-header">
                <div class="tv-brand">
                    <div class="tv-logo">USG</div>
                    <div>
                        <div style="font-size:1.6rem;font-weight:900;color:#fff;letter-spacing:-.5px;line-height:1.2">{{ app_name }}</div>
                        <div style="font-size:.7rem;color:rgba(255,255,255,.3);font-weight:700;letter-spacing:.2em">SISTEM INFORMASI ANTRIAN</div>
                    </div>
                </div>
                <div class="text-right">
                    <div id="tv-clock" class="tv-clock">00:00:00</div>
                    <div class="tv-date">{{ today_label }}</div>
                </div>
            </header>

            <!-- Main -->
            <div class="tv-main">
                <!-- Current Patient -->
                <div class="tv-current">
                    {% if current %}
                    <div style="position:absolute;top:1.5rem;left:1.5rem;z-index:2">
                        <span class="tv-badge">🔴 LIVE &bull; SEDANG DIPERIKSA</span>
                    </div>
                    {% endif %}
                    
                    <div class="tv-current-inner">
                        {% if current %}
                            <div class="tv-patient-label">Nama Pasien</div>
                            <div class="tv-patient-name">{{ current['nama_pasien'] }}</div>
                            <div class="tv-divider"></div>
                            <div class="tv-doctor-label">Dokter Tujuan</div>
                            <div class="tv-doctor-name">{{ current['dokter_tujuan'] or '—' }}</div>
                        {% else %}
                            <div style="font-size:4rem;margin-bottom:1rem;opacity:.3">🛋️</div>
                            <div style="font-size:2rem;font-weight:800;color:rgba(255,255,255,.25);">Menunggu Pasien Berikutnya</div>
                            <div style="font-size:.9rem;color:rgba(255,255,255,.15);margin-top:.5rem">Silakan menunggu, kami akan segera memanggil Anda</div>
                        {% endif %}
                    </div>

                    <!-- Stats -->
                    <div class="tv-stats">
                        <div class="tv-stat">
                            <div class="tv-stat-value" style="color:#22c55e">{{ total_today }}</div>
                            <div class="tv-stat-label">Total Hari Ini</div>
                        </div>
                        <div class="tv-stat">
                            <div class="tv-stat-value" style="color:#0ea5e9">{{ waiting|length }}</div>
                            <div class="tv-stat-label">Menunggu</div>
                        </div>
                        <div class="tv-stat">
                            <div class="tv-stat-value" style="color:#a78bfa">{{ done_today }}</div>
                            <div class="tv-stat-label">Selesai</div>
                        </div>
                    </div>
                </div>

                <!-- Queue List -->
                <div class="tv-queue">
                    <div class="tv-queue-title">
                        <span class="bar"></span>
                        ANTRIAN BERIKUTNYA
                        <span style="margin-left:auto;font-size:.75rem;color:rgba(255,255,255,.25);font-weight:700">{{ waiting|length }} pasien</span>
                    </div>

                    <div class="tv-queue-list">
                        {% for p in waiting %}
                        <div class="tv-q-item">
                            <div class="tv-q-num">{{ loop.index }}</div>
                            <div>
                                <div class="tv-q-name">{{ p['nama_pasien'] }}</div>
                                <div class="tv-q-rm">{{ p['nomor_rekam_medis'] }}</div>
                            </div>
                        </div>
                        {% else %}
                        <div class="tv-empty">✅ Semua antrian telah selesai</div>
                        {% endfor %}
                    </div>
                </div>
            </div>

            <footer class="tv-footer">
                Klinik Arissa USG 4D Premium &bull; Sistem Informasi Layanan Terpadu
            </footer>
        </div>

        <script>
            // Live clock
            function updateClock() {
                const now = new Date();
                const h = String(now.getHours()).padStart(2, '0');
                const m = String(now.getMinutes()).padStart(2, '0');
                const s = String(now.getSeconds()).padStart(2, '0');
                document.getElementById('tv-clock').textContent = h + ":" + m + ":" + s;
            }
            setInterval(updateClock, 1000);
            updateClock();
            
            // Auto-refresh every 15 seconds
            setTimeout(() => { location.reload(); }, 15000);
            
            // Fade in on load
            document.body.style.opacity = '0';
            document.body.style.transition = 'opacity .6s ease';
            window.addEventListener('load', () => { document.body.style.opacity = '1'; });
        </script>
    </body>
    </html>
    """
    return render_template_string(body_tpl, current=current, waiting=waiting, total_today=total_today, done_today=done_today,
                                 today_label=datetime.now().strftime("%A, %d %B %Y"), app_name=APP_NAME)


@app.route('/patients')
@role_required('superadmin', 'admin', 'dokter')
def patients():
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    doctor = request.args.get('doctor', '').strip()
    from_date = request.args.get('from_date', '').strip()
    to_date = request.args.get('to_date', '').strip()
    sort_by = request.args.get('sort_by', 'newest').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    conn = get_db(); cur = conn.cursor()
    # Hanya ambil kolom yang diperlukan, bukan SELECT * — lebih cepat & hemat memori
    select_cols = "id, nama_pasien, nomor_rekam_medis, nik, nomor_hp, jenis_layanan, dokter_tujuan, status_antrian, created_at, tanggal_lahir, umur"
    cur.execute("SELECT full_name,username FROM users WHERE role='dokter' AND active=1 ORDER BY full_name,username"); doctors = cur.fetchall()
    
    where = 'WHERE deleted=0'; params = []
    if q:
        like = '%' + q + '%'
        where += ' AND (nama_pasien LIKE ? OR nomor_rekam_medis LIKE ? OR nik LIKE ? OR nomor_hp LIKE ?)'
        params += [like, like, like, like]
    if status:
        where += ' AND status_antrian=?'; params.append(status)
    if doctor:
        where += ' AND dokter_tujuan=?'; params.append(doctor)
    if from_date:
        where += ' AND date(created_at) >= ?'; params.append(from_date)
    if to_date:
        where += ' AND date(created_at) <= ?'; params.append(to_date)
    
    if sort_by == 'oldest':
        order = " ORDER BY created_at ASC"
    elif sort_by == 'name':
        order = " ORDER BY nama_pasien ASC"
    else:
        order = " ORDER BY CASE status_antrian WHEN 'menunggu' THEN 1 WHEN 'diperiksa' THEN 2 ELSE 3 END, created_at DESC"
    
    # COUNT query — cepat karena hanya hitung jumlah baris
    cur.execute('SELECT COUNT(*) FROM patients ' + where, tuple(params))
    total = cur.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    
    # DATA query — hanya ambil 20 baris + kolom minimal
    sql = 'SELECT ' + select_cols + ' FROM patients ' + where + order + ' LIMIT ? OFFSET ?'
    cur.execute(sql, tuple(params) + (per_page, offset)); rows = cur.fetchall()
    
    # Hitung end_count untuk display (hindari min() yang tidak ada di Jinja2)
    end_count = offset + per_page
    if end_count > total:
        end_count = total
    
    # Hitung rentang halaman untuk pagination compact
    max_visible = 7
    half = max_visible // 2
    start_page = max(1, page - half)
    end_page = min(total_pages, page + half)
    if end_page - start_page < max_visible - 1:
        if start_page == 1:
            end_page = min(total_pages, start_page + max_visible - 1)
        else:
            start_page = max(1, end_page - max_visible + 1)
    pages_range = list(range(start_page, end_page + 1))
    
    body = '''
    <div class="space-y-4">
      <!-- Filter Card -->
      <div class="card no-print">
        <form class="space-y-3" id="filterForm">
          <div class="flex flex-wrap gap-2 items-end">
            <div class="flex-1 min-w-[180px]">
              <label class="text-[10px] uppercase tracking-wider font-bold text-slate-500 mb-1">🔍 Cari Pasien</label>
              <input class="input py-2" name="q" value="{{ q }}" placeholder="Nama / RM / NIK / No. HP...">
            </div>
            <div>
              <label class="text-[10px] uppercase tracking-wider font-bold text-slate-500 mb-1">Status</label>
              <select class="select py-2" name="status">
                <option value="">Semua Status</option>
                {% for s in ['menunggu','diperiksa','selesai'] %}<option value="{{ s }}" {{ 'selected' if status==s else '' }}>{{ s }}</option>{% endfor %}
              </select>
            </div>
            <div>
              <label class="text-[10px] uppercase tracking-wider font-bold text-slate-500 mb-1">Dokter</label>
              <select class="select py-2" name="doctor">
                <option value="">Semua Dokter</option>
                {% for d in doctors %}{% set dn = d['full_name'] or d['username'] %}<option value="{{ dn }}" {{ 'selected' if doctor==dn else '' }}>{{ dn }}</option>{% endfor %}
              </select>
            </div>
            <div>
              <label class="text-[10px] uppercase tracking-wider font-bold text-slate-500 mb-1">Urutkan</label>
              <select class="select py-2" name="sort_by">
                <option value="newest" {{ 'selected' if sort_by=='newest' else '' }}>Terbaru</option>
                <option value="oldest" {{ 'selected' if sort_by=='oldest' else '' }}>Terlama</option>
                <option value="name" {{ 'selected' if sort_by=='name' else '' }}>Nama A-Z</option>
              </select>
            </div>
            <button class="btn btn-primary py-2">🔍 Filter</button>
            <a class="btn py-2" href="{{ url_for('patients') }}">↻ Reset</a>
            {% if current_user['role'] in ['superadmin','admin'] %}
            <a class="btn btn-primary py-2" href="{{ url_for('patient_new') }}">➕ Baru</a>
            <a class="btn py-2" href="{{ url_for('patients_deleted') }}">🗂️ Arsip</a>
            {% endif %}
          </div>
          <!-- Date Filter (collapsible) -->
          <div class="flex flex-wrap gap-2 items-end border-t border-white/5 pt-2">
            <div>
              <label class="text-[10px] uppercase tracking-wider font-bold text-slate-500">Dari</label>
              <input class="input py-1.5" type="date" name="from_date" value="{{ from_date }}">
            </div>
            <div>
              <label class="text-[10px] uppercase tracking-wider font-bold text-slate-500">Sampai</label>
              <input class="input py-1.5" type="date" name="to_date" value="{{ to_date }}">
            </div>
            <div class="text-[11px] text-slate-500 ml-auto">
              {% if total > 0 %}
              Menampilkan {{ offset+1 }}-{{ end_count }} dari <strong class="text-white">{{ total }}</strong> pasien
              {% endif %}
            </div>
          </div>
        </form>
      </div>

      <!-- Data Table -->
      <div class="card p-0 overflow-hidden">
        {% if rows %}
        <div class="table-wrap">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-white/5 text-slate-400 uppercase text-[10px] tracking-widest border-b border-white/10">
                <th class="px-4 py-3 text-right min-w-[120px]">Aksi</th>
                <th class="px-4 py-3 text-left w-8">#</th>
                <th class="px-4 py-3 text-left min-w-[140px]">Pasien</th>
                <th class="px-4 py-3 text-left">RM</th>
                <th class="px-4 py-3 text-left hidden md:table-cell">HP / NIK</th>
                <th class="px-4 py-3 text-left hidden md:table-cell">Layanan</th>
                <th class="px-4 py-3 text-left hidden lg:table-cell">Dokter</th>
                <th class="px-4 py-3 text-center">Status</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-white/5">
              {% for p in rows %}
              <tr class="hover:bg-white/5 transition-colors">
                <td class="px-4 py-3 text-left" style="position:sticky;left:0;z-index:3;background:var(--bg);">
                  <div class="flex gap-1.5 flex-nowrap">
                    <a class="btn btn-sm text-[10px] px-2 py-1 btn-primary" href="{{ url_for('patient_history', patient_id=p['id']) }}" title="Rekam Medis">📋 RM</a>
                    <a class="btn btn-sm text-[10px] px-2 py-1" href="{{ url_for('patient_new', edit=p['id']) }}" title="Edit">✏️</a>
                    <form method="post" action="{{ url_for('patient_detail', patient_id=p['id']) }}" style="display:inline" onsubmit="return confirm('Antrikan {{ p['nama_pasien'] }}?')">
                      <input type="hidden" name="action" value="add_to_queue">
                      <button class="btn btn-sm btn-primary text-[10px] px-2 py-1" title="Antrikan">🚶</button>
                    </form>
                    {% if current_user['role'] == 'superadmin' %}
                    <form method="post" action="{{ url_for('patient_delete', patient_id=p['id']) }}" style="display:inline" onsubmit="return confirm('Hapus {{ p['nama_pasien'] }}?')">
                      <button class="btn btn-sm bg-red-600/20 text-red-400 border-red-500/30 text-[10px] px-2 py-1" title="Hapus">🗑️</button>
                    </form>
                    {% endif %}
                  </div>
                </td>
                <td class="px-4 py-3 text-xs text-slate-500">{{ offset + loop.index }}</td>
                <td class="px-4 py-3">
                  <div class="font-bold text-white text-sm">{{ p['nama_pasien'] }}</div>
                  <div class="text-[10px] text-slate-500">{{ fmt_dt(p['created_at']) }}</div>
                </td>
                <td class="px-4 py-3 font-mono text-xs text-slate-300">{{ p['nomor_rekam_medis'] }}</td>
                <td class="px-4 py-3 text-xs text-slate-400 hidden md:table-cell">{{ p['nomor_hp'] or '-' }}<br><span class="text-[10px]">{{ p['nik'] or '' }}</span></td>
                <td class="px-4 py-3 text-xs hidden md:table-cell">{{ p['jenis_layanan'] or '-' }}</td>
                <td class="px-4 py-3 text-xs hidden lg:table-cell">{{ p['dokter_tujuan'] or '-' }}</td>
                <td class="px-4 py-3 text-center">
                  <span class="pill text-[10px] {{ p['status_antrian'] }}">{{ p['status_antrian'] }}</span>
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>

        <!-- Pagination -->
        {% if total_pages > 1 %}
        <div class="flex flex-wrap items-center justify-center gap-1.5 px-4 py-4 border-t border-white/5 bg-white/5">
          <!-- First -->
          <a class="btn btn-sm text-[10px] px-2 py-1 {{ 'opacity-40 pointer-events-none' if page==1 }}" href="?page=1&q={{ q }}&status={{ status }}&doctor={{ doctor }}&sort_by={{ sort_by }}&from_date={{ from_date }}&to_date={{ to_date }}">««</a>
          <!-- Prev -->
          <a class="btn btn-sm text-[10px] px-2 py-1 {{ 'opacity-40 pointer-events-none' if page==1 }}" href="?page={{ page-1 }}&q={{ q }}&status={{ status }}&doctor={{ doctor }}&sort_by={{ sort_by }}&from_date={{ from_date }}&to_date={{ to_date }}">«</a>
          <!-- Pages -->
          {% for pn in pages_range %}
          <a class="btn btn-sm text-[10px] px-3 py-1 {{ 'btn-primary' if pn==page else '' }}" href="?page={{ pn }}&q={{ q }}&status={{ status }}&doctor={{ doctor }}&sort_by={{ sort_by }}&from_date={{ from_date }}&to_date={{ to_date }}">{{ pn }}</a>
          {% endfor %}
          <!-- Next -->
          <a class="btn btn-sm text-[10px] px-2 py-1 {{ 'opacity-40 pointer-events-none' if page==total_pages }}" href="?page={{ page+1 }}&q={{ q }}&status={{ status }}&doctor={{ doctor }}&sort_by={{ sort_by }}&from_date={{ from_date }}&to_date={{ to_date }}">»</a>
          <!-- Last -->
          <a class="btn btn-sm text-[10px] px-2 py-1 {{ 'opacity-40 pointer-events-none' if page==total_pages }}" href="?page={{ total_pages }}&q={{ q }}&status={{ status }}&doctor={{ doctor }}&sort_by={{ sort_by }}&from_date={{ from_date }}&to_date={{ to_date }}">»»</a>
          <!-- Info -->
          <span class="text-[10px] text-slate-500 ml-2">Hal {{ page }}/{{ total_pages }}</span>
        </div>
        {% endif %}
        {% else %}
        <div class="flex flex-col items-center justify-center py-20 text-slate-500">
          <div class="text-4xl mb-3">📭</div>
          <div class="font-medium">Tidak ada data pasien ditemukan</div>
          <div class="text-xs mt-1">Coba ubah kata kunci atau filter pencarian</div>
        </div>
        {% endif %}
      </div>
    </div>
    '''
    return render_page('Data Pasien', body, q=q, status=status, doctor=doctor, from_date=from_date, to_date=to_date, sort_by=sort_by, end_count=end_count,
                       doctors=doctors, rows=rows, total=total, page=page, total_pages=total_pages, pages_range=pages_range,
                       offset=offset, per_page=per_page, fmt_dt=fmt_dt)


@app.route('/patients/new', methods=['GET', 'POST'])
@role_required('superadmin', 'admin')
def patient_new():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT full_name,username FROM users WHERE role='dokter' AND active=1 ORDER BY full_name,username"); doctors = cur.fetchall()
    # Load master options untuk dropdown
    cur.execute('SELECT category, options_text FROM master_options')
    master_rows = cur.fetchall()
    master_opts = {}
    for r in master_rows:
        opts = [o.strip() for o in (r['options_text'] or '').split(',') if o.strip()]
        master_opts[r['category']] = opts
    goldar_opts = master_opts.get('golongan_darah', ['A','B','AB','O'])
    layanan_opts = master_opts.get('jenis_layanan', ['Umum','BPJS','Asuransi'])
    pekerjaan_opts = master_opts.get('pekerjaan', ['Karyawan','Wiraswasta','PNS','Pelajar','Mahasiswa','Ibu Rumah Tangga'])
    edit_id = request.args.get('edit', type=int)
    edit_patient = None
    if edit_id:
        edit_patient = get_patient(edit_id)
    if request.method == 'POST':
        f = request.form
        nama = f.get('nama_pasien', '').strip()
        rm = f.get('nomor_rekam_medis', '').strip() or rm_auto()
        edit_pid = f.get('edit_id', '').strip()
        if not nama:
            flash('Nama pasien wajib diisi.', 'danger')
        elif edit_pid:
            # UPDATE existing patient
            try:
                cur.execute('''UPDATE patients SET nama_pasien=?,nomor_rekam_medis=?,nik=?,tanggal_lahir=?,umur=?,alamat=?,nomor_hp=?,golongan_darah=?,status_perkawinan=?,pekerjaan=?,nama_keluarga=?,jenis_layanan=?,dokter_tujuan=?,prioritas=?,status_antrian=?,tinggi_badan=?,berat_badan=?,lingkar_perut=?,bmi=?,updated_at=?
                               WHERE id=?''',
                            (nama, rm, f.get('nik','').strip(), f.get('tanggal_lahir','').strip(), f.get('umur','').strip(), f.get('alamat','').strip(), f.get('nomor_hp','').strip(), f.get('golongan_darah','').strip(), f.get('status_perkawinan','').strip(), f.get('pekerjaan','').strip(), f.get('nama_keluarga','').strip(), f.get('jenis_layanan','').strip(), f.get('dokter_tujuan','').strip(), f.get('prioritas','Non-urgent').strip(), f.get('status_antrian','menunggu').strip(), f.get('tinggi_badan','') or None, f.get('berat_badan','') or None, f.get('lingkar_perut','') or None, f.get('bmi','') or None, now(), int(edit_pid)))
                conn.commit()
                log_action('UPDATE_PATIENT', 'Update pasien #{} {}'.format(edit_pid, nama))
                flash('Data pasien berhasil diperbarui.', 'success')
                return redirect(url_for('patient_detail', patient_id=int(edit_pid)))
            except sqlite3.IntegrityError:
                flash('Nomor rekam medis sudah digunakan.', 'danger')
        else:
            try:
                cur.execute('''INSERT INTO patients (nama_pasien,nomor_rekam_medis,nik,tanggal_lahir,umur,alamat,nomor_hp,golongan_darah,status_perkawinan,pekerjaan,nama_keluarga,jenis_layanan,dokter_tujuan,prioritas,status_antrian,tinggi_badan,berat_badan,lingkar_perut,bmi,access_token,created_by,created_at,updated_at)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                            (nama, rm, f.get('nik','').strip(), f.get('tanggal_lahir','').strip(), f.get('umur','').strip(), f.get('alamat','').strip(), f.get('nomor_hp','').strip(), f.get('golongan_darah','').strip(), f.get('status_perkawinan','').strip(), f.get('pekerjaan','').strip(), f.get('nama_keluarga','').strip(), f.get('jenis_layanan','').strip(), f.get('dokter_tujuan','').strip(), f.get('prioritas','Non-urgent').strip(), f.get('status_antrian','menunggu').strip(), f.get('tinggi_badan','') or None, f.get('berat_badan','') or None, f.get('lingkar_perut','') or None, f.get('bmi','') or None, token_auto(), current_user()['id'], now(), now()))
                conn.commit(); pid = cur.lastrowid
                log_action('CREATE_PATIENT', 'Tambah pasien #{} {}'.format(pid, nama))
                flash('Pasien baru berhasil ditambahkan.', 'success')
                return redirect(url_for('patient_detail', patient_id=pid))
            except sqlite3.IntegrityError:
                flash('Nomor rekam medis sudah digunakan.', 'danger')
    body = '''
    <div class="card" style="margin-bottom:16px">
      <h3 style="margin:0">Cari & Edit Pasien Lama</h3>
      <div class="small muted">Ketik nama / RM / NIK untuk mencari pasien yang sudah terdaftar.</div>
      <div style="margin-top:10px;padding:10px 14px;border-radius:14px;border:1px solid rgba(245,158,11,.3);background:rgba(245,158,11,.08);font-size:.8rem;color:#fcd34d">
        ⚠️ Peringatan: Memilih pasien dari pencarian akan mengisi ulang semua field form di bawah. Jika Anda sedang mengisi data pasien baru, data yang sudah diketik akan <strong>ditimpa</strong>.
      </div>
      <div style="margin-top:12px;margin-bottom:12px">
        <input class="input" id="searchExisting" placeholder="Ketik nama / RM / NIK minimal 2 huruf..." style="width:100%">
        <div id="searchResults" style="margin-top:4px;max-height:300px;overflow-y:auto;background:var(--bg-light);border:1px solid var(--primary);border-radius:12px;display:none;box-shadow:var(--shadow);position:absolute;width:100%;z-index:9999;backdrop-filter:blur(20px);"></div>
      </div>
      <div id="selectedPatient" style="display:none;margin-top:12px;margin-bottom:16px;padding:14px;border-radius:16px;border:1px solid var(--pri);background:rgba(34,197,94,0.1)"></div>
    </div>
    <div class="card">
      <h3>{{ 'Edit Pasien' if edit_patient else 'Form Input Pasien Baru' }}</h3>
      <form method="post" class="grid">
        <input type="hidden" name="edit_id" id="edit_id" value="{{ edit_patient['id'] if edit_patient else '' }}">
        <input type="hidden" name="keluarga_id" id="fkeluarga_id" value="{{ edit_patient['keluarga_id'] if edit_patient else '' }}">
        <div class="form2">
          <div>
            <label>Nama Pasien *</label>
            <input class="input" name="nama_pasien" id="fnama" value="{{ edit_patient['nama_pasien'] if edit_patient else '' }}" required>
          </div>
          <div>
            <label>Nomor Rekam Medis</label>
            <input class="input" name="nomor_rekam_medis" id="frm" value="{{ edit_patient['nomor_rekam_medis'] if edit_patient else '' }}" placeholder="Kosongkan untuk auto-generate">
          </div>
          <div><label>NIK</label><input class="input" name="nik" id="fnik" value="{{ edit_patient['nik'] if edit_patient else '' }}"></div>
          <div>
            <label>Tanggal Lahir</label>
            <input class="input" type="date" name="tanggal_lahir" id="ftgl" value="{{ edit_patient['tanggal_lahir'] if edit_patient else '' }}" onchange="hitungUmur()">
          </div>
          <div><label>Umur (otomatis dari TTL)</label><input class="input" name="umur" id="fumur" value="{{ edit_patient['umur'] if edit_patient else '' }}" readonly placeholder="Terisi otomatis"></div>
          <div><label>Nomor HP</label><input class="input" name="nomor_hp" id="fhp" value="{{ edit_patient['nomor_hp'] if edit_patient else '' }}"></div>
          <div>
            <label>Golongan Darah</label>
            <select class="select" name="golongan_darah" id="fgoldar">
              <option value="">- Pilih Golongan Darah -</option>
              {% for opt in goldar_opts %}<option value="{{ opt }}" {{ 'selected' if edit_patient and edit_patient['golongan_darah']==opt else '' }}>{{ opt }}</option>{% endfor %}
            </select>
          </div>
          <div>
            <label>Status Perkawinan</label>
            <select class="select" name="status_perkawinan" id="fstatus">
              <option value="">- Pilih Status -</option>
              {% for opt in ['Belum Menikah','Menikah','Cerai Hidup','Cerai Mati','Janda','Duda'] %}<option value="{{ opt }}" {{ 'selected' if edit_patient and edit_patient['status_perkawinan']==opt else '' }}>{{ opt }}</option>{% endfor %}
            </select>
          </div>
          <div>
            <label>Pekerjaan</label>
            <select class="select" name="pekerjaan" id="fpekerjaan">
              <option value="">- Pilih Pekerjaan -</option>
              {% for opt in pekerjaan_opts %}<option value="{{ opt }}" {{ 'selected' if edit_patient and edit_patient['pekerjaan']==opt else '' }}>{{ opt }}</option>{% endfor %}
            </select>
          </div>
          <div><label>Nama Suami/Keluarga</label><input class="input" name="nama_keluarga" id="fkeluarga" value="{{ edit_patient['nama_keluarga'] if edit_patient else '' }}"></div>
          <div>
            <label>Jenis Layanan</label>
            <select class="select" name="jenis_layanan" id="flayanan">
              <option value="">- Pilih Jenis Layanan -</option>
              {% for opt in layanan_opts %}<option value="{{ opt }}" {{ 'selected' if edit_patient and edit_patient['jenis_layanan']==opt else '' }}>{{ opt }}</option>{% endfor %}
            </select>
          </div>
          <div>
            <label>Dokter Tujuan</label>
            <select class="select" name="dokter_tujuan" id="fdokter">
              <option value="">- Pilih Dokter -</option>
              {% for d in doctors %}<option value="{{ d['full_name'] or d['username'] }}" {{ 'selected' if edit_patient and edit_patient['dokter_tujuan']==(d['full_name'] or d['username']) else '' }}>{{ d['full_name'] or d['username'] }}</option>{% endfor %}
            </select>
          </div>
          <div>
            <label>Prioritas</label>
            <select class="select" name="prioritas" id="fprioritas">
              <option value="Non-urgent" {{ 'selected' if edit_patient and edit_patient['prioritas']=='Non-urgent' else '' }}>Non-urgent</option>
              <option value="Urgent" {{ 'selected' if edit_patient and edit_patient['prioritas']=='Urgent' else '' }}>Urgent</option>
            </select>
          </div>
          <input type="hidden" name="status_antrian" id="fstatusq" value="{{ edit_patient['status_antrian'] if edit_patient else 'menunggu' }}">
          <div style="border-top:1px solid var(--border);padding-top:10px;margin-top:4px;grid-column:1/-1">
            <div class="small muted font-bold mb-2">👨‍👩‍👧‍👦 DATA KELUARGA</div>
            <div><label>Tautkan ke Ibu / Keluarga</label>
              <input class="input" id="keluargaSearch" placeholder="Cari nama ibu / anggota keluarga..." style="width:100%">
              <div id="keluargaResults" style="margin-top:6px;max-height:200px;overflow-y:auto;background:var(--bg-light);border:1px solid var(--border);border-radius:12px;display:none"></div>
              <div id="selectedKeluarga" style="display:none;margin-top:8px;padding:10px 14px;border-radius:12px;border:1px solid rgba(34,197,94,.3);background:rgba(34,197,94,.08)"></div>
            </div>
            <div style="margin-top:8px">
              <label>Hubungan dalam Keluarga</label>
              <select class="select" name="hubungan" id="fhubungan">
                <option value="">- Pilih Hubungan -</option>
                <option value="Ibu">Ibu</option>
                <option value="Anak ke-1">Anak ke-1</option>
                <option value="Anak ke-2">Anak ke-2</option>
                <option value="Anak ke-3">Anak ke-3</option>
                <option value="Anak ke-4">Anak ke-4</option>
                <option value="Anak ke-5">Anak ke-5</option>
              </select>
            </div>
          </div>
          <div style="border-top:1px solid var(--border);grid-column:1/-1;padding-top:10px;margin-top:4px">
            <div class="small muted font-bold mb-2">📏 DATA ANTROPOMETRI</div>
            <div class="grid grid-cols-4 gap-3">
              <div><label class="text-[10px]">Tinggi Badan (cm)</label><input class="input py-1 text-center" type="number" step="0.1" name="tinggi_badan" id="ftinggi" value="{{ edit_patient['tinggi_badan'] if edit_patient else '' }}" oninput="hitungBMI()" placeholder="165"></div>
              <div><label class="text-[10px]">Berat Badan (kg)</label><input class="input py-1 text-center" type="number" step="0.1" name="berat_badan" id="fberat" value="{{ edit_patient['berat_badan'] if edit_patient else '' }}" oninput="hitungBMI()" placeholder="65"></div>
              <div><label class="text-[10px]">Lingkar Perut (cm)</label><input class="input py-1 text-center" type="number" step="0.1" name="lingkar_perut" id="fperut" value="{{ edit_patient['lingkar_perut'] if edit_patient else '' }}" placeholder="80"></div>
              <div><label class="text-[10px]">BMI (otomatis)</label><input class="input py-1 text-center" name="bmi" id="fbmi" value="{{ edit_patient['bmi'] if edit_patient else '' }}" readonly placeholder="22.5"></div>
            </div>
          </div>
          <div style="grid-column:1/-1"><label>Alamat</label><textarea class="textarea" name="alamat" id="falamat">{{ edit_patient['alamat'] if edit_patient else '' }}</textarea></div>
        </div>
        <div class="toolbar">
          <button class="btn btn-primary">💾 {{ 'Update Pasien' if edit_patient else 'Simpan Pasien Baru' }}</button>
          <a class="btn" href="{{ url_for('patients') }}">Kembali</a>
          {% if edit_patient %}<a class="btn" href="{{ url_for('patient_detail', patient_id=edit_patient['id']) }}">🔍 Detail Pasien</a>{% endif %}
        </div>
      </form>
    </div>
    <script>
    // Helper: set select value, add temp option if value not in list
    function setSelectVal(id, val) {
      var el = document.getElementById(id);
      if (!el) return;
      // Remove previous temp options
      Array.from(el.options).forEach(function(o){ if(o.dataset.temp) el.removeChild(o); });
      el.value = val || '';
      if (val && el.value !== val) {
        var opt = document.createElement('option');
        opt.value = val; opt.text = val + ' (lama)';
        opt.dataset.temp = '1';
        el.appendChild(opt);
        el.value = val;
      }
    }

    function hitungBMI(){
      var tb=parseFloat(document.getElementById('ftinggi').value)||0;
      var bb=parseFloat(document.getElementById('fberat').value)||0;
      if(tb>0&&bb>0){
        var bmi=bb/((tb/100)*(tb/100));
        document.getElementById('fbmi').value=bmi.toFixed(1);
      }
    }
    function hitungUmur(){
      var tgl=document.getElementById('ftgl').value;
      if(!tgl) return;
      var parts=tgl.split('-');
      var lahir=new Date(parseInt(parts[0]),parseInt(parts[1])-1,parseInt(parts[2]));
      var now=new Date();
      var usia=now.getFullYear()-lahir.getFullYear();
      var m=now.getMonth()-lahir.getMonth();
      if(m<0||(m===0&&now.getDate()<lahir.getDate())) usia--;
      document.getElementById('fumur').value=usia+' tahun';
    }
    document.addEventListener('DOMContentLoaded',function(){
      // === Keluarga search ===
      var ks=document.getElementById('keluargaSearch');
      var kr=document.getElementById('keluargaResults');
      var sk=document.getElementById('selectedKeluarga');
      var kTimer=null;
      if(ks){
        ks.addEventListener('input',function(){
          clearTimeout(kTimer);
          var v=ks.value.trim();
          if(v.length<2){kr.style.display='none';sk.style.display='none';return;}
          kTimer=setTimeout(function(){
            fetch('/api/keluarga_search?q='+encodeURIComponent(v)).then(function(r){return r.json()}).then(function(data){
              if(!data.results||data.results.length===0){kr.innerHTML='<div style="padding:14px;color:var(--text-muted)">Tidak ditemukan</div>';kr.style.display='block';return;}
              var h='';
              data.results.forEach(function(p){h+='<div onclick="pilihKeluarga('+p.id+','+JSON.stringify(p.nama_pasien)+','+JSON.stringify(p.nomor_rekam_medis)+','+(p.keluarga_id||'null')+','+JSON.stringify(p.hubungan||'')+')" style="padding:10px 14px;cursor:pointer;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center"><div><strong>'+p.nama_pasien+'</strong><div class="small muted">RM: '+p.nomor_rekam_medis+' • Hub: '+(p.hubungan||'-')+'</div></div><span class="badge">pilih</span></div>';
              });
              kr.innerHTML=h;kr.style.display='block';
            });
          },300);
        });
      }
      window.pilihKeluarga=function(id,name,rm,kid,hub){
        kr.style.display='none';
        document.getElementById('fkeluarga_id').value=kid||id;
        if(!hub){
          sk.style.display='block';
          sk.innerHTML='<div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><div><strong>'+name+'</strong> <span class="small muted">(RM: '+rm+')</span></div><button type="button" class="btn btn-sm" onclick="document.getElementById(\'fkeluarga_id\').value=\'\';document.getElementById(\'selectedKeluarga\').style.display=\'none\'">✕</button></div>';
        }else{
          sk.style.display='block';
          sk.innerHTML='<div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><div><strong>'+name+'</strong> <span class="small muted">(RM: '+rm+', '+hub+')</span></div><button type="button" class="btn btn-sm" onclick="document.getElementById(\'fkeluarga_id\').value=\'\';document.getElementById(\'selectedKeluarga\').style.display=\'none\'">✕</button></div>';
        }
      };
      // === Existing patient search ===
      var inp=document.getElementById('searchExisting');
      var res=document.getElementById('searchResults');
      var sel=document.getElementById('selectedPatient');
      var timer=null;
      inp.addEventListener('input',function(){
        clearTimeout(timer);
        var v=inp.value.trim();
        if(v.length<2){res.style.display='none';return;}
      sel.style.display='none'; // Sembunyikan kartu pasien terpilih segera saat mulai mencari
        timer=setTimeout(function(){
          res.innerHTML='<div style="padding:14px;color:var(--text-muted)"><span class="animate-pulse">⌛ Mencari...</span></div>';
          res.style.display='block';
          fetch('/api/patient_search?q='+encodeURIComponent(v)).then(function(r){return r.json()}).then(function(data){
            if(!data.results||data.results.length===0){
              res.innerHTML='<div style="padding:14px;color:var(--text-muted)">Tidak ditemukan</div>';res.style.display='block';return;
            }
            var html='';
            data.results.forEach(function(p){
              html+='<div onclick="pilihPasien('+p.id+')" style="padding:12px 16px;cursor:pointer;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center">'+
                '<div><strong>'+p.nama_pasien+'</strong><div class="small muted">RM: '+p.nomor_rekam_medis+' • NIK: '+(p.nik||'-')+'</div></div>'+
                '<span class="badge">pilih</span></div>';
            });
            res.innerHTML=html;res.style.display='block';
          });
        },300);
      });
      document.addEventListener('click',function(e){
        if(!inp.contains(e.target)&&!res.contains(e.target)&&!sel.contains(e.target)){res.style.display='none';}
      });
      window.pilihPasien=function(id){
        fetch('/api/patient_by_id/'+id).then(function(r){return r.json()}).then(function(data){
          var p=data.result;
          if(!p) return;
          res.style.display='none';
          inp.value=p.nama_pasien+' ('+p.nomor_rekam_medis+')';
          // Fetch visit count
          fetch('/api/patient_visits/'+id).then(function(r2){return r2.json()}).then(function(vc){
            var visitInfo = (vc.count>0) ? '• Kunjungan ke-'+(vc.count+1) : '';
            sel.style.display='block';
            sel.innerHTML='<div style="display:flex;justify-content:space-between;align-items:center;gap:10px">'+
              '<div><strong>'+p.nama_pasien+'</strong><div class="small muted">RM: '+p.nomor_rekam_medis+' • NIK: '+(p.nik||'-')+' • HP: '+(p.nomor_hp||'-')+'  '+visitInfo+'</div></div>'+
              '<button type="button" class="btn btn-sm" onclick="batalPilih()">✕ Batal</button></div>'+
              '<div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">'+
              '<a class="btn btn-primary btn-sm" href="/patients/'+id+'">🔍 Detail</a>'+
              '<a class="btn btn-sm" href="/patients/'+id+'/history">🕘 History</a>'+
              '<a class="btn btn-sm" href="?edit='+id+'">✏️ Edit</a></div>';
          });
          document.getElementById('edit_id').value=id;
          document.getElementById('fnama').value=p.nama_pasien;
          document.getElementById('frm').value=p.nomor_rekam_medis;
          document.getElementById('fnik').value=p.nik||'';
          document.getElementById('ftgl').value=p.tanggal_lahir||'';
          document.getElementById('fumur').value=p.umur||'';
          document.getElementById('fhp').value=p.nomor_hp||'';
          setSelectVal('fgoldar', p.golongan_darah);
          setSelectVal('fstatus', p.status_perkawinan);
          setSelectVal('fpekerjaan', p.pekerjaan);
          document.getElementById('fkeluarga').value=p.nama_keluarga||'';
          setSelectVal('flayanan', p.jenis_layanan);
          setSelectVal('fdokter', p.dokter_tujuan);
          setSelectVal('fstatusq', p.status_antrian||'menunggu');
          document.getElementById('falamat').value=p.alamat||'';
        });
      };
      window.batalPilih=function(){
        inp.value='';sel.style.display='none';sel.innerHTML='';
        document.getElementById('edit_id').value='';
        ['fnama','frm','fnik','ftgl','fumur','fhp','fkeluarga','falamat'].forEach(function(id){
          document.getElementById(id).value='';
        });
        ['fgoldar','fstatus','fpekerjaan','flayanan','fdokter'].forEach(function(id){
          document.getElementById(id).value='';
        });
        document.getElementById('fstatusq').value='menunggu';
      };
    });
    </script>
    '''
    return render_page('Input Pasien Baru', body, doctors=doctors, edit_patient=edit_patient,
                       goldar_opts=goldar_opts, layanan_opts=layanan_opts, pekerjaan_opts=pekerjaan_opts)


@app.route('/patients/<int:patient_id>', methods=['GET', 'POST'])
@role_required('superadmin', 'admin', 'dokter', 'pasien')
def patient_detail(patient_id):
    patient = get_patient(patient_id)
    if not patient: abort(404)
    if not patient_allowed(patient): abort(403)
    user = current_user()
    conn = get_db(); cur = conn.cursor()
    if request.method == 'POST':
        action = request.form.get('action', '')
        if action == 'update_status' and user['role'] in ('superadmin','admin','dokter'):
            st = request.form.get('status_antrian', '').strip()
            current_st = patient['status_antrian']
            valid = False
            # Transisi yang diizinkan: hanya maju 1 langkah
            if current_st == 'menunggu' and st == 'diperiksa':
                valid = True
            elif current_st == 'diperiksa' and st == 'selesai':
                valid = True
            if valid and st in ('menunggu','diperiksa','selesai'):
                cur.execute('UPDATE patients SET status_antrian=?, updated_at=? WHERE id=?', (st, now(), patient_id)); conn.commit()
                log_action('UPDATE_QUEUE_STATUS', 'Patient #{} -> {}'.format(patient_id, st)); flash('Status antrian diperbarui.', 'success'); return redirect(url_for('patient_detail', patient_id=patient_id))
            else:
                flash('Transisi status tidak valid: dari "{}" ke "{}" tidak diizinkan.'.format(current_st, st), 'danger')
        if action == 'add_to_queue' and user['role'] in ('superadmin', 'admin', 'dokter'):
            current_st = patient['status_antrian']
            if current_st in ('menunggu', 'diperiksa'):
                flash('Pasien masih dalam antrian aktif ({}). Tidak bisa mengantrikan ulang.'.format(current_st), 'danger')
                return redirect(url_for('patient_detail', patient_id=patient_id))
            # Update created_at agar pasien muncul di urutan terbawah antrian hari ini (FIFO)
            cur.execute("UPDATE patients SET status_antrian='menunggu', created_at=?, updated_at=? WHERE id=?", (now(), now(), patient_id))
            conn.commit()
            log_action('ADD_TO_QUEUE', 'Pasien #{} dimasukkan ke antrian'.format(patient_id))
            flash('Pasien berhasil dimasukkan ke antrian hari ini.', 'success')
            return redirect(url_for('antrian'))
        if action == 'set_lunas' and user['role'] in ('superadmin', 'admin'):
            bid = request.form.get('billing_id')
            if bid:
                cur.execute("UPDATE billing SET status_bayar='lunas' WHERE id=?", (bid,))
                conn.commit()
                log_action('SET_LUNAS', 'Billing #{} ditandai lunas'.format(bid))
                flash('Pembayaran berhasil dikonfirmasi (Lunas).', 'success')
                return redirect(url_for('patient_detail', patient_id=patient_id))
        if action == 'save_soap' and user['role'] in ('superadmin','admin','dokter'):
            vals = [request.form.get(k,'').strip() for k in ['subjective','objective','assessment','plan','kode_icd10','td_sistolik','td_diastolik','nadi','suhu','rr','usia_kehamilan','detak_jantung_janin','posisi_janin','estimasi_berat_janin','catatan_dokter','rekomendasi_kontrol_ulang']]
            ic = 1 if request.form.get('informed_consent') == 'on' else 0
            cur.execute('''INSERT INTO soap_records (patient_id,doctor_id,subjective,objective,assessment,plan,kode_icd10,td_sistolik,td_diastolik,nadi,suhu,rr,usia_kehamilan,detak_jantung_janin,posisi_janin,estimasi_berat_janin,catatan_dokter,rekomendasi_kontrol_ulang,informed_consent,created_at,updated_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (patient_id, user['id'], *vals, ic, now(), now()))
            if patient['status_antrian'] != 'selesai':
                cur.execute("UPDATE patients SET status_antrian='diperiksa', updated_at=? WHERE id=?", (now(), patient_id))
            conn.commit(); log_action('SAVE_SOAP', 'SOAP pasien #{}'.format(patient_id)); flash('SOAP berhasil disimpan.', 'success'); return redirect(url_for('patient_detail', patient_id=patient_id))
        if action == 'upload_file' and user['role'] in ('superadmin','admin','dokter'):
            f = request.files.get('usg_file')
            if not f or not f.filename:
                flash('Pilih file yang akan di-upload.', 'danger')
            elif not allowed_file(f.filename):
                flash('Format file tidak diizinkan. Hanya: {}'.format(', '.join(sorted(ALLOWED))), 'danger')
            else:
                original = secure_filename(f.filename)
                ext = original.rsplit('.',1)[1].lower()
                stored = '{}_{}.{}'.format(patient_id, uuid.uuid4().hex, ext)
                path = os.path.join(UPLOAD_DIR, stored)
                f.save(path)
                size = os.path.getsize(path)
                cur.execute('INSERT INTO uploads (patient_id,uploader_id,original_filename,stored_filename,file_ext,file_size,mime_type,created_at) VALUES (?,?,?,?,?,?,?,?)',
                            (patient_id, user['id'], original, stored, ext, size, f.mimetype or '', now()))
                conn.commit(); log_action('UPLOAD_USG', 'Upload pasien #{}: {}'.format(patient_id, original)); flash('File hasil USG berhasil di-upload.', 'success'); return redirect(url_for('patient_detail', patient_id=patient_id))
        if action == 'add_billing' and user['role'] in ('superadmin','admin'):
            item = request.form.get('item_name','').strip(); amount = request.form.get('amount','0').strip(); st = request.form.get('status_bayar','belum_lunas').strip(); notes = request.form.get('notes','').strip()
            try:
                amt = float(amount or 0)
                if not item: raise ValueError('empty')
                cur.execute('INSERT INTO billing (patient_id,item_name,amount,status_bayar,notes,created_by,created_at) VALUES (?,?,?,?,?,?,?)', (patient_id, item, amt, st, notes, user['id'], now()))
                conn.commit(); log_action('ADD_BILLING', 'Billing pasien #{}: {}'.format(patient_id, item)); flash('Billing berhasil ditambahkan.', 'success'); return redirect(url_for('patient_detail', patient_id=patient_id))
            except Exception:
                flash('Data billing tidak valid.', 'danger')
    cur.execute('SELECT * FROM patients WHERE id=?', (patient_id,)); patient = cur.fetchone()
    cur.execute('SELECT st.*, u.full_name doctor_name, u.username doctor_username FROM soap_records st LEFT JOIN users u ON st.doctor_id=u.id WHERE st.patient_id=? ORDER BY st.created_at DESC', (patient_id,)); soaps = cur.fetchall()
    cur.execute('SELECT * FROM uploads WHERE patient_id=? ORDER BY created_at DESC', (patient_id,)); files = cur.fetchall()
    cur.execute('SELECT * FROM billing WHERE patient_id=? ORDER BY created_at DESC', (patient_id,)); bills = cur.fetchall()
    cur.execute('SELECT * FROM soap_templates ORDER BY id DESC'); templates = cur.fetchall()
    public_url = request.url_root.rstrip('/') + url_for('patient_result', token=patient['access_token'])
    qr_uri = qr_data_uri(public_url)
    body = '''
    <div class="space-y-4">
      <!-- Header Pasien -->
      <div class="card p-6 bg-gradient-to-br from-slate-800/40 to-slate-900/40">
        <div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-4">
          <div>
            <div class="flex items-center gap-3 mb-1">
              <h2 class="text-2xl font-black text-white">{{ patient['nama_pasien'] }}</h2>
              <span class="pill {{ patient['status_antrian'] }}">{{ patient['status_antrian'] }}</span>
            </div>
            <div class="text-sm text-slate-400 font-medium">
              No RM: <span class="text-emerald-400 font-mono">{{ patient['nomor_rekam_medis'] }}</span> • 
              NIK: {{ patient['nik'] or '-' }} • 
              HP: {{ patient['nomor_hp'] or '-' }}
            </div>
          </div>
          <div class="flex gap-2 no-print">
            {% if patient['status_antrian'] == 'selesai' %}
            <form method="post" onsubmit="return confirm('Antrikan pasien ini?')">
              <input type="hidden" name="action" value="add_to_queue">
              <button class="btn btn-sm btn-primary">➕ Antrikan</button>
            </form>
            {% endif %}
            <a class="btn btn-sm" href="{{ url_for('patient_new', edit=patient['id']) }}">✏️ Edit</a>
            {% if user['role'] == 'superadmin' %}
            <form method="post" action="{{ url_for('patient_delete', patient_id=patient['id']) }}" onsubmit="return confirm('Hapus pasien ini PERMANEN?')">
              <button class="btn btn-sm bg-red-600 hover:bg-red-500 text-white border-none">🗑️ Hapus</button>
            </form>
            {% endif %}
          </div>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mt-6 pt-4 border-t border-white/5 text-xs">
          <div><span class="text-slate-500 block mb-1">UMUR / TTL</span><span class="text-slate-200 font-bold">{{ patient['umur'] or '-' }} / {{ patient['tanggal_lahir'] or '-' }}</span></div>
          <div><span class="text-slate-500 block mb-1">LAYANAN / GOLDAR</span><span class="text-slate-200 font-bold">{{ patient['jenis_layanan'] or '-' }} / {{ patient['golongan_darah'] or '-' }}</span></div>
          <div><span class="text-slate-500 block mb-1">NAMA KELUARGA</span><span class="text-slate-200 font-bold">{{ patient['nama_keluarga'] or '-' }}</span></div>
          <div><span class="text-slate-500 block mb-1">DOKTER TUJUAN</span><span class="text-slate-200 font-bold text-sky-400">{{ patient['dokter_tujuan'] or '-' }}</span></div>
        </div>
        <!-- Antropometri -->
        <div class="grid grid-cols-4 gap-4 mt-2 pt-3 border-t border-white/5 text-xs">
          <div><span class="text-slate-500 block mb-1">TB (cm)</span><span class="text-slate-200 font-bold">{{ "%.1f"|format(patient['tinggi_badan']) if patient['tinggi_badan'] else '-' }}</span></div>
          <div><span class="text-slate-500 block mb-1">BB (kg)</span><span class="text-slate-200 font-bold">{{ "%.1f"|format(patient['berat_badan']) if patient['berat_badan'] else '-' }}</span></div>
          <div><span class="text-slate-500 block mb-1">Lingkar Perut (cm)</span><span class="text-slate-200 font-bold">{{ "%.1f"|format(patient['lingkar_perut']) if patient['lingkar_perut'] else '-' }}</span></div>
          <div><span class="text-slate-500 block mb-1">BMI</span><span class="text-slate-200 font-bold {% if patient['bmi'] and ((patient['bmi']|float < 18.5) or (patient['bmi']|float >= 25)) %}text-amber-400{% else %}text-emerald-400{% endif %}">{{ "%.1f"|format(patient['bmi']|float) if patient['bmi'] else '-' }}</span></div>
        </div>
        <!-- Progress Bar Alur Klinik -->
        {% set s_antrian = patient['status_antrian'] %}
        {% set upload_count = files|length %}
        {% set billing_count = bills|length %}
        {% set soap_count = soaps|length %}
        {% set p_steps = [] %}
        {% if patient and patient['id'] %}{% set _ = p_steps.append(1) %}{% endif %}
        {% if s_antrian in ('menunggu','diperiksa','selesai') %}{% set _ = p_steps.append(1) %}{% endif %}
        {% if s_antrian in ('diperiksa','selesai') and soap_count > 0 %}{% set _ = p_steps.append(1) %}{% endif %}
        {% if upload_count > 0 %}{% set _ = p_steps.append(1) %}{% endif %}
        {% if billing_count > 0 %}{% set _ = p_steps.append(1) %}{% endif %}
        {% if s_antrian == 'selesai' %}{% set _ = p_steps.append(1) %}{% endif %}
        {% set pct = (p_steps|length / 6 * 100)|int %}
        <div class="mt-4 pt-3 border-t border-white/5">
          <div class="flex justify-between items-center mb-1">
            <span class="text-[10px] text-slate-500 font-bold uppercase tracking-wider">Progress Alur Klinik</span>
            <span class="text-[10px] font-bold text-emerald-400">{{ pct }}%</span>
          </div>
          <div class="w-full h-2 bg-slate-700/50 rounded-full overflow-hidden">
            <div class="h-full bg-gradient-to-r from-emerald-500 to-cyan-500 rounded-full transition-all duration-500" style="width:{{ pct }}%"></div>
          </div>
          <div class="flex justify-between text-[8px] text-slate-500 mt-1">
            <span class="{% if p_steps|length >= 1 %}text-emerald-400{% endif %}">📝 Daftar</span>
            <span class="{% if p_steps|length >= 2 %}text-emerald-400{% endif %}">🚶 Antri</span>
            <span class="{% if p_steps|length >= 3 %}text-emerald-400{% endif %}">🩺 Periksa</span>
            <span class="{% if p_steps|length >= 4 %}text-emerald-400{% endif %}">📁 Upload</span>
            <span class="{% if p_steps|length >= 5 %}text-emerald-400{% endif %}">💳 Bayar</span>
            <span class="{% if p_steps|length >= 6 %}text-emerald-400{% endif %}">✅ Selesai</span>
          </div>
        </div>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
        <!-- Workspace Utama (SOAP & Growth) -->
        <div class="lg:col-span-8 space-y-4">
          <!-- Form SOAP -->
          <div class="card">
            <div class="flex justify-between items-center mb-4">
              <h3 class="text-lg font-bold flex items-center gap-2"><span class="w-1.5 h-6 bg-emerald-500 rounded-full"></span> Pemeriksaan Baru (SOAP)</h3>
              {% if patient['status_antrian'] != 'selesai' %}
              <div class="no-print flex items-center gap-2">
                <select class="select py-1 text-xs" id="soapTemplate" style="max-width:180px">
                  <option value="">Gunakan Template...</option>
                  {% for t in templates %}<option value='{{ {"subjective":t["subjective"],"objective":t["objective"],"assessment":t["assessment"],"plan":t["plan"]}|tojson }}'>{{ t['title'] }}</option>{% endfor %}
                </select>
                <button type="button" class="btn btn-sm" onclick="applySoap()">⚡</button>
              </div>
              {% endif %}
            </div>
            {% if patient['status_antrian'] == 'selesai' %}
            <div class="p-6 text-center text-slate-500 italic">
              <div class="text-3xl mb-2">✅</div>
              <div>Pasien sudah selesai diperiksa. Tidak bisa menambah pemeriksaan baru.</div>
              <div class="text-xs mt-2">Untuk pemeriksaan ulang, gunakan tombol "Antrikan" untuk mendaftarkan pasien kembali.</div>
            </div>
            {% else %}
            <form method="post" class="space-y-4 no-print">
              <input type="hidden" name="action" value="save_soap">
              <div class="grid md:grid-cols-2 gap-4">
                <div class="space-y-3">
                  <div><label>Subjective (Keluhan)</label><textarea id="subjective" class="textarea h-24" name="subjective" placeholder="Keluhan pasien..."></textarea></div>
                  <div><label>Objective (Vital & Fisik)</label><textarea id="objective" class="textarea h-24" name="objective" placeholder="Hasil pemeriksaan fisik..."></textarea></div>
                  <div class="grid grid-cols-3 gap-2">
                    <div><label class="text-[10px]">TD SYST</label><input class="input py-1 text-center" name="td_sistolik" placeholder="120"></div>
                    <div><label class="text-[10px]">TD DIAST</label><input class="input py-1 text-center" name="td_diastolik" placeholder="80"></div>
                    <div><label class="text-[10px]">DJJ</label><input class="input py-1 text-center font-bold text-sky-400" name="detak_jantung_janin" placeholder="140"></div>
                  </div>
                </div>
                <div class="space-y-3">
                  <div><label>Assessment (Diagnosis)</label><textarea id="assessment" class="textarea h-24" name="assessment" placeholder="Diagnosis / ICD-10..."></textarea></div>
                  <div><label>Plan (Rencana / Terapi)</label><textarea id="plan" class="textarea h-24" name="plan" placeholder="Rencana tindak lanjut..."></textarea></div>
                  <div class="grid grid-cols-3 gap-2">
                    <div><label class="text-[10px]">USIA HAMIL</label><input class="input py-1 text-center" name="usia_kehamilan" placeholder="28w"></div>
                    <div><label class="text-[10px]">POSISI JANIN</label><input class="input py-1 text-center" name="posisi_janin" placeholder="Kepala"></div>
                    <div><label class="text-[10px]">EBJ (Gram)</label><input class="input py-1 text-center font-bold text-emerald-400" name="estimasi_berat_janin" placeholder="1200"></div>
                  </div>
                  <div><label>Rekomendasi Kontrol</label><input class="input py-1" name="rekomendasi_kontrol_ulang" placeholder="Contoh: 2 minggu lagi"></div>
                </div>
                <div class="md:col-span-2">
                  {% set last_risk = hitung_risiko_kehamilan(soaps[0]['td_sistolik'], soaps[0]['td_diastolik'], soaps[0]['detak_jantung_janin']) if soaps else None %}
                  {% if last_risk and last_risk.status == 'Merah' %}
                  <div class="p-4 bg-red-500/20 border border-red-500/50 rounded-2xl flex justify-between items-center animate-pulse">
                    <span class="text-sm font-bold text-red-200">⚠️ PERINGATAN: Pasien Terdeteksi Risiko Tinggi (Preeklampsia/Gawat Janin)</span>
                    <a href="{{ url_for('referral_letter', patient_id=patient['id']) }}" class="btn btn-sm bg-white text-red-600 font-bold">Cetak Surat Rujukan</a>
                  </div>
                  {% endif %}
                </div>
              </div>
              <div class="p-3 bg-white/5 rounded-xl border border-white/10 flex justify-between items-center">
                <label class="flex items-center gap-2 cursor-pointer text-xs mb-0"><input type="checkbox" name="informed_consent" required> Informed Consent Disetujui Pasien</label>
                <button class="btn btn-primary">🩺 Simpan Rekam Medis</button>
              </div>
            </form>
          </div>
          {% endif %}

          <!-- Grafik Pertumbuhan Janin -->
          <div class="card">
            <h3 class="text-lg font-bold mb-4 flex items-center gap-2"><span class="w-1.5 h-6 bg-sky-500 rounded-full"></span> Tren Pertumbuhan Janin</h3>
            <div class="text-[10px] text-slate-500 mb-2 italic">Area arsiran menunjukkan rentang normal WHO (Persentil 10-90)</div>
            <div class="h-[250px] w-full">
              <canvas id="growthChart"></canvas>
            </div>
          </div>

          <!-- Tabel Growth -->
          {% if soaps %}
          <div class="card overflow-hidden">
            <h3 class="text-sm font-bold mb-3 uppercase tracking-wider text-slate-500">Monitoring Perkembangan</h3>
            <div class="table-wrap">
              <table class="text-xs">
                <thead class="bg-white/5">
                  <tr><th>Tanggal</th><th>Usia Hamil</th><th>DJJ (bpm)</th><th>Posisi</th><th>Berat (gr)</th></tr>
                </thead>
                <tbody class="divide-y divide-white/5">
                  {# Kita gunakan data dari SQL yang sudah di-sort ASC di backend untuk tabel tren #}
                  {% for s in soaps[::-1] %}
                  <tr class="hover:bg-white/5"><td>{{ fmt_dt(s['created_at']).split(' ')[0] }}</td><td class="font-bold text-white">{{ s['usia_kehamilan'] or '-' }}</td><td>{{ s['detak_jantung_janin'] or '-' }}</td><td>{{ s['posisi_janin'] or '-' }}</td><td class="font-bold text-emerald-400">{{ s['estimasi_berat_janin'] or '-' }}</td></tr>
                  {% endfor %}
                </tbody>
              </table>
            </div>
          </div>
          {% endif %}

          <!-- SOAP HISTORY -->
          <div class="space-y-3">
            <h3 class="text-lg font-bold px-2">Kunjungan Sebelumnya</h3>
            {% for s in soaps %}
            <div class="card p-4 bg-white/5 border-white/5 hover:bg-white/10 transition-colors">
              <div class="flex justify-between items-start mb-2 border-b border-white/5 pb-2">
                <div class="text-xs font-bold text-emerald-400">{{ fmt_dt(s['created_at']) }}</div>
                <div class="flex items-center gap-2">
                  <div class="text-[10px] uppercase text-slate-500 font-bold">Oleh: {{ s['doctor_name'] or s['doctor_username'] }}</div>
                  {% if user['role'] != 'pasien' %}
                  <form method="post" action="{{ url_for('soap_delete', soap_id=s['id']) }}" onsubmit="return confirm('Hapus rekam medis ini?')">
                    <button class="text-red-500 hover:text-red-400 text-[10px] font-bold">🗑️ Hapus</button>
                  </form>
                  {% endif %}
                </div>
              </div>
              <div class="grid grid-cols-2 gap-4 text-xs">
                <div class="wrap"><span class="text-slate-500 font-bold">A:</span> {{ s['assessment'] or '-' }}</div>
                <div class="wrap"><span class="text-slate-500 font-bold">P:</span> {{ s['plan'] or '-' }}</div>
              </div>
            </div>
            {% endfor %}
          </div>
        </div>

        <!-- Sidebar Aksi (lg:col-span-4) -->
        <div class="lg:col-span-4 space-y-4">
          <!-- Antrian & Upload -->
          <div class="card space-y-4 no-print">
          <div>
              <div class="small muted font-bold mb-2">Status Antrian</div>
              <div class="flex items-center gap-2">
                <span class="pill {{ patient['status_antrian'] }} text-sm px-4 py-2">{{ patient['status_antrian'] }}</span>
                {% if patient['status_antrian'] == 'menunggu' %}
                <form method="post" style="display:inline">
                  <input type="hidden" name="action" value="update_status">
                  <input type="hidden" name="status_antrian" value="diperiksa">
                  <button class="btn btn-sm btn-primary">🩺 Mulai Periksa</button>
                </form>
                {% endif %}
                {% if patient['status_antrian'] == 'diperiksa' %}
                <form method="post" style="display:inline" onsubmit="return confirm('Selesaikan pemeriksaan? Pastikan SOAP & billing sudah diisi.')">
                  <input type="hidden" name="action" value="update_status">
                  <input type="hidden" name="status_antrian" value="selesai">
                  <button class="btn btn-sm btn-primary">✅ Selesaikan</button>
                </form>
                {% endif %}
              </div>
            </div>
            <div class="pt-4 border-t border-white/5">
              <label>Upload Gambar/Video USG</label>
              <form method="post" enctype="multipart/form-data" class="space-y-2">
                <input type="hidden" name="action" value="upload_file">
                <input class="input text-xs" type="file" name="usg_file" accept=".jpg,.jpeg,.png,.pdf,.mp4,.mov" required>
                <button class="btn btn-sm btn-primary w-full justify-center">⬆️ Mulai Upload</button>
              </form>
            </div>
          </div>

          <!-- Billing -->
          <div class="card space-y-4">
            <h3 class="text-sm font-bold uppercase tracking-wider text-slate-500">Billing & Transaksi</h3>
            {% if current_user['role'] in ['superadmin','admin'] %}
            <form method="post" class="space-y-2 no-print">
              <input type="hidden" name="action" value="add_billing">
              <input class="input py-1 text-xs" name="item_name" placeholder="Nama Layanan">
              <input class="input py-1 text-xs" type="number" name="amount" placeholder="Harga (Rp)">
              <button class="btn btn-sm w-full">💳 Tambah Tagihan</button>
            </form>
            {% endif %}
            <div class="space-y-2 max-h-48 overflow-y-auto pr-1">
              {% for b in bills %}
              <div class="flex justify-between items-center p-2 bg-white/5 rounded-lg text-[10px]">
                <div><div class="font-bold text-slate-200">{{ b['item_name'] }}</div><div class="text-slate-500">{{ rupiah(b['amount']) }}</div></div>
                <div class="flex items-center gap-2">
                  <span class="pill {{ 'selesai' if b['status_bayar']=='lunas' else 'unpaid' }}">{{ b['status_bayar'] }}</span>
                  {% if user['role'] in ['superadmin','admin'] %}
                  <form method="post" action="{{ url_for('billing_delete', billing_id=b['id']) }}" onsubmit="return confirm('Hapus item billing?')">
                    <button class="text-red-500">✕</button>
                  </form>
                  {% endif %}
                </div>
              </div>
              {% endfor %}
            </div>
          </div>

          <!-- Akses Token & Hasil -->
          <div class="card text-center p-6 space-y-4">
            <h3 class="text-sm font-bold uppercase tracking-wider text-slate-500">Link Hasil Pasien</h3>
            {% if qr_uri %}<img src="{{ qr_uri }}" class="mx-auto w-32 h-32 p-2 bg-white rounded-2xl shadow-xl">{% endif %}
            <div class="mono text-[10px] break-all bg-black/20 p-2 rounded-lg text-slate-400">{{ public_url }}</div>
            <button class="btn btn-sm w-full justify-center" onclick="navigator.clipboard.writeText('{{ public_url }}');alert('Link disalin')">📋 Salin Link WA</button>
          </div>
        </div>
      </div>
    </div>

    <script>
    function applySoap(){
      const el=document.getElementById('soapTemplate');
      if(!el.value)return;
      try{
        const d=JSON.parse(el.value);
        document.getElementById('subjective').value=d.subjective||'';
        document.getElementById('objective').value=d.objective||'';
        document.getElementById('assessment').value=d.assessment||'';
        document.getElementById('plan').value=d.plan||'';
      }catch(e){alert('Template gagal dipakai')}
    }

    // Initialize Chart
    document.addEventListener('DOMContentLoaded', function() {
      fetch('/api/fetal_growth/{{ patient["id"] }}')
        .then(r => r.json())
        .then(data => {
          // WHO Simplified Percentiles (P10 - P90)
          const whoP10 = [250, 450, 750, 1100, 1600, 2100, 2600, 2900];
          const whoP90 = [380, 700, 1100, 1650, 2300, 2900, 3500, 4100];
          const whoLabels = ["20w", "23w", "26w", "29w", "32w", "35w", "38w", "40w"];

          const ctx = document.getElementById('growthChart').getContext('2d');
          new Chart(ctx, {
            type: 'line',
            data: {
              labels: data.labels,
              datasets: [
                {
                  label: 'Rentang Normal (WHO P90)',
                  data: whoP90,
                  borderColor: 'rgba(34, 197, 94, 0.2)',
                  backgroundColor: 'rgba(34, 197, 94, 0.05)',
                  fill: '+1',
                  pointRadius: 0,
                  tension: 0.4
                },
                {
                  label: 'Rentang Normal (WHO P10)',
                  data: whoP10,
                  borderColor: 'rgba(34, 197, 94, 0.2)',
                  backgroundColor: 'transparent',
                  fill: false,
                  pointRadius: 0,
                  tension: 0.4
                },
                {
                  label: 'Berat Janin (gr)',
                  data: data.ebj,
                  borderColor: '#10b981',
                  backgroundColor: 'rgba(16, 185, 129, 0.1)',
                  yAxisID: 'y',
                  tension: 0.3
                },
                {
                  label: 'DJJ (bpm)',
                  data: data.djj,
                  borderColor: '#0ea5e9',
                  backgroundColor: 'transparent',
                  yAxisID: 'y1',
                  borderDash: [5, 5],
                  tension: 0.3
                }
              ]
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              scales: {
                y: { type: 'linear', display: true, position: 'left', grid: { color: 'rgba(255,255,255,0.05)' } },
                y1: { type: 'linear', display: true, position: 'right', grid: { drawOnChartArea: false } }
              },
              plugins: { 
                legend: { 
                  labels: { 
                    color: '#7b93b5', boxWidth: 10, font: { size: 9 },
                    filter: function(item) { return !item.text.includes('Rentang'); }
                  } 
                } 
              }
            }
          });
        });
    });
    </script>
    '''
    return render_page('Detail Pasien - ' + patient['nama_pasien'], body, patient=patient, public_url=public_url, qr_uri=qr_uri, soaps=soaps, files=files, bills=bills, templates=templates, file_badge=file_badge, fmt_dt=fmt_dt, rupiah=rupiah, max_mb=MAX_MB)


@app.route('/patients/<int:patient_id>/history')
@role_required('superadmin', 'admin', 'dokter', 'pasien')
def patient_history(patient_id):
    patient = get_patient(patient_id)
    if not patient: abort(404)
    if not patient_allowed(patient): abort(403)
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT st.*, u.full_name doctor_name, u.username doctor_username FROM soap_records st LEFT JOIN users u ON st.doctor_id=u.id WHERE st.patient_id=? ORDER BY st.created_at DESC', (patient_id,)); soaps = cur.fetchall()
    cur.execute('SELECT * FROM uploads WHERE patient_id=? ORDER BY created_at DESC', (patient_id,)); files = cur.fetchall()
    cur.execute('SELECT * FROM billing WHERE patient_id=? ORDER BY created_at DESC', (patient_id,)); bills = cur.fetchall()
    # Keluarga
    keluarga_members = []
    if patient['keluarga_id']:
        cur.execute("SELECT id, nama_pasien, nomor_rekam_medis, hubungan FROM patients WHERE keluarga_id=? AND id!=? AND deleted=0 ORDER BY id", (patient['keluarga_id'], patient_id))
        keluarga_members = cur.fetchall()
    
    milestone = get_milestone_info(soaps[0]['usia_kehamilan']) if soaps else None

    body = '''
    <div class="space-y-6">
      <!-- Full Patient Data (Read Only) -->
      <div class="card p-6 bg-gradient-to-r from-slate-800/50 to-slate-900/50 border-white/10">
        <div class="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
          <div>
            <h3 class="text-3xl font-black text-white mb-1">{{ patient['nama_pasien'] }}</h3>
            <p class="text-slate-400 font-medium">No RM: <span class="text-emerald-400 font-mono">{{ patient['nomor_rekam_medis'] }}</span></p>
          </div>
          <div class="flex items-center gap-2">
            <span class="pill {{ patient['status_antrian'] }}">{{ patient['status_antrian'] }}</span>
            <a class="btn bg-slate-700 hover:bg-slate-600 text-white" href="{{ url_for('patient_detail', patient_id=patient['id']) }}">⬅️ Kembali</a>
          </div>
        </div>
        <!-- Data Diri -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4 pt-4 border-t border-white/5 text-sm">
          <div><span class="text-slate-500 block text-[10px] font-bold uppercase tracking-wider">Umur / TTL</span><span class="text-white font-bold">{{ patient['umur'] or '-' }}<br><span class="text-slate-400 font-normal text-xs">{{ patient['tanggal_lahir'] or '-' }}</span></span></div>
          <div><span class="text-slate-500 block text-[10px] font-bold uppercase tracking-wider">NIK</span><span class="text-white font-bold">{{ patient['nik'] or '-' }}</span></div>
          <div><span class="text-slate-500 block text-[10px] font-bold uppercase tracking-wider">No. HP</span><span class="text-white font-bold">{{ patient['nomor_hp'] or '-' }}</span></div>
          <div><span class="text-slate-500 block text-[10px] font-bold uppercase tracking-wider">Alamat</span><span class="text-slate-300">{{ patient['alamat'] or '-' }}</span></div>
          <div><span class="text-slate-500 block text-[10px] font-bold uppercase tracking-wider">Gol. Darah</span><span class="text-white font-bold">{{ patient['golongan_darah'] or '-' }}</span></div>
          <div><span class="text-slate-500 block text-[10px] font-bold uppercase tracking-wider">Status</span><span class="text-white font-bold">{{ patient['status_perkawinan'] or '-' }}</span></div>
          <div><span class="text-slate-500 block text-[10px] font-bold uppercase tracking-wider">Pekerjaan</span><span class="text-white font-bold">{{ patient['pekerjaan'] or '-' }}</span></div>
          <div><span class="text-slate-500 block text-[10px] font-bold uppercase tracking-wider">Nama Suami/Keluarga</span><span class="text-white font-bold">{{ patient['nama_keluarga'] or '-' }}</span></div>
          <div><span class="text-slate-500 block text-[10px] font-bold uppercase tracking-wider">Layanan</span><span class="text-white font-bold">{{ patient['jenis_layanan'] or '-' }}</span></div>
          <div><span class="text-slate-500 block text-[10px] font-bold uppercase tracking-wider">Dokter Tujuan</span><span class="text-sky-400 font-bold">{{ patient['dokter_tujuan'] or '-' }}</span></div>
          <div><span class="text-slate-500 block text-[10px] font-bold uppercase tracking-wider">Hubungan</span><span class="text-white font-bold">{{ patient['hubungan'] or '-' }}</span></div>
          <div><span class="text-slate-500 block text-[10px] font-bold uppercase tracking-wider">Tanggal Daftar</span><span class="text-white font-bold">{{ fmt_dt(patient['created_at']) }}</span></div>
        </div>
        <!-- Antropometri -->
        <div class="grid grid-cols-4 gap-4 mt-3 pt-3 border-t border-white/5 text-xs">
          <div><span class="text-slate-500 block mb-1">TB (cm)</span><span class="text-white font-bold">{{ "%.1f"|format(patient['tinggi_badan']) if patient['tinggi_badan'] else '-' }}</span></div>
          <div><span class="text-slate-500 block mb-1">BB (kg)</span><span class="text-white font-bold">{{ "%.1f"|format(patient['berat_badan']) if patient['berat_badan'] else '-' }}</span></div>
          <div><span class="text-slate-500 block mb-1">Lingkar Perut</span><span class="text-white font-bold">{{ "%.1f"|format(patient['lingkar_perut']) if patient['lingkar_perut'] else '-' }}</span></div>
          <div><span class="text-slate-500 block mb-1">BMI</span><span class="text-white font-bold {% if patient['bmi'] and (patient['bmi']<18.5 or patient['bmi']>=25) %}text-amber-400{% else %}text-emerald-400{% endif %}">{{ "%.1f"|format(patient['bmi']) if patient['bmi'] else '-' }}</span></div>
        </div>
        <!-- Keluarga -->
        {% if keluarga_members %}
        <div class="mt-3 pt-3 border-t border-white/5">
          <div class="text-slate-500 text-[10px] font-bold uppercase tracking-wider mb-2">👨‍👩‍👧‍👦 Anggota Keluarga</div>
          <div class="flex flex-wrap gap-2">
            {% for km in keluarga_members %}
            <a href="{{ url_for('patient_history', patient_id=km['id']) }}" class="inline-flex items-center gap-2 px-3 py-2 rounded-xl bg-white/5 border border-white/10 hover:bg-emerald-500/10 hover:border-emerald-500/30 transition-all text-xs">
              <span class="text-slate-400">{{ km['hubungan'] or 'Anggota' }}:</span>
              <span class="text-white font-bold">{{ km['nama_pasien'] }}</span>
              <span class="text-slate-500 font-mono">({{ km['nomor_rekam_medis'] }})</span>
            </a>
            {% endfor %}
          </div>
        </div>
        {% endif %}
        <!-- Stats -->
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mt-4 pt-4 border-t border-white/5">
          <div class="p-3 rounded-2xl bg-white/5 border border-white/5 text-center">
            <div class="text-[10px] text-slate-500 uppercase font-bold mb-1">Total Kunjungan</div>
            <div class="text-2xl font-black text-emerald-400">{{ soaps|length }}</div>
          </div>
          <div class="p-3 rounded-2xl bg-white/5 border border-white/5 text-center">
            <div class="text-[10px] text-slate-500 uppercase font-bold mb-1">Dokumen</div>
            <div class="text-2xl font-black text-sky-400">{{ files|length }}</div>
          </div>
          <div class="p-3 rounded-2xl bg-white/5 border border-white/5 text-center">
            <div class="text-[10px] text-slate-500 uppercase font-bold mb-1">Transaksi</div>
            <div class="text-2xl font-black text-amber-400">{{ bills|length }}</div>
          </div>
          <div class="p-3 rounded-2xl bg-white/5 border border-white/5 text-center">
            <div class="text-[10px] text-slate-500 uppercase font-bold mb-1">Dibuat</div>
            <div class="text-sm font-bold text-white">{{ fmt_dt(patient['created_at']).split(' ')[0] }}</div>
          </div>
        </div>
      </div>

      <!-- Trend Pertumbuhan Janin -->
      <div class="card">
        <h3 class="text-lg font-bold mb-4 flex items-center gap-2">
          <span class="w-1.5 h-6 bg-sky-500 rounded-full"></span> Tren Pertumbuhan Janin
        </h3>
        <div class="text-[10px] text-slate-500 mb-2 italic">Area arsiran menunjukkan rentang normal WHO (Persentil 10-90)</div>
        <div class="h-[250px] w-full"><canvas id="historyGrowthChart"></canvas></div>
      </div>

      <!-- Monitoring Perkembangan -->
      <div class="card overflow-hidden">
        <div class="p-6 border-b border-white/5 bg-white/5">
          <h3 class="text-lg font-bold m-0 flex items-center gap-2">
            <span class="w-1.5 h-6 bg-emerald-500 rounded-full"></span>
            Monitoring Perkembangan Janin (USG)
          </h3>
        </div>
        <div class="table-wrap">
          <table class="w-full text-sm">
            <thead>
              <tr class="bg-white/5 text-slate-400 uppercase text-[10px] tracking-widest border-b border-white/10">
                <th class="px-6 py-4">Tgl Periksa</th>
                <th class="px-6 py-4">Usia Hamil</th>
                <th class="px-6 py-4">DJJ (bpm)</th>
                <th class="px-6 py-4 text-center">Posisi</th>
                <th class="px-6 py-4">Berat (gr)</th>
                <th class="px-6 py-4">Tensi</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-white/5">
              {% if soaps %}
                {% for s in soaps %} 
                <tr class="hover:bg-white/5 transition-colors">
                  <td class="px-6 py-4 text-slate-400">{{ fmt_dt(s['created_at']).split(' ')[0] }}</td>
                  <td class="px-6 py-4 font-bold text-white">{{ s['usia_kehamilan'] or '-' }}</td>
                  <td class="px-6 py-4 text-sky-400 font-bold">{{ s['detak_jantung_janin'] or '-' }}</td>
                  <td class="px-6 py-4 text-center"><span class="badge">{{ s['posisi_janin'] or '-' }}</span></td>
                  <td class="px-6 py-4 font-mono font-bold text-emerald-400">{{ s['estimasi_berat_janin'] or '-' }}</td>
                  <td class="px-6 py-4 text-xs">{{ s['td_sistolik'] or '-' }}/{{ s['td_diastolik'] or '-' }}</td>
                </tr>
                {% endfor %}
              {% else %}
                <tr><td colspan="6" class="py-12 text-center text-slate-500 italic">Data rekam medis belum tersedia.</td></tr>
              {% endif %}
            </tbody>
          </table>
        </div>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        <!-- Timeline Kunjungan -->
        <div class="lg:col-span-8 space-y-6">
          <h3 class="text-xl font-bold px-2 flex items-center gap-2">
            <span class="w-1.5 h-6 bg-cyan-500 rounded-full"></span>
            Timeline Pemeriksaan (SOAP)
          </h3>
          
          {% if milestone %}
          <div class="card bg-gradient-to-r from-emerald-500/10 to-cyan-500/10 border-emerald-500/30 p-4">
            <div class="flex items-center gap-4">
              <div class="text-3xl">✨</div>
              <div><div class="text-[10px] font-black uppercase text-emerald-400">Info Milestone Bunda</div><div class="text-sm italic text-slate-200">"{{ milestone }}"</div></div>
            </div>
          </div>
          {% endif %}

          <div class="space-y-6">
            {% if soaps %}
              {% for s in soaps %}
              <div class="relative pl-10 border-l-2 border-slate-800 pb-2">
                <!-- Timeline Indicator -->
                <div class="absolute -left-[11px] top-0 w-5 h-5 rounded-full bg-slate-900 border-4 border-emerald-500 z-10"></div>
                
                <div class="card p-6 bg-white/5 hover:bg-white/10 transition-all">
                  <div class="flex flex-col md:flex-row justify-between items-start gap-4 mb-4 border-b border-white/5 pb-4">
                    <div class="flex-1">
                      <div class="text-lg font-bold text-emerald-400 mb-1">{{ fmt_dt(s['created_at']) }}</div>
                      <div class="text-xs text-slate-500 font-bold uppercase tracking-wider">Dokter: {{ s['doctor_name'] or s['doctor_username'] or '-' }}</div>
                      
                      <div class="mt-3">
                        {% set risk = hitung_risiko_kehamilan(s['td_sistolik'], s['td_diastolik'], s['detak_jantung_janin']) %}
                        <span class="risk-badge {% if risk.status == 'Merah' %}risk-merah{% endif %}" style="background: {{ risk.bg }}; color: {{ risk.color }}; border: 1px solid {{ risk.color }};">
                          {{ '⚠️' if risk.status == 'Merah' else '⚡' if risk.status == 'Kuning' else '✅' }} {{ risk.label }}
                        </span>
                      </div>
                    </div>
                    <div class="flex gap-2">
                      {% if s['informed_consent'] %}<span class="badge bg-emerald-500/10 text-emerald-400 border-emerald-500/30">Informed Consent</span>{% endif %}
                      {% if soaps[0]['id'] == s['id'] and user['role'] != 'pasien' %}
                        {% set r = hitung_risiko_kehamilan(s['td_sistolik'], s['td_diastolik'], s['detak_jantung_janin']) %}
                        {% if r.status == 'Merah' %}
                          <a href="{{ url_for('referral_letter', patient_id=patient['id']) }}" class="badge bg-red-500 text-white animate-pulse">Cetak Rujukan</a>
                        {% endif %}
                      {% endif %}
                      <span class="badge bg-slate-800 text-slate-300 font-mono">{{ s['kode_icd10'] or 'ICD-10' }}</span>
                    </div>
                  </div>
                  
                  <div class="grid grid-cols-1 md:grid-cols-2 gap-6 text-sm">
                    <div class="space-y-2">
                      <span class="text-[10px] font-black text-slate-500 uppercase tracking-widest">Subjective</span>
                      <div class="wrap text-slate-300">{{ s['subjective'] or '-' }}</div>
                    </div>
                    <div class="space-y-2">
                      <span class="text-[10px] font-black text-slate-500 uppercase tracking-widest">Objective</span>
                      <div class="wrap text-slate-300">{{ s['objective'] or '-' }}</div>
                    </div>
                    <div class="md:col-span-2 pt-4 border-t border-white/5">
                      <span class="text-[10px] font-black text-slate-500 uppercase tracking-widest">Assessment & Plan</span>
                      <div class="mt-2 p-4 rounded-2xl bg-black/20 border border-white/5">
                        <div class="wrap text-white font-bold text-base mb-2">Diagnosis: {{ s['assessment'] or '-' }}</div>
                        <div class="wrap text-emerald-200 italic">Terapi: {{ s['plan'] or '-' }}</div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              {% endfor %}
            {% else %}
              <div class="empty py-16 card bg-white/5 border-dashed">
                <div class="text-4xl mb-2">📭</div>
                <div class="text-slate-500 font-medium">Belum ada riwayat pemeriksaan medis (SOAP).</div>
              </div>
            {% endif %}
          </div>
        </div>

        <!-- Sidebar Hasil Digital -->
        <div class="lg:col-span-4 space-y-6">
          <!-- Galeri -->
          <div class="card p-0 overflow-hidden">
            <div class="p-5 border-b border-white/5 bg-white/5 flex justify-between items-center">
              <h4 class="text-white font-bold m-0 flex items-center gap-2">🎞️ Galeri USG</h4>
              <span class="badge bg-emerald-500/10 text-emerald-400">{{ files|length }}</span>
            </div>
            <div class="p-4">
              {% if files %}
              <div class="space-y-3 max-h-[500px] overflow-y-auto pr-1">
                {% for f in files %}
                <a href="{{ url_for('file_view_auth', upload_id=f['id']) }}" target="_blank" class="flex items-center gap-3 p-3 rounded-2xl bg-slate-800/40 border border-white/5 hover:border-emerald-500/50 transition-all group">
                  <div class="w-12 h-12 rounded-xl bg-slate-700 flex items-center justify-center text-2xl group-hover:scale-110 transition-transform">
                    {% if f['file_ext'] in ['jpg','jpeg','png'] %}🖼️{% elif f['file_ext']=='pdf' %}📄{% else %}🎞️{% endif %}
                  </div>
                  <div class="flex-1 min-w-0">
                    <div class="text-xs text-white font-bold truncate">{{ f['original_filename'] }}</div>
                    <div class="text-[10px] text-slate-500 mt-0.5">{{ fmt_dt(f['created_at']) }}</div>
                  </div>
                </a>
                {% endfor %}
              </div>
              {% else %}
              <div class="text-center py-10 text-slate-500 text-xs italic">Belum ada dokumen yang di-upload.</div>
              {% endif %}
            </div>
          </div>

          <!-- Billing -->
          <div class="card p-0 overflow-hidden">
            <div class="p-5 border-b border-white/5 bg-white/5">
              <h4 class="text-white font-bold m-0 flex items-center gap-2">💳 Tagihan Pasien</h4>
            </div>
            <div class="p-4">
              {% if bills %}
              <div class="space-y-3">
                {% for b in bills %}
                <div class="p-4 rounded-2xl bg-white/5 border border-white/5">
                  <div class="flex justify-between items-center mb-2">
                    <span class="text-xs text-slate-200 font-bold">{{ b['item_name'] }}</span>
                    <span class="pill {{ 'selesai' if b['status_bayar']=='lunas' else 'unpaid' }}">{{ b['status_bayar'] }}</span>
                  </div>
                  <div class="flex justify-between items-end">
                    <span class="text-[10px] text-slate-500">{{ fmt_dt(b['created_at']).split(' ')[0] }}</span>
                    <span class="text-lg font-black text-white">{{ rupiah(b['amount']) }}</span>
                  </div>
                </div>
                {% endfor %}
              </div>
              {% else %}
              <div class="text-center py-10 text-slate-500 text-xs italic">Belum ada rincian tagihan.</div>
              {% endif %}
            </div>
          </div>
        </div>
      </div>
    <script>
    document.addEventListener('DOMContentLoaded', function() {
      if (!document.getElementById('historyGrowthChart')) return;
      fetch('/api/fetal_growth/{{ patient["id"] }}')
        .then(r => r.json())
        .then(data => {
          const whoP10 = [250, 450, 750, 1100, 1600, 2100, 2600, 2900];
          const whoP90 = [380, 700, 1100, 1650, 2300, 2900, 3500, 4100];
          const ctx = document.getElementById('historyGrowthChart').getContext('2d');
          new Chart(ctx, {
            type: 'line',
            data: {
              labels: data.labels,
              datasets: [
                { label: 'Rentang Normal (WHO P90)', data: whoP90, borderColor: 'rgba(34,197,94,0.2)', backgroundColor: 'rgba(34,197,94,0.05)', fill: '+1', pointRadius: 0, tension: 0.4 },
                { label: 'Rentang Normal (WHO P10)', data: whoP10, borderColor: 'rgba(34,197,94,0.2)', backgroundColor: 'transparent', fill: false, pointRadius: 0, tension: 0.4 },
                { label: 'Berat Janin (gr)', data: data.ebj, borderColor: '#10b981', backgroundColor: 'rgba(16,185,129,0.1)', yAxisID: 'y', tension: 0.3 },
                { label: 'DJJ (bpm)', data: data.djj, borderColor: '#0ea5e9', backgroundColor: 'transparent', yAxisID: 'y1', borderDash: [5, 5], tension: 0.3 }
              ]
            },
            options: {
              responsive: true, maintainAspectRatio: false,
              scales: {
                y: { type: 'linear', display: true, position: 'left', grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#7b93b5' } },
                y1: { type: 'linear', display: true, position: 'right', grid: { drawOnChartArea: false }, ticks: { color: '#7b93b5' } },
                x: { grid: { display: false }, ticks: { color: '#7b93b5' } }
              },
              plugins: { legend: { labels: { color: '#7b93b5', boxWidth: 10, font: { size: 9 }, filter: function(item) { return !item.text.includes('Rentang'); } } } }
            }
          });
        });
    });
    </script>
    '''
    return render_page('Riwayat Pemeriksaan Pasien', body, patient=patient, soaps=soaps, files=files, bills=bills, fmt_dt=fmt_dt, rupiah=rupiah, file_badge=file_badge, milestone=milestone)


@app.route('/uploads')
@role_required('superadmin', 'admin', 'dokter')
def uploads_page():
    q = request.args.get('q', '').strip()
    conn = get_db(); cur = conn.cursor()
    sql = '''SELECT up.*, p.nama_pasien, p.nomor_rekam_medis, u.username
             FROM uploads up JOIN patients p ON up.patient_id=p.id LEFT JOIN users u ON up.uploader_id=u.id WHERE 1=1'''
    params = []
    if q:
        like = '%' + q + '%'
        sql += ' AND (p.nama_pasien LIKE ? OR p.nomor_rekam_medis LIKE ? OR up.original_filename LIKE ?)'
        params += [like, like, like]
    sql += ' ORDER BY up.created_at DESC'
    cur.execute(sql, tuple(params)); rows = cur.fetchall()
    body = '''
    <div class="card no-print"><form class="searchbox"><input class="input" name="q" value="{{ q }}" placeholder="Cari pasien / RM / nama file..."><button class="btn btn-primary">🔍 Cari</button></form></div>
    <div class="card" style="margin-top:16px"><div style="display:flex;justify-content:space-between;align-items:center;gap:10px"><h3 style="margin:0">Semua Upload Hasil USG</h3><span class="badge">{{ rows|length }} file</span></div>{% if rows %}<table><thead><tr><th>Pasien</th><th>File</th><th>Tipe</th><th>Ukuran</th><th>Tanggal</th><th>Aksi</th></tr></thead><tbody>{% for r in rows %}<tr><td><strong>{{ r['nama_pasien'] }}</strong><div class="small muted">{{ r['nomor_rekam_medis'] }}</div></td><td>{{ r['original_filename'] }}<div class="small muted">Uploader: {{ r['username'] or '-' }}</div></td><td>{{ file_badge(r['file_ext']) }}</td><td>{{ '%.2f MB'|format((r['file_size'] or 0)/1024/1024) }}</td><td>{{ fmt_dt(r['created_at']) }}</td><td><a class="btn btn-sm" href="{{ url_for('file_view_auth', upload_id=r['id']) }}" target="_blank">Buka</a></td></tr>{% endfor %}</tbody></table>{% else %}<div class="empty">Belum ada upload.</div>{% endif %}</div>
    '''
    return render_page('Hasil Upload USG', body, q=q, rows=rows, file_badge=file_badge, fmt_dt=fmt_dt)


@app.route('/file/<int:upload_id>')
@role_required('superadmin', 'admin', 'dokter', 'pasien')
def file_view_auth(upload_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT up.*, p.id pid FROM uploads up JOIN patients p ON up.patient_id=p.id WHERE up.id=?', (upload_id,))
    row = cur.fetchone()
    if not row: abort(404)
    patient = get_patient(row['pid'])
    if not patient_allowed(patient): abort(403)
    return send_from_directory(UPLOAD_DIR, row['stored_filename'], as_attachment=False, download_name=row['original_filename'])


@app.route('/hasil/<token>')
def patient_result(token):
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT * FROM patients WHERE access_token=?', (token,)); patient = cur.fetchone()
    if not patient:
        abort(404)
    cur.execute('SELECT * FROM uploads WHERE patient_id=? ORDER BY created_at DESC', (patient['id'],)); files = cur.fetchall()
    cur.execute('SELECT st.*, u.full_name doctor_name, u.username doctor_username FROM soap_records st LEFT JOIN users u ON st.doctor_id=u.id WHERE st.patient_id=? ORDER BY st.created_at DESC LIMIT 5', (patient['id'],)); soaps = cur.fetchall()
    cur.execute('SELECT SUM(amount) FROM billing WHERE patient_id=?', (patient['id'],)); total_bill = cur.fetchone()[0] or 0
    
    milestone = None
    if soaps:
        milestone = get_milestone_info(soaps[0]['usia_kehamilan'])

    body = '''
    <div class="authbox"><div class="card"><div class="hero"><div><h2 style="margin:0">Hasil USG Pasien</h2><div class="muted">Halaman aman berbasis token unik. Data pasien lain tidak dapat diakses dari halaman ini.</div><div class="pill-list" style="margin-top:10px"><span class="pill">Nama: {{ patient['nama_pasien'] }}</span><span class="pill">No RM: {{ patient['nomor_rekam_medis'] }}</span><span class="pill">Status: {{ patient['status_antrian'] }}</span></div></div><div class="toolbar no-print"><button class="btn btn-primary" onclick="printPage()">🖨️ Cetak Hasil</button></div></div><div class="g2 grid" style="margin-top:16px"><div class="card"><h3>Ringkasan Pemeriksaan</h3>{% if soaps %}{% set s = soaps[0] %}<div class="small muted">Pemeriksaan terbaru: {{ fmt_dt(s['created_at']) }} oleh {{ s['doctor_name'] or s['doctor_username'] or '-' }}</div><div class="wrap" style="margin-top:8px"><strong>Assessment:</strong> {{ s['assessment'] or '-' }}</div><div class="wrap"><strong>Plan:</strong> {{ s['plan'] or '-' }}</div><div class="pill-list" style="margin-top:10px"><span class="pill">Usia Kehamilan: {{ s['usia_kehamilan'] or '-' }}</span><span class="pill">DJJ: {{ s['detak_jantung_janin'] or '-' }}</span><span class="pill">Posisi: {{ s['posisi_janin'] or '-' }}</span><span class="pill">EBJ: {{ s['estimasi_berat_janin'] or '-' }}</span></div>{% if s['catatan_dokter'] %}<div class="wrap" style="margin-top:10px"><strong>Catatan Dokter:</strong> {{ s['catatan_dokter'] }}</div>{% endif %}{% if s['rekomendasi_kontrol_ulang'] %}<div class="wrap"><strong>Kontrol Ulang:</strong> {{ s['rekomendasi_kontrol_ulang'] }}</div>{% endif %}{% else %}<div class="empty">Belum ada ringkasan pemeriksaan.</div>{% endif %}</div><div class="card"><h3>Ringkasan Billing</h3><div class="stat"><div class="small muted">Total tagihan tercatat</div><div style="font-size:30px;font-weight:800">{{ rupiah(total_bill) }}</div></div><div class="small muted" style="margin-top:10px">Hubungi klinik untuk rincian pembayaran bila diperlukan.</div></div></div><div class="card" style="margin-top:16px"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><h3 style="margin:0">File Hasil USG</h3><span class="badge">{{ files|length }} file</span></div>{% if files %}<table><thead><tr><th>Nama File</th><th>Tipe</th><th>Tanggal</th><th>Aksi</th></tr></thead><tbody>{% for f in files %}<tr><td>{{ f['original_filename'] }}</td><td>{{ file_badge(f['file_ext']) }}</td><td>{{ fmt_dt(f['created_at']) }}</td><td><a class="btn btn-sm" href="{{ url_for('patient_file_public', token=patient['access_token'], upload_id=f['id']) }}" target="_blank">Buka File</a></td></tr>{% endfor %}</tbody></table>{% else %}<div class="empty">Belum ada file hasil.</div>{% endif %}</div></div></div>
    {% if milestone %}
    <div class="card" style="margin-top:16px; background: linear-gradient(135deg, rgba(14,165,233,0.1), rgba(34,197,94,0.1)); border: 1px solid var(--accent);">
      <h3 style="color: var(--accent); margin-bottom: 8px;">✨ Tahukah Bunda?</h3>
      <div class="text-sm italic">"{{ milestone }}"</div>
    </div>
    {% endif %}
    <div class="card" style="margin-top:16px"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><h3 style="margin:0">File Hasil USG</h3><span class="badge">{{ files|length }} file</span></div>{% if files %}<table><thead><tr><th>Nama File</th><th>Tipe</th><th>Tanggal</th><th>Aksi</th></tr></thead><tbody>{% for f in files %}<tr><td>{{ f['original_filename'] }}</td><td>{{ file_badge(f['file_ext']) }}</td><td>{{ fmt_dt(f['created_at']) }}</td><td><a class="btn btn-sm" href="{{ url_for('patient_file_public', token=patient['access_token'], upload_id=f['id']) }}" target="_blank">Buka File</a></td></tr>{% endfor %}</tbody></table>{% else %}<div class="empty">Belum ada file hasil.</div>{% endif %}</div></div></div>
    '''
    return render_page('Hasil Pasien - ' + patient['nama_pasien'], body, patient=patient, files=files, soaps=soaps, total_bill=total_bill, file_badge=file_badge, fmt_dt=fmt_dt, rupiah=rupiah, milestone=milestone)


@app.route('/hasil/<token>/file/<int:upload_id>')
def patient_file_public(token, upload_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT up.* FROM uploads up JOIN patients p ON up.patient_id=p.id WHERE up.id=? AND p.access_token=?', (upload_id, token))
    row = cur.fetchone()
    if not row: abort(404)
    return send_from_directory(UPLOAD_DIR, row['stored_filename'], as_attachment=False, download_name=row['original_filename'])


@app.route('/patients/<int:patient_id>/delete', methods=['POST'])
@role_required('superadmin')
def patient_delete(patient_id):
    user = current_user()
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE patients SET deleted=1, deleted_at=?, deleted_by=?, status_antrian='selesai' WHERE id=?", (now(), user['id'], patient_id))
    conn.commit()
    log_action('SOFT_DELETE_PATIENT', f'Hapus (soft) pasien ID #{patient_id}')
    flash('Data pasien telah dihapus. Masih bisa dilihat di menu Arsip Pasien.', 'info')
    return redirect(url_for('patients'))


@app.route('/patients/deleted')
@role_required('superadmin', 'admin')
def patients_deleted():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id, nama_pasien, nomor_rekam_medis, nik, nomor_hp, deleted_at, deleted_by FROM patients WHERE deleted=1 ORDER BY deleted_at DESC LIMIT 100")
    rows = cur.fetchall()
    body = '''
    <div class="space-y-4">
      <div class="card">
        <div class="flex items-center gap-2 mb-4">
          <h3 class="text-lg font-bold m-0">🗂️ Arsip Pasien (Soft-Delete)</h3>
          <span class="badge">{{ rows|length }} data</span>
        </div>
        <div class="small muted mb-4">Data pasien yang dihapus masih tersimpan dan dapat dikembalikan (restore).</div>
        {% if rows %}
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>Nama</th><th>RM</th><th>NIK / HP</th><th>Dihapus Pada</th><th>Aksi</th></tr>
            </thead>
            <tbody>
              {% for r in rows %}
              <tr class="text-sm">
                <td><strong class="text-slate-300">{{ r['nama_pasien'] }}</strong></td>
                <td class="font-mono text-xs">{{ r['nomor_rekam_medis'] }}</td>
                <td class="text-xs text-slate-400">{{ r['nik'] or '-' }} / {{ r['nomor_hp'] or '-' }}</td>
                <td class="text-xs">{{ fmt_dt(r['deleted_at']) }}</td>
                <td>
                  <form method="post" action="{{ url_for('patient_restore', patient_id=r['id']) }}" style="display:inline" onsubmit="return confirm('Kembalikan {{ r['nama_pasien'] }}?')">
                    <button class="btn btn-sm btn-primary">↩ Restore</button>
                  </form>
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
        {% else %}
        <div class="text-center py-12 text-slate-500">Tidak ada data pasien yang dihapus.</div>
        {% endif %}
      </div>
      <a class="btn" href="{{ url_for('patients') }}">⬅ Kembali ke Data Pasien</a>
    </div>
    '''
    return render_page('Arsip Pasien', body, rows=rows, fmt_dt=fmt_dt)


@app.route('/patients/<int:patient_id>/restore', methods=['POST'])
@role_required('superadmin', 'admin')
def patient_restore(patient_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE patients SET deleted=0, restored_at=?, deleted_by=NULL WHERE id=?", (now(), patient_id))
    conn.commit()
    log_action('RESTORE_PATIENT', f'Restore pasien ID #{patient_id}')
    flash('Data pasien berhasil dikembalikan.', 'success')
    return redirect(url_for('patients_deleted'))


@app.route('/soap/<int:soap_id>/delete', methods=['POST'])
@role_required('superadmin', 'admin', 'dokter')
def soap_delete(soap_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT patient_id FROM soap_records WHERE id=?', (soap_id,))
    row = cur.fetchone()
    if not row: abort(404)
    pid = row['patient_id']
    cur.execute('DELETE FROM soap_records WHERE id=?', (soap_id,))
    conn.commit()
    log_action('DELETE_SOAP', f'Hapus SOAP ID #{soap_id}')
    flash('Catatan rekam medis berhasil dihapus.', 'info')
    return redirect(url_for('patient_detail', patient_id=pid))


@app.route('/billing/<int:billing_id>/delete', methods=['POST'])
@role_required('superadmin', 'admin')
def billing_delete(billing_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT patient_id FROM billing WHERE id=?', (billing_id,))
    row = cur.fetchone()
    if not row: abort(404)
    pid = row['patient_id']
    cur.execute('DELETE FROM billing WHERE id=?', (billing_id,))
    conn.commit()
    log_action('DELETE_BILLING', f'Hapus item billing #{billing_id}')
    flash('Item tagihan berhasil dihapus.', 'info')
    return redirect(url_for('patient_detail', patient_id=pid))


@app.route('/file/<int:upload_id>/delete', methods=['POST'])
@role_required('superadmin', 'admin', 'dokter')
def file_delete(upload_id):
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT stored_filename, patient_id FROM uploads WHERE id=?', (upload_id,))
    row = cur.fetchone()
    if not row: abort(404)
    pid = row['patient_id']
    path = os.path.join(UPLOAD_DIR, row['stored_filename'])
    if os.path.exists(path): os.remove(path)
    cur.execute('DELETE FROM uploads WHERE id=?', (upload_id,))
    conn.commit()
    log_action('DELETE_UPLOAD', f'Hapus file upload #{upload_id}')
    flash('File hasil USG berhasil dihapus.', 'success')
    return redirect(url_for('patient_detail', patient_id=pid))


@app.route('/soap-templates', methods=['GET', 'POST'])
@role_required('superadmin', 'admin', 'dokter')
def soap_templates_page():
    user = current_user()
    conn = get_db(); cur = conn.cursor()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if title:
            cur.execute('INSERT INTO soap_templates (title,subjective,objective,assessment,plan,created_by,created_at) VALUES (?,?,?,?,?,?,?)', (title, request.form.get('subjective','').strip(), request.form.get('objective','').strip(), request.form.get('assessment','').strip(), request.form.get('plan','').strip(), user['id'], now()))
            conn.commit(); log_action('CREATE_SOAP_TEMPLATE', title); flash('Template SOAP berhasil ditambahkan.', 'success'); return redirect(url_for('soap_templates_page'))
        flash('Judul template wajib diisi.', 'danger')
    cur.execute('SELECT st.*, u.username FROM soap_templates st LEFT JOIN users u ON st.created_by=u.id ORDER BY st.id DESC'); rows = cur.fetchall()
    body = '''
    <div class="g2 grid"><div class="card no-print"><h3>Tambah Template SOAP Cepat</h3><form method="post" class="grid"><div><label>Judul Template</label><input class="input" name="title" required></div><div><label>Subjective</label><textarea class="textarea" name="subjective"></textarea></div><div><label>Objective</label><textarea class="textarea" name="objective"></textarea></div><div><label>Assessment</label><textarea class="textarea" name="assessment"></textarea></div><div><label>Plan</label><textarea class="textarea" name="plan"></textarea></div><button class="btn btn-primary">💾 Simpan Template</button></form></div><div class="card"><div style="display:flex;justify-content:space-between;align-items:center;gap:10px"><h3 style="margin:0">Daftar Template SOAP</h3><span class="badge">{{ rows|length }} template</span></div>{% if rows %}{% for r in rows %}<div class="card" style="padding:14px;margin-bottom:12px"><div style="display:flex;justify-content:space-between;align-items:center;gap:10px"><strong>{{ r['title'] }}</strong><span class="small muted">{{ r['username'] or '-' }}</span></div><div class="wrap"><strong>S:</strong> {{ r['subjective'] or '-' }}</div><div class="wrap"><strong>O:</strong> {{ r['objective'] or '-' }}</div><div class="wrap"><strong>A:</strong> {{ r['assessment'] or '-' }}</div><div class="wrap"><strong>P:</strong> {{ r['plan'] or '-' }}</div></div>{% endfor %}{% else %}<div class="empty">Belum ada template.</div>{% endif %}</div></div>
    '''
    return render_page('Template SOAP Cepat', body, rows=rows)




@app.route('/panduan/admin')
@role_required('superadmin', 'admin')
def panduan_admin():
    body = '''
    <div class="hero card mb-6">
        <div>
            <h3 class="text-2xl font-bold text-white mb-2">👩‍💻 SOP Panduan Admin</h3>
            <div class="text-slate-400">Prosedur operasional sistem khusus untuk Admin/Resepsionis.</div>
        </div>
    </div>
    
    <div class="g2 grid mb-6">
        <div class="card">
            <h4 class="text-sky-400 font-bold mb-4 flex items-center gap-2">1. Registrasi & Pendaftaran</h4>
            <div class="space-y-4 text-sm text-slate-300">
                <div class="p-3 bg-slate-800/50 rounded-xl border border-slate-700">
                    <strong class="text-white">Pasien Baru:</strong> Masuk ke menu <b>"Input Pasien"</b>. Isi kelengkapan data diri dan atur status ke <b>"menunggu"</b>.
                </div>
                <div class="p-3 bg-slate-800/50 rounded-xl border border-slate-700">
                    <strong class="text-white">Pasien Lama:</strong> Di menu Input Pasien, cari nama atau RM di kotak pencarian teratas. Jika ketemu, klik untuk auto-fill, dan daftarkan kembali ke antrian hari ini.
                </div>
            </div>
        </div>
        
        <div class="card">
            <h4 class="text-emerald-400 font-bold mb-4 flex items-center gap-2">2. Manajemen Kasir (Billing)</h4>
            <div class="space-y-4 text-sm text-slate-300">
                <div class="p-3 bg-slate-800/50 rounded-xl border border-slate-700">
                    <strong class="text-white">Input Tagihan:</strong> Setelah diperiksa dokter, gulir ke kotak "Billing" pada detail pasien. Masukkan nama layanan dan nominal harga.
                </div>
                <div class="p-3 bg-slate-800/50 rounded-xl border border-slate-700">
                    <strong class="text-white">Pelunasan:</strong> Setelah dibayar, tekan <b>"LUNAS"</b>. Jangan lupa ubah status antrian pasien tersebut menjadi <b>"selesai"</b> agar hilang dari antrian aktif dokter.
                </div>
            </div>
        </div>
    </div>
    '''
    return render_page('SOP Admin', body)


@app.route('/panduan/dokter')
@role_required('superadmin', 'dokter')
def panduan_dokter():
    body = '''
    <div class="hero card mb-6">
        <div>
            <h3 class="text-2xl font-bold text-white mb-2">👨‍⚕️ SOP Panduan Dokter</h3>
            <div class="text-slate-400">Panduan standar pelayanan medis dan penggunaan fitur USG 4D.</div>
        </div>
    </div>
    
    <div class="g2 grid mb-6">
        <div class="card">
            <h4 class="text-emerald-400 font-bold mb-4 flex items-center gap-2">1. Pemeriksaan SOAP & Deteksi Dini</h4>
            <div class="space-y-3 text-sm text-slate-300">
                <div class="p-3 bg-slate-800/50 rounded-xl border border-slate-700">
                    <strong class="text-white">Memanggil Pasien:</strong> Buka menu <b>"Antrian Hari Ini"</b>. Klik "Periksa" untuk masuk ke halaman detail medis pasien.
                </div>
                <div class="p-3 bg-slate-800/50 rounded-xl border border-slate-700">
                    <strong class="text-white">Pengisian SOAP:</strong> Isi keluhan dan diagnosis. Anda dapat menggunakan <i>Template SOAP Cepat</i> untuk auto-fill standar SOP.
                </div>
                <div class="p-3 bg-amber-500/10 rounded-xl border border-amber-500/30">
                    <strong class="text-amber-400">Wajib Diisi (Algoritma Risiko):</strong> Kolom <b>TD Sistolik/Diastolik</b> dan <b>DJJ</b> harus diisi angka. Sistem akan memunculkan <i>Badge Merah/Kuning/Hijau</i> otomatis mendeteksi bahaya (seperti Preeklampsia).
                </div>
            </div>
        </div>
        
        <div class="card">
            <h4 class="text-sky-400 font-bold mb-4 flex items-center gap-2">2. Upload Hasil & Kurva Janin</h4>
            <div class="space-y-3 text-sm text-slate-300">
                <div class="p-3 bg-slate-800/50 rounded-xl border border-slate-700">
                    <strong class="text-white">Upload Digital:</strong> Pilih file foto/video USG. Klik <b>"Copy Link"</b> dan kirim via WhatsApp ke pasien agar mereka dapat mendownload mandiri.
                </div>
                <div class="p-3 bg-slate-800/50 rounded-xl border border-slate-700">
                    <strong class="text-white">Membaca Kurva:</strong> Grafik di bagian atas detail pasien (EBJ & DJJ) membaca riwayat pemeriksaan sebelumnya untuk memantau tren pertumbuhan janin.
                </div>
            </div>
        </div>
    </div>
    '''
    return render_page('SOP Dokter', body)



@app.route('/sop')
@role_required('superadmin', 'admin', 'dokter')
def sop_page():
    body = '''
    <div class="hero card"><div><h3 style="margin:0">SOP Operasional Klinik USG</h3><div class="muted">Panduan ringkas alur kerja admin dan dokter.</div></div><a class="btn btn-primary no-print" href="{{ url_for('patient_new') }}">➕ Input Pasien</a></div>
    <div class="g2 grid" style="margin-top:16px">
      <div class="card"><h3>1. Registrasi Pasien</h3><ul><li>Input identitas minimal: nama, HP, umur/tanggal lahir, layanan.</li><li>Pilih dokter tujuan dan status awal <strong>menunggu</strong>.</li><li>Pastikan No. RM unik atau gunakan auto-generate.</li></ul></div>
      <div class="card"><h3>2. Pemeriksaan Dokter</h3><ul><li>Buka detail pasien dari daftar antrian.</li><li>Isi SOAP: Subjective, Objective, Assessment, Plan.</li><li>Lengkapi data USG: usia kehamilan, DJJ, posisi janin, EBJ.</li></ul></div>
      <div class="card"><h3>3. Upload Hasil</h3><ul><li>Upload hasil USG format jpg/png/pdf/mp4/mov.</li><li>Gunakan link/QR pasien untuk berbagi hasil.</li><li>Jangan kirim data pasien lain melalui link yang sama.</li></ul></div>
      <div class="card"><h3>4. Billing & Penutupan</h3><ul><li>Admin input item billing dan status pembayaran.</li><li>Set antrian menjadi <strong>selesai</strong> setelah layanan selesai.</li><li>Backup database rutin dari tombol Backup.</li></ul></div>
    </div>
    '''
    return render_page('SOP Klinik', body)


@app.route('/billing')
@role_required('superadmin', 'admin')
def billing_page():
    q = request.args.get('q', '').strip()
    conn = get_db(); cur = conn.cursor()
    sql = 'SELECT b.*, p.nama_pasien, p.nomor_rekam_medis FROM billing b JOIN patients p ON b.patient_id=p.id WHERE 1=1'; params = []
    if q:
        like = '%' + q + '%'; sql += ' AND (p.nama_pasien LIKE ? OR p.nomor_rekam_medis LIKE ? OR b.item_name LIKE ?)'; params += [like, like, like]
    sql += ' ORDER BY b.created_at DESC'; cur.execute(sql, tuple(params)); rows = cur.fetchall()
    body = '''
    <div class="card no-print"><form class="searchbox"><input class="input" name="q" value="{{ q }}" placeholder="Cari pasien / RM / item billing..."><button class="btn btn-primary">🔍 Cari</button></form></div>
    <div class="card" style="margin-top:16px"><div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap"><h3 style="margin:0">Billing Klinik</h3><span class="badge">{{ rows|length }} transaksi</span></div>{% if rows %}<div class="table-wrap"><table><thead><tr><th>Pasien</th><th>Item</th><th>Nominal</th><th>Status</th><th>Tanggal</th><th class="action-col">Aksi</th></tr></thead><tbody>{% for r in rows %}<tr><td><strong>{{ r['nama_pasien'] }}</strong><div class="small muted">{{ r['nomor_rekam_medis'] }}</div></td><td>{{ r['item_name'] }}<div class="small muted">{{ r['notes'] or '' }}</div></td><td>{{ rupiah(r['amount']) }}</td><td><span class="badge {{ 'paid' if r['status_bayar']=='lunas' else 'unpaid' }}">{{ r['status_bayar'] }}</span></td><td>{{ fmt_dt(r['created_at']) }}</td><td class="action-col"><div class="action-buttons">{% if r['status_bayar'] != 'lunas' %}<form method="post" action="{{ url_for('billing_set_lunas', billing_id=r['id']) }}"><button class="btn btn-primary btn-sm">✅ Lunas</button></form>{% else %}<span class="badge paid">Sudah Lunas</span>{% endif %}</div></td></tr>{% endfor %}</tbody></table></div>{% else %}<div class="empty">Belum ada billing.</div>{% endif %}</div>
    '''
    return render_page('Billing', body, q=q, rows=rows, rupiah=rupiah, fmt_dt=fmt_dt)



@app.route('/billing/<int:billing_id>/set_lunas', methods=['POST'])
@role_required('superadmin','admin')
def billing_set_lunas(billing_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE billing SET status_bayar='lunas' WHERE id=?", (billing_id,))
    conn.commit()
    flash('Billing berhasil ditandai lunas.', 'success')
    return redirect(request.referrer or url_for('billing_page'))


@app.route('/users', methods=['GET', 'POST'])
@role_required('superadmin')
def users_page():
    conn = get_db(); cur = conn.cursor()
    if request.method == 'POST':
        username = request.form.get('username','').strip(); full_name = request.form.get('full_name','').strip(); role = request.form.get('role','').strip(); password = request.form.get('password','').strip(); patient_id = request.form.get('patient_id','').strip() or None
        if not username or not password or role not in ('superadmin','admin','dokter','pasien'):
            flash('Data user tidak valid.', 'danger')
        else:
            try:
                cur.execute('INSERT INTO users (username,password_hash,role,full_name,patient_id,active,created_at,updated_at) VALUES (?,?,?,?,?,1,?,?)', (username, generate_password_hash(password), role, full_name, patient_id, now(), now()))
                conn.commit(); log_action('CREATE_USER', username + ' (' + role + ')'); flash('User berhasil dibuat.', 'success'); return redirect(url_for('users_page'))
            except sqlite3.IntegrityError:
                flash('Username sudah dipakai.', 'danger')
    cur.execute('SELECT id,nama_pasien,nomor_rekam_medis FROM patients ORDER BY nama_pasien'); patients_list = cur.fetchall()
    cur.execute('SELECT * FROM users ORDER BY id DESC'); rows = cur.fetchall()
    body = '''
    <div class="g2 grid"><div class="card no-print"><h3>Tambah User</h3><form method="post" class="grid"><div><label>Username</label><input class="input" name="username" required></div><div><label>Nama Lengkap</label><input class="input" name="full_name"></div><div><label>Password</label><input class="input" type="password" name="password" required></div><div><label>Role</label><select class="select" name="role"><option value="admin">admin</option><option value="dokter">dokter</option><option value="pasien">pasien</option><option value="superadmin">superadmin</option></select></div><div><label>Tautkan ke pasien (opsional untuk role pasien)</label><select class="select" name="patient_id"><option value="">- Tidak ditautkan -</option>{% for p in patients_list %}<option value="{{ p['id'] }}">{{ p['nama_pasien'] }} - {{ p['nomor_rekam_medis'] }}</option>{% endfor %}</select></div><button class="btn btn-primary">👤 Simpan User</button></form></div><div class="card"><div style="display:flex;justify-content:space-between;align-items:center;gap:10px"><h3 style="margin:0">Daftar User</h3><span class="badge">{{ rows|length }} user</span></div><table><thead><tr><th>Username</th><th>Role</th><th>Nama</th><th>Patient ID</th><th>Aktif</th></tr></thead><tbody>{% for r in rows %}<tr><td>{{ r['username'] }}</td><td>{{ r['role'] }}</td><td>{{ r['full_name'] or '-' }}</td><td>{{ r['patient_id'] or '-' }}</td><td>{{ 'Ya' if r['active'] else 'Tidak' }}</td></tr>{% endfor %}</tbody></table></div></div>
    '''
    return render_page('Manajemen User', body, rows=rows, patients_list=patients_list)


@app.route('/audit-logs')
@role_required('superadmin', 'admin')
def audit_logs_page():
    q = request.args.get('q', '').strip(); conn = get_db(); cur = conn.cursor(); sql = 'SELECT * FROM audit_logs WHERE 1=1'; params = []
    if q:
        like = '%' + q + '%'; sql += ' AND (username LIKE ? OR action LIKE ? OR details LIKE ?)'; params += [like, like, like]
    sql += ' ORDER BY id DESC LIMIT 300'; cur.execute(sql, tuple(params)); rows = cur.fetchall()
    body = '''
    <div class="card no-print"><form class="searchbox"><input class="input" name="q" value="{{ q }}" placeholder="Cari username / action / detail..."><button class="btn btn-primary">🔍 Cari</button></form></div>
    <div class="card" style="margin-top:16px"><div style="display:flex;justify-content:space-between;align-items:center;gap:10px"><h3 style="margin:0">Audit Log Aktivitas</h3><span class="badge">max 300</span></div>{% if rows %}<table><thead><tr><th>Waktu</th><th>User</th><th>Aksi</th><th>Detail</th><th>IP</th></tr></thead><tbody>{% for r in rows %}<tr><td>{{ fmt_dt(r['created_at']) }}</td><td>{{ r['username'] or '-' }}</td><td><strong>{{ r['action'] }}</strong></td><td>{{ r['details'] or '' }}</td><td>{{ r['ip_address'] or '-' }}</td></tr>{% endfor %}</tbody></table>{% else %}<div class="empty">Belum ada aktivitas audit.</div>{% endif %}</div>
    '''
    return render_page('Audit Log', body, q=q, rows=rows, fmt_dt=fmt_dt)


@app.route('/settings', methods=['GET', 'POST'])
@role_required('superadmin', 'admin', 'dokter', 'pasien')
def settings():
    user = current_user()
    if request.method == 'POST':
        action = request.form.get('action', '')

        # Update master options (superadmin/admin only)
        if action == 'save_master_opts' and user['role'] in ('superadmin', 'admin'):
            conn = get_db(); cur = conn.cursor()
            for cat in ['golongan_darah', 'jenis_layanan', 'pekerjaan']:
                val = request.form.get(cat, '').strip()
                if val:
                    cur.execute('INSERT OR REPLACE INTO master_options(category, options_text) VALUES(?,?)', (cat, val))
            conn.commit()
            log_action('UPDATE_MASTER_OPTS', 'Master options diperbarui')
            flash('Opsi dropdown berhasil diperbarui.', 'success')
            return redirect(url_for('settings'))

        # Update Nama
        new_name = request.form.get('full_name', '').strip()
        if 'full_name' in request.form and action != 'save_master_opts':
            if not new_name:
                flash('Nama tidak boleh kosong.', 'danger')
            elif new_name != user['full_name']:
                conn = get_db(); cur = conn.cursor()
                cur.execute('UPDATE users SET full_name=?, updated_at=? WHERE id=?', (new_name, now(), user['id']))
                conn.commit()
                log_action('UPDATE_PROFILE', 'Nama diubah menjadi: ' + new_name)
                flash('Nama profil berhasil diperbarui.', 'success')
                return redirect(url_for('settings'))

        # Update Password
        old = request.form.get('old_password',''); new = request.form.get('new_password',''); conf = request.form.get('confirm_password','')
        if old or new or conf:
            if not check_password_hash(user['password_hash'], old):
                flash('Password lama salah.', 'danger')
            elif len(new) < 6:
                flash('Password baru minimal 6 karakter.', 'danger')
            elif new != conf:
                flash('Konfirmasi password tidak cocok.', 'danger')
            else:
                conn = get_db(); cur = conn.cursor(); cur.execute('UPDATE users SET password_hash=?, updated_at=? WHERE id=?', (generate_password_hash(new), now(), user['id'])); conn.commit(); log_action('CHANGE_PASSWORD', user['username']); flash('Password berhasil diubah.', 'success'); return redirect(url_for('settings'))

    # Load master options
    conn = get_db(); cur = conn.cursor()
    cur.execute('SELECT category, options_text FROM master_options')
    master_rows = cur.fetchall()
    master_opts = {r['category']: r['options_text'] for r in master_rows}

    body = '''
    <div class="g2 grid">
      <div class="card no-print">
        <h3>Ubah Profil</h3>
        <form method="post" class="grid mb-6">
          <input type="hidden" name="action" value="update_profile">
          <div><label>Nama Lengkap</label><input class="input" name="full_name" value="{{ user['full_name'] or '' }}" required placeholder="Contoh: dr. Arissa, Sp.OG"></div>
          <button class="btn btn-primary">👤 Simpan Nama</button>
        </form>
        <hr style="margin:24px 0; border:0; border-top:1px solid var(--border)">
        <h3>Ubah Password</h3>
        <form method="post" class="grid">
          <div><label>Password Lama</label><input class="input" type="password" name="old_password"></div>
          <div><label>Password Baru</label><input class="input" type="password" name="new_password"></div>
          <div><label>Konfirmasi Password Baru</label><input class="input" type="password" name="confirm_password"></div>
          <button class="btn bg-slate-700 hover:bg-slate-600 text-white">🔒 Simpan Password</button>
        </form>
      </div>
      <div class="card">
        <h3>Info Akun</h3>
        <div class="space-y-2">
          <div><span class="text-slate-400">Username:</span> <span class="font-mono text-emerald-400">{{ user['username'] }}</span></div>
          <div><span class="text-slate-400">Nama Saat Ini:</span> <span class="font-bold">{{ user['full_name'] or '-' }}</span></div>
          <div><span class="text-slate-400">Role:</span> <span class="badge">{{ user['role'] }}</span></div>
          <div><span class="text-slate-400">Dibuat:</span> {{ fmt_dt(user['created_at']) }}</div>
        </div>
        <div class="small muted mt-4 p-3 bg-slate-800/30 rounded-lg">
          Tips: Dokter disarankan menggunakan nama lengkap beserta gelar untuk tampilan pada rekam medis (SOAP) dan link hasil pasien agar lebih profesional.
        </div>
      </div>
    </div>

    {% if user['role'] in ['superadmin','admin'] %}
    <div class="card" style="margin-top:16px">
      <h3 style="margin:0 0 6px">⚙️ Kelola Opsi Dropdown Form Pasien</h3>
      <div class="small muted" style="margin-bottom:16px">Pisahkan setiap opsi dengan koma. Contoh: <code>Umum,BPJS,Asuransi</code>. Perubahan langsung berlaku pada form Input Pasien.</div>
      <form method="post" class="grid">
        <input type="hidden" name="action" value="save_master_opts">
        <div class="form3">
          <div>
            <label>🩸 Golongan Darah</label>
            <input class="input" name="golongan_darah" value="{{ master_opts.get('golongan_darah','A,B,AB,O') }}" placeholder="A,B,AB,O">
            <div class="small muted" style="margin-top:4px">Nilai saat ini: {{ master_opts.get('golongan_darah','A,B,AB,O') }}</div>
          </div>
          <div>
            <label>🏥 Jenis Layanan</label>
            <input class="input" name="jenis_layanan" value="{{ master_opts.get('jenis_layanan','Umum,BPJS,Asuransi') }}" placeholder="Umum,BPJS,Asuransi">
            <div class="small muted" style="margin-top:4px">Nilai saat ini: {{ master_opts.get('jenis_layanan','') }}</div>
          </div>
          <div>
            <label>💼 Pekerjaan</label>
            <input class="input" name="pekerjaan" value="{{ master_opts.get('pekerjaan','') }}" placeholder="Karyawan,Wiraswasta,PNS,...">
            <div class="small muted" style="margin-top:4px">Nilai saat ini: {{ master_opts.get('pekerjaan','') }}</div>
          </div>
        </div>
        <div class="toolbar" style="margin-top:8px">
          <button class="btn btn-primary">💾 Simpan Opsi Dropdown</button>
        </div>
      </form>
    </div>
    {% endif %}
    '''
    return render_page('Settings', body, fmt_dt=fmt_dt, master_opts=master_opts)


@app.route('/backup-db')
@role_required('superadmin', 'admin')
def backup_db():
    name = 'backup_usg4d_{}.db'.format(datetime.now().strftime('%Y%m%d_%H%M%S'))
    path = os.path.join(BASE_DIR, name)
    shutil.copyfile(DB_PATH, path)
    log_action('BACKUP_DB', name)
    return send_file(path, as_attachment=True, download_name=name, mimetype='application/octet-stream')


@app.errorhandler(404)
def e404(e):
    body = '''<div class="authbox loginbox"><div class="card center"><h2>404 - Halaman tidak ditemukan</h2><div class="muted">Periksa URL atau kembali ke dashboard/login.</div><div class="toolbar" style="justify-content:center;margin-top:14px"><a class="btn btn-primary" href="{{ url_for('index') }}">🏠 Kembali</a></div></div></div>'''
    return render_page('404', body), 404


@app.errorhandler(403)
def e403(e):
    body = '''<div class="authbox loginbox"><div class="card center"><h2>403 - Akses ditolak</h2><div class="muted">Anda tidak memiliki izin membuka halaman ini.</div><div class="toolbar" style="justify-content:center;margin-top:14px"><a class="btn btn-primary" href="{{ url_for('dashboard') if current_user else url_for('login') }}">⬅️ Kembali</a></div></div></div>'''
    return render_page('403', body), 403


@app.errorhandler(413)
def e413(e):
    body = '''<div class="authbox loginbox"><div class="card center"><h2>File terlalu besar</h2><div class="muted">Ukuran maksimal upload adalah {{ max_mb }} MB.</div><div class="toolbar" style="justify-content:center;margin-top:14px"><a class="btn btn-primary" href="{{ request.referrer or url_for('dashboard') }}">⬅️ Kembali</a></div></div></div>'''
    return render_page('Upload Terlalu Besar', body, max_mb=MAX_MB), 413


if __name__ == '__main__':
    init_db()  # [FIX] Inisialisasi database sebelum server jalan
    port = get_port()
    print('=' * 66)
    print(APP_NAME + ' siap dijalankan')
    print('Database : ' + DB_PATH)
    print('Uploads  : ' + UPLOAD_DIR)
    print('User default:')
    print('  superadmin / admin123')
    print('  admin      / admin123')
    print('  dokter     / dokter123')
    print('Buka di browser: http://127.0.0.1:{}'.format(port))
    print('=' * 66)
    try:
        app.run(debug=False, use_reloader=True, host='0.0.0.0', port=port)
    except OSError as exc:
        print('\nGAGAL menjalankan server di port {}.'.format(port))
        print('Detail error: {}'.format(exc))
        print('Solusi: tutup aplikasi lain yang memakai port ini, jalankan BAT sebagai Administrator, atau set KLINIK_PORT ke port lain.')
        raise
