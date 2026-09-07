# Current Task — Phase 1: Fondasi/Scaffold Xenopus

## Objective
Membangun fondasi repository Xenopus yang bersih, reproducible, testable,
dan siap menjadi basis Agent Runtime — tanpa fitur runtime lanjutan.

## Scope
- pyproject.toml (metadata, toolchain, sumber versi tunggal)
- Struktur paket `src/xenopus` + 7 subpaket boundary (ADR-001)
- config.py (XENOPUS_HOME), bootstrap.py (mkdir idempoten), cli.py
- Test harness: import, version, config, bootstrap, CLI
- README.md, CHANGELOG.md, LICENSE (MIT), .gitignore
- .plans/, .adr/001-002-018, CI + release-parity guard
- git init, push ke github.com/neuralforgeio/Xenopus, metadata repo

## Non-Goals (dilarang tersentuh di Phase 1)
FSM agent, Goal/Plan engine, multi-agent/paralel, Telegram/Discord/webhook,
scheduler, browser, memory/skills tingkat lanjut, self-improvement/repair,
desktop, Web/TUI lanjut, Vercel deploy, rilis publik/tag.

## Baseline Snapshot
- Workspace: kosong (0 file) [E 2026-09-07]
- Remote github.com/neuralforgeio/Xenopus: exists, EMPTY, public [E]
- Test baseline: 0 test → target >0, semua PASS
- Python 3.13.3 global; venv .venv/ dibuat sesi ini [E]

## Impact Radius
Repository baru — tidak ada konsumen. Risiko dampak: nol eksternal.

## Contract Stability
CLI `xenopus --version|doctor` = kontrak publik pertama; stabil ke depan.

## Test Strategy
Unit: config (default/env/error), bootstrap (idempotensi), CLI (exit code),
import seluruh modul, version bukan "unknown".

## Rollback Strategy
`Remove-Item -Recurse -Force C:\Users\Dearly Febriano\xenopus` + remote
masih kosong; rollback < 5 menit tanpa kehilangan data (semua file baru).

## Risk Assessment
- [A] Kebijakan dependency-groups (PEP 735) di pip 25.0.1 venv — divalidasi
  saat install (fallback: requirements-dev.txt) → TERVERIFIKASI OK [E]
- [E] Semua dependency PyPI kompatibel Python 3.13 (dicek 2026-09-07)
- [I] Hatchling resolve `src/` layout — divalidasi via build wheel

## Blast Radius Hypothesis
Worst case: push gagal → remote tetap kosong; lokal dapat dihapus ulang.

## Abortion Criteria
- Secret scan menemukan kredensial → HALT
- Identity mismatch gh/git → HALT
- Test/lint/typecheck gagal > 3 attempts → HALT (S12)

## Release Strategy Preview
TIDAK ada rilis publik Phase 1. Versi `1.0.0.dev0` (internal snapshot,
tanpa tag). Rilis publik pertama 1.0.0 di Phase 19 (ADR-002).

## Knowledge Artifacts
ADR-001, ADR-002, ADR-018; README; CHANGELOG; WORKLOG; checkpoint.

## Decision Ledger (dari planning)
- D-01: git user.name repo-level = neuralforgeio
- D-02: push pertama di akhir Phase 1 (setelah gates)
- D-03: TUI = Textual (install Phase 10, bukan sekarang)
- D-04: versi dua-tier: dev `1.0.0.devN` tanpa tag; publik pertama 1.0.0
- D-05: urutan exit Phase 1 = gates → secret scan → hygiene → push
