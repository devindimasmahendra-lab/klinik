import sqlite3
import random
import uuid
import os
from datetime import datetime, timedelta

# Konfigurasi Path Database
DB_PATH = 'usg4d_klinik.db'

# =============================================
# DATA SAMPEL - SEMUA PEREMPUAN (Klinik USG 4D)
# =============================================

NAMA_DEPAN = [
    "Siti", "Ani", "Dewi", "Ratna", "Lestari", "Rina", "Indah", "Sari", "Maya",
    "Rina", "Putri", "Wati", "Erna", "Yuli", "Susi", "Murni", "Ningsih", "Herlina",
    "Eka", "Yanti", "Neni", "Tuti", "Ika", "Mila", "Vera", "Lia", "Risa", "Dina",
    "Ayu", "Kartini", "Wulan", "Aisyah", "Nabila", "Fatimah", "Zahra", "Aminah",
    "Halimah", "Rohani", "Sumarni", "Lastri"
]
NAMA_BELAKANG = [
    "Rahmawati", "Lestari", "Kusuma", "Hidayat", "Pratama", "Wijaya", "Putri",
    "Setiawan", "Utami", "Fauzi", "Sholehah", "Ramadhan", "Handayani", "Saputri",
    "Anggraini", "Permatasari", "Oktaviani", "Sari", " Astuti", "Purwanti",
    "Kusumawati", "Indahsari", "Novianti", "Maulani"
]
NAMA_SUAMI = [
    "Budi", "Agus", "Yusuf", "Bambang", "Eko", "Andi", "Dedi", "Heri", "Rudi",
    "Taufik", "Asep", "Rizal", "Firmansyah", "Hendra", "Adi", "Bagus",
    "Dimas", "Fajar", "Gilang", "Hadi", "Imam", "Joko"
]
ALAMAT = [
    "Jl. Melati No. ", "Jl. Mawar Gg. 4 No. ", "Jl. Kenanga No. ",
    "Jl. Anggrek No. ", "Perumahan Indah Blok ", "Jl. Raya Utama No. ",
    "Jl. Dewi Sartika No. ", "Jl. Kartini No. ", "Jl. Diponegoro No. ",
    "Jl. Sudirman No. ", "Jl. Ahmad Yani No. ", "Jl. Gatot Subroto No. "
]
KOTA = ["Jakarta", "Bandung", "Surabaya", "Medan", "Palembang", "Semarang", "Makassar", "Yogyakarta", "Bogor", "Bekasi", "Tangerang", "Depok"]
PEKERJAAN = ["Ibu Rumah Tangga", "Pegawai Swasta", "Wiraswasta", "PNS", "Guru", "Perawat", "Bidan", "Dosen", "Karyawan BUMN", "Designer"]

# Keluhan per trimester
KELUHAN_T1 = [
    "Kontrol rutin kehamilan trimester 1",
    "Mual muntah berlebihan sejak usia 6 minggu",
    "Nyeri perut bagian bawah sejak 1 minggu",
    "Pusing dan mual, telat menstruasi 8 minggu",
    "Ingin USG pertama kali untuk memastikan kehamilan",
    "Keluhan keputihan sejak hamil muda",
    "Berat badan turun karena mual tidak nafsu makan",
    "Spotting flek cokelat 2 hari, khawatir keguguran",
    "Konsultasi program hamil setelah 1 tahun menikah",
    "Minta cek HB dan lab awal kehamilan"
]
KELUHAN_T2 = [
    "Kontrol rutin trimester 2, sudah mulai gerakan janin",
    "Nyeri punggung bawah sejak usia 5 bulan",
    "Bengkak pada kaki dan tangan sejak minggu ke-24",
    "Ingin cek jenis kelamin janin",
    "Gerakan janin berkurang, ingin cek detak jantung janin",
    "Mual mulai membaik, nafsu makan meningkat",
    "Gatal-gatal pada perut bagian bawah",
    "Sering kram kaki malam hari",
    "Ingin USG 4D untuk lihat wajah bayi",
    "Hasil gula darah screening positif, kontrol ulang"
]
KELUHAN_T3 = [
    "Kontrol rutin trimester 3, persiapan persalinan",
    "Nyeri panggul dan sesak napas saat tidur",
    "Posisi janin belum sempurna, minta penanganan",
    "Kaki bengkak besar dan tekanan darah naik",
    "Braxton Hicks sering terjadi sejak minggu ke-32",
    "Keputihan berlebih dan gatal pada trimester 3",
    "Pusing kepala dan penglihatan kabur, khawatir preeklamsia",
    "Minta estimasi berat janin dan posisi plasenta",
    "Persiapan persalinan normal, minta penjelasan proses",
    "Kontraksi palsu mulai terjadi, ingin konsultasi"
]

# Diagnosis per trimester dengan kode ICD-10
DIAGNOSA_T1 = [
    ("G1P0A0 Hamil 12 Minggu", "Z34.0"),
    ("G2P1A0 Hamil 10 Minggu", "Z34.1"),
    ("Kehamilan Normal Trimester 1", "Z34.8"),
    ("G1P0A0 Hamil 8 Minggu (Kehamilan Dini)", "Z34.0"),
    ("Threatened Abortion (Ancaman Keguguran)", "O20.0"),
    ("G1P0A0 Hamil 14 Minggu", "Z34.0"),
    ("G3P2A1 Hamil 9 Minggu", "Z34.2"),
    ("Hyperemesis Gravidarum Ringan", "O21.0"),
    ("G1P0A0 Hamil 11 Minggu", "Z34.0"),
    ("Kehamilan Ganda (Twins) 10 Minggu", "Z34.0")
]
DIAGNOSA_T2 = [
    ("G2P1A0 Hamil 24 Minggu", "Z34.1"),
    ("G1P0A0 Hamil 20 Minggu", "Z34.0"),
    ("G3P2A0 Hamil 26 Minggu", "Z34.2"),
    ("Kehamilan Normal Trimester 2", "Z34.8"),
    ("Gestational Diabetes Mellitus (GDM)", "O24.4"),
    ("Anemia Ringan pada Kehamilan", "O99.0"),
    ("G2P1A0 Hamil 22 Minggu", "Z34.1"),
    ("G1P0A0 Hamil 28 Minggu", "Z34.0"),
    ("Placenta Previa Minor", "O44.1"),
    ("Suspect IUGR (Pertumbuhan Janin Terhambat)", "O36.5")
]
DIAGNOSA_T3 = [
    ("G1P0A0 Hamil 36 Minggu", "Z34.0"),
    ("G2P1A0 Hamil 34 Minggu", "Z34.1"),
    ("G3P2A0 Hamil 38 Minggu", "Z34.2"),
    ("Suspect Preeklampsia Ringan", "O13.9"),
    ("Letak Sungsang (Breech Presentation)", "O32.1"),
    ("G1P0A0 Hamil 32 Minggu", "Z34.0"),
    ("G4P3A0 Hamil 37 Minggu", "Z34.2"),
    ("Kehamilan Lintang (Transverse Lie)", "O32.5"),
    ("Polyhydramnios Ringan", "O40.1"),
    ("G2P1A0 Hamil 35 Minggu, Persiapan Persalinan", "Z34.1")
]

# Plan/anjuran per trimester
PLAN_T1 = [
    "Lanjut suplemen asam folat 400mcg/hari dan kalsium. Kontrol ulang 2 minggu lagi.",
    "Istirahat cukup, hindari aktivitas berat. Minum vitamin prenatal rutin. Kontrol bulanan.",
    "Pantau gejala spotting. Jika flek banyak atau nyeri hebat, segera ke IGD. Kontrol 2 minggu.",
    "Lanjutkan asam folat. Penuhi kebutuhan nutrisi. Kontrol ulang untuk USG pertama.",
    "Rutin minum vitamin kehamilan. Perbanyak air putih dan protein. Kontrol bulanan.",
    "Periksa laboratorium lengkap (HB, golongan darah, urin). Kontrol ulang 2 minggu.",
    "Diet seimbang, hindari makanan mentah. Lanjut suplemen prenatal. Kontrol bulanan.",
    "Bed rest 1-2 hari. Hindari hubungan intim sementara. Kontrol ulang 1 minggu.",
    "Minum asam folat dan vitamin D. Rutin kontrol per bulan. Mulai catat gerakan janin.",
    "Edukasi tanda bahaya kehamilan muda. Kontrol ulang 2 minggu."
]
PLAN_T2 = [
    "Lanjut suplemen kalsium dan zat besi. Kontrol ulang 1 bulan. Mulai hitung gerakan janin.",
    "Fisioterapi ringan untuk nyeri punggung. Kompres hangat. Kontrol bulanan.",
    "Elevasi kaki saat istirahat. Monitoring tekanan darah. Kontrol ulang 2 minggu.",
    "Lanjutkan nutrisi seimbang. Kontrol ulang untuk cek pertumbuhan janin.",
    "Kick count 10 gerakan dalam 2 jam. Jika berkurang, segera ke klinik. Kontrol 2 minggu.",
    "Tingkatkan asupan protein dan sayuran. Lanjut vitamin prenatal. Kontrol bulanan.",
    "Gunakan pelembab kulit. Mandi air hangat. Hindari sabun keras. Kontrol bulanan.",
    "Peregangan ringan dan kompres hangat untuk kram. Minum air putih cukup. Kontrol bulanan.",
    "USG 4D terjadwal. Lanjutkan suplemen. Kontrol bulanan.",
    "Gula darah puasa dan postprandial. Diet rendah gula. Kontrol ulang 2 minggu."
]
PLAN_T3 = [
    "Persiapan persalinan: siapkan dokumen, tas persiapan. Kontrol mingguan mulai minggu ke-36.",
    "Istirahat miring ke kiri. Elevasi kaki. Monitoring tekanan darah harian. Kontrol mingguan.",
    "Latihan posisi jonggol untuk koreksi posisi janin. Kontrol ulang 1 minggu.",
    "Monitoring tekanan darah 2x sehari. Diet rendah garam. Jika memburuk, rawat inap.",
    "Istirahat cukup. Minum banyak air. Pantau frekuensi kontraksi. Kontrol mingguan.",
    "Keputihan normal jika tidak berbau. Gunakan celana dalam katun. Kontrol bulanan.",
    "Segera ke IGD jika sakit kepala hebat, penglihatan kabur, atau nyeri ulu hati.",
    "USG untuk estimasi berat janin dan plasenta. Persiapan persalinan. Kontrol mingguan.",
    "Konsultasi rencana persalinan normal. Siapkan birth plan. Kontrol mingguan.",
    "Bed rest jika kontraksi sering. Persiapan masuk RS. Kontrol 3 hari lagi."
]

CATATAN_DOKTER = [
    "Janin aktif, ketuban cukup, plasenta fundus posterior. Semua dalam batas normal.",
    "DJJ normal 140x/menit. Presentasi kepala, HOD. Plasenta tidak menghalangi jalan lahir.",
    "Pertumbuhan janin sesuai usia kehamilan. BPD, HC, AC, FL dalam range normal.",
    "Tidak ada tanda preeklamsia. Tekanan darah stabil. Edema ringan fisiologis.",
    "Plasenta insertasi normal, tidak ada previa. Ketuban cukup (AFI 12 cm).",
    "Janin dalam posisi oblik, kemungkinan akan rotasi sendiri. Kontrol ulang 1 minggu.",
    "USG menunjukkan janin perempuan, sehat dan aktif. Berat estimasi sesuai.",
    "DJJ 148x/menit, irama regular. Gerakan janin baik. Tidak ada anomaly.",
    "Serviks tertutup, panjang normal. Tidak ada tanda persalinan prematur.",
    "Growth scan menunjukkan pertumbuhan normal. Estimasi berat janin 2800 gram.",
    "Ketuban jernih, plasenta matang grade 2. Janin siap untuk persalinan.",
    "Deteksi denyut jantung janin baik. Tidak ada arrhythmia. Posisi kepala bawah.",
    "Pemeriksaan lengkap: Hb 12.5, golongan darah A, HBsAg negatif, HIV non-reaktif.",
    "Preeklamsia ringan, tekanan darah 145/95. Observasi ketat, kontrol 3 hari lagi.",
    "Janin sungsang pada usia 34 minggu, rekomendasikan versi luar jika memungkinkan.",
    "Plasenta previa minor, bed rest direkomendasikan. Kontrol ulang 2 minggu dengan USG.",
    "Polyhydramnios ringan, AFI 24 cm. Evaluasi kemungkinan GDM. Diet ketat.",
    "IUGR ringan, EBJ 2400g pada 36 minggu. Nutrisi tinggi protein, kontrol mingguan.",
    "Kehamilan ganda dikonfirmasi, kedua janin sehat. Persiapan persalinan di RS tipe A.",
    "Hasil USG 4D sangat baik, fitur wajah bayi terlihat jelas. Janin sehat dan aktif."
]

REKOMENDASI_KONTROL = [
    "Kontrol ulang 2 minggu lagi di poli OG.",
    "Kontrol bulan depan, bawa hasil lab sebelumnya.",
    "Kontrol ulang 1 minggu lagi untuk pantau tekanan darah.",
    "Kontrol mingguan mulai minggu ke-36.",
    "Kontrol 3 hari lagi untuk evaluasi preeklamsia.",
    "Kontrol ulang 2 minggu dengan USG control.",
    "Kontrol bulanan, bawa buku KIA.",
    "Kontrol ulang 1 minggu untuk cek posisi janin.",
    "Kontrol segera jika gejala memburuk.",
    "Kontrol rutin 2 minggu sekali hingga persalinan."
]

def seed():
    if not os.path.exists(DB_PATH):
        print(f"Error: {DB_PATH} tidak ditemukan. Silahkan jalankan klinik.py sekali dulu untuk inisialisasi DB.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Hapus data lama agar fresh
    print("[*] Membersihkan data lama...")
    cur.execute("DELETE FROM billing")
    cur.execute("DELETE FROM soap_records")
    cur.execute("DELETE FROM patients")

    # Ambil ID dokter dan admin
    cur.execute("SELECT id FROM users WHERE role='dokter' LIMIT 1")
    doctor_row = cur.fetchone()
    doctor_id = doctor_row['id'] if doctor_row else 3

    cur.execute("SELECT id FROM users WHERE role IN ('superadmin', 'admin') LIMIT 1")
    admin_row = cur.fetchone()
    admin_id = admin_row['id'] if admin_row else 1

    print(f"[*] Menghubungkan ke database {DB_PATH}...")
    print("[*] Memulai pembuatan 550 data sampel pasien (PEREMPUAN), rekam medis, dan billing...")

    for i in range(550):
        # 1. Generate Data Pasien (SEMUA PEREMPUAN)
        nama = f"{random.choice(NAMA_DEPAN)} {random.choice(NAMA_BELAKANG)}"
        rm = f"RM{datetime.now().strftime('%y%m%d')}{i+1000:04d}"
        nik = f"3201{random.randint(100000000000, 999999999999)}"
        tgl_lahir = (datetime.now() - timedelta(days=random.randint(7000, 13000))).strftime('%Y-%m-%d')
        umur = f"{random.randint(19, 42)} tahun"
        alamat_full = f"{random.choice(ALAMAT)}{random.randint(1, 120)}, {random.choice(KOTA)}"
        hp = f"0812{random.randint(10000000, 99999999)}"
        token = uuid.uuid4().hex
        nama_suami = f"Bp. {random.choice(NAMA_SUAMI)}"

        # Antropometri realistis untuk perempuan hamil
        tb = round(random.uniform(148.0, 170.0), 1)
        bb = round(random.uniform(45.0, 85.0), 1)
        bmi = round(bb / ((tb / 100) ** 2), 1)
        lp = round(random.uniform(75.0, 105.0), 1)

        # Distribusi status: 70% selesai, 20% diperiksa, 10% menunggu
        status_q = random.choices(
            ['selesai', 'diperiksa', 'menunggu'],
            weights=[70, 20, 10],
            k=1
        )[0]

        # Tanggal pendaftaran dalam 9 bulan terakhir
        created_at = (datetime.now() - timedelta(days=random.randint(0, 270))).strftime('%Y-%m-%d %H:%M:%S')

        cur.execute('''
            INSERT INTO patients (
                nama_pasien, nomor_rekam_medis, nik, tanggal_lahir, umur, alamat, nomor_hp,
                golongan_darah, status_perkawinan, pekerjaan, nama_keluarga, jenis_layanan,
                dokter_tujuan, status_antrian, access_token, created_by, created_at, updated_at,
                prioritas, tinggi_badan, berat_badan, lingkar_perut, bmi, deleted
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
        ''', (
            nama, rm, nik, tgl_lahir, umur, alamat_full, hp,
            random.choice(['A', 'B', 'AB', 'O']), 'Menikah', random.choice(PEKERJAAN),
            nama_suami, random.choice(['Umum', 'BPJS']),
            "Dr. Kemas Anhar, Sp.OG", status_q, token, admin_id, created_at, created_at,
            random.choice(['Non-urgent', 'Urgent', 'Non-urgent', 'Non-urgent']),
            tb, bb, lp, bmi
        ))

        patient_id = cur.lastrowid

        # 2. Generate SOAP LENGKAP untuk pasien yang sudah diperiksa/selesai
        if status_q in ('diperiksa', 'selesai'):
            num_visits = random.choices([2, 3, 4], weights=[30, 50, 20], k=1)[0]
            trimester_start = random.choice([1, 2, 3])

            for v in range(num_visits):
                trimester = min(trimester_start + v, 3)
                base_date = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
                visit_date = (base_date + timedelta(days=v * 30)).strftime('%Y-%m-%d %H:%M:%S')

                if trimester == 1:
                    usia_h = random.randint(8, 14)
                elif trimester == 2:
                    usia_h = random.randint(16, 28)
                else:
                    usia_h = random.randint(30, 39)

                # EBJ realistis per usia kehamilan
                if usia_h <= 14:
                    ebj = random.randint(20, 80)
                elif usia_h <= 28:
                    ebj = random.randint(300, 1200)
                else:
                    ebj = random.randint(1800, 3500)

                if trimester == 1:
                    keluhan = random.choice(KELUHAN_T1)
                    diag, icd = random.choice(DIAGNOSA_T1)
                    plan = random.choice(PLAN_T1)
                elif trimester == 2:
                    keluhan = random.choice(KELUHAN_T2)
                    diag, icd = random.choice(DIAGNOSA_T2)
                    plan = random.choice(PLAN_T2)
                else:
                    keluhan = random.choice(KELUHAN_T3)
                    diag, icd = random.choice(DIAGNOSA_T3)
                    plan = random.choice(PLAN_T3)

                catatan = random.choice(CATATAN_DOKTER)
                rekomendasi = random.choice(REKOMENDASI_KONTROL)

                djj = random.randint(120, 160)
                if trimester == 3:
                    posisi = random.choices(['Kepala', 'Sungsang', 'Lintang'], weights=[70, 20, 10], k=1)[0]
                else:
                    posisi = random.choices(['Kepala', 'Sungsang', 'Lintang', 'Belum Tetap'], weights=[30, 10, 10, 50], k=1)[0]

                td_sis = random.randint(100, 140)
                td_dia = random.randint(60, 90)
                if random.random() < 0.08:
                    td_sis = random.randint(140, 160)
                    td_dia = random.randint(90, 110)

                nadi = random.randint(78, 92)
                suhu = round(random.uniform(36.2, 36.8), 1)
                rr_val = random.randint(18, 22)
                lp_val = round(random.uniform(25.0, 38.0), 1)

                objective = f"KU Baik, Compos Mentis. TD {td_sis}/{td_dia} mmHg, Nadi {nadi}x/menit, Suhu {suhu}C, RR {rr_val}x/menit. BJTU {djj}x/menit. Posisi janin: {posisi}. EBJ: {ebj} gram. Lingkar perut: {lp_val} cm."

                cur.execute('''
                    INSERT INTO soap_records (
                        patient_id, doctor_id, subjective, objective, assessment, plan,
                        kode_icd10, td_sistolik, td_diastolik, nadi, suhu, rr, informed_consent,
                        usia_kehamilan, detak_jantung_janin, posisi_janin, estimasi_berat_janin,
                        catatan_dokter, rekomendasi_kontrol_ulang, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ''', (
                    patient_id, doctor_id,
                    keluhan,
                    objective,
                    f"{diag} - {catatan}",
                    plan,
                    icd,
                    str(td_sis), str(td_dia),
                    str(nadi), str(suhu), str(rr_val),
                    1,
                    f"{usia_h} minggu",
                    str(djj),
                    posisi,
                    str(ebj),
                    catatan,
                    rekomendasi,
                    visit_date, visit_date
                ))

        # 3. Generate Billing LENGKAP
        billing_items = [
            ("Konsultasi Dokter Sp.OG", 200000),
            ("USG 4D + Print Foto", 350000),
            ("USG 2D Kontrol", 150000),
            ("Vitamin Prenatal & Suplemen", 175000),
            ("Administrasi Pendaftaran", 25000),
            ("Laboratorium (Darah Lengkap)", 125000),
            ("Laboratorium (Urine Lengkap)", 75000),
            ("Paket Persiapan Persalinan", 500000),
        ]
        num_billing = random.randint(3, 6)
        selected_items = random.sample(billing_items, num_billing)

        for item_name, base_price in selected_items:
            amount = base_price + random.choice([0, 0, 0, 5000, -5000, 10000])
            status_bayar = random.choices(['lunas', 'belum_lunas'], weights=[75, 25], k=1)[0]
            billing_date = (datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S') + timedelta(days=random.randint(0, 3))).strftime('%Y-%m-%d %H:%M:%S')

            cur.execute('''
                INSERT INTO billing (patient_id, item_name, amount, status_bayar, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (patient_id, item_name, amount, status_bayar, admin_id, billing_date))

    conn.commit()
    conn.close()

    print("-" * 60)
    print("[+] SUKSES! Data seed telah dimasukkan:")
    print("    - 550 Pasien (SEMUA PEREMPUAN)")
    print("    - 2-4 kunjungan SOAP per pasien (total ~1500+ rekam medis)")
    print("    - Setiap SOAP: Subjective, Objective, Assessment, Plan, ICD-10 lengkap")
    print("    - Vital signs realistis (TD, Nadi, Suhu, RR, DJJ)")
    print("    - Estimasi berat janin (EBJ) logis per usia kehamilan")
    print("    - Billing 3-6 item per pasien (total ~1700+ transaksi)")
    print("    - Data trimester 1, 2, 3 bervariasi")
    print("-" * 60)
    print("[+] Jalankan 'python klinik.py' untuk demo.")

if __name__ == "__main__":
    seed()