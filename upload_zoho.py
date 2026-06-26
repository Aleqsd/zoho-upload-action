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
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Sequence, Tuple

import requests
from glob import glob, has_magic

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
ACCESS_TOKEN = os.getenv("ZOHO_ACCESS_TOKEN") or os.getenv("INPUT_ACCESS_TOKEN")


def is_within(path: str, parent: str) -> bool:
    path_abs = os.path.abspath(path)
    parent_abs = os.path.abspath(parent)
    try:
        return os.path.commonpath([path_abs, parent_abs]) == parent_abs
    except ValueError:
        return False


def resolve_file_path(raw_path: str) -> str:
    abs_path = os.path.abspath(raw_path)
    if os.path.isfile(abs_path):
        return abs_path

    workspace = os.getenv("GITHUB_WORKSPACE")
    if workspace:
        workspace_abs = os.path.abspath(workspace)
        if not is_within(abs_path, workspace_abs):
            message = (
                f"❌ File not found: {raw_path}\n"
                f"   Docker-based GitHub Actions can only access files inside the workspace ({workspace_abs}). "
                "Copy or generate the file there before invoking the action."
            )
        else:
            message = (
                f"❌ File not found in workspace: {abs_path}\n"
                "   Confirm previous steps produced the file inside the repository before this action runs."
            )
        sys.exit(color(message, RED, True))

    sys.exit(color(f"❌ File not found: {raw_path}", RED, True))


def _split_raw_entries(raw: str) -> List[str]:
    segments = [raw] if "," not in raw else raw.split(",")
    trimmed = [segment.strip() for segment in segments]
    return [segment for segment in trimmed if segment]


def expand_input_paths(raw_paths: Sequence[str]) -> List[str]:
    expanded: List[str] = []
    for raw in raw_paths:
        for entry in _split_raw_entries(raw):
            candidate = os.path.expanduser(entry)
            if has_magic(candidate):
                matches = [
                    path
                    for path in glob(candidate, recursive=True)
                    if os.path.isfile(path)
                ]
                if not matches:
                    sys.exit(color(f"❌ No files matched pattern: {entry}", RED, True))
                expanded.extend(sorted(matches))
            else:
                expanded.append(candidate)
    return expanded


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


def _is_retryable_refresh(status: int, body: str) -> bool:
    lower = body.lower()
    if status == 429 or status >= 500:
        return True
    return status == 400 and (
        "access denied" in lower or "rate" in lower or "too many requests" in lower
    )


def _is_retryable_api_status(status: int) -> bool:
    return status == 429 or status >= 500


def _response_error_ids(response: requests.Response) -> List[str]:
    try:
        payload = response.json()
    except ValueError:
        return []

    errors = payload.get("errors") if isinstance(payload, dict) else None
    if not isinstance(errors, list):
        return []
    return [
        str(error.get("id", ""))
        for error in errors
        if isinstance(error, dict) and error.get("id")
    ]


def _is_retryable_api_response(response: requests.Response) -> bool:
    if _is_retryable_api_status(response.status_code):
        return True
    return response.status_code == 401 and "R008" in _response_error_ids(response)


def _is_transient_unauthorized_response(response: requests.Response) -> bool:
    return response.status_code == 401 and "R008" in _response_error_ids(response)


def _retry_delay_seconds(
    retry_delay: float,
    attempt: int,
    response: Optional[requests.Response] = None,
) -> float:
    if response is not None:
        retry_after = getattr(response, "headers", {}).get("Retry-After")
        if retry_after:
            try:
                delay = float(retry_after)
                if delay >= 0:
                    return delay
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
                    return max(0.0, delay)
                except (TypeError, ValueError, IndexError, OverflowError):
                    pass

    return retry_delay * (2 ** (attempt - 1))


def get_access_token(
    accounts_base: str,
    *,
    max_retries: int,
    retry_delay: float,
    enable_logs: bool,
) -> str:
    for attempt in range(1, max_retries + 1):
        try:
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
        except requests.RequestException as exc:
            if attempt >= max_retries:
                sys.exit(color(f"❌ Token refresh failed: {exc}", RED, True))
            delay = retry_delay * (2 ** (attempt - 1))
            log_line(
                f"🔁 Token refresh network error; retrying in {delay:.0f}s…",
                YELLOW,
                enable_logs,
            )
            time.sleep(delay)
            continue

        try:
            response.raise_for_status()
        except requests.HTTPError:
            body = response.text or ""
            if attempt < max_retries and _is_retryable_refresh(response.status_code, body):
                delay = retry_delay * (2 ** (attempt - 1))
                log_line(
                    f"🔁 Token refresh failed ({response.status_code}); retrying in {delay:.0f}s…",
                    YELLOW,
                    enable_logs,
                )
                time.sleep(delay)
                continue
            sys.exit(color(f"❌ Token refresh failed: {response.status_code} {body}", RED, True))

        token = response.json().get("access_token")
        if not token:
            sys.exit(
                color(f"❌ No access_token in refresh response: {response.text}", RED, True)
            )
        return token

    sys.exit(color("❌ Token refresh failed after all retries.", RED, True))


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
            if attempt == 1:
                message = f"⏳ Uploading '{current_name}'"
            else:
                message = f"⏳ Uploading '{current_name}' (attempt {attempt}/{max_retries})"
            log_line(message, CYAN, enable_logs)
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
                delay = _retry_delay_seconds(retry_delay, attempt)
                log_line(f"🔁 Network error ({exc}); retrying in {delay:.0f}s…", YELLOW, enable_logs)
                time.sleep(delay)
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
                elif _is_retryable_api_response(response) and attempt < max_retries:
                    delay = _retry_delay_seconds(retry_delay, attempt, response)
                    log_line(
                        f"🔁 Zoho responded with {status}; retrying in {delay:.0f}s…",
                        YELLOW,
                        enable_logs,
                    )
                    time.sleep(delay)
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


def share_everyone_view(
    api_base: str,
    token: str,
    resource_id: str,
    max_retries: int,
    retry_delay: float,
    enable_logs: bool,
) -> bool:
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
            return True
        except requests.HTTPError as http_err:
            response = http_err.response
            status = response.status_code
            if _is_transient_unauthorized_response(response):
                log_line(
                    "⚠️  Public permissions were not applied (Zoho R008); "
                    "continuing with external link creation.",
                    YELLOW,
                    enable_logs,
                )
                return False
            if _is_retryable_api_status(status) and attempt < max_retries:
                delay = _retry_delay_seconds(retry_delay, attempt, response)
                log_line(f"🔁 Share API error {status}; retrying in {delay:.0f}s…", YELLOW, enable_logs)
                time.sleep(delay)
                continue
            sys.exit(color(f"❌ Share everyone failed: {status} {response.text}", RED, True))
        except requests.RequestException as exc:
            if attempt == max_retries:
                sys.exit(color(f"❌ Share everyone failed: {exc}", RED, True))
            delay = _retry_delay_seconds(retry_delay, attempt)
            log_line(f"🔁 Share request error ({exc}); retrying in {delay:.0f}s…", YELLOW, enable_logs)
            time.sleep(delay)
    return False


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
            data = response.json()
            download_url = data["data"]["attributes"]["download_url"]
            log_line(f"🔗 External download link created: {download_url}", GREEN, enable_logs)
            return download_url
        except requests.HTTPError as http_err:
            response = http_err.response
            status = response.status_code
            if _is_retryable_api_response(response) and attempt < max_retries:
                delay = _retry_delay_seconds(retry_delay, attempt, response)
                log_line(f"🔁 Link API error {status}; retrying in {delay:.0f}s…", YELLOW, enable_logs)
                time.sleep(delay)
                continue
            sys.exit(color(f"❌ Create link failed: {status} {response.text}", RED, True))
        except requests.RequestException as exc:
            if attempt == max_retries:
                sys.exit(color(f"❌ Create link failed: {exc}", RED, True))
            delay = _retry_delay_seconds(retry_delay, attempt)
            log_line(f"🔁 Link request error ({exc}); retrying in {delay:.0f}s…", YELLOW, enable_logs)
            time.sleep(delay)
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


@dataclass
class UploadResult:
    source_path: str
    resource_id: str
    remote_name: str
    links: Dict[str, Optional[str]]
    html_snippet: Optional[str]
    permalink: Optional[str]


def output_full(
    *,
    results: Sequence[UploadResult],
    region: str,
    share_mode: str,
    link_mode: str,
    api_base: str,
    enable_color: bool,
) -> None:
    total = len(results)
    for idx, result in enumerate(results, 1):
        prefix = "✅ Upload complete"
        if total > 1:
            prefix += f" [{idx}/{total}]"
        print(color(prefix, GREEN, enable_color) + " — " + color(result.resource_id, BOLD, enable_color))
        print(
            "\n"
            + color("📄 Remote filename", CYAN, enable_color)
            + f": {result.remote_name}"
        )
        direct = result.links.get("direct")
        preview = result.links.get("preview")
        if direct:
            print(
                "\n"
                + color("⚡ Direct download", CYAN, enable_color)
                + f": {direct}"
            )
        if preview:
            print(
                "\n"
                + color("🖥️  WorkDrive share", BLUE, enable_color)
                + f": {preview}"
            )
        if result.html_snippet:
            print(
                "\n"
                + color("🧩 HTML embed", MAGENTA, enable_color)
                + f":\n{result.html_snippet}"
            )
        if idx < total:
            print("\n" + "-" * 40 + "\n")
    print(
        "\n"
        + color("ℹ️  Context", YELLOW, enable_color)
        + f": region={region.upper()} · share_mode={share_mode} · link_mode={link_mode} · api_base={api_base}"
    )

def results_payload(results: Sequence[UploadResult]) -> List[Dict[str, Optional[str]]]:
    return [
        {
            "source_path": result.source_path,
            "resource_id": result.resource_id,
            "remote_name": result.remote_name,
            "direct_url": result.links.get("direct"),
            "preview_url": result.links.get("preview"),
            "html": result.html_snippet,
        }
        for result in results
    ]


def stdout_payload(results: Sequence[UploadResult]) -> object:
    payload = results_payload(results)
    if len(payload) == 1:
        return payload[0]
    return payload


def write_results_file(path: str, results: Sequence[UploadResult]) -> None:
    output_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(results_payload(results), handle)


def output_json(results: Sequence[UploadResult]) -> None:
    print(json.dumps(stdout_payload(results)))


def compact_results_json(results: Sequence[UploadResult], output_limit: int) -> str:
    payload = json.dumps(results_payload(results))
    if output_limit >= 0 and len(payload.encode("utf-8")) > output_limit:
        return ""
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload a file to Zoho WorkDrive and emit public URLs."
    )

    parser.add_argument("file_paths", nargs="+", help="Local file(s) to upload.")
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
        "--results-json-file",
        help="Optional path where the full multi-upload JSON results should be written.",
    )
    parser.add_argument(
        "--results-json-output-limit",
        type=int,
        default=int(os.getenv("ZOHO_RESULTS_JSON_OUTPUT_LIMIT", "65536")),
        help=(
            "Maximum UTF-8 byte size for the zoho_results_json GitHub output. "
            "Oversized payloads are only written to --results-json-file. Use -1 to disable the limit."
        ),
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
    parser.add_argument(
        "--access-token",
        default=(
            os.getenv("ZOHO_ACCESS_TOKEN")
            or os.getenv("INPUT_ACCESS_TOKEN")
            or ACCESS_TOKEN
            or ""
        ),
        help="Optional access token to reuse across concurrent uploads.",
    )
    parser.add_argument(
        "--token-max-retries",
        type=int,
        default=int(os.getenv("ZOHO_TOKEN_MAX_RETRIES", "8")),
        help="Number of retries for token refresh calls (default: 8).",
    )
    parser.add_argument(
        "--token-retry-delay",
        type=float,
        default=float(os.getenv("ZOHO_TOKEN_RETRY_DELAY", "12")),
        help="Delay in seconds between token refresh retries (default: 12).",
    )

    args = parser.parse_args()

    if args.access_token:
        need("FOLDER_ID")
    else:
        need("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN", "FOLDER_ID")

    expanded_inputs = expand_input_paths(args.file_paths)

    if len(expanded_inputs) > 1 and args.remote_name:
        sys.exit(color("❌ --remote-name can only be used when uploading a single file.", RED, True))

    region, api_base, accounts_base = resolve_endpoints(args.region)
    token = args.access_token or get_access_token(
        accounts_base,
        max_retries=args.token_max_retries,
        retry_delay=args.token_retry_delay,
        enable_logs=args.stdout_mode == "full",
    )
    log_enabled = args.stdout_mode == "full"

    target_paths = [resolve_file_path(path) for path in expanded_inputs]

    results: List[UploadResult] = []

    for index, target_path in enumerate(target_paths, 1):
        resource_id, permalink, final_remote_name = upload_file(
            api_base=api_base,
            token=token,
            path=target_path,
            remote_name=args.remote_name,
            conflict_mode=args.conflict_mode,
            max_retries=args.max_retries,
            retry_delay=args.retry_delay,
            enable_logs=log_enabled,
        )

        log_line(
            f"📄 Remote filename for '{os.path.basename(target_path)}': {final_remote_name}",
            CYAN,
            log_enabled,
        )

        links: Dict[str, Optional[str]] = {}
        html_snippet: Optional[str] = None

        if args.share_mode == "public":
            share_everyone_view(
                api_base, token, resource_id, args.max_retries, args.retry_delay, log_enabled
            )
            base_link = create_external_link(
                api_base, token, resource_id, args.max_retries, args.retry_delay, log_enabled
            )
            links = compose_links(base_link, args.link_mode)
            html_snippet = build_html_snippet(links.get("direct"))
        else:
            if index == 1:
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

        results.append(
            UploadResult(
                source_path=target_path,
                resource_id=resource_id,
                remote_name=final_remote_name,
                links=links,
                html_snippet=html_snippet,
                permalink=permalink,
            )
        )

    primary_links: List[str] = []
    for result in results:
        if args.link_mode == "preview":
            link = result.links.get("preview")
        else:
            link = result.links.get("direct") or (result.links.get("preview") if args.link_mode == "both" else None)
        if link:
            primary_links.append(link)
        else:
            primary_links.append("")

    if args.stdout_mode == "direct":
        missing = [res.source_path for res, link in zip(results, primary_links) if not link]
        if missing:
            sys.exit(
                color(
                    "❌ No direct link available for: " + ", ".join(missing) + ". Consider share_mode=public or link_mode=preview.",
                    RED,
                    True,
                )
            )
        if len(primary_links) == 1:
            print(primary_links[0])
        else:
            print("\n".join(primary_links))
    elif args.stdout_mode == "json":
        output_json(results)
    else:
        output_full(
            results=results,
            region=region,
            share_mode=args.share_mode,
            link_mode=args.link_mode,
            api_base=api_base,
            enable_color=True,
        )

    if args.results_json_file:
        write_results_file(args.results_json_file, results)

    outputs_path = args.github_output or os.getenv("GITHUB_OUTPUT")
    if outputs_path:
        primary_result = results[0]
        to_write: Dict[str, str] = {
            "zoho_resource_id": primary_result.resource_id,
            "zoho_remote_name": primary_result.remote_name,
            "zoho_results_json": compact_results_json(
                results,
                args.results_json_output_limit,
            ),
        }
        if args.results_json_file:
            to_write["zoho_results_file"] = os.path.abspath(args.results_json_file)
        direct_primary = primary_result.links.get("direct")
        preview_primary = primary_result.links.get("preview")
        if direct_primary:
            to_write[args.output_key] = direct_primary
            to_write["zoho_direct_url"] = direct_primary
        elif preview_primary:
            to_write[args.output_key] = preview_primary
        if preview_primary:
            to_write["zoho_preview_url"] = preview_primary
        if primary_result.html_snippet:
            to_write["zoho_html_snippet"] = primary_result.html_snippet

        for idx, result in enumerate(results, 1):
            suffix = str(idx)
            to_write[f"zoho_resource_id_{suffix}"] = result.resource_id
            to_write[f"zoho_remote_name_{suffix}"] = result.remote_name
            if result.links.get("direct"):
                to_write[f"zoho_direct_url_{suffix}"] = result.links["direct"]
            if result.links.get("preview"):
                to_write[f"zoho_preview_url_{suffix}"] = result.links["preview"]
            if result.html_snippet:
                to_write[f"zoho_html_snippet_{suffix}"] = result.html_snippet

        append_outputs(outputs_path, to_write)


if __name__ == "__main__":
    main()
