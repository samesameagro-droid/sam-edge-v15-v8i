# SAM EDGE V15 — Master Forward Journal

## Cohort
- Baseline: **#1–#100** (historical, closed)
- Forward cohort: **#101–#200**
- Starting equity: **$100**
- Risk: **1% per trade**
- Paper only

## Status sekarang
- CLOSED: **6 trade** (#101–#106)
- ONGOING: **1 trade**
- Next available trade number: **#108**
- Max active positions: **5**
- Cohort status: **RUNNING**

### CLOSED #101–#106
| Trade | Coin | Side | Result |
|---:|---|---|---|
| #101 | ORDI | LONG | TP +1.25R |
| #102 | ARB | LONG | TP +1.25R |
| #103 | RUNE | LONG | TP +1.25R |
| #104 | TIA | LONG | SL -1.00R |
| #105 | FIL | LONG | TP +1.25R |
| #106 | IMX | LONG | TP +1.25R |

### 🔵 ONGOING
| Trade | Coin | Side | Entry | SL | TP | Core | Status |
|---:|---|---|---:|---:|---:|---|---|
| **#107** | **TAO/USDT:USDT** | LONG | 313.36 | 308.1505 | 319.8719 | V15_PRECISION_V2_FINAL | **ONGOING** |

**Aturan:** trade yang masih OPEN tidak masuk statistik CLOSED sampai TP/SL benar-benar terkonfirmasi. Jika muncul beberapa posisi aktif, semuanya akan dicantumkan sebagai #107, #108, #109, dst. berdasarkan urutan waktu entry.

## File sumber
- [Master Journal CSV](https://github.com/samesameagro-droid/sam-edge-v15-v8i/blob/main/v15_forward_test_master_journal.csv) — CLOSED
- [Ongoing Journal CSV](https://github.com/samesameagro-droid/sam-edge-v15-v8i/blob/main/v15_forward_test_ongoing.csv) — ONGOING
- [Master State JSON](https://github.com/samesameagro-droid/sam-edge-v15-v8i/blob/main/v15_forward_test_master_state.json) — status gabungan
- [Forward Summary JSON](https://github.com/samesameagro-droid/sam-edge-v15-v8i/blob/main/v15_forward_test_summary.json) — statistik cohort
