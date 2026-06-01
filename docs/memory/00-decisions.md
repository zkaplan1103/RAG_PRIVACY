# Decisions log — append one line per meaningful decision

Format: `YYYY-MM-DD | agent | decision`

---

2026-06-01 | orchestrator | Phase 0 scaffold complete: uv project, directory layout, .gitignore, pyproject.toml, source stubs (config, ingest, index, retrieve, generate, app.py), docs/CONTRACTS.md frozen.
2026-06-01 | orchestrator | Data sources selected: OPP-115 (usableprivacy.org, CC-NC research license), PrivacyQA (GitHub AbhilashaRavichander/PrivacyQA_EMNLP), PolicyQA (GitHub wasiahmad/PolicyQA). Raw data in data/raw/, git-ignored.
2026-06-01 | orchestrator | Embedding model: BAAI/bge-small-en-v1.5 (local, no API key needed). Generation model dev: claude-haiku-4-5; final: claude-sonnet-4-6.
2026-06-01 | orchestrator | score_floor=0.30 — below this for all top-k hits, answer() abstains.
2026-06-01 | orchestrator | OPP-115 acquired via GitHub mirror (pmayostendorp/beforeiaccept): 115 sanitized_policies HTML + 115 annotation CSVs in data/raw/opp115/. License: CC-NC, research/teaching only, cite Wilson et al. ACL 2016.
2026-06-01 | orchestrator | PrivacyQA acquired via git clone (AbhilashaRavichander/PrivacyQA_EMNLP): 185,200 train rows + 62,150 test rows in data/raw/privacyqa/data/. License: MIT.
2026-06-01 | orchestrator | torch pinned to 2.2.x, sentence-transformers to 2.7.x, onnxruntime overridden to 1.23.2 — macOS x86_64 has no wheels for newer onnxruntime/torch. Record in pyproject.toml [tool.uv] override-dependencies.
2026-06-01 | orchestrator | All 7 contract tests pass + 5/5 smoke test (stub mode) pass. Phase 0 complete. Ready for Phase 1.
2026-06-01 | index-engineer | numpy pinned to <2.0 for torch 2.2 ABI compat. Chroma score = 1 - cosine_distance. Cache via collection.count()>0.
