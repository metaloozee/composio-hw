#!/usr/bin/env python3
"""
Research Agent: Analyzes API documentation for 100 SaaS apps.
Extracts: auth method, self-serve status, API surface, buildability verdict.

Uses stdlib only (urllib + json + re). Falls back to heuristics when
pages can't be fetched. Designed to be run in parallel batches.
"""

import json
import re
import sys
import time
import urllib.request
import urllib.error
import ssl
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

BATCH = os.environ.get("BATCH", None)  # optional: only process one batch
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "results")
TIMEOUT = 15
USER_AGENT = "Composio-Research-Agent/1.0"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─── Signal detection ────────────────────────────────────────────

def detect_auth(text_lower, text):
    """Detect auth methods from page content."""
    methods = []

    # OAuth2 signals
    oauth_signals = ["oauth2", "oauth 2.0", "oauth2.0", "oauth_token", "authorization code",
                     "access_token", "grant_type", "refresh_token", "client_id", "client_secret",
                     "bearer token", "bearer access"]
    if any(s in text_lower for s in oauth_signals):
        methods.append("OAuth2")

    # API Key signals
    apikey_signals = ["api key", "api_key", "apikey", "x-api-key", "api token",
                      "api-token", "x-key", "private token"]
    if any(s in text_lower for s in apikey_signals):
        methods.append("API Key")

    # Basic Auth signals
    basic_signals = ["basic auth", "http basic", "basicauthentication",
                     "authorization: basic", "basic authorization"]
    if any(s in text_lower for s in basic_signals):
        methods.append("Basic Auth")

    # Bot token / Telegram-style
    bot_signals = ["bot token", "bot_token", "bot api token", "botfather"]
    if any(s in text_lower for s in bot_signals):
        methods.append("Bot Token")

    # JWT
    jwt_signals = ["jwt", "json web token", "jsonwebtoken"]
    if any(s in text_lower for s in jwt_signals):
        methods.append("JWT")

    # Session / cookie based
    session_signals = ["session cookie", "session authentication", "cookie-based"]
    if any(s in text_lower for s in session_signals):
        methods.append("Session")

    return methods or ["Unknown"]


def detect_selfserve(text_lower, text):
    """Detect whether credentials are self-serve or gated."""
    selfserve_signals = ["free tier", "free account", "create an app",
                         "developer account", "sign up", "get started",
                         "try for free", "sandbox", "test account",
                         "no credit card", "create app",
                         "get api key", "generate api key",
                         "personal access token", "quickstart",
                         "developer portal", "api playground"]
    gated_signals = ["contact sales", "contact us", "enterprise plan",
                     "request access", "business plan", "paid plan",
                     "talk to sales", "schedule a demo",
                     "apply for access", "approval", "partnership",
                     "premium", "custom pricing",
                     "speak with our team", "partner program",
                     "request demo", "request a demo"]

    selfserve_count = sum(1 for s in selfserve_signals if s in text_lower)
    gated_count = sum(1 for s in gated_signals if s in text_lower)

    if selfserve_count > gated_count:
        return "Self-serve"
    elif gated_count > selfserve_count:
        return "Gated"
    elif selfserve_count > 0:
        return "Self-serve (likely)"
    elif gated_count > 0:
        return "Gated (likely)"
    return "Unknown"


def detect_api_surface(text_lower, text):
    """Detect API surface: REST, GraphQL, MCP, breadth."""
    surface = []
    if "rest" in text_lower or "restful" in text_lower or "http api" in text_lower:
        surface.append("REST")
    if "graphql" in text_lower or "gql" in text_lower:
        surface.append("GraphQL")
    if "mcp" in text_lower and "server" in text_lower:
        surface.append("MCP")
    if "webhook" in text_lower or "web hook" in text_lower:
        surface.append("Webhooks")
    if "sdk" in text_lower or "client library" in text_lower:
        surface.append("SDK")
    return surface or ["Unknown"]


def estimate_breadth(text_lower, text, url):
    """Estimate API breadth based on signals in text."""
    endpoint_count = len(re.findall(r'(?:GET|POST|PUT|DELETE|PATCH)\s+', text, re.IGNORECASE))
    resource_signals = ["accounts", "contacts", "deals", "tickets", "messages",
                        "users", "orders", "products", "events", "tasks",
                        "projects", "issues", "subscriptions", "invoices"]

    # Count unique resource mentions
    resources_found = [r for r in resource_signals if r in text_lower]

    if endpoint_count > 30 or len(resources_found) > 8:
        return "Broad (50+ endpoints)"
    elif endpoint_count > 10 or len(resources_found) > 4:
        return "Moderate (20-50 endpoints)"
    elif endpoint_count > 0 or len(resources_found) > 1:
        return "Narrow (10-20 endpoints)"
    return "Narrow (<10 endpoints)"


def buildability_verdict(name, auth, selfserve, surface):
    """Determine if this can be an agent toolkit today."""
    blockers = []

    if "Unknown" in auth:
        blockers.append("Auth unknown")
    if selfserve in ("Gated", "Gated (likely)", "Unknown"):
        blockers.append(f"Access gate: {selfserve}")
    if "Unknown" in surface:
        blockers.append("API surface unclear")
    if not surface or surface == ["Unknown"]:
        blockers.append("No documented API")

    if not blockers:
        return "Ready", "No blockers"
    return "Blocked", "; ".join(blockers)


# ─── Fetch ────────────────────────────────────────────────────────

def fetch_url(url, timeout=TIMEOUT):
    """Fetch a URL and return text content."""
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html, application/json, text/plain, */*"
        })
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            # Read first 200KB
            content = b""
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                content += chunk
                if len(content) > 200000:
                    break
            encoding = resp.headers.get_content_charset() or "utf-8"
            return content.decode(encoding, errors="replace")
    except Exception as e:
        return None


def analyze_app(app):
    """Analyze a single app by fetching its docs and extracting signals."""
    name = app["name"]
    url = app["docs_url"]
    category = app["category"]

    result = {
        "id": app["id"],
        "name": name,
        "category": category,
        "website": app["website"],
        "docs_url": url,
        "what_it_does": "",
        "auth_methods": [],
        "selfserve": "Unknown",
        "api_surface": [],
        "api_breadth": "Unknown",
        "existing_mcp": False,
        "buildability": "Unknown",
        "blockers": [],
        "evidence_url": url,
        "fetch_success": False,
        "fetch_error": None
    }

    text = fetch_url(url)
    if text:
        result["fetch_success"] = True
        text_lower = text.lower()
    else:
        result["fetch_error"] = "Failed to fetch"
        text = ""
        text_lower = ""

    # Extract signals
    result["auth_methods"] = detect_auth(text_lower, text)
    result["selfserve"] = detect_selfserve(text_lower, text)
    result["api_surface"] = detect_api_surface(text_lower, text)
    result["api_breadth"] = estimate_breadth(text_lower, text, url)
    result["existing_mcp"] = "mcp" in text_lower and "server" in text_lower
    verdict, blocker_str = buildability_verdict(
        name, result["auth_methods"], result["selfserve"], result["api_surface"]
    )
    result["buildability"] = verdict
    result["blockers"] = blocker_str.split("; ") if blocker_str != "No blockers" else []

    # Try to extract a short description
    desc_match = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]+)"', text, re.IGNORECASE)
    if desc_match:
        result["what_it_does"] = desc_match.group(1)[:200]
    else:
        title_match = re.search(r'<title[^>]*>([^<]+)</title>', text, re.IGNORECASE)
        if title_match:
            result["what_it_does"] = title_match.group(1).strip()[:200]

    return result


# ─── Batch processing ─────────────────────────────────────────────

def process_batch(apps, batch_name):
    """Process a batch of apps in parallel."""
    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(analyze_app, app): app for app in apps}
        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
                print(f"  [{result['name']}] auth={result['auth_methods']} "
                      f"selfserve={result['selfserve']} surface={result['api_surface']} "
                      f"buildable={result['buildability']} fetch={result['fetch_success']}")
            except Exception as e:
                app = futures[future]
                print(f"  [{app['name']}] ERROR: {e}")

    results.sort(key=lambda r: r["id"])

    outpath = os.path.join(OUTPUT_DIR, f"batch_{batch_name}.json")
    with open(outpath, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n  Wrote {len(results)} results to {outpath}")
    return results


def main():
    with open(os.path.join(os.path.dirname(__file__), "..", "data", "apps.json")) as f:
        all_apps = json.load(f)

    if BATCH:
        # Process a specific batch (e.g., "1" for apps 1-10)
        batch_num = int(BATCH)
        start = (batch_num - 1) * 10
        end = start + 10
        apps = all_apps[start:end]
        print(f"\n{'='*60}")
        print(f"Processing batch {batch_num}: apps {start+1}-{end}")
        print(f"{'='*60}\n")
        results = process_batch(apps, f"{batch_num:02d}")
    else:
        # Process all in batches
        all_results = []
        batch_size = 10
        for i in range(0, len(all_apps), batch_size):
            batch = all_apps[i:i+batch_size]
            batch_num = (i // batch_size) + 1
            print(f"\n{'='*60}")
            print(f"Processing batch {batch_num}: apps {i+1}-{i+len(batch)}")
            print(f"{'='*60}\n")
            results = process_batch(batch, f"{batch_num:02d}")
            all_results.extend(results)

        # Merge all into one
        merged_path = os.path.join(OUTPUT_DIR, "all_results.json")
        with open(merged_path, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"\n{'='*60}")
        print(f"MERGED: {len(all_results)} apps -> {merged_path}")
        print(f"{'='*60}")

        # Summary stats
        stats = {"selfserve": 0, "gated": 0, "unknown_selfserve": 0,
                  "ready": 0, "blocked": 0, "unknown_build": 0,
                  "oauth2": 0, "apikey": 0, "basic": 0, "bot_token": 0,
                  "fetched": 0, "failed": 0}
        for r in all_results:
            if r["fetch_success"]: stats["fetched"] += 1
            else: stats["failed"] += 1
            if "Self-serve" in r["selfserve"]: stats["selfserve"] += 1
            elif "Gated" in r["selfserve"]: stats["gated"] += 1
            else: stats["unknown_selfserve"] += 1
            if r["buildability"] == "Ready": stats["ready"] += 1
            elif r["buildability"] == "Blocked": stats["blocked"] += 1
            else: stats["unknown_build"] += 1
            auths = [a.lower() for a in r["auth_methods"]]
            for a in auths:
                if "oauth" in a: stats["oauth2"] += 1
                if "api key" in a: stats["apikey"] += 1
                if "basic" in a: stats["basic"] += 1
                if "bot" in a: stats["bot_token"] += 1

        print("\nSUMMARY:")
        print(f"  Fetched: {stats['fetched']} | Failed: {stats['failed']}")
        print(f"  Self-serve: {stats['selfserve']} | Gated: {stats['gated']} | Unknown: {stats['unknown_selfserve']}")
        print(f"  Ready: {stats['ready']} | Blocked: {stats['blocked']} | Unknown: {stats['unknown_build']}")
        print(f"  Auth: OAuth2={stats['oauth2']} APIKey={stats['apikey']} Basic={stats['basic']} Bot={stats['bot_token']}")


if __name__ == "__main__":
    main()
