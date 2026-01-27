import base64
import hashlib
import json
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from rich.console import Console

from strix.llm.antigravity.constants import (
    ANTIGRAVITY_CALLBACK_PORT,
    ANTIGRAVITY_CLIENT_ID,
    ANTIGRAVITY_CLIENT_SECRET,
    ANTIGRAVITY_REDIRECT_URI,
    ANTIGRAVITY_SCOPES,
    CODE_ASSIST_ENDPOINT_FALLBACKS,
    CODE_ASSIST_HEADERS,
)

console = Console()

ACCOUNTS_FILE = Path.home() / ".strix" / "antigravity-accounts.json"


def generate_pkce() -> tuple[str, str]:
    """Generates (verifier, challenge) for PKCE."""
    verifier = secrets.token_urlsafe(32)
    m = hashlib.sha256()
    m.update(verifier.encode("ascii"))
    challenge = base64.urlsafe_b64encode(m.digest()).decode("ascii").rstrip("=")
    return verifier, challenge


def encode_state(payload: dict[str, Any]) -> str:
    """Encodes state payload to base64url."""
    json_bytes = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(json_bytes).decode("ascii").rstrip("=")


def decode_state(state: str) -> dict[str, Any]:
    """Decodes state payload from base64url."""
    # Add padding back if necessary
    padding = 4 - (len(state) % 4)
    if padding != 4:
        state += "=" * padding
    json_bytes = base64.urlsafe_b64decode(state)
    return json.loads(json_bytes)


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handles the OAuth callback."""

    callback_code: Optional[str] = None
    callback_state: Optional[str] = None

    def do_GET(self):
        parsed_url = urlparse(self.path)
        if parsed_url.path == "/oauth-callback":
            query_params = parse_qs(parsed_url.query)
            if "code" in query_params and "state" in query_params:
                OAuthCallbackHandler.callback_code = query_params["code"][0]
                OAuthCallbackHandler.callback_state = query_params["state"][0]

                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(b"""
                    <html>
                    <head><title>Authentication Complete</title></head>
                    <body style="font-family: sans-serif; text-align: center; padding-top: 50px;">
                        <h1>Authentication Complete</h1>
                        <p>You can close this tab and return to the terminal.</p>
                    </body>
                    </html>
                """)
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing code or state parameter.")
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def log_message(self, format, *args):
        pass  # Suppress logging


def authenticate_account(project_id: str = "") -> Optional[dict[str, Any]]:
    """Performs the OAuth flow to authenticate a new account."""
    verifier, challenge = generate_pkce()
    state = encode_state({"verifier": verifier, "projectId": project_id})

    params = {
        "client_id": ANTIGRAVITY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": ANTIGRAVITY_REDIRECT_URI,
        "scope": " ".join(ANTIGRAVITY_SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }

    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    console.print(f"\n[bold green]Opening browser for authentication...[/bold green]")
    console.print(f"URL: {auth_url}\n")

    try:
        webbrowser.open(auth_url)
    except Exception:
        console.print("[yellow]Could not open browser automatically.[/yellow]")

    # Start local server to listen for callback
    server = HTTPServer(("localhost", ANTIGRAVITY_CALLBACK_PORT), OAuthCallbackHandler)
    server.timeout = 300  # 5 minutes timeout

    # Reset handler state
    OAuthCallbackHandler.callback_code = None
    OAuthCallbackHandler.callback_state = None

    console.print("[dim]Waiting for callback...[/dim]")

    while OAuthCallbackHandler.callback_code is None:
        server.handle_request()

    server.server_close()

    if not OAuthCallbackHandler.callback_code or not OAuthCallbackHandler.callback_state:
        console.print("[red]Authentication failed: missing code or state.[/red]")
        return None

    # Exchange code for token
    code = OAuthCallbackHandler.callback_code
    returned_state = OAuthCallbackHandler.callback_state

    decoded_state = decode_state(returned_state)
    if decoded_state.get("verifier") != verifier:
         console.print("[red]Authentication failed: state mismatch (verifier).[/red]")
         return None

    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": ANTIGRAVITY_CLIENT_ID,
        "client_secret": ANTIGRAVITY_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": ANTIGRAVITY_REDIRECT_URI,
        "code_verifier": verifier,
    }

    try:
        response = requests.post(token_url, data=data)
        response.raise_for_status()
        token_data = response.json()
    except Exception as e:
        console.print(f"[red]Failed to exchange token: {e}[/red]")
        return None

    # Get user info
    access_token = token_data["access_token"]
    user_info = {}
    try:
        user_info_resp = requests.get(
            "https://www.googleapis.com/oauth2/v1/userinfo?alt=json",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        if user_info_resp.ok:
            user_info = user_info_resp.json()
    except Exception:
        pass

    # Determine Project ID and Tier
    effective_project_id = project_id
    tier = "free"

    # Try to discover project/tier via loadCodeAssist
    account_info = fetch_account_info(access_token)
    if not effective_project_id:
        effective_project_id = account_info.get("projectId", "")
    tier = account_info.get("tier", "free")

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        console.print("[red]Missing refresh token in response.[/red]")
        return None

    # Stored refresh token includes project ID separator if needed,
    # but we store structured data in JSON, so we can keep them separate.
    # The typescript plugin does: `${refreshToken}|${effectiveProjectId || ""}`
    # We will just store them in the JSON object.

    return {
        "email": user_info.get("email"),
        "refreshToken": refresh_token,
        "projectId": effective_project_id,
        "tier": tier,
        "access": access_token,
        "expires": time.time() + token_data.get("expires_in", 3600),
        "addedAt": time.time() * 1000,
        "lastUsed": 0,
    }


def fetch_account_info(access_token: str) -> dict[str, str]:
    """Discovers project ID and tier."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        **CODE_ASSIST_HEADERS
    }

    detected_tier = "free"

    for base_endpoint in CODE_ASSIST_ENDPOINT_FALLBACKS:
        try:
            url = f"{base_endpoint}/v1internal:loadCodeAssist"
            resp = requests.post(url, headers=headers, json={
                "metadata": {
                    "ideType": "IDE_UNSPECIFIED",
                    "platform": "PLATFORM_UNSPECIFIED",
                    "pluginType": "GEMINI",
                }
            })

            if not resp.ok:
                continue

            data = resp.json()
            project_id = ""

            companion = data.get("cloudaicompanionProject")
            if isinstance(companion, str):
                project_id = companion
            elif isinstance(companion, dict):
                project_id = companion.get("id", "")

            # Check tier
            allowed_tiers = data.get("allowedTiers", [])
            if isinstance(allowed_tiers, list):
                for t in allowed_tiers:
                    if t.get("isDefault"):
                        tid = t.get("id", "")
                        if tid != "legacy-tier" and "free" not in tid and "zero" not in tid:
                            detected_tier = "paid"

            paid_tier = data.get("paidTier")
            if paid_tier and isinstance(paid_tier, dict):
                pid = paid_tier.get("id", "")
                if "free" not in pid and "zero" not in pid:
                    detected_tier = "paid"

            if project_id:
                return {"projectId": project_id, "tier": detected_tier}

        except Exception:
            pass

    return {"projectId": "", "tier": detected_tier}


def refresh_access_token(refresh_token: str) -> Optional[dict[str, Any]]:
    """Refreshes the access token."""
    url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": ANTIGRAVITY_CLIENT_ID,
        "client_secret": ANTIGRAVITY_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    try:
        response = requests.post(url, data=data)
        if not response.ok:
            return None
        return response.json()
    except Exception:
        return None


class AccountManager:
    def __init__(self):
        self.accounts: list[dict[str, Any]] = []
        self.active_index: int = 0
        self.load()

    def load(self):
        if ACCOUNTS_FILE.exists():
            try:
                with open(ACCOUNTS_FILE, "r") as f:
                    data = json.load(f)
                    self.accounts = data.get("accounts", [])
                    self.active_index = data.get("activeIndex", 0)
            except Exception:
                self.accounts = []

    def save(self):
        ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ACCOUNTS_FILE, "w") as f:
            json.dump({
                "version": 3,
                "accounts": self.accounts,
                "activeIndex": self.active_index
            }, f, indent=2)

    def add_account(self, account: dict[str, Any]):
        self.accounts.append(account)
        self.save()

    def get_current_account(self) -> Optional[dict[str, Any]]:
        if not self.accounts:
            return None
        if self.active_index >= len(self.accounts):
            self.active_index = 0
        return self.accounts[self.active_index]

    def rotate_account(self):
        if not self.accounts:
            return
        self.active_index = (self.active_index + 1) % len(self.accounts)
        self.save()
        console.print(f"[bold cyan]Rotated to account {self.active_index + 1}/{len(self.accounts)}[/bold cyan]")

    def get_valid_token(self) -> Optional[tuple[str, str]]:
        """Returns (access_token, project_id) for the current account, refreshing if needed."""
        account = self.get_current_account()
        if not account:
            return None

        expires = account.get("expires", 0)
        if time.time() >= expires - 60:  # Buffer of 60 seconds
            # Refresh
            console.print(f"[dim]Refreshing token for {account.get('email', 'unknown')}...[/dim]")
            new_tokens = refresh_access_token(account["refreshToken"])
            if new_tokens:
                account["access"] = new_tokens["access_token"]
                account["expires"] = time.time() + new_tokens["expires_in"]
                self.save()
            else:
                console.print(f"[red]Failed to refresh token for {account.get('email')}[/red]")
                # Maybe remove account or rotate? For now just fail.
                return None

        return account["access"], account.get("projectId", "")

    def mark_rate_limited(self, retry_after: int):
        # In the TS plugin, they track rate limits per model family.
        # For simplicity, we'll just rotate.
        self.rotate_account()
