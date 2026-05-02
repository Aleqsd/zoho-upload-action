# ☁️ Zoho Upload GitHub Action

Upload any artifact to **Zoho WorkDrive**, automatically share it with “Everyone on the internet”, and surface the URLs you need for deployments or handoffs directly in your workflow logs.

---

## 🧩 Example
```yaml
- name: Upload build artifact to Zoho
  uses: aleqsd/zoho-upload-action@v1
  with:
    file_path: dist/my_app.zip
    region: eu                # us by default
    link_mode: direct         # direct | preview | both
    conflict_mode: rename     # abort | rename | replace
  env:
    ZOHO_CLIENT_ID: ${{ secrets.ZOHO_CLIENT_ID }}
    ZOHO_CLIENT_SECRET: ${{ secrets.ZOHO_CLIENT_SECRET }}
    ZOHO_REFRESH_TOKEN: ${{ secrets.ZOHO_REFRESH_TOKEN }}
    ZOHO_FOLDER_ID: ${{ secrets.ZOHO_FOLDER_ID }}
```

Use the generated outputs anywhere later in the job:
```yaml
- run: |
    echo "Direct:  ${{ steps.upload.outputs.zoho_direct_url }}"
    echo "Preview: ${{ steps.upload.outputs.zoho_preview_url }}"
```

---

### 📦 Uploading multiple files

Provide several files in a single step by separating the paths with newlines or commas:

```yaml
- name: Upload multiple assets to Zoho
  uses: aleqsd/zoho-upload-action@v1
  with:
    file_path: |
      dist/api.tar.gz
      dist/web-assets.zip
    # Comma-separated also works on a single line:
    # file_path: dist/api.tar.gz,dist/web-assets.zip
```

The action uploads each file sequentially, sharing them with the same options (`link_mode`, `share_mode`, etc.). The summary
logs repeat for every file and the composite output `zoho_results_json` contains a JSON array with metadata and URLs for each
upload. Existing outputs (`zoho_direct_url`, `zoho_preview_url`, etc.) continue to reflect the first file for backwards
compatibility, while enumerated keys such as `zoho_direct_url_2` are provided for convenience.

> 💡 **Wildcard support**
> You can also supply [glob patterns](https://docs.python.org/3/library/glob.html) like `dist/*.png` or `artifacts/**/*.zip`
> (quoted in YAML) and the action will expand them before uploading. Patterns that do not match any files cause the step to
> fail fast with guidance.

---

## ⚙️ Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `file_path` | – | Local file(s) to upload (required, newline- or comma-separated to upload multiple files, must reside inside the workflow workspace). |
| `remote_name` | – | Override the filename stored in WorkDrive (only valid when uploading a single file). |
| `stdout_mode` | `full` | Control script logging (`full`, `direct`, `json`). |
| `region` | `us` | Target data centre (`us`, `eu`, `in`, `au`, `jp`, `cn`). |
| `link_mode` | `direct` | Emit `direct`, `preview`, or `both` URLs. |
| `share_mode` | `public` | `public` applies "Everyone on the internet" permissions; `skip` keeps the file private. |
| `conflict_mode` | `abort` | `abort` (default), `rename` (append UTC timestamp), or `replace` (trash the existing file first). |
| `access_token` | – | Optional Zoho access token to skip `refresh_token` calls and keep multiple uploads concurrent without re-auth storms. |
| `max_retries` | `3` | Retry count for upload/link API calls. |
| `retry_delay` | `2` | Seconds to wait between retries. |
| `token_max_retries` | `8` | Retry count for access-token refresh calls. |
| `token_retry_delay` | `12` | Seconds to wait between token refresh retries (increases with exponential backoff). |

## 📤 Outputs

| Output | Description |
|--------|-------------|
| `zoho_direct_url` | Direct download URL (default output). Falls back to preview when direct is disabled. |
| `zoho_preview_url` | WorkDrive share link (transforms `/download` to `/preview`). |
| `zoho_html_snippet` | `<img>` snippet pointing at the direct link (when available). |
| `zoho_resource_id` | WorkDrive resource identifier for the uploaded file. |
| `zoho_remote_name` | Final filename stored in WorkDrive after conflict handling. |
| `zoho_results_json` | JSON array describing every uploaded file (source path, remote name, URLs). |

> 🗂️ **Workspace access only**  
> The action can only read files that live inside `${{ github.workspace }}`. Copy or generate build artifacts into that directory (or use `actions/download-artifact` earlier in the job) before invoking the upload step. The script fails fast with guidance when the file is missing or comes from outside the workspace.

---

## 🔐 Setting up Zoho credentials

1. **Create a client ID/secret:** visit the [Zoho API Console](https://api-console.zoho.com/), create a new **Server-based** client, and whitelist your callback URL. A searchable overview lives in the [Zoho OAuth knowledge base](https://help.zoho.com/portal/en/kb/articles?searchStr=register+client+oauth).  
2. **Generate a refresh token:** complete the OAuth flow once with the `ZohoWorkDrive.files.ALL` scope. Follow the “Generate refresh token” steps in Zoho’s [OAuth search results](https://help.zoho.com/portal/en/kb/articles?searchStr=generate+refresh+token+oauth) for the consent URL template and token exchange.  
3. **Collect a WorkDrive folder ID:** open the destination folder in the WorkDrive web UI, click the **More Options → Share → External Sharing**, and copy the ID from the URL segment after `/folder/`. Zoho’s knowledge base covers the flow under [WorkDrive external sharing](https://help.zoho.com/portal/en/kb/articles?searchStr=workdrive+external+sharing).  
4. Store all values as GitHub Secrets (`ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN`, `ZOHO_FOLDER_ID`). Use `.env.example` as a local reference when running the Docker image or `act`.

---

## 🧪 Local test loop

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env  # then fill in real credentials

# Load credentials and run the uploader locally
set -a && source .env && set +a
python upload_zoho.py sample_upload.txt --remote-name sample-$(date +%s).txt --link-mode both

# Run the automated test suite
make test
```

The script honours all CLI flags available in the action, so you can dry-run new combinations locally before updating your workflow.

## ❗ Duplicate filenames

Zoho WorkDrive rejects uploads when a file with the same name already exists in the target folder. Choose the strategy that fits your workflow:

- `conflict_mode: abort` (default) — fail fast with a warning.
- `conflict_mode: rename` — append a UTC timestamp (e.g., `build-20241015-101530.zip`) and proceed.
- `conflict_mode: replace` — reuse the same filename by asking Zoho to overwrite the previous version.

You can also continue to pass a custom `remote_name` unique to the workflow run.

## 🔐 Sharing modes

Set `share_mode: public` (default) to grant "Everyone on the internet" view access and receive direct/preview URLs. Use `share_mode: skip` to keep the file private; the action emits the internal WorkDrive permalink (access requires authenticated WorkDrive users). Direct-download links remain exclusive to the public mode.

## ⏱️ Reliability helpers

Large uploads, transient Zoho issues, and WorkDrive `429` rate limits are handled with `max_retries` and `retry_delay`.
When Zoho sends `Retry-After`, the action waits that long before retrying; otherwise retries use exponential backoff.
Token refresh failures also retry with `token_max_retries` / `token_retry_delay` and exponential backoff (`2^attempt`),
which helps reduce `Access Denied` throttling when several upload jobs run at once.

---

## 🧰 Maintainer
Made with ❤️ by [Alexandre DO-O ALMEIDA](https://github.com/aleqsd)

MIT License
