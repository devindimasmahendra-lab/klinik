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
from datetime import datetime, date
from functools import wraps
from typing import Optional

from flask import Flask, request, redirect, url_for, render_template_string, session, flash, abort, send_from_directory, send_file
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
DB_PATH = os.path.join(BASE_DIR, 'usg4d_klinik.db')
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
ALLOWED = {'jpg', 'jpeg', 'png', 'pdf', 'mp4', 'mov'}
MAX_MB = 32
DEFAULT_PORT = 5006

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-secret')
app.config['UPLOAD_FOLDER'] = UPLOAD_DIR
app.config['MAX_CONTENT_LENGTH'] = MAX_MB * 1024 * 1024
os.makedirs(UPLOAD_DIR, exist_ok=True)



def hitung_risiko_kehamilan(td_sistolik, td_diastolik, djj):
    try:
        sys = int(str(td_sistolik).strip()) if td_sistolik else 120
        dia = int(str(td_diastolik).strip()) if td_diastolik else 80
        # Try to parse string with digits like "145 bpm"
        d = int(''.join(filter(str.isdigit, str(djj)))) if djj else 140
    except ValueError:
        return {'status': 'Hijau', 'label': 'Risiko Rendah (Data Tidak Lengkap)', 'color': '#22c55e', 'bg': 'rgba(34,197,94,0.15)'}

    if sys >= 160 or dia >= 110 or d < 100 or d > 170:
        return {'status': 'Merah', 'label': 'Risiko Tinggi (Peringatan Dini)', 'color': '#ef4444', 'bg': 'rgba(239,68,68,0.15)'}
    elif sys >= 140 or dia >= 90 or d < 110 or d > 160:
        return {'status': 'Kuning', 'label': 'Risiko Sedang (Pantau Lanjut)', 'color': '#f59e0b', 'bg': 'rgba(245,158,11,0.15)'}
    else:
        return {'status': 'Hijau', 'label': 'Risiko Rendah (Normal)', 'color': '#22c55e', 'bg': 'rgba(34,197,94,0.15)'}

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


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
    return 'RM' + datetime.now().strftime('%y%m%d%H%M%S')


def get_port():
    try:
        return int(os.environ.get('KLINIK_PORT', DEFAULT_PORT))
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
    conn = db()
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
    conn.commit()
    conn.close()


def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    conn = db(); cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE id=? AND active=1', (uid,))
    row = cur.fetchone()
    conn.close()
    return row


def log_action(action, details=''):
    user = current_user()
    uid = user['id'] if user else None
    uname = user['username'] if user else 'guest'
    conn = db(); cur = conn.cursor()
    cur.execute('INSERT INTO audit_logs (user_id,username,action,details,ip_address,created_at) VALUES (?,?,?,?,?,?)',
                (uid, uname, action, details[:2000], request.headers.get('X-Forwarded-For', request.remote_addr or '-'), now()))
    conn.commit(); conn.close()


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
    conn = db(); cur = conn.cursor()
    cur.execute('SELECT * FROM patients WHERE id=?', (pid,))
    row = cur.fetchone(); conn.close()
    return row


@app.route('/api/patient_search')
@role_required('superadmin', 'admin')
def api_patient_search():
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return {'results': []}
    conn = db(); cur = conn.cursor()
    like = '%' + q + '%'
    cur.execute("SELECT id,nama_pasien,nomor_rekam_medis,nik,tanggal_lahir,umur,alamat,nomor_hp,golongan_darah,status_perkawinan,pekerjaan,nama_keluarga,jenis_layanan,dokter_tujuan,created_at FROM patients WHERE nama_pasien LIKE ? OR nomor_rekam_medis LIKE ? OR nik LIKE ? OR nomor_hp LIKE ? ORDER BY nama_pasien LIMIT 20", (like, like, like, like))
    rows = [dict(r) for r in cur.fetchall()]; conn.close()
    return {'results': rows}


@app.route('/api/patient_by_id/<int:patient_id>')
@role_required('superadmin', 'admin')
def api_patient_by_id(patient_id):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT id,nama_pasien,nomor_rekam_medis,nik,tanggal_lahir,umur,alamat,nomor_hp,golongan_darah,status_perkawinan,pekerjaan,nama_keluarga,jenis_layanan,dokter_tujuan,created_at FROM patients WHERE id=?", (patient_id,))
    row = cur.fetchone(); conn.close()
    if not row:
        return {'result': None}
    return {'result': dict(row)}



@app.route('/api/fetal_growth/<int:patient_id>')
@role_required('superadmin', 'admin', 'dokter')
def api_fetal_growth(patient_id):
    conn = db(); cur = conn.cursor()
    cur.execute('''
        SELECT created_at, detak_jantung_janin, estimasi_berat_janin, usia_kehamilan 
        FROM soap_records 
        WHERE patient_id=? 
        ORDER BY created_at ASC
    ''', (patient_id,))
    rows = cur.fetchall()
    conn.close()
    
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


@app.route('/api/patient_visits/<int:patient_id>')
@role_required('superadmin', 'admin')
def api_patient_visits(patient_id):
    conn = db(); cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM soap_records WHERE patient_id=?', (patient_id,))
    count = cur.fetchone()[0]
    conn.close()
    return {'count': count}




def render_page(title, body_tpl, **ctx):
    user = current_user()
    page_ctx = dict(ctx)
    page_ctx['user'] = user
    page_ctx['current_user'] = user
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
    conn = db()
    rows = conn.execute("""
        SELECT a.*, p.nama_pasien, p.nomor_rekam_medis
        FROM appointments a
        JOIN patients p ON p.id = a.patient_id
        ORDER BY a.appointment_date DESC
    """).fetchall()
    conn.close()

    body = """
    <div class="card">
      <div class="toolbar">
        <h2>📅 Jadwal Appointment</h2>
        <a class="btn btn-primary" href="{{ url_for('add_appointment') }}">+ Tambah Appointment</a>
      </div>

      <table class="table">
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
      </table>
      </div>
    </div>
    """
    return render_page('Appointments', body, rows=rows, fmt_dt=fmt_dt)


@app.route('/appointments/add', methods=['GET', 'POST'])
@role_required('superadmin', 'admin', 'dokter')
def add_appointment():
    conn = db()

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
        conn.close()

        flash('Appointment berhasil ditambahkan.', 'success')
        return redirect(url_for('appointments'))

    patients = conn.execute("""
        SELECT id, nama_pasien, nomor_rekam_medis
        FROM patients
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()

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
    conn = db()
    rows = conn.execute("""
        SELECT nama_pasien, nomor_rekam_medis, nomor_hp, alamat, created_at
        FROM patients
        ORDER BY created_at DESC
    """).fetchall()
    conn.close()

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
    conn = db()

    total_patients = conn.execute(
        "SELECT COUNT(*) FROM patients"
    ).fetchone()[0]

    total_soap = conn.execute(
        "SELECT COUNT(*) FROM soap_records"
    ).fetchone()[0]

    total_appointments = conn.execute(
        "SELECT COUNT(*) FROM appointments"
    ).fetchone()[0]

    conn.close()

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
        conn = db(); cur = conn.cursor()
        cur.execute('SELECT * FROM users WHERE username=? AND active=1', (username,))
        user = cur.fetchone(); conn.close()
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
    conn = db(); cur = conn.cursor()
    if user['role'] == 'pasien' and user['patient_id']:
        cur.execute('SELECT * FROM patients WHERE id=?', (user['patient_id'],)); patient = cur.fetchone()
        cur.execute('SELECT COUNT(*) FROM uploads WHERE patient_id=?', (user['patient_id'],)); total_upload = cur.fetchone()[0]
        cur.execute('SELECT * FROM soap_records WHERE patient_id=? ORDER BY created_at DESC LIMIT 5', (user['patient_id'],)); soaps = cur.fetchall(); conn.close()
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
    if user['role'] == 'dokter':
        cur.execute("SELECT * FROM patients WHERE status_antrian IN ('menunggu','diperiksa') ORDER BY created_at ASC LIMIT 10")
    else:
        cur.execute('SELECT * FROM patients ORDER BY created_at DESC LIMIT 8')
    patients_rows = cur.fetchall()
    cur.execute('SELECT * FROM audit_logs ORDER BY id DESC LIMIT 8'); audits = cur.fetchall(); conn.close()
    body = '''
    <div class="hero card"><div><h3 style="margin:0">Selamat datang, {{ user['full_name'] or user['username'] }}</h3><div class="muted">Dashboard ringkas fokus admin dan dokter.</div></div><div class="toolbar no-print">{% if user['role'] in ['superadmin','admin'] %}<a class="btn btn-primary" href="{{ url_for('patient_new') }}">➕ Input Pasien Baru</a>{% endif %}<a class="btn" href="{{ url_for('patients') }}">📋 Lihat Pasien</a></div></div>
    <div class="g4 grid" style="margin-top:16px"><div class="stat"><div class="muted small">Total pasien hari ini</div><div style="font-size:30px;font-weight:800">{{ total_today }}</div></div><div class="stat"><div class="muted small">Pasien menunggu</div><div style="font-size:30px;font-weight:800">{{ waiting }}</div></div><div class="stat"><div class="muted small">Sedang diperiksa</div><div style="font-size:30px;font-weight:800">{{ checked }}</div></div><div class="stat"><div class="muted small">Pasien selesai</div><div style="font-size:30px;font-weight:800">{{ finished }}</div></div></div>
    <div class="g2 grid" style="margin-top:16px">
      <div class="card"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><h3 style="margin:0">Pasien Terbaru / Antrian</h3><span class="badge">Upload: {{ total_uploads }}</span></div>{% if patients_rows %}<table><thead><tr><th>Pasien</th><th>RM</th><th>Status</th><th>Dokter</th><th>Aksi</th></tr></thead><tbody>{% for p in patients_rows %}<tr><td><strong>{{ p['nama_pasien'] }}</strong><div class="small muted">{{ fmt_dt(p['created_at']) }}</div></td><td>{{ p['nomor_rekam_medis'] }}</td><td><span class="badge {{ p['status_antrian'] }}">{{ p['status_antrian'] }}</span></td><td>{{ p['dokter_tujuan'] or '-' }}</td><td><a class="btn btn-sm" href="{{ url_for('patient_detail', patient_id=p['id']) }}">Buka</a></td></tr>{% endfor %}</tbody></table>{% else %}<div class="empty">Belum ada pasien.</div>{% endif %}</div>
      <div class="card"><h3>Audit Terbaru</h3>{% if audits %}<table><thead><tr><th>Waktu</th><th>User</th><th>Aksi</th></tr></thead><tbody>{% for a in audits %}<tr><td>{{ fmt_dt(a['created_at']) }}</td><td>{{ a['username'] or '-' }}</td><td><strong>{{ a['action'] }}</strong><div class="small muted">{{ a['details'] or '' }}</div></td></tr>{% endfor %}</tbody></table>{% else %}<div class="empty">Belum ada audit.</div>{% endif %}</div>
    </div>
    '''
    return render_page('Dashboard', body, total_today=total_today, waiting=waiting, checked=checked, finished=finished, total_uploads=total_uploads, patients_rows=patients_rows, audits=audits, fmt_dt=fmt_dt)


@app.route('/antrian')
@role_required('superadmin', 'admin', 'dokter')
def antrian():
    conn = db(); cur = conn.cursor()
    # Hanya tampilkan antrian aktif (menunggu/diperiksa) urut dari yang paling lama (FIFO)
    cur.execute("SELECT * FROM patients WHERE status_antrian IN ('menunggu','diperiksa') ORDER BY created_at ASC")
    rows = cur.fetchall(); conn.close()
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


@app.route('/patients')
@role_required('superadmin', 'admin', 'dokter')
def patients():
    q = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    doctor = request.args.get('doctor', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 20
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT full_name,username FROM users WHERE role='dokter' AND active=1 ORDER BY full_name,username"); doctors = cur.fetchall()
    where = 'WHERE 1=1'; params = []
    if q:
        like = '%' + q + '%'
        where += ' AND (nama_pasien LIKE ? OR nomor_rekam_medis LIKE ? OR nik LIKE ? OR nomor_hp LIKE ?)'
        params += [like, like, like, like]
    if status:
        where += ' AND status_antrian=?'; params.append(status)
    if doctor:
        where += ' AND dokter_tujuan=?'; params.append(doctor)
    order = " ORDER BY CASE status_antrian WHEN 'menunggu' THEN 1 WHEN 'diperiksa' THEN 2 ELSE 3 END, created_at DESC"
    cur.execute('SELECT COUNT(*) FROM patients ' + where, tuple(params))
    total = cur.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    sql = 'SELECT * FROM patients ' + where + order + ' LIMIT ? OFFSET ?'
    cur.execute(sql, tuple(params) + (per_page, offset)); rows = cur.fetchall(); conn.close()
    body = '''
    <div class="card no-print"><form class="searchbox"><input class="input" name="q" value="{{ q }}" placeholder="Cari nama / RM / NIK / HP..."><select class="select" name="status"><option value="">Semua status</option>{% for s in ['menunggu','diperiksa','selesai'] %}<option value="{{ s }}" {{ 'selected' if status==s else '' }}>{{ s }}</option>{% endfor %}</select><select class="select" name="doctor"><option value="">Semua dokter</option>{% for d in doctors %}{% set dn = d['full_name'] or d['username'] %}<option value="{{ dn }}" {{ 'selected' if doctor==dn else '' }}>{{ dn }}</option>{% endfor %}</select><button class="btn btn-primary">🔍 Filter</button>{% if current_user['role'] in ['superadmin','admin'] %}<a class="btn" href="{{ url_for('patient_new') }}">➕ Pasien Baru</a>{% endif %}</form></div>
    <div class="card" style="margin-top:16px"><div style="display:flex;justify-content:space-between;align-items:center;gap:10px"><h3 style="margin:0">Daftar Pasien</h3><span class="badge">{{ total }} data • Hal {{ page }}/{{ total_pages }}</span></div>{% if rows %}<table><thead><tr><th>Pasien</th><th>RM</th><th>Layanan</th><th>Dokter</th><th>Status</th><th>Aksi</th></tr></thead><tbody>{% for p in rows %}<tr><td><strong>{{ p['nama_pasien'] }}</strong><div class="small muted">NIK: {{ p['nik'] or '-' }} • HP: {{ p['nomor_hp'] or '-' }}</div></td><td>{{ p['nomor_rekam_medis'] }}</td><td>{{ p['jenis_layanan'] or '-' }}</td><td>{{ p['dokter_tujuan'] or '-' }}</td><td><span class="badge {{ p['status_antrian'] }}">{{ p['status_antrian'] }}</span></td><td><div class="toolbar"><a class="btn btn-sm" href="{{ url_for('patient_detail', patient_id=p['id']) }}">Buka</a><a class="btn btn-sm" href="{{ url_for('patient_history', patient_id=p['id']) }}">History</a><form method="post" action="{{ url_for('patient_detail', patient_id=p['id']) }}" style="display:inline" onsubmit="return confirm('Antrikan pasien ini?')"><input type="hidden" name="action" value="add_to_queue"><button class="btn btn-sm btn-primary">➕ Antrikan</button></form></div></td></tr>{% endfor %}</tbody></table>
    <div class="toolbar" style="justify-content:center;margin-top:14px">{% if page > 1 %}<a class="btn btn-sm" href="?page={{ page-1 }}&q={{ q }}&status={{ status }}&doctor={{ doctor }}">⬅ Sebelumnya</a>{% endif %}<span class="badge">Halaman {{ page }} dari {{ total_pages }}</span>{% if page < total_pages %}<a class="btn btn-sm" href="?page={{ page+1 }}&q={{ q }}&status={{ status }}&doctor={{ doctor }}">Berikutnya ➡</a>{% endif %}</div>
    {% else %}<div class="empty">Tidak ada data pasien.</div>{% endif %}</div>
    '''
    return render_page('Data Pasien', body, q=q, status=status, doctor=doctor, doctors=doctors, rows=rows, total=total, page=page, total_pages=total_pages)


@app.route('/patients/new', methods=['GET', 'POST'])
@role_required('superadmin', 'admin')
def patient_new():
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT full_name,username FROM users WHERE role='dokter' AND active=1 ORDER BY full_name,username"); doctors = cur.fetchall()
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
                cur.execute('''UPDATE patients SET nama_pasien=?,nomor_rekam_medis=?,nik=?,tanggal_lahir=?,umur=?,alamat=?,nomor_hp=?,golongan_darah=?,status_perkawinan=?,pekerjaan=?,nama_keluarga=?,jenis_layanan=?,dokter_tujuan=?,prioritas=?,status_antrian=?,updated_at=?
                               WHERE id=?''',
                            (nama, rm, f.get('nik','').strip(), f.get('tanggal_lahir','').strip(), f.get('umur','').strip(), f.get('alamat','').strip(), f.get('nomor_hp','').strip(), f.get('golongan_darah','').strip(), f.get('status_perkawinan','').strip(), f.get('pekerjaan','').strip(), f.get('nama_keluarga','').strip(), f.get('jenis_layanan','').strip(), f.get('dokter_tujuan','').strip(), f.get('prioritas','Non-urgent').strip(), f.get('status_antrian','menunggu').strip(), now(), int(edit_pid)))
                conn.commit()
                log_action('UPDATE_PATIENT', 'Update pasien #{} {}'.format(edit_pid, nama))
                flash('Data pasien berhasil diperbarui.', 'success')
                return redirect(url_for('patient_detail', patient_id=int(edit_pid)))
            except sqlite3.IntegrityError:
                flash('Nomor rekam medis sudah digunakan.', 'danger')
        else:
            try:
                cur.execute('''INSERT INTO patients (nama_pasien,nomor_rekam_medis,nik,tanggal_lahir,umur,alamat,nomor_hp,golongan_darah,status_perkawinan,pekerjaan,nama_keluarga,jenis_layanan,dokter_tujuan,prioritas,status_antrian,access_token,created_by,created_at,updated_at)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                            (nama, rm, f.get('nik','').strip(), f.get('tanggal_lahir','').strip(), f.get('umur','').strip(), f.get('alamat','').strip(), f.get('nomor_hp','').strip(), f.get('golongan_darah','').strip(), f.get('status_perkawinan','').strip(), f.get('pekerjaan','').strip(), f.get('nama_keluarga','').strip(), f.get('jenis_layanan','').strip(), f.get('dokter_tujuan','').strip(), f.get('prioritas','Non-urgent').strip(), f.get('status_antrian','menunggu').strip(), token_auto(), current_user()['id'], now(), now()))
                conn.commit(); pid = cur.lastrowid
                log_action('CREATE_PATIENT', 'Tambah pasien #{} {}'.format(pid, nama))
                flash('Pasien baru berhasil ditambahkan.', 'success')
                return redirect(url_for('patient_detail', patient_id=pid))
            except sqlite3.IntegrityError:
                flash('Nomor rekam medis sudah digunakan.', 'danger')
    conn.close()
    body = '''
    <div class="card no-print" style="margin-bottom:16px">
      <h3>Cari Pasien Lama</h3>
      <div class="small muted">Ketik nama / RM / NIK untuk mencari pasien yang sudah terdaftar, lalu pilih untuk mengedit datanya.</div>
      <div style="position:relative;margin-top:10px">
        <input class="input" id="searchExisting" placeholder="Ketik minimal 2 huruf..." style="width:100%">
        <div id="searchResults" style="position:absolute;top:100%;left:0;right:0;max-height:300px;overflow-y:auto;background:var(--card2);border:1px solid var(--border);border-radius:16px;z-index:100;display:none"></div>
      </div>
      <div id="selectedPatient" style="display:none;margin-top:12px;padding:14px;border-radius:16px;border:1px solid var(--pri);background:rgba(34,197,94,.1)"></div>
    </div>
    <div class="card">
      <h3>{{ 'Edit Pasien' if edit_patient else 'Form Input Pasien Baru' }}</h3>
      <form method="post" class="grid">
        <input type="hidden" name="edit_id" id="edit_id" value="{{ edit_patient['id'] if edit_patient else '' }}">
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
          <div><label>Golongan Darah</label><input class="input" name="golongan_darah" id="fgoldar" value="{{ edit_patient['golongan_darah'] if edit_patient else '' }}"></div>
          <div><label>Status</label><input class="input" name="status_perkawinan" id="fstatus" value="{{ edit_patient['status_perkawinan'] if edit_patient else '' }}" placeholder="mis. Menikah"></div>
          <div><label>Pekerjaan</label><input class="input" name="pekerjaan" id="fpekerjaan" value="{{ edit_patient['pekerjaan'] if edit_patient else '' }}"></div>
          <div><label>Nama Suami/Keluarga</label><input class="input" name="nama_keluarga" id="fkeluarga" value="{{ edit_patient['nama_keluarga'] if edit_patient else '' }}"></div>
          <div><label>Jenis Layanan</label><input class="input" name="jenis_layanan" id="flayanan" value="{{ edit_patient['jenis_layanan'] if edit_patient else '' }}" placeholder="mis. USG 4D"></div>
          <div><label>Dokter Tujuan</label><select class="select" name="dokter_tujuan" id="fdokter"><option value="">- Pilih Dokter -</option>{% for d in doctors %}<option value="{{ d['full_name'] or d['username'] }}" {{ 'selected' if edit_patient and edit_patient['dokter_tujuan']==(d['full_name'] or d['username']) else '' }}>{{ d['full_name'] or d['username'] }}</option>{% endfor %}</select></div>
          <div><label>Status Antrian</label><select class="select" name="status_antrian" id="fstatusq"><option value="menunggu">menunggu</option><option value="diperiksa">diperiksa</option><option value="selesai">selesai</option></select></div>
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
      var inp=document.getElementById('searchExisting');
      var res=document.getElementById('searchResults');
      var sel=document.getElementById('selectedPatient');
      var timer=null;
      inp.addEventListener('input',function(){
        clearTimeout(timer);
        var v=inp.value.trim();
        if(v.length<2){res.style.display='none';return;}
        timer=setTimeout(function(){
          fetch('/api/patient_search?q='+encodeURIComponent(v)).then(function(r){return r.json()}).then(function(data){
            if(!data.results||data.results.length===0){
              res.innerHTML='<div style="padding:14px;color:var(--muted)">Tidak ditemukan</div>';res.style.display='block';return;
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
              '<button class="btn btn-sm" onclick="batalPilih()">✕ Batal</button></div>'+
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
          document.getElementById('fgoldar').value=p.golongan_darah||'';
          document.getElementById('fstatus').value=p.status_perkawinan||'';
          document.getElementById('fpekerjaan').value=p.pekerjaan||'';
          document.getElementById('fkeluarga').value=p.nama_keluarga||'';
          document.getElementById('flayanan').value=p.jenis_layanan||'';
          document.getElementById('fdokter').value=p.dokter_tujuan||'';
          document.getElementById('falamat').value=p.alamat||'';
        });
      };
      window.batalPilih=function(){
        inp.value='';sel.style.display='none';sel.innerHTML='';
        document.getElementById('edit_id').value='';
        document.getElementById('fnama').value='';document.getElementById('frm').value='';
        document.getElementById('fnik').value='';document.getElementById('ftgl').value='';
        document.getElementById('fumur').value='';document.getElementById('fhp').value='';
        document.getElementById('fgoldar').value='';document.getElementById('fstatus').value='';
        document.getElementById('fpekerjaan').value='';document.getElementById('fkeluarga').value='';
        document.getElementById('flayanan').value='';document.getElementById('fdokter').value='';
        document.getElementById('falamat').value='';
      };
    });
    </script>
    '''
    return render_page('Input Pasien Baru', body, doctors=doctors, edit_patient=edit_patient)


@app.route('/patients/<int:patient_id>', methods=['GET', 'POST'])
@role_required('superadmin', 'admin', 'dokter', 'pasien')
def patient_detail(patient_id):
    patient = get_patient(patient_id)
    if not patient: abort(404)
    if not patient_allowed(patient): abort(403)
    user = current_user()
    conn = db(); cur = conn.cursor()
    if request.method == 'POST':
        action = request.form.get('action', '')
        if action == 'update_status' and user['role'] in ('superadmin','admin','dokter'):
            st = request.form.get('status_antrian', '').strip()
            if st in ('menunggu','diperiksa','selesai'):
                cur.execute('UPDATE patients SET status_antrian=?, updated_at=? WHERE id=?', (st, now(), patient_id)); conn.commit()
                log_action('UPDATE_QUEUE_STATUS', 'Patient #{} -> {}'.format(patient_id, st)); flash('Status antrian diperbarui.', 'success'); return redirect(url_for('patient_detail', patient_id=patient_id))
        if action == 'add_to_queue' and user['role'] in ('superadmin', 'admin', 'dokter'):
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
            cur.execute('''INSERT INTO soap_records (patient_id,doctor_id,subjective,objective,assessment,plan,kode_icd10,td_sistolik,td_diastolik,nadi,suhu,rr,informed_consent,usia_kehamilan,detak_jantung_janin,posisi_janin,estimasi_berat_janin,catatan_dokter,rekomendasi_kontrol_ulang,created_at,updated_at)
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
    cur.execute('SELECT * FROM soap_templates ORDER BY id DESC'); templates = cur.fetchall(); conn.close()
    public_url = request.url_root.rstrip('/') + url_for('patient_result', token=patient['access_token'])
    qr_uri = qr_data_uri(public_url)
    body = '''
    <div class="g2 grid"><div class="card"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><div><h3 style="margin:0">{{ patient['nama_pasien'] }}</h3><div class="small muted">No RM: {{ patient['nomor_rekam_medis'] }} • Dibuat: {{ fmt_dt(patient['created_at']) }}</div></div><span class="badge {{ patient['status_antrian'] }}">{{ patient['status_antrian'] }}</span></div><div class="pill-list" style="margin:12px 0"><span class="pill">NIK: {{ patient['nik'] or '-' }}</span><span class="pill">Umur: {{ patient['umur'] or '-' }}</span><span class="pill">Gol. darah: {{ patient['golongan_darah'] or '-' }}</span><span class="pill">Layanan: {{ patient['jenis_layanan'] or '-' }}</span></div><div class="grid"><div><strong>Alamat:</strong><div class="muted">{{ patient['alamat'] or '-' }}</div></div><div><strong>HP:</strong> {{ patient['nomor_hp'] or '-' }}</div><div><strong>Status:</strong> {{ patient['status_perkawinan'] or '-' }}</div><div><strong>Pekerjaan:</strong> {{ patient['pekerjaan'] or '-' }}</div><div><strong>Suami/Keluarga:</strong> {{ patient['nama_keluarga'] or '-' }}</div><div><strong>Dokter Tujuan:</strong> {{ patient['dokter_tujuan'] or '-' }}</div></div></div>
    <div class="card"><h3>Akses Hasil Pasien</h3><div class="small muted">Bagikan link token unik ini ke pasien.</div><div class="mono wrap" style="margin:10px 0 14px">{{ public_url }}</div><div class="toolbar no-print"><a class="btn btn-primary" href="{{ url_for('patient_result', token=patient['access_token']) }}" target="_blank">🔗 Buka Link</a><button class="btn" onclick="navigator.clipboard.writeText({{ public_url|tojson }});alert('Link disalin');">📋 Copy Link</button></div>{% if qr_uri %}<div style="margin-top:14px"><img src="{{ qr_uri }}" style="max-width:180px;border-radius:18px;background:#fff;padding:10px"></div>{% else %}<div class="small muted" style="margin-top:14px">QR code aktif jika modul qrcode tersedia. Link tetap bisa dipakai.</div>{% endif %}</div></div>
    <div class="toolbar no-print" style="margin-top:16px">
        <form method="post" onsubmit="return confirm('Antrikan pasien ini?')"><input type="hidden" name="action" value="add_to_queue"><button class="btn btn-primary">➕ Masukkan ke Antrian Hari Ini</button></form>
        <a class="btn" href="{{ url_for('patient_new', edit=patient['id']) }}">✏️ Edit Data Pasien</a>
    </div>
    {% if soaps %}
    <div class="card" style="margin-top:16px">
      <h3 class="flex items-center gap-2"><span class="bg-emerald-500 w-2 h-6 rounded-full inline-block"></span> Ringkasan Perkembangan Janin</h3>
      <div class="small muted mb-3">Data historis dari kunjungan sebelumnya untuk memantau tren pertumbuhan.</div>
      <table class="w-full text-sm">
          <thead class="bg-slate-800/50">
            <tr><th class="p-3">Tanggal</th><th class="p-3">Usia Hamil</th><th class="p-3">DJJ (bpm)</th><th class="p-3">Posisi</th><th class="p-3">Berat (gr)</th></tr>
          </thead>
          <tbody class="divide-y divide-slate-800">
            {% for s in soaps|reverse %}
            <tr class="hover:bg-slate-800/20"><td>{{ fmt_dt(s['created_at']).split(' ')[0] }}</td><td class="font-bold text-white">{{ s['usia_kehamilan'] or '-' }}</td><td>{{ s['detak_jantung_janin'] or '-' }}</td><td>{{ s['posisi_janin'] or '-' }}</td><td class="font-bold text-emerald-400">{{ s['estimasi_berat_janin'] or '-' }}</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
    {% endif %}
    <div class="g2 grid" style="margin-top:16px"><div class="card no-print"><h3>Update Status Antrian</h3><form method="post" class="toolbar"><input type="hidden" name="action" value="update_status"><select class="select" name="status_antrian" style="max-width:220px">{% for s in ['menunggu','diperiksa','selesai'] %}<option value="{{ s }}" {{ 'selected' if patient['status_antrian']==s else '' }}>{{ s }}</option>{% endfor %}</select><button class="btn btn-primary">💾 Simpan Status</button><a class="btn" href="{{ url_for('patient_history', patient_id=patient['id']) }}">🕘 Riwayat</a></form></div><div class="card no-print"><h3>Upload Hasil USG</h3><div class="small muted">jpg/png/pdf/mp4/mov • max {{ max_mb }} MB</div><form method="post" enctype="multipart/form-data" class="toolbar" style="margin-top:12px"><input type="hidden" name="action" value="upload_file"><input class="input" type="file" name="usg_file" accept=".jpg,.jpeg,.png,.pdf,.mp4,.mov" required><button class="btn btn-primary">⬆️ Upload</button></form></div></div>
    <div class="card" style="margin-top:16px"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><h3 style="margin:0">SOAP Pemeriksaan</h3><div class="toolbar no-print"><select class="select" id="soapTemplate" style="max-width:280px"><option value="">Pilih template SOAP cepat...</option>{% for t in templates %}<option value='{{ {"subjective":t["subjective"],"objective":t["objective"],"assessment":t["assessment"],"plan":t["plan"]}|tojson }}'>{{ t['title'] }}</option>{% endfor %}</select><button type="button" class="btn" onclick="applySoap()">⚡ Terapkan</button></div></div><div class="small muted" style="margin:8px 0 12px">SOP internasional: SOAP + Kode ICD-10, Tanda Vital (TD/Nadi/Suhu/RR), Informed Consent.</div><form method="post" class="grid no-print"><input type="hidden" name="action" value="save_soap"><div class="form2"><div style="grid-column:1/-1"><label>Subjective (Keluhan)</label><textarea id="subjective" class="textarea" name="subjective"></textarea></div><div style="grid-column:1/-1"><label>Objective (Temuan Objektif)</label><textarea id="objective" class="textarea" name="objective"></textarea></div><div><label>TD Sistolik (mmHg)</label><input class="input" name="td_sistolik" placeholder="mis. 120"></div><div><label>TD Diastolik (mmHg)</label><input class="input" name="td_diastolik" placeholder="mis. 80"></div><div><label>Nadi (x/menit)</label><input class="input" name="nadi" placeholder="mis. 80"></div><div><label>Suhu (°C)</label><input class="input" name="suhu" placeholder="mis. 36.5"></div><div><label>RR (x/menit)</label><input class="input" name="rr" placeholder="mis. 20"></div><div style="grid-column:1/-1"><label>Assessment (Diagnosis)</label><textarea id="assessment" class="textarea" name="assessment"></textarea></div><div><label>Kode ICD-10</label><input class="input" name="kode_icd10" placeholder="mis. O99.0"></div><div><label>Informed Consent</label><label style="display:flex;align-items:center;gap:8px;margin-top:8px"><input type="checkbox" name="informed_consent"> Pasien sudah mendapatkan penjelasan dan menyetujui tindakan</label></div><div style="grid-column:1/-1"><label>Plan (Rencana Tindak Lanjut)</label><textarea id="plan" class="textarea" name="plan"></textarea></div><div><label>Usia Kehamilan</label><input class="input" name="usia_kehamilan" placeholder="mis. 28 minggu"></div><div><label>Detak Jantung Janin</label><input class="input" name="detak_jantung_janin" placeholder="mis. 145 bpm"></div><div><label>Posisi Janin</label><input class="input" name="posisi_janin" placeholder="mis. cephalic"></div><div><label>Estimasi Berat Janin</label><input class="input" name="estimasi_berat_janin" placeholder="mis. 1200 gr"></div><div style="grid-column:1/-1"><label>Catatan Dokter</label><textarea class="textarea" name="catatan_dokter"></textarea></div><div style="grid-column:1/-1"><label>Rekomendasi Kontrol Ulang</label><textarea class="textarea" name="rekomendasi_kontrol_ulang"></textarea></div></div><div class="toolbar"><button class="btn btn-primary">🩺 Simpan SOAP</button></div></form><script>function applySoap(){const el=document.getElementById('soapTemplate');if(!el.value)return;try{const d=JSON.parse(el.value);document.getElementById('subjective').value=d.subjective||'';document.getElementById('objective').value=d.objective||'';document.getElementById('assessment').value=d.assessment||'';document.getElementById('plan').value=d.plan||'';}catch(e){alert('Template gagal dipakai')}}</script></div>
    <div class="g2 grid" style="margin-top:16px"><div class="card"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><h3 style="margin:0">Riwayat SOAP</h3><span class="badge">{{ soaps|length }} catatan</span></div>{% if soaps %}{% for s in soaps %}<div class="card" style="padding:14px;margin-bottom:12px"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><div><strong>{{ s['doctor_name'] or s['doctor_username'] or '-' }}</strong><div class="small muted">{{ fmt_dt(s['created_at']) }}</div></div><span class="badge">SOAP</span></div><div class="wrap"><strong>S:</strong> {{ s['subjective'] or '-' }}</div><div class="wrap"><strong>O:</strong> {{ s['objective'] or '-' }}</div><div class="wrap"><strong>A:</strong> {{ s['assessment'] or '-' }}</div><div class="wrap"><strong>P:</strong> {{ s['plan'] or '-' }}</div><div class="pill-list" style="margin-top:10px"><span class="pill">Usia: {{ s['usia_kehamilan'] or '-' }}</span><span class="pill">DJJ: {{ s['detak_jantung_janin'] or '-' }}</span><span class="pill">Posisi: {{ s['posisi_janin'] or '-' }}</span><span class="pill">EBJ: {{ s['estimasi_berat_janin'] or '-' }}</span></div>{% if s['catatan_dokter'] %}<div class="wrap" style="margin-top:10px"><strong>Catatan:</strong> {{ s['catatan_dokter'] }}</div>{% endif %}{% if s['rekomendasi_kontrol_ulang'] %}<div class="wrap"><strong>Kontrol Ulang:</strong> {{ s['rekomendasi_kontrol_ulang'] }}</div>{% endif %}</div>{% endfor %}{% else %}<div class="empty">Belum ada riwayat SOAP.</div>{% endif %}</div>
    <div class="card"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><h3 style="margin:0">Billing Sederhana</h3><span class="badge">{{ bills|length }} item</span></div>{% if current_user['role'] in ['superadmin','admin'] %}<form method="post" class="grid no-print"><input type="hidden" name="action" value="add_billing"><div class="form3"><div><label>Item</label><input class="input" name="item_name" placeholder="mis. USG 4D"></div><div><label>Nominal</label><input class="input" type="number" step="0.01" name="amount" placeholder="0"></div><div><label>Status Bayar</label><select class="select" name="status_bayar"><option value="belum_lunas">belum_lunas</option><option value="lunas">lunas</option></select></div></div><div><label>Catatan</label><textarea class="textarea" name="notes"></textarea></div><button class="btn btn-primary">💳 Tambah Billing</button></form><hr style="border-color:var(--border);margin:16px 0">{% endif %}{% if bills %}<table><thead><tr><th>Tanggal</th><th>Item</th><th>Nominal</th><th>Status</th></tr></thead><tbody>{% set ns = namespace(total=0) %}{% for b in bills %}{% set ns.total = ns.total + (b['amount'] or 0) %}<tr><td>{{ fmt_dt(b['created_at']) }}</td><td><strong>{{ b['item_name'] }}</strong><div class="small muted">{{ b['notes'] or '' }}</div></td><td>{{ rupiah(b['amount']) }}</td><td><div class="flex items-center gap-2"><span class="badge {{ 'paid' if b['status_bayar']=='lunas' else 'unpaid' }}">{{ b['status_bayar'] }}</span>{% if b['status_bayar'] != 'lunas' and current_user['role'] in ['superadmin','admin'] %}<form method="post" style="display:inline"><input type="hidden" name="action" value="set_lunas"><input type="hidden" name="billing_id" value="{{ b['id'] }}"><button class="btn btn-sm bg-emerald-600 hover:bg-emerald-500 text-white py-1 px-2 text-[10px]">LUNAS</button></form>{% endif %}</div></td></tr>{% endfor %}</tbody><tfoot><tr><th colspan="2">Total</th><th>{{ rupiah(ns.total) }}</th><th></th></tr></tfoot></table>{% else %}<div class="empty">Belum ada billing.</div>{% endif %}</div></div>
    <div class="card" style="margin-top:16px"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><h3 style="margin:0">File Hasil USG</h3><span class="badge">{{ files|length }} file</span></div>{% if files %}<table><thead><tr><th>File</th><th>Tipe</th><th>Ukuran</th><th>Tanggal</th><th>Aksi</th></tr></thead><tbody>{% for f in files %}<tr><td><strong>{{ f['original_filename'] }}</strong></td><td>{{ file_badge(f['file_ext']) }}</td><td>{{ '%.2f MB'|format((f['file_size'] or 0)/1024/1024) }}</td><td>{{ fmt_dt(f['created_at']) }}</td><td><div class="toolbar"><a class="btn btn-sm" href="{{ url_for('file_view_auth', upload_id=f['id']) }}" target="_blank">Buka</a><a class="btn btn-sm" href="{{ url_for('patient_file_public', token=patient['access_token'], upload_id=f['id']) }}" target="_blank">Link Pasien</a></div></td></tr>{% endfor %}</tbody></table>{% else %}<div class="empty">Belum ada file hasil USG.</div>{% endif %}</div>
    '''
    return render_page('Detail Pasien - ' + patient['nama_pasien'], body, patient=patient, public_url=public_url, qr_uri=qr_uri, soaps=soaps, files=files, bills=bills, templates=templates, file_badge=file_badge, fmt_dt=fmt_dt, rupiah=rupiah, max_mb=MAX_MB)


@app.route('/patients/<int:patient_id>/history')
@role_required('superadmin', 'admin', 'dokter', 'pasien')
def patient_history(patient_id):
    patient = get_patient(patient_id)
    if not patient: abort(404)
    if not patient_allowed(patient): abort(403)
    conn = db(); cur = conn.cursor()
    cur.execute('SELECT st.*, u.full_name doctor_name, u.username doctor_username FROM soap_records st LEFT JOIN users u ON st.doctor_id=u.id WHERE st.patient_id=? ORDER BY st.created_at DESC', (patient_id,)); soaps = cur.fetchall()
    cur.execute('SELECT * FROM uploads WHERE patient_id=? ORDER BY created_at DESC', (patient_id,)); files = cur.fetchall()
    cur.execute('SELECT * FROM billing WHERE patient_id=? ORDER BY created_at DESC', (patient_id,)); bills = cur.fetchall(); conn.close()
    body = '''
    <div class="hero card mb-6">
        <div>
            <h3 class="text-2xl font-bold text-white mb-1">Riwayat Pasien: {{ patient['nama_pasien'] }}</h3>
            <div class="text-slate-400 font-medium">No RM: {{ patient['nomor_rekam_medis'] }} • Resume Kunjungan Periodik</div>
        </div>
        <div class="toolbar no-print">
            <a class="btn bg-slate-700 hover:bg-slate-600 text-white" href="{{ url_for('patient_detail', patient_id=patient['id']) }}">⬅️ Detail Pasien</a>
        </div>
    </div>

    <div class="g3 grid mb-6">
        <div class="stat"><div class="small muted">Kunjungan (SOAP)</div><div class="text-3xl font-black text-emerald-400">{{ soaps|length }}</div></div>
        <div class="stat"><div class="small muted">Dokumen Digital</div><div class="text-3xl font-black text-blue-400">{{ files|length }}</div></div>
        <div class="stat"><div class="small muted">Transaksi Terlampir</div><div class="text-3xl font-black text-amber-400">{{ bills|length }}</div></div>
    </div>

    <!-- Growth Monitoring Chart Table -->
    <div class="card mb-6 overflow-hidden">
        <h3 class="text-lg font-bold text-white mb-4 flex items-center gap-2">
            <span class="bg-emerald-500 w-2 h-6 rounded-full inline-block"></span>
            Monitoring Perkembangan Janin (USG)
        </h3>
        <table class="w-full text-sm border-collapse">
                <thead>
                    <tr class="text-slate-500 bg-slate-800/50 uppercase text-[10px] tracking-widest border-b border-slate-700">
                        <th class="py-3 px-4 text-left">Tgl Periksa</th>
                        <th class="py-3 px-4 text-left">Usia Hamil</th>
                        <th class="py-3 px-4 text-left">DJJ (bpm)</th>
                        <th class="py-3 px-4 text-left">Posisi Janin</th>
                        <th class="py-3 px-4 text-left">Berat (gr)</th>
                        <th class="py-3 px-4 text-left">Tekanan Darah</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-slate-800 text-slate-300">
                    {% if soaps %}
                        {% for s in soaps|reverse %}
                        <tr class="hover:bg-slate-800/30 transition-colors">
                            <td class="py-3 px-4 whitespace-nowrap">{{ fmt_dt(s['created_at']).split(' ')[0] }}</td>
                            <td class="py-3 px-4 font-bold text-white">{{ s['usia_kehamilan'] or '-' }}</td>
                            <td class="py-3 px-4">{{ s['detak_jantung_janin'] or '-' }}</td>
                            <td class="py-3 px-4 uppercase text-[11px]">{{ s['posisi_janin'] or '-' }}</td>
                            <td class="py-3 px-4 font-mono text-emerald-400 font-bold">{{ s['estimasi_berat_janin'] or '-' }}</td>
                            <td class="py-3 px-4 text-xs">{{ s['td_sistolik'] or '-' }}/{{ s['td_diastolik'] or '-' }}</td>
                        </tr>
                        {% endfor %}
                    {% else %}
                        <tr><td colspan="6" class="py-6 text-center text-slate-500 italic">Data rekam medis belum tersedia.</td></tr>
                    {% endif %}
                </tbody>
            </table>
        </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <!-- Timeline Content -->
        <div class="lg:col-span-2 space-y-6">
            <h3 class="text-lg font-bold text-white px-2">Timeline Kunjungan</h3>
            <div class="space-y-4">
                {% if soaps %}
                    {% for s in soaps %}
                    <div class="relative pl-8 border-l-2 border-slate-800 pb-2">
                        <!-- Timeline Indicator -->
                        <div class="absolute -left-[9px] top-0 w-4 h-4 rounded-full bg-emerald-500 border-4 border-slate-900"></div>
                        
                        <div class="card bg-slate-800/20 hover:bg-slate-800/40 transition-all p-5">
                            <div class="flex justify-between items-start mb-4 border-b border-slate-700/50 pb-3">
                                <div>
                                    <div class="text-emerald-400 font-bold">{{ fmt_dt(s['created_at']) }}</div>
                                    <div class="text-[10px] text-slate-500 uppercase tracking-tighter">Pemeriksa: {{ s['doctor_name'] or s['doctor_username'] or '-' }}</div>
                                    
{% set risk = hitung_risiko_kehamilan(s['td_sistolik'], s['td_diastolik'], s['detak_jantung_janin']) %}
<span class="risk-badge {% if risk.status == 'Merah' %}risk-merah{% endif %}" style="background: {{ risk.bg }}; color: {{ risk.color }}; border: 1px solid {{ risk.color }};">
    {% if risk.status == 'Merah' %} ⚠️ {% elif risk.status == 'Kuning' %} ⚡ {% else %} ✅ {% endif %}
    {{ risk.label }}
</span>

                                </div>
                                <div class="flex gap-2">
                                    {% if s['informed_consent'] %}<span class="badge bg-emerald-500/10 text-emerald-400 border-emerald-500/30 text-[9px]">Consent OK</span>{% endif %}
                                    <span class="badge bg-slate-700 text-slate-300 text-[10px]">{{ s['kode_icd10'] or 'ICD-10' }}</span>
                                </div>
                            </div>
                            
                            <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs leading-relaxed">
                                <div><span class="text-slate-500 font-bold block mb-1">SUBJECTIVE</span><div class="wrap text-slate-300">{{ s['subjective'] or '-' }}</div></div>
                                <div><span class="text-slate-500 font-bold block mb-1">OBJECTIVE</span><div class="wrap text-slate-300">{{ s['objective'] or '-' }}</div></div>
                                <div class="md:col-span-2 mt-2 pt-2 border-t border-slate-700/30"><span class="text-slate-500 font-bold block mb-1">ASSESSMENT & DIAGNOSIS</span><div class="wrap text-white font-semibold text-sm">{{ s['assessment'] or '-' }}</div></div>
                                <div class="md:col-span-2 mt-1"><span class="text-slate-500 font-bold block mb-1">PLAN & EDUKASI</span><div class="wrap text-emerald-100 italic bg-emerald-900/10 p-2 rounded">{{ s['plan'] or '-' }}</div></div>
                            </div>
                        </div>
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="empty py-10">Belum ada riwayat SOAP.</div>
                {% endif %}
            </div>
        </div>

        <!-- Side Panel -->
        <div class="space-y-6">
            <div class="card">
                <h4 class="text-white font-bold mb-4 flex items-center gap-2">🎞️ Galeri Hasil USG</h4>
                {% if files %}
                <div class="grid grid-cols-1 gap-3 max-h-[400px] overflow-y-auto pr-2">
                    {% for f in files %}
                    <a href="{{ url_for('file_view_auth', upload_id=f['id']) }}" target="_blank" class="flex items-center gap-3 p-3 rounded-xl bg-slate-900/50 border border-slate-700 hover:border-emerald-500 transition-all group">
                        <div class="w-10 h-10 rounded-lg bg-slate-800 flex items-center justify-center text-lg group-hover:scale-110 transition-transform">
                            {% if f['file_ext'] in ['jpg','jpeg','png'] %}🖼️{% elif f['file_ext']=='pdf' %}📄{% else %}🎞️{% endif %}
                        </div>
                        <div class="overflow-hidden">
                            <div class="text-[11px] text-slate-200 font-bold truncate">{{ f['original_filename'] }}</div>
                            <div class="text-[9px] text-slate-500">{{ fmt_dt(f['created_at']) }}</div>
                        </div>
                    </a>
                    {% endfor %}
                </div>
                {% else %}
                <div class="text-center py-6 text-slate-500 text-xs italic">Tidak ada file.</div>
                {% endif %}
            </div>

            <div class="card">
                <h4 class="text-white font-bold mb-4">💳 Riwayat Pembayaran</h4>
                {% if bills %}
                <div class="space-y-3">
                    {% for b in bills %}
                    <div class="p-3 rounded-xl bg-slate-900/50 border border-slate-700">
                        <div class="flex justify-between items-center mb-1">
                            <span class="text-[11px] text-slate-300 font-bold">{{ b['item_name'] }}</span>
                            <span class="text-[10px] font-black {{ 'text-emerald-400' if b['status_bayar']=='lunas' else 'text-red-400' }} uppercase">{{ b['status_bayar'] }}</span>
                        </div>
                        <div class="flex justify-between items-end">
                            <span class="text-[9px] text-slate-500">{{ fmt_dt(b['created_at']).split(' ')[0] }}</span>
                            <span class="text-sm font-mono text-white">{{ rupiah(b['amount']) }}</span>
                        </div>
                    </div>
                    {% endfor %}
                </div>
                {% else %}
                <div class="text-center py-6 text-slate-500 text-xs italic">Belum ada billing.</div>
                {% endif %}
            </div>
        </div>
    </div>
    '''
    return render_page('Riwayat Pemeriksaan Pasien', body, patient=patient, soaps=soaps, files=files, bills=bills, fmt_dt=fmt_dt, rupiah=rupiah, file_badge=file_badge)


@app.route('/uploads')
@role_required('superadmin', 'admin', 'dokter')
def uploads_page():
    q = request.args.get('q', '').strip()
    conn = db(); cur = conn.cursor()
    sql = '''SELECT up.*, p.nama_pasien, p.nomor_rekam_medis, u.username
             FROM uploads up JOIN patients p ON up.patient_id=p.id LEFT JOIN users u ON up.uploader_id=u.id WHERE 1=1'''
    params = []
    if q:
        like = '%' + q + '%'
        sql += ' AND (p.nama_pasien LIKE ? OR p.nomor_rekam_medis LIKE ? OR up.original_filename LIKE ?)'
        params += [like, like, like]
    sql += ' ORDER BY up.created_at DESC'
    cur.execute(sql, tuple(params)); rows = cur.fetchall(); conn.close()
    body = '''
    <div class="card no-print"><form class="searchbox"><input class="input" name="q" value="{{ q }}" placeholder="Cari pasien / RM / nama file..."><button class="btn btn-primary">🔍 Cari</button></form></div>
    <div class="card" style="margin-top:16px"><div style="display:flex;justify-content:space-between;align-items:center;gap:10px"><h3 style="margin:0">Semua Upload Hasil USG</h3><span class="badge">{{ rows|length }} file</span></div>{% if rows %}<table><thead><tr><th>Pasien</th><th>File</th><th>Tipe</th><th>Ukuran</th><th>Tanggal</th><th>Aksi</th></tr></thead><tbody>{% for r in rows %}<tr><td><strong>{{ r['nama_pasien'] }}</strong><div class="small muted">{{ r['nomor_rekam_medis'] }}</div></td><td>{{ r['original_filename'] }}<div class="small muted">Uploader: {{ r['username'] or '-' }}</div></td><td>{{ file_badge(r['file_ext']) }}</td><td>{{ '%.2f MB'|format((r['file_size'] or 0)/1024/1024) }}</td><td>{{ fmt_dt(r['created_at']) }}</td><td><a class="btn btn-sm" href="{{ url_for('file_view_auth', upload_id=r['id']) }}" target="_blank">Buka</a></td></tr>{% endfor %}</tbody></table>{% else %}<div class="empty">Belum ada upload.</div>{% endif %}</div>
    '''
    return render_page('Hasil Upload USG', body, q=q, rows=rows, file_badge=file_badge, fmt_dt=fmt_dt)


@app.route('/file/<int:upload_id>')
@role_required('superadmin', 'admin', 'dokter', 'pasien')
def file_view_auth(upload_id):
    conn = db(); cur = conn.cursor()
    cur.execute('SELECT up.*, p.id pid FROM uploads up JOIN patients p ON up.patient_id=p.id WHERE up.id=?', (upload_id,))
    row = cur.fetchone(); conn.close()
    if not row: abort(404)
    patient = get_patient(row['pid'])
    if not patient_allowed(patient): abort(403)
    return send_from_directory(UPLOAD_DIR, row['stored_filename'], as_attachment=False, download_name=row['original_filename'])


@app.route('/hasil/<token>')
def patient_result(token):
    conn = db(); cur = conn.cursor()
    cur.execute('SELECT * FROM patients WHERE access_token=?', (token,)); patient = cur.fetchone()
    if not patient:
        conn.close(); abort(404)
    cur.execute('SELECT * FROM uploads WHERE patient_id=? ORDER BY created_at DESC', (patient['id'],)); files = cur.fetchall()
    cur.execute('SELECT st.*, u.full_name doctor_name, u.username doctor_username FROM soap_records st LEFT JOIN users u ON st.doctor_id=u.id WHERE st.patient_id=? ORDER BY st.created_at DESC LIMIT 5', (patient['id'],)); soaps = cur.fetchall()
    cur.execute('SELECT SUM(amount) FROM billing WHERE patient_id=?', (patient['id'],)); total_bill = cur.fetchone()[0] or 0
    conn.close()
    body = '''
    <div class="authbox"><div class="card"><div class="hero"><div><h2 style="margin:0">Hasil USG Pasien</h2><div class="muted">Halaman aman berbasis token unik. Data pasien lain tidak dapat diakses dari halaman ini.</div><div class="pill-list" style="margin-top:10px"><span class="pill">Nama: {{ patient['nama_pasien'] }}</span><span class="pill">No RM: {{ patient['nomor_rekam_medis'] }}</span><span class="pill">Status: {{ patient['status_antrian'] }}</span></div></div><div class="toolbar no-print"><button class="btn btn-primary" onclick="printPage()">🖨️ Cetak Hasil</button></div></div><div class="g2 grid" style="margin-top:16px"><div class="card"><h3>Ringkasan Pemeriksaan</h3>{% if soaps %}{% set s = soaps[0] %}<div class="small muted">Pemeriksaan terbaru: {{ fmt_dt(s['created_at']) }} oleh {{ s['doctor_name'] or s['doctor_username'] or '-' }}</div><div class="wrap" style="margin-top:8px"><strong>Assessment:</strong> {{ s['assessment'] or '-' }}</div><div class="wrap"><strong>Plan:</strong> {{ s['plan'] or '-' }}</div><div class="pill-list" style="margin-top:10px"><span class="pill">Usia Kehamilan: {{ s['usia_kehamilan'] or '-' }}</span><span class="pill">DJJ: {{ s['detak_jantung_janin'] or '-' }}</span><span class="pill">Posisi: {{ s['posisi_janin'] or '-' }}</span><span class="pill">EBJ: {{ s['estimasi_berat_janin'] or '-' }}</span></div>{% if s['catatan_dokter'] %}<div class="wrap" style="margin-top:10px"><strong>Catatan Dokter:</strong> {{ s['catatan_dokter'] }}</div>{% endif %}{% if s['rekomendasi_kontrol_ulang'] %}<div class="wrap"><strong>Kontrol Ulang:</strong> {{ s['rekomendasi_kontrol_ulang'] }}</div>{% endif %}{% else %}<div class="empty">Belum ada ringkasan pemeriksaan.</div>{% endif %}</div><div class="card"><h3>Ringkasan Billing</h3><div class="stat"><div class="small muted">Total tagihan tercatat</div><div style="font-size:30px;font-weight:800">{{ rupiah(total_bill) }}</div></div><div class="small muted" style="margin-top:10px">Hubungi klinik untuk rincian pembayaran bila diperlukan.</div></div></div><div class="card" style="margin-top:16px"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><h3 style="margin:0">File Hasil USG</h3><span class="badge">{{ files|length }} file</span></div>{% if files %}<table><thead><tr><th>Nama File</th><th>Tipe</th><th>Tanggal</th><th>Aksi</th></tr></thead><tbody>{% for f in files %}<tr><td>{{ f['original_filename'] }}</td><td>{{ file_badge(f['file_ext']) }}</td><td>{{ fmt_dt(f['created_at']) }}</td><td><a class="btn btn-sm" href="{{ url_for('patient_file_public', token=patient['access_token'], upload_id=f['id']) }}" target="_blank">Buka File</a></td></tr>{% endfor %}</tbody></table>{% else %}<div class="empty">Belum ada file hasil.</div>{% endif %}</div></div></div>
    '''
    return render_page('Hasil Pasien - ' + patient['nama_pasien'], body, patient=patient, files=files, soaps=soaps, total_bill=total_bill, file_badge=file_badge, fmt_dt=fmt_dt, rupiah=rupiah)


@app.route('/hasil/<token>/file/<int:upload_id>')
def patient_file_public(token, upload_id):
    conn = db(); cur = conn.cursor()
    cur.execute('SELECT up.* FROM uploads up JOIN patients p ON up.patient_id=p.id WHERE up.id=? AND p.access_token=?', (upload_id, token))
    row = cur.fetchone(); conn.close()
    if not row: abort(404)
    return send_from_directory(UPLOAD_DIR, row['stored_filename'], as_attachment=False, download_name=row['original_filename'])


@app.route('/soap-templates', methods=['GET', 'POST'])
@role_required('superadmin', 'admin', 'dokter')
def soap_templates_page():
    user = current_user()
    conn = db(); cur = conn.cursor()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        if title:
            cur.execute('INSERT INTO soap_templates (title,subjective,objective,assessment,plan,created_by,created_at) VALUES (?,?,?,?,?,?,?)', (title, request.form.get('subjective','').strip(), request.form.get('objective','').strip(), request.form.get('assessment','').strip(), request.form.get('plan','').strip(), user['id'], now()))
            conn.commit(); log_action('CREATE_SOAP_TEMPLATE', title); flash('Template SOAP berhasil ditambahkan.', 'success'); return redirect(url_for('soap_templates_page'))
        flash('Judul template wajib diisi.', 'danger')
    cur.execute('SELECT st.*, u.username FROM soap_templates st LEFT JOIN users u ON st.created_by=u.id ORDER BY st.id DESC'); rows = cur.fetchall(); conn.close()
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
    conn = db(); cur = conn.cursor()
    sql = 'SELECT b.*, p.nama_pasien, p.nomor_rekam_medis FROM billing b JOIN patients p ON b.patient_id=p.id WHERE 1=1'; params = []
    if q:
        like = '%' + q + '%'; sql += ' AND (p.nama_pasien LIKE ? OR p.nomor_rekam_medis LIKE ? OR b.item_name LIKE ?)'; params += [like, like, like]
    sql += ' ORDER BY b.created_at DESC'; cur.execute(sql, tuple(params)); rows = cur.fetchall(); conn.close()
    body = '''
    <div class="card no-print"><form class="searchbox"><input class="input" name="q" value="{{ q }}" placeholder="Cari pasien / RM / item billing..."><button class="btn btn-primary">🔍 Cari</button></form></div>
    <div class="card" style="margin-top:16px"><div style="display:flex;justify-content:space-between;align-items:center;gap:10px"><h3 style="margin:0">Billing Klinik</h3><span class="badge">{{ rows|length }} transaksi</span></div>{% if rows %}<table><thead><tr><th>Pasien</th><th>Item</th><th>Nominal</th><th>Status</th><th>Tanggal</th></tr></thead><tbody>{% for r in rows %}<tr><td><strong>{{ r['nama_pasien'] }}</strong><div class="small muted">{{ r['nomor_rekam_medis'] }}</div></td><td>{{ r['item_name'] }}<div class="small muted">{{ r['notes'] or '' }}</div></td><td>{{ rupiah(r['amount']) }}</td><td><span class="badge {{ 'paid' if r['status_bayar']=='lunas' else 'unpaid' }}">{{ r['status_bayar'] }}</span></td><td>{{ fmt_dt(r['created_at']) }}</td></tr>{% endfor %}</tbody></table>{% else %}<div class="empty">Belum ada billing.</div>{% endif %}</div>
    '''
    return render_page('Billing', body, q=q, rows=rows, rupiah=rupiah, fmt_dt=fmt_dt)


@app.route('/users', methods=['GET', 'POST'])
@role_required('superadmin')
def users_page():
    conn = db(); cur = conn.cursor()
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
    cur.execute('SELECT * FROM users ORDER BY id DESC'); rows = cur.fetchall(); conn.close()
    body = '''
    <div class="g2 grid"><div class="card no-print"><h3>Tambah User</h3><form method="post" class="grid"><div><label>Username</label><input class="input" name="username" required></div><div><label>Nama Lengkap</label><input class="input" name="full_name"></div><div><label>Password</label><input class="input" type="password" name="password" required></div><div><label>Role</label><select class="select" name="role"><option value="admin">admin</option><option value="dokter">dokter</option><option value="pasien">pasien</option><option value="superadmin">superadmin</option></select></div><div><label>Tautkan ke pasien (opsional untuk role pasien)</label><select class="select" name="patient_id"><option value="">- Tidak ditautkan -</option>{% for p in patients_list %}<option value="{{ p['id'] }}">{{ p['nama_pasien'] }} - {{ p['nomor_rekam_medis'] }}</option>{% endfor %}</select></div><button class="btn btn-primary">👤 Simpan User</button></form></div><div class="card"><div style="display:flex;justify-content:space-between;align-items:center;gap:10px"><h3 style="margin:0">Daftar User</h3><span class="badge">{{ rows|length }} user</span></div><table><thead><tr><th>Username</th><th>Role</th><th>Nama</th><th>Patient ID</th><th>Aktif</th></tr></thead><tbody>{% for r in rows %}<tr><td>{{ r['username'] }}</td><td>{{ r['role'] }}</td><td>{{ r['full_name'] or '-' }}</td><td>{{ r['patient_id'] or '-' }}</td><td>{{ 'Ya' if r['active'] else 'Tidak' }}</td></tr>{% endfor %}</tbody></table></div></div>
    '''
    return render_page('Manajemen User', body, rows=rows, patients_list=patients_list)


@app.route('/audit-logs')
@role_required('superadmin', 'admin')
def audit_logs_page():
    q = request.args.get('q', '').strip(); conn = db(); cur = conn.cursor(); sql = 'SELECT * FROM audit_logs WHERE 1=1'; params = []
    if q:
        like = '%' + q + '%'; sql += ' AND (username LIKE ? OR action LIKE ? OR details LIKE ?)'; params += [like, like, like]
    sql += ' ORDER BY id DESC LIMIT 300'; cur.execute(sql, tuple(params)); rows = cur.fetchall(); conn.close()
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
        # Update Nama
        new_name = request.form.get('full_name', '').strip()
        if 'full_name' in request.form:
            if not new_name:
                flash('Nama tidak boleh kosong.', 'danger')
            elif new_name != user['full_name']:
                conn = db(); cur = conn.cursor()
                cur.execute('UPDATE users SET full_name=?, updated_at=? WHERE id=?', (new_name, now(), user['id']))
                conn.commit(); conn.close()
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
                conn = db(); cur = conn.cursor(); cur.execute('UPDATE users SET password_hash=?, updated_at=? WHERE id=?', (generate_password_hash(new), now(), user['id'])); conn.commit(); conn.close(); log_action('CHANGE_PASSWORD', user['username']); flash('Password berhasil diubah.', 'success'); return redirect(url_for('settings'))
    body = '''
    <div class="g2 grid">
      <div class="card no-print">
        <h3>Ubah Profil</h3>
        <form method="post" class="grid mb-6">
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
    '''
    return render_page('Settings', body, fmt_dt=fmt_dt)


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


init_db()

if __name__ == '__main__':
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
