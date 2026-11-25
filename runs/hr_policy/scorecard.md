# Scorecard

| Iter | Change | Kept | Overall | Dev | Holdout | Answerable | Multi-doc | Ambiguous | Out-of-scope | Retrieval miss | Prompt defect | Hallucination | Wrong refusal |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | baseline | yes | 20/23 (87%) | 88% | 86% | 10/10 | 3/3 | 2/4 | 5/6 | 2 | 0 | 1 | 0 |
| 1 | abstain gate fired on 2 miss(es): min_relevance 0.2 -> 0.12 | reverted | 20/23 (87%) | 88% | 86% | 10/10 | 3/3 | 3/4 | 4/6 | 1 | 0 | 2 | 0 |
| 2 | top_k 4 -> 6 | reverted | 18/23 (78%) | 81% | 71% | 8/10 | 3/3 | 2/4 | 5/6 | 2 | 1 | 2 | 0 |
