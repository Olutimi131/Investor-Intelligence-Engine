"""
Standalone Evaluation Runner for Investor Intelligence Engine
=============================================================
Runs empirical evaluation on the dataset, asserts correctness,
and generates evaluation metric reports and visualizations.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure Windows stdout can handle UTF-8 if supported
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import matplotlib.pyplot as plt
import pandas as pd

from engine import (
    InvestorExtractor,
    InvestorIntelligenceEngine,
    get_default_dataset_path,
    load_default_dataset,
)


def run_evaluation():
    print("=" * 80)
    print("INVESTOR INTELLIGENCE ENGINE - EMPIRICAL EVALUATION")
    print("=" * 80)

    dataset_path = get_default_dataset_path()
    print(f"\n[1] Loading dataset from: {dataset_path}")
    raw_records = load_default_dataset(dataset_path)
    print(f"    Total Raw Ingested Records: {len(raw_records)}")

    # Breakdown of synthetic dataset challenges
    dup_records = [r for r in raw_records if r.get("metadata", {}).get("is_duplicate")]
    missing_records = [r for r in raw_records if r.get("metadata", {}).get("has_missing_data")]
    conflict_records = [r for r in raw_records if r.get("metadata", {}).get("has_conflicts")]

    print(f"\n    --- Synthetic Dataset Diagnostic Breakdown ---")
    print(f"    • Duplicates ({len(dup_records)} records):")
    for r in dup_records:
        meta = r.get("metadata", {})
        print(f"        - [{r['source_id']}] {r['raw_name']:<28} -> Cluster: {meta.get('duplicate_group')}")

    print(f"    • Missing Data ({len(missing_records)} records):")
    for r in missing_records:
        meta = r.get("metadata", {})
        missing_list = ", ".join(meta.get("missing_fields", []))
        print(f"        - [{r['source_id']}] {r['raw_name']:<28} -> Missing: [{missing_list}]")

    print(f"    • Conflicting Sources ({len(conflict_records)} records):")
    for r in conflict_records:
        meta = r.get("metadata", {})
        conf_fields = ", ".join(meta.get("conflict_fields", []))
        print(f"        - [{r['source_id']}] {r['raw_name']:<28} -> Fields: [{conf_fields}] | Cluster: {meta.get('duplicate_group')}")

    # 2. Pipeline Execution
    print("\n[2] Executing extraction, deduplication, and conflict resolution pipeline...")
    engine = InvestorIntelligenceEngine()
    canonical_entities = engine.process_records(raw_records)
    metrics = engine.get_metrics()

    print(f"    [OK] Total Raw Ingested:        {metrics['total_raw_records']}")
    print(f"    [OK] Total Canonical Entities:  {metrics['total_canonical_entities']}")
    print(f"    [OK] Deduplication Rate:        {metrics['deduplication_rate_percent']}%")
    print(f"    [OK] Average Completeness:      {int(metrics['average_completeness_score'] * 100)}%")
    print(f"    [OK] Status Breakdown:          {metrics['status_breakdown']}")
    print(f"    [OK] Conflicts by Field:        {metrics['conflicts_by_field']}")

    # 3. Deduplication Verification
    print("\n[3] Verifying Entity Resolution & False-Positive Prevention...")
    apex = next(e for e in canonical_entities if "Apex Horizon" in e.canonical_name)
    assert len(apex.sources) == 3, f"Expected 3 sources for Apex Horizon, got {len(apex.sources)}"
    print(f"    [OK] Apex Horizon correctly merged {len(apex.sources)} sources (Aliases: {apex.aliases})")

    bluecrest = next(e for e in canonical_entities if "Bluecrest" in e.canonical_name)
    assert len(bluecrest.sources) == 2
    print(f"    [OK] Bluecrest correctly merged {len(bluecrest.sources)} sources (Aliases: {bluecrest.aliases})")

    horizon = next(e for e in canonical_entities if e.canonical_name == "Horizon Ventures")
    assert len(horizon.sources) == 1, "Horizon Ventures was falsely merged!"
    print("    [OK] Horizon Ventures stayed isolated (False positive safeguard verified).")

    # 4. Conflict Resolution Verification
    print("\n[4] Auditing Flagged Source Conflicts...")
    conflicts_found = [e for e in canonical_entities if e.conflicts]
    print(f"    Total Entities with Conflicts: {len(conflicts_found)}")
    for e in conflicts_found:
        print(f"\n    • Entity: {e.canonical_name} ({e.id})")
        for c in e.conflicts:
            print(f"      - Conflict: {c.field.upper()} (Severity: {c.severity})")
            print(f"        Details: {c.description}")
            print(f"        Action:  {c.suggested_action}")

    # 4b. Uncertain Matches Audit
    print("\n[4b] Auditing Flagged Uncertain Matches...")
    uncertain_entities = [e for e in canonical_entities if e.uncertain_matches]
    print(f"    Total Entities with Uncertain Matches: {len(uncertain_entities)}")
    for e in uncertain_entities:
        print(f"    • Entity: {e.canonical_name} ({e.id})")
        for u in e.uncertain_matches:
            print(f"      - Matched: {u.source_a_name} ({u.source_a_id}) ⟷ {u.source_b_name} ({u.source_b_id})")
            print(f"        Confidence: {int(u.confidence * 100)}% ({u.match_type})")
            print(f"        Reason:     {u.reason}")
            print(f"        Action:     {u.suggested_action}")

    # 5. Completeness & Missing Fields
    print("\n[5] Auditing Incomplete Profiles & Missing Data...")
    incomplete = [e for e in canonical_entities if e.missing_fields]
    for e in incomplete:
        print(f"    • {e.canonical_name} ({e.status}): Missing fields -> {e.missing_fields} (Completeness: {int(e.completeness_score * 100)}%)")

    # 6. Generate Analytics Chart
    print("\n[6] Generating evaluation visual charts...")
    try:
        fig, axes = plt.subplots(1, 3, figsize=(18, 5))

        # Status chart
        status_counts = pd.Series(metrics["status_breakdown"])
        colors = ["#f59e0b", "#3b82f6", "#10b981", "#ef4444"]
        axes[0].bar(status_counts.index, status_counts.values, color=colors[:len(status_counts)])
        axes[0].set_title("Canonical Entity Status Breakdown", fontsize=12, fontweight="bold")
        axes[0].set_ylabel("Number of Entities")
        axes[0].tick_params(axis='x', rotation=30)
        for i, v in enumerate(status_counts.values):
            axes[0].text(i, v + 0.05, str(v), ha="center", fontweight="bold")

        # Sector chart
        sector_counts = {}
        for e in canonical_entities:
            for s in e.sectors:
                sector_counts[s] = sector_counts.get(s, 0) + 1
        s_series = pd.Series(sector_counts).sort_values(ascending=True)
        axes[1].barh(s_series.index, s_series.values, color="#6366f1")
        axes[1].set_title("Identified Investment Sectors", fontsize=12, fontweight="bold")
        axes[1].set_xlabel("Number of Active Firms")
        for i, v in enumerate(s_series.values):
            axes[1].text(v + 0.05, i, str(v), va="center", fontweight="bold")

        # Completeness chart
        names = [e.canonical_name.split()[0] for e in canonical_entities]
        scores = [e.completeness_score * 100 for e in canonical_entities]
        axes[2].bar(names, scores, color="#06b6d4")
        axes[2].set_title("Entity Profile Completeness (%)", fontsize=12, fontweight="bold")
        axes[2].set_ylabel("Completeness Percentage")
        axes[2].set_ylim(0, 115)
        axes[2].tick_params(axis='x', rotation=30)
        for i, v in enumerate(scores):
            axes[2].text(i, v + 2, f"{int(v)}%", ha="center", fontweight="bold")

        plt.tight_layout()
        chart_path = Path("evaluation_summary.png")
        plt.savefig(chart_path, dpi=150)
        plt.close()
        print(f"    [OK] Visual evaluation chart saved to: {chart_path.resolve()}")
    except Exception as exc:
        print(f"    [!] Could not generate plot: {exc}")

    # 7. Ad-Hoc Extraction Demo
    print("\n[7] Testing Ad-Hoc Text Extraction on Unseen Data...")
    extractor = InvestorExtractor()
    sample_text = (
        "Quantum Leap Ventures announced their new $120M Fund II to lead Seed and Series A "
        "rounds in Cybersecurity, AI/ML, and Quantum Computing. Typically writing check sizes "
        "from $2.5M to $5.0M across North America and Western Europe."
    )
    obs = extractor.extract({
        "source_id": "eval-01",
        "source_name": "Test Press Release",
        "raw_name": "Quantum Leap Ventures",
        "raw_text": sample_text
    })
    print(f"    [OK] Extracted Sectors:   {obs.sectors}")
    print(f"    [OK] Extracted Stages:    {obs.stages}")
    print(f"    [OK] Extracted Ticket:    {obs.ticket_size.formatted_range if obs.ticket_size else 'None'}")
    print(f"    [OK] Extracted Fund Size: {obs.signals.fund_size}")

    print("\n" + "=" * 80)
    print("[OK] ALL EVALUATION CHECKS PASSED SUCCESSFULLY")
    print("=" * 80)


if __name__ == "__main__":
    run_evaluation()
