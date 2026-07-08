#!/usr/bin/env python3
"""
Final Composio Research Pipeline.
Fetches all 1000 toolkits from Composio v3.1, matches against our 100,
fixes the matching logic, and produces unified dataset with auth + breadth + self-serve.

Then merges with task-agent research and Firecrawl verification.
"""

import json, os, ssl, urllib.request, urllib.error, sys, time
from collections import Counter

COMPOSIO_API_KEY = "ak_dxQlIoP7K5sjtEUDuLGA"
COMPOSIO_BASE = "https://backend.composio.dev"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(OUTPUT_DIR, exist_ok=True)

def fetch_toolkits():
    url = f"{COMPOSIO_BASE}/api/v3.1/toolkits"
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"x-api-key": COMPOSIO_API_KEY, "User-Agent": "Composio-Research/3.0"})
    resp = urllib.request.urlopen(req, timeout=30, context=ctx)
    data = json.loads(resp.read())
    return data.get("items", [])

def build_match_index(toolkits):
    """Build multiple lookup indexes."""
    by_slug = {t["slug"]: t for t in toolkits}
    by_name_lower = {t["name"].lower(): t for t in toolkits}
    return by_slug, by_name_lower

def smart_match(app_name, by_slug, by_name_lower):
    """Try multiple strategies to match an app name to a toolkit."""
    name_lower = app_name.lower()

    # Strategy 1: exact slug
    for slug_candidate in [
        name_lower.replace(" ", "_"),
        name_lower.replace(" ", ""),
        name_lower.replace(" ", "-"),
    ]:
        if slug_candidate in by_slug:
            return by_slug[slug_candidate]

    # Strategy 2: exact name
    if name_lower in by_name_lower:
        return by_name_lower[name_lower]

    # Strategy 3: known alternates
    KNOWN_ALIASES = {
        "zoho crm": "zoho",
        "whatsapp business": "whatsapp",
        "meta ads": "facebook_ads",
        "threads (meta)": "threads",
        "magento (adobe commerce)": "magento",
        "salesforce commerce cloud": "salesforce_commerce_cloud",
        "mongodb atlas": "mongodb",
        "amazon selling partner": "amazon_seller_partner",
        "otter ai": "otter",
        "bright data": "bright_data",
        "lark": "lark_suite",
        "mermaid cli": "mermaid",
    }
    if name_lower in KNOWN_ALIASES:
        alias = KNOWN_ALIASES[name_lower]
        if alias in by_slug:
            return by_slug[alias]

    # Strategy 4: partial slug match
    parts = name_lower.split()
    for part in parts:
        if len(part) < 4:
            continue
        for slug, tk in by_slug.items():
            if part == slug or slug.startswith(part):
                # Don't match if too generic
                if part not in ("data", "cloud", "app", "api", "ads"):
                    return tk

    return None

def extract_result(app, toolkit):
    """Extract structured result from an app + its matched toolkit."""
    if toolkit is None:
        return {
            "id": app["id"], "name": app["name"], "category": app["category"],
            "website": app["website"], "composio_slug": None, "in_composio": False,
            "what_it_does": "", "auth_methods": ["Unknown"],
            "composio_managed_auth": [], "selfserve": "Unknown",
            "api_breadth": "Unknown", "tools_count": 0, "triggers_count": 0,
            "buildability": "Not in Composio", "main_blocker": "Not yet in Composio toolkit registry",
            "source": "external", "composio_url": ""
        }

    tk = toolkit
    meta = tk.get("meta", {})
    tc = meta.get("tools_count", 0)
    auth_schemes = tk.get("auth_schemes", [])
    managed_auth = tk.get("composio_managed_auth_schemes", [])
    no_auth = tk.get("no_auth", False)

    # Auth methods formatted nicely
    auth_formatted = [s.replace("_", " ").title() for s in auth_schemes]

    # Self-serve classification
    if no_auth:
        ss = "Self-serve"
    elif managed_auth:
        ss = "Self-serve"
    elif tc > 0:
        ss = "Self-serve (likely)"
    elif auth_schemes:
        ss = "Gated (likely)"
    else:
        ss = "Unknown"

    # Breadth
    if tc >= 50:
        breadth = f"Broad ({tc} actions)"
    elif tc >= 20:
        breadth = f"Moderate ({tc} actions)"
    elif tc >= 1:
        breadth = f"Narrow ({tc} actions)"
    else:
        breadth = "None"

    # Buildability
    if not auth_schemes and not no_auth:
        build, blocker = "Blocked", "No auth scheme configured"
    elif tc < 1:
        build, blocker = "Blocked", "0 actions/tools in Composio"
    elif tc < 3:
        build, blocker = "Blocked", f"Too few actions ({tc})"
    else:
        build, blocker = "Ready", ""

    return {
        "id": app["id"], "name": app["name"], "category": app["category"],
        "website": app["website"], "composio_slug": tk["slug"], "in_composio": True,
        "composio_name": tk["name"],
        "what_it_does": meta.get("description", ""),
        "auth_methods": auth_formatted,
        "composio_managed_auth": managed_auth,
        "selfserve": ss, "no_auth": no_auth,
        "api_breadth": breadth, "tools_count": tc,
        "triggers_count": meta.get("triggers_count", 0),
        "buildability": build, "main_blocker": blocker,
        "source": "composio_api",
        "composio_url": meta.get("app_url", ""),
    }

def main():
    print("=== COMPOSIO RESEARCH PIPELINE ===\n")

    # 1. Fetch all toolkits
    print("Fetching toolkits from Composio v3.1 API...")
    toolkits = fetch_toolkits()
    print(f"  Got {len(toolkits)} toolkits")

    by_slug, by_name_lower = build_match_index(toolkits)

    # 2. Load target apps
    with open(os.path.join(OUTPUT_DIR, "apps.json")) as f:
        target_apps = json.load(f)

    # 3. Match and extract
    print(f"\nMatching {len(target_apps)} target apps...")
    results = []
    matched = 0
    unmatched_names = []

    for app in target_apps:
        tk = smart_match(app["name"], by_slug, by_name_lower)
        if tk:
            matched += 1
        else:
            unmatched_names.append(app["name"])
        results.append(extract_result(app, tk))

    print(f"  Matched: {matched}/{len(target_apps)}")
    print(f"  Unmatched: {len(unmatched_names)}")
    if unmatched_names:
        print(f"  Unmatched apps: {unmatched_names}")

    # 4. Save results
    outpath = os.path.join(OUTPUT_DIR, "composio_research_v2.json")
    with open(outpath, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to {outpath}")

    # 5. Summary
    print("\n=== SUMMARY ===")
    ready = sum(1 for r in results if r["buildability"] == "Ready")
    blocked = sum(1 for r in results if r["buildability"] == "Blocked")
    not_found = sum(1 for r in results if r["buildability"] == "Not in Composio")
    ss = sum(1 for r in results if "Self-serve" in r.get("selfserve", ""))
    gated = sum(1 for r in results if "Gated" in r.get("selfserve", ""))

    auth_all = [a for r in results for a in r["auth_methods"]]
    oauth2 = sum(1 for a in auth_all if "oauth" in a.lower())
    apikey = sum(1 for a in auth_all if "api_key" in a.lower() or "api key" in a.lower())
    total_tools = sum(r["tools_count"] for r in results)
    total_triggers = sum(r["triggers_count"] for r in results)

    print(f"  Ready: {ready} | Blocked: {blocked} | Not in Composio: {not_found}")
    print(f"  Self-serve: {ss} | Gated: {gated}")
    print(f"  OAuth2: {oauth2} | API Key: {apikey}")
    print(f"  Total tools: {total_tools} | Total triggers: {total_triggers}")

    print("\n=== BY CATEGORY ===")
    for cat in sorted(set(r["category"] for r in results)):
        cat_results = [r for r in results if r["category"] == cat]
        cat_ready = sum(1 for r in cat_results if r["buildability"] == "Ready")
        cat_in = sum(1 for r in cat_results if r["in_composio"])
        print(f"  {cat}: {cat_ready}/{len(cat_results)} ready, {cat_in}/{len(cat_results)} in Composio")

    # Auth distribution
    print("\n=== AUTH DISTRIBUTION ===")
    auth_counter = Counter()
    for r in results:
        for a in r["auth_methods"]:
            if a != "Unknown":
                auth_counter[a] += 1
    for method, count in auth_counter.most_common():
        print(f"  {method}: {count}")

if __name__ == "__main__":
    main()
