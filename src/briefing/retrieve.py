"""Assemble the evidence bundle for one account's briefing.

Two retrievals, deliberately different in kind:

  1. Financial deltas  -- a direct SQL read of silver_financial_deltas.
     Exact numbers, no similarity search, no paraphrase.
  2. Prose context     -- Vector Search over silver_doc_chunks, filtered to
     this account, then top-k per source_type so one noisy feed cannot crowd
     out the others.

Writing to gold_briefing_evidence keeps the inputs to each briefing
inspectable after the fact — useful when a grade looks wrong and you need to
tell a bad recap from a bad brief.
"""

# TODO: VectorSearchClient(...).get_index(...).similarity_search(
#           filters={"account_id": account_id}, num_results=k)
# TODO: enforce per-source_type quotas before writing
