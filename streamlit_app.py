"""
Investor Intelligence Engine - Streamlit Interactive Dashboard
==============================================================
A modern, production-grade interface for visualizing extracted investor data,
exploring deduplicated clusters, auditing source conflicts, and testing live extraction.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from engine import (
    CanonicalInvestor,
    InvestorExtractor,
    InvestorIntelligenceEngine,
    get_default_dataset_path,
    load_default_dataset,
)

# Page configuration
st.set_page_config(
    page_title="Investor Intelligence Engine",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for polished styling
st.markdown("""
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(90deg, #3b82f6, #8b5cf6);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        color: #64748b;
        font-size: 1.05rem;
        margin-bottom: 1.5rem;
    }
    .status-badge {
        padding: 4px 10px;
        border-radius: 9999px;
        font-size: 0.8rem;
        font-weight: 700;
        display: inline-block;
    }
    .badge-verified { background-color: #d1fae5; color: #065f46; border: 1px solid #10b981; }
    .badge-conflict { background-color: #fef3c7; color: #92400e; border: 1px solid #f59e0b; }
    .badge-unverified { background-color: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; }
    .badge-incomplete { background-color: #fee2e2; color: #991b1b; border: 1px solid #ef4444; }
    .tag-chip {
        background-color: #e0e7ff;
        color: #3730a3;
        padding: 2px 8px;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 600;
        margin-right: 4px;
        margin-bottom: 4px;
        display: inline-block;
    }
    .metric-card {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 10px;
        padding: 15px;
        text-align: center;
    }
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def get_engine_data():
    dataset_path = get_default_dataset_path()
    records = load_default_dataset(dataset_path)
    engine = InvestorIntelligenceEngine()
    entities = engine.process_records(records)
    metrics = engine.get_metrics()
    return engine, entities, metrics, records, dataset_path


engine, entities, metrics, raw_records, dataset_path = get_engine_data()

# ----------------------------------------------------------------------------
# Header & Key Metrics
# ----------------------------------------------------------------------------
st.markdown('<div class="main-header">🧠 Investor Intelligence Engine</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Autonomous extraction, entity resolution & conflict auditing for venture capital data</div>', unsafe_allow_html=True)

col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("Raw Ingested Records", metrics["total_raw_records"], help="Total entries from various data sources")
with col2:
    st.metric("Canonical Entities", metrics["total_canonical_entities"], f"-{metrics['deduplication_rate_percent']}% Dedup", help="Resolved unique firms")
with col3:
    conflicted_count = metrics["status_breakdown"].get("CONFLICT_DETECTED", 0)
    st.metric("Flagged Conflicts", conflicted_count, "Needs Review", delta_color="inverse", help="Profiles with contradictory source data")
with col4:
    verified_count = metrics["status_breakdown"].get("VERIFIED_MULTI_SOURCE", 0)
    st.metric("Verified Multi-Source", verified_count, help="Corroborated across >= 2 independent sources")
with col5:
    st.metric("Avg Completeness", f"{int(metrics['average_completeness_score'] * 100)}%", help="Profile attribute completeness score")

st.markdown("---")

# ----------------------------------------------------------------------------
# Sidebar Filters
# ----------------------------------------------------------------------------
with st.sidebar:
    st.header("🔍 Search & Filter")
    search_query = st.text_input("Search firm, alias, or portfolio:", placeholder="e.g. Apex, PayStacker")

    st.subheader("Filter Attributes")
    all_stages = ["Pre-Seed", "Seed", "Series A", "Series B", "Series C", "Growth"]
    selected_stages = st.multiselect("Investment Stage:", options=all_stages, default=[])

    all_sectors = ["FinTech", "B2B SaaS", "AI/ML", "DeepTech", "ClimateTech", "CyberSecurity", "Biotech", "Developer Tools"]
    selected_sectors = st.multiselect("Sector Focus:", options=all_sectors, default=[])

    st.subheader("Audit Flags")
    conflicts_only = st.checkbox("⚡ Conflicted Profiles Only", value=False)
    incomplete_only = st.checkbox("⚠️ Incomplete Profiles Only", value=False)

    st.markdown("---")
    st.caption(f"📁 Dataset: `{dataset_path.name}`")
    if st.button("🔄 Reload & Re-process"):
        st.cache_resource.clear()
        st.rerun()

# Filter logic
filtered_entities: list[CanonicalInvestor] = []
for e in entities:
    # Keyword search
    if search_query:
        q = search_query.lower()
        match_name = q in e.canonical_name.lower()
        match_alias = any(q in a.lower() for a in e.aliases)
        match_port = any(q in p.lower() for p in e.signals.portfolio_companies)
        if not (match_name or match_alias or match_port):
            continue

    # Stage filter
    if selected_stages:
        if not any(s in e.stages for s in selected_stages):
            continue

    # Sector filter
    if selected_sectors:
        if not any(s in e.sectors for s in selected_sectors):
            continue

    # Conflict flag
    if conflicts_only and not e.conflicts:
        continue

    # Incomplete flag
    if incomplete_only and e.status != "INCOMPLETE_PROFILE":
        continue

    filtered_entities.append(e)

# ----------------------------------------------------------------------------
# Main Navigation Tabs
# ----------------------------------------------------------------------------
tab_portfolio, tab_conflicts, tab_extractor, tab_analytics, tab_dataset = st.tabs([
    "📇 Resolved Profiles",
    "⚡ Conflict Audit",
    "🧪 Live Extractor",
    "📊 Analytics",
    "📁 Dataset Explorer"
])

# ----------------------------------------------------------------------------
# Tab 1: Resolved Profiles Portfolio
# ----------------------------------------------------------------------------
with tab_portfolio:
    st.subheader(f"Resolved Investor Profiles ({len(filtered_entities)} of {len(entities)})")

    if not filtered_entities:
        st.warning("No investor profiles match your filter criteria.")
    else:
        for e in filtered_entities:
            with st.container():
                # Status styling
                status_class = {
                    "VERIFIED_MULTI_SOURCE": "badge-verified",
                    "CONFLICT_DETECTED": "badge-conflict",
                    "SINGLE_SOURCE_UNVERIFIED": "badge-unverified",
                    "INCOMPLETE_PROFILE": "badge-incomplete"
                }.get(e.status, "badge-unverified")

                status_label = {
                    "VERIFIED_MULTI_SOURCE": "✓ VERIFIED MULTI-SOURCE",
                    "CONFLICT_DETECTED": "⚡ CONFLICT DETECTED",
                    "SINGLE_SOURCE_UNVERIFIED": "○ SINGLE-SOURCE UNVERIFIED",
                    "INCOMPLETE_PROFILE": "⚠️ INCOMPLETE PROFILE"
                }.get(e.status, e.status)

                # Card Header
                hcol1, hcol2 = st.columns([3, 1])
                with hcol1:
                    alias_str = f" <span style='color: #64748b; font-size: 0.9rem;'>[aka {', '.join(e.aliases)}]</span>" if e.aliases else ""
                    st.markdown(f"### {e.canonical_name} <span class='status-badge {status_class}'>{status_label}</span>{alias_str}", unsafe_allow_html=True)
                with hcol2:
                    st.progress(e.completeness_score, text=f"Completeness: {int(e.completeness_score * 100)}%")

                # Details Grid
                dcol1, dcol2, dcol3, dcol4 = st.columns(4)
                with dcol1:
                    ticket_val = e.ticket_size.formatted_range if e.ticket_size else "Unknown"
                    st.markdown(f"**💰 Ticket Size:** `{ticket_val}`")
                    st.markdown(f"**🏢 Headquarters:** {e.headquarters or 'Unknown'}")
                with dcol2:
                    st.markdown(f"**🌐 Website:** {f'[{e.canonical_website}]({e.canonical_website})' if e.canonical_website else 'None'}")
                    st.markdown(f"**🌍 Regions:** {', '.join(e.geography[:2]) if e.geography else 'Unknown'}")
                with dcol3:
                    st.markdown(f"**💼 Fund Size:** {e.signals.fund_size or 'Not disclosed'}")
                    st.markdown(f"**👔 Leadership:** {', '.join(e.signals.leadership) if e.signals.leadership else 'None listed'}")
                with dcol4:
                    st.markdown(f"**📚 Sources Ingested:** `{len(e.sources)} sources`")
                    st.markdown(f"**🆔 Entity ID:** `{e.id}`")

                # Tags Row
                tcol1, tcol2 = st.columns(2)
                with tcol1:
                    if e.stages:
                        st.markdown("**Stages:** " + "".join(f"<span class='tag-chip'>{s}</span>" for s in e.stages), unsafe_allow_html=True)
                with tcol2:
                    if e.sectors:
                        st.markdown("**Sectors:** " + "".join(f"<span class='tag-chip' style='background-color:#ecfdf5; color:#065f46;'>{s}</span>" for s in e.sectors), unsafe_allow_html=True)

                if e.signals.portfolio_companies:
                    st.markdown("**Portfolio Mentions:** " + "".join(f"<span class='tag-chip' style='background-color:#fef3c7; color:#92400e;'>{p}</span>" for p in e.signals.portfolio_companies), unsafe_allow_html=True)

                # Conflicts Expander
                if e.conflicts:
                    with st.expander(f"⚡ **Flagged Discrepancies ({len(e.conflicts)} conflicts require review)**", expanded=True):
                        for c in e.conflicts:
                            st.warning(f"**[{c.severity} SEVERITY] Field: `{c.field.upper()}`**\n\n"
                                       f"• **Discrepancy:** {c.description}\n\n"
                                       f"• **Recommended Action:** {c.suggested_action}")

                # Provenance Expander
                with st.expander(f"📚 View Contributing Evidence & Provenance ({len(e.sources)} sources)"):
                    prov_rows = []
                    for s in e.sources:
                        prov_rows.append({
                            "Source ID": s.source_id,
                            "Source Name": s.source_name,
                            "Raw Firm Name": s.raw_name,
                            "Confidence": f"{s.confidence:.2f}",
                            "Ticket Size": s.ticket_size.formatted_range if s.ticket_size else "None",
                            "Reported HQ": s.headquarters or "None",
                            "Stages": ", ".join(s.stages) if s.stages else "None",
                            "Sectors": ", ".join(s.sectors[:3]) if s.sectors else "None"
                        })
                    st.dataframe(pd.DataFrame(prov_rows))

                st.markdown("---")

# ----------------------------------------------------------------------------
# Tab 2: Conflict & Resolution Audit
# ----------------------------------------------------------------------------
with tab_conflicts:
    st.subheader("⚡ Source Contradiction & Provenance Audit")
    st.markdown("This tab details all cross-source discrepancies identified during entity reconciliation.")

    conflicted = [e for e in entities if e.conflicts]
    if not conflicted:
        st.success("✓ No conflicts detected across the ingested dataset.")
    else:
        st.info(f"Identified {len(conflicted)} canonical entities with contradictory data across different sources.")

        for e in conflicted:
            st.markdown(f"### {e.canonical_name} (`{e.id}`)")
            for c in e.conflicts:
                sev_color = "red" if c.severity == "HIGH" else "orange"
                st.markdown(f":{sev_color}[**{c.severity} SEVERITY: Discrepancy on `{c.field.upper()}`**]")
                st.write(c.description)
                st.caption(f"**Suggested Resolution:** {c.suggested_action}")
                st.json(c.conflicting_values)
            st.markdown("---")

    # Incomplete profiles section
    st.subheader("⚠️ Incomplete Profiles Audit")
    incomplete_list = [e for e in entities if e.missing_fields]
    incomp_data = [
        {
            "ID": e.id,
            "Canonical Name": e.canonical_name,
            "Status": e.status,
            "Missing Attributes": ", ".join(e.missing_fields),
            "Completeness Score": f"{int(e.completeness_score * 100)}%"
        }
        for e in incomplete_list
    ]
    st.dataframe(pd.DataFrame(incomp_data))

# ----------------------------------------------------------------------------
# Tab 3: Live Ad-Hoc Text Extractor
# ----------------------------------------------------------------------------
with tab_extractor:
    st.subheader("🧪 Live Ad-Hoc Text Extraction Sandbox")
    st.markdown("Test the extraction heuristics on any raw investor text, press release, or founder referral note.")

    # Preset sample selector
    preset_choice = st.selectbox(
        "Choose a sample snippet or write your own below:",
        [
            "Custom Input",
            "Sample 1: Growth AI & DevTools Fund ($5M-$10M)",
            "Sample 2: European DeepTech & Robotics (€500k-€2M)",
            "Sample 3: ClimateTech & AgriTech Pre-Seed"
        ]
    )

    preset_texts = {
        "Sample 1: Growth AI & DevTools Fund ($5M-$10M)": (
            "Apex Horizon Capital announced an expanded thesis. The firm is now leading growth rounds "
            "with check sizes between $5,000,000 and $10,000,000 ($5M - $10M) for Series A and Series B startups. "
            "Expanding beyond FinTech into Artificial Intelligence, ML, and Developer Tools. Headquartered in San Francisco, CA. "
            "Managing Director Alex Rivera noted aggressive deployment from their $250M Fund IV."
        ),
        "Sample 2: European DeepTech & Robotics (€500k-€2M)": (
            "Nordic Frontier Fund is a regional venture capital fund backing founders in the Nordics (Sweden, Finland, Estonia). "
            "Typical ticket sizes range from €500,000 to €2,000,000 ($550k - $2.2M) at Seed and Series A. "
            "Key sectors: DeepTech, Robotics, and B2B SaaS. Managing Partner Matti Virtanen in Helsinki."
        ),
        "Sample 3: ClimateTech & AgriTech Pre-Seed": (
            "Verdant Seed Partners is a boutique fund dedicated exclusively to regenerative agriculture and AgriTech. "
            "We partner with founders at the Pre-Seed and Seed stages. Founded by former agronomist Elena Rostova. "
            "No ticket size publicized; contact partners directly."
        )
    }

    default_text = preset_texts.get(preset_choice, "")

    user_text = st.text_area(
        "Unstructured Investor Paragraph:",
        value=default_text,
        height=150,
        placeholder="e.g. Apex Horizons writes checks of $1.5M to $3M for Seed startups in FinTech..."
    )

    col_btn1, col_btn2 = st.columns([1, 4])
    with col_btn1:
        extract_button = st.button("🚀 Extract Structured Data", type="primary")

    if extract_button and user_text.strip():
        extractor = InvestorExtractor()
        obs = extractor.extract({
            "source_id": "sandbox-001",
            "source_name": "Streamlit Interactive Sandbox",
            "raw_name": "Sandbox Entity",
            "raw_text": user_text
        })

        st.success("✓ Successfully extracted structured attributes!")

        ecol1, ecol2, ecol3 = st.columns(3)
        with ecol1:
            st.markdown(f"**💰 Extracted Ticket:** `{obs.ticket_size.formatted_range if obs.ticket_size else 'None'}`")
            st.markdown(f"**🏢 Extracted HQ:** `{obs.headquarters or 'None'}`")
        with ecol2:
            st.markdown(f"**📈 Stages:** {', '.join(obs.stages) if obs.stages else 'None'}")
            st.markdown(f"**🎯 Sectors:** {', '.join(obs.sectors) if obs.sectors else 'None'}")
        with ecol3:
            st.markdown(f"**💼 Fund Size:** `{obs.signals.fund_size or 'None'}`")
            st.markdown(f"**👔 Leadership:** `{', '.join(obs.signals.leadership) if obs.signals.leadership else 'None'}`")

        with st.expander("🔍 View Complete Raw Pydantic Model JSON"):
            st.json(obs.model_dump())

# ----------------------------------------------------------------------------
# Tab 4: Analytics
# ----------------------------------------------------------------------------
with tab_analytics:
    st.subheader("📊 Intelligence Engine Analytics")

    acol1, acol2 = st.columns(2)

    with acol1:
        st.markdown("#### Status Breakdown")
        status_df = pd.DataFrame(list(metrics["status_breakdown"].items()), columns=["Status", "Count"])
        st.bar_chart(status_df.set_index("Status"))

    with acol2:
        st.markdown("#### Identified Sector Distribution")
        sector_freq = {}
        for e in entities:
            for s in e.sectors:
                sector_freq[s] = sector_freq.get(s, 0) + 1
        sec_df = pd.DataFrame(list(sector_freq.items()), columns=["Sector", "Active Firms"]).sort_values("Active Firms", ascending=False)
        st.bar_chart(sec_df.set_index("Sector"))

    st.markdown("#### Entity Profile Completeness")
    comp_chart_df = pd.DataFrame([
        {"Entity": e.canonical_name, "Completeness (%)": int(e.completeness_score * 100)}
        for e in entities
    ])
    st.bar_chart(comp_chart_df.set_index("Entity"))

# ----------------------------------------------------------------------------
# Tab 5: Raw Dataset Explorer & Export
# ----------------------------------------------------------------------------
with tab_dataset:
    st.subheader("📁 Ingested Multi-Source Synthetic Dataset (`data/investors.json`)")
    st.caption("Inspect raw multi-source inputs with dedicated audits for duplicates, missing fields, and conflicting data.")

    # High-level metrics for the dataset challenges
    dup_records = [r for r in raw_records if r.get("metadata", {}).get("is_duplicate")]
    missing_records = [r for r in raw_records if r.get("metadata", {}).get("has_missing_data")]
    conflict_records = [r for r in raw_records if r.get("metadata", {}).get("has_conflicts")]

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Total Raw Records", len(raw_records), help="Total synthetic inputs in data/investors.json")
    with m2:
        st.metric("Duplicate Records", len(dup_records), "3 Target Clusters", help="Records sharing underlying firm identities")
    with m3:
        st.metric("Missing Data Records", len(missing_records), "Unlisted Fields", delta_color="inverse", help="Records lacking website, check size, or HQ")
    with m4:
        st.metric("Conflicting Sources", len(conflict_records), "Cross-Source Discrepancies", delta_color="inverse", help="Records asserting contradictory facts")

    st.markdown("---")

    # Segment filter for dataset views
    dataset_view = st.radio(
        "Select Dataset View:",
        ["📋 All Raw Records (10)", "🔁 Duplicate Clusters (7)", "⚠️ Missing Information (3)", "⚡ Conflicting Discrepancies (5)"],
        horizontal=True
    )

    if dataset_view == "📋 All Raw Records (10)":
        all_table_data = [
            {
                "Source ID": r["source_id"],
                "Raw Firm Name": r["raw_name"],
                "Source Name": r["source_name"],
                "Website": r.get("raw_website") or "[Missing / None]",
                "Dataset Role": r.get("metadata", {}).get("dataset_role", "Raw Record"),
                "Target Cluster": r.get("metadata", {}).get("expected_cluster", "Unknown"),
                "Challenges & Notes": r.get("metadata", {}).get("challenge_description", r["raw_text"][:75] + "...")
            }
            for r in raw_records
        ]
        st.dataframe(pd.DataFrame(all_table_data), width="stretch")

    elif dataset_view == "🔁 Duplicate Clusters (7)":
        st.markdown("**7 raw records represent duplicates clustered into 3 distinct canonical venture firms:**")
        dup_table_data = [
            {
                "Duplicate Cluster": r.get("metadata", {}).get("duplicate_group"),
                "Source ID": r["source_id"],
                "Source Name": r["source_name"],
                "Raw Entity Name": r["raw_name"],
                "Reported Website": r.get("raw_website") or "[None]",
                "Expected Canonical Firm": r.get("metadata", {}).get("expected_cluster"),
                "Resolution Strategy & Challenge": r.get("metadata", {}).get("challenge_description")
            }
            for r in dup_records
        ]
        st.dataframe(pd.DataFrame(dup_table_data), width="stretch")

        st.info("💡 **Deduplication Heuristic:** The engine clusters duplicates using deterministic root domain normalization (e.g. `apexhorizon.vc`) followed by RapidFuzz token sorting ratio (≥88%) with single-token false-positive protection.")

    elif dataset_view == "⚠️ Missing Information (3)":
        st.markdown("**3 records contain deliberately unlisted or missing attributes:**")
        missing_table_data = [
            {
                "Source ID": r["source_id"],
                "Source Name": r["source_name"],
                "Raw Entity Name": r["raw_name"],
                "Missing Attributes": ", ".join(r.get("metadata", {}).get("missing_fields", [])) if r.get("metadata", {}).get("missing_fields") else "None",
                "Website Present?": "No" if not r.get("raw_website") else "Yes",
                "Role / Impact": r.get("metadata", {}).get("dataset_role"),
                "Context & Impact": r.get("metadata", {}).get("challenge_description")
            }
            for r in missing_records
        ]
        st.dataframe(pd.DataFrame(missing_table_data), width="stretch")

        st.warning("⚠️ **Data Completeness Impact:** `raw-008` (Verdant Seed Partners) lacks website, check size, and geography, resulting in an `INCOMPLETE_PROFILE` status (Completeness score: 45%).")

    elif dataset_view == "⚡ Conflicting Discrepancies (5)":
        st.markdown("**5 raw records introduce contradictory assertions across independent sources:**")
        conflict_table_data = [
            {
                "Target Cluster": r.get("metadata", {}).get("duplicate_group"),
                "Source ID": r["source_id"],
                "Source Name": r["source_name"],
                "Raw Entity Name": r["raw_name"],
                "Conflicting Field(s)": ", ".join(r.get("metadata", {}).get("conflict_fields", [])),
                "Contradiction Details": r.get("metadata", {}).get("challenge_description")
            }
            for r in conflict_records
        ]
        st.dataframe(pd.DataFrame(conflict_table_data), width="stretch")

        st.error("⚡ **Conflict Severity Rules:** Check size disparities >2x trigger HIGH severity; geographic headquarters discrepancies and stage divergence (Seed vs Growth) trigger MEDIUM severity with suggested analyst actions.")

    st.subheader("📥 Export Intelligence Outputs")
    dcol1, dcol2, dcol3 = st.columns(3)

    with dcol1:
        resolved_json = json.dumps([e.model_dump() for e in entities], indent=2)
        st.download_button(
            label="⬇️ Download Resolved Profiles (JSON)",
            data=resolved_json,
            file_name="resolved_investors.json",
            mime="application/json"
        )

    with dcol2:
        raw_json = json.dumps(raw_records, indent=2)
        st.download_button(
            label="⬇️ Download Raw Dataset (JSON)",
            data=raw_json,
            file_name="raw_investors.json",
            mime="application/json"
        )

    with dcol3:
        csv_path = Path("data/investors.csv")
        if csv_path.exists():
            with open(csv_path, "r", encoding="utf-8") as f:
                csv_data = f.read()
            st.download_button(
                label="⬇️ Download Raw Dataset (CSV)",
                data=csv_data,
                file_name="investors.csv",
                mime="text/csv"
            )
