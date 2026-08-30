Unit-test the pure logic that does not need a workspace:

- delta arithmetic in `src/pipelines/xbrl_deltas.py` (QoQ/YoY, division by zero,
  missing quarters)
- chunking boundaries in `src/pipelines/chunk_and_embed.py`
- grade JSON parsing in `src/grading/grade.py`

Prompt quality belongs in MLflow evaluation, not here.
