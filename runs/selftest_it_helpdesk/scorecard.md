# Scorecard

| Iter | Change | Kept | Overall | Dev | Holdout | Answerable | Multi-doc | Ambiguous | Out-of-scope | Retrieval miss | Prompt defect | Hallucination | Wrong refusal |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | baseline | yes | 20/22 (91%) | 88% | 100% | 10/10 | 3/3 | 3/4 | 4/5 | 1 | 0 | 1 | 0 |
| 1 | added generalised decline categories to REFUSAL section: ['requests_fo | yes | 21/22 (96%) | 94% | 100% | 10/10 | 3/3 | 3/4 | 5/5 | 1 | 0 | 0 | 0 |
| 2 | abstain gate fired on 1 miss(es): min_relevance 0.2 -> 0.12 | yes | 22/22 (100%) | 100% | 100% | 10/10 | 3/3 | 4/4 | 5/5 | 0 | 0 | 0 | 0 |
