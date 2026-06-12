"""Unit tests for the eval infrastructure.

Tests cover:
- GoldenItemV2 schema validation (golden_v1.jsonl)
- build_golden.py logic (stub chunks, reproducibility)
- eval/metrics.py evaluate() with canned answers
- eval/ragas/run_ragas.py dry-run wiring (2-item, no API key)

All tests pass with no ANTHROPIC_API_KEY set.
The Chroma index and PrivacyQA files are not required.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

GOLDEN_PATH = Path("eval/golden/golden_v1.jsonl")
FIXTURES = Path("tests/fixtures/chunks_sample.jsonl")

REQUIRED_V2_KEYS = {
    "id", "query", "policy_id",
    "expected_answerable", "gold_chunk_ids", "reference_answer",
}


# ---------------------------------------------------------------------------
# golden_v1.jsonl validation
# ---------------------------------------------------------------------------

class TestGoldenV1:
    def _load(self) -> list[dict]:
        if not GOLDEN_PATH.exists():
            pytest.skip("golden_v1.jsonl not built yet")
        return [json.loads(line) for line in GOLDEN_PATH.read_text().splitlines() if line.strip()]

    def test_file_exists(self):
        assert GOLDEN_PATH.exists(), "golden_v1.jsonl missing — run build_golden.py"

    def test_item_count_in_range(self):
        items = self._load()
        assert 150 <= len(items) <= 200, f"Expected 150–200 items, got {len(items)}"

    def test_schema_all_items(self):
        items = self._load()
        for i, item in enumerate(items):
            missing = REQUIRED_V2_KEYS - set(item.keys())
            assert not missing, f"Item {i} ({item.get('id')}) missing keys: {missing}"

    def test_ids_stable_format(self):
        items = self._load()
        for item in items:
            assert item["id"].startswith("gv1-"), f"Bad id format: {item['id']}"

    def test_ids_unique(self):
        items = self._load()
        ids = [i["id"] for i in items]
        assert len(ids) == len(set(ids)), "Duplicate IDs in golden set"

    def test_unanswerable_pct_at_least_15(self):
        items = self._load()
        n_unans = sum(1 for i in items if not i["expected_answerable"])
        pct = n_unans / len(items) * 100
        assert pct >= 15.0, f"Unanswerable % = {pct:.1f}% < 15% target"

    def test_answerable_items_have_reference_answer(self):
        items = self._load()
        for item in items:
            if item["expected_answerable"]:
                assert item["reference_answer"], (
                    f"Answerable item {item['id']} has empty reference_answer"
                )

    def test_unanswerable_items_empty_gold_and_ref(self):
        items = self._load()
        for item in items:
            if not item["expected_answerable"]:
                assert item["gold_chunk_ids"] == [], (
                    f"Unanswerable item {item['id']} has non-empty gold_chunk_ids"
                )
                assert item["reference_answer"] == "", (
                    f"Unanswerable item {item['id']} has non-empty reference_answer"
                )

    def test_answerable_items_have_gold_chunks(self):
        items = self._load()
        for item in items:
            if item["expected_answerable"]:
                assert len(item["gold_chunk_ids"]) >= 1, (
                    f"Answerable item {item['id']} has empty gold_chunk_ids"
                )

    def test_policy_ids_are_strings(self):
        items = self._load()
        for item in items:
            assert isinstance(item["policy_id"], str) and item["policy_id"], (
                f"Item {item['id']} has invalid policy_id: {item['policy_id']!r}"
            )


# ---------------------------------------------------------------------------
# build_golden.py logic (stub chunks, no real corpus needed)
# ---------------------------------------------------------------------------

class TestBuildGolden:
    def _make_stub_chunks(self, tmpdir: Path) -> Path:
        """Create a minimal chunks.jsonl with known policy_ids and sections."""
        chunks = [
            {
                "chunk_id": "1034_aol_com::first_party_collection_use::c000",
                "policy_id": "1034_aol_com",
                "policy_name": "aol.com",
                "section": "First Party Collection/Use",
                "text": "AOL collects your name, email address, and browsing history.",
                "char_start": 0, "char_end": 60, "source_url": None,
            },
            {
                "chunk_id": "1034_aol_com::do_not_track::c000",
                "policy_id": "1034_aol_com",
                "policy_name": "aol.com",
                "section": "Do Not Track",
                "text": "AOL does not respond to Do Not Track signals.",
                "char_start": 61, "char_end": 110, "source_url": None,
            },
            {
                "chunk_id": "1028_redorbit_com::data_security::c000",
                "policy_id": "1028_redorbit_com",
                "policy_name": "redorbit.com",
                "section": "Data Security",
                "text": "We use SSL encryption to protect sensitive information.",
                "char_start": 0, "char_end": 55, "source_url": None,
            },
            {
                "chunk_id": "105_amazon_com::third_party_sharing_collection::c000",
                "policy_id": "105_amazon_com",
                "policy_name": "amazon.com",
                "section": "Third Party Sharing/Collection",
                "text": "Amazon shares data with third-party sellers on its marketplace.",
                "char_start": 0, "char_end": 63, "source_url": None,
            },
            # Add more policies needed by the unanswerable and curated lists
            {
                "chunk_id": "135_instagram_com::first_party_collection_use::c000",
                "policy_id": "135_instagram_com",
                "policy_name": "instagram.com",
                "section": "First Party Collection/Use",
                "text": "Instagram collects username, email and profile photos.",
                "char_start": 0, "char_end": 55, "source_url": None,
            },
            {
                "chunk_id": "1300_bankofamerica_com::first_party_collection_use::c000",
                "policy_id": "1300_bankofamerica_com",
                "policy_name": "bankofamerica.com",
                "section": "First Party Collection/Use",
                "text": "Bank of America collects personal information including name and address.",
                "char_start": 0, "char_end": 72, "source_url": None,
            },
            {
                "chunk_id": "1050_honda_com::first_party_collection_use::c000",
                "policy_id": "1050_honda_com",
                "policy_name": "honda.com",
                "section": "First Party Collection/Use",
                "text": "Honda collects personally identifiable information such as name and email.",  # noqa: E501
                "char_start": 0, "char_end": 73, "source_url": None,
            },
            {
                "chunk_id": "1259_fool_com::data_retention::c000",
                "policy_id": "1259_fool_com",
                "policy_name": "fool.com",
                "section": "Data Retention",
                "text": "We retain your data as long as needed for the purposes described.",
                "char_start": 0, "char_end": 65, "source_url": None,
            },
            {
                "chunk_id": "1468_rockstargames_com::third_party_sharing_collection::c000",
                "policy_id": "1468_rockstargames_com",
                "policy_name": "rockstargames.com",
                "section": "Third Party Sharing/Collection",
                "text": "We may share data in connection with a merger or acquisition.",
                "char_start": 0, "char_end": 61, "source_url": None,
            },
            {
                "chunk_id": "1017_sci_news_com::other::c000",
                "policy_id": "1017_sci_news_com",
                "policy_name": "sci-news.com",
                "section": "Other",
                "text": "Privacy Policy Sci-News.com is committed to protecting your privacy.",
                "char_start": 0, "char_end": 68, "source_url": None,
            },
        ]
        chunks_file = tmpdir / "stub_chunks.jsonl"
        with open(chunks_file, "w") as f:
            for c in chunks:
                f.write(json.dumps(c) + "\n")
        return chunks_file

    def test_build_returns_items(self, tmp_path: Path):
        chunks_path = self._make_stub_chunks(tmp_path)
        output = tmp_path / "test_golden.jsonl"

        # Import build function
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "build_golden",
            "eval/golden/build_golden.py",
        )
        assert spec is not None
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        items = mod.build(chunks_path=chunks_path, output_path=output)
        assert len(items) > 0

    def test_build_output_file_written(self, tmp_path: Path):
        chunks_path = self._make_stub_chunks(tmp_path)
        output = tmp_path / "test_golden.jsonl"

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "build_golden",
            "eval/golden/build_golden.py",
        )
        assert spec is not None
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        mod.build(chunks_path=chunks_path, output_path=output)
        assert output.exists()
        lines = [ln for ln in output.read_text().splitlines() if ln.strip()]
        assert len(lines) > 0

    def test_build_ids_are_stable(self, tmp_path: Path):
        """Two runs with same seed produce identical output."""
        chunks_path = self._make_stub_chunks(tmp_path)
        out1 = tmp_path / "run1.jsonl"
        out2 = tmp_path / "run2.jsonl"

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "build_golden",
            "eval/golden/build_golden.py",
        )
        assert spec is not None
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        items1 = mod.build(chunks_path=chunks_path, output_path=out1, seed=42)
        items2 = mod.build(chunks_path=chunks_path, output_path=out2, seed=42)
        ids1 = [i["id"] for i in items1]
        ids2 = [i["id"] for i in items2]
        assert ids1 == ids2, "Build is not deterministic"

    def test_schema_compliance(self, tmp_path: Path):
        chunks_path = self._make_stub_chunks(tmp_path)
        output = tmp_path / "test_golden.jsonl"

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "build_golden",
            "eval/golden/build_golden.py",
        )
        assert spec is not None
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        items = mod.build(chunks_path=chunks_path, output_path=output, seed=42)
        for item in items:
            missing = REQUIRED_V2_KEYS - set(item.keys())
            assert not missing, f"Item missing keys: {missing}"

    def test_unanswerable_items_have_valid_policy_ids(self, tmp_path: Path):
        chunks_path = self._make_stub_chunks(tmp_path)
        output = tmp_path / "test_golden.jsonl"

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "build_golden",
            "eval/golden/build_golden.py",
        )
        assert spec is not None
        assert spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        items = mod.build(chunks_path=chunks_path, output_path=output, seed=42)
        # All items must have a non-empty policy_id
        for item in items:
            assert item["policy_id"], f"Empty policy_id in item {item['id']}"


# ---------------------------------------------------------------------------
# eval/metrics.py — evaluate()
# ---------------------------------------------------------------------------

class TestEvaluateMetrics:
    def _make_golden_items(self) -> list:
        from eval.golden import GoldenItem
        return [
            GoldenItem(
                query="do you sell data?",
                policy_id="1034_aol_com",
                expected_answerable=True,
                gold_chunk_ids=["1034_aol_com::third_party_sharing_collection::c000"],
            ),
            GoldenItem(
                query="do you have biometric data?",
                policy_id="1034_aol_com",
                expected_answerable=False,
                gold_chunk_ids=[],
            ),
            GoldenItem(
                query="how is data protected?",
                policy_id="1028_redorbit_com",
                expected_answerable=True,
                gold_chunk_ids=["1028_redorbit_com::data_security::c000"],
            ),
        ]

    def _make_answers(self) -> list:
        from src.policylens.generate import Answer, Citation
        return [
            # Correct: answerable, cites gold chunk
            Answer(
                answerable=True,
                text="AOL does not sell personal data [1].",
                citations=[Citation(
                    chunk_id="1034_aol_com::third_party_sharing_collection::c000",
                    section="Third Party Sharing/Collection",
                    quote="does not sell personal data",
                )],
                policy_id="1034_aol_com",
                model="test",
            ),
            # Correct: unanswerable, abstains
            Answer(
                answerable=False,
                text="The policy doesn't address this question.",
                citations=[],
                policy_id="1034_aol_com",
                model="test",
            ),
            # Correct: answerable, cites gold chunk
            Answer(
                answerable=True,
                text="Data is protected using SSL encryption [1].",
                citations=[Citation(
                    chunk_id="1028_redorbit_com::data_security::c000",
                    section="Data Security",
                    quote="SSL encryption to protect sensitive information",
                )],
                policy_id="1028_redorbit_com",
                model="test",
            ),
        ]

    def test_perfect_score(self):
        from eval.metrics import evaluate
        golden = self._make_golden_items()
        answers = self._make_answers()
        result = evaluate(golden, answers)

        assert result["n_total"] == 3
        assert result["n_answerable"] == 2
        assert result["n_unanswerable"] == 1
        assert result["abstention_accuracy"] == 1.0
        assert result["answerable_accuracy"] == 1.0
        assert result["citation_recall"] == 1.0
        assert result["citation_precision"] == 1.0

    def test_wrong_abstention(self):
        from eval.golden import GoldenItem
        from eval.metrics import evaluate
        from src.policylens.generate import Answer, Citation

        golden = [
            GoldenItem(
                query="biometric data?",
                policy_id="1034_aol_com",
                expected_answerable=False,
                gold_chunk_ids=[],
            ),
        ]
        # Pipeline incorrectly answered (did not abstain)
        answers = [
            Answer(
                answerable=True,
                text="The policy mentions biometric data [1].",
                citations=[Citation(
                    chunk_id="1034_aol_com::other::c000",
                    section="Other",
                    quote="biometric data",
                )],
                policy_id="1034_aol_com",
                model="test",
            ),
        ]
        result = evaluate(golden, answers)
        assert result["abstention_accuracy"] == 0.0

    def test_wrong_answer_on_answerable(self):
        from eval.golden import GoldenItem
        from eval.metrics import evaluate
        from src.policylens.generate import Answer

        golden = [
            GoldenItem(
                query="do you sell data?",
                policy_id="1034_aol_com",
                expected_answerable=True,
                gold_chunk_ids=["1034_aol_com::third_party_sharing_collection::c000"],
            ),
        ]
        # Pipeline incorrectly abstained
        answers = [
            Answer(
                answerable=False,
                text="The policy doesn't address this question.",
                citations=[],
                policy_id="1034_aol_com",
                model="test",
            ),
        ]
        result = evaluate(golden, answers)
        assert result["answerable_accuracy"] == 0.0
        # Abstained on answerable → citation recall should be 0
        assert result["citation_recall"] == 0.0

    def test_partial_citation_recall(self):
        from eval.golden import GoldenItem
        from eval.metrics import evaluate
        from src.policylens.generate import Answer, Citation

        golden = [
            GoldenItem(
                query="what data do you collect?",
                policy_id="1034_aol_com",
                expected_answerable=True,
                gold_chunk_ids=[
                    "1034_aol_com::first_party_collection_use::c000",
                    "1034_aol_com::first_party_collection_use::c001",
                ],
            ),
        ]
        # Only cites one of the two gold chunks
        answers = [
            Answer(
                answerable=True,
                text="AOL collects name and email [1].",
                citations=[Citation(
                    chunk_id="1034_aol_com::first_party_collection_use::c000",
                    section="First Party Collection/Use",
                    quote="collects name and email",
                )],
                policy_id="1034_aol_com",
                model="test",
            ),
        ]
        result = evaluate(golden, answers)
        assert result["citation_recall"] == 0.5
        assert result["citation_precision"] == 1.0

    def test_wrong_citation_precision(self):
        from eval.golden import GoldenItem
        from eval.metrics import evaluate
        from src.policylens.generate import Answer, Citation

        golden = [
            GoldenItem(
                query="data security?",
                policy_id="1028_redorbit_com",
                expected_answerable=True,
                gold_chunk_ids=["1028_redorbit_com::data_security::c000"],
            ),
        ]
        # Cites a chunk not in gold set
        answers = [
            Answer(
                answerable=True,
                text="Data is protected [1][2].",
                citations=[
                    Citation(
                        chunk_id="1028_redorbit_com::data_security::c000",
                        section="Data Security",
                        quote="protected",
                    ),
                    Citation(
                        chunk_id="1028_redorbit_com::other::c000",  # NOT in gold
                        section="Other",
                        quote="other info",
                    ),
                ],
                policy_id="1028_redorbit_com",
                model="test",
            ),
        ]
        result = evaluate(golden, answers)
        assert result["citation_recall"] == 1.0
        assert result["citation_precision"] == 0.5

    def test_length_mismatch_raises(self):
        from eval.golden import GoldenItem
        from eval.metrics import evaluate

        golden = [
            GoldenItem(
                query="q1", policy_id="p1",
                expected_answerable=True, gold_chunk_ids=[],
            )
        ]
        from src.policylens.generate import Answer
        answers: list[Answer] = []  # wrong length
        with pytest.raises(ValueError, match="equal length"):
            evaluate(golden, answers)

    def test_empty_gold_ids_skipped_from_citation_metrics(self):
        from eval.golden import GoldenItem
        from eval.metrics import evaluate
        from src.policylens.generate import Answer, Citation

        golden = [
            GoldenItem(
                query="q1", policy_id="p1",
                expected_answerable=True,
                gold_chunk_ids=[],  # no ground truth
            ),
        ]
        answers = [
            Answer(
                answerable=True,
                text="Some answer [1].",
                citations=[Citation(
                    chunk_id="p1::sec::c000", section="Sec", quote="x"
                )],
                policy_id="p1",
                model="test",
            )
        ]
        result = evaluate(golden, answers)
        # No citation scores computed → defaults to 0.0
        assert result["citation_recall"] == 0.0
        assert result["citation_precision"] == 0.0
        assert result["answerable_accuracy"] == 1.0


# ---------------------------------------------------------------------------
# Ragas harness — dry-run 2-item wiring test
# ---------------------------------------------------------------------------

class TestRagasHarnessDryRun:
    """Verify 2-item dry-run wiring without API key or Chroma index."""

    def test_load_golden_returns_items(self):
        if not GOLDEN_PATH.exists():
            pytest.skip("golden_v1.jsonl not built yet")
        from eval.ragas.run_ragas import load_golden
        items = load_golden(str(GOLDEN_PATH))
        assert len(items) >= 2

    def test_run_pipeline_dry_run(self):
        """run_pipeline with _DRY_RUN=1 returns records without API calls."""
        import eval.ragas.run_ragas as harness_module  # type: ignore[import]
        # Temporarily set dry-run flag
        original = harness_module._DRY_RUN
        harness_module._DRY_RUN = True
        try:
            from eval.ragas.run_ragas import load_golden, run_pipeline
            from src.policylens.config import Config

            if not GOLDEN_PATH.exists():
                pytest.skip("golden_v1.jsonl not built yet")

            items = load_golden(str(GOLDEN_PATH))[:2]
            retriever = harness_module._StubRetriever(
                chunks_path=str(FIXTURES) if FIXTURES.exists() else None
            )
            cfg = Config()

            records, raw_answers = run_pipeline(items, retriever, cfg)

            assert len(records) == 2
            assert len(raw_answers) == 2

            for r in records:
                assert "question" in r
                assert "answer" in r
                assert "contexts" in r
                assert "ground_truth" in r
        finally:
            harness_module._DRY_RUN = original

    def test_house_metrics_on_dry_run_output(self):
        """run_house_metrics doesn't crash on canned answers."""
        import eval.ragas.run_ragas as harness_module  # type: ignore[import]
        original = harness_module._DRY_RUN
        harness_module._DRY_RUN = True
        try:
            from eval.ragas.run_ragas import load_golden, run_house_metrics, run_pipeline
            from src.policylens.config import Config

            if not GOLDEN_PATH.exists():
                pytest.skip("golden_v1.jsonl not built yet")

            items = load_golden(str(GOLDEN_PATH))[:2]
            retriever = harness_module._StubRetriever()
            cfg = Config()

            _records, raw_answers = run_pipeline(items, retriever, cfg)
            scores = run_house_metrics(items, raw_answers)

            assert "n_total" in scores or "house_metrics_error" not in scores
        finally:
            harness_module._DRY_RUN = original

    def test_check_thresholds_passes_with_good_scores(self):
        from eval.ragas.run_ragas import check_thresholds
        ragas = {"faithfulness": 0.90, "answer_relevancy": 0.85}
        house = {"abstention_accuracy": 0.95}
        passed, failures = check_thresholds(ragas, house)
        assert passed
        assert failures == []

    def test_check_thresholds_fails_low_faithfulness(self):
        from eval.ragas.run_ragas import check_thresholds
        ragas = {"faithfulness": 0.50}
        house = {"abstention_accuracy": 0.95}
        passed, failures = check_thresholds(ragas, house)
        assert not passed
        assert any("faithfulness" in f for f in failures)

    def test_check_thresholds_fails_low_abstention(self):
        from eval.ragas.run_ragas import check_thresholds
        ragas = {"faithfulness": 0.90}
        house = {"abstention_accuracy": 0.70}
        passed, failures = check_thresholds(ragas, house)
        assert not passed
        assert any("abstention_accuracy" in f for f in failures)

    def test_main_dry_run_exit_0(self, tmp_path: Path):
        """main() with --dry-run --max-items 2 exits 0 (gate passes for NaN scores)."""
        if not GOLDEN_PATH.exists():
            pytest.skip("golden_v1.jsonl not built yet")

        from eval.ragas.run_ragas import main
        output = str(tmp_path / "report.json")
        rc = main([
            "--golden", str(GOLDEN_PATH),
            "--output", output,
            "--max-items", "2",
            "--dry-run",
        ])
        assert Path(output).exists()
        report = json.loads(Path(output).read_text())
        assert report["n_items"] == 2
        assert "ragas" in report
        # Gate should pass since NaN doesn't fail (it's a dry run)
        assert rc == 0
