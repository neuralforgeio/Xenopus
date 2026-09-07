# ADR-018: Framework TUI = Textual

## Status
Accepted

## Context
Xenopus membutuhkan TUI yang terasa sebagai aplikasi terminal sungguhan:
command palette, panel tugas, approval prompt, diff viewer, scrollback.
Dua kandidat: Textual dan prompt_toolkit (sudah ada di env global user).
Keputusan diminta sejak planning agar roadmap Phase 10 pasti.

## Decision
Textual (8.2.8, MIT, terverifikasi kompatibel Python 3.13 via PyPI 2026-09-07)
dipilih sebagai framework TUI. Instalasi hanya pada Phase 10 — bukan sekarang.
TUI mengonsumsi RuntimeDriver (ADR-001); tidak ada logika runtime di modul TUI.

## Reversal Criteria
Bila Textual terbukti memblokir rendering stream multi-agent atau performa
terminal pada mesin kelas i5-8GB (benchmark Phase 10), pindah ke
prompt_toolkit dengan menjaga kontrak view tetap identik.

## Sunset Review
Akhir Phase 10, dengan hasil benchmark TUI (master prompt 119).

## Consequences
### Positive
- Komponen siap pakai (palette, panel, tree) mempercepat Phase 10.
### Negative
- Dependency berukuran sedang masuk pada Phase 10; footprint diukur ulang.
### Neutral
- TUI tetap surface tipis; perubahan framework tidak menyentuh core.

## Alternatives Considered
- prompt_toolkit — ditolak: komponen harus dirakit manual; biaya Phase 10
  lebih tinggi dari benefit footprint yang lebih kecil.
- Rich saja (tanpa interaktivitas penuh) — ditolak: tidak ada command palette
  dan input model yang dibutuhkan (master prompt 76).

## References
- Master prompt Section 76
- Keputusan user D-03 (sesi planning 2026-09-07)
