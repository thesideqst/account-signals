Unit-test the pure logic that does not need a workspace:

- delta arithmetic in `silver_financial_deltas` (`src/pipelines/xbrl_metrics.py`)
  (QoQ/YoY, division by zero, missing quarters)
- chunking boundaries in `src/pipelines/chunk_and_embed.py`
- grade JSON parsing in `src/grading/grade.py`

Prompt quality belongs in MLflow evaluation, not here.
