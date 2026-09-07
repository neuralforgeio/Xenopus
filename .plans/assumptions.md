# Assumption Ledger — Xenopus

| # | Assumption | Type | Risk | Evidence | Confidence | Validation | Status |
|---|---|---|---|---|---|---|---|
| 1 | pip supports `--group dev` (PEP 735) | [A] | Medium | venv pip 25.0.1 | Medium | actual install | VERIFIED [E] this session |
| 2 | Hatchling resolves the `src/xenopus` wheel | [A] | Low | hatchling docs | Medium | `python -m build --wheel` | VERIFIED [E] this session |
| 3 | ruff+mypy strict pass on scaffold | [I] | Low | toolchain experience | High | run gates | VERIFIED [E] |
| 4 | Textual 8.2.8 compatible with Python 3.13 | [E] | — | PyPI metadata | High | reinstall in Phase 10 | OPEN (non-blocking) |
| 5 | Telegram long-polling suffices local-first | [A] | Medium | P2 design | Medium | API research in Phase 12 | OPEN |
| 6 | MAX_CONCURRENT_AGENTS=4 fits the hardware | [I] | Medium | i5-8350U/8GB | Medium | Phase 7 benchmark | OPEN |
| 7 | SQLite WAL suffices for the journal | [I] | Medium | design | Medium | Phase 6 load test | OPEN |
| 8 | Inbound webhooks need a tunnel/relay | [A] | High | Rev.2 tension S | Low | user decision in Phase 14 | OPEN |
| 9 | Telegram/Discord libs compatible with 3.13 | [A] | Medium | — | Low | registry check in Phase 12/13 | OPEN |
