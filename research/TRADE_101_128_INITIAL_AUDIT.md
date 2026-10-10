# SAM EDGE V15 — Audit Awal Cohort 101–128
Tanggal audit: 2026-10-10
Branch: research/premium-indicator-benchmark
Status: INITIAL DATA AUDIT — belum merupakan candle-by-candle replay.

## Ruang lingkup
Sumber: paper_v15_state_v2.json pada branch main, disaring untuk core=V15_PRECISION_V2_FINAL dan result TP/SL. Main/produksi tidak diubah.
Jumlah yang ditemukan: 28 closed trades; 16 TP, 12 SL; gross net = 16*1.25R - 12*1R = +8R. Ini belum memasukkan fee, slippage, funding, atau mark-to-market portfolio drawdown. Trade nomor 101–128 adalah label cohort analisis; jangan campur dengan baseline trade 1–100.

## Hasil audit entry yang sudah dapat diverifikasi
- Semua 12 SL yang ditemukan adalah LONG.
- Failure Shield tercatat disabled pada sinyal-sinyal yang diperiksa; sinyal tidak diveto oleh shield.
- BTC context mode tercatat shadow, sehingga tidak memblokir entry. Di antara 12 SL, BTC allowed_long=false untuk TIA, LDO, STRK; BTC allowed_long=true untuk SL lainnya. Jadi BTC alignment saja tidak menjelaskan semua kerugian.
- Skor entry tidak cukup sebagai pemilah tunggal: contoh LDO (55.85), WLD (54.87), 1000PEPE (58.78), SAND (62.28), NEAR (56.24), dan STRK (50.97) tetap SL.
- Kekuatan tren/ADX juga tidak cukup sendiri: LDO (H4 ADX percentile 0.84) dan SAND (0.79) tetap SL.
- Ini temuan deskriptif pada sampel kecil, bukan bukti kausal dan bukan dasar untuk mengaktifkan filter di produksi.

## Daftar 12 SL
| Coin | Entry UTC | Close UTC | Score | H4 ADX pct | H4 ADX delta | Vol ratio | Distance EMA20 / ATR | Room ATR |
|---|---|---|---:|---:|---:|---:|---:|---:|
| TIA | 2026-09-26 01:30 | 2026-09-26 03:10 | 37.75 | 0.715 | -1.956 | 2.704 | 0.538 | 1.549 |
| LDO | 2026-09-26 12:00 | 2026-09-26 12:40 | 55.85 | 0.840 | 5.105 | 2.487 | 0.259 | 1.543 |
| JUP | 2026-09-27 09:30 | 2026-09-27 12:50 | 42.83 | 0.840 | -0.733 | 1.828 | 0.703 | 1.640 |
| AAVE | 2026-09-27 09:30 | 2026-09-27 13:00 | 22.03 | 0.655 | 0.401 | 1.264 | 0.270 | 1.318 |
| WLD | 2026-09-30 20:15 | 2026-10-01 07:55 | 54.87 | 0.435 | 2.633 | 1.991 | 0.525 | 1.539 |
| 1000PEPE | 2026-10-02 07:45 | 2026-10-02 11:40 | 58.78 | 0.515 | 3.402 | 4.489 | 0.192 | 5.173 |
| SAND | 2026-10-03 09:45 | 2026-10-03 10:50 | 62.28 | 0.790 | 9.195 | 1.291 | 0.433 | 3.060 |
| ETH | 2026-10-04 12:15 | 2026-10-04 12:50 | 31.62 | 0.470 | 0.254 | 1.230 | 0.473 | 1.498 |
| SUI | 2026-10-05 11:45 | 2026-10-05 12:35 | 54.27 | 0.560 | 3.739 | 1.631 | 0.713 | 1.626 |
| XRP | 2026-10-05 13:15 | 2026-10-05 14:30 | 30.61 | 0.140 | 1.155 | 1.593 | 0.247 | 1.478 |
| NEAR | 2026-10-05 12:45 | 2026-10-05 15:00 | 56.24 | 0.050 | 2.624 | 1.413 | 0.278 | 4.056 |
| STRK | 2026-10-09 14:30 | 2026-10-09 20:25 | 50.97 | 0.675 | 7.157 | 1.606 | 0.718 | 1.292 |

## Yang belum boleh diklaim
MAE, MFE, urutan SL/TP intrabar, alternatif entry non-lookahead, serta perbandingan hasil setelah fee belum dihitung pada dokumen ini. Data candle historis yang tepat belum diverifikasi untuk seluruh simbol dan waktu. Jangan mengisi metrik tersebut dengan estimasi tebakan.

## Prosedur lanjutan
1. Ambil OHLCV futures historis untuk simbol dan rentang waktu tiap trade; verifikasi timestamp UTC dan cakupan candle.
2. Replay tiap trade dari candle entry sampai candle close; hitung MAE/MFE dalam R, lama trade, dan first-touch SL/TP. Jika SL dan TP tersentuh dalam candle yang sama dan tidak ada data lebih rendah timeframe yang cocok, tandai ambiguous, jangan asumsikan TP.
3. Uji perubahan entry, stop, dan exit secara terpisah; laporkan semua 28 trade termasuk TP yang bisa rusak.
4. Hanya setelah itu jalankan portfolio replay dengan posisi simultan dan biaya.

Sumber data harga rencana: Binance USDⓈ-M Futures klines sebagai proxy historis, bukan bukti identik dengan harga eksekusi BingX. Produksi tetap tidak berubah.
