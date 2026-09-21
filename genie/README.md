# Genie Agents

`recall_insights.json` is the `serialized_space` for **Account Signals: Rep
Comprehension**, a Genie Agent for managers: scores, misses, retries, and the
topic queue. It is not for account financials. Genie writes its own SQL, so it
is never given the XBRL tables; see SCOPE.md, 2026-09-21.

The file is the source of truth. Edit it here, then push it:

```bash
databricks genie update-space <space_id> --profile <name> \
  --json "{\"serialized_space\": $(jq -c . genie/recall_insights.json | jq -Rs .)}"
```

- **Dev space ID:** `01f1b5ff6897185fb2276a2d5b9bba42` (warehouse `c183f02d121f496a`).
- **Table names hardcode `workspace.account_signals_dev`.** For prod or another
  workspace, replace that string in the file before `create-space`.
- **IDs:** every sample question, example SQL, and the one text instruction
  needs a unique 32-char hex `id`. The scheme is prefix `1`/`2`/`3` per list
  plus a counter. The API allows only one `text_instructions` item.
- **Data freshness:** the tables are refreshed by the grading job at 20:00 ET
  on weekdays, not live from Lakebase.
