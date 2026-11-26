# Scorecard

| Iter | Change | Kept | Overall | Dev | Holdout | Answerable | Multi-doc | Ambiguous | Out-of-scope | Retrieval miss | Prompt defect | Hallucination | Wrong refusal |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | baseline | yes | 20/22 (91%) | 88% | 100% | 9/10 | 2/2 | 3/4 | 6/6 | 1 | 1 | 0 | 0 |
| 1 | abstain gate fired on 1 miss(es): min_relevance 0.2 -> 0.12 | reverted | 20/22 (91%) | 88% | 100% | 9/10 | 2/2 | 3/4 | 6/6 | 0 | 1 | 0 | 1 |
| 2 | top_k 6 -> 8 | reverted | 20/22 (91%) | 88% | 100% | 9/10 | 2/2 | 3/4 | 6/6 | 1 | 1 | 0 | 0 |
