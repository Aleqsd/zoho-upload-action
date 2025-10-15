#!/usr/bin/env python3
"""
Upload to Zoho WorkDrive → publish for everyone → emit share links tailored for GitHub Actions.

Environment requirements
------------------------
  ZOHO_CLIENT_ID        # required
  ZOHO_CLIENT_SECRET    # required
  ZOHO_REFRESH_TOKEN    # required
  ZOHO_FOLDER_ID        # required
  ZOHO_REGION           # optional (us | eu | in | au | jp | cn); defaults to us
  ZOHO_API_BASE         # optional override for WorkDrive API endpoint
  ZOHO_ACCOUNTS_BASE    # optional override for Accounts OAuth endpoint
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import requests

# Terminal styling (GitHub Actions understands ANSI escapes).
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
CYAN = "\033[36m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
YELLOW = "\033[33m"
RED = "\033[31m"

REGION_ENDPOINTS: Dict[str, Tuple[str, str]] = {
    "us": ("https://www.zohoapis.com/workdrive/api/v1", "https://accounts.zoho.com"),
    "eu": ("https://www.zohoapis.eu/workdrive/api/v1", "https://accounts.zoho.eu"),
    "in": ("https://www.zohoapis.in/workdrive/api/v1", "https://accounts.zoho.in"),
    "au": ("https://www.zohoapis.com.au/workdrive/api/v1", "https://accounts.zoho.com.au"),
    "jp": ("https://www.zohoapis.jp/workdrive/api/v1", "https://accounts.zoho.jp"),
    "cn": ("https://www.zohoapis.com.cn/workdrive/api/v1", "https://accounts.zoho.com.cn"),
}
DEFAULT_REGION = "us"

CLIENT_ID = os.getenv("ZOHO_CLIENT_ID")
CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN")
FOLDER_ID = os.getenv("ZOHO_FOLDER_ID")


def color(text: str, ansi: str, enable: bool) -> str:
    return f"{ansi}{text}{RESET}" if enable else text


def log_line(message: str, ansi: str, enable: bool) -> None:
    if enable:
        print(color(message, ansi, True))


def need(*names: str) -> None:
    missing = [n for n in names if not globals()[n]]
    if missing:
        sys.exit(color("❌ Missing env vars: " + ", ".join(missing), RED, True))


def resolve_endpoints(region: str) -> Tuple[str, str, str]:
    region = region.lower()
    endpoint = REGION_ENDPOINTS.get(region, REGION_ENDPOINTS[DEFAULT_REGION])
    api_override = os.getenv("ZOHO_API_BASE")
    accounts_override = os.getenv("ZOHO_ACCOUNTS_BASE")
    api_base = (api_override or endpoint[0]).rstrip("/")
    accounts_base = (accounts_override or endpoint[1]).rstrip("/")
    return region, api_base, accounts_base


def get_access_token(accounts_base: str) -> str:
    response = requests.post(
        f"{accounts_base}/oauth/v2/token",
        data={
            "refresh_token": REFRESH_TOKEN,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError:
        sys.exit(
            color(
                f"❌ Token refresh failed: {response.status_code} {response.text}",
                RED,
                True,
            )
        )
    token = response.json().get("access_token")
    if not token:
        sys.exit(color(f"❌ No access_token in refresh response: {response.text}", RED, True))
    return token


def auth_header(token: str) -> Dict[str, str]:
    return {"Authorization": f"Zoho-oauthtoken {token}"}


def generate_unique_name(original_name: str, counter: int) -> str:
    stem, ext = os.path.splitext(original_name)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = f"{timestamp}" if counter == 1 else f"{timestamp}-{counter}"
    return f"{stem}-{suffix}{ext}"


def upload_file(
    api_base: str,
    token: str,
    path: str,
    remote_name: Optional[str],
    conflict_mode: str,
    max_retries: int,
    retry_delay: float,
    enable_logs: bool,
) -> Tuple[str, Optional[str], str]:
    if not os.path.isfile(path):
        sys.exit(color(f"❌ File not found: {path}", RED, True))
    url = f"{api_base}/upload"
    original_name = remote_name or os.path.basename(path)
    current_name = original_name
    rename_counter = 0
    override_existing = False

    while True:
        for attempt in range(1, max_retries + 1):
            log_line(
                f"⏳ Uploading '{current_name}' (attempt {attempt}/{max_retries})",
                CYAN,
                enable_logs,
            )
            content_type, _ = mimetypes.guess_type(current_name)
            data = {"parent_id": FOLDER_ID}
            if override_existing:
                data["override-name-exist"] = "true"
            try:
                with open(path, "rb") as handle:
                    files = {
                        "content": (
                            current_name,
                            handle,
                            content_type or "application/octet-stream",
                        )
                    }
                    response = requests.post(
                        url,
                        headers=auth_header(token),
                        files=files,
                        data=data,
                        timeout=120,
                    )
            except requests.RequestException as exc:
                if attempt == max_retries:
                    sys.exit(color(f"❌ Upload failed: {exc}", RED, True))
                log_line(f"🔁 Network error ({exc}); retrying in {retry_delay}s…", YELLOW, enable_logs)
                time.sleep(retry_delay)
                continue

            try:
                response.raise_for_status()
            except requests.HTTPError:
                status = response.status_code
                if status == 409:
                    if conflict_mode == "abort":
                        sys.exit(
                            color(
                                f"⚠️  File already exists: '{current_name}'. Set conflict_mode to rename or replace.",
                                YELLOW,
                                True,
                            )
                        )
                    if conflict_mode == "replace":
                        if override_existing:
                            sys.exit(
                                color(
                                    f"❌ Replace attempt failed again for '{current_name}'.",
                                    RED,
                                    True,
                                )
                            )
                        log_line("🔁 Existing file detected; overriding in place.", MAGENTA, enable_logs)
                        override_existing = True
                        break
                    if conflict_mode == "rename":
                        rename_counter += 1
                        if rename_counter > 10:
                            sys.exit(
                                color(
                                    "❌ Too many rename attempts triggered by name conflicts.",
                                    RED,
                                    True,
                                )
                            )
                        new_name = generate_unique_name(original_name, rename_counter)
                        log_line(f"♻️  Conflict detected; retrying with '{new_name}'.", MAGENTA, enable_logs)
                        current_name = new_name
                        break
                elif status >= 500 and attempt < max_retries:
                    log_line(
                        f"🔁 Zoho responded with {status}; retrying in {retry_delay}s…",
                        YELLOW,
                        enable_logs,
                    )
                    time.sleep(retry_delay)
                    continue
                else:
                    sys.exit(color(f"❌ Upload failed: {status} {response.text}", RED, True))
            else:
                payload = response.json()
                try:
                    item = payload["data"][0]
                    attributes = item.get("attributes", {})
                    resource_id = item.get("id") or attributes.get("resource_id")
                    if not resource_id:
                        raise KeyError("resource_id")
                    permalink = attributes.get("Permalink")
                    return resource_id, permalink, current_name
                except Exception:
                    sys.exit(color(f"❌ Unexpected upload response: {payload}", RED, True))
        else:
            sys.exit(color("❌ Upload failed after exhausting retries.", RED, True))
        # conflict handled via break; loop to retry
        continue


def share_everyone_view(api_base: str, token: str, resource_id: str, max_retries: int, retry_delay: float, enable_logs: bool) -> None:
    url = f"{api_base}/permissions"
    payload = {
        "data": {
            "type": "permissions",
            "attributes": {
                "resource_id": resource_id,
                "shared_type": "everyone",
                "role_id": "34",  # View
            },
        }
    }
    headers = {
        **auth_header(token),
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/json",
    }
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=20)
            response.raise_for_status()
            log_line("🌍 Public permissions applied.", GREEN, enable_logs)
            return
        except requests.HTTPError as http_err:
            status = http_err.response.status_code
            if status >= 500 and attempt < max_retries:
                log_line(f"🔁 Share API error {status}; retrying in {retry_delay}s…", YELLOW, enable_logs)
                time.sleep(retry_delay)
                continue
            sys.exit(color(f"❌ Share everyone failed: {status} {http_err.response.text}", RED, True))
        except requests.RequestException as exc:
            if attempt == max_retries:
                sys.exit(color(f"❌ Share everyone failed: {exc}", RED, True))
            log_line(f"🔁 Share request error ({exc}); retrying in {retry_delay}s…", YELLOW, enable_logs)
            time.sleep(retry_delay)


def create_external_link(api_base: str, token: str, resource_id: str, max_retries: int, retry_delay: float, enable_logs: bool) -> str:
    url = f"{api_base}/links"
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
    headers = {
        **auth_header(token),
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/json",
    }
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            log_line("🔗 External download link created.", GREEN, enable_logs)
            return response.json()["data"]["attributes"]["download_url"]
        except requests.HTTPError as http_err:
            status = http_err.response.status_code
            if status >= 500 and attempt < max_retries:
                log_line(f"🔁 Link API error {status}; retrying in {retry_delay}s…", YELLOW, enable_logs)
                time.sleep(retry_delay)
                continue
            sys.exit(color(f"❌ Create link failed: {status} {http_err.response.text}", RED, True))
        except requests.RequestException as exc:
            if attempt == max_retries:
                sys.exit(color(f"❌ Create link failed: {exc}", RED, True))
            log_line(f"🔁 Link request error ({exc}); retrying in {retry_delay}s…", YELLOW, enable_logs)
            time.sleep(retry_delay)
    sys.exit(color("❌ Unable to create download link after retries.", RED, True))


def compose_links(base_url: str, link_mode: str) -> Dict[str, Optional[str]]:
    preview_url = base_url.replace("/download", "/preview", 1) if "/download" in base_url else base_url
    sep = "&" if "?" in base_url else "?"
    direct_url = f"{base_url}{sep}directDownload=true"
    selected: Dict[str, Optional[str]] = {}
    if link_mode in ("both", "direct"):
        selected["direct"] = direct_url
    if link_mode in ("both", "preview"):
        selected["preview"] = preview_url
    return selected


def build_html_snippet(direct_url: Optional[str]) -> Optional[str]:
    if not direct_url:
        return None
    return f'<img src="{direct_url}" alt="WorkDrive asset" />'


def append_outputs(path: str, pairs: Dict[str, str]) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in pairs.items():
            handle.write(f"{key}={value}\n")


def output_full(
    *,
    resource_id: str,
    region: str,
    share_mode: str,
    link_mode: str,
    links: Dict[str, Optional[str]],
    html_snippet: Optional[str],
    api_base: str,
    enable_color: bool,
) -> None:
    print(
        color("✅ Upload complete", GREEN, enable_color)
        + " — "
        + color(resource_id, BOLD, enable_color)
    )
    if "direct" in links and links["direct"]:
        print(
            "\n"
            + color("⚡ Direct download", CYAN, enable_color)
            + f": {links['direct']}"
        )
    if "preview" in links and links["preview"]:
        print(
            "\n"
            + color("🖥️  WorkDrive share", BLUE, enable_color)
            + f": {links['preview']}"
        )
    if html_snippet:
        print(
            "\n"
            + color("🧩 HTML embed", MAGENTA, enable_color)
            + f":\n{html_snippet}"
        )
    print(
        "\n"
        + color("ℹ️  Context", YELLOW, enable_color)
        + f": region={region.upper()} · share_mode={share_mode} · link_mode={link_mode} · api_base={api_base}"
    )


def output_json(resource_id: str, links: Dict[str, Optional[str]], html_snippet: Optional[str]) -> None:
    payload = {
        "resource_id": resource_id,
        "direct_url": links.get("direct"),
        "preview_url": links.get("preview"),
        "html": html_snippet,
    }
    print(json.dumps(payload))


def main() -> None:
    need("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN", "FOLDER_ID")

    parser = argparse.ArgumentParser(
        description="Upload a file to Zoho WorkDrive and emit public URLs."
    )
    parser.add_argument("file_path", help="Local file to upload.")
    parser.add_argument(
        "--stdout-mode",
        choices=("full", "direct", "json"),
        default="full",
        help="Controls stdout (full logs, direct URL only, or JSON payload).",
    )
    parser.add_argument(
        "--github-output",
        help="Path to the GitHub output file (falls back to $GITHUB_OUTPUT).",
    )
    parser.add_argument(
        "--output-key",
        default="zoho_direct_url",
        help="Primary output key for backward compatibility (default: zoho_direct_url).",
    )
    parser.add_argument(
        "--remote-name",
        help="Optional remote filename to use instead of the local basename.",
    )
    parser.add_argument(
        "--region",
        default=os.getenv("ZOHO_REGION", DEFAULT_REGION),
        help="Zoho data centre region (us | eu | in | au | jp | cn).",
    )
    parser.add_argument(
        "--link-mode",
        choices=("both", "direct", "preview"),
        default="direct",
        help="Which URLs to emit (default: direct).",
    )
    parser.add_argument(
        "--share-mode",
        choices=("public", "skip"),
        default=os.getenv("ZOHO_SHARE_MODE", "public"),
        help="Control sharing behaviour: public (default) or skip to keep the file private.",
    )
    parser.add_argument(
        "--conflict-mode",
        choices=("abort", "rename", "replace"),
        default=os.getenv("ZOHO_CONFLICT_MODE", "abort"),
        help="Handle duplicate filenames: abort (default), rename automatically, or replace the existing file.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=int(os.getenv("ZOHO_MAX_RETRIES", "3")),
        help="Number of retries for upload/link API calls (default: 3).",
    )
    parser.add_argument(
        "--retry-delay",
        type=float,
        default=float(os.getenv("ZOHO_RETRY_DELAY", "2")),
        help="Delay in seconds between retries (default: 2).",
    )

    args = parser.parse_args()

    region, api_base, accounts_base = resolve_endpoints(args.region)
    token = get_access_token(accounts_base)
    log_enabled = args.stdout_mode == "full"

    resource_id, permalink, final_remote_name = upload_file(
        api_base=api_base,
        token=token,
        path=args.file_path,
        remote_name=args.remote_name,
        conflict_mode=args.conflict_mode,
        max_retries=args.max_retries,
        retry_delay=args.retry_delay,
        enable_logs=log_enabled,
    )

    log_line(f"📄 Remote filename: {final_remote_name}", CYAN, log_enabled)

    links: Dict[str, Optional[str]] = {}
    html_snippet: Optional[str] = None

    if args.share_mode == "public":
        share_everyone_view(api_base, token, resource_id, args.max_retries, args.retry_delay, log_enabled)
        base_link = create_external_link(api_base, token, resource_id, args.max_retries, args.retry_delay, log_enabled)
        links = compose_links(base_link, args.link_mode)
        html_snippet = build_html_snippet(links.get("direct"))
    else:
        log_line("🔒 Skipping public share; using internal WorkDrive URL.", BLUE, log_enabled)
        internal_link = permalink or f"https://workdrive.zoho.com/file/{resource_id}"
        if args.link_mode in ("direct", "both"):
            links["direct"] = internal_link
            log_line(
                "⚠️  Direct downloads require public sharing; emitting the WorkDrive permalink instead.",
                YELLOW,
                log_enabled,
            )
        if args.link_mode in ("preview", "both") or args.link_mode == "direct":
            links["preview"] = internal_link

    primary_link = (
        links.get("direct")
        if args.link_mode != "preview"
        else links.get("preview")
    )
    if args.stdout_mode == "direct" and not primary_link:
        sys.exit(color("❌ No direct link available. Consider share_mode=public or link_mode=preview.", RED, True))

    if args.stdout_mode == "full":
        output_full(
            resource_id=resource_id,
            region=region,
            share_mode=args.share_mode,
            link_mode=args.link_mode,
            links=links,
            html_snippet=html_snippet,
            api_base=api_base,
            enable_color=True,
        )
    elif args.stdout_mode == "direct":
        if primary_link:
            print(primary_link)
    elif args.stdout_mode == "json":
        output_json(resource_id, links, html_snippet)

    outputs_path = args.github_output or os.getenv("GITHUB_OUTPUT")
    if outputs_path:
        to_write: Dict[str, str] = {"zoho_resource_id": resource_id, "zoho_remote_name": final_remote_name}
        if links.get("direct"):
            to_write[args.output_key] = links["direct"]
            to_write["zoho_direct_url"] = links["direct"]
        elif links.get("preview"):
            to_write[args.output_key] = links["preview"]
        if links.get("preview"):
            to_write["zoho_preview_url"] = links["preview"]
        if html_snippet:
            to_write["zoho_html_snippet"] = html_snippet
        append_outputs(outputs_path, to_write)


if __name__ == "__main__":
    main()
