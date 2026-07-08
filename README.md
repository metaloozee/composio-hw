# Composio Agent-Toolkit Readiness Research

Research pipeline that analyzes 100 SaaS APIs for agent-toolkit readiness: auth methods, self-serve vs gated access, API surface, and whether each app can be turned into an agent-callable toolkit today.

[**Live case study**](https://deploy-sigma-lovat.vercel.app) — the output page with the Readiness Matrix, patterns, and verification results.

## Quick start

```bash
# 1. Install Composio SDK (optional — the v3.1 API is called directly with curl, but the SDK is used for offline enums)
python3 -m pip install composio-core --break-system-packages

# 2. Export your Composio API key (required for the toolkit registry step)
export COMPOSIO_API_KEY="ak_YOUR_KEY_HERE"

# 3. Export your Firecrawl API key (required for the verification step)
export FIRECRAWL_API_KEY="fc_YOUR_KEY_HERE"

# 4. Run the research pipeline in order
python3 agent/composio_research_v2.py   # Step 1: fetch toolkit metadata from Composio v3.1
python3 agent/research_agent.py         # Step 2: scrape docs URLs with regex heuristics (pass 1)
python3 agent/firecrawl_verify.py       # Step 3: Firecrawl verification on a sample (pass 2)
python3 agent/build_html_v3.py          # Step 4: merge all sources + generate case_study.html
```

All output lands in `output/case_study.html` — a single self-contained page with the full table, charts, and verification report.

## What each script does

| Script | Input | Output | What it does |
|--------|-------|--------|-------------|
| `composio_research_v2.py` | `data/apps.json`, Composio API | `data/composio_research_v2.json` | Hits `/api/v3.1/toolkits`, matches our 100 apps by slug, extracts auth schemes, tool counts, descriptions |
| `research_agent.py` | `data/apps.json` | `data/results/batch_*.json` | Fetches each app's docs URL with urllib, runs regex to detect OAuth2 / API Key / self-serve signals, parallelized in batches of 10 |
| `firecrawl_verify.py` | `data/apps.json`, Firecrawl API | `data/firecrawl_results/` | Scrapes a random sample via Firecrawl's `/v1/scrape`, compares signals against research agent output, reports accuracy |
| `build_html_v3.py` | All of the above | `data/unified_research.json`, `output/case_study.html` | Merges Composio data (preferred), task-agent research, and Firecrawl verification into one dataset, then renders the full HTML page |

## Data flow

```
data/apps.json (100 target apps, hand-curated)
          │
          ├──→ composio_research_v2.py ──→ data/composio_research_v2.json (56 matched in registry)
          │
          ├──→ research_agent.py ──→ data/results/batch_*.json (regex-scraped signals, all 100)
          │
          └──→ firecrawl_verify.py ──→ data/firecrawl_results/ (20-app random sample verification)
                    │
                    ▼
          build_html_v3.py
                    │
                    ├──→ data/unified_research.json (merged dataset, 100 rows)
                    └──→ output/case_study.html (self-contained HTML)
```

## Key findings

- **88 of 100** apps are agent-toolkit ready today
- **56** are already in Composio's toolkit registry (live auth, managed actions)
- **OAuth2** (64 apps) and **API Key** (61) dominate; 30 support both
- **89%** are self-serve — a developer can get credentials for free or on a trial
- **Top blocker**: enterprise sales gate (Brex, PitchBook, DealCloud, LinkedIn Ads), not missing API endpoints
- **Easiest wins**: Vercel, Netlify, MongoDB Atlas, Monday.com, Smartsheet, Plaid, Binance — all have broad REST APIs, self-serve auth, and are not yet in Composio

## Requirements

- Python 3.12+
- No pip packages required (uses stdlib `urllib`, `json`, `ssl` for core agents)
- Optional: `composio-core` (for offline enums), `firecrawl` API key (for JS-rendered doc scraping)
- The Composio API v3.1 (`backend.composio.dev`) is queried directly with an x-api-key header — no SDK needed for the core pipeline

## Verification

Accuracy was measured across three passes:

| Pass | Method | Accuracy |
|------|--------|----------|
| 1 | Regex heuristics on raw HTML | ~35% |
| 2 | LLM-analyzed markdown from Exa fetch | ~75% |
| 3 | Composio registry ground truth + human login inspection | ~91% |

A random 20-app sample was Firecrawl-verified; 10 are shown with hits and misses in the case study. Human login inspection of 8 apps corrected 2 misclassifications (Podio, Gladly) and confirmed 6 others.

## Project structure

```
composio-hq/
├── agent/
│   ├── build_html_v3.py          # Final HTML generator (Composio data + Firecrawl + manual research)
│   ├── composio_research_v2.py   # Composio v3.1 API client
│   ├── research_agent.py         # Regex-based docs scraper (pass 1)
│   └── firecrawl_verify.py       # Firecrawl verification agent (pass 2)
├── data/
│   ├── apps.json                 # 100 target apps (source of truth)
│   ├── composio_research_v2.json # Composio API output (56 matched)
│   ├── unified_research.json     # Merged final dataset
│   └── results/                  # Batch outputs from research_agent.py
├── output/
│   └── case_study.html           # Final self-contained HTML deliverable
├── deploy/
│   └── index.html                # Deployed on Vercel
└── README.md
```
