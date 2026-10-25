"""
Investor Intelligence Engine - CLI & Web Service Interface
==========================================================
Provides:
1. Command Line Interface (CLI) for running the pipeline, querying profiles,
   inspecting provenance, and detecting conflicts.
2. Lightweight FastAPI server for REST API endpoints and Swagger UI.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Ensure standard output can handle UTF-8 if supported
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from engine import (
    CanonicalInvestor,
    InvestorIntelligenceEngine,
    SourceObservation,
    get_default_dataset_path,
    load_default_dataset,
)

console = Console(highlight=False)

DEFAULT_DATA_PATH = get_default_dataset_path()


def load_and_run_engine(data_path: Path = DEFAULT_DATA_PATH) -> InvestorIntelligenceEngine:
    try:
        records = load_default_dataset(data_path)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    engine = InvestorIntelligenceEngine()
    engine.process_records(records)
    return engine


# ============================================================================
# CLI Presentation Helpers
# ============================================================================

def display_summary_table(entities: list[CanonicalInvestor]):
    table = Table(title="Investor Intelligence Engine - Resolved Profiles", show_header=True, header_style="bold cyan")
    table.add_column("ID", style="dim", width=9)
    table.add_column("Canonical Name", style="bold white", width=25)
    table.add_column("Stages", width=22)
    table.add_column("Sectors", width=24)
    table.add_column("Ticket Size", width=18)
    table.add_column("HQ / Geo", width=18)
    table.add_column("Sources", justify="center", width=8)
    table.add_column("Status", width=18)

    status_styles = {
        "VERIFIED_MULTI_SOURCE": "[bold green]VERIFIED[/bold green]",
        "CONFLICT_DETECTED": "[bold yellow]! CONFLICT[/bold yellow]",
        "SINGLE_SOURCE_UNVERIFIED": "[dim white]UNVERIFIED[/dim white]",
        "INCOMPLETE_PROFILE": "[bold red]INCOMPLETE[/bold red]"
    }

    for e in entities:
        status_str = status_styles.get(e.status, e.status)
        stages_str = ", ".join(e.stages) if e.stages else "[italic dim]None[/italic dim]"
        sectors_str = ", ".join(e.sectors[:3]) + (f" (+{len(e.sectors)-3})" if len(e.sectors) > 3 else "") if e.sectors else "[italic dim]None[/italic dim]"
        ticket_str = e.ticket_size.formatted_range if e.ticket_size else "[italic dim]Unknown[/italic dim]"
        geo_str = e.headquarters or (", ".join(e.geography[:2]) if e.geography else "[italic dim]Unknown[/italic dim]")

        table.add_row(
            e.id,
            e.canonical_name,
            stages_str,
            sectors_str,
            ticket_str,
            geo_str,
            str(len(e.sources)),
            status_str
        )

    console.print(table)


def display_entity_detail(e: CanonicalInvestor):
    console.print()
    console.print(Panel(f"[bold cyan]{e.canonical_name}[/bold cyan]  (ID: {e.id})", subtitle=f"Status: {e.status} | Completeness: {int(e.completeness_score * 100)}%"))

    # Metadata overview
    meta_table = Table(show_header=False, box=None)
    meta_table.add_column("Key", style="bold yellow", width=20)
    meta_table.add_column("Value")

    meta_table.add_row("Canonical Website", e.canonical_website or "N/A")
    meta_table.add_row("Known Aliases", ", ".join(e.aliases) if e.aliases else "None")
    meta_table.add_row("Stages", ", ".join(e.stages) if e.stages else "None")
    meta_table.add_row("Sectors", ", ".join(e.sectors) if e.sectors else "None")
    meta_table.add_row("Primary HQ", e.headquarters or "Unknown")
    meta_table.add_row("Target Regions", ", ".join(e.geography) if e.geography else "Unknown")
    meta_table.add_row("Resolved Ticket", e.ticket_size.formatted_range if e.ticket_size else "Unknown")
    meta_table.add_row("Fund Size", e.signals.fund_size or "N/A")
    meta_table.add_row("Portfolio Mentions", ", ".join(e.signals.portfolio_companies) if e.signals.portfolio_companies else "None")
    meta_table.add_row("Key Leadership", ", ".join(e.signals.leadership) if e.signals.leadership else "None")
    meta_table.add_row("Missing Fields", ", ".join(e.missing_fields) if e.missing_fields else "[green]None (Complete)[/green]")

    console.print(meta_table)

    # Conflicts section
    if e.conflicts:
        console.print("\n[bold red][!] CONFLICTS IDENTIFIED ACROSS SOURCES:[/bold red]")
        for c in e.conflicts:
            c_panel = Panel(
                f"[bold]Field:[/bold] {c.field} (Severity: [bold]{c.severity}[/bold])\n"
                f"[bold]Description:[/bold] {c.description}\n"
                f"[bold]Suggested Action:[/bold] {c.suggested_action}",
                title=f"[yellow]Conflict: {c.field.upper()}[/yellow]",
                border_style="yellow"
            )
            console.print(c_panel)

    # Uncertain Matches section
    if e.uncertain_matches:
        console.print("\n[bold yellow][?] UNCERTAIN MATCHES REQUIRING REVIEW:[/bold yellow]")
        for u in e.uncertain_matches:
            u_panel = Panel(
                f"[bold]Matched Pair:[/bold] {u.source_a_name} ({u.source_a_id}) ⟷ {u.source_b_name} ({u.source_b_id})\n"
                f"[bold]Confidence:[/bold] {int(u.confidence * 100)}% ({u.match_type})\n"
                f"[bold]Reason:[/bold] {u.reason}\n"
                f"[bold]Suggested Action:[/bold] {u.suggested_action}",
                title="[yellow]Uncertain Match Flag[/yellow]",
                border_style="yellow"
            )
            console.print(u_panel)

    # Source Provenance section
    console.print(f"\n[bold green]SOURCE OBSERVATIONS & PROVENANCE ({len(e.sources)} sources):[/bold green]")
    for i, s in enumerate(e.sources, 1):
        s_table = Table(title=f"Source #{i}: {s.source_name} (ID: {s.source_id})", show_header=False)
        s_table.add_column("Field", style="dim", width=18)
        s_table.add_column("Observation")
        s_table.add_row("Raw Name", s.raw_name)
        s_table.add_row("Raw Website", s.raw_website or "N/A")
        s_table.add_row("Confidence", f"{s.confidence:.2f}")
        s_table.add_row("Ticket Size", s.ticket_size.formatted_range if s.ticket_size else "None")
        s_table.add_row("Sectors", ", ".join(s.sectors) if s.sectors else "None")
        s_table.add_row("Stages", ", ".join(s.stages) if s.stages else "None")
        s_table.add_row("Geography", ", ".join(s.geography) if s.geography else "None")
        console.print(s_table)


# ============================================================================
# FastAPI Web Application with Lifespan
# ============================================================================

engine_instance: InvestorIntelligenceEngine | None = None


def get_engine() -> InvestorIntelligenceEngine:
    global engine_instance
    if engine_instance is None:
        engine_instance = load_and_run_engine()
    return engine_instance


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_engine()
    yield


app = FastAPI(
    title="Investor Intelligence Engine API",
    description="Production prototype converting unstructured investor data into structured, deduplicated profiles with conflict resolution.",
    version="1.0.0",
    lifespan=lifespan
)


class ExtractRequest(BaseModel):
    raw_text: str
    raw_name: str | None = "Unknown Investor"
    raw_website: str | None = None
    source_name: str | None = "Ad-hoc User Submission"


@app.get("/health")
def health_check():
    engine = get_engine()
    return {
        "status": "healthy",
        "service": "investor-intelligence-engine",
        "entities_loaded": len(engine.canonical_entities)
    }


@app.get("/api/investors", response_model=list[CanonicalInvestor])
def list_investors(
    q: str | None = Query(None, description="Search keyword for name, alias, or portfolio company"),
    sector: str | None = Query(None, description="Filter by sector (e.g. FinTech, B2B SaaS)"),
    stage: str | None = Query(None, description="Filter by stage (e.g. Seed, Series A)"),
    conflicts_only: bool = Query(False, description="Filter only entities with conflicting information")
):
    engine = get_engine()
    return engine.query(keyword=q, sector=sector, stage=stage, conflicts_only=conflicts_only)


@app.get("/api/investors/{entity_id}", response_model=CanonicalInvestor)
def get_investor(entity_id: str):
    engine = get_engine()
    entity = engine.get_by_id(entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail=f"Investor {entity_id} not found")
    return entity


@app.get("/api/stats")
def get_stats():
    engine = get_engine()
    return engine.get_metrics()


@app.post("/api/extract", response_model=SourceObservation)
def extract_adhoc(req: ExtractRequest):
    engine = get_engine()
    record = {
        "source_id": "adhoc-001",
        "source_name": req.source_name,
        "raw_name": req.raw_name,
        "raw_website": req.raw_website,
        "raw_text": req.raw_text,
        "metadata": {"confidence": 0.85}
    }
    return engine.extractor.extract(record)


# ============================================================================
# CLI Dispatcher
# ============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Investor Intelligence Engine - Extract, Deduplicate, and Resolve Investor Data",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Command: run
    run_parser = subparsers.add_parser("run", help="Run extraction & deduplication pipeline on dataset")
    run_parser.add_argument("--data", default=str(DEFAULT_DATA_PATH), help="Path to input json")

    # Command: query
    query_parser = subparsers.add_parser("query", help="Query resolved investor profiles")
    query_parser.add_argument("keyword", help="Search keyword (name, alias, portfolio)")
    query_parser.add_argument("--data", default=str(DEFAULT_DATA_PATH), help="Path to input json")

    # Command: filter
    filter_parser = subparsers.add_parser("filter", help="Filter investors by sector, stage, or conflict state")
    filter_parser.add_argument("--sector", help="Filter by sector")
    filter_parser.add_argument("--stage", help="Filter by investment stage")
    filter_parser.add_argument("--conflicts-only", action="store_true", help="Only show entities with conflicts")
    filter_parser.add_argument("--data", default=str(DEFAULT_DATA_PATH), help="Path to input json")

    # Command: inspect
    inspect_parser = subparsers.add_parser("inspect", help="Deep inspection of an investor ID (provenance & conflicts)")
    inspect_parser.add_argument("id", help="Canonical Investor ID (e.g., inv-001)")
    inspect_parser.add_argument("--data", default=str(DEFAULT_DATA_PATH), help="Path to input json")

    # Command: export
    export_parser = subparsers.add_parser("export", help="Export canonical profiles to JSON")
    export_parser.add_argument("--output", default="data/resolved_investors.json", help="Output JSON path")
    export_parser.add_argument("--data", default=str(DEFAULT_DATA_PATH), help="Path to input json")

    # Command: extract
    extract_parser = subparsers.add_parser("extract", help="Parse ad-hoc raw text snippet on the fly")
    extract_parser.add_argument("text", help="Raw investor blurb or text snippet")

    # Command: serve
    serve_parser = subparsers.add_parser("serve", help="Start FastAPI REST web service")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host address")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port")

    args = parser.parse_args()

    # Default action if no subcommand is provided: run pipeline
    if not args.command or args.command == "run":
        data_path = Path(args.data) if hasattr(args, "data") else DEFAULT_DATA_PATH
        engine = load_and_run_engine(data_path)
        display_summary_table(engine.canonical_entities)
        metrics = engine.get_metrics()
        console.print(f"\n[bold green]Pipeline completed:[/bold green] Ingested {metrics['total_raw_records']} raw records into {metrics['total_canonical_entities']} canonical entities ({metrics['deduplication_rate_percent']}% deduplication rate).")
        console.print(f"[bold yellow]Status breakdown:[/bold yellow] {metrics['status_breakdown']}")
        console.print(f"[bold red]Conflicts breakdown:[/bold red] {metrics['conflicts_by_field']}")
        return 0

    elif args.command == "query":
        engine = load_and_run_engine(Path(args.data))
        matches = engine.query(keyword=args.keyword)
        if not matches:
            console.print(f"[yellow]No investors found matching '{args.keyword}'.[/yellow]")
        else:
            display_summary_table(matches)
            for m in matches:
                display_entity_detail(m)
        return 0

    elif args.command == "filter":
        engine = load_and_run_engine(Path(args.data))
        matches = engine.query(sector=args.sector, stage=args.stage, conflicts_only=args.conflicts_only)
        console.print(f"[cyan]Found {len(matches)} matching investors:[/cyan]")
        display_summary_table(matches)
        return 0

    elif args.command == "inspect":
        engine = load_and_run_engine(Path(args.data))
        entity = engine.get_by_id(args.id)
        if not entity:
            console.print(f"[red]Investor ID '{args.id}' not found.[/red]")
            return 1
        display_entity_detail(entity)
        return 0

    elif args.command == "export":
        engine = load_and_run_engine(Path(args.data))
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump([e.model_dump() for e in engine.canonical_entities], f, indent=2)
        console.print(f"[bold green]Successfully exported {len(engine.canonical_entities)} canonical entities to {out_path}[/bold green]")
        return 0

    elif args.command == "extract":
        engine = InvestorIntelligenceEngine()
        obs = engine.extractor.extract({
            "source_id": "cli-001",
            "source_name": "CLI Manual Input",
            "raw_name": "Ad-hoc Investor",
            "raw_text": args.text
        })
        console.print(Panel("[bold cyan]Ad-Hoc Extracted Observation[/bold cyan]"))
        console.print(json.dumps(obs.model_dump(), indent=2))
        return 0

    elif args.command == "serve":
        global engine_instance
        engine_instance = load_and_run_engine()
        console.print(f"[bold green]Starting Investor Intelligence API on http://{args.host}:{args.port}[/bold green]")
        console.print(f"[dim]Interactive Swagger UI available at http://{args.host}:{args.port}/docs[/dim]")
        uvicorn.run(app, host=args.host, port=args.port)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
