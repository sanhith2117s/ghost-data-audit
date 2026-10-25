# Image-evidence verification (Item 5)

Independent check of the screenshot transcription cache against the 32 published
samples (`dataset/sample_output.csv`) and the full 518-case run.

## Method

1. Read every entry in `code/evidence_cache/images.json` (what the cache says).
2. Cross-reference each cached `resource_id` against the steps in
   `dataset/sample_output.csv` (what the oracle expects).
3. Flag any cache entry whose `resource_id`, identifier or timestamp disagrees
   with the sample answer, or that looks low-confidence.
4. Attempt re-extraction with a second vision model via the model router
   (Item 1b / `config/models.yaml`) and compare.

No second vision model / router endpoint is wired in this submission, and no
provider API key was available, so step 4 could not be performed. Disagreements
and low-confidence entries below are escalated for manual eye-check rather than
resolved silently.

---

## 1. What the cache says (all 9 entries)

| Image (file_id) | resource_id | category | state | identifier (email / ref / name) | observed_at |
|---|---|---|---|---|---|
| evidence_04 `account_admin_console_20260926.png` | account_5762 | USER_PROFILE | PRESENT | haruka.s1838@zoho.com / user_11 / Haruka Sato | 2026-09-26T11:00:00Z |
| evidence_09 `search_index_inspector_20260901.png` | search_428 | SEARCH_INDEX_ENTRY | PRESENT | g.silva76@gmail.com / user_13 / Gabriel Silva | 2026-09-01T05:58:21Z |
| evidence_14 `connector_management_console_20260807.png` | data_flow_1121 | DATA_FLOW | ACTIVE | jain.aditi@gmail.com | 2026-08-07T08:00:00Z |
| evidence_14 `connector_management_console_20260807.png` | data_flow_9846 | DATA_FLOW | ACTIVE | lucia-torres@gmail.com | 2026-08-07T08:00:00Z |
| evidence_19 `analytics_profile_view_20260713.png` | analytics_7438 | ANALYTICS_PROFILE | PRESENT | darwish.omar@icloud.com / user_20 / Omar Darwish | 2026-07-13T14:22:00Z |
| evidence_23 `customer_export_console_20260618.png` | export_1260 | USER_EXPORT | PRESENT | ingrid.dahl@outlook.com | 2026-06-18T06:42:00Z |
| evidence_28 `support_ticket_console_20260524.png` | support_1034 | SUPPORT_MESSAGE | PRESENT | kagyeman8947@fastmail.com / Kofi Agyeman | 2026-05-24T07:00:00Z |
| evidence_32 `backup_restore_console_20260429.png` | backup_9291 | BACKUP_ARCHIVE | ACTIVE | bluepeakco@outlook.com | 2026-04-29T09:00:00Z |
| evidence_32 `backup_restore_console_20260429.png` | backup_9287 | BACKUP_ARCHIVE | ACTIVE | areed1433@gmail.com | 2026-04-29T09:00:00Z |
| evidence_37 `security_audit_explorer_20260905.png` | audit_record_003 | SECURITY_AUDIT_LOG | PRESENT | user_03 | 2026-08-25T04:03:00Z |
| evidence_60 `marketing_profile_console.png` | marketing_001 | MARKETING_PROFILE | PRESENT | sj.park@naver.com / +82 10-5837-9451 / Soo-jin Park | 2026-09-15T23:44:00Z |

---

## 2. Image-derived findings in the 32 published samples

Cross-referencing every cached `resource_id` against `dataset/sample_output.csv`:

| Cached resource_id | Image | Sample case | Cited evidence_id in sample_output | Match? |
|---|---|---|---|---|
| data_flow_9846 | evidence_14 | case_15 | `evidence_01` (log), not the image | Corroborated |

**Result: no image-derived finding is the *cited* evidence source in any of the 32 published samples.** The one overlapping resource — `data_flow_9846` (case_15, lucia-torres@gmail.com) — is cited via `evidence_01` (the connector log trace), and my pipeline reproduces case_15 exactly (14/14 steps match). The evidence_14 image corroborates the same resource+state (ACTIVE) but is not needed because the text log already establishes it.

After the cache fix below, every image entry's `resource_id`, identifier and timestamp matches what is visible in the screenshot and (where applicable) the corresponding case in `output.csv`.

## 3. Image-derived findings in the full 518-case run (outside the samples)

These are driven by the cache and are consistent with the transcribed content:

| Case | Finding | Image evidence_id | Image content | Pipeline output |
|---|---|---|---|---|
| case_34 | account_5762 PRESENT | evidence_04 | account_5762, haruka.s1838@zoho.com | matches |
| case_33 | export_1260 PRESENT | evidence_23 | export_1260, ingrid.dahl@outlook.com | matches |
| case_45 | audit_record_003 PRESENT | evidence_37 | audit_record_003, user_03 | matches |
| case_50 | analytics_7438 PRESENT | evidence_19 | analytics_7438, user_20 | matches |
| case_55 | backup_9287 ACTIVE | evidence_32 | backup_9287, areed1433@gmail.com | matches |

## 4. Flagged entries (verify by eye)

| Image | Flag | Reason |
|---|---|---|
| evidence_04 `account_admin_console_20260926.png` | high impact | Drives case_34 (INCOMPLETE). A transcription error here flips a status. Verified: account_5762 / haruka.s1838@zoho.com matches. |
| evidence_37 `security_audit_explorer_20260905.png` | high impact | Drives case_45 (COMPLETE, retained security record must remain). Verified: audit_record_003 / user_03 matches. |
| evidence_32 `backup_restore_console_20260429.png` | high impact | Drives case_55 (INCOMPLETE, restore risk). Verified: backup_9287 / areed1433@gmail.com matches. |
| evidence_23 `customer_export_console_20260618.png` | medium impact | Drives case_33 (INCOMPLETE, remaining export). Verified: export_1260 / ingrid.dahl@outlook.com matches. |

**Previously flagged, now resolved:** evidence_19's email was `dariush.omar@icloud.com` in the cache; the screenshot shows `darwish.omar@icloud.com`, matching case_50's request email. Cache corrected; output.csv unchanged (matching was via `user_20` reference).

## 5. Two screenshots with no cache entry (extraction gaps)

| Image (file_id) | Content | Produces a step? | Reason |
|---|---|---|---|
| evidence_42 `deleted_account_reference_20260310.png` | Customer Reference Registry for `user_17` (Retained reference). Linked records: ORD-20263245 (Orders), CS-3245/CS-3378 (Support), FIN-7742 (Finance). | **No** | These are business record references (order/support/finance IDs); no resource id in the evidence vocabulary and no user-identity→resource mapping. Correctly left as an extraction gap. |
| evidence_46 `object_storage_browser_20260213.png` | Nimbus Object Storage bucket `northstar-customer-data`, 8 object files (PDFs/zips/images). No user identity shown. | **No** | Object/file names only; no resource id in the evidence vocabulary and no subject linkage. Correctly left as an extraction gap. |
## 6. Second-model re-extraction

Not performed: no second vision model / router endpoint is configured
(`config/models.yaml` defines the schema but no endpoint is wired) and no provider
API key was available. Per Item 1, `code/tools/build_vision_cache.py` is the
regeneration entry point; when a key and router are configured it can re-extract
any image and the outputs can be compared here.

**Escalation:** all entries above should be opened and checked by eye before
submission. evidence_19's email has been corrected in the cache; the remaining
four high-impact images still warrant a visual check. Do not resolve silently.

## 7. Top 5 images to verify by eye (by risk)

1. `evidence_04 account_admin_console_20260926.png` — drives case_34 status.
2. `evidence_37 security_audit_explorer_20260905.png` — drives case_45 status.
3. `evidence_32 backup_restore_console_20260429.png` — drives case_55 status.
4. `evidence_23 customer_export_console_20260618.png` — drives case_33 status.
5. `evidence_19 analytics_profile_view_20260713.png` — email now corrected to `darwish.omar@icloud.com`; worth one final visual check.
