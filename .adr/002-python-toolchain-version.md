# ADR-002: Python 3.13.3, Toolchain, dan Sumber Versi Tunggal

## Status
Accepted

## Context
Master prompt menetapkan Python 3.13.3 sebagai baseline runtime dan melarang
menambah runtime bahasa lain untuk core. Tidak ada dependency runtime yang
dibutuhkan untuk fondasi (prinsip stdlib-first). Versioning harus tunggal —
tidak boleh ada dua manifest yang saling bertentangan (Gate 8 Protocol v9).

## Decision
1. `requires-python = ">=3.13,<3.14"` — baseline terkunci ke 3.13.x.
2. **Sumber versi tunggal: `pyproject.toml`** (`version = "1.0.0.dev0"`).
   Modul Python membaca via `importlib.metadata`; tidak ada duplikat konstanta.
3. Toolchain dev (dipasang di venv, bukan global): pytest 9.1.1, ruff 0.16.6,
   mypy 2.3.1 (strict), build backend hatchling. Semua MIT, terverifikasi
   kompatibel Python 3.13 via PyPI metadata pada 2026-09-07.
4. Kebijakan versi dua-tier: internal `1.0.0.devN` tanpa tag/rilis publik;
   rilis publik pertama = `1.0.0` + tag `v1.0.0` + GitHub Release (Phase 19).
   SemVer tanpa rollover pada angka 10.
5. Textual (8.2.8, MIT) dipilih untuk TUI (ADR-018) tetapi TIDAK dipasang
   pada Phase 1 — instalasi ditunda ke fase TUI (Phase 10).

## Reversal Criteria
Bila dependency runtime muncul yang tidak kompatibel dengan 3.13 tanpa
alternatif wajar, keputusan dependency yang di-eskalasi — BUKAN versi Python
(master prompt Section 00). Penggantian build backend boleh bila hatchling
terbukti menghambat (dengan bukti).

## Sunset Review
Saat Python 3.13.3 berhenti menerima security fix (evaluasi EOL), atau saat
 rilis publik pertama.

## Consequences
### Positive
- Reproducible build, satu sumber versi, nol dependency runtime Phase 1.
### Negative
- Fitur yang butuh library eksternal harus melewati matriks persetujuan
  dependensi (Protocol v9 Section 9) setiap kali.
### Neutral
- `pip install -e` diperlukan agar `importlib.metadata` menemukan versi.

## Alternatives Considered
- setuptools — ditolak: hatchling lebih deklaratif, tanpa setup.py boilerplate.
- poetry — ditolak: dependency-groups PEP 735 sudah tercakup pip; toolchain
  tambahan tidak memberi nilai pada Phase 1.
- pyproject.toml + file VERSION terpisah — ditolak: dua sumber kebenaran.

## References
- Master prompt Section 00, 11, 12, 154-156
- Protocol v9 Section 5 Gate 8
