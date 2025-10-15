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

## ⚙️ Inputs

| Input | Default | Description |
|-------|---------|-------------|
| `file_path` | – | Local file to upload (required, must reside inside the workflow workspace). |
| `remote_name` | – | Override the filename stored in WorkDrive. |
| `stdout_mode` | `full` | Configure container logging (`full`, `direct`, `json`). |
| `region` | `us` | Target data centre (`us`, `eu`, `in`, `au`, `jp`, `cn`). |
| `link_mode` | `direct` | Emit `direct`, `preview`, or `both` URLs. |
| `share_mode` | `public` | `public` applies "Everyone on the internet" permissions; `skip` keeps the file private. |
| `conflict_mode` | `abort` | `abort` (default), `rename` (append UTC timestamp), or `replace` (trash the existing file first). |
| `max_retries` | `3` | Retry count for upload/link API calls. |
| `retry_delay` | `2` | Seconds to wait between retries. |

## 📤 Outputs

| Output | Description |
|--------|-------------|
| `zoho_direct_url` | Direct download URL (default output). Falls back to preview when direct is disabled. |
| `zoho_preview_url` | WorkDrive share link (transforms `/download` to `/preview`). |
| `zoho_html_snippet` | `<img>` snippet pointing at the direct link (when available). |
| `zoho_resource_id` | WorkDrive resource identifier for the uploaded file. |
| `zoho_remote_name` | Final filename stored in WorkDrive after conflict handling. |

> 🗂️ **Workspace access only**  
> This Docker-based action can only read files that live inside `${{ github.workspace }}`. Copy or generate build artifacts into that directory (or use `actions/download-artifact` earlier in the job) before invoking the upload step. The action now fails fast with guidance when the file is missing or comes from outside the workspace mount.

---

## 🔐 Setting up Zoho credentials

1. **Create a client ID/secret:** visit the [Zoho API Console](https://api-console.zoho.com/), create a new **Server-based** client, and whitelist your callback URL. A searchable overview lives in the [Zoho OAuth knowledge base](https://help.zoho.com/portal/en/kb/articles?searchStr=register+client+oauth).  
2. **Generate a refresh token:** complete the OAuth flow once with the `ZohoWorkDrive.files.ALL` scope. Follow the “Generate refresh token” steps in Zoho’s [OAuth search results](https://help.zoho.com/portal/en/kb/articles?searchStr=generate+refresh+token+oauth) for the consent URL template and token exchange.  
3. **Collect a WorkDrive folder ID:** open the destination folder in the WorkDrive web UI, click the **More Options → Share → External Sharing**, and copy the ID from the URL segment after `/folder/`. Zoho’s knowledge base covers the flow under [WorkDrive external sharing](https://help.zoho.com/portal/en/kb/articles?searchStr=workdrive+external+sharing).  
4. Store all values as GitHub Secrets (`ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN`, `ZOHO_FOLDER_ID`). Use `.env.example` as a local reference when running the Docker image or `act`.

---

## 🧪 Local test loop

```bash
cp .env.example .env  # then fill in real credentials
docker build -t zoho-upload-action .
docker run --rm --env-file .env -v "$PWD":/workspace -w /workspace \
  zoho-upload-action sample_upload.txt --remote-name sample-$(date +%s).txt

# Run the automated test suite (requires Python 3)
make test
```

The `docker run` invocation mirrors the GitHub Actions mount by exposing your project as `/workspace`; keep the file you pass to the container inside that directory so the action can read it just like it would under `${{ github.workspace }}`.

## ❗ Duplicate filenames

Zoho WorkDrive rejects uploads when a file with the same name already exists in the target folder. Choose the strategy that fits your workflow:

- `conflict_mode: abort` (default) — fail fast with a warning.
- `conflict_mode: rename` — append a UTC timestamp (e.g., `build-20241015-101530.zip`) and proceed.
- `conflict_mode: replace` — reuse the same filename by asking Zoho to overwrite the previous version.

You can also continue to pass a custom `remote_name` unique to the workflow run.

## 🔐 Sharing modes

Set `share_mode: public` (default) to grant "Everyone on the internet" view access and receive direct/preview URLs. Use `share_mode: skip` to keep the file private; the action emits the internal WorkDrive permalink (access requires authenticated WorkDrive users). Direct-download links remain exclusive to the public mode.

## ⏱️ Reliability helpers

Large uploads or transient Zoho issues are handled with `max_retries` and `retry_delay`. Progress logs (⏳/🔁 icons) only appear when `stdout_mode=full`, keeping other modes clean.

---

## 🧰 Maintainer
Made with ❤️ by [Alexandre DO-O ALMEIDA](https://github.com/aleqsd)

MIT License
