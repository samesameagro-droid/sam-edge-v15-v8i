# SAM EDGE V15 — V8I FROZEN | GitHub Actions 24H Paper

Versi ini mengubah `main_paper.py` yang sebelumnya berjalan loop di laptop menjadi **one-shot scanner** (`paper_scan_once.py`). GitHub Actions menjalankannya setiap 5 menit, sehingga laptop tidak perlu menyala.

## Arsitektur

- Signal timeframe: **15M**
- Tracking active paper trade: **5M**
- Core: **V8I_ADX_ASYM (FROZEN)**
- RR: **1.25R**
- Risk: **0.50% per trade**
- Universe: dynamic BingX USDT linear swaps
- Filter: crypto-only + minimum 24h quote volume $10M
- Default universe: top 100 by volume
- Mode: **PAPER ONLY** — tidak ada endpoint order/trading
- Scheduler: **setiap 5 menit**

## Penting

Workflow ini paling cocok untuk **repository PUBLIC**. GitHub menyatakan standard GitHub-hosted runners gratis dan unlimited pada public repositories. Untuk private repository, GitHub Free hanya memberi kuota Actions bulanan, sehingga scan setiap 5 menit tidak cocok untuk 24/7.

Kode strategi dan jurnal paper akan terlihat di repository public. **Jangan pernah memasukkan BOT_TOKEN atau CHAT_ID ke file repository.** Token disimpan sebagai GitHub Actions Secrets.

## 1. Buat repository GitHub

Buat repository baru, sebaiknya **Public**.

Contoh nama:

`sam-edge-v15-v8i-paper`

## 2. Upload isi folder ini

Struktur minimal:

```text
sam-edge-v15-v8i-paper/
├── .github/
│   └── workflows/
│       └── v8i_paper.yml
├── core_engine_v8.py
├── main_paper.py
├── notifier.py
├── paper_scan_once.py
├── requirements.txt
├── .gitignore
└── README_GITHUB_24H.md
```

## 3. Tambahkan Telegram Secrets

Di repository:

**Settings → Secrets and variables → Actions → New repository secret**

Buat dua secret:

- `BOT_TOKEN` = token bot Telegram
- `CHAT_ID` = chat ID Telegram tujuan

Jangan tulis nilainya di source code.

## 4. Aktifkan Actions

Buka tab **Actions** repository. Pastikan workflow `SAM EDGE V15 - V8I Paper Scanner` terlihat dan Actions diizinkan berjalan.

## 5. Test manual

Buka:

**Actions → SAM EDGE V15 - V8I Paper Scanner → Run workflow**

Jalankan sekali.

Di log harus muncul kira-kira:

```text
STARTUP OK | V8I FROZEN | GITHUB ACTIONS ONE-SHOT | PAPER ONLY
UNIVERSE DISCOVERED | ... symbols | mode=TOP_VOLUME
```

Jika ada signal:

```text
SIGNAL ...
```

Telegram akan menerima alert bila kedua secret sudah benar.

## 6. Setelah itu otomatis

Workflow dijadwalkan setiap 5 menit. Setiap run:

1. checkout repository
2. restore state/journal dari commit sebelumnya
3. scan dynamic BingX universe
4. evaluasi V8I pada candle 15M yang sudah close
5. track posisi aktif dengan candle 5M
6. kirim Telegram bila ada signal
7. simpan state/journal
8. commit hasil kembali ke repository
9. selesai

Jadi runner tidak perlu hidup terus-menerus.

## File state yang akan muncul otomatis

- `paper_v8i_state.json` — equity + posisi aktif + closed trades
- `paper_v8i_trades.csv` — jurnal trade
- `paper_v8i_signal_history.json` — anti-duplicate signal
- `paper_v8i_universe.json` — universe terakhir

Semua itu **paper data**, bukan API trading credential.

## Catatan scheduler

GitHub menggunakan UTC secara default, tetapi workflow ini memakai timezone `Asia/Jakarta`. Interval minimum schedule adalah 5 menit. Scheduled run dapat mengalami sedikit keterlambatan saat beban GitHub tinggi.

Workflow juga memakai `concurrency` agar dua scan tidak memproses state yang sama secara bersamaan.

## Keamanan

Repository public berarti `core_engine_v8.py` dan jurnal paper dapat dilihat orang lain. Secret Telegram tetap berada di GitHub Secrets dan tidak ditulis ke repository.

**Jangan pernah menambahkan:**

- `BOT_TOKEN=...` ke file Python
- `CHAT_ID=...` ke source
- API key BingX private trading
- secret exchange key apa pun

Sistem ini hanya menggunakan market-data/public endpoints dari BingX dan mode paper.
