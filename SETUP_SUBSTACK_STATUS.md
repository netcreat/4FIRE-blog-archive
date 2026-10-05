# 4FIRE Naver ↔ Substack status setup

Place these files in the GitHub repository:

- `substack_manifest_v2.json` → rename to `substack_manifest.json` in the repository root.
- `generate_substack_status.py` → repository root.
- `substack-status.yml` → `.github/workflows/substack-status.yml`.

Then run **Actions → Update Substack Status → Run workflow** once.

The workflow generates:

- `SUBSTACK_STATUS.md` — human-readable dashboard.
- `substack_status.json` — machine-readable full status.

Rules:

- Naver `index.json` is the source of truth.
- A Naver `logNo` with a `published` mapping is shown as Published.
- A Naver `logNo` not present in the manifest is automatically shown as Missing.
- Add `status: "skip"` for Naver-only posts you intentionally do not want on Substack.
- Public Substack posts found in the sitemap but not referenced by any mapping appear under **Unmatched public Substack posts**.
- The public Substack sitemap cannot see drafts or scheduled/unpublished posts.
