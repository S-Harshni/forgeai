# Scorecard

| Iter | Change | Kept | Overall | Dev | Holdout | Answerable | Multi-doc | Ambiguous | Out-of-scope | Retrieval miss | Prompt defect | Hallucination | Wrong refusal |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | baseline | yes | 19/23 (83%) | 81% | 86% | 9/10 | 2/3 | 3/4 | 5/6 | 0 | 2 | 1 | 1 |
| 1 | FORMAT: require complete answers with specific figures | yes | 19/23 (83%) | 88% | 71% | 8/10 | 3/3 | 3/4 | 5/6 | 0 | 3 | 1 | 0 |
| 2 | max_words 150 -> 210 | reverted | 19/23 (83%) | 81% | 86% | 9/10 | 3/3 | 3/4 | 4/6 | 0 | 2 | 2 | 0 |
