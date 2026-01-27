from typing import Final

ANTIGRAVITY_CLIENT_ID: Final[str] = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
ANTIGRAVITY_CLIENT_SECRET: Final[str] = "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"
ANTIGRAVITY_CALLBACK_PORT: Final[int] = 36742
ANTIGRAVITY_REDIRECT_URI: Final[str] = f"http://localhost:{ANTIGRAVITY_CALLBACK_PORT}/oauth-callback"

ANTIGRAVITY_SCOPES: Final[list[str]] = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]

# Endpoints
CODE_ASSIST_ENDPOINT_DAILY: Final[str] = "https://daily-cloudcode-pa.sandbox.googleapis.com"
CODE_ASSIST_ENDPOINT_AUTOPUSH: Final[str] = "https://autopush-cloudcode-pa.sandbox.googleapis.com"
CODE_ASSIST_ENDPOINT_PROD: Final[str] = "https://cloudcode-pa.googleapis.com"

CODE_ASSIST_ENDPOINT_FALLBACKS: Final[list[str]] = [
    CODE_ASSIST_ENDPOINT_DAILY,
    CODE_ASSIST_ENDPOINT_AUTOPUSH,
    CODE_ASSIST_ENDPOINT_PROD,
]

CODE_ASSIST_ENDPOINT: Final[str] = CODE_ASSIST_ENDPOINT_DAILY

ANTIGRAVITY_USER_AGENT: Final[str] = "antigravity/1.11.5 windows/amd64"
ANTIGRAVITY_API_CLIENT: Final[str] = "google-cloud-sdk vscode_cloudshelleditor/0.1"
ANTIGRAVITY_CLIENT_METADATA: Final[str] = '{"ideType":"IDE_UNSPECIFIED","platform":"PLATFORM_UNSPECIFIED","pluginType":"GEMINI"}'

CODE_ASSIST_HEADERS: Final[dict[str, str]] = {
    "User-Agent": ANTIGRAVITY_USER_AGENT,
    "X-Goog-Api-Client": ANTIGRAVITY_API_CLIENT,
    "Client-Metadata": ANTIGRAVITY_CLIENT_METADATA,
}

MAX_ACCOUNTS: Final[int] = 10
