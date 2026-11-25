# Scorecard

| Iter | Change | Kept | Overall | Dev | Holdout | Answerable | Multi-doc | Ambiguous | Out-of-scope | Retrieval miss | Prompt defect | Hallucination | Wrong refusal |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | baseline | yes | 15/22 (68%) | 67% | 71% | 8/9 | 2/3 | 1/4 | 4/6 | 3 | 1 | 2 | 1 |
| 1 | abstain gate fired on 3 miss(es): min_relevance 0.2 -> 0.12 | yes | 18/22 (82%) | 87% | 71% | 8/9 | 3/3 | 3/4 | 4/6 | 0 | 1 | 2 | 1 |
| 2 | added generalised decline categories to REFUSAL section: ['policy-rela | reverted | 17/22 (77%) | 80% | 71% | 8/9 | 3/3 | 3/4 | 3/6 | 0 | 1 | 3 | 1 |
| 3 | min_relevance 0.12 -> 0.19 (abstain more) | reverted | 16/22 (73%) | 73% | 71% | 8/9 | 2/3 | 2/4 | 4/6 | 2 | 1 | 2 | 1 |
