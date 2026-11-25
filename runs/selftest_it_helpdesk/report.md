# ForgeAI report - Assistant

## Bottom line

- **Final score: 18/22 (82%)** - dev 87%, **holdout 71%** (holdout cases were never shown to the optimizer).
- Baseline was 15/22 (68%); 3 optimisation iteration(s); stop reason: reached max iterations (3).
- Judge calibration: 100% agreement on 21 synthetic known-label checks (0 false positives, 0 false negatives; by kind {'positive': '8/8', 'verbose-negative': '8/8', 'paraphrase-positive': '5/5'}; LLM judge consulted on 1).
- Cost: 161 LLM calls, 98,130 tokens, 7.0 min wall clock.

> **Overfitting warning:** dev beats holdout by 15 points - some fixes may be tuned to the dev questions rather than the domain.

## Groundability (requirements stage)

| Topic | Status | Relevance | Evidence |
|---|---|---|---|
| Account Lockouts | **partial** | 0.28 | short |
| Passwords | **supported** | 1.00 | Passwords must be at least 14 characters and are changed every 180 days.... |
| Laptop Replacement | **supported** | 0.28 | Hardware Standard laptops are replaced every 4 years.... |
| Internet Allowances | **supported** | 1.00 | Each vessel has a VSAT link with a monthly data allowance of 200 GB for business use.... |
| Support Hours | **supported** | 1.00 | short [evidence quote NOT found in docs - ignored] |

**Assumptions made without the requester (unattended mode):**
- What is the preferred tone for the assistant's communication with users? Should the assistant escalate any issues automatically or should the users be prompted to escalate? -> The IT service desk guide does not specify the preferred tone for the assistant's communication with users or whether issues should be escalated automatically. Therefore, the most conservative reasonable default is to assume the assistant should use a professional and friendly tone in their communication and prompt users to escalate issues as needed.
- How should the assistant handle cases where information is missing or unclear from the documents? Should it provide a default response or seek clarification? -> No preference stated in the brief; using the conservative default.
- Who should the assistant contact for escalation in case of critical issues not covered by the documents? -> No preference stated in the brief; using the conservative default.
- Should the assistant maintain a formal or more casual tone when addressing users? -> No preference stated in the brief; using the conservative default.
- What level of detail should the assistant provide in its responses? Should it include all the information from the documents or should it summarize the key points? -> No preference stated in the brief; using the conservative default.

## Tool selection

- `csv_lookup`: REJECTED - model proposed it but the brief gives no evidence it is needed
- `calculator`: not attached - no evidence in brief
- `current_date`: REJECTED - model proposed it but the brief gives no evidence it is needed
- `weather`: not attached - no evidence in brief

## What still fails and why

Failure mix: hallucination x2, prompt_defect x1, wrong_refusal x1

| Case | Type | Split | Category | Question | Diagnosis | Eval case suspect? |
|---|---|---|---|---|---|---|
| E03 | answerable | holdout | prompt_defect | How long does it take to get a new monitor or docking station through the IT portal? | facts were retrieved but the answer omitted them (coverage 0%) | - |
| E16 | ambiguous | dev | wrong_refusal | what is the ideal length for a single team issue reply? | refused although the answer was in the retrieved context | - |
| E18 | out_of_scope | dev | hallucination | I'm curious about the crew welfare internet policy. Can you tell me more about it?? | answered an out-of-scope question instead of refusing | - |
| E22 | out_of_scope | holdout | hallucination | Do you have any information on the current password policy for our accounts?? | answered an out-of-scope question instead of refusing | - |

**Likely root causes:**
- *Hallucinations*: the small model answered from general knowledge or stated figures absent from the excerpts; the grounding check caught them (they would have shipped otherwise).
- *Wrong refusals*: the passage was retrieved but the model declined - over-cautious refusal rules trade recall for safety.
- *Prompt defects*: relevant text was in context but the answer omitted key facts or broke the output contract (no citations) - a 3B model's instruction-following limit.

## Optimisation log

- **Iter 1** `retrieval_miss:lower_gate` - abstain gate fired on 3 miss(es): min_relevance 0.2 -> 0.12  
  why: 3 dev failure(s) classified retrieval_miss; targeted ['E10', 'E13', 'E14']; fixed ['E10', 'E13', 'E14']; newly broken none; overall 68% -> 82% (holdout 71% -> 71%); **kept**
- **Iter 2** `hallucination:refusal_topics` - added generalised decline categories to REFUSAL section: ['policy-related-questions', 'non-service-related-questions', 'personal-requests']  
  why: 1 dev failure(s) classified hallucination; targeted ['E18']; fixed none; newly broken ['E21']; overall 82% -> 77% (holdout 71% -> 71%); **reverted**
- **Iter 3** `hallucination:raise_gate` - min_relevance 0.12 -> 0.19 (abstain more)  
  why: 1 dev failure(s) classified hallucination; targeted ['E18']; fixed none; newly broken ['E10', 'E14']; overall 82% -> 73% (holdout 71% -> 71%); **reverted**

## Eval set provenance

- 22 cases, generated by the prompts in `prompts/evalgen_*.txt` (none hand-written).
- Generation attempts: 32; dropped as unverifiable (key fact not literally in source): 0; duplicates: 12; leaky/empty: 0; 'out-of-scope' questions the docs actually answer: 0; failed round-trip check (an independent model, reading only the source excerpt, could not reproduce the expected facts - i.e. a mislabeled case): 3.
- Known limitation: questions are generated from single excerpts, so they skew towards literal, well-formed phrasing; real users will be messier than this set.

## How the judge is defended

- Deterministic first: refusal detection, key-fact matching (exact numbers), citation validity, and an unsupported-number check against retrieved text.
- LLM judge (different model family from the generator) only rules on single facts that code could not match - pointwise, so no position bias; one fact at a time and truncated, so no verbosity reward.
- Calibrated on known-label synthetic answers, including padded wrong answers (results above).

## Cost by stage

| Stage | Calls | Tokens | Seconds |
|---|---|---|---|
| intake | 7 | 4,190 | 39.67 |
| guard | 5 | 2,256 | 12.3 |
| generator | 1 | 791 | 8.39 |
| evalgen | 32 | 9,471 | 65.85 |
| evalgen_roundtrip | 19 | 4,141 | 22.72 |
| calibration | 8 | 825 | 7.25 |
| judge | 13 | 1,848 | 6.17 |
| agent_answer | 75 | 74,444 | 255.2 |
| optimizer | 1 | 164 | 1.23 |
