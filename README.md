# Xenopus

<div align="center">

<img src="docs/assets/xenopus.png" alt="Logo Xenopus" width="180"/>

**Xenopus — self-improving autonomous AI agent runtime.**

Lokal-first · Verifiable · Model-agnostic · Python 3.13

</div>

---

## Apa itu Xenopus?

Xenopus adalah runtime untuk **AI agent otonom yang mampu memperbaiki
dirinya sendiri secara terverifikasi**. Agent tidak sekadar memanggil tool —
Xenopus menjalankan siklus lengkap: **Perceive → Understand → Goal → Plan →
Act → Observe → Verify → Reflect → Learn → Consolidate → Improve → Reuse**.

Setiap klaim didukung bukti. Setiap aksi destruktif membutuhkan otorisasi,
penilaian risiko, observabilitas, dan jalur pemulihan. Setiap perilaku yang
dipelajari tunduk pada governance sebelum dipromosikan.

## Visi

> Sebuah runtime agent yang aman dan dapat diverifikasi — berjalan lokal di
> mesin Anda, belajar dari pengalaman tervalidasi, dan tidak pernah
> meningkatkan dirinya tanpa evidence, evaluasi, safety, dan rollback.

## Status Development

**Pre-Alpha — Phase 1 (Fondasi).** Repository ini sedang dibangun bertahap
mengikuti roadmap yang ketat. Fitur yang tercantum di bawah "Roadmap"
**belum tersedia** dan akan hadir pada fase masing-masing.

- Runtime inti saat ini: konfigurasi lokal-first, bootstrap state, CLI dasar
  (`xenopus --version`, `xenopus doctor`), test harness.
- Versi development: `1.0.0.dev0` — rilis publik pertama akan berversi
  **1.0.0** (Semantic Versioning).

## Prasyarat

- **Python 3.13.3** (baseline resmi; tidak mendukung versi lain pada saat ini)
- Git, GitHub CLI (`gh`) — untuk kontribusi dan verifikasi rilis

## Arsitektur (Arah Desain)

```
Interfaces (CLI · TUI · Web · Desktop · Telegram · Discord)
        ↓ RuntimeDriver (kontrak stabil)
Gateway Layer (channel adapter · notification router)
        ↓
Agent Runtime (FSM · Goal · Planner · Executor · Verifier)
        ↓
Tool Gateway · Permission Engine · Risk Engine · Sandbox
        ↓
Memory · Skills · Experience · Provider/Model Router
```

Prinsip: satu core runtime, banyak surface. Channel tidak pernah masuk ke
core. Setiap modul memiliki kepemilikan eksklusif (lihat `ADR-001`).

## Scope Saat Ini (Phase 1)

- [x] Struktur paket `xenopus` dengan boundary modul yang jelas
- [x] Konfigurasi lokal-first (`~/.xenopus/`, override `XENOPUS_HOME`)
- [x] Bootstrap state idempoten + CLI `doctor`
- [x] Test harness (pytest) + toolchain (ruff, mypy strict)
- [x] CI GitHub Actions + guard paritas tag-rilis

## Roadmap Singkat

| Phase | Isi | Status |
|---|---|---|
| 1 | Fondasi repository & toolchain | 🚧 sedang berjalan |
| 2 | Agent FSM · Goal · Plan (DAG) · event journal | ⏳ belum mulai |
| 3 | Provider layer · Context · Session | ⏳ belum mulai |
| 4 | Tool Gateway · Permission · Risk · Verifier | ⏳ belum mulai |
| 5 | Memory · Skills · Checkpoint · Observability | ⏳ belum mulai |
| 6+ | Task runtime tahan-lama · orkestrasi multi-agent · TUI · Web · channel | ⏳ jauh di depan |

Rincian lengkap: lihat `.plans/` dan seri ADR di `.adr/`.

## Development

```bash
git clone https://github.com/neuralforgeio/Xenopus.git
cd Xenopus
python -m venv .venv
# Windows: .venv\Scripts\activate   |   POSIX: source .venv/bin/activate
pip install -e . --group dev
```

## Testing

```bash
pytest            # seluruh suite
ruff check .      # lint
ruff format --check .  # format
mypy              # typecheck (strict)
```

## Lisensi

MIT — lihat [LICENSE](LICENSE).

## Versi

Kebijakan: [Semantic Versioning](https://semver.org). Sumber versi tunggal:
`pyproject.toml`. Rilis publik pertama: **1.0.0**.
