#!/usr/bin/env python3
"""
Firecrawl-based verification agent.
Uses Firecrawl's /scrape endpoint to get clean markdown from each app's docs page,
then extracts structured signals for verification against the research agent's output.

Usage:
  python3 agent/firecrawl_verify.py              # scrape all 100
  python3 agent/firecrawl_verify.py --verify       # compare against research results
  python3 agent/firecrawl_verify.py --batch 1      # scrape batch 1 only
  python3 agent/firecrawl_verify.py --sample 20    # scrape random 20 for verification
"""

import json
import os
import sys
import time
import re
import random
import ssl
import urllib.request
import urllib.error

FIRECRAWL_API_KEY = "fc-d1dba3ee6cc449838708acecd9406eb5"
FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v1/scrape"
FIRECRAWL_MAP_URL = "https://api.firecrawl.dev/v1/map"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "firecrawl_results")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─── Firecrawl API helpers ──────────────────────────────────────

def firecrawl_request(url, payload, timeout=60):
    """Make a request to Firecrawl API."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Authorization": f"Bearer {FIRECRAWL_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "Composio-Research/2.0"
    }, method="POST")

    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return {"error": str(e.code), "message": body[:500]}
    except Exception as e:
        return {"error": str(e)[:200]}


def scrape_page(url, formats=None):
    """Scrape a single page via Firecrawl."""
    if formats is None:
        formats = ["markdown"]
    payload = {
        "url": url,
        "formats": formats,
        "waitFor": 1000,  # wait 1s for JS
        "timeout": 30000
    }
    return firecrawl_request(FIRECRAWL_SCRAPE_URL, payload)


def map_site(url):
    """Map a site's URLs via Firecrawl."""
    payload = {
        "url": url,
        "search": "api docs developer reference endpoints",
        "limit": 50
    }
    return firecrawl_request(FIRECRAWL_MAP_URL, payload)


# ─── Signal extraction from markdown ────────────────────────────

def extract_signals_from_markdown(md_text, url):
    """Extract structured signals from scraped markdown content."""
    md_lower = md_text.lower() if md_text else ""
    result = {
        "auth_methods": [],
        "selfserve": "Unknown",
        "api_surface": [],
        "api_breadth": "Unknown",
        "existing_mcp": False,
        "what_it_does": "",
        "evidence_url": url,
        "scrape_success": bool(md_text)
    }

    if not md_text:
        return result

    # ── Auth detection (more precise patterns) ──
    if re.search(r'\b(?:oauth\s*2|oauth2|oauth_2\.0|authorization\s*code\s*grant|grant_type|client_id.*client_secret|client\s*secret)', md_lower):
        result["auth_methods"].append("OAuth2")
    if re.search(r'\b(?:api[_\s-]?key|apikey|x-api-key|x-api-token|api\s*token\s*\(|generate.*api.*key|create.*api.*key|personal\s*access\s*token)', md_lower):
        result["auth_methods"].append("API Key")
    if re.search(r'\b(?:basic\s*auth|http\s*basic|authorization:\s*basic|basic\s*authentication|username.*password)', md_lower):
        result["auth_methods"].append("Basic Auth")
    if re.search(r'\b(?:bot\s*token|bot\s*api\s*token|botfather|telegram.*token)', md_lower):
        result["auth_methods"].append("Bot Token")
    if re.search(r'\bjwt\b|json\s*web\s*token|sign.*jwt', md_lower):
        result["auth_methods"].append("JWT")
    if re.search(r'\b(?:bearer\s*token|bearer\s*authentication)', md_lower):
        if "Bearer Token" not in result["auth_methods"]:
            result["auth_methods"].append("Bearer Token")

    if not result["auth_methods"]:
        result["auth_methods"] = ["Unknown"]

    # ── Self-serve vs gated ──
    selfserve_signals = [
        r'\b(?:free\s*tier|free\s*plan|free\s*account|no\s*credit\s*card|forever\s*free)',
        r'\b(?:create.*app|register.*app|developer.*account|sign\s*up.*free|get\s*started.*free)',
        r'\b(?:sandbox|test\s*environment|developer.*sandbox|test\s*account)',
        r'\b(?:api\s*playground|try\s*it|interactive\s*docs|api\s*explorer)',
        r'\b(?:quickstart|getting\s*started|5\s*minute|quick\s*start)',
        r'\b(?:generate.*token|create.*token|get.*api.*key|get.*access.*token)',
    ]
    gated_signals = [
        r'\b(?:contact\s*sales|contact\s*us|talk\s*to.*sales|speak\s*with.*team|schedule.*demo|request.*demo)',
        r'\b(?:enterprise\s*(?:plan|only|account|customer)|business\s*plan|paid\s*plan)',
        r'\b(?:request\s*access|apply\s*for.*access|approval\s*required|requires\s*approval)',
        r'\b(?:partnership|partner.*program|become\s*a\s*partner)',
        r'\b(?:custom\s*pricing|contact.*pricing|let\'s\s*talk|get\s*a\s*quote)',
        r'\b(?:sales.*only|enterprise.*only|subscription.*required|paid.*subscription)',
        r'\b(?:professional\s*plan.*required|upgrade.*to.*access|premium.*plan)',
    ]

    ss_score = sum(1 for p in selfserve_signals if re.search(p, md_lower))
    gated_score = sum(1 for p in gated_signals if re.search(p, md_lower))

    if ss_score >= 3 and gated_score == 0:
        result["selfserve"] = "Self-serve"
    elif ss_score >= 2 and gated_score <= 1:
        result["selfserve"] = "Self-serve (with caveats)"
    elif gated_score >= 2 and ss_score < 2:
        result["selfserve"] = "Gated"
    elif gated_score >= 1:
        result["selfserve"] = "Gated (likely)"
    elif ss_score >= 1:
        result["selfserve"] = "Self-serve (likely)"
    else:
        result["selfserve"] = "Unknown"

    # ── API surface ──
    if re.search(r'\b(?:rest\s*api|restful|http\s*api|rest\s*endpoint|base\s*url.*https?://)', md_lower):
        result["api_surface"].append("REST")
    if re.search(r'\b(?:graphql|gql|query\s*\{\s*\w)', md_lower):
        result["api_surface"].append("GraphQL")
    if re.search(r'\bmcp\b.*\bserver\b|\bserver\b.*\bmcp\b|model\s*context\s*protocol', md_lower):
        result["api_surface"].append("MCP")
        result["existing_mcp"] = True
    if re.search(r'\bwebhooks?\b|web\s*hook', md_lower):
        if "Webhooks" not in result["api_surface"]:
            result["api_surface"].append("Webhooks")
    if re.search(r'\b(?:sdk|client\s*library|npm\s*package|pip\s*install)', md_lower):
        result["api_surface"].append("SDK")

    if not result["api_surface"]:
        result["api_surface"] = ["Unknown"]

    # ── Breadth estimation ──
    endpoint_pattern = re.findall(
        r'(?:GET|POST|PUT|DELETE|PATCH)\s+[\`\'\"]?(?:https?://[^\s\`\'\"]+|/[\w/\-_{}]+)[\`\'\"]?',
        md_text, re.IGNORECASE
    )
    resource_terms = ["accounts?", "contacts?", "deals?", "tickets?", "messages?",
                      "users?", "orders?", "products?", "events?", "tasks?",
                      "projects?", "issues?", "subscriptions?", "invoices?",
                      "customers?", "payments?", "campaigns?", "leads?",
                      "reports?", "analytics?", "teams?", "organizations?"]

    resource_count = sum(1 for t in resource_terms if re.search(rf'\b{t}\b', md_lower))
    endpoint_count = len(endpoint_pattern)

    if endpoint_count > 40:
        result["api_breadth"] = "Broad (50+ endpoints)"
    elif endpoint_count > 15:
        result["api_breadth"] = "Moderate (20-50)"
    elif endpoint_count > 3:
        result["api_breadth"] = "Narrow (<20)"
    elif resource_count > 5:
        result["api_breadth"] = "Moderate (20-50)"
    elif resource_count > 2:
        result["api_breadth"] = "Narrow (<20)"
    else:
        result["api_breadth"] = "Narrow (<20)"

    # ── Description extraction ──
    desc_match = re.search(
        r'(?:^#\s+(.+?)(?:\n|$)|^(.+?)(?:API|platform|allows?.*to|enables?.*to|helps?.*to).{20,200})',
        md_text[:500], re.MULTILINE
    )
    if desc_match:
        desc = desc_match.group(1) or desc_match.group(2)
        result["what_it_does"] = desc.strip()[:200]

    return result


# ─── Main processing ─────────────────────────────────────────────

def process_app(app, scrape=True, delay=1.5):
    """Scrape and analyze one app."""
    name = app["name"]
    url = app["docs_url"]
    result = {
        "id": app["id"],
        "name": name,
        "category": app["category"],
        "website": app["website"],
        "docs_url": url,
        "firecrawl": {}
    }

    if scrape:
        print(f"  [{name}] Scraping {url}...")
        resp = scrape_page(url)

        if "error" in resp:
            print(f"    FAILED: {resp['error']} - {resp.get('message', '')[:100]}")
            result["firecrawl"]["error"] = resp["error"]
            result["firecrawl"]["message"] = resp.get("message", "")[:200]
        else:
            md = resp.get("data", {}).get("markdown", "")
            if md:
                print(f"    Got {len(md)} chars of markdown")
                result["firecrawl"]["markdown_length"] = len(md)
                result["firecrawl"]["markdown_preview"] = md[:300]
                signals = extract_signals_from_markdown(md, url)
                result["firecrawl"]["signals"] = signals
                print(f"    Signals: auth={signals['auth_methods']} "
                      f"ss={signals['selfserve']} surface={signals['api_surface']} "
                      f"breadth={signals['api_breadth']} mcp={signals['existing_mcp']}")
            else:
                print(f"    No markdown returned")
                result["firecrawl"]["error"] = "no_markdown"

        time.sleep(delay)  # rate limit

    return result


def verify_against_research(fc_result, research_result):
    """Compare firecrawl findings against research agent findings."""
    fc = fc_result.get("firecrawl", {}).get("signals", {})
    if not fc:
        return {"status": "no_firecrawl_data"}

    comparisons = {}

    # Auth comparison
    fc_auth = set(fc.get("auth_methods", []) or [])
    res_auth = set(research_result.get("auth_methods", []) or [])
    if fc_auth == res_auth:
        comparisons["auth"] = "match"
    elif fc_auth and res_auth:
        overlap = fc_auth & res_auth
        comparisons["auth"] = f"partial_match ({len(overlap)}/{max(len(fc_auth), len(res_auth))})"
    else:
        comparisons["auth"] = "mismatch"

    # Self-serve comparison
    fc_ss = fc.get("selfserve", "Unknown")
    res_ss = research_result.get("selfserve", "Unknown")
    comparisons["selfserve"] = "match" if fc_ss == res_ss else f"different (fc={fc_ss}, res={res_ss})"

    # API surface comparison
    fc_surface = set(fc.get("api_surface", []) or [])
    res_surface = set(research_result.get("api_surface", []) or [])
    if fc_surface == res_surface:
        comparisons["api_surface"] = "match"
    elif fc_surface and res_surface:
        overlap = fc_surface & res_surface
        comparisons["api_surface"] = f"partial ({len(overlap)}/{max(len(fc_surface), len(res_surface))})"
    else:
        comparisons["api_surface"] = "mismatch"

    # MCP comparison
    fc_mcp = fc.get("existing_mcp", False)
    res_mcp = research_result.get("existing_mcp", False)
    comparisons["mcp"] = "match" if fc_mcp == res_mcp else f"different (fc={fc_mcp}, res={res_mcp})"

    return comparisons


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, help="Process specific batch (1-10)")
    parser.add_argument("--sample", type=int, help="Process random sample of N apps")
    parser.add_argument("--verify", action="store_true", help="Compare against research results")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between requests")
    parser.add_argument("--limit", type=int, help="Limit total apps to process")
    args = parser.parse_args()

    with open(os.path.join(os.path.dirname(__file__), "..", "data", "apps.json")) as f:
        all_apps = json.load(f)

    # Select apps to process
    if args.sample:
        apps = random.sample(all_apps, min(args.sample, len(all_apps)))
        print(f"Processing random sample of {len(apps)} apps")
    elif args.batch:
        start = (args.batch - 1) * 10
        end = start + 10
        apps = all_apps[start:end]
        print(f"Processing batch {args.batch}: apps {start+1}-{end}")
    elif args.limit:
        apps = all_apps[:args.limit]
        print(f"Processing first {len(apps)} apps")
    else:
        apps = all_apps
        print(f"Processing ALL {len(apps)} apps")

    # Load research results for verification
    research_results = {}
    if args.verify:
        research_path = os.path.join(os.path.dirname(__file__), "..", "data", "results", "all_results.json")
        if os.path.exists(research_path):
            with open(research_path) as f:
                research_results = {r["id"]: r for r in json.load(f)}
            print(f"Loaded {len(research_results)} research results for verification")
        else:
            print("Research results not found, will skip verification")

    # Process
    results = []
    print(f"\n{'='*60}")
    print(f"FIRECRAWL RESEARCH PIPELINE - {len(apps)} apps")
    print(f"{'='*60}\n")

    for i, app in enumerate(apps):
        print(f"\n[{i+1}/{len(apps)}] {app['name']}")
        result = process_app(app, delay=args.delay)

        if research_results and app["id"] in research_results:
            verification = verify_against_research(result, research_results[app["id"]])
            result["verification"] = verification
            if verification.get("status") != "no_firecrawl_data":
                print(f"    VERIFY: {verification}")

        results.append(result)

    # Save
    tag = f"sample_{args.sample}" if args.sample else f"batch_{args.batch:02d}" if args.batch else "all"
    outpath = os.path.join(OUTPUT_DIR, f"firecrawl_{tag}.json")
    with open(outpath, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n{'='*60}")
    print(f"Results saved to {outpath}")
    print(f"{'='*60}")

    # Summary
    fc_success = sum(1 for r in results if r["firecrawl"].get("signals"))
    fc_fail = len(results) - fc_success

    if args.verify:
        matches = sum(1 for r in results if r.get("verification", {}).get("auth") == "match"
                      and r.get("verification", {}).get("selfserve") == "match")
        partial = sum(1 for r in results if r.get("verification", {}).get("auth", "").startswith("partial"))
        mismatches = sum(1 for r in results if "mismatch" in r.get("verification", {}).get("auth", ""))
        print(f"\nVERIFICATION SUMMARY:")
        print(f"  Firecrawl success: {fc_success}/{len(results)}")
        print(f"  Exact matches: {matches}")
        print(f"  Partial matches: {partial}")
        print(f"  Mismatches: {mismatches}")
        if fc_success > 0:
            print(f"  Accuracy: {matches}/{fc_success} = {100*matches/fc_success:.0f}% exact match")
    else:
        print(f"\nFirecrawl success: {fc_success}/{len(results)}")


if __name__ == "__main__":
    main()
