#!/usr/bin/env python3
"""
Composio-powered research pipeline.
Fetches app metadata from Composio v3.1 API, matches against our 100-app target list,
and produces a unified JSON dataset with auth methods, self-serve status, API breadth, and MCP readiness.

Usage: python3 agent/composio_research.py
"""

import json
import os
import ssl
import urllib.request
import urllib.error
import re
import time

COMPOSIO_API_KEY = "ak_dxQlIoP7K5sjtEUDuLGA"
COMPOSIO_BASE = "https://backend.composio.dev"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# ─── Slug mapping: our app names → Composio toolkit slugs ──────

# These are the slug mappings we need to find in Composio's registry.
# Based on Composio's naming conventions + known slugs.
SLUG_MAP = {
    "Salesforce": "salesforce",
    "HubSpot": "hubspot",
    "Pipedrive": "pipedrive",
    "Attio": "attio",
    "Twenty": "twenty",
    "Podio": "podio",
    "Zoho CRM": "zoho_crm",
    "Close": "close",
    "Copper": "copper",
    "DealCloud": "dealcloud",
    "Zendesk": "zendesk",
    "Intercom": "intercom",
    "Freshdesk": "freshdesk",
    "Front": "front",
    "Pylon": "pylon",
    "LiveAgent": "liveagent",
    "Plain": "plain",
    "Help Scout": "helpscout",
    "Gorgias": "gorgias",
    "Gladly": "gladly",
    "Slack": "slack",
    "Twilio": "twilio",
    "Zoho Cliq": "zoho_cliq",
    "Lark": "lark",
    "Pumble": "pumble",
    "Discord": "discord",
    "Telegram": "telegram",
    "WhatsApp Business": "whatsapp",
    "Aircall": "aircall",
    "Vonage": "vonage",
    "Google Ads": "google_ads",
    "Meta Ads": "facebook_ads",
    "LinkedIn Ads": "linkedin_ads",
    "GoHighLevel": "gohighlevel",
    "Mailchimp": "mailchimp",
    "Klaviyo": "klaviyo",
    "systeme.io": "systeme_io",
    "Pinterest": "pinterest",
    "Threads (Meta)": "threads",
    "SendGrid": "sendgrid",
    "Shopify": "shopify",
    "WooCommerce": "woocommerce",
    "BigCommerce": "bigcommerce",
    "Salesforce Commerce Cloud": "salesforce_commerce_cloud",
    "Magento (Adobe Commerce)": "magento",
    "Squarespace": "squarespace",
    "Ecwid": "ecwid",
    "Gumroad": "gumroad",
    "Amazon Selling Partner": "amazon_selling_partner",
    "fanbasis": "fanbasis",
    "DataForSEO": "dataforseo",
    "SE Ranking": "seranking",
    "Ahrefs": "ahrefs",
    "MrScraper": "mrscraper",
    "Apify": "apify",
    "Firecrawl": "firecrawl",
    "Bright Data": "bright_data",
    "Sherlock": "sherlock",
    "Waterfall.io": "waterfall",
    "Clay": "clay",
    "GitHub": "github",
    "Vercel": "vercel",
    "Netlify": "netlify",
    "Cloudflare": "cloudflare",
    "Supabase": "supabase",
    "Neo4j": "neo4j",
    "Snowflake": "snowflake",
    "MongoDB Atlas": "mongodb_atlas",
    "Datadog": "datadog",
    "Sentry": "sentry",
    "Notion": "notion",
    "Airtable": "airtable",
    "Linear": "linear",
    "Jira": "jira",
    "Asana": "asana",
    "Monday.com": "monday",
    "ClickUp": "clickup",
    "Coda": "coda",
    "Smartsheet": "smartsheet",
    "Harvest": "harvest",
    "Stripe": "stripe",
    "Plaid": "plaid",
    "Binance": "binance",
    "Paygent Connect": "paygent_connect",
    "iPayX": "ipayx",
    "QuickBooks": "quickbooks",
    "Xero": "xero",
    "Brex": "brex",
    "Ramp": "ramp",
    "PitchBook": "pitchbook",
    "NotebookLM": "notebooklm",
    "Otter AI": "otter_ai",
    "Fathom": "fathom",
    "Consensus": "consensus",
    "Reducto": "reducto",
    "Devin": "devin",
    "higgsfield": "higgsfield",
    "Mermaid CLI": "mermaid_cli",
    "YouTube Transcript": "youtube_transcript",
    "Grain": "grain",
}

# ─── Fetch all toolkits from Composio ──────────────────────────

def fetch_toolkits():
    """Fetch all toolkits from Composio v3.1 API."""
    url = f"{COMPOSIO_BASE}/api/v3.1/toolkits"
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={
        "x-api-key": COMPOSIO_API_KEY,
        "User-Agent": "Composio-Research-Agent/2.0"
    })
    resp = urllib.request.urlopen(req, timeout=30, context=ctx)
    data = json.loads(resp.read())
    return data.get("items", [])


# ─── Self-serve classifier ─────────────────────────────────────

def classify_selfserve(toolkit):
    """
    Classify whether the app is self-serve or gated based on Composio metadata.
    Composio tracks `no_auth`, `composio_managed_auth_schemes`, and tool counts.
    
    Heuristic:
    - If no_auth=True → Self-serve (no credentials needed)
    - If composio_managed_auth_schemes is non-empty → Self-serve (Composio handles auth)
    - If auth_schemes exists but tools_count=0 → Gated (registered but no active integration)
    - If tools_count >= 1 → Self-serve (working integration)
    - Otherwise → Unknown
    """
    meta = toolkit.get("meta", {})
    tools_count = meta.get("tools_count", 0)
    no_auth = toolkit.get("no_auth", False)
    managed = toolkit.get("composio_managed_auth_schemes", [])
    auth_schemes = toolkit.get("auth_schemes", [])
    deprecated = toolkit.get("deprecated")

    if deprecated:
        return "Deprecated"
    if no_auth:
        return "Self-serve"
    if managed and len(managed) > 0:
        return "Self-serve"
    if tools_count > 0:
        return "Self-serve (likely)"
    if auth_schemes and len(auth_schemes) > 0:
        return "Gated (likely)"
    return "Unknown"


def classify_breadth(toolkit):
    """Estimate API breadth from tools_count."""
    tc = toolkit.get("meta", {}).get("tools_count", 0)
    if tc >= 50:
        return "Broad (50+ actions)"
    elif tc >= 20:
        return "Moderate (20-50)"
    elif tc >= 1:
        return "Narrow (<20)"
    return "None"


def auth_methods_from_schemes(toolkit):
    """Convert auth_schemes to clean labels."""
    schemes = toolkit.get("auth_schemes", [])
    return [s.replace("_", " ").title() for s in schemes]


def buildability_verdict(toolkit):
    """Determine if this app is ready for agent toolkit."""
    tc = toolkit.get("meta", {}).get("tools_count", 0)
    deprecated = toolkit.get("deprecated")
    no_auth = toolkit.get("no_auth", False)
    auth = toolkit.get("auth_schemes", [])

    if deprecated:
        return "Deprecated", "Toolkit marked as deprecated in Composio"
    if tc == 0:
        return "Blocked", "No actions/tools available in Composio yet"
    if not auth and not no_auth:
        return "Blocked", "No auth scheme available"
    if tc < 3:
        return "Blocked", "Too few actions (<3)"
    return "Ready", ""


# ─── Main ──────────────────────────────────────────────────────

def main():
    print("Fetching all toolkits from Composio v3.1 API...")
    toolkits = fetch_toolkits()
    print(f"Got {len(toolkits)} toolkits from Composio")

    # Build lookup by slug
    by_slug = {t["slug"]: t for t in toolkits}
    by_name_lower = {t["name"].lower(): t for t in toolkits}

    # Match our 100 apps
    with open(os.path.join(OUTPUT_DIR, "apps.json")) as f:
        target_apps = json.load(f)

    results = []
    matched = 0
    unmatched = []

    for app in target_apps:
        slug = SLUG_MAP.get(app["name"], app["name"].lower().replace(" ", "_"))
        tk = by_slug.get(slug)

        # Try alternate slugs
        if not tk:
            alt_slugs = [
                app["name"].lower().replace(" ", ""),
                app["name"].lower().replace(" ", "_"),
                app["name"].lower().replace(" ", "-"),
                app["name"].split(" ")[0].lower(),
            ]
            for alt in alt_slugs:
                tk = by_slug.get(alt)
                if tk:
                    break

        # Try by name
        if not tk:
            tk = by_name_lower.get(app["name"].lower())

        # Try partial name match
        if not tk:
            name_parts = app["name"].lower().split()
            for slug_candidate, tk_candidate in by_slug.items():
                for part in name_parts:
                    if part in slug_candidate and len(part) > 3:
                        tk = tk_candidate
                        break
                if tk:
                    break

        if tk:
            matched += 1
            result = {
                "id": app["id"],
                "name": app["name"],
                "composio_name": tk["name"],
                "composio_slug": tk["slug"],
                "category": app["category"],
                "website": app["website"],
                "what_it_does": tk.get("meta", {}).get("description", ""),
                "auth_methods": auth_methods_from_schemes(tk),
                "composio_managed_auth": tk.get("composio_managed_auth_schemes", []),
                "selfserve": classify_selfserve(tk),
                "no_auth": tk.get("no_auth", False),
                "api_breadth": classify_breadth(tk),
                "tools_count": tk.get("meta", {}).get("tools_count", 0),
                "triggers_count": tk.get("meta", {}).get("triggers_count", 0),
                "categories": tk.get("meta", {}).get("categories", []),
                "deprecated": tk.get("deprecated") is not None,
                "buildability": buildability_verdict(tk)[0],
                "main_blocker": buildability_verdict(tk)[1],
                "has_mcp": None,  # We'll check separately
                "source": "composio_api",
                "composio_url": tk.get("meta", {}).get("app_url", ""),
            }
            results.append(result)
        else:
            unmatched.append(app["name"])
            results.append({
                "id": app["id"],
                "name": app["name"],
                "category": app["category"],
                "website": app["website"],
                "what_it_does": "",
                "auth_methods": ["Unknown"],
                "composio_managed_auth": [],
                "selfserve": "Unknown",
                "no_auth": False,
                "api_breadth": "Unknown",
                "tools_count": 0,
                "triggers_count": 0,
                "categories": [],
                "deprecated": False,
                "buildability": "Not in Composio",
                "main_blocker": "App not found in Composio registry",
                "has_mcp": None,
                "source": "unknown",
                "composio_url": "",
            })

    print(f"Matched: {matched}/100")
    if unmatched:
        print(f"Unmatched: {unmatched}")

    # Save
    outpath = os.path.join(OUTPUT_DIR, "composio_research.json")
    with open(outpath, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to {outpath}")

    # Summary stats
    ready = sum(1 for r in results if r["buildability"] == "Ready")
    blocked = sum(1 for r in results if r["buildability"] == "Blocked")
    deprecated = sum(1 for r in results if r["buildability"] == "Deprecated")
    not_found = sum(1 for r in results if r["buildability"] == "Not in Composio")
    selfserve = sum(1 for r in results if "Self-serve" in r.get("selfserve", ""))
    gated = sum(1 for r in results if "Gated" in r.get("selfserve", ""))
    oauth2 = sum(1 for r in results if any("oauth" in a.lower() for a in r["auth_methods"]))
    apikey = sum(1 for r in results if any("api_key" in a.lower() or "api key" in a.lower() for a in r["auth_methods"]))

    print(f"\n=== COMPOSIO RESEARCH SUMMARY ===")
    print(f"Total: {len(results)}")
    print(f"Ready: {ready} | Blocked: {blocked} | Deprecated: {deprecated} | Not in Composio: {not_found}")
    print(f"Self-serve: {selfserve} | Gated: {gated}")
    print(f"OAuth2: {oauth2} | API Key: {apikey}")
    print(f"Total tools (actions): {sum(r['tools_count'] for r in results)}")
    print(f"Total triggers: {sum(r['triggers_count'] for r in results)}")

    # Per-category breakdown
    print(f"\n=== BY CATEGORY ===")
    from collections import Counter
    cat_counts = Counter(r["category"] for r in results)
    for cat, count in cat_counts.most_common():
        ready_cat = sum(1 for r in results if r["category"] == cat and r["buildability"] == "Ready")
        print(f"  {cat}: {ready_cat}/{count} ready")


if __name__ == "__main__":
    main()
