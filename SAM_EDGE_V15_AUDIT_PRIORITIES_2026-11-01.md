# SAM EDGE V15 — Audit Prioritas 1–5
Tanggal audit: 2026-11-01
Branch: `audit/sam-edge-priorities-2026-11-01`
Tujuan: audit data, diagnosis loss, evaluasi kandidat secara terpisah, validasi OOS, dan keputusan core. Tidak ada perubahan pada core produksi atau konfigurasi eksekusi.

## Ringkasan keputusan
- **Core produksi: TIDAK DIUBAH.** Cohort forward-test #101–#200 tetap memakai V15_PRECISION_V2_FINAL hingga protokol cohort selesai; kandidat tetap shadow-only.
- **Jurnal otoritatif:** `v15_forward_test_master_journal.csv` + `v15_forward_test_summary.json` + `v15_forward_test_master_state.json`. Markdown master journal lama terbukti stale dan tidak boleh dipakai sebagai statistik terkini.
- **Validasi kandidat OOS: BELUM LULUS / belum dapat dinilai.** Data kandidat historis adalah filter counterfactual pada cohort lama, bukan hasil replay portfolio penuh atau hasil OOS.
- **Penyebab risiko paling kuat pada cohort #101–#155:** SHORT negatif dan kerugian berkelompok pada 20 trade terakhir. Data belum membuktikan bahwa mematikan SHORT akan memperbaiki portfolio secara keseluruhan karena tidak ada replay dengan penggantian slot dan sinyal alternatif.
- **Hambatan audit:** 155 baris jurnal tidak memuat score, strategy fingerprint, execution parity, dan failure-shield diagnostics. Tanpa candle OHLCV historis yang disimpan serta log sinyal kandidat yang mencakup sinyal PASS *dan* REJECT, full event replay tidak dapat direkonstruksi secara independen dari file ini saja.

## Prioritas 1 — Rekonsiliasi jurnal dan statistik

### Hasil rekonsiliasi
Sumber:
- `v15_forward_test_master_journal.csv`: 155 baris closed, trade #1–#155.
- `v15_forward_test_summary.json`: diperbarui 2026-11-01 08:17:44 UTC, 155 closed, cohort #101–#155 berjumlah 55, remaining 45, active positions 0, next trade #156.
- `v15_forward_test_master_state.json`: konsisten dengan 155 closed dan 0 ongoing.
- `v15_forward_test_ongoing.csv`: header saja, tidak ada posisi ongoing.
- `SAM_EDGE_V15_MASTER_JOURNAL.md`: TIDAK KONSISTEN; masih mengklaim hanya #101–#106 closed dan #107 TAO ongoing.

### Statistik dihitung ulang dari CSV
| Metrik | Seluruh #1–#155 | Forward cohort #101–#155 |
|---|---:|---:|
| Closed trades | 155 | 55 |
| TP | 77 | 26 |
| SL | 78 | 29 |
| Win rate | 49.68% | 47.27% |
| Net R | +18.25R | +3.50R |
| Gross winning R | 96.25R | 32.50R |
| Gross losing R | 78.00R | 29.00R |
| Profit factor | 1.2340 | 1.1207 |
| Expectancy | +0.1177R/trade | +0.0636R/trade |

Dedup: 0 duplicate `trade_key` pada master CSV. #101–#155 semuanya closed; #107 adalah STRK closed dan #108 TAO closed, bukan ongoing. Angka ini menggantikan status Markdown lama, bukan menimpa isi CSV.

### Data integrity gaps
- 155/155 baris tidak memiliki nilai `score`.
- 155/155 baris tidak memiliki `strategy_fingerprint`.
- 155/155 baris tidak memiliki `execution_parity`.
- 155/155 baris tidak memiliki `failure_shield` diagnostics.
- Nilai statistik agregat yang sama ditanam di setiap baris CSV; statistik harus dihitung ulang dari kolom per-trade, bukan dipercaya dari kolom agregat berulang.
- Audit parity tanggal 2026-09-25 memang menyatakan cohort lama #1–#100 memiliki fingerprint UNKNOWN. Namun requirement untuk fingerprint dan execution diagnostics pada cohort baru juga belum tercermin di master CSV. Ini perlu diperbaiki pada logging ke depan; jangan mengarang metadata historis.

Tindakan: laporan ini menetapkan CSV + JSON summary/state sebagai sumber utama; Markdown status lama ditandai stale/dikoreksi pada branch audit saja. Core produksi tidak diubah.

## Prioritas 2 — Audit loss, terutama SHORT dan 20 trade terakhir

### Per arah pada cohort #101–#155
| Side | N | W/L | Win rate | Net R | PF | Expectancy |
|---|---:|---:|---:|---:|---:|---:|
| LONG | 31 | 17/14 | 54.84% | +7.25R | 1.5179 | +0.2339R |
| SHORT | 24 | 9/15 | 37.50% | -3.75R | 0.7500 | -0.1563R |

SHORT adalah sumber kelemahan yang terukur pada cohort saat ini. Namun data ini belum membuktikan bahwa SHORT harus dinonaktifkan permanen; perlu shadow counterfactual dan portfolio replay, karena penolakan satu trade bisa membuka slot untuk sinyal berikutnya.

### 20 trade terakhir (#136–#155)
- 20 trade; 6 TP / 14 SL.
- Win rate 30.00%; net -6.50R; PF 0.5357; expectancy -0.325R/trade.
- 19/20 adalah SHORT. SHORT pada subset ini: 5 TP / 14 SL, win rate 26.32%, net -7.75R, PF 0.4464.
- Satu LONG (#143 1000PEPE) menang +1.25R.
- Loss streak terbaru: 6 trade (#150–#155).
- 10 trade terakhir: 2 menang / 8 kalah, net -5.50R, PF 0.3125.

Interpretasi yang didukung data: penurunan terbaru berkonsentrasi pada SHORT. Jangan simpulkan dari korelasi ini saja bahwa satu filter tertentu akan memperbaiki hasil. Catatan BTC context tidak cukup untuk menyimpulkan filter BTC akan memperbaiki hasil: beberapa loss terjadi ketika `btc_allowed_for_side=True`, sementara sejumlah trade juga tercatat ketika False. BTC mode pada cohort tercatat `shadow`, sehingga bukan gate eksekusi.

### Pola loss historis #1–#100
`v15_loss_opportunity_audit_100.json` mencatat:
- 20 loss bertanda overextended.
- 26 loss bertanda weak volume.
- 21 loss bertanda high score.
- 8 failure <60 menit.
- 39/49 loss tidak tertangkap oleh shield yang ada.
- Data tersebut sendiri memperingatkan bahwa MAE/MFE dari candle mentah diperlukan; jurnal saja tidak dapat membuktikan loss dapat dihindari atau diubah menjadi win.

Tindakan analitis: prioritaskan replay candle untuk SHORT #136–#155 dan seluruh loss #101–#155, lalu bandingkan timing entry, adverse excursion, target/stop touch, dan regime. Jangan mengubah core hanya berdasarkan label jurnal.

## Prioritas 3 — Evaluasi kandidat tanpa mengubah core

Kandidat tersimpan: `V15_PRECISION_CANDIDATE_V1`, filter:
1. fresh pullback: EMA20 touch-zone pada salah satu dari dua candle 15m lengkap sebelumnya;
2. reject jika percentile ADX 4H >= 0.90 dan delta ADX < 0;
3. volume ratio >= 1.20;
4. jarak EMA20 <= 0.80 ATR.

### Hasil historis yang tercatat
| Varian pada cohort lama 100 trade | Trades | WR | Net R | PF | Max DD |
|---|---:|---:|---:|---:|---:|
| Baseline | 100 | 51.00% | +14.75R | 1.301 | -13.50R |
| A: Fresh pullback | 99 | 51.52% | +15.75R | 1.328 | -13.50R |
| B: A + ADX deterioration | 96 | 52.08% | +16.50R | 1.359 | -13.50R |
| C: Fresh + volume >=1.20 | 52 | 55.77% | +13.25R | 1.576 | -5.75R |
| D: Fresh + EMA distance <=0.80 ATR | 63 | 55.56% | +15.75R | 1.563 | -8.50R |
| E: kombinasi A+B+C+D | 31 | 64.52% | +14.00R | 2.273 | -4.00R |

**Status: bukan bukti OOS.** Varian E menghapus 69 dari 100 trade: 38 loss dan 31 winner. Ini mengurangi frekuensi dan membuang banyak winner. Perhitungan historis juga tidak memodelkan penggantian slot portfolio, MAE/MFE intrabar, atau replay sinyal penuh. Maka angka E tidak boleh dipakai sebagai estimasi hasil live.

File `v15_1_shadow_e_signals.csv` memuat 57 baris / 56 key unik dan satu duplikat; file ini tidak menyediakan catatan eksplisit untuk semua sinyal yang ditolak. Semua 55 trade forward cohort dapat dicocokkan dengan baris `e_pass=True`, sehingga file ini tidak membuktikan candidate pass-rate pada keseluruhan peluang sinyal dan tidak dapat dipakai menghitung hasil counterfactual E pada cohort #101–#155. Perlu logging eksplisit PASS dan REJECT pada setiap kandidat sebelum evaluasi shadow dapat dipercaya.

Keputusan: jangan mengaktifkan E; jangan mengubah parameter core. Jalankan kandidat hanya sebagai shadow setelah logger diperbaiki, sambil baseline V15 tetap menjadi kontrol.

## Prioritas 4 — Validasi out-of-sample

### Status
BELUM DAPAT DINYATAKAN LULUS. Data yang tersedia belum cukup untuk full-event portfolio replay yang dapat diaudit:
- master journal tidak menyimpan OHLCV candle mentah atau semua input boolean untuk merekonstruksi `signal_mask()`;
- 155/155 trade kehilangan strategy fingerprint, execution parity, dan failure-shield diagnostics;
- shadow-E log tidak merekam sinyal REJECT secara lengkap;
- historical replay report `v15_forensic_replay_100.json` menyatakan parity signal penuh tidak lengkap dan cohort lama tidak boleh direlabel sebagai hasil executable fingerprint saat ini.

### Protokol OOS yang harus dipenuhi
1. Bekukan kode dan konfigurasi baseline dan candidate; simpan SHA/fingerprint.
2. Persist seluruh peluang sinyal per scan: timestamp UTC, symbol, side, semua input/gate, PASS/REJECT dan reason, entry/SL/TP, sizing, slot portfolio, serta fingerprint.
3. Rekam OHLCV yang digunakan (atau cache deterministik) untuk semua timeframe, termasuk candle HTF hanya setelah close.
4. Lakukan full-event replay kronologis dengan max-active/slot replacement, satu posisi per identitas yang didefinisikan, biaya, slippage, dan urutan intrabar konservatif.
5. Bandingkan baseline dan candidate pada sinyal dan slot yang sama; jika slot kosong karena veto, replay sinyal eligible berikutnya sesuai urutan waktu. Jangan hanya menghapus trade dari jurnal.
6. Bekukan parameter selama 100 trade tertutup OOS; WAITLIST tidak dihitung, posisi open tidak dihitung sebagai closed.
7. Laporkan net R, expectancy, PF, max drawdown, win rate, trade count, loss streak, side/core breakdown, fee sensitivity, dan confidence interval bootstrap. Tidak ada tuning di tengah cohort.
8. Lulus hanya jika candidate memperbaiki expectancy/PF pada OOS dan tidak merusak drawdown melebihi batas yang ditentukan sebelumnya.

Cohort #101–#200 saat ini adalah forward-test V15_PRECISION_V2_FINAL. Jangan mengganti core di tengah cohort. Candidate bisa dicatat sebagai shadow paralel, tetapi tidak boleh menggantikan baseline sampai protokol validasi terpisah selesai.

## Prioritas 5 — Keputusan core

### Keputusan saat ini
- **Pertahankan V15_PRECISION_V2_FINAL sebagai baseline paper control sampai cohort #101–#200 selesai**, kecuali ada bug eksekusi/integritas yang memerlukan penghentian keselamatan.
- **Jangan promosikan V15_PRECISION_CANDIDATE_V1/E ke produksi.** Hasil +14R / PF 2.273 adalah counterfactual historis yang belum tervalidasi.
- **Jangan nonaktifkan SHORT permanen berdasarkan 24 trade saja.** Jadikan SHORT kandidat investigasi pertama di shadow; hasil cohort terbaru menunjukkan -3.75R dan subset 20 trade terakhir didominasi SHORT, tetapi portfolio replay wajib menghitung efek slot dan peluang berikutnya.
- Prioritas engineering berikutnya bukan indikator baru: benahi audit logging (fingerprint, semua gate, rejected signals, OHLCV snapshot), kemudian jalankan replay OOS.
- Tidak ada klaim pendapatan bulanan yang dapat dibuat dari statistik ini. Cohort baru memiliki +3.50R pada 55 trade tetapi recent 20 -6.50R, jadi edge belum stabil.

## Perubahan pada branch audit
- Dokumen ini menambahkan hasil rekonsiliasi dan keputusan audit.
- `SAM_EDGE_V15_MASTER_JOURNAL.md` akan diperbarui sebagai ringkasan yang menunjuk sumber CSV/JSON yang otoritatif.
- Tidak ada perubahan pada `core_engine_v15.py`, `main_paper_v15.py`, scanner, sizing, entry/SL/TP, atau konfigurasi produksi.
