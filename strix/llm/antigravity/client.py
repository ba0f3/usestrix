import asyncio
import json
import time
import uuid
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Optional

import httpx
from rich.console import Console

from strix.llm.antigravity.auth import AccountManager, authenticate_account
from strix.llm.antigravity.constants import (
    CODE_ASSIST_ENDPOINT_FALLBACKS,
    CODE_ASSIST_HEADERS,
)

console = Console()


class AntigravityClient:
    def __init__(self):
        self.account_manager = AccountManager()

    def ensure_auth(self):
        """Ensures at least one account is authenticated."""
        if not self.account_manager.accounts:
            console.print("[yellow]No Antigravity accounts found. Starting authentication...[/yellow]")
            while True:
                account = authenticate_account()
                if account:
                    self.account_manager.add_account(account)
                    console.print(f"[green]Authenticated {account.get('email')}[/green]")

                if len(self.account_manager.accounts) >= 1:
                     # In a real CLI loop we might want to ask, but for automated runs
                     # we should just proceed if we have at least one.
                     # But initially we want to prompt.
                     try:
                        if console.input("Add another account? (y/n): ").lower() != 'y':
                             break
                     except Exception:
                        break # Handle cases where input fails (e.g. non-interactive)
                else:
                    try:
                        if console.input("Retry authentication? (y/n): ").lower() != 'y':
                            raise Exception("Authentication failed and no accounts available.")
                    except Exception:
                         raise Exception("Authentication failed and no accounts available.")

    async def stream_generate_content(
        self,
        model: str,
        messages: list[dict[str, Any]],
        **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        # Run auth check in executor if it requires input?
        # Actually input() blocks, so we should probably assume auth is done
        # or do it synchronously before the async loop starts.
        # But we are in an async function.
        if not self.account_manager.accounts:
            await asyncio.to_thread(self.ensure_auth)

        # Convert messages
        contents = self._convert_messages_to_gemini(messages)

        gemini_body = {
            "contents": contents,
            "generationConfig": {
                "temperature": kwargs.get("temperature", 0.7),
                "maxOutputTokens": kwargs.get("max_tokens", 8192),
            }
        }

        effective_model = model.replace("antigravity/", "")

        max_retries = 3 * len(self.account_manager.accounts)
        attempts = 0

        last_error = None

        while attempts < max_retries:
            attempts += 1
            token, project_id = self.account_manager.get_valid_token()

            if not token:
                raise Exception("Could not get valid token.")

            wrapped_body = {
                "project": project_id,
                "model": effective_model,
                "userAgent": "antigravity",
                "requestId": str(uuid.uuid4()),
                "request": {
                    **gemini_body,
                    "sessionId": str(uuid.uuid4())
                }
            }

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                **CODE_ASSIST_HEADERS
            }

            # Try endpoints
            async with httpx.AsyncClient(timeout=60.0) as client:
                for endpoint in CODE_ASSIST_ENDPOINT_FALLBACKS:
                    url = f"{endpoint}/v1internal:streamGenerateContent?alt=sse"

                    try:
                        async with client.stream("POST", url, headers=headers, json=wrapped_body) as response:
                            if response.status_code == 429:
                                retry_after = int(response.headers.get("Retry-After", 1))
                                self.account_manager.mark_rate_limited(retry_after)
                                last_error = f"Rate limit (429) on {endpoint}"
                                break # Break inner loop to rotate account

                            if response.status_code >= 500:
                                last_error = f"Server error ({response.status_code}) on {endpoint}"
                                continue # Try next endpoint

                            response.raise_for_status()

                            async for line in response.aiter_lines():
                                if not line:
                                    continue
                                if line.startswith("data:"):
                                    json_str = line[5:].strip()
                                    if not json_str:
                                        continue
                                    try:
                                        data = json.loads(json_str)
                                        actual_response = data.get("response", data)
                                        candidates = actual_response.get("candidates", [])
                                        if candidates:
                                            content_part = candidates[0].get("content", {}).get("parts", [])
                                            if content_part:
                                                text = content_part[0].get("text", "")
                                                if text:
                                                    # Format to mimic OpenAI/Litellm chunk using objects
                                                    yield SimpleNamespace(
                                                        choices=[
                                                            SimpleNamespace(
                                                                delta=SimpleNamespace(content=text),
                                                                finish_reason=None
                                                            )
                                                        ],
                                                        usage=None
                                                    )

                                            # Handle finish reason if present
                                            finish_reason = candidates[0].get("finishReason")
                                            if finish_reason:
                                                 pass
                                    except Exception:
                                        pass
                        return

                    except Exception as e:
                        last_error = str(e)
                        continue

            # If we are here, we failed all endpoints for this account (or broke out due to 429)
            if last_error:
                console.print(f"[yellow]Attempt {attempts} failed: {last_error}[/yellow]")

        raise Exception(f"All accounts rate limited or failed. Last error: {last_error}")

    def _convert_messages_to_gemini(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        gemini_contents = []
        system_instruction = ""

        # Merge system prompts
        for msg in messages:
            if msg["role"] == "system":
                content = msg.get("content", "")
                if isinstance(content, str):
                    system_instruction += content + "\n"
                elif isinstance(content, list):
                    for item in content:
                        if item.get("type") == "text":
                            system_instruction += item.get("text", "") + "\n"

        # If system instruction exists, prepend it to the first user message
        # or handle it if we used v1beta. v1internal might not support system_instruction field.
        # We will prepend to first user message.

        current_role = None
        current_parts = []

        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            if role == "system":
                continue # Already handled

            parts = []
            if isinstance(content, str):
                parts.append({"text": content})
            elif isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        parts.append({"text": item.get("text", "")})

            if not parts:
                continue

            g_role = "user" if role == "user" else "model"

            if g_role == current_role:
                # Merge with previous
                current_parts.extend(parts)
            else:
                if current_role:
                    gemini_contents.append({
                        "role": current_role,
                        "parts": current_parts
                    })
                current_role = g_role
                current_parts = parts

        if current_role:
            gemini_contents.append({
                "role": current_role,
                "parts": current_parts
            })

        # Prepend system instruction to first user message
        if system_instruction and gemini_contents:
            if gemini_contents[0]["role"] == "user":
                gemini_contents[0]["parts"].insert(0, {"text": system_instruction})
            else:
                # First message is model? Should not happen usually.
                # Prepend a user message.
                gemini_contents.insert(0, {
                    "role": "user",
                    "parts": [{"text": system_instruction}]
                })
        elif system_instruction:
             # Only system instruction?
             gemini_contents.append({
                 "role": "user",
                 "parts": [{"text": system_instruction}]
             })

        return gemini_contents
