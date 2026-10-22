"""Wait for the console to boot, checking both process and data readiness."""

import argparse
import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--phase", choices=("health", "create", "verify"), default="health",
                        help="Create test settings, then verify/remove them after a container restart")
    args = parser.parse_args()
    deadline = time.monotonic() + args.timeout
    last_error = "No response"
    while time.monotonic() < deadline:
        try:
            for path in ("/healthz", "/readyz"):
                with urllib.request.urlopen(args.base_url + path, timeout=3) as response:
                    if response.status != 200:
                        raise RuntimeError(f"{path}: HTTP {response.status}")
            break
        except (urllib.error.URLError, TimeoutError, RuntimeError, ConnectionError) as exc:
            last_error = str(exc)
            time.sleep(1)
    else:
        raise SystemExit(f"Console did not become healthy: {last_error}")
    if args.phase == "health":
        print("Container liveness and readiness passed")
        return

    password = os.environ.get("CONTROL_PANEL_PASSWORD", "")
    if not password:
        raise SystemExit("Set CONTROL_PANEL_PASSWORD to run the API smoke test")
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(path, *, method="GET", data=None, expected=200):
        request = urllib.request.Request(
            args.base_url + path,
            data=json.dumps(data).encode() if data is not None else None,
            method=method,
            headers={"Content-Type": "application/json", "X-Control-Panel": "1"},
        )
        try:
            response = client.open(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            if response.status != expected:
                raise SystemExit(f"{method} {path}: expected {expected}, got {response.status}")
            content = response.read()
            return json.loads(content) if content else None

    call("/api/profiles", expected=401)
    call("/api/login", method="POST", data={"password": password})
    call("/api/import-legacy", method="POST", expected=403)
    if args.phase == "create":
        if call("/api/profiles")["profiles"]:
            raise SystemExit("Use a fresh volume for this test; existing profiles were found")
        profile = call("/api/profiles", method="POST", expected=201, data={
            "name": "Container persistence smoke test",
            "discord_token": "dummy-not-a-real-discord-token",
            "default_model": "smoke-test-model",
            "providers": [{"id": "ollama", "type": "ollama", "name": "Offline test",
                           "base_url": "http://127.0.0.1:9", "api_key": "dummy-not-a-real-api-key"}],
        })
        if profile["discord_token"] or profile["providers"][0]["api_key"]:
            raise SystemExit("The API exposed saved credentials")
        print("Container auth, profile creation and credential masking passed; restart to verify persistence")
    else:
        profiles = call("/api/profiles")["profiles"]
        if len(profiles) != 1 or profiles[0]["name"] != "Container persistence smoke test":
            raise SystemExit("Saved profile was not restored from the volume after restart")
        profile = profiles[0]
        if not profile["has_discord_token"] or not profile["providers"][0]["has_api_key"]:
            raise SystemExit("Saved credentials were not restored")
        if profile["discord_token"] or profile["providers"][0]["api_key"]:
            raise SystemExit("The API exposed saved credentials")
        profile["name"] = "Container updated smoke test"
        updated = call(f"/api/profiles/{profile['id']}", method="PUT", data=profile)
        if updated["name"] != profile["name"] or not updated["has_discord_token"]:
            raise SystemExit("Profile update did not preserve the saved credential")
        call(f"/api/profiles/{profile['id']}", method="DELETE", expected=204)
        if call("/api/profiles")["profiles"]:
            raise SystemExit("Deleted test profile still exists")
        print("Container restart persistence, masked credential retention, update and deletion passed")


if __name__ == "__main__":
    main()
