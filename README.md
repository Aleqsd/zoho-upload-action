# ☁️ Zoho Upload GitHub Action

Upload any file to **Zoho WorkDrive**, automatically make it public, and get a **direct-download URL**.

---

## 🧩 Example usage
```yaml
- name: Upload build artifact to Zoho
  uses: aleqsd/zoho-upload-action@v1
  with:
    file_path: dist/my_app.zip
  env:
    ZOHO_CLIENT_ID: ${{ secrets.ZOHO_CLIENT_ID }}
    ZOHO_CLIENT_SECRET: ${{ secrets.ZOHO_CLIENT_SECRET }}
    ZOHO_REFRESH_TOKEN: ${{ secrets.ZOHO_REFRESH_TOKEN }}
    ZOHO_FOLDER_ID: ${{ secrets.ZOHO_FOLDER_ID }}
```

Output available as:
```
${{ steps.upload.outputs.zoho_direct_url }}
```

You can reuse it later in the same workflow, for example:
```yaml
- run: echo "Download URL: ${{ steps.upload.outputs.zoho_direct_url }}"
```

---

## 🔑 Required secrets

| Name | Description |
|------|--------------|
| `ZOHO_CLIENT_ID` | Your Zoho OAuth client ID |
| `ZOHO_CLIENT_SECRET` | Your Zoho OAuth client secret |
| `ZOHO_REFRESH_TOKEN` | Long-lived refresh token |
| `ZOHO_FOLDER_ID` | Target WorkDrive folder ID |

---

## 🧰 Maintainer
Made with ❤️ by [Alexandre DO-O ALMEIDA](https://github.com/aleqsd)

MIT License
