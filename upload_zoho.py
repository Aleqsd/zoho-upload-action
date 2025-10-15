#!/usr/bin/env python3
"""
Upload to Zoho WorkDrive → make it public → emit a direct-download URL.

Requirements:
  pip install requests

Env vars (required):
  ZOHO_CLIENT_ID
  ZOHO_CLIENT_SECRET
  ZOHO_REFRESH_TOKEN
  ZOHO_FOLDER_ID          # destination folder (Team Folder recommandé)

Optional:
  ZOHO_API_BASE           # default: https://www.zohoapis.com/workdrive/api/v1
  ZOHO_ACCOUNTS_BASE      # default: https://accounts.zoho.com
"""

import argparse
import json
import mimetypes
import os
import sys
from typing import Optional

import requests

API_BASE = os.getenv("ZOHO_API_BASE", "https://www.zohoapis.com/workdrive/api/v1")
ACCOUNTS_BASE = os.getenv("ZOHO_ACCOUNTS_BASE", "https://accounts.zoho.com")

CLIENT_ID = os.getenv("ZOHO_CLIENT_ID")
CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN")
FOLDER_ID = os.getenv("ZOHO_FOLDER_ID")


def need(*names):
    missing = [n for n in names if not globals()[n]]
    if missing:
        sys.exit("❌ Missing env vars: " + ", ".join(missing))


def get_access_token() -> str:
    """Get a fresh access_token from refresh_token."""
    r = requests.post(
        f"{ACCOUNTS_BASE}/oauth/v2/token",
        data={
            "refresh_token": REFRESH_TOKEN,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    try:
        r.raise_for_status()
    except requests.HTTPError:
        sys.exit(f"❌ Token refresh failed: {r.status_code} {r.text}")
    token = r.json().get("access_token")
    if not token:
        sys.exit(f"❌ No access_token in refresh response: {r.text}")
    return token


def H(tok):
    return {"Authorization": f"Zoho-oauthtoken {tok}"}


def upload_file(tok: str, path: str, remote_name: Optional[str] = None) -> str:
    if not os.path.isfile(path):
        sys.exit(f"❌ File not found: {path}")
    url = f"{API_BASE}/upload"
    fn = remote_name or os.path.basename(path)
    ctype, _ = mimetypes.guess_type(fn)
    data = {"parent_id": FOLDER_ID}
    with open(path, "rb") as fh:
        files = {"content": (fn, fh, ctype or "application/octet-stream")}
        r = requests.post(url, headers=H(tok), files=files, data=data, timeout=120)
    try:
        r.raise_for_status()
    except requests.HTTPError:
        sys.exit(f"❌ Upload failed: {r.status_code} {r.text}")
    try:
        return r.json()["data"][0]["attributes"]["resource_id"]
    except Exception:
        sys.exit(f"❌ Unexpected upload response: {r.text}")


def share_everyone_view(tok: str, resource_id: str):
    """Grant 'Everyone on the internet' → View (file role_id=34)."""
    url = f"{API_BASE}/permissions"
    payload = {
        "data": {
            "type": "permissions",
            "attributes": {
                "resource_id": resource_id,
                "shared_type": "everyone",
                "role_id": "34",
            },
        }
    }
    r = requests.post(
        url,
        headers={
            **H(tok),
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=20,
    )
    try:
        r.raise_for_status()
    except requests.HTTPError:
        sys.exit(f"❌ Share everyone failed: {r.status_code} {r.text}")


def create_download_link(tok: str, resource_id: str) -> str:
    """Create an External Share Download Link and return base download_url."""
    url = f"{API_BASE}/links"
    payload = {
        "data": {
            "type": "links",
            "attributes": {
                "resource_id": resource_id,
                "link_type": "download",
                "link_name": "public_asset",
                "request_user_data": False,
                "allow_download": True,
            },
        }
    }
    r = requests.post(
        url,
        headers={
            **H(tok),
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    try:
        r.raise_for_status()
    except requests.HTTPError:
        sys.exit(f"❌ Create link failed: {r.status_code} {r.text}")
    try:
        return r.json()["data"]["attributes"]["download_url"]
    except Exception:
        sys.exit(f"❌ Unexpected link response: {r.text}")


def output_full(direct: str, base: str, resource_id: str):
    print(f"✓ Uploaded — resource_id = {resource_id}")
    print("\n🔗 Direct download URL (try this):")
    print(direct)
    print("\nFallback (also public, if direct returns 500):")
    print(base)
    print("\nHTML example (use direct if OK, else base):")
    print(f'<img src="{direct}" alt="img" />')


def output_json(direct: str, base: str, resource_id: str):
    payload = {
        "resource_id": resource_id,
        "direct_url": direct,
        "fallback_url": base,
        "html": f'<img src="{direct}" alt="img" />',
    }
    print(json.dumps(payload))


def append_output(path: str, key: str, value: str):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{key}={value}\n")


def main():
    need("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN", "FOLDER_ID")

    parser = argparse.ArgumentParser(
        description="Upload a file to Zoho WorkDrive and emit a public download URL."
    )
    parser.add_argument("file_path", help="Local file to upload.")
    parser.add_argument(
        "--stdout-mode",
        choices=("full", "direct", "json"),
        default="full",
        help="Controls what is printed to stdout (default: full).",
    )
    parser.add_argument(
        "--github-output",
        help="Optional path to append zoho_direct_url=<url> for GitHub Actions integration.",
    )
    parser.add_argument(
        "--output-key",
        default="zoho_direct_url",
        help="Key used when writing to --github-output or $GITHUB_OUTPUT (default: zoho_direct_url).",
    )
    parser.add_argument(
        "--remote-name",
        help="Optional remote filename to use instead of the local basename.",
    )

    args = parser.parse_args()

    token = get_access_token()

    # 1) Upload
    rid = upload_file(token, args.file_path, args.remote_name)

    # 2) Public for everyone (view)
    share_everyone_view(token, rid)

    # 3) Download link (public unsigned)
    base = create_download_link(token, rid)

    # 4) Direct URL (try ?directDownload=true first; keep base as fallback)
    sep = "&" if "?" in base else "?"
    direct = f"{base}{sep}directDownload=true"

    # stdout handling
    if args.stdout_mode == "full":
        output_full(direct, base, rid)
    elif args.stdout_mode == "direct":
        print(direct)
    elif args.stdout_mode == "json":
        output_json(direct, base, rid)

    target = args.github_output or os.getenv("GITHUB_OUTPUT")
    if target:
        append_output(target, args.output_key, direct)


if __name__ == "__main__":
    main()
