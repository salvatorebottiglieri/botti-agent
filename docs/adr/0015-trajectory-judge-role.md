# Trajectory Judge is never the pass/fail oracle

For loop Eval Tasks, the annotated goal state (sandbox filesystem, database) is the only pass/fail
## Scoring rubric (G-Eval structure)

Partial credit is a pointwise absolute score on a single failed trajectory, so the position-bias
order-swap does not apply to it — the rubric is its primary validity control, and the human
calibration audit has nothing concrete to calibrate without it. The judge therefore follows the
G-Eval structure (Liu et al., arXiv:2303.16634): (1) evaluation criteria are written BEFORE judging,
one per dimension — tool selection correctness, argument correctness, iteration efficiency, policy
compliance — with positive and negative indicators each; (2) the judge reasons in chain-of-thought
over the trajectory against the criteria; (3) it fills a form-fill Likert score per dimension; (4)
multiple sampling rounds are averaged. The rubric text is versioned and pinned in the eval manifest
alongside prompt/model/grading versions, so score drift is attributable.

## Length-control waiver

The trajectory judge grades a single trajectory (no candidate pair), so pairwise length-control
(Dubois et al., arXiv:2404.04475) does not apply directly. Accepted trade-off: the rubric's
efficiency dimension bounds verbosity inflation, and the human audit set (10-15 trajectories)
measures judge-vs-human agreement per dimension before trust. Revisit if verbosity drift appears in
audit.
