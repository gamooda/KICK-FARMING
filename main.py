import base64
import getpass
import hashlib
import http.server
import json
import os
import re
import secrets
import sys
import time
import webbrowser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

OAUTH_BASE = "https://id.kick.com"
API_BASE = "https://api.kick.com/public/v1"
REDIRECT_URI = "http://localhost:8765/callback"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 8765
SCOPES = "user:read"
APP_DIR = Path.home() / ".kick-farming"
SESSION_FILE = APP_DIR / "session.json"


class KickError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path != "/callback":
            self.send_error(404)
            return

        params = parse_qs(parsed.query)
        state = params.get("state", [""])[0]
        expected_state = getattr(self.server, "expected_state", "")

        if not secrets.compare_digest(state, expected_state):
            self.server.auth_error = "OAuth state check failed."
        elif params.get("error"):
            self.server.auth_error = params.get("error_description", params["error"])[0]
        else:
            self.server.auth_code = params.get("code", [""])[0]
            if not self.server.auth_code:
                self.server.auth_error = "Kick did not return an authorization code."

        message = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>KICK Watch Companion</title></head>"
            "<body style='font-family:Arial,sans-serif;background:#111;color:#eee;"
            "padding:48px'><h2>Kick connection response received.</h2>"
            "<p>You can return to KICK Watch Companion.</p></body></html>"
        )
        body = message.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string, *args):
        return


def request_json(url, method="GET", form=None, headers=None):
    request_headers = {"Accept": "application/json", "User-Agent": "KICK-Watch-Companion/1.0"}
    if headers:
        request_headers.update(headers)

    body = None
    if form is not None:
        body = urlencode(form).encode("utf-8")
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = Request(url, data=body, headers=request_headers, method=method)

    try:
        with urlopen(request, timeout=25) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(details)
            details = payload.get("message") or payload.get("error_description") or payload.get("error") or details
        except json.JSONDecodeError:
            pass
        raise KickError(f"HTTP {error.code}: {details}", error.code) from error
    except URLError as error:
        raise KickError(f"Network error: {error.reason}") from error
    except TimeoutError as error:
        raise KickError("The request timed out.") from error
    except json.JSONDecodeError as error:
        raise KickError("Kick returned an invalid JSON response.") from error


def load_session():
    try:
        with SESSION_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def save_session(data):
    APP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(str(SESSION_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    try:
        os.chmod(SESSION_FILE, 0o600)
    except OSError:
        pass


def delete_session():
    try:
        SESSION_FILE.unlink()
    except FileNotFoundError:
        pass


def create_pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def authorize(client_id):
    verifier, challenge = create_pkce_pair()
    state = secrets.token_urlsafe(32)

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{OAUTH_BASE}/oauth/authorize?{urlencode(params)}"

    try:
        server = http.server.HTTPServer((CALLBACK_HOST, CALLBACK_PORT), OAuthCallbackHandler)
    except OSError as error:
        raise KickError(
            f"Cannot start the local OAuth callback on port {CALLBACK_PORT}: {error}"
        ) from error

    server.timeout = 300
    server.expected_state = state
    server.auth_code = ""
    server.auth_error = ""

    print("\nOpening Kick authorization in your browser...")
    print("Sign in on kick.com and approve the requested access.")
    webbrowser.open(auth_url, new=1)

    try:
        server.handle_request()
    finally:
        server.server_close()

    if server.auth_error:
        raise KickError(f"Authorization failed: {server.auth_error}")
    if not server.auth_code:
        raise KickError("Authorization timed out. Run the program and try again.")

    token_data = request_json(
        f"{OAUTH_BASE}/oauth/token",
        method="POST",
        form={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
            "code": server.auth_code,
            "code_verifier": verifier,
        },
    )
    return token_data


def refresh_access_token(client_id, client_secret, refresh_token):
    return request_json(
        f"{OAUTH_BASE}/oauth/token",
        method="POST",
        form={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
    )


def normalize_token_data(token_data, previous=None):
    previous = previous or {}
    access_token = token_data.get("access_token")
    if not access_token:
        raise KickError("Kick did not return an access token.")

    expires_in = int(token_data.get("expires_in", 0))
    return {
        "client_id": CLIENT_ID,
        "access_token": access_token,
        "refresh_token": token_data.get("refresh_token") or previous.get("refresh_token"),
        "expires_at": time.time() + expires_in,
        "scope": token_data.get("scope") or previous.get("scope", SCOPES),
        "user": previous.get("user"),
    }


def api_get(path, access_token):
    return request_json(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Bearer {access_token}"},
    )


def get_profile(access_token):
    payload = api_get("/users", access_token)
    users = payload.get("data") or []
    if not users:
        raise KickError("Kick returned no user profile.")
    return users[0]


def connect(client_id, client_secret):
    previous = load_session()
    token_data = None

    if previous and previous.get("client_id") == client_id:
        if previous.get("access_token") and previous.get("expires_at", 0) > time.time() + 90:
            token_data = {
                "access_token": previous["access_token"],
                "refresh_token": previous.get("refresh_token"),
                "expires_in": max(0, int(previous["expires_at"] - time.time())),
                "scope": previous.get("scope", SCOPES),
            }
        elif previous.get("refresh_token"):
            print("Refreshing the saved Kick session...")
            try:
                token_data = refresh_access_token(
                    client_id, client_secret, previous["refresh_token"]
                )
            except KickError:
                token_data = None

    if token_data is None:
        token_data = authorize(client_id)

    session = normalize_token_data(token_data, previous)
    try:
        profile = get_profile(session["access_token"])
    except KickError as error:
        if error.status != 401 or not session.get("refresh_token"):
            raise
        token_data = refresh_access_token(
            client_id, client_secret, session["refresh_token"]
        )
        session = normalize_token_data(token_data, session)
        profile = get_profile(session["access_token"])

    session["user"] = {
        "user_id": profile.get("user_id"),
        "name": profile.get("name"),
    }
    save_session(session)
    return session, profile


def watch_stream():
    slug = input("\nKick channel name: ").strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,25}", slug):
        print("Invalid channel name.")
        return

    subscriber = input("Are you subscribed to this channel? [y/N]: ").strip().lower() in {"y", "yes"}
    start_time = time.monotonic()
    stream_url = f"https://kick.com/{slug}"

    if not webbrowser.open(stream_url, new=1):
        print(f"Open this link manually: {stream_url}")

    print("\nStream opened in your browser.")
    print("Watch the stream while logged into the same Kick account.")
    print("This timer is local only; Kick independently determines eligible watch time.")
    input("Press Enter when you finish watching...")

    elapsed = max(0, int(time.monotonic() - start_time))
    points_per_interval = 20 if subscriber else 10
    estimated_points = (elapsed // 300) * points_per_interval

    print(f"Session duration: {elapsed // 3600:02d}:{(elapsed % 3600) // 60:02d}:{elapsed % 60:02d}")
    print(f"Estimated Channel Points for this session: {estimated_points}")
    if elapsed >= 3600:
        print("One hour elapsed. Check Kick for Daily Rewards eligibility and claim it there.")
    else:
        remaining = 3600 - elapsed
        print(f"Time to one hour in this session: {remaining // 60} min {remaining % 60} sec")
    print("The estimate is not a confirmed Kick balance.")


def print_profile(profile):
    print("\nConnected account")
    print(f"Name: {profile.get('name') or 'Unknown'}")
    print(f"User ID: {profile.get('user_id') or 'Unknown'}")


def main():
    global CLIENT_ID, CLIENT_SECRET

    print("KICK WATCH COMPANION")
    print("Official OAuth connection and manual watch-time tracking")
    print("-" * 54)

    previous = load_session()
    default_client_id = os.getenv("KICK_CLIENT_ID", "")
    if not default_client_id and previous:
        default_client_id = previous.get("client_id", "")

    CLIENT_ID = default_client_id or input("Kick Client ID: ").strip()
    if not CLIENT_ID:
        raise KickError("Client ID is required.")

    CLIENT_SECRET = os.getenv("KICK_CLIENT_SECRET", "") or getpass.getpass("Kick Client Secret: ").strip()
    if not CLIENT_SECRET:
        raise KickError("Client Secret is required.")

    session, profile = connect(CLIENT_ID, CLIENT_SECRET)
    print_profile(profile)

    while True:
        print("\n1. Open a stream and track a watch session")
        print("2. Show connected account")
        print("3. Disconnect")
        print("0. Exit")
        choice = input("\nSelect: ").strip()

        if choice == "1":
            watch_stream()
        elif choice == "2":
            try:
                current_profile = get_profile(session["access_token"])
                print_profile(current_profile)
            except KickError as error:
                print(f"Unable to load profile: {error}")
        elif choice == "3":
            delete_session()
            session = None
            print("Local session removed. You can revoke app access in Kick settings.")
            break
        elif choice == "0":
            break
        else:
            print("Unknown option.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)
    except KickError as error:
        print(f"\nError: {error}")
        sys.exit(1)
