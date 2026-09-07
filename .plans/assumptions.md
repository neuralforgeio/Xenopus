# Assumption Ledger — Xenopus

| # | Assumption | Tipe | Risk | Evidence | Confidence | Validasi | Status |
|---|---|---|---|---|---|---|---|
| 1 | pip 25.0.1 mendukung `--group dev` (PEP 735) | [A] | Medium | pip venv 25.0.1 | Sedang | install aktual | TERVERIFIKASI [E] sesi ini |
| 2 | Hatchling resolve `src/xenopus` wheel | [A] | Low | dok. hatchling | Sedang | `python -m build --wheel` | TERVERIFIKASI [E] sesi ini |
| 3 | ruff+mypy strict lulus pada scaffold | [I] | Low | pengalaman toolchain | Tinggi | jalankan gates | TERVERIFIKASI [E] |
| 4 | Textual 8.2.8 kompatibel Python 3.13 | [E] | — | PyPI metadata | Tinggi | install ulang Phase 10 | TERBUKA (non-blocking) |
| 5 | Telegram long-polling cukup local-first | [A] | Medium | desain P2 | Sedang | riset API Phase 12 | TERBUKA |
| 6 | MAX_CONCURRENT_AGENTS=4 cocok hardware | [I] | Medium | i5-8350U/8GB | Sedang | benchmark Phase 7 | TERBUKA |
| 7 | SQLite WAL cukup untuk journal | [I] | Medium | desain | Sedang | load test Phase 6 | TERBUKA |
| 8 | Webhook inbound butuh tunnel/relay | [A] | High | tension S Rev.2 | Rendah | keputusan user Phase 14 | TERBUKA |
| 9 | lib Telegram/Discord kompatibel 3.13 | [A] | Medium | — | Rendah | cek registry Phase 12/13 | TERBUKA |
