# 4FIRE Naver ↔ Substack status setup

The repository now uses a single combined GitHub Actions workflow:

- `.github/workflows/sync-all.yml` — **Sync Naver + Substack Status**

This workflow does both jobs in one run:

1. Runs `sync_naver.py` to refresh Naver RSS, `index.json`, and archived posts.
2. Runs `generate_substack_status.py` to regenerate:
   - `SUBSTACK_STATUS.md`
   - `substack_status.json`

It can be triggered in three ways:

- **Automatically every 3 hours**
- **Automatically whenever `substack_manifest.json` changes**
- **Manually/indirectly by updating `.github/SYNC_ALL_TRIGGER.txt`**

The trigger file exists so ChatGPT can force a refresh through the GitHub connector even when a manifest change is not needed.

## Normal Substack publishing workflow

After a Substack post is published:

1. Add or update the Naver logNo mapping in `substack_manifest.json`.
2. The manifest push automatically triggers `Sync Naver + Substack Status`.
3. The workflow syncs the latest Naver posts first.
4. It then regenerates both status files.
5. The generated dashboard should show the mapped post as **✅ Published**.

If a forced refresh is needed, update `.github/SYNC_ALL_TRIGGER.txt`.

## Status rules

- Naver `index.json` is the source of truth.
- A Naver `logNo` with a `published` mapping is shown as Published.
- A Naver `logNo` not present in the manifest is automatically shown as Missing.
- Add `status: "skip"` for Naver-only posts that should not be published on Substack.
- Public Substack discovery can fail because Substack may return HTTP 403 to GitHub-hosted runners. Existing manifest mappings are still preserved and used.
- Drafts and scheduled/unpublished Substack posts are not expected to appear as public posts.
