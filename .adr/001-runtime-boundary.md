# ADR-001: Batas Runtime dan Boundary Modul Xenopus

## Status
Accepted

## Context
Xenopus adalah agent runtime multi-surface (CLI, TUI, Web, Desktop, Telegram,
Discord) dengan risiko arsitektur terbesar: satu modul raksasa ("god module")
yang memiliki planning, eksekusi, tools, memory, dan UI sekaligus. Protocol v9
melarang hal ini (Red Zone 4.1), dan addendum orkestrasi menegaskan bahwa
channel tidak boleh menjangkar ke core.

## Decision
Paket dibagi menjadi subpaket dengan kepemilikan eksklusif:

- `xenopus.runtime` — AgentRuntime inti (FSM, Goal, Plan, Orkestrator).
- `xenopus.gateway` — adapter channel + notification router; TIDAK PERNAH
  diimpor oleh `runtime`.
- `xenopus.tools` — Tool Gateway: discovery, permission, risk, eksekusi.
- `xenopus.memory` — memori berprovenance; promosi butuh evidence.
- `xenopus.skills` — registry skill + lifecycle 7 tahap, tanpa auto-trust.
- `xenopus.provider` — abstraksi model provider; core provider-agnostic.
- `xenopus.observability` — log terstruktur, metrik, trace, correlation ID.

Aturan dependensi satu arah: interface (cli/tui/web) → runtime → (tools,
memory, skills, provider) → observability. Gateway hanya mengonsumsi kontrak
`RuntimeDriver` yang akan didefinisikan pada Phase 2.

## Reversal Criteria
Bila terbukti ada dua subpaket yang saling mengimpor (dependensi siklik),
atau `runtime` terbukti mengimpor modul channel, keputusan ini gagal dan
boundary harus digambar ulang.

## Sunset Review
Ditinjau ulang setiap kali subpaket baru ditambahkan (paling cepat: Phase 9).

## Consequences
### Positive
- Blast radius kegagalan per-modul terbatas; testable per boundary.
- Channel outage tidak menjatuhkan core (addendum 82-84).
### Negative
- Butuh disiplin import; linter harus menjaga arah dependensi.
### Neutral
- Jumlah file bertambah, tetapi tiap file kecil dan berkepemilikan jelas.

## Alternatives Considered
- Monolit `xenopus/core.py` — ditolak: melanggar Red Zone 4.1 (god module).
- Paket per-fitur — ditolak: fitur berubah antar fase, kepemilikan stabil.

## References
- Protocol v9 Section 4.1, Section 19
- XENOPUS MASTER PROMPT Section 09, 75, 79, 113
