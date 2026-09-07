# Changelog

Semua perubahan signifikan pada Xenopus didokumentasikan di file ini.
Format mengikuti [Keep a Changelog](https://keepachangelog.com/id/1.1.0/),
dan project ini menggunakan [Semantic Versioning](https://semver.org).

Rilis publik pertama Xenopus akan berversi **1.0.0**. Selama pengembangan,
versi internal berbentuk `1.0.0.devN` (development snapshot, tanpa tag/rilis publik).

## [Belum Dirilis — 1.0.0.dev0]

### Added
- Fondasi repository: struktur paket `xenopus` (runtime, gateway, tools, memory,
  skills, provider, observability) dengan batas kepemilikan modul yang jelas.
- Sumber versi tunggal di `pyproject.toml` (kebijakan ADR-002).
- Konfigurasi runtime lokal-first (`~/.xenopus/`, dapat dipindahkan via `XENOPUS_HOME`).
- Bootstrap runtime: pembuatan direktori home yang idempoten + diagnostik.
- CLI dasar: `xenopus --version` dan `xenopus doctor`.
- Test harness dasar: import paket, ketersediaan versi, konfigurasi, bootstrap.
- Toolchain pengembangan: pytest, ruff, mypy (strict), hatchling (ADR-002).
- CI GitHub Actions: lint, format, typecheck, test, build wheel,
  dan guard paritas tag-rilis (Protocol v9 Section 17.9).
- Artefak governance: `.plans/`, ADR-001/002/018, WORKLOG.
