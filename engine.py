"""
Investor Intelligence Engine - Core Module
=========================================
Implements:
1. Extraction Pipeline: Unstructured text parsing to structured investor profiles.
2. Entity Resolution & Deduplication: Two-tier matching (exact domain + fuzzy name clustering).
3. Conflict Resolution & Source Provenance: Field-level evidence tracking and anomaly flagging.
"""

from __future__ import annotations

import ast
import csv
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

# ============================================================================
# 1. Pydantic Domain Models
# ============================================================================

class TicketSize(BaseModel):
    min_usd: float | None = None
    max_usd: float | None = None
    currency: str = "USD"
    raw_text: str | None = None
    formatted_range: str = "Unknown"

    @classmethod
    def from_values(cls, min_val: float | None, max_val: float | None, currency: str = "USD", raw: str | None = None) -> TicketSize:
        if min_val is None and max_val is None:
            return cls(currency=currency, raw_text=raw, formatted_range="Not specified")

        def fmt(val: float) -> str:
            if val >= 1_000_000:
                return f"${val / 1_000_000:.1f}M".replace(".0M", "M")
            if val >= 1_000:
                return f"${val / 1_000:.0f}k"
            return f"${val:,.0f}"

        if min_val is not None and max_val is not None:
            if min_val == max_val:
                fmt_str = fmt(min_val)
            else:
                fmt_str = f"{fmt(min_val)} - {fmt(max_val)}"
        elif min_val is not None:
            fmt_str = f"From {fmt(min_val)}"
        else:
            fmt_str = f"Up to {fmt(max_val)}" # type: ignore

        return cls(min_usd=min_val, max_usd=max_val, currency=currency, raw_text=raw, formatted_range=fmt_str)


class ExtractedSignals(BaseModel):
    portfolio_companies: list[str] = Field(default_factory=list)
    fund_size: str | None = None
    leadership: list[str] = Field(default_factory=list)
    key_notes: list[str] = Field(default_factory=list)


class SourceObservation(BaseModel):
    source_id: str
    source_name: str
    retrieved_at: str | None = None
    raw_name: str
    raw_website: str | None = None
    confidence: float = 1.0
    sectors: list[str] = Field(default_factory=list)
    stages: list[str] = Field(default_factory=list)
    geography: list[str] = Field(default_factory=list)
    headquarters: str | None = None
    ticket_size: TicketSize | None = None
    signals: ExtractedSignals = Field(default_factory=ExtractedSignals)


class ConflictReport(BaseModel):
    field: str
    severity: str  # HIGH, MEDIUM, LOW
    description: str
    conflicting_values: dict[str, Any]
    suggested_action: str


class UncertainMatchReport(BaseModel):
    source_a_id: str
    source_b_id: str
    source_a_name: str
    source_b_name: str
    confidence: float
    match_type: str
    reason: str
    suggested_action: str


class CanonicalInvestor(BaseModel):
    id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    canonical_website: str | None = None
    sectors: list[str] = Field(default_factory=list)
    stages: list[str] = Field(default_factory=list)
    geography: list[str] = Field(default_factory=list)
    headquarters: str | None = None
    ticket_size: TicketSize | None = None
    signals: ExtractedSignals = Field(default_factory=ExtractedSignals)
    sources: list[SourceObservation] = Field(default_factory=list)
    conflicts: list[ConflictReport] = Field(default_factory=list)
    uncertain_matches: list[UncertainMatchReport] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    status: str = "SINGLE_SOURCE_UNVERIFIED"
    completeness_score: float = 0.0


# ============================================================================
# 2. Extraction Pipeline
# ============================================================================

class InvestorExtractor:
    """
    Extracts structured attributes from unstructured text and semi-structured fields.
    Uses regex pattern matchers, controlled financial dictionaries, and NER-like heuristics.
    """

    STAGE_TAXONOMY = [
        "Pre-Seed",
        "Seed",
        "Series A",
        "Series B",
        "Series C",
        "Growth",
        "Late-Stage"
    ]

    SECTOR_TAXONOMY = {
        "FinTech": ["fintech", "financial tech", "payments", "banking", "ledger", "defi", "crypto", "cryptography"],
        "B2B SaaS": ["b2b saas", "enterprise software", "saas", "cloud software", "software"],
        "AI/ML": ["artificial intelligence", "ai", "machine learning", "ml", "generative ai", "deeptech"],
        "DeepTech": ["deeptech", "deep tech", "semiconductors", "quantum computing", "robotics", "hardware"],
        "Developer Tools": ["developer tools", "devtools", "open source", "developer platform"],
        "ClimateTech": ["climatetech", "climate tech", "clean energy", "cleantech", "sustainability", "regenerative agriculture", "agritech", "biodiversity"],
        "CyberSecurity": ["cybersecurity", "security", "cloudshield"],
        "Biotech": ["biotech", "synthetic biology", "life sciences", "healthtech", "health"]
    }

    GEOGRAPHY_KEYWORDS = {
        "San Francisco, CA": ["san francisco", "sf", "bay area", "silicon valley"],
        "New York, NY": ["new york", "nyc", "manhattan"],
        "Boston, MA": ["boston", "massachusetts"],
        "Austin, TX": ["austin", "texas"],
        "London, UK": ["london", "united kingdom", "uk"],
        "Helsinki, Finland": ["helsinki", "finland"],
        "Hong Kong": ["hong kong"],
        "North America": ["north america", "united states", "us", "usa"],
        "Europe": ["europe", "european", "western europe"],
        "Nordics & Baltics": ["nordics", "baltics", "sweden", "estonia", "norway"],
        "Asia-Pacific": ["asia-pacific", "apac", "asia"]
    }

    CURRENCY_EXCHANGE_TO_USD = {
        "$": 1.0,
        "USD": 1.0,
        "€": 1.10,
        "EUR": 1.10,
        "£": 1.28,
        "GBP": 1.28
    }

    def extract_stages(self, text: str) -> list[str]:
        found = []
        low = text.lower()
        for stage in self.STAGE_TAXONOMY:
            pattern = r'\b' + re.escape(stage.lower()) + r'\b'
            if re.search(pattern, low):
                found.append(stage)
        return found

    def extract_sectors(self, text: str) -> list[str]:
        found = []
        low = text.lower()
        for canonical_sector, keywords in self.SECTOR_TAXONOMY.items():
            for kw in keywords:
                pattern = r'\b' + re.escape(kw) + r'\b'
                if re.search(pattern, low):
                    if canonical_sector not in found:
                        found.append(canonical_sector)
                    break
        return found

    def extract_geography(self, text: str) -> tuple[str | None, list[str]]:
        low = text.lower()
        hq = None
        regions = []

        # Find specific cities/HQs first
        hq_candidates = ["San Francisco, CA", "New York, NY", "Boston, MA", "Austin, TX", "London, UK", "Helsinki, Finland", "Hong Kong"]
        for candidate in hq_candidates:
            keywords = self.GEOGRAPHY_KEYWORDS[candidate]
            for kw in keywords:
                if re.search(r'\b' + re.escape(kw) + r'\b', low):
                    hq = candidate
                    break
            if hq:
                break

        # Detect all operating regions
        for region, keywords in self.GEOGRAPHY_KEYWORDS.items():
            for kw in keywords:
                if re.search(r'\b' + re.escape(kw) + r'\b', low):
                    if region not in regions:
                        regions.append(region)
                    break

        return hq, regions

    def extract_ticket_size(self, text: str) -> TicketSize | None:
        """
        Parses ticket sizes, handling formats like:
        - '$1,500,000 to $3,000,000'
        - '$1.5M - $3M' or '$1.5M and $3M'
        - '€500,000 to €2,000,000'
        - '$5M - $10M'
        - '$500k' or '€1M'
        - 'checks of $2M to $4M'
        """
        context_patterns = [
            # Check context with flexible words (e.g. "checks typically between $1.5M and $3M")
            r'(?:check|ticket|investing|investment|checks|tickets|write|writing)\s+(?:[a-zA-Z]+\s+){0,3}(?:of|around|between|from)?\s*([\$€£]?\s*[\d,.]+\s*(?:k|m|b|million|thousand|billion)?\s*(?:[-–—]|\bto\b|\band\b)\s*[\$€£]?\s*[\d,.]+\s*(?:k|m|b|million|thousand|billion)?)',
            # Parenthetical range e.g. ($1.5M - $3M)
            r'\(([\$€£]?\s*[\d,.]+\s*(?:k|m|b|million|thousand|billion)?\s*[-–—]\s*[\$€£]?\s*[\d,.]+\s*(?:k|m|b|million|thousand|billion)?)\)',
            # General currency range with -, to, and
            r'([\$€£]\s*[\d,.]+\s*(?:k|m|b|million|thousand|billion)?\s*(?:[-–—]|\bto\b|\band\b)\s*[\$€£]?\s*[\d,.]+\s*(?:k|m|b|million|thousand|billion)?)',
            # Single ticket amount with context
            r'(?:check|ticket|investing|investment|checks|tickets|write|writing)\s+(?:size|around|of)?\s*(?:[a-zA-Z]+\s+){0,2}([\$€£]\s*[\d,.]+\s*(?:k|m|b|million|thousand|billion)?)'
        ]

        raw_match = None
        for pattern in context_patterns:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                raw_match = m.group(1).strip()
                break

        if not raw_match:
            return None

        # Determine currency with word boundaries to avoid false positives (e.g. "Europe" containing "EUR")
        currency = "USD"
        rate = 1.0
        if "€" in raw_match or re.search(r'\b(?:eur|euros?)\b', text, re.IGNORECASE):
            currency = "EUR"
            rate = self.CURRENCY_EXCHANGE_TO_USD["€"]
        elif "£" in raw_match or re.search(r'\b(?:gbp|pounds?)\b', text, re.IGNORECASE):
            currency = "GBP"
            rate = self.CURRENCY_EXCHANGE_TO_USD["£"]

        # Parse numeric bounds
        bounds = self._parse_numeric_bounds(raw_match, rate)
        if bounds:
            min_val, max_val = bounds
            return TicketSize.from_values(min_val, max_val, currency=currency, raw=raw_match)

        return None

    def _parse_numeric_bounds(self, span: str, exchange_rate: float) -> tuple[float, float] | None:
        def parse_single(token: str) -> float | None:
            original = token.strip().lower()
            clean = re.sub(r'[^0-9.]+', '', original)
            if not clean:
                return None

            multiplier = 1.0
            if 'billion' in original or original.endswith('b'):
                multiplier = 1_000_000_000.0
            elif 'million' in original or original.endswith('m'):
                multiplier = 1_000_000.0
            elif 'thousand' in original or original.endswith('k'):
                multiplier = 1_000.0

            try:
                val = float(clean) * multiplier * exchange_rate
                return val
            except ValueError:
                return None

        normalized = span.strip()
        if not normalized:
            return None

        tokens = re.split(r'(?:(?:\bto\b)|(?:\band\b)|[-–—])', normalized, flags=re.IGNORECASE)
        tokens = [t.strip() for t in tokens if t.strip()]

        if len(tokens) >= 2:
            values = [parse_single(t) for t in tokens[:2]]
            if all(v is not None for v in values):
                first, second = values
                if first < 100 and second >= 1_000_000 and 'm' in normalized.lower():
                    first *= 1_000_000
                elif first < 1000 and second >= 1_000 and 'k' in normalized.lower():
                    first *= 1_000
                return min(first, second), max(first, second)
        elif len(tokens) == 1:
            val = parse_single(tokens[0])
            if val is not None:
                return val, val

        return None

    def extract_signals(self, text: str) -> ExtractedSignals:
        signals = ExtractedSignals()

        # 1. Fund size
        fund_match = re.search(r'(\$[\d,.]+\s*(?:M|B|K|million|billion|thousand)\s+(?:Fund\s+[IVX]+|maiden\s+fund|fund))', text, re.IGNORECASE)
        if not fund_match:
            fund_match = re.search(r'(?:totaling|close of their|closed(?:\s+its)?|raising|raise|raised|new)\s+(?:a\s+)?(\$[\d,.]+\s*(?:M|B|K|million|billion|thousand))', text, re.IGNORECASE)
        if not fund_match:
            fund_match = re.search(r'(?:\b(?:maiden|new|first|second|third|fourth)\s+)?(\$[\d,.]+\s*(?:M|B|K|million|billion|thousand)\s+(?:Fund\s+[IVX]+|maiden\s+fund|fund))', text, re.IGNORECASE)
        if fund_match:
            signals.fund_size = fund_match.group(1).strip()

        # 2. Portfolio mentions
        portfolio_patterns = [
            r'(?:portfolio investments include|portfolio includes|early backers of|investments include)\s+([^.]+)',
            r'(?:round for|investment in|round in)\s+([A-Za-z0-9][A-Za-z0-9\-]*(?:\s+[A-Za-z0-9][A-Za-z0-9\-]*)?)'
        ]
        companies = []
        for pat in portfolio_patterns:
            for m in re.finditer(pat, text, re.IGNORECASE):
                snippet = m.group(1).strip()
                # Split commas / 'and'
                parts = re.split(r',|\band\b', snippet)
                for part in parts:
                    cleaned = re.sub(r'[\.\(\)]', '', part).strip(" ,;:-")
                    if cleaned and len(cleaned) > 2 and len(cleaned.split()) <= 3:
                        if cleaned.lower() not in ["we", "our", "the", "seed", "series a", "series b", "series c"]:
                            if cleaned not in companies:
                                companies.append(cleaned)
        signals.portfolio_companies = companies[:5]

        # 3. Leadership
        leader_patterns = [
            r'(?:General Partner|Managing Director|Managing Partner|Founded by|led by)\s+([A-Za-z][A-Za-z\'-]*(?:\s+[A-Za-z][A-Za-z\'-]*)+)',
            r'([A-Za-z][A-Za-z\'-]*(?:\s+[A-Za-z][A-Za-z\'-]*)+)\s+leads the'
        ]
        leaders = []
        for pat in leader_patterns:
            for m in re.finditer(pat, text, re.IGNORECASE):
                lead = m.group(1).strip()
                lead = re.sub(r'^(?:general partner|managing director|managing partner|founded by|led by)\s+', '', lead, flags=re.IGNORECASE)
                lead = re.sub(r'\s+leads(?:\s+the)?(?:\s+team)?$', '', lead, flags=re.IGNORECASE)
                if lead and lead not in leaders:
                    leaders.append(lead)
        signals.leadership = leaders

        return signals

    def extract(self, raw_record: dict[str, Any]) -> SourceObservation:
        text = raw_record.get("raw_text", "")
        raw_name = raw_record.get("raw_name", "Unknown")
        raw_website = raw_record.get("raw_website")
        source_id = raw_record.get("source_id", "src-000")
        source_name = raw_record.get("source_name", "Unknown Source")
        retrieved_at = raw_record.get("retrieved_at")
        meta = raw_record.get("metadata", {})
        confidence = meta.get("confidence", 1.0)

        sectors = self.extract_sectors(text)
        stages = self.extract_stages(text)
        hq, regions = self.extract_geography(text)
        ticket_size = self.extract_ticket_size(text)
        signals = self.extract_signals(text)

        return SourceObservation(
            source_id=source_id,
            source_name=source_name,
            retrieved_at=retrieved_at,
            raw_name=raw_name,
            raw_website=raw_website,
            confidence=confidence,
            sectors=sectors,
            stages=stages,
            geography=regions,
            headquarters=hq,
            ticket_size=ticket_size,
            signals=signals
        )


# ============================================================================
# 3. Entity Resolution & Deduplication
# ============================================================================

class DisjointSetUnion:
    """Disjoint Set Union (Union-Find) with path compression and union by rank."""

    def __init__(self):
        self.parent: dict[str, str] = {}
        self.rank: dict[str, int] = {}

    def find(self, item: str) -> str:
        if item not in self.parent:
            self.parent[item] = item
            self.rank[item] = 0
            return item
        if self.parent[item] != item:
            self.parent[item] = self.find(self.parent[item])
        return self.parent[item]

    def union(self, a: str, b: str):
        root_a = self.find(a)
        root_b = self.find(b)
        if root_a != root_b:
            if self.rank[root_a] < self.rank[root_b]:
                self.parent[root_a] = root_b
            elif self.rank[root_a] > self.rank[root_b]:
                self.parent[root_b] = root_a
            else:
                self.parent[root_b] = root_a
                self.rank[root_a] += 1


class EntityResolver:
    """
    Two-Tier Entity Deduplication:
    - Tier 1: Domain-based exact matching (cleaned canonical domains).
    - Tier 2: Normalized name fuzzy clustering using RapidFuzz with stopword filtering.
    """

    LEGAL_STOPWORDS = {
        "llc", "inc", "lp", "ltd", "corp", "corporation", "capital",
        "ventures", "venture", "vc", "partners", "fund", "group",
        "holdings", "the", "management", "co", "company"
    }

    @classmethod
    def normalize_domain(cls, url: str | None) -> str | None:
        if not url:
            return None

        raw_url = url.strip().lower()
        if not raw_url.startswith(("http://", "https://")):
            raw_url = "https://" + raw_url

        try:
            parsed = urlparse(raw_url)
            netloc = (parsed.hostname or parsed.netloc or parsed.path or "").strip().rstrip(".")
            if not netloc:
                return None

            netloc = re.sub(r'^www\.', '', netloc)
            netloc = netloc.split('/')[0].split(':')[0]
            parts = [p for p in netloc.split('.') if p]
            if len(parts) > 2:
                netloc = ".".join(parts[-2:])
            return netloc if "." in netloc else None
        except Exception:
            return None

    @classmethod
    def normalize_name(cls, name: str) -> str:
        # Lowercase and remove punctuation
        clean = re.sub(r'[^\w\s]', ' ', name.lower())
        tokens = clean.split()
        # Filter stopwords only if name has > 1 non-stopword token
        meaningful = [t for t in tokens if t not in cls.LEGAL_STOPWORDS]
        if meaningful:
            return " ".join(meaningful)
        return " ".join(tokens)

    def evaluate_match(self, obs_a: SourceObservation, obs_b: SourceObservation) -> tuple[bool, float, str, bool]:
        """
        Determines whether two observations represent the same investor entity,
        and flags whether the match is uncertain (e.g. unverified domain or borderline fuzzy).
        Returns: (is_duplicate: bool, confidence: float, reason: str, is_uncertain: bool)
        """
        # 1. Exact domain match
        dom_a = self.normalize_domain(obs_a.raw_website)
        dom_b = self.normalize_domain(obs_b.raw_website)
        if dom_a and dom_b and dom_a == dom_b:
            return True, 0.98, f"Exact root domain match: {dom_a}", False

        # 2. Normalized name comparison
        norm_a = self.normalize_name(obs_a.raw_name)
        norm_b = self.normalize_name(obs_b.raw_name)

        if norm_a == norm_b and len(norm_a) >= 4:
            is_uncertain = not (dom_a and dom_b)
            reason = f"Exact normalized entity name: '{norm_a}'"
            if is_uncertain:
                reason += " (uncorroborated by domain)"
            return True, 0.95, reason, is_uncertain

        # 3. Fuzzy similarity
        token_sort = fuzz.token_sort_ratio(norm_a, norm_b)
        raw_token_sort = fuzz.token_sort_ratio(obs_a.raw_name.lower(), obs_b.raw_name.lower())

        # Safeguard: prevent matching "Horizon" to "Apex Horizon"
        tokens_a = set(norm_a.split())
        tokens_b = set(norm_b.split())
        is_subset = tokens_a.issubset(tokens_b) or tokens_b.issubset(tokens_a)
        min_tokens = min(len(tokens_a), len(tokens_b))

        if min_tokens == 1 and not (norm_a == norm_b):
            # Only 1 meaningful token and not exact match: don't match on fuzzy
            return False, 0.0, "Single-token mismatch guard", False

        if token_sort >= 88 or raw_token_sort >= 88:
            conf = token_sort / 100.0
            is_uncertain = conf < 0.92 or not (dom_a and dom_b)
            return True, conf, f"High fuzzy similarity ({token_sort}%)", is_uncertain

        if is_subset and min_tokens >= 2:
            return True, 0.85, f"Multi-token subset match: '{norm_a}' in '{norm_b}'", True

        return False, 0.0, "Distinct entities", False

    def are_duplicates(self, obs_a: SourceObservation, obs_b: SourceObservation) -> tuple[bool, float, str]:
        """
        Determines whether two observations represent the same canonical investor entity.
        Returns: (is_duplicate: bool, confidence: float, reason: str)
        """
        is_dup, conf, reason, _ = self.evaluate_match(obs_a, obs_b)
        return is_dup, conf, reason

    def cluster_entities(self, observations: list[SourceObservation]) -> list[list[SourceObservation]]:
        dsu = DisjointSetUnion()

        for i in range(len(observations)):
            for j in range(i + 1, len(observations)):
                a = observations[i]
                b = observations[j]
                is_dup, _, _ = self.are_duplicates(a, b)
                if is_dup:
                    dsu.union(a.source_id, b.source_id)

        clusters_by_root: dict[str, list[SourceObservation]] = {}
        for obs in observations:
            root = dsu.find(obs.source_id)
            clusters_by_root.setdefault(root, []).append(obs)

        return list(clusters_by_root.values())


# ============================================================================
# 4. Conflict Resolution & Provenance Tracking
# ============================================================================

class ConflictResolver:
    """
    Analyzes all observations in an entity cluster to detect contradictions,
    reconcile diverse evidence, and synthesize a canonical record.
    """

    def analyze_and_reconcile(self, cluster: list[SourceObservation], entity_index: int) -> CanonicalInvestor:
        # Determine canonical name & aliases
        # Sort by source confidence and length
        sorted_by_conf = sorted(cluster, key=lambda o: (o.confidence, len(o.raw_name)), reverse=True)
        canonical_name = sorted_by_conf[0].raw_name
        aliases = sorted({o.raw_name for o in cluster if o.raw_name != canonical_name})

        # Canonical website
        domains = [o.raw_website for o in sorted_by_conf if o.raw_website]
        canonical_website = domains[0] if domains else None

        # Conflict Detection
        conflicts: list[ConflictReport] = []

        # 1. Ticket Size Conflict Analysis
        ticket_observations = [o for o in cluster if o.ticket_size and (o.ticket_size.min_usd or o.ticket_size.max_usd)]
        ticket_conflict = self._check_ticket_size_conflicts(ticket_observations)
        if ticket_conflict:
            conflicts.append(ticket_conflict)

        # 2. Stage Conflict / Divergence
        stage_conflict = self._check_stage_conflicts(cluster)
        if stage_conflict:
            conflicts.append(stage_conflict)

        # 3. Geography Discrepancy
        geo_conflict = self._check_geography_conflicts(cluster)
        if geo_conflict:
            conflicts.append(geo_conflict)

        # 4. Uncertain Matches Analysis
        uncertain_matches: list[UncertainMatchReport] = []
        if len(cluster) > 1:
            resolver = EntityResolver()
            for i in range(len(cluster)):
                for j in range(i + 1, len(cluster)):
                    obs_a = cluster[i]
                    obs_b = cluster[j]
                    is_dup, conf, reason, is_uncertain = resolver.evaluate_match(obs_a, obs_b)
                    if is_dup and is_uncertain:
                        uncertain_matches.append(UncertainMatchReport(
                            source_a_id=obs_a.source_id,
                            source_b_id=obs_b.source_id,
                            source_a_name=obs_a.raw_name,
                            source_b_name=obs_b.raw_name,
                            confidence=conf,
                            match_type="UNCORROBORATED_NAME_MATCH" if not (obs_a.raw_website and obs_b.raw_website) else "FUZZY_NAME_MATCH",
                            reason=reason,
                            suggested_action=f"Matched with {int(conf * 100)}% confidence based on '{reason}'. Manual review recommended because domain verification is absent."
                        ))

        # Synthesize Canonical Fields
        # Sectors: frequency-sorted union
        all_sectors: dict[str, int] = {}
        for o in cluster:
            for s in o.sectors:
                all_sectors[s] = all_sectors.get(s, 0) + 1
        sectors_resolved = sorted(all_sectors.keys(), key=lambda s: all_sectors[s], reverse=True)

        # Stages: ordered by venture lifecycle
        stage_order = {s: i for i, s in enumerate(InvestorExtractor.STAGE_TAXONOMY)}
        all_stages = set()
        for o in cluster:
            all_stages.update(o.stages)
        stages_resolved = sorted(all_stages, key=lambda s: stage_order.get(s, 99))

        # Geography: HQ and regions
        headquarters = None
        for o in sorted_by_conf:
            if o.headquarters:
                headquarters = o.headquarters
                break

        all_geography = set()
        for o in cluster:
            all_geography.update(o.geography)
        geography_resolved = sorted(all_geography)

        # Ticket size resolution
        resolved_ticket = self._resolve_ticket_size(ticket_observations, sorted_by_conf)

        # Signals consolidation
        resolved_signals = self._consolidate_signals(cluster)

        # Identify missing fields
        missing_fields = []
        if not resolved_ticket or resolved_ticket.formatted_range in ("Unknown", "Not specified"):
            missing_fields.append("ticket_size")
        if not geography_resolved and not headquarters:
            missing_fields.append("geography")
        if not stages_resolved:
            missing_fields.append("stages")
        if not sectors_resolved:
            missing_fields.append("sectors")
        if not canonical_website:
            missing_fields.append("website")

        # Entity status & Completeness
        status = self._determine_status(cluster, conflicts, missing_fields)
        completeness = self._calculate_completeness(sectors_resolved, stages_resolved, geography_resolved, resolved_ticket, canonical_website)

        entity_id = f"inv-{entity_index:03d}"

        return CanonicalInvestor(
            id=entity_id,
            canonical_name=canonical_name,
            aliases=aliases,
            canonical_website=canonical_website,
            sectors=sectors_resolved,
            stages=stages_resolved,
            geography=geography_resolved,
            headquarters=headquarters,
            ticket_size=resolved_ticket,
            signals=resolved_signals,
            sources=cluster,
            conflicts=conflicts,
            uncertain_matches=uncertain_matches,
            missing_fields=missing_fields,
            status=status,
            completeness_score=completeness
        )

    def _check_ticket_size_conflicts(self, observations: list[SourceObservation]) -> ConflictReport | None:
        if len(observations) < 2:
            return None

        # Compare min and max across sources
        vals = {}
        for o in observations:
            ts = o.ticket_size
            if ts:
                vals[o.source_name] = {
                    "min_usd": ts.min_usd,
                    "max_usd": ts.max_usd,
                    "formatted": ts.formatted_range,
                    "source_id": o.source_id
                }

        # Check if ranges are completely disjoint or have > 2x divergence
        mins = [v["min_usd"] for v in vals.values() if v["min_usd"] is not None]
        maxs = [v["max_usd"] for v in vals.values() if v["max_usd"] is not None]

        if not mins or not maxs:
            return None

        highest_min = max(mins)
        lowest_max = min(maxs)
        highest_max = max(maxs)

        # Conflict condition: disjoint ranges or substantial disparity (e.g. $500k vs $5M)
        is_disjoint = highest_min > lowest_max
        ratio_disparity = (highest_max / lowest_max) > 2.0 if lowest_max > 0 else False

        if is_disjoint or ratio_disparity:
            desc = "Discrepancy detected across sources: " + ", ".join(
                f"{src} reports {data['formatted']}" for src, data in vals.items()
            )
            return ConflictReport(
                field="ticket_size",
                severity="HIGH",
                description=desc,
                conflicting_values=vals,
                suggested_action="Review primary source filing vs media reports; possible multi-fund strategy or staged checks."
            )
        return None

    def _check_stage_conflicts(self, cluster: list[SourceObservation]) -> ConflictReport | None:
        stage_by_source = {o.source_name: o.stages for o in cluster if o.stages}
        if len(stage_by_source) < 2:
            return None

        # If one source ONLY has early stage and another ONLY has late stage, flag
        pure_early_sources = [src for src, stgs in stage_by_source.items() if all(s in ["Pre-Seed", "Seed"] for s in stgs)]
        pure_late_sources = [src for src, stgs in stage_by_source.items() if all(s in ["Series B", "Series C", "Growth", "Late-Stage"] for s in stgs)]

        if pure_early_sources and pure_late_sources:
            return ConflictReport(
                field="stages",
                severity="MEDIUM",
                description=f"Stage divergence: {pure_early_sources} reports early-stage only, whereas {pure_late_sources} reports growth/late-stage only.",
                conflicting_values=stage_by_source,
                suggested_action="Verify if investor operates distinct early-stage and growth/opportunity funds."
            )
        return None

    def _check_geography_conflicts(self, cluster: list[SourceObservation]) -> ConflictReport | None:
        hqs = {}
        for o in cluster:
            if o.headquarters:
                hqs[o.source_name] = o.headquarters

        unique_hqs = set(hqs.values())
        if len(unique_hqs) > 1:
            return ConflictReport(
                field="geography",
                severity="MEDIUM",
                description=f"Multiple distinct headquarters reported: {', '.join(f'{src}: {hq}' for src, hq in hqs.items())}.",
                conflicting_values=hqs,
                suggested_action="Confirm primary global headquarters vs regional satellite office."
            )
        return None

    def _resolve_ticket_size(self, ticket_obs: list[SourceObservation], sorted_by_conf: list[SourceObservation]) -> TicketSize | None:
        if not ticket_obs:
            return None

        # Prioritize highest confidence source
        best_obs = sorted_by_conf[0]
        if best_obs.ticket_size and (best_obs.ticket_size.min_usd or best_obs.ticket_size.max_usd):
            return best_obs.ticket_size

        return ticket_obs[0].ticket_size

    def _consolidate_signals(self, cluster: list[SourceObservation]) -> ExtractedSignals:
        portfolios = []
        leaderships = []
        fund_sizes = []

        for o in cluster:
            for p in o.signals.portfolio_companies:
                if p not in portfolios:
                    portfolios.append(p)
            for leader in o.signals.leadership:
                if leader not in leaderships:
                    leaderships.append(leader)
            if o.signals.fund_size and o.signals.fund_size not in fund_sizes:
                fund_sizes.append(o.signals.fund_size)

        primary_fund = fund_sizes[0] if fund_sizes else None

        return ExtractedSignals(
            portfolio_companies=portfolios,
            fund_size=primary_fund,
            leadership=leaderships,
            key_notes=[f"Aggregated from {len(cluster)} distinct data sources."]
        )

    def _determine_status(self, cluster: list[SourceObservation], conflicts: list[ConflictReport], missing: list[str]) -> str:
        if conflicts:
            return "CONFLICT_DETECTED"
        if len(missing) >= 2:
            return "INCOMPLETE_PROFILE"
        if len(cluster) > 1:
            return "VERIFIED_MULTI_SOURCE"
        return "SINGLE_SOURCE_UNVERIFIED"

    def _calculate_completeness(self, sectors: list[str], stages: list[str], geo: list[str], ticket: TicketSize | None, website: str | None) -> float:
        score = 0.0
        if sectors:
            score += 0.25
        if stages:
            score += 0.20
        if geo:
            score += 0.20
        if ticket and ticket.formatted_range not in ("Unknown", "Not specified"):
            score += 0.25
        if website:
            score += 0.10
        return round(score, 2)


# ============================================================================
# 5. High-Level Engine Orchestrator & Dataset Helpers
# ============================================================================

def get_default_dataset_path() -> Path:
    """Finds the dataset path reliably across varied working directories."""
    candidates = [
        Path(__file__).resolve().parent / "data" / "investors.json",
        Path(__file__).resolve().parent / "data" / "investors.csv",
        Path.cwd() / "data" / "investors.json",
        Path.cwd() / "data" / "investors.csv",
        Path.cwd() / "investors.json",
        Path.cwd() / "investors.csv",
        Path(__file__).resolve().parent.parent / "data" / "investors.json",
        Path(__file__).resolve().parent.parent / "data" / "investors.csv"
    ]
    for c in candidates:
        if c.exists():
            return c
    return Path(__file__).resolve().parent / "data" / "investors.json"


def _load_csv_dataset(path: Path) -> list[dict[str, Any]]:
    """Loads the synthetic investor dataset from CSV records while preserving metadata."""
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            record = {key: value for key, value in row.items()}
            metadata_value = record.get("metadata", "")
            if metadata_value:
                try:
                    metadata = ast.literal_eval(metadata_value)
                except (ValueError, SyntaxError):
                    metadata = {}
            else:
                metadata = {}
            if isinstance(metadata, dict):
                record["metadata"] = metadata
            else:
                record["metadata"] = {}
            record["raw_website"] = record.get("raw_website") or None
            rows.append(record)
    return rows


def load_default_dataset(path: Path | str | None = None) -> list[dict[str, Any]]:
    """Loads the synthetic investor dataset from JSON or CSV."""
    target_path = Path(path) if path else get_default_dataset_path()
    if not target_path.exists():
        raise FileNotFoundError(f"Investor dataset not found at: {target_path}")

    if target_path.suffix.lower() == ".csv":
        return _load_csv_dataset(target_path)

    import json
    with open(target_path, "r", encoding="utf-8") as f:
        return json.load(f)


class InvestorIntelligenceEngine:
    """
    Main API interface combining Extraction, Resolution, Conflict Tracking, and Querying.
    """

    def __init__(self):
        self.extractor = InvestorExtractor()
        self.resolver = EntityResolver()
        self.conflict_resolver = ConflictResolver()
        self.raw_records: list[dict[str, Any]] = []
        self.observations: list[SourceObservation] = []
        self.canonical_entities: list[CanonicalInvestor] = []

    def process_records(self, records: list[dict[str, Any]]) -> list[CanonicalInvestor]:
        self.raw_records = records

        # 1. Extraction Pipeline
        self.observations = [self.extractor.extract(rec) for rec in records]

        # 2. Entity Resolution / Clustering
        clusters = self.resolver.cluster_entities(self.observations)

        # 3. Conflict Resolution & Canonical Consolidation
        self.canonical_entities = [
            self.conflict_resolver.analyze_and_reconcile(cluster, idx + 1)
            for idx, cluster in enumerate(clusters)
        ]

        # Sort entities by canonical name
        self.canonical_entities.sort(key=lambda e: e.canonical_name)
        return self.canonical_entities

    def query(self,
              keyword: str | None = None,
              sector: str | None = None,
              stage: str | None = None,
              conflicts_only: bool = False) -> list[CanonicalInvestor]:
        results = self.canonical_entities

        if keyword:
            kw = keyword.lower()
            results = [
                e for e in results
                if kw in e.canonical_name.lower() or
                any(kw in alias.lower() for alias in e.aliases) or
                any(kw in p.lower() for p in e.signals.portfolio_companies)
            ]

        if sector:
            sec_low = sector.lower()
            results = [
                e for e in results
                if any(sec_low in s.lower() for s in e.sectors)
            ]

        if stage:
            stg_low = stage.lower()
            results = [
                e for e in results
                if any(stg_low in s.lower() for s in e.stages)
            ]

        if conflicts_only:
            results = [e for e in results if e.conflicts]

        return results

    def get_by_id(self, entity_id: str) -> CanonicalInvestor | None:
        for e in self.canonical_entities:
            if e.id == entity_id:
                return e
        return None

    def get_metrics(self) -> dict[str, Any]:
        total_raw = len(self.observations)
        total_entities = len(self.canonical_entities)
        dedup_rate = round((1 - (total_entities / total_raw)) * 100, 1) if total_raw > 0 else 0.0

        status_counts = {}
        conflict_types = {}
        for e in self.canonical_entities:
            status_counts[e.status] = status_counts.get(e.status, 0) + 1
            for c in e.conflicts:
                conflict_types[c.field] = conflict_types.get(c.field, 0) + 1

        avg_completeness = round(
            sum(e.completeness_score for e in self.canonical_entities) / total_entities, 2
        ) if total_entities > 0 else 0.0

        return {
            "total_raw_records": total_raw,
            "total_canonical_entities": total_entities,
            "deduplication_rate_percent": dedup_rate,
            "status_breakdown": status_counts,
            "conflicts_by_field": conflict_types,
            "average_completeness_score": avg_completeness,
            "total_uncertain_matches": sum(len(e.uncertain_matches) for e in self.canonical_entities)
        }
