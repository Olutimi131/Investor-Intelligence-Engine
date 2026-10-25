"""
Unit & Integration Tests for Investor Intelligence Engine
========================================================
Tests extraction, deduplication, conflict detection, and end-to-end processing.
"""

import json
import sys
from pathlib import Path

import pytest

# Ensure workspace root is in sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from engine import (
    ConflictResolver,
    EntityResolver,
    InvestorExtractor,
    InvestorIntelligenceEngine,
    SourceObservation,
    TicketSize,
    load_default_dataset,
)


@pytest.fixture
def extractor():
    return InvestorExtractor()


@pytest.fixture
def resolver():
    return EntityResolver()


@pytest.fixture
def conflict_resolver():
    return ConflictResolver()


@pytest.fixture
def sample_dataset():
    data_path = Path(__file__).parent.parent / "data" / "investors.json"
    with open(data_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================================
# 1. Extraction Pipeline Tests
# ============================================================================

def test_ticket_size_extraction_ranges(extractor):
    # Million dollar range
    text1 = "We write checks typically between $1.5M and $3M for high-growth tech."
    ts1 = extractor.extract_ticket_size(text1)
    assert ts1 is not None
    assert ts1.min_usd == 1_500_000.0
    assert ts1.max_usd == 3_000_000.0
    assert ts1.formatted_range == "$1.5M - $3M"

    # Full numeric range
    text2 = "Standard ticket sizes ranging from $1,500,000 to $3,000,000."
    ts2 = extractor.extract_ticket_size(text2)
    assert ts2 is not None
    assert ts2.min_usd == 1_500_000.0
    assert ts2.max_usd == 3_000_000.0

    # Euro range with conversion
    text3 = "Tickets range from €500,000 to €2,000,000 ($550k - $2.2M)."
    ts3 = extractor.extract_ticket_size(text3)
    assert ts3 is not None
    assert ts3.currency == "EUR"
    assert ts3.min_usd == pytest.approx(550_000.0, rel=1e-2)
    assert ts3.max_usd == pytest.approx(2_200_000.0, rel=1e-2)

    # Thousand dollar single/range
    text4 = "Providing angel checks around $250k - $500k to early founders."
    ts4 = extractor.extract_ticket_size(text4)
    assert ts4 is not None
    assert ts4.min_usd == 250_000.0
    assert ts4.max_usd == 500_000.0


def test_missing_ticket_size(extractor):
    text = "We partner with early-stage founders. No ticket size publicized; contact partners directly."
    ts = extractor.extract_ticket_size(text)
    assert ts is None


def test_stage_and_sector_extraction(extractor):
    text = "Early-stage fund leading Pre-Seed and Seed rounds in FinTech and B2B SaaS, expanding to ClimateTech."
    stages = extractor.extract_stages(text)
    assert "Pre-Seed" in stages
    assert "Seed" in stages
    assert "Series B" not in stages

    sectors = extractor.extract_sectors(text)
    assert "FinTech" in sectors
    assert "B2B SaaS" in sectors
    assert "ClimateTech" in sectors
    assert "Biotech" not in sectors


def test_geography_extraction(extractor):
    text = "Headquartered in San Francisco, CA, we actively deploy capital across North America and Europe."
    hq, regions = extractor.extract_geography(text)
    assert hq == "San Francisco, CA"
    assert "North America" in regions
    assert "Europe" in regions


def test_signals_extraction(extractor):
    text = "Apex Horizon closed its $250M Fund IV. Recent portfolio investments include PayStacker, CloudShield, and LedgerFlow. Managing Partner Sarah Chen leads the team."
    signals = extractor.extract_signals(text)
    assert signals.fund_size == "$250M Fund IV"
    assert "PayStacker" in signals.portfolio_companies
    assert "CloudShield" in signals.portfolio_companies
    assert "Sarah Chen" in signals.leadership


# ============================================================================
# 2. Entity Resolution & Deduplication Tests
# ============================================================================

def test_exact_domain_matching(resolver):
    obs_a = SourceObservation(
        source_id="1", source_name="Source A", raw_name="Apex Horizon",
        raw_website="https://www.apexhorizon.vc"
    )
    obs_b = SourceObservation(
        source_id="2", source_name="Source B", raw_name="Apex Capital",
        raw_website="http://apexhorizon.vc/about"
    )
    is_dup, conf, reason = resolver.are_duplicates(obs_a, obs_b)
    assert is_dup is True
    assert conf >= 0.95
    assert "domain" in reason.lower()


def test_normalized_name_deduplication(resolver):
    obs_a = SourceObservation(
        source_id="1", source_name="Source A", raw_name="Apex Horizon Ventures"
    )
    obs_b = SourceObservation(
        source_id="2", source_name="Source B", raw_name="Apex Horizon Capital LLC"
    )
    is_dup, conf, _ = resolver.are_duplicates(obs_a, obs_b)
    assert is_dup is True
    assert conf >= 0.90


def test_false_positive_protection(resolver):
    """
    Ensure 'Horizon Ventures' is NOT falsely merged with 'Apex Horizon Ventures'.
    """
    obs_a = SourceObservation(
        source_id="1", source_name="Source A", raw_name="Apex Horizon Ventures",
        raw_website="https://apexhorizon.vc"
    )
    obs_b = SourceObservation(
        source_id="2", source_name="Source B", raw_name="Horizon Ventures",
        raw_website="https://horizonventures.hk"
    )
    is_dup, _, reason = resolver.are_duplicates(obs_a, obs_b)
    assert is_dup is False, f"Expected distinct entities, but matched due to: {reason}"


# ============================================================================
# 3. Conflict Detection Tests
# ============================================================================

def test_ticket_size_conflict_detection(conflict_resolver):
    obs1 = SourceObservation(
        source_id="1", source_name="Source A", raw_name="Test VC",
        ticket_size=TicketSize.from_values(1_500_000, 3_000_000)
    )
    obs2 = SourceObservation(
        source_id="2", source_name="Source B", raw_name="Test VC",
        ticket_size=TicketSize.from_values(8_000_000, 15_000_000)
    )
    cluster = [obs1, obs2]
    canonical = conflict_resolver.analyze_and_reconcile(cluster, 1)

    assert canonical.status == "CONFLICT_DETECTED"
    ticket_conflicts = [c for c in canonical.conflicts if c.field == "ticket_size"]
    assert len(ticket_conflicts) == 1
    assert ticket_conflicts[0].severity == "HIGH"
    assert "Source A" in ticket_conflicts[0].description
    assert "Source B" in ticket_conflicts[0].description


def test_stage_divergence_conflict(conflict_resolver):
    obs1 = SourceObservation(
        source_id="1", source_name="Filing", raw_name="Growth Fund",
        stages=["Series B", "Series C"]
    )
    obs2 = SourceObservation(
        source_id="2", source_name="Directory", raw_name="Growth Fund",
        stages=["Pre-Seed", "Seed"]
    )
    canonical = conflict_resolver.analyze_and_reconcile([obs1, obs2], 1)
    stage_conflicts = [c for c in canonical.conflicts if c.field == "stages"]
    assert len(stage_conflicts) == 1
    assert stage_conflicts[0].severity == "MEDIUM"


# ============================================================================
# 4. End-to-End Pipeline on Synthetic Dataset
# ============================================================================

def test_load_default_dataset_csv_support():
    records = load_default_dataset(Path(__file__).parent.parent / "data" / "investors.csv")
    assert len(records) == 10
    assert records[0]["source_id"] == "raw-001"
    assert records[0]["metadata"]["confidence"] == pytest.approx(0.95)
    assert records[0]["raw_website"] == "https://www.apexhorizon.vc"
    assert records[6]["raw_website"] is None

    engine = InvestorIntelligenceEngine()
    entities = engine.process_records(records)
    assert len(entities) == 6


def test_end_to_end_synthetic_pipeline(sample_dataset):
    engine = InvestorIntelligenceEngine()
    entities = engine.process_records(sample_dataset)

    # 10 raw entries should cluster into 6 canonical entities
    assert len(sample_dataset) == 10
    assert len(entities) == 6

    # Verify Apex Horizon deduplication & conflicts
    apex = next(e for e in entities if "Apex Horizon" in e.canonical_name)
    assert len(apex.sources) == 3
    assert set(apex.aliases) == {"Apex Horizon Capital LLC", "Apex Horizon VC"}
    assert apex.status == "CONFLICT_DETECTED"
    conflict_fields = {c.field for c in apex.conflicts}
    assert "ticket_size" in conflict_fields
    assert "geography" in conflict_fields

    # Verify Bluecrest deduplication & stage conflict
    bluecrest = next(e for e in entities if "Bluecrest" in e.canonical_name)
    assert len(bluecrest.sources) == 2
    assert "Bluecrest Ventures" in bluecrest.aliases
    assert any(c.field == "stages" for c in bluecrest.conflicts)

    # Verify Nordic Frontier multi-source agreement without conflict
    nordic = next(e for e in entities if "Nordic Frontier" in e.canonical_name)
    assert len(nordic.sources) == 2
    assert nordic.status == "VERIFIED_MULTI_SOURCE"

    # Verify Horizon Ventures stayed distinct
    horizon = next(e for e in entities if e.canonical_name == "Horizon Ventures")
    assert len(horizon.sources) == 1

    # Verify Incomplete entity (Verdant Seed Partners)
    verdant = next(e for e in entities if "Verdant" in e.canonical_name)
    assert verdant.status == "INCOMPLETE_PROFILE"
    assert "ticket_size" in verdant.missing_fields

    # Verify Metrics
    metrics = engine.get_metrics()
    assert metrics["total_raw_records"] == 10
    assert metrics["total_canonical_entities"] == 6
    assert metrics["deduplication_rate_percent"] == 40.0


# ============================================================================
# 5. Benchmark Cases: Duplicates, Missing Data, Conflicts & Uncertain Matches
# ============================================================================

def test_demonstrate_duplicate_entities_detection(sample_dataset):
    """
    Demonstrates actual examples of duplicate entities across multiple sources:
    1. Apex Horizon (raw-001, raw-002, raw-003) -> 3 sources merged into inv-001.
    2. Bluecrest (raw-004, raw-005) -> 2 sources merged into inv-002.
    3. Nordic Frontier (raw-006, raw-007) -> 2 sources merged into inv-003.
    Also asserts that Horizon Ventures (raw-010) is NOT merged (false positive guard).
    """
    engine = InvestorIntelligenceEngine()
    entities = engine.process_records(sample_dataset)

    # 1. Apex Horizon cluster
    apex = next(e for e in entities if "Apex Horizon" in e.canonical_name)
    apex_source_ids = {s.source_id for s in apex.sources}
    assert apex_source_ids == {"raw-001", "raw-002", "raw-003"}
    assert set(apex.aliases) == {"Apex Horizon Capital LLC", "Apex Horizon VC"}

    # 2. Bluecrest cluster
    bluecrest = next(e for e in entities if "Bluecrest" in e.canonical_name)
    bluecrest_source_ids = {s.source_id for s in bluecrest.sources}
    assert bluecrest_source_ids == {"raw-004", "raw-005"}
    assert "Bluecrest Ventures" in bluecrest.aliases

    # 3. Nordic Frontier cluster
    nordic = next(e for e in entities if "Nordic Frontier" in e.canonical_name)
    nordic_source_ids = {s.source_id for s in nordic.sources}
    assert nordic_source_ids == {"raw-006", "raw-007"}
    assert "Nordic Frontier VC" in nordic.aliases

    # 4. Negative control: Horizon Ventures must not merge with Apex Horizon
    horizon = next(e for e in entities if e.canonical_name == "Horizon Ventures")
    assert len(horizon.sources) == 1
    assert horizon.sources[0].source_id == "raw-010"


def test_demonstrate_missing_information_detection(sample_dataset):
    """
    Demonstrates actual examples of missing information:
    1. Verdant Seed Partners (raw-008): Missing ticket size, missing geography, and missing website.
    2. Bluecrest Growth Partners (raw-004): SEC filing missing ticket size and geography.
    3. Nordic Frontier VC (raw-007): Missing raw website (null).
    """
    engine = InvestorIntelligenceEngine()
    entities = engine.process_records(sample_dataset)

    # Incomplete canonical entity: Verdant Seed Partners
    verdant = next(e for e in entities if "Verdant" in e.canonical_name)
    assert verdant.status == "INCOMPLETE_PROFILE"
    assert "ticket_size" in verdant.missing_fields
    assert "geography" in verdant.missing_fields
    assert "website" in verdant.missing_fields
    assert verdant.completeness_score == 0.45
    assert verdant.ticket_size is None
    assert verdant.headquarters is None
    assert verdant.canonical_website is None

    # Source-level missing fields
    obs_map = {o.source_id: o for o in engine.observations}
    assert obs_map["raw-004"].ticket_size is None
    assert obs_map["raw-004"].headquarters is None
    assert obs_map["raw-007"].raw_website is None


def test_demonstrate_conflicting_values_detection(sample_dataset):
    """
    Demonstrates actual examples of conflicting investor values:
    1. Apex Horizon:
       - Conflicting check size (HIGH severity): $1.5M-$3M vs $5M-$10M vs $250k-$500k.
       - Conflicting HQ (MEDIUM severity): San Francisco, CA vs London, UK.
    2. Bluecrest:
       - Conflicting stages (MEDIUM severity): Early-stage Seed vs Late-stage Series B/C/Growth.
    """
    engine = InvestorIntelligenceEngine()
    entities = engine.process_records(sample_dataset)

    # 1. Apex Horizon conflicts
    apex = next(e for e in entities if "Apex Horizon" in e.canonical_name)
    assert apex.status == "CONFLICT_DETECTED"
    assert len(apex.conflicts) == 2

    # Check ticket size conflict details
    ticket_conf = next(c for c in apex.conflicts if c.field == "ticket_size")
    assert ticket_conf.severity == "HIGH"
    assert "PitchBook 2024 Profile" in ticket_conf.conflicting_values
    assert "TechCrunch Article (Funding Announcement)" in ticket_conf.conflicting_values
    assert "AngelList / European Ecosystem Directory" in ticket_conf.conflicting_values

    # Check geography conflict details
    geo_conf = next(c for c in apex.conflicts if c.field == "geography")
    assert geo_conf.severity == "MEDIUM"
    assert geo_conf.conflicting_values["PitchBook 2024 Profile"] == "San Francisco, CA"
    assert geo_conf.conflicting_values["AngelList / European Ecosystem Directory"] == "London, UK"

    # 2. Bluecrest stage conflict details
    bluecrest = next(e for e in entities if "Bluecrest" in e.canonical_name)
    assert bluecrest.status == "CONFLICT_DETECTED"
    stage_conf = next(c for c in bluecrest.conflicts if c.field == "stages")
    assert stage_conf.severity == "MEDIUM"
    assert "SEC Form D Filing Note" in stage_conf.conflicting_values
    assert "Crunchbase Community Export" in stage_conf.conflicting_values


def test_demonstrate_source_references_preserved(sample_dataset):
    """
    Demonstrates that source references, IDs, names, confidence scores,
    and underlying raw observations are fully preserved without fabrication.
    """
    engine = InvestorIntelligenceEngine()
    entities = engine.process_records(sample_dataset)

    # Apex Horizon preserves 3 sources
    apex = next(e for e in entities if "Apex Horizon" in e.canonical_name)
    assert len(apex.sources) == 3
    for s in apex.sources:
        assert s.source_id in {"raw-001", "raw-002", "raw-003"}
        assert s.source_name in {
            "PitchBook 2024 Profile",
            "TechCrunch Article (Funding Announcement)",
            "AngelList / European Ecosystem Directory"
        }
        assert 0.0 < s.confidence <= 1.0


def test_demonstrate_uncertain_matches_flagging(sample_dataset):
    """
    Demonstrates that uncertain matches (such as name matches lacking domain corroboration)
    are explicitly flagged for researcher review:
    - Nordic Frontier: raw-007 lacks a website, so matching with raw-006 is flagged as uncertain.
    - Apex Horizon & Bluecrest: Exact root domain matches have high certainty and are not flagged.
    """
    engine = InvestorIntelligenceEngine()
    entities = engine.process_records(sample_dataset)

    # Nordic Frontier should have an uncertain match report flagged
    nordic = next(e for e in entities if "Nordic Frontier" in e.canonical_name)
    assert len(nordic.uncertain_matches) >= 1
    report = nordic.uncertain_matches[0]
    assert report.source_a_id in {"raw-006", "raw-007"}
    assert report.source_b_id in {"raw-006", "raw-007"}
    assert report.match_type == "UNCORROBORATED_NAME_MATCH"
    assert "domain verification is absent" in report.suggested_action.lower()

    # Exact domain matches for Apex Horizon (raw-001 and raw-002) are certain
    resolver = EntityResolver()
    obs_map = {o.source_id: o for o in engine.observations}
    _, conf, _, is_uncertain = resolver.evaluate_match(obs_map["raw-001"], obs_map["raw-002"])
    assert conf == 0.98
    assert is_uncertain is False
