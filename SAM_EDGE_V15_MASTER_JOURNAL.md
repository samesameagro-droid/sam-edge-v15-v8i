# SAM EDGE V15 — Master Forward Journal

## Cohort
- Baseline historis: **#1–#100**
- Forward cohort: **#101–#200**
- Starting equity cohort: **$100**
- Risk model: **1% risk per trade** (nominal $1 pada equity awal $100)
- Mode: **PAPER ONLY**
- Core aktif pada cohort ini: **V15_PRECISION_V2_FINAL**

## Status terbaru
- CLOSED seluruh sistem: **186 trade** (100 baseline + 86 forward)
- CLOSED cohort #101–#186: **86 trade**
- Wins / losses cohort: **41 / 45**
- Win rate cohort: **47.67%**
- Net result cohort: **+6.25R**
- Profit factor cohort: **1.1389**
- ONGOING: **1 posisi (#187)**
- Sisa menuju 100 trade forward CLOSED: **14**
- Next trade number: **#188**
- Status: **RUNNING**

## Statistik seluruh sistem
- Wins / losses: **92 / 94**
- Win rate: **49.46%**
- Net R: **+21.00R**
- Profit factor: **1.2234**
- Max drawdown: **13.75R**
- Paper equity tercatat: **$105.8717**
- Current loss streak: **3** (cohort max loss streak: **6**)

## Diagnostik cohort #101–#186
- Recent 10: **2W / 8L**, win rate **20.0%**, net **-5.50R**, PF **0.3125**
- Recent 20: **8W / 12L**, win rate **40.0%**, net **-2.00R**, PF **0.8333**
- LONG: **31 trade**, 17W / 14L, win rate **54.84%**, net **+7.25R**, PF **1.5179**
- SHORT: **55 trade**, 24W / 31L, win rate **43.64%**, net **-1.00R**, PF **0.9677**

## ONGOING #187
| Trade | Coin | Side | Entry | SL | TP | Score | Core | Status |
|---:|---|---|---:|---:|---:|---:|---|---|
| **#187** | **XRP/USDT:USDT** | **SHORT** | **1.1949** | **1.2054593700792937** | **1.181700787400883** | **54.2881** | **V15_PRECISION_V2_FINAL** | **ONGOING** |

## Closed journal and state files
- [Master Journal CSV — all CLOSED trades](https://github.com/samesameagro-droid/sam-edge-v15-v8i/blob/main/v15_forward_test_master_journal.csv)
- [Ongoing Journal CSV](https://github.com/samesameagro-droid/sam-edge-v15-v8i/blob/main/v15_forward_test_ongoing.csv)
- [Master State JSON](https://github.com/samesameagro-droid/sam-edge-v15-v8i/blob/main/v15_forward_test_master_state.json)
- [Forward Summary JSON](https://github.com/samesameagro-droid/sam-edge-v15-v8i/blob/main/v15_forward_test_summary.json)

## Data notes
- CLOSED dihitung hanya jika CSV master mencatat TP HIT atau SL HIT.
- ONGOING tidak dimasukkan ke win rate, Net R, atau profit cohort.
- Kolom score pada CLOSED CSV saat ini kosong; jangan mengisi score secara estimasi.
- Profit nominal per trade di ringkasan manual memakai model 1R = $1 dari modal awal $100: TP +$1.25 dan SL -$1.00. Angka equity aktual pada JSON tetap menjadi acuan performa sistem.
- Performa terbaru melemah pada recent 10 dan recent 20; jangan menaikkan risiko atau mengubah strategi sebelum evaluasi validasi selesai.
