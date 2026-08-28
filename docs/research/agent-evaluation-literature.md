# Designing a Serious Evaluation System for an LLM-Driven Personal Agent — Literature Research

**Purpose.** This document collects the primary literature, benchmark specifications, and first-party
engineering guidance that should drive the design of a serious evaluation system for **Cortex**, the core
LLM orchestration layer of the Botti personal AI agent. It is written to be acted on: each thematic section
ends in the design implications that survive contact with the sources.

**Scope of sources.** Everything cited below is a *primary* artifact: a peer-reviewed paper (arXiv / ACL /
NeurIPS / ICLR / ICML / TMLR / COLM page), the official repository of a benchmark or eval framework, or
first-party engineering guidance from OpenAI / Anthropic / METR / Stanford CRFM. Secondary write-ups and
aggregator posts are deliberately excluded. Source kinds are tagged as **[paper]** (peer-reviewed venue),
**[repo]** (official benchmark/framework repository), **[leaderboard]** (official leaderboard), **[blog]**
(first-party engineering/research post).

**Conflicts are flagged inline.** Where sources disagree (e.g., benchmark repos superseding their own
papers, or different reported numbers), the discrepancy is called out explicitly rather than smoothed over.

---

## Table of contents

1. [Executive summary](#1-executive-summary)
2. [Agent evaluation benchmarks: what they measure](#2-agent-evaluation-benchmarks-what-they-measure)
3. [LLM-as-judge methodology](#3-llm-as-judge-methodology)
4. [Statistical rigor in LLM evaluation](#4-statistical-rigor-in-llm-evaluation)
5. [Behavioral and regression testing of LLM systems](#5-behavioral-and-regression-testing-of-llm-systems)
6. [Agent-specific metrics beyond task success](#6-agent-specific-metrics-beyond-task-success)
7. [Production evaluation infrastructure](#7-production-evaluation-infrastructure)
8. [Implications for Cortex](#8-implications-for-cortex)
9. [Primary source list](#9-primary-source-list)

---

## 1. Executive summary

The literature converges on a small number of robust, mutually supporting principles:

1. **Prefer executable, state-based evaluation over subjective judgment whenever possible.** Every serious
   agent benchmark — SWE-bench (tests run against a real repo), τ-bench (final database state compared to an
   annotated goal state), WebArena/VisualWebArena (page + backend state checks), OSWorld (artifact-inspecting
   evaluation scripts), Terminal-Bench (test scripts in a sandboxed terminal) — grades *outcomes in an
   environment*, not prose (SWE-bench [repo] https://github.com/SWE-bench/SWE-bench; τ-bench [paper]
   https://arxiv.org/abs/2406.12045; WebArena [paper] https://arxiv.org/abs/2307.13854; OSWorld [paper]
   https://arxiv.org/abs/2404.07972; Terminal-Bench [repo] https://github.com/harbor-framework/terminal-bench).
   Anthropic's field guidance makes this the first design rule: "grade what the agent produced, not the path
   it took," and reserve LLM graders for what code cannot check (Anthropic, "Demystifying evals for AI
   agents" [blog] https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

2. **LLM-as-a-judge is a calibrated instrument, not a ground truth.** The founding paper documents position,
   verbosity, and self-enhancement biases and shows GPT-4 judges agree with humans at ~80%, about the same as
   human-human agreement (Zheng et al. [paper] https://arxiv.org/abs/2306.05685). Position bias alone can
   flip a ranking: reordering candidates lets Vicuna-13B "beat" ChatGPT on 66/80 queries under a ChatGPT
   judge (Wang et al. [paper] https://arxiv.org/abs/2305.17926). Judges must be de-biased (order swapping,
   length control, separate judge model), calibrated against human labels, and audited.

3. **Small evaluation sets cannot support small claims.** Accuracy on N examples has a binomial confidence
   interval of roughly ±1.96·√(p(1−p)/N): ±9.8 points at N=100, ±4.4 at N=500, ±3.1 at N=1000. The
   contamination study GSM1k reports standard errors explicitly and shows reported accuracies are
   prompt- and sample-dependent (Zhang et al. [paper] https://arxiv.org/abs/2405.00332). Anthropic's
   practical guidance: start with 20–50 tasks drawn from real failures (effect sizes are large early), and
   grow the suite as the system matures and effects shrink (Anthropic [blog]
   https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

4. **Agents need consistency metrics, not just average success.** τ-bench's `pass^k` (probability that *all*
   k trials succeed) shows state-of-the-art agents with >60% pass¹ drop below 25% at pass⁸ (τ-bench [paper]
   https://arxiv.org/abs/2406.12045). For a personal agent whose user relies on it, per-task consistency is
   the product metric that matters.

5. **Contamination is a first-class threat to every benchmark you use or build.** GSM1k measured accuracy
   drops of up to 8% between GSM8k and a matched fresh set, correlated with memorization signals (Zhang et
   al. [paper] https://arxiv.org/abs/2405.00332); the community position is that every benchmark should
   report contamination measurements (Sainz et al. [paper] https://arxiv.org/abs/2310.18018). The countermeasure
   is held-out, private, continuously-refreshed evaluation data — exactly what GAIA does by withholding
   answers (GAIA [paper] https://arxiv.org/abs/2311.12983) and what SWE-bench Multimodal does by keeping its
   test split private (SWE-bench [repo] https://github.com/SWE-bench/SWE-bench).

6. **Continuous evaluation in CI is now an industry-standard practice** with first-party tooling:
   OpenAI's evals framework (openai/evals [repo] https://github.com/openai/evals), promptfoo's CI/CD
   integration with quality gates (promptfoo docs [docs] https://www.promptfoo.dev/docs/integrations/ci-cd/),
   DeepEval's pytest-native CI gates (DeepEval docs [docs] https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd),
   and LangSmith's offline/online evaluation lifecycle (LangSmith docs [docs] https://docs.smith.langchain.com/evaluation).

7. **For Cortex specifically** (grounded in `CONTEXT.md`, `docs/ARCHITECTURE.md`, `docs/evidence-system.md`),
   the highest-leverage artifacts are: (a) a τ-bench-style domain task suite for the Execution module's agent
   loop with `pass^k`, cost, and latency as tracked metrics; (b) a regression suite over the Bayesian
   fact/confidence machinery whose oracles are the invariants already enumerated in `evidence-system.md`;
   (c) an LLM-as-judge pipeline with order-swapping, length control, a judge model distinct from the
   generator, and human calibration — critical because Memory's `llm` evidence source and any LLM-graded
   eval would otherwise share the same bias surface; (d) a CI eval gate with statistically honest thresholds.

---

## 2. Agent evaluation benchmarks: what they measure

The benchmark families below cover the design space of agent evaluation. For each: what it measures, task
format, metrics, and known limitations (contamination, saturation, cost). A comparison table follows at the
end of the section.

### 2.1 GAIA — general AI assistants

- **What it measures:** end-to-end assistant capability on real-world questions that require reasoning,
  multi-modality, web browsing, and tool use; explicitly "conceptually simple for humans yet challenging for
  advanced AIs" (GAIA [paper] https://arxiv.org/abs/2311.12983).
- **Task format:** 466 free-form questions with unambiguous answers; 300 answers withheld to power a
  leaderboard (GAIA [paper] https://arxiv.org/abs/2311.12983). Questions are released under gating with a
  no-reshare rule on validation/test sets (GAIA dataset [repo] https://huggingface.co/datasets/gaia-benchmark/GAIA).
- **Metrics:** exact-match correctness of the final answer; leaderboard hosted at
  https://huggingface.co/spaces/gaia-benchmark/leaderboard ([leaderboard]
  https://huggingface.co/spaces/gaia-benchmark/leaderboard).
- **Headline numbers:** human respondents 92% vs. 15% for GPT-4 with plugins at publication (GAIA [paper]
  https://arxiv.org/abs/2311.12983).
- **Limitations:** no interaction loop by default (single answer per question — tool use is exercised inside
  the answer, not graded as a trajectory); answer-withholding is its contamination defense, but the public
  166 questions are now long-saturated and any agent trained on web data risks leakage of the validation
  split. Note: the original GitHub repository (`gaia-benchmark/GAIA`) is no longer reachable (404 as of this
  writing); the authoritative artifacts are the paper, the gated dataset, and the leaderboard — flagging this
  because the paper's own links now point into a partially dismantled project.

### 2.2 SWE-bench / SWE-bench Verified — real-world software engineering

- **What it measures:** whether a model can resolve real GitHub issues by editing a codebase — "understanding
  and coordinating changes across multiple functions, classes, and even files," plus interacting with
  execution environments (SWE-bench [paper] https://arxiv.org/abs/2310.06770).
- **Task format:** 2,294 problems from 12 popular Python repos; model receives codebase + issue text and must
  produce a patch; graded by running `FAIL_TO_PASS` and `PASS_TO_PASS` unit tests in a Dockerized harness
  (SWE-bench [paper] https://arxiv.org/abs/2310.06770; [repo] https://github.com/SWE-bench/SWE-bench).
- **Metrics:** % of instances resolved (patch passes all tests); also cost (per-instance USD) and latency in
  the official harness (SWE-bench [repo] https://github.com/SWE-bench/SWE-bench).
- **Headline numbers:** best model at publication (Claude 2) resolved 1.96% (SWE-bench [paper]
  https://arxiv.org/abs/2310.06770); by Aug 2024 top agents scored ~20% on full SWE-bench, 43% on Lite
  (OpenAI, "Introducing SWE-bench Verified" [blog] https://openai.com/index/introducing-swe-bench-verified/);
  Anthropic reports LLMs progressed from ~40% to >80% on SWE-bench Verified in one year (Anthropic [blog]
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
- **SWE-bench Verified:** a human-curated subset of 500 instances; OpenAI + 93 professional developers
  annotated 1,699 test instances, flagging underspecified problem statements (38.3%) and unfair unit tests
  (61.1%), filtering 68.3% of the original set; annotations slice difficulty (196 easy <15 min, 45 hard
  >1 hr); GPT-4o resolves 33.2% (OpenAI [blog] https://openai.com/index/introducing-swe-bench-verified/).
  This is the canonical example of **benchmark repair**: the original set systematically underestimated
  models because some instances were impossible or ambiguous.
- **Limitations:** Python-only repos; saturation (see the 40%→80% figure); eval-time cost is heavy (the
  harness needs Docker images per repo, ~120 GB disk) (SWE-bench [repo] https://github.com/SWE-bench/SWE-bench);
  SWE-bench Multimodal keeps its test split private precisely to resist contamination (SWE-bench [repo]
  https://github.com/SWE-bench/SWE-bench).

### 2.3 AgentBench — multi-environment LLM-as-agent

- **What it measures:** reasoning and decision-making of LLMs acting as agents across 8 environments: 5 new
  (Operating System, Database, Knowledge Graph, Digital Card Game, Lateral Thinking Puzzles) plus 3 recompiled
  (ALFWorld household, WebShop, Mind2Web) (AgentBench [paper] https://arxiv.org/abs/2308.03688; [repo]
  https://github.com/THUDM/AgentBench).
- **Task format:** multi-turn interactive tasks (~4k/13k LLM calls for dev/test splits per the repo
  statistics) (AgentBench [repo] https://github.com/THUDM/AgentBench).
- **Metrics:** per-environment success; an aggregate leaderboard. A 2025 "AgentBench FC" revision re-packages
  it around function-calling with Dockerized deployments (AgentBench [repo] https://github.com/THUDM/AgentBench).
- **Limitations:** environment diversity is its strength but its scores are not comparable across
  environments (no single scalar meaning); several environments (e.g., Lateral Thinking Puzzles) are
  knowledge-heavy and contamination-prone; the repo is actively being reworked (the function-calling version
  supersedes v0.1/v0.2), so older published numbers are not reproducible against current code (AgentBench
  [repo] https://github.com/THUDM/AgentBench).

### 2.4 WebArena / VisualWebArena — web tasks

- **What it measures:** language-guided agents performing routine web tasks on fully functional, self-hosted
  websites across e-commerce, social forum, software development (GitLab), and content management domains,
  with tools (map, manuals) (WebArena [paper] https://arxiv.org/abs/2307.13854; [repo]
  https://github.com/web-arena-x/webarena).
- **Task format:** 812 tasks; agent observes an accessibility-tree or screenshot and issues browser actions;
  grading is **execution-based**: URL/page state checks plus backend state verification (e.g., an order was
  actually placed) (WebArena [repo] https://github.com/web-arena-x/webarena; Anthropic [blog]
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
- **Headline numbers:** best GPT-4-based agent 14.41% vs. human 78.24% at publication (WebArena [paper]
  https://arxiv.org/abs/2307.13854). VisualWebArena extends to visually-grounded tasks (910 tasks; human
  success ~89% on the human-evaluated subset; GPT-4V+SoM as strongest baseline) (VisualWebArena [paper]
  https://arxiv.org/abs/2401.13649; [repo] https://github.com/web-arena-x/visualwebarena).
- **Limitations:** expensive and fiddly to self-host (Docker stack of demo sites, login cookies, environment
  reset between runs) (WebArena [repo] https://github.com/web-arena-x/webarena); the dataset had annotation
  bugs fixed in v0.2.0 — i.e., even the authors re-graded after release (WebArena [repo]
  https://github.com/web-arena-x/webarena); web benchmarks age because real websites change, which the
  authors partially mitigate by self-hosting; saturation is now a concern for the original 812 tasks given
  how widely it is used.

### 2.5 τ-bench / τ²-bench (now τ³-bench) — tool-agent-user interaction

- **What it measures:** conversational agents following **domain-specific policy rules** while using API
  tools in simulated customer-service domains (airline, retail; later telecom, banking) — explicitly the
  interaction between agent, user, and shared state (τ-bench [paper] https://arxiv.org/abs/2406.12045; [repo]
  https://github.com/sierra-research/tau-bench).
- **Task format:** dynamic multi-turn conversation between an LLM-simulated user and the agent; the agent
  picks tools from a domain API set and must obey a written policy (e.g., refund rules). **Grading compares
  the final database state against an annotated goal state** — outcome-based, not transcript-based
  (τ-bench [paper] https://arxiv.org/abs/2406.12045).
- **Metrics:** `pass^k` — probability all k trials succeed (reliability), plus **cost per task** (agent
  $0.38 / user-sim $0.23 per task on τ-retail with gpt-4o; ~$200 for one trial over the retail suite; ~96%
  of agent cost is input tokens from the long policy+tool system prompt) (τ-bench [paper]
  https://arxiv.org/abs/2406.12045). This is the canonical source for **cost/latency per task** as first-class
  agent metrics.
- **Headline numbers:** gpt-4o succeeds on <50% of tasks; `pass^8` <25% in retail despite >60% pass¹ —
  agents are "quite inconsistent" (τ-bench [paper] https://arxiv.org/abs/2406.12045).
- **τ²-bench:** adds a **dual-control** Dec-POMDP telecom domain where the *user also acts in the shared
  environment*, a compositional task generator for controllable difficulty, and a tool-constrained user
  simulator; ablations separate reasoning errors from communication/coordination errors (τ²-bench [paper]
  https://arxiv.org/abs/2506.07982).
- **τ³-bench (current):** the `sierra-research/tau2-bench` repo now hosts τ³ with a knowledge-retrieval
  banking domain (RAG configs, embeddings, agentic search) and full-duplex voice evaluation (τ³ [repo]
  https://github.com/sierra-research/tau2-bench). **Versioning warning from the authors:** a July 2026
  grading fix means "results produced with tau2-bench < 1.0.1 are not comparable with >= 1.0.1" and old
  leaderboard entries were re-graded — a live demonstration that benchmark scores are only meaningful pinned
  to a dataset/grading version (τ³ [repo] https://github.com/sierra-research/tau2-bench).
- **Limitations:** simulated users are not real users (the user model influences difficulty); cost is
  material (the repo itself ships historical trajectories "because τ-bench might be expensive to run")
  (τ-bench [repo] https://github.com/sierra-research/tau-bench); task/grading bugs were real (75+ fixes in
  τ³, derived from an external audit) (τ³ [repo] https://github.com/sierra-research/tau2-bench).

### 2.6 OSWorld — open-ended computer use

- **What it measures:** multimodal agents on open-ended computer tasks in a **real, interactive OS** (Ubuntu,
  Windows, macOS) across arbitrary apps, file I/O, and multi-app workflows — "the first scalable real
  computer environment" (OSWorld [paper] https://arxiv.org/abs/2404.07972; [repo] https://github.com/xlang-ai/OSWorld).
- **Task format:** 369 tasks, each with an initial-state setup config and a **custom execution-based
  evaluation script** that inspects artifacts post-hoc (file state, app configs, DB contents, UI properties)
  (OSWorld [paper] https://arxiv.org/abs/2404.07972; Anthropic [blog]
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
- **Headline numbers:** humans >72.36% vs. best model 12.24%, struggling mainly with GUI grounding (OSWorld
  [paper] https://arxiv.org/abs/2404.07972).
- **Limitations:** heavy infra (VMs via VMware/VirtualBox/Docker/AWS); slow evals (the project's main
  complaint — AWS parallelization "can reduce evaluation time to within 1 hour"); **OSWorld-Verified (2025)**
  fixed community-reported issues and re-ran all model results, explicitly warning that new results are not
  comparable with old ones (OSWorld [repo] https://github.com/xlang-ai/OSWorld).

### 2.7 Terminal-Bench — terminal agents

- **What it measures:** agents completing end-to-end technical tasks in a sandboxed Linux terminal: compiling
  a kernel, training an ML model, server setup, security/forensics tasks (Terminal-Bench [repo]
  https://github.com/harbor-framework/terminal-bench; tbench.ai [site] https://www.tbench.ai/).
- **Task format:** each task = an English instruction + a **test script** (verifier) + an **oracle solution**;
  the harness connects an LLM agent to a Docker sandbox and runs the verifier (Terminal-Bench 1.0 archived
  repo [repo] https://github.com/harbor-framework/terminal-bench-1).
- **Metrics:** task resolution success rate on the leaderboard (tbench.ai [site] https://www.tbench.ai/).
- **Lineage/versioning (important):** Terminal-Bench 1.0 (80 tasks) → 2.0 (89 tasks) → 2.1 (inspired by
  Z.ai's "Terminal-Bench 2.0 Verified") → 3.0, now a **continuous benchmark** with tagged releases run under
  the Harbor framework (tbench.ai [site] https://www.tbench.ai/; [repo]
  https://github.com/harbor-framework/terminal-bench). The canonical academic citation has moved with it:
  the current repo's CITATION.cff points at the ICLR 2026 paper "Terminal-Bench: Benchmarking Agents on Hard,
  Realistic Tasks in Command Line Interfaces" (OpenReview link in the repo). Any claim about "Terminal-Bench"
  must state which version.
- **Limitations:** environment-setup and verifier flakiness are known (the README asks users to run oracle
  solutions 5× to confirm tasks work; Anthropic's audit found tasks where graders assumed unstated file
  paths, i.e., ambiguous specs producing unfair failures) (Terminal-Bench [repo]
  https://github.com/harbor-framework/terminal-bench; Anthropic [blog]
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

### 2.8 Berkeley Function Calling Leaderboard (BFCL)

- **What it measures:** function/tool-calling capability: simple, multiple, parallel, and parallel-multiple
  calls; AST-based and **executable** (functions actually run) checks; function-relevance detection (withhold
  a call when no function fits); plus REST API, SQL, Java, JavaScript categories; ~2k question-function-answer
  pairs across 40 sub-domains (BFCL launch blog [blog]
  https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html).
- **Metrics:** category accuracies (AST + execution), plus **cost and latency for all models** added to the
  leaderboard in April 2024 (BFCL launch blog [blog]
  https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html; Gorilla repo [repo]
  https://github.com/ShishirPatil/gorilla). Live leaderboard: https://gorilla.cs.berkeley.edu/leaderboard
  ([leaderboard] https://gorilla.cs.berkeley.edu/leaderboard.html).
- **Versioning:** V1 expert-curated → V2 enterprise-contributed ("tackling issues like bias and data
  contamination", dynamic real-world scenarios) → V3 multi-turn/multi-step state-based evaluation → V4
  "Agentic" (web search with multi-hop reasoning, agent memory, format sensitivity) (Gorilla repo [repo]
  https://github.com/ShishirPatil/gorilla).
- **Limitations:** single-turn V1 is near-saturated for frontier models; the leaderboard explicitly reports
  only category-level numbers, and some categories (chatting, SQL) are excluded from the leaderboard score
  because of evaluation ambiguity (BFCL launch blog [blog]
  https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html). Relevance detection is
  the most product-relevant category for a personal agent (it measures *when not to call a tool*).

### 2.9 METR long-horizon / time-horizon work

- **What it measures:** the *length* of software tasks AI agents can complete autonomously, as a capability
  metric. METR shows the time horizon (task length an agent completes with 50% success) has grown
  exponentially over ~6 years with a doubling time of ~7 months, extrapolating to agents completing
  days-to-weeks-long software tasks within a decade (METR, "Measuring AI Ability to Complete Long Software
  Tasks" [blog] https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/).
- **Task format:** task suites of varying lengths run against agents with scaffolding; **HCAST**
  (Human-Calibrated Autonomy Software Tasks) is the public benchmark distilled from this work
  (HCAST [paper] https://arxiv.org/abs/2503.17354).
- **Why it matters here:** it is the strongest primary argument that *task length / horizon* is a distinct,
  important axis from task success rate, and that evaluations must include long-horizon tasks or they will
  overestimate readiness for real personal-agent workloads. METR also documents **reward hacking on its own
  tasks** (agents exploiting bugs in scoring code), a caution for any automated grader (METR [blog]
  https://metr.org/blog/2025-06-05-recent-reward-hacking/).

### 2.10 Benchmarks comparison table

| Benchmark | What it grades | Task format | Metric | Infra cost | Contamination defense | Saturation status |
|---|---|---|---|---|---|---|
| GAIA | final answer correctness | 466 open Qs, 300 answers withheld | exact-match % | low (no env) | withheld answers, gated data | public subset saturated |
| SWE-bench / Verified | repo state after patch | 2,294 / 500 GitHub issues | % resolved (tests) | high (Docker) | Verified human-audit; Multimodal test set private | resolved 40%→80% in a year |
| AgentBench | per-env success | 8 interactive envs | per-env % | high | n/a (envs) | being reworked (FC v2) |
| WebArena / VWA | page + backend state | 812 / 910 web tasks | success % | high (self-hosted sites) | self-hosted sites | OG 812 aging |
| τ-bench / τ² / τ³ | final DB state vs goal | simulated user+agent convos | pass^k, cost, latency | medium (LLM user sims) | policy-based domains | frontier models <50% pass¹ |
| OSWorld | post-hoc artifacts | 369 OS tasks | success % | very high (VMs) | OSWorld-Verified re-grade | best 12.24% at pub |
| Terminal-Bench | verifier scripts in sandbox | 80→89→continuous tasks | resolution % | medium (Docker) | canary string, tagged releases | continuous, anti-saturation design |
| BFCL | function call AST/execution | ~2k pairs, 9 categories | category %, cost, latency | low | V2 enterprise re-curation | V1 near-saturated |
| METR time horizon | task length at 50% success | varied-length task suites | time horizon (hrs) | very high | n/a (private tasks) | 7-month doubling |

---

## 3. LLM-as-judge methodology

### 3.1 The founding result: MT-Bench and Chatbot Arena

Zheng et al. (2023) establish the empirical basis for LLM judges: GPT-4-as-judge reaches **over 80% agreement
with human preferences** — the same level as human-human agreement — on MT-bench (multi-turn) and Chatbot
Arena (crowdsourced battles) (Zheng et al. [paper] https://arxiv.org/abs/2306.05685). They explicitly
enumerate the failure modes:

- **Position bias** — the judge favors the first or second answer by position;
- **Verbosity bias** — longer answers are favored regardless of quality;
- **Self-enhancement bias** — the judge prefers its own generations;
- **Limited reasoning ability** — the judge can be wrong on hard reasoning.

Their proposed remedies include prompting the judge to reason before judging, swapping answer positions and
taking the consistent verdict, and using reference-guided judging (Zheng et al. [paper]
https://arxiv.org/abs/2306.05685). Repo with MT-bench questions, 3K expert votes, 30K preference
conversations: https://github.com/lm-sys/FastChat/tree/main/fastchat/llm_judge.

### 3.2 Position bias is exploitable

Wang et al. (2023) show position bias is not noise but a **controllable attack surface**: merely reordering
the two candidate responses flips rankings so that Vicuna-13B "beats" ChatGPT on 66 of 80 queries under a
ChatGPT judge (Wang et al. [paper] https://arxiv.org/abs/2305.17926). Their calibration framework:
(1) *Multiple Evidence Calibration* — judge must produce evidence before rating; (2) *Balanced Position
Calibration* — aggregate over both orderings; (3) *Human-in-the-loop* — escalate items with high position
instability to humans (Wang et al. [paper] https://arxiv.org/abs/2305.17926).

### 3.3 G-Eval: rubric + CoT + form-filling

G-Eval (Liu et al., 2024) frames judging as: generate a detailed **evaluation rubric/score criteria**
from the task, then have the judge produce a chain-of-thought followed by a **form-filled score** on a Likert
scale, with multiple sampling rounds averaged (G-Eval [paper] https://arxiv.org/abs/2303.16634). Reported
Spearman correlation with humans of 0.514 on summarization (outperforming prior automatic metrics at the
time). Two cautions the paper itself raises: LLM evaluators have a bias toward LLM-generated text, and
verbosity can inflate scores (G-Eval [paper] https://arxiv.org/abs/2303.16634). Code: https://github.com/nlpyang/geval.

### 3.4 LLM evaluation can stand in for expert human evaluation — with caveats

Chiang & Lee (ACL 2023) ran LLMs through the exact instruments used for expert human evaluation (same
instructions, samples, questions) on open-ended story generation and adversarial attacks and found LLM
ratings **consistent with expert human ratings**, and stable across instruction formatting and sampling
methods (Chiang & Lee [paper] https://arxiv.org/abs/2305.01937). Caveat from the same community: the
evaluated texts in their study were *not* LLM-generated, which sidesteps self-preference; the paper calls
out ethical/limitation considerations for LLM evaluation (Chiang & Lee [paper]
https://arxiv.org/abs/2305.01937).

### 3.5 Self-preference and self-recognition

Panickssery et al. (2024) demonstrate that the self-enhancement bias has a **causal mechanism**: LLMs can
recognize their own outputs out-of-the-box, and fine-tuning experiments show a linear correlation between
self-recognition accuracy and the strength of self-preference bias (Panickssery et al. [paper]
https://arxiv.org/abs/2404.13076). **Design consequence: the judge model should not be the generator model**
— or, if it must be, the eval must control for it (e.g., anonymization is insufficient; the models recognize
their own style).

### 3.6 Calibration of judges

- **AutoCalibrate** (Liu et al., 2023) calibrates an off-the-shelf LLM evaluator toward human labels without
  gradient training: draft scoring criteria via in-context learning, select best-performing criteria,
  self-refine them; significantly improves correlation with expert evaluation across multiple NLG quality
  datasets (Liu et al. [paper] https://arxiv.org/abs/2309.13308).
- **Length control** (Dubois et al., COLM 2024) removes the single largest known confounder in pairwise
  judges (length) by regressing out length effects before ranking ("Length-Controlled AlpacaEval") — a
  simple, effective debiasing that other suites have adopted (Dubois et al. [paper]
  https://arxiv.org/abs/2404.04475).
- **Pairwise vs pointwise:** pairwise comparison ("which is better?") is more reliable than absolute scoring
  for ranking, but only gives relative signal; pointwise rubric scoring (G-Eval style) gives absolute
  signal for thresholds/gates. LangSmith's docs codify this trade-off: pairwise works when "choosing the more
  informative of two summaries is often easier than assigning an absolute score," and both are supported as
  evaluator types (LangSmith docs [docs] https://docs.smith.langchain.com/evaluation). Industry guidance
  adds: rubric-based scoring, natural-language assertions, and multi-judge consensus are the model-based
  grader toolbox, and model graders "require calibration with human graders for accuracy" (Anthropic [blog]
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

### 3.7 Rubric design

Consensus across the sources: (a) write criteria before judging (G-Eval rubric step, [paper]
https://arxiv.org/abs/2303.16634); (b) draft criteria from a few labeled examples and refine by selection
(AutoCalibrate [paper] https://arxiv.org/abs/2309.13308); (c) "vague rubrics produce inconsistent judgments"
and a task is only well-specified when two domain experts reach the same verdict independently (Anthropic
[blog] https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents); (d) include both positive and
negative criteria (class-imbalanced evals create one-sided optimization — Anthropic's search-trigger example:
test both "should search" and "should not search" cases) (Anthropic [blog]
https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

---

## 4. Statistical rigor in LLM evaluation

### 4.1 HELM: holistic, multi-metric, transparent

HELM (Liang et al., TMLR 2023) is the reference framework for making evaluation trustworthy rather than
score-chasing: it measures **7 metrics (accuracy, calibration, robustness, fairness, bias, toxicity,
efficiency) across 16 core scenarios** per model, publishes all raw prompts and completions, and standardizes
conditions across 30 models (previously "models on average were evaluated on just 17.9% of the core HELM
scenarios") (HELM [paper] https://arxiv.org/abs/2211.09110). HELM's uncertainty handling — per-scenario
bootstrap confidence intervals and calibrated uncertainty reporting — is part of its leaderboard tooling
(HELM [repo] https://github.com/stanford-crfm/helm). Note for planning: HELM entered **maintenance mode on
June 1, 2026**, per the repo README — its methodology remains the reference, but the project is no longer
extended (HELM [repo] https://github.com/stanford-crfm/helm). The lesson for Cortex: report accuracy *with*
calibration, robustness, and cost; never a single number.

### 4.2 Small samples: how many examples do you need to trust a delta?

- Accuracy on N binary-graded examples is a binomial proportion. The 95% CI half-width is
  ±1.96·√(p(1−p)/N); at p≈0.5: N=100 → ±9.8pp, N=300 → ±5.7pp, N=500 → ±4.4pp, N=1000 → ±3.1pp
  (computed from the standard normal approximation; the same math underpins the analyses cited below).
- The **GSM1k** study (Zhang et al., NeurIPS 2024 D&B) is the named primary source on evaluation variance:
  it commissioned a fresh, matched 1,000-question set to test contamination, found accuracy drops of up to
  8% between GSM8k and GSM1k for some model families, and reports **standard errors and statistical
  significance in its appendix**, noting results are "highly prompt dependent" — ablating even which few-shot
  examples are chosen shifts numbers materially (Zhang et al. [paper] https://arxiv.org/abs/2405.00332).
- Anthropic's field guidance matches the math: "20-50 simple tasks drawn from real failures is a great
  start… this large effect size means small sample sizes suffice. More mature agents may need larger, more
  difficult evals to detect smaller effects" (Anthropic [blog]
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
- Practical synthesis: for a CI gate that must distinguish a real regression from noise, threshold on
  *effect size relative to the CI half-width*, not on raw score movement. A 3–5pp movement on a 100-example
  suite is not evidence of anything; the same movement on a 500-example suite is.

### 4.3 Error bars vs. model ranking

HELM's multi-metric philosophy plus the GSM1k variance findings imply: never rank on point estimates without
error bars; per-sample (not aggregated) result storage is what makes re-analysis possible (HELM publishes
raw prompts/completions, [paper] https://arxiv.org/abs/2211.09110). The benchmarks themselves model this:
OSWorld and τ³ re-graded and re-ranked results after fixing bugs, warning that old and new scores are
incomparable (OSWorld [repo] https://github.com/xlang-ai/OSWorld; τ³ [repo]
https://github.com/sierra-research/tau2-bench) — i.e., even headline leaderboards are provisional and must be
pinned to a version.

### 4.4 Contamination measurement as a statistical practice

Sainz et al. argue every benchmark evaluation should measure contamination per-benchmark (position paper,
EMNLP 2024 Findings) (Sainz et al. [paper] https://arxiv.org/abs/2310.18018); the CONDA shared task turned
this into a community measurement exercise (Sainz et al. [paper] https://arxiv.org/abs/2407.21530). GSM1k
operationalizes the measurement (memorization proxies correlate with performance gaps, Spearman r²=0.36)
(Zhang et al. [paper] https://arxiv.org/abs/2405.00332). Design consequence: any golden dataset Cortex
builds from *user data* is safe; any golden dataset built from *public text* needs a contamination strategy
(holdout, refresh, or private generation).

---

## 5. Behavioral and regression testing of LLM systems

### 5.1 CheckList: behavioral testing instead of accuracy-only

Ribeiro et al. (ACL 2020) introduced **behavioral testing** for NLP models: held-out accuracy overestimates
models and gives no guidance on *what* fails. CheckList provides (a) a capability × test-type matrix and
(b) three test types that have become the industry vocabulary (CheckList [paper]
https://arxiv.org/abs/2005.04118; [repo] https://github.com/marcotcr/checklist):

- **MFT — Minimum Functionality Tests:** a small batch of examples a capability should always pass
  (like unit tests).
- **INV — Invariance tests:** perturb inputs along a dimension that should not change the output; assert
  output stability.
- **DIR — Directional expectation tests:** perturb inputs along a dimension that *should* change the output
  in a predictable direction; assert the direction.

Evidence of value: teams using CheckList found "critical failures in both commercial and state-of-art
models"; in user studies, practitioners with CheckList created twice as many tests and found almost three
times as many bugs (CheckList [paper] https://arxiv.org/abs/2005.04118).

### 5.2 Eval-driven development and regression suites

Anthropic's engineering guidance formalizes the lifecycle (Anthropic [blog]
https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents):

- **Capability ("quality") evals** ask "what can this agent do well?" — start at a low pass rate to give a
  hill to climb.
- **Regression evals** ask "does it still do everything it used to?" — should sit near 100% pass; a drop is
  a bug signal.
- Capability evals "graduate" into regression suites once they pass reliably, and both run continuously.
- Start by converting **manual checks and user-reported failures** into test cases; prioritize by user
  impact; write tasks with **reference solutions** so a task is provably solvable and graders verifiable.
- Build **balanced problem sets** (positive and negative cases) to avoid one-sided optimization.
- An agent failing 0% across many trials is usually a **broken task, not a broken agent** — inspect the task
  spec first.

### 5.3 Golden datasets, holdout sets, contamination control

- GAIA withholds 300/466 answers to keep its leaderboard honest (GAIA [paper] https://arxiv.org/abs/2311.12983);
  SWE-bench Multimodal keeps its test split private (SWE-bench [repo] https://github.com/SWE-bench/SWE-bench);
  BFCL V2 re-curated with enterprise data specifically to fight bias/contamination (Gorilla repo [repo]
  https://github.com/ShishirPatil/gorilla).
- LangSmith's evaluation concepts codify dataset hygiene: curated examples as ground truth, dataset **splits**
  (train/validation/test) "to avoid overfitting, where a model performs well on training data but poorly on
  unseen data," and production traces feeding new examples (LangSmith docs [docs]
  https://docs.smith.langchain.com/evaluation).
- For LLM systems (as opposed to trained models), "overfitting" is replaced by **prompt/model memorization of
  the eval data**; the GSM1k result is the canonical demonstration that this happens and is measurable
  (Zhang et al. [paper] https://arxiv.org/abs/2405.00332).

### 5.4 Continuous evaluation in CI — industry patterns (primary docs)

- **OpenAI evals**: a framework plus an open registry of benchmarks, runnable against private data with
  "model-graded" YAML evals; the README positions evals as "one of the most impactful things you can do" for
  understanding how model versions affect your use case (openai/evals [repo] https://github.com/openai/evals).
- **promptfoo**: declarative config, CLI + CI/CD integration; supports **quality gates** (fail the build
  below a pass-rate threshold), JUnit output for native CI viewers, GitHub Actions workflows triggered on
  prompt changes, red-teaming scans, and **token/API cost tracking over time** (promptfoo docs [docs]
  https://www.promptfoo.dev/docs/integrations/ci-cd/; [repo] https://github.com/promptfoo/promptfoo).
- **DeepEval**: pytest-native (`assert_test`, `deepeval test run`) so the same evals run locally and in CI
  per push/PR; goldens loaded from code/CSV/JSON/cloud; supports end-to-end, component-level, and
  trajectory-based agent evals with agentic metrics (Task Completion, Tool Correctness, Step Efficiency,
  Plan Adherence) (DeepEval docs [docs]
  https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd; [repo] https://github.com/confident-ai/deepeval).
- **LangSmith**: offline evals (datasets/experiments: benchmarking, regression testing, unit testing,
  backtesting) + online evals (production runs/threads with reference-free evaluators), with annotation
  queues for human review feeding back into datasets (LangSmith docs [docs]
  https://docs.smith.langchain.com/evaluation).
- **RAGAS**: reference-free RAG pipeline evaluation (retrieval relevance, faithfulness of generation to
  retrieved context) without ground-truth annotations (RAGAS [paper] https://arxiv.org/abs/2309.15217; [repo]
  https://github.com/explodinggradients/ragas) — the standard for retrieval-system regression in CI.

---

## 6. Agent-specific metrics beyond task success

### 6.1 Intermediate milestones / process rewards

"Let's Verify Step by Step" (Lightman et al., 2024) showed **process supervision (per-step feedback)
significantly outperforms outcome supervision** for training reliable reasoners (78% on MATH subset; released
PRM800K: 800k step-level human labels) (Lightman et al. [paper] https://arxiv.org/abs/2305.20050). For
evaluation (not training), the transferable point: **step-level signal is more informative than a single
outcome bit**, both for debugging and for rewarding partial progress. Agent-as-a-Judge makes the same move on
the evaluation side: outcome-only grading "ignores the step-by-step nature of agentic systems," so they
evaluate the whole task-solving process with an agentic judge (Zhuge et al. [paper]
https://arxiv.org/abs/2410.10934). Anthropic's partial-credit guidance is the practical corollary: "A support
agent that correctly identifies the problem and verifies the customer but fails to process a refund is
meaningfully better than one that fails immediately. It's important to represent this continuum of success
in results" (Anthropic [blog] https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

### 6.2 Tool-call accuracy and efficiency

- BFCL grades function-call **ASTs and executions**, including relevance detection (the "don't call anything"
  case) and reports **cost and latency per model** (BFCL launch blog [blog]
  https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html).
- τ-bench reports **cost per task and pass^k consistency** and shows cost is dominated by prompt length
  (policy + tool definitions ≈ 96% of token spend) — a direct lever for agent design (τ-bench [paper]
  https://arxiv.org/abs/2406.12045).
- Anthropic's eval spec treats tool-call verification as a first-class grader ("tools used, parameters") and
  transcript metrics (`n_turns`, `n_toolcalls`, `n_total_tokens`, latency) as tracked metrics on every task
  (Anthropic [blog] https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

### 6.3 Trajectory evaluation: Agent-as-a-Judge

Zhuge et al. (2024) introduce **Agent-as-a-Judge**: an agentic system evaluates other agentic systems,
providing intermediate feedback over the entire task-solving process; on DevAI (55 realistic AI development
tasks with 365 hierarchical user requirements) it "dramatically outperforms LLM-as-a-Judge and is as reliable
as our human evaluation baseline" (Zhuge et al. [paper] https://arxiv.org/abs/2410.10934). Caveat: this is a
single-benchmark proof of concept; treat "as reliable as human" as bounded to that setting.

### 6.4 Robustness to perturbations

HELM measures robustness as a first-class metric alongside accuracy (HELM [paper] https://arxiv.org/abs/2211.09110).
CheckList's INV/DIR tests are the concrete mechanism (perturbation sets with fixed expectations) (CheckList
[paper] https://arxiv.org/abs/2005.04118). For agent systems, METR's reward-hacking observations add the
adversarial robustness dimension: agents exploit grader bugs, so graders must be treated as attack surface
(METR [blog] https://metr.org/blog/2025-06-05-recent-reward-hacking/).

### 6.5 Safety / refusal behavior

Safety-relevant evaluation is part of HELM's 7 metrics (bias, toxicity; targeted safety scenarios) (HELM
[paper] https://arxiv.org/abs/2211.09110). For conversational agents, refusal/policy compliance is embedded
in τ-bench's design (policy rules the agent must follow, graded via final state) (τ-bench [paper]
https://arxiv.org/abs/2406.12045). DeepEval ships dedicated safety metrics (bias, toxicity, PII leakage,
misuse, role violation) usable as CI gates (DeepEval docs [docs]
https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd). Note: no single primary source in this set
defines a complete safety eval for a *personal* agent; the defensible minimum is refusal-rate tests on
curated sensitive prompts plus policy-compliance tasks in the domain suite.

### 6.6 Cost/latency budgets

Cost and latency are first-class metrics in BFCL (per-model cost/latency on the leaderboard) and τ-bench
(cost per task, input-dominated) (BFCL [blog] https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html;
τ-bench [paper] https://arxiv.org/abs/2406.12045). Anthropic recommends tracking "latency, token usage, cost
per task, and error rates… on a static bank of tasks" — i.e., a fixed task bank doubles as a cost/latency
regression harness (Anthropic [blog] https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
promptfoo's CI docs likewise list "cost control: track token usage and API costs over time" among the reasons
to run evals in CI (promptfoo docs [docs] https://www.promptfoo.dev/docs/integrations/ci-cd/).

---

## 7. Production evaluation infrastructure

### 7.1 Dataset curation and versioning

- Curate from **production failures and manual checks first** (Anthropic [blog]
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents; LangSmith [docs]
  https://docs.smith.langchain.com/evaluation); synthetic data is a supplement, best templated on curated
  examples (LangSmith [docs] https://docs.smith.langchain.com/evaluation; DeepEval's Golden Synthesizer is a
  concrete tool, [docs] https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd).
- **Version everything:** the τ³-bench v1.0.1 warning ("results produced with tau2-bench < 1.0.1 are not
  comparable") and OSWorld-Verified re-grade are the cautionary tales (τ³ [repo]
  https://github.com/sierra-research/tau2-bench; OSWorld [repo] https://github.com/xlang-ai/OSWorld).
  LangSmith's dataset/experiment model (a dataset is immutable-ish; an experiment is a versioned run against
  it; experiments are comparable side-by-side) is the industry answer (LangSmith [docs]
  https://docs.smith.langchain.com/evaluation).

### 7.2 Human-in-the-loop labeling and judge auditing

- SWE-bench Verified is the template for human quality control of eval data: multiple annotators per sample
  (3×), onboarding tests for annotators, conservative ensembling (highest severity wins), published rubric,
  difficulty labels (OpenAI [blog] https://openai.com/index/introducing-swe-bench-verified/).
- For LLM judges: human graders are the gold standard used to **calibrate** model-based graders (Anthropic
  [blog] https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents); AutoCalibrate shows the
  calibration loop (human labels → criteria drafting → selection → self-refinement) (Liu et al. [paper]
  https://arxiv.org/abs/2309.13308); LangSmith annotation queues operationalize ongoing human review of
  production runs (LangSmith [docs] https://docs.smith.langchain.com/evaluation).

### 7.3 Eval gates in CI

promptfoo (quality gates with fail thresholds, JUnit, GitHub Actions), DeepEval (pytest asserts), and
LangSmith (offline experiments as release gates) all implement the same pattern: **the eval suite runs on
every push/PR; a threshold breach fails the build** (promptfoo docs [docs]
https://www.promptfoo.dev/docs/integrations/ci-cd/; DeepEval docs [docs]
https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd; LangSmith docs [docs]
https://docs.smith.langchain.com/evaluation). Statistically honest gates follow §4.2: compare against CI
half-widths, not raw deltas.

### 7.4 Prompt/model version pinning

LangSmith datasets/experiments pin the exact prompt + model configuration per experiment, enabling
side-by-side comparison of "different prompts or LLMs" (LangSmith [docs]
https://docs.smith.langchain.com/evaluation). Anthropic: evals are what let teams "quickly determine the
model's strengths, tune their prompts, and upgrade in days" when new models arrive — implying the eval
pipeline is pinned while the model under test varies (Anthropic [blog]
https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents). BFCL's per-category and per-version
leaderboards (V1–V4) model pinning at benchmark scale (Gorilla repo [repo]
https://github.com/ShishirPatil/gorilla).

### 7.5 Eval for memory/retrieval systems

- **RAGAS** provides reference-free metrics for the retrieval-and-generation pipeline: retrieval relevance
  and generation faithfulness to context, "without having to rely on ground truth human annotations" —
  designed for fast iteration cycles (RAGAS [paper] https://arxiv.org/abs/2309.15217; [repo]
  https://github.com/explodinggradients/ragas).
- DeepEval's RAG metric family (answer relevancy, faithfulness, contextual precision/recall/relevancy) is
  the same family in test form (DeepEval [repo] https://github.com/confident-ai/deepeval).
- τ³-bench's `banking_knowledge` domain evaluates retrieval-augmented conversational agents against
  verifiable goals, extending the RAGAS idea to agents (τ³ [repo] https://github.com/sierra-research/tau2-bench).

### 7.6 Regression suites for fact/confidence updates

No public benchmark covers Bayesian fact-confidence bookkeeping; this is a bespoke surface. The applicable
primary sources are: CheckList's INV/DIR structure for the tests (perturb input → assert posterior behavior)
(CheckList [paper] https://arxiv.org/abs/2005.04118); HELM's calibration metric for confidence-calibration
checks (HELM [paper] https://arxiv.org/abs/2211.09110); and the GSM1k contamination methodology for guarding
LLM-extracted facts against dataset memorization (Zhang et al. [paper] https://arxiv.org/abs/2405.00332).
Cortex's `evidence-system.md` already enumerates the invariants that *are* the regression oracle (see §8).

### 7.7 Cost management

τ-bench's cost-per-task accounting (input-token-dominated) (τ-bench [paper] https://arxiv.org/abs/2406.12045),
BFCL's per-model cost metrics ([blog] https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html),
and promptfoo's CI cost tracking ([docs] https://www.promptfoo.dev/docs/integrations/ci-cd/) together
establish cost as a tracked, gated metric. HELM's efficiency metric is the research-side counterpart (HELM
[paper] https://arxiv.org/abs/2211.09110).

---

## 8. Implications for Cortex

Grounded in the repo's own documents: `CONTEXT.md` (LLMClient factory with OpenAI/Anthropic providers,
CircuitBreakerLLMClient per module, AgentLoop emitting LoopEvents, SSE `/chat/stream`, Facts with Bayesian
Confidence updated by Evidence with likelihood ratios), `docs/ARCHITECTURE.md` (event bus; Interaction is a
thin interface; Execution runs the agentic loop; Memory stores facts; Learning detects patterns), and
`docs/evidence-system.md` (evidence model, update rule, invariants, windows, sweep).

### 8.1 Execution module — the agent loop

- **Use a τ-bench-style domain suite as the core eval** for the AgentLoop: simulated-user conversations over
  domain tools, graded by **final state against a goal state**, with `pass^k` (start with k=1, add k≥3 once
  pass¹ stabilizes) (τ-bench [paper] https://arxiv.org/abs/2406.12045). Cortex's tool ecosystem and goals
  (`goal.created`/`goal.status`/`goal.completed` events in ARCHITECTURE.md) map directly onto the
  task/goal-state pattern.
- **GAIA-style question suites** measure end-to-end autonomy (reasoning + tool use) with exact-match grading
  (GAIA [paper] https://arxiv.org/abs/2311.12983) — cheap, no environment, good as a capability smoke test.
- **The LoopEvent stream is already a transcript.** `ResponseDoneEvent` carries tool names and iteration
  count (CONTEXT.md) — the transcript-level metrics Anthropic recommends (`n_turns`, `n_toolcalls`,
  tokens, latency) can be recorded with zero new instrumentation (Anthropic [blog]
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents). Track **cost per task** as a
  gate metric; τ-bench shows input tokens dominate cost, which is directly relevant to Cortex's policy-heavy
  system prompts (τ-bench [paper] https://arxiv.org/abs/2406.12045).
- **Consistency over average:** a personal agent that "usually" works is not a personal agent; adopt pass^k
  semantics for anything user-facing (τ-bench [paper] https://arxiv.org/abs/2406.12045).
- **Robustness grading:** run targeted suites with provider faults injected (the CircuitBreaker is Cortex's
  resilience mechanism — eval that a CLOSED→OPEN→HALF_OPEN cycle under load degrades gracefully, per the
  CircuitBreaker vocabulary in CONTEXT.md). Perturbation methodology: CheckList INV/DIR (CheckList [paper]
  https://arxiv.org/abs/2005.04118).
- **Long-horizon tasks** per METR: add at least a small set of multi-minute-to-multi-hour goal tasks so the
  suite doesn't only measure short-horizon behavior (METR [blog]
  https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/).

### 8.2 Memory module — facts, confidence, retrieval

- **The regression oracle already exists:** `evidence-system.md` lists invariants with negation→test pairs
  (stored confidence equals posterior over active evidence; static facts pinned at 1.0 with zero evidence;
  expired windows contribute nothing; value-change invalidates old evidence; 1-hour dedup; confidence ∈
  [0,1]). Formalize these as the Memory eval suite, run in CI on every change to the evidence machinery —
  this is behavioral testing in the CheckList sense (invariance of the posterior under irrelevant
  perturbation; directional expectations under new evidence) (CheckList [paper]
  https://arxiv.org/abs/2005.04118).
- **Calibrate the likelihood ratios as an eval, not just a constant:** HELM measures calibration as a
  first-class metric (HELM [paper] https://arxiv.org/abs/2211.09110). A natural eval: collect
  ground-truth-verifiable facts, and check whether posterior confidence ≈ empirical truth rate at each
  confidence level; LR(sensor)=10, LR(user_confirm)=20, LR(llm)=3 are the calibration points (evidence-system.md)
  and should be validated empirically, not assumed.
- **Retrieval eval for `get_relevant`:** use RAGAS-style reference-free metrics (context relevance +
  faithfulness of answers to retrieved facts) plus a small labeled relevance set (RAGAS [paper]
  https://arxiv.org/abs/2309.15217; LangSmith [docs] https://docs.smith.langchain.com/evaluation for dataset
  hygiene). The fact store's layering (hot/warm/cold, recency+access weighting in ARCHITECTURE.md) suggests
  invariance tests: retrieval order should be stable under irrelevant rephrasings and should change
  predictably under fact-validity changes.
- **Guard the `llm` evidence source:** LLM extraction feeds facts with LR=3 (evidence-system.md). The
  self-preference and contamination literature is directly relevant: if the same provider extracts and later
  judges, biases compound (Panickssery et al. [paper] https://arxiv.org/abs/2404.13076; G-Eval [paper]
  https://arxiv.org/abs/2303.16634). Sample audits of LLM-extracted facts against `user_confirm`/`sensor`
  ground truth give the LLM source's real precision — the input data for LR calibration.

### 8.3 Learning module — patterns and preferences

- Learning's outputs are behavioral (`pattern.detected`, `preference.learned` in ARCHITECTURE.md) — test
  them with **directional-expectation and invariance tests** (CheckList DIR/INV: perturb input streams,
  assert the pattern signal moves the right way or stays stable) (CheckList [paper]
  https://arxiv.org/abs/2005.04118).
- **Balanced problem sets** apply directly: include both "pattern should fire" and "pattern must NOT fire"
  cases; one-sided suites produce one-sided systems (Anthropic [blog]
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

### 8.4 LLM-as-judge safeguards that matter here

Cortex will use LLM judges for open-ended outputs (responses, summaries, extraction quality). Mandatory
safeguards, each with a primary source:

1. **Position bias** — judge both orderings, keep only consistent verdicts (Wang et al. [paper]
   https://arxiv.org/abs/2305.17926; Zheng et al. [paper] https://arxiv.org/abs/2306.05685).
2. **Verbosity/length bias** — length-controlled or length-regressed judging (Dubois et al. [paper]
   https://arxiv.org/abs/2404.04475); G-Eval's own verbosity caution (G-Eval [paper]
   https://arxiv.org/abs/2303.16634).
3. **Self-enhancement** — judge model ≠ generator model, since self-recognition causally drives
   self-preference (Panickssery et al. [paper] https://arxiv.org/abs/2404.13076). With Cortex's factory
   abstraction (OpenAI/Anthropic clients), cross-provider judging is cheap: e.g., generate with provider A,
   judge with provider B.
4. **Calibration** — maintain a small human-labeled audit set; measure judge-vs-human agreement and
   re-calibrate rubrics (AutoCalibrate loop) (Liu et al. [paper] https://arxiv.org/abs/2309.13308; Anthropic
   [blog] https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
5. **Rubrics** — G-Eval structure (criteria → CoT → form-fill score, averaged over samples) (G-Eval [paper]
   https://arxiv.org/abs/2303.16634); pairwise for ranking, pointwise rubric for gates (LangSmith [docs]
   https://docs.smith.langchain.com/evaluation).

### 8.5 Statistically sound eval sizes for Cortex

- Early development: **20–50 tasks** per suite from real failures (Anthropic [blog]
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).
- For CI gates: compute the binomial CI half-width for the suite size and set the gate threshold
  outside noise (see §4.2 table). Concretely: a 100-task suite cannot support a <10pp regression claim; a
  500-task suite supports ~4–5pp claims at 95%.
- Run each trial **more than once** for agents (pass^k); agent evals are non-deterministic by nature
  (τ-bench [paper] https://arxiv.org/abs/2406.12045; Anthropic's trial/outcome vocabulary [blog]
  https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents).

### 8.6 Pipeline and infrastructure for Cortex

- **Layered suites:** (1) unit/invariant tests for evidence machinery (§8.2) — deterministic, always green
  except on real bugs; (2) domain task suite for the loop (§8.1) — CI-gated with pass^k + cost/latency;
  (3) capability/quality suite that "graduates" into regression; (4) safety/refusal checks (HELM [paper]
  https://arxiv.org/abs/2211.09110; DeepEval safety metrics [docs]
  https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd).
- **CI integration:** the three open frameworks all support it — promptfoo (declarative + thresholds,
  JUnit) (promptfoo docs [docs] https://www.promptfoo.dev/docs/integrations/ci-cd/), DeepEval (pytest)
  (DeepEval docs [docs] https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd), and LangSmith
  (experiments + annotation queues) (LangSmith docs [docs] https://docs.smith.langchain.com/evaluation).
  Pick one for the gate; keep transcripts for all.
- **Pin and version everything:** dataset versions, prompt versions, judge model, provider model, grading
  code (τ³ [repo] https://github.com/sierra-research/tau2-bench; OSWorld [repo]
  https://github.com/xlang-ai/OSWorld; LangSmith [docs] https://docs.smith.langchain.com/evaluation).
- **Contamination control:** keep the private golden data out of any prompt history and out of
  fine-tuning/learning loops; refresh periodically; measure overlap if public data is ever used (GSM1k
  methodology [paper] https://arxiv.org/abs/2405.00332; Sainz et al. [paper] https://arxiv.org/abs/2310.18018).
- **Human audit loop:** periodic expert review of judge decisions and of LLM-extracted facts, feeding both
  the calibration set and the dataset (OpenAI's SWE-bench Verified annotation process is the template for
  rigor: multiple annotators, conservative ensembling) (OpenAI [blog]
  https://openai.com/index/introducing-swe-bench-verified/).

---

## 9. Primary source list

**Benchmarks & task suites**

| Source | Kind | URL |
|---|---|---|
| GAIA: a benchmark for General AI Assistants (Mialon et al.) | paper | https://arxiv.org/abs/2311.12983 |
| GAIA dataset (gated) | repo | https://huggingface.co/datasets/gaia-benchmark/GAIA |
| GAIA leaderboard | leaderboard | https://huggingface.co/spaces/gaia-benchmark/leaderboard |
| SWE-bench: Can Language Models Resolve Real-World GitHub Issues? (Jimenez et al., ICLR 2024) | paper | https://arxiv.org/abs/2310.06770 |
| SWE-bench repo | repo | https://github.com/SWE-bench/SWE-bench |
| Introducing SWE-bench Verified (OpenAI, 2024) | blog | https://openai.com/index/introducing-swe-bench-verified/ |
| AgentBench: Evaluating LLMs as Agents (Liu et al., ICLR 2024) | paper | https://arxiv.org/abs/2308.03688 |
| AgentBench repo | repo | https://github.com/THUDM/AgentBench |
| WebArena: A Realistic Web Environment (Zhou et al.) | paper | https://arxiv.org/abs/2307.13854 |
| WebArena repo | repo | https://github.com/web-arena-x/webarena |
| VisualWebArena (Koh et al., ACL 2024) | paper | https://arxiv.org/abs/2401.13649 |
| VisualWebArena repo | repo | https://github.com/web-arena-x/visualwebarena |
| τ-bench: Tool-Agent-User Interaction (Yao et al.) | paper | https://arxiv.org/abs/2406.12045 |
| τ-bench repo | repo | https://github.com/sierra-research/tau-bench |
| τ²-Bench: Dual-Control Environment (Barres et al.) | paper | https://arxiv.org/abs/2506.07982 |
| τ²/τ³-bench repo (current) | repo | https://github.com/sierra-research/tau2-bench |
| OSWorld (Xie et al., NeurIPS 2024) | paper | https://arxiv.org/abs/2404.07972 |
| OSWorld repo | repo | https://github.com/xlang-ai/OSWorld |
| Terminal-Bench (current, Harbor) | repo | https://github.com/harbor-framework/terminal-bench |
| Terminal-Bench 1.0 (archived original) | repo | https://github.com/harbor-framework/terminal-bench-1 |
| Terminal-Bench site/leaderboards | site | https://www.tbench.ai/ |
| Berkeley Function Calling Leaderboard launch post | blog | https://gorilla.cs.berkeley.edu/blogs/8_berkeley_function_calling_leaderboard.html |
| BFCL leaderboard | leaderboard | https://gorilla.cs.berkeley.edu/leaderboard.html |
| Gorilla / BFCL repo | repo | https://github.com/ShishirPatil/gorilla |
| Measuring AI Ability to Complete Long Software Tasks (METR, 2025) | blog | https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/ |
| HCAST: Human-Calibrated Autonomy Software Tasks | paper | https://arxiv.org/abs/2503.17354 |
| Recent Frontier Models Are Reward Hacking (METR, 2025) | blog | https://metr.org/blog/2025-06-05-recent-reward-hacking/ |

**LLM-as-judge**

| Source | Kind | URL |
|---|---|---|
| Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena (Zheng et al.) | paper | https://arxiv.org/abs/2306.05685 |
| LLM-as-a-judge data (MT-bench, votes, conversations) | repo | https://github.com/lm-sys/FastChat/tree/main/fastchat/llm_judge |
| Large Language Models are not Fair Evaluators (Wang et al.) | paper | https://arxiv.org/abs/2305.17926 |
| G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment (Liu et al.) | paper | https://arxiv.org/abs/2303.16634 |
| G-Eval code | repo | https://github.com/nlpyang/geval |
| Can Large Language Models Be an Alternative to Human Evaluations? (Chiang & Lee, ACL 2023) | paper | https://arxiv.org/abs/2305.01937 |
| LLM Evaluators Recognize and Favor Their Own Generations (Panickssery et al.) | paper | https://arxiv.org/abs/2404.13076 |
| Calibrating LLM-Based Evaluator / AutoCalibrate (Liu et al.) | paper | https://arxiv.org/abs/2309.13308 |
| Length-Controlled AlpacaEval (Dubois et al., COLM 2024) | paper | https://arxiv.org/abs/2404.04475 |

**Statistical rigor & contamination**

| Source | Kind | URL |
|---|---|---|
| Holistic Evaluation of Language Models (Liang et al., TMLR 2023) | paper | https://arxiv.org/abs/2211.09110 |
| HELM repo | repo | https://github.com/stanford-crfm/helm |
| A Careful Examination of LLM Performance on Grade School Arithmetic / GSM1k (Zhang et al., NeurIPS 2024 D&B) | paper | https://arxiv.org/abs/2405.00332 |
| NLP Evaluation in trouble: On the Need to Measure LLM Data Contamination (Sainz et al., EMNLP 2024 Findings) | paper | https://arxiv.org/abs/2310.18018 |
| Data Contamination Report from the 2024 CONDA Shared Task (Sainz et al.) | paper | https://arxiv.org/abs/2407.21530 |

**Behavioral testing, process rewards, agent grading**

| Source | Kind | URL |
|---|---|---|
| Beyond Accuracy: Behavioral Testing of NLP Models with CheckList (Ribeiro et al., ACL 2020) | paper | https://arxiv.org/abs/2005.04118 |
| CheckList repo | repo | https://github.com/marcotcr/checklist |
| Let's Verify Step by Step (Lightman et al.) | paper | https://arxiv.org/abs/2305.20050 |
| Agent-as-a-Judge: Evaluate Agents with Agents (Zhuge et al.) | paper | https://arxiv.org/abs/2410.10934 |
| RAGAS: Automated Evaluation of Retrieval Augmented Generation (Es et al.) | paper | https://arxiv.org/abs/2309.15217 |
| RAGAS repo | repo | https://github.com/explodinggradients/ragas |

**Frameworks & first-party engineering guidance**

| Source | Kind | URL |
|---|---|---|
| OpenAI Evals framework & registry | repo | https://github.com/openai/evals |
| Anthropic: Demystifying evals for AI agents (2026) | blog | https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents |
| promptfoo (CLI/CI eval & red-teaming) | repo | https://github.com/promptfoo/promptfoo |
| promptfoo CI/CD integration docs | docs | https://www.promptfoo.dev/docs/integrations/ci-cd/ |
| DeepEval — the LLM evaluation framework | repo | https://github.com/confident-ai/deepeval |
| DeepEval: Unit Testing in CI/CD docs | docs | https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd |
| LangSmith evaluation concepts (datasets, experiments, judges) | docs | https://docs.smith.langchain.com/evaluation |

---

*Research compiled 2026-08-28. All URLs verified reachable at compile time except where noted (GAIA GitHub
repo removed; Anthropic's 2025 "Evaluating AI agents" post replaced by the 2026 "Demystifying evals for AI
agents" post). Repo-relative claims about Cortex (module names, event types, evidence machinery) reference
`CONTEXT.md`, `docs/ARCHITECTURE.md`, and `docs/evidence-system.md` as read on the same date.*
