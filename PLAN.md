# review-loop: Codex reviews, Claude assesses and fixes, a coordinator drives

Date: 2026-09-26. Status: implemented through milestone 5 on 2026-09-27 (187 offline tests green; the
fixtures under `fixtures/` are synthetic, shaped after real Codex and Claude outputs for PR #984); milestone 6, daily use,
is in progress. Section 30 records what the spike and the first runs changed. Merges two drafts of the same
day: the coordinator-first draft written in the Codex worktree (`2026-09-26-review-loop-development-tooling-plan.md`)
and the draft grounded in the pilot repository. Section 21 records which decisions came from which draft and
which ones changed. Part II (sections 24 to 29) is the engineering plan: module boundaries, error
policy, test strategy and the test-first order of work.

This file lives with the tool in its own repository. Nothing of the tool, its plan, its state or
its fixtures lives inside a registered repository.

## 1. Purpose

Today every PR goes through a hand-driven cycle: paste the review prompt into the Codex desktop app,
wait, paste the assess-and-resolve prompt into the Claude desktop app, wait, paste the next-pass
prompt, and watch for ping-pong. PR #984 took 16 review events, 18 inline comments and 6 author
summaries in one day, all typed by hand. `review-loop` performs those handoffs, keeps the reasoning
behind every disposition, and leaves anything unresolved in a final report the developer reads
at merge time.

It is personal developer tooling in its own repository, usable on any registered repository. The
first version runs on the Mac; the same coordinator later runs on a Linux VPS. The pilot
repository, a PHP and Node application, was registered first and supplies the worked examples below.

## 2. What stays fixed

- Codex reviews; Claude assesses critically and fixes only what it accepts. The current prompts are
  the seeds, extended with structured output and anti-oscillation rules.
- Both agents work on a complete checkout of the PR branch, never on a diff alone.
- The coordinator publishes to GitHub. Agents have no GitHub write access.
- Verification is run by the coordinator, from repository configuration. A model's claim that tests
  passed is evidence for the model's reasoning, never for the completion gate.
- Adjacent problems an agent notices go to a persistent inbox and never widen the PR.
- The loop never waits on the developer. Disagreements are aligned and, failing that, arbitrated
  between the agents; whatever survives is written into the final report for the developer to
  read when they come to mark the PR ready.
- The developer keeps merge, draft-to-ready, and hosted CI. The tool never runs `gh pr ready`,
  `gh workflow run`, a merge, or a force-push.
- No AI attribution in commits, review bodies, replies or comments where the repository forbids it
  (the pilot repository does).

## 3. Facts checked on 2026-09-26

| Item | State |
|---|---|
| `claude` CLI | 2.1.278 installed. `-p` with `--output-format json`, `--json-schema`, `--tools`, `--allowedTools`, `--disallowedTools`, `--permission-mode`, `--settings`, `--setting-sources`, `--strict-mcp-config`, `--mcp-config`, `--effort`, `--resume`, `--no-session-persistence`; `claude setup-token` for headless subscription auth. The support notice of 2026-06-15 says `claude -p` keeps drawing on the subscription. |
| `codex` CLI | Not installed on the Mac; npm has 0.157.1. The ChatGPT desktop app keeps its login at `~/.codex/auth.json` and its config at `~/.codex/config.toml` (`gpt-6-astra`, reasoning `ultra`, three MCP servers, one of which reads the main checkout's database). `codex exec` has `--output-schema`, `-o`, `--json`, `-C`, `-m`, `-c key=value`, `--sandbox read-only|workspace-write|danger-full-access`, `--ignore-user-config`, `--ephemeral`, `codex exec resume`; `codex login --device-auth` for headless boxes. |
| GitHub identity | Every review on the last 15 merged pilot PRs was posted by the developer's own account. GitHub refuses `APPROVE` and `REQUEST_CHANGES` on one's own PR, so verdicts post as `COMMENT` with the verdict in the body. |
| Thread surfaces | Reviews, inline review comments and issue comments are three REST endpoints; inline threads with replies and the resolved flag come from GraphQL `reviewThreads`. |
| Pilot worktrees | `.claude/worktree-setup.sh` provisions dependencies and built assets (`--js` adds a local npm build). `.claude/run-tests.sh changed` maps changed files to tests and exits 3 when nothing maps. Claude Code keys auto-memory to the main checkout for worktrees under it; an external worktree path is checked in Milestone 0. |
| Pilot CI | Hosted CI runs when a PR opens, reopens or turns ready, and in the merge queue, not on every push. Actions minutes are the merge-gate budget. |
| Usage | Both subscriptions have rolling usage windows. An unattended loop must pause on a limit. |
| Measured rounds | Last 20 merged pilot PRs (appendix A): median 3 review passes, mean 3.4, max 7; four PRs needed 5 or more; no alignment note was posted in any of them. |

## 4. Defaults

| Choice | Default |
|---|---|
| Runtime | Mac first; VPS later; one authoritative host per PR run |
| Implementation | Python 3.12 coordinator, SQLite state, CLI subprocess adapters, argument-array commands without a shell |
| Invocation | `review-loop start <PR URL>`; label-driven enrollment later |
| Workspace | one dedicated worktree per enrolled PR, on a tool-owned local branch |
| Agent execution | sequential within a PR; one active PR run at a time |
| Reviewer | Codex, `gpt-5.6-sol`, reasoning `xhigh`, sandbox `read-only`; any registered agent per repository (section 32) |
| Assessor and fixer | Claude, `claude-opus-5-5`, effort `xhigh`; assessment read-only, fix phase write; any registered agent per repository |
| Alignment | automatic: Codex note, Claude assessment, then blind arbitration by both models with labels swapped |
| Review budget | 7 review passes (the observed maximum; median is 3), configurable per repository and per run |
| Fix budget | 2 editing attempts per assessment: initial fix plus one repair after failed checks |
| Alignment budget | 1 exchange plus 1 arbitration per dispute; a dispute never re-opens without new evidence |
| Phase time limits | review 40 min, assess 20 min, fix 60 min, alignment 15 min, arbitration 15 min |
| Verification | repository-configured targeted checks, run by the coordinator |
| Completion | `complete`, `complete_with_exceptions`, or `blocked`, always with a final report; developer controls merge and CI |
| Notifications | completion, failure, blocked outcome |
| Code location | the tool's own repository, never inside a registered repository |
| Runtime home | `~/.review-loop/`: `config.toml`, `state.db`, `runs/<run-id>/`, `worktrees/`, `logs/`, `prompts/`, `schemas/` |

Starting a run is the explicit opt-in to review, assess, fix, verify, commit, push and comment on
that PR. The command states that scope during onboarding.

## 5. Developer interface

```text
review-loop repo add <local repository path>
review-loop doctor                       # CLI versions, auth routes, sandbox behaviour, config validity
review-loop start <PR URL> [--detach] [--no-preflight] [--author-session <claude session id>]
review-loop drive <run-id>               # the loop in this process; what --detach starts in its own session
review-loop wait <run-id>                # follow a run another process drives, to its stop
review-loop status [run-id]
review-loop show <run-id>                # findings, dispositions, verification, publication receipts
review-loop pause|resume [--detach]|stop <run-id>
review-loop align <run-id> --file <decision.md>   # optional override; never required
review-loop inbox list|show|dismiss|schedule|resolve
```

`pause` keeps the run resumable. `stop` cancels further work and keeps changes, logs and records.
Neither resets the branch or deletes the worktree, and neither writes anything but the run's control
columns, so a coordinator's progress persisted after the command read the run is never rewound. A
finished run (complete, failed, cancelled) is never paused or stopped again; both commands report the
state they found instead, so a stopped run can never come back into the active set.

`--detach` hands the run to `drive` in its own session, with its output in `runs/<run-id>/coordinator.log`,
so the run outlives the shell that started it; `wait` follows it and ends as a foreground drive would.
The PR lock doubles as the liveness signal: a run in a working state whose lock nobody holds is shown
as `(no coordinator)` by `status` and `show`, `wait` reports it, and `resume` continues it from that
phase. A crash inside the coordinator pauses the run as `coordinator_failed` with the traceback kept
for `show`. Before the first phase and on every resume, each agent answers one structured probe with
the model and effort a phase would use; a CLI that cannot serve its model pauses the run as
`agent_unavailable` before anything is read or posted, and `doctor` runs the same probes.

Later: `review-loop watch` enrolls PRs that carry a `review-loop` label from a trusted author, and
trusted logins can post `/review-loop pause|resume|stop|note <text>` on the PR.

A completion message:

```text
Review complete for acme/webapp PR #1004 at 3c286c29 after 4 passes.
4 findings verified, 1 rejection accepted by the reviewer, 1 arbitrated exception (see report).
Required checks passed: .claude/run-tests.sh changed (42 tests).
2 adjacent problems saved to your inbox.
The PR stays a draft; mark it ready to run hosted CI.
```

## 6. Components

- **Coordinator**: phase transitions, budgets, locks, context packets, subprocesses, verification,
  publication. Explicit rules decide what happens next; models supply judgments and proposed changes.
- **Agent adapters**: one for Codex, one for Claude. Each turns a phase request into CLI arguments
  and normalises events, results, session ids and failures, so a CLI change stays in one file.
- **Git and GitHub adapter**: PR metadata and discussion, worktrees, commits, guarded pushes,
  reviews and replies, reconciliation of uncertain operations.
- **Verification runner**: runs configured checks, records commands, exit status, logs and the exact
  tree checked.
- **State and artifacts**: SQLite for runs, findings, decisions, inbox, outbox; files for context
  packets, prompts, structured outputs, sanitised event logs, diffs and verification reports.
- **Inbox and notifications**: adjacent problems across repositories; quiet notifications.

## 7. Repository onboarding

Registration supplies what the generic coordinator cannot know. Trusted configuration lives in the
tool's own config directory, never on the reviewed branch: a PR must not be able to change the
coordinator's permissions, limits or publication policy by editing a file the tool trusts.

The pilot registration as `repo add` writes it, with placeholder names:

```toml
[repositories.webapp]
remote = "https://github.com/acme/webapp.git"
local_path = "/home/dev/webapp"
worktree_root = "/home/dev/.review-loop/worktrees"
instruction_files = ["CLAUDE.md", "PROJECT.md"]
allowed_pr_authors = ["alice"]
trusted_logins = ["alice"]

[repositories.webapp.workspace]
prepare = [["bash", ".claude/worktree-setup.sh"]]
prepare_when_paths_match = { "^resources/(js|css)/" = [["bash", ".claude/worktree-setup.sh", "--js"]] }
sanitize_env = ["DB_*", "DB_URL", "CACHE_STORE", "SESSION_DRIVER", "QUEUE_CONNECTION", "BROADCAST_CONNECTION", "MAIL_MAILER"]

[repositories.webapp.verification]
required = [[".claude/run-tests.sh", "changed"]]
unavailable_exit_codes = [3]          # "nothing maps" is incomplete verification, never a pass
fallback = [[".claude/run-tests.sh", "full"]]   # runs when every required check passed or selected nothing; decides in their place
format = [["vendor/bin/pint", "--dirty", "--format", "agent"]]

[repositories.webapp.review]
max_review_passes = 3
max_fix_attempts = 2
max_alignment_exchanges = 1
reviewer_model = "gpt-5.6-sol"
reviewer_effort = "xhigh"
author_model = "claude-opus-5-5"
author_effort = "xhigh"

[repositories.webapp.publication]
post_reviews = true
post_author_responses = true
push_verified_fixes = true
mirror_inbox_in_pr_comment = true
forbid_commit_trailers = ["Co-Authored-By", "Generated with"]
```

Commands are argument arrays run without a shell. Placeholders are single arguments. Commands found
in PR comments are never executed. The tool never installs or changes a project's dependencies to
make a run proceed unless the registered prepare step does so.

## 8. Workspace

One worktree per enrolled PR at `<worktree_root>/<owner>-<repo>-<pr>/`, created from the local
checkout with `git worktree add` on a tool-owned local branch `review-loop/pr-<n>` that tracks the
PR head. The push target is explicit (the PR's head ref on `origin`), so the developer's own branch
checkouts are never touched and no branch is checked out twice.

The coordinator:

1. Resolves repository, base branch, base commit, merge base and head commit from GitHub metadata.
2. Takes an exclusive lock per repository and PR before touching the workspace.
3. Fetches `origin/<base>` and `origin/<head>` before every phase, so a push from a desktop session
   is picked up and both agents can diff against the base offline.
4. Requires a clean tree at phase start. A dirty tree pauses the run with `workspace_dirty` and the
   changes preserved; it is never reset automatically.
5. Runs the registered prepare step; provisioning is idempotent.
6. Checks branch and tree state between phases.

Mechanical guards, because a worktree is a workspace and a prompt is a wish:

- The worktree's `origin` push URL is `DISABLED`; only the coordinator pushes, with an explicit URL
  and a guarded ref. The value lives in the worktree's own `config.worktree`: git keeps remote
  settings in the shared `.git/config`, which every checkout reads, so `git remote set-url --push`
  would lock them all. Push URLs add up across config files, so the tool first writes an empty
  `pushurl` (which drops the shared ones on git 2.46 and later), then reads the effective list back
  and refuses to continue unless `DISABLED` is all that remains.
- The trade-off: `git config --worktree` needs `extensions.worktreeConfig=true` on the repository.
  The pilot repository has it. Turning it on writes the shared config, so the tool leaves that to the developer:
  `doctor` fails the repository and `prepare` refuses it until
  `git -C <checkout> config extensions.worktreeConfig true` has been run once. A lock set through
  the agents' environment (`GIT_CONFIG_COUNT`) would need no setting, but a shell opened in the
  worktree could still push. On git before 2.46 (the bookworm image has 2.39), a repository whose
  shared config sets its own push URL is refused at `prepare`.
- The agents' environment has `gh` off the PATH, `GH_TOKEN` unset, and the registered variables
  unset, so the pilot's tests stay on sqlite in memory. The worktree gets no `.env`.
- MCP servers are off for both CLIs: Codex runs with `--ignore-user-config` plus explicit `-c`
  model settings; Claude runs with `--strict-mcp-config` and an empty `--mcp-config`. A desktop
  MCP server that reads the main checkout's database has no place in a review.
- Neither CLI runs the checkout's own config. `claude -p` skips the workspace trust prompt, so Claude
  runs with `--setting-sources ""` and loads no settings file. The user's own settings stay out too:
  their hooks run with the checkout as the project directory, and an `env` block would override the
  environment guards above. Codex's `--ignore-user-config` also drops the trusted-project list, so the worktree is
  untrusted and its `.codex/` config, hooks, rules and MCP servers never load.
- The reviewer's sandbox is `read-only`. The assessment phase runs Claude with a read-only tool
  list. Only the fix phase may edit, and it cannot push or post.

## 9. Context packet

Each phase receives:

- PR title, body, base and head commits, diff basis, and the original request or handoff material.
- The registered instruction files.
- The whole discussion, fetched with pagination: issue comments, review bodies, inline threads with
  replies and resolved state. Each entry is tagged `[reviewer]`, `[author]` or `[human]`. The
  coordinator recognises its own posts by an HTML-comment marker in each one
  (`<!-- review-loop role=reviewer run=… round=2 finding=R2-F1 -->`); an unmarked post is a human.
- The finding ledger: ids, lifecycle state, evidence, responses, fix commits, verification.
- Versioned alignment decisions.
- Verification results relevant to the current tree.
- The phase's permitted actions and its output schema.

Discussion is evidence. Control instructions and alignment decisions come only from `trusted_logins`
(through the CLI, or later through PR comments). The digest keeps links to the originals so an agent
can pull supporting material.

The context is refreshed before every consequential phase and before completion; a new human
decision can invalidate a pending assessment with no commit change.

**Author continuity.** A fresh `claude -p` process does not remember writing the code, and the prompt
must not claim it does. Two routes: `--author-session <id|auto>` resumes the desktop session that wrote
the PR. `auto` (the default) picks the newest Claude session under the repository's project
directories whose records carry a `gitBranch` equal to the PR head branch (session files record
`gitBranch` and `cwd`), and falls back to the handoff packet when none matches. Otherwise the packet carries the PR body, the linked plan file
under the repository's `artifacts/` folder if any, and the known design decisions. Missing intent that affects a material
decision is handled as a dispute: alignment, arbitration, then the final report.

## 10. The cycle

### A. Prepare

Snapshot the PR and discussion, lock and prepare the workspace, load policy, run `doctor` checks
without exposing credentials. Record head SHA, base SHA, diff basis, discussion digest hash, policy,
prompt and schema versions, and the alignment version. A head SHA alone does not identify a run's
inputs.

### B. Codex review (read-only)

```bash
codex exec -C "$WT" --sandbox read-only --ignore-user-config \
  -c model=gpt-6-astra -c model_reasoning_effort=ultra \
  --output-schema schemas/review.json -o "$RUN/review.json" --json \
  - < "$RUN/review-prompt.md" > "$RUN/review-events.jsonl"
```

Pillars: correctness and regressions, architecture and existing contracts, performance on real
paths, resilience, security. Each finding carries a stable id (`R<pass>-F<n>`), severity (`BLOCKER`,
`ISSUE`, `CHORE`, `QUESTION`), file, line, symbol, the protected behaviour or contract, evidence or
a failure scenario, a recommendation, an illustrative refactor, `supersedes` when it re-raises a
prior finding, `new_evidence` when it does so against a rejection, and `resolved_prior` for prior
findings it now considers closed. The first review also states the contract it judges against: what
the PR owns and what it does not.

Rules: a significant finding needs a failure scenario or a violated requirement; a preference alone
never forces a refactor; the proposed remedy is illustrative and an alternative that protects the
same behaviour is acceptable; a `QUESTION` blocks only when its answer decides an implementation or
verification choice; `CHORE` never enters the fix scope by itself. The review bar is reachability
before consequence, and project decisions in `PROJECT.md` are checked before a design is called wrong.

The coordinator validates the output, assigns or matches ids, and publishes (section 14).

### C. Claude assessment (read-only)

```bash
claude -p --output-format json --json-schema "$(cat schemas/assessment.json)" \
  --model claude-fable-5-1 --effort high \
  --tools "Read,Grep,Glob,Bash" --allowedTools "Bash(git diff *)" "Bash(git log *)" "Bash(git show *)" \
  --disallowedTools "Edit" "Write" "NotebookEdit" "Bash(git commit *)" "Bash(git push *)" "Bash(gh *)" \
  --strict-mcp-config --mcp-config "{}" --setting-sources "" --settings review-loop/claude-settings.json \
  < "$RUN/assess-prompt.md" > "$RUN/assessment.json"
```

One disposition per active finding:

| Disposition | Required content |
|---|---|
| accept | the defect and the minimal intended fix, in scope |
| reject | code or requirement evidence, and why the recommendation is unsuitable |
| needs_alignment | the missing decision and the viable alternatives |
| answer | for a question: the answer, with references |

Anything noticed outside the PR's purpose goes to `adjacent_findings`. The assessor may not close a
disputed reviewer finding on its own, and may not accept a finding outside the stated contract
without alignment. The assessor owns its half of any oscillation: over-claims ("bounded",
"self-healing", "cannot happen") are retracted in the same words, and residuals are stated precisely.

### D. Claude fix (write, accepted findings only)

```bash
claude -p --output-format json --json-schema "$(cat schemas/fix.json)" \
  --model claude-fable-5-1 --effort high --permission-mode bypassPermissions \
  --disallowedTools "Bash(gh *)" "Bash(git push *)" \
  --strict-mcp-config --mcp-config "{}" --setting-sources "" --settings review-loop/claude-settings.json \
  < "$RUN/fix-prompt.md" > "$RUN/fix.json"
```

The request lists the accepted ids, their protected behaviours, and the scope boundary. Claude edits
code and tests and reports each change with the finding it addresses. No settings file, hook, skill,
CLAUDE.md or rule loads, the user's included (`--setting-sources ""`); the packet names the
instruction files to follow. `claude-settings.json` turns attribution off. Claude
may run tests for its own confidence; the coordinator's run in E is the one that counts.

The coordinator inspects the diff and the changed-file inventory (including new and deleted files).
Files no accepted finding names are listed in the round summary as scope drift; the rereview judges
them. Two attempts per assessment: the fix, and one repair after failed checks. An interrupted fix
leaves the partial workspace in place and pauses; it is never replayed blindly.

### E. Verify, commit, publish

The coordinator runs the registered format step, then the required checks, against the final tree,
and records arguments, exit status, logs and the tree hash. Checks that did not run, crashed, or
selected nothing (exit 3 from the pilot's test runner) are incomplete verification, never a pass. On failure, one repair
attempt (D), then pause with the failures and the changed-workspace evidence.

On success it commits the verified changes (refusing any commit whose message carries a forbidden
trailer), confirms the remote head still equals the expected parent, and pushes with
`--force-with-lease=<head>:<expected>` plus a fast-forward ancestry check. A remote head that moved
pauses the run with `head_changed`; nothing is overwritten and nothing is rebased automatically.

After the push it replies in every finding thread with the disposition, the fixing commits and the
verification evidence. Rejections get their reasoned reply with no code change. One round summary
comment lists verdict, counts by severity, dispositions, checks, scope drift, and new inbox items.

### F. Codex rereview

Same invocation as B with the rereview prompt. Codex examines every open finding and its response,
the changes since its last review, the whole PR against unchanged code, rejection arguments,
alignment decisions, and the verification report. For each prior finding it verifies the fix,
withdraws a mistaken finding, accepts a justified rejection, or keeps it disputed with new evidence.
It may add new in-scope findings with evidence. A rereview can happen on the same commit when the
discussion alone changed a disposition; publication deduplication keys on run, round and discussion
hash, never on the head SHA alone.

### G. Complete, continue, or pause

Complete when the inspected head and discussion are still current, every `BLOCKER` and `ISSUE` is
`verified`, `rejection_accepted` or `withdrawn`, every blocking question is answered, and required
checks passed on that tree. `CHORE` and non-blocking `QUESTION` items stay recorded. A developer's
decision to defer a required finding appears as an accepted exception in the report, never as a
verified fix.

Within budget, continue with C. When the last allowed pass leaves work open, stop and write the
final report: open `ISSUE` and lower items become accepted exceptions with both positions
recorded; an open `BLOCKER` makes the outcome `blocked`. No further fix is attempted. Before completion, recheck GitHub and the workspace; a
clean review is scoped to a commit and a discussion state.

The final report is built from the run's records, never written by a model, so every id, commit and
count in it is one the tool recorded. It opens with an overview (passes, elapsed time, how the findings
ended, commits made, the last checks), then gives each finding's outcome, every exception with both
positions, the findings closed without a verified fix with the author's and reviewer's own words in quotes, the
run pass by pass, and each fix commit with its title and files read back from git. It is saved as
`report.md` and posted once as a PR comment. GitHub caps a comment at 65,536 characters, so a longer
report is posted cut at a line with the path of the full file.

## 11. Finding lifecycle and identity

Coordinator-assigned stable ids; severity separate from state:

```text
open
  -> accepted -> fixed_pending_verification -> verified
  -> rejected_pending_review -> rejection_accepted
  -> disputed -> needs_alignment -> accepted | rejection_accepted | deferred_by_decision
  -> withdrawn_by_reviewer
```

A finding record holds: id, repository and PR, severity and state, concern and protected behaviour,
file, symbol and location history, evidence and failure scenario, original and latest reviewed
commits, author and reviewer responses, fix commits and verification, GitHub thread and comment ids,
alignment references.

Matching a re-raised finding to a prior one uses the reviewer's `supersedes` first, then a fingerprint
(file, symbol, normalised concern). An uncertain match stays a separate record flagged
`possible_duplicate_of`; distinct problems are never merged for sharing a file or wording.

## 12. Oscillation and alignment, without the developer

Prevention, in the prompts:

- A rejected finding may be re-raised only with `new_evidence`; without it the reviewer marks it
  `disputed`, and a dispute goes to alignment, never straight into another fix round.
- The reviewer's first review states the contract: what the PR owns and what it does not, drawn
  from the PR body, the linked plan and `PROJECT.md`. A finding outside it is an inbox candidate or
  a `needs_alignment` item, never a blocker by default.
- The fix phase cannot widen the diff beyond accepted ids.
- The assessor retracts over-claims and states residuals precisely.

Detection, mechanical, from the ledger and git, with no model call:

| Signal | Rule |
|---|---|
| re-raise | `supersedes` or fingerprint points at a `rejected_*` finding with no `new_evidence` |
| reversal | the reviewer contradicts a recommendation it previously accepted or verified, with no `new_evidence` |
| flip-flop | a file's blob at the round n+1 head equals its blob at the round n-1 head while round n changed it |
| no convergence | open `BLOCKER` plus `ISSUE` count did not fall over two consecutive passes, or one fingerprint appears in three passes |
| drift | the fix diff keeps touching files no accepted finding names, two rounds running |

An approve followed by a request-changes on new commits is a new finding, never a reversal.
Signals prompt the exchange below; a real counterexample can justify reopening a conclusion.

The exchange, run by the coordinator with no developer in the loop:

1. Editing pauses for that PR.
2. Codex writes an alignment note at reasoning `high`: the contract, the invariant at stake, the
   conflicting recommendations, the evidence, the alternatives, its preferred resolution, and
   whether the repetition points at a design gap rather than a missed detail.
3. Claude assesses the note read-only against the contract, the requirements and the code: it
   agrees (accept and fix, or withdraw its rejection) or disagrees with evidence tied to the
   contract.
4. Agreement becomes a versioned alignment decision. The note and the decision are posted on the
   PR and included in every later phase, exactly as the manual alignment note is today. The run
   resumes within scope.
5. Disagreement goes to **arbitration**. A fresh invocation of each model, with no role history,
   receives the finding, the contract, the evidence excerpts, the relevant code, and the two
   positions labelled A and B with no mention of who held them. Claude sees one labelling, Codex
   the other, so a position bias in either model cancels rather than compounds. Each returns
   `{decision: A|B|neither, rationale, residual}`.
6. Two matching decisions become the versioned decision, posted on the PR with the rationale, and
   the run resumes: a decision for the fix side re-enters phase D; a decision for the rejection
   side closes the finding as `rejection_accepted`.
7. A split, or two `neither`, falls to the severity rule: an `ISSUE`, `CHORE` or `QUESTION` becomes
   an accepted exception, recorded with both positions and the arbiters' rationales, and the run
   continues; a `BLOCKER` stays open, the run stops with outcome `blocked`, and the final report
   carries the packet below. No further fix is attempted either way.

A decided or excepted dispute cannot re-open on the same fingerprint without `new_evidence`; a second
signal on it goes straight into the final report as a residual, with no further model calls.

What the developer reads in the final report, for each arbitrated or excepted finding:

```text
R3-F2 (BLOCKER, open): Should a notification failure prevent order creation?
Position A (fix): roll back, to keep the stated all-or-nothing contract.
Position B (keep): complete the order and retry notification separately.
Arbitration: Claude chose A; Codex chose B. Rationales attached.
Contract as stated in pass 1: the PR owns order creation; notification delivery is out of scope.
Residual if merged as is: an order can exist with no notification attempt recorded.
```

`review-loop align <run> --file` remains as an override for the rare case the developer wants to
settle a dispute themselves; nothing in the loop waits for it.

## 13. Adjacent findings inbox

An adjacent finding is a problem noticed while investigating that lies outside the PR's purpose,
including Claude's own "we should also fix" proposals. It goes to the tool's inbox: repository,
stable id, title, description, file and symbol, evidence, an impact estimate labelled as such, a
suggested next step, source PR, commit, agent and run, related entries, and a status (`new`,
`dismissed`, `scheduled`, `resolved`). A clear match across PRs adds evidence to the existing item;
an ambiguous match is suggested, and both records stay.

Saving an item creates no issue, starts no task, changes no code, and blocks nothing. When
`mirror_inbox_in_pr_comment` is on, one pinned PR comment, edited in place, lists the items captured
during that PR, so they are visible where the developer already reads. `inbox schedule` links an
item to a task reference the developer creates.

## 14. Publication rules

- The coordinator publishes structured results as readable reviews and replies, keeping the
  executive summary, the risk profile, the severity vocabulary and one finding per comment.
- Each review is bound to the inspected commit. Inline locations are validated against that
  commit's diff; a finding without a valid anchor goes in the body with `file:line`.
- Prior findings get replies in their existing threads; a rereview does not re-post them.
- Every post carries a marker and its remote id is stored. After an uncertain timeout the
  coordinator looks for the intended post before sending again.
- While both roles post from the developer's account, reviews use the `COMMENT` event with the
  verdict in the body. A separate reviewer identity (machine user or GitHub App) is an optional
  later setup that enables real `REQUEST_CHANGES` and `APPROVE`.
- Human-authored threads are never resolved by the tool. Coordinator-owned threads are resolved
  after the agreed verification or disposition, per policy.
- No merges, no draft-to-ready changes, no CI dispatch.

## 15. State, outbox, restart

A transactional SQLite database plus run artifacts outside the project tree.

| Record | Contents |
|---|---|
| repositories | trusted configuration, local paths |
| runs | PR identity, state, budgets, owner host, workspace |
| phase_attempts | inputs, output paths, CLI and session versions, result |
| findings, finding_history | concern, transitions, evidence |
| alignment_decisions | versioned constraints and provenance |
| inbox_items | adjacent discoveries and follow-up state |
| verification_results | commands, tree hash, status, logs |
| publication_outbox | intended external operation, receipt, reconciliation state |

Run states: `preparing`, `reviewing`, `assessing`, `fixing`, `verifying`, `publishing`,
`rereviewing`, `aligning`, `paused`, `complete`, `failed`, `cancelled`. Pause reasons are stored
separately: `usage_limit`, `auth_required`, `head_changed`, `workspace_dirty`, `checks_failed`. Outcomes:
`complete`, `complete_with_exceptions`, `blocked`.

Before any external write, the intended operation is saved; after it, the receipt. On restart,
unfinished operations are reconciled against the remote before any retry. A run records tool
version, CLI versions, models and effort, prompt, schema and policy versions. Credentials stay in
the CLIs' own stores. The author CLI's login is claimed from the process environment before
anything else runs and travels with its adapter alone: no other subprocess of the coordinator, no
project command and no other agent receives it. The state directory is the confidentiality
boundary, created private (0700), checked by `doctor`, with backup archives written 0600. Captured
output is kept whole: a process that was given no secret cannot leak one, and redaction would
silently alter evidence.

## 16. Failure handling

| Situation | Behaviour |
|---|---|
| remote branch moves during a run | keep the old result; reconcile; review current inputs before acting; git, not the API, says whether it moved, a resume continues in place when it did not, and the run's own pushed candidate is recognised from git before anything counts as foreign |
| new human decision arrives | refresh context; invalidate affected pending assessments |
| unexpected local edits | pause, preserve |
| a stop or manual pause lands mid-phase | the phase in flight finishes its work, but no post and no push starts after the control landed: each is reserved in one statement that fails once the control is on the run, the checkpoint keeps the control columns, and a withheld alignment note is checkpointed for the resume |
| duplicate start for one PR | return the active run or refuse |
| invalid or missing agent output | no dependent write; bounded retry, then pause; partial output kept for diagnosis |
| required checks fail | preserve evidence; one repair attempt (counted in fix runs, so a verification that ran nothing costs none), then pause; the repair may touch code the PR already had |
| checks unavailable or nothing selected | report incomplete verification; never a pass; a registered fallback runs when every check passed or selected nothing, and its result stands in their place |
| an agent cannot serve its configured model | probe each agent before the first phase and on every resume; pause with `agent_unavailable` and the CLI's own error |
| coordinator crash | pause with `coordinator_failed`, traceback kept; resume retries the phase; a stop or manual pause that landed meanwhile stands, as after any coordinator pause |
| coordinator process lost (session closed, shell killed) | the free lock shows the run as `(no coordinator)`; resume continues from that phase |
| usage limit | pause with `usage_limit`; automatic resumption after the window is an opt-in policy |
| authentication expired | pause with `auth_required`; renew through the CLI's own flow |
| agent timeout or crash | stop the process group; reconcile the workspace and partial output |
| uncertain post or push | reconcile remote state before retry |
| review budget exhausted | stop; final report with exceptions or `blocked`; no developer prompt |
| PR closed or merged externally | stop writes; keep history |

Only registered repositories and PRs from `allowed_pr_authors` run. Forked or untrusted PRs need a
separate isolation design first.

## 17. Portable by construction: local now, cloud later

The coordinator is written from the first commit as a service that happens to run on a Mac. The
rules that make the move a copy rather than a port:

- **One process, one config file, one state directory.** Every path comes from `config.toml`;
  every secret comes from the environment or the CLIs' own stores; no macOS API is called
  anywhere in the coordinator.
- **A host profile picks two adapters and nothing else**: the notifier (macOS notification
  locally; a PR comment plus Telegram, WhatsApp or email on the VPS) and the supervisor
  (`launchd` plist locally; `systemd` unit on the VPS). Phase logic, budgets, prompts, schemas and
  publication are identical on both.
- **GitHub over HTTPS with a token**, never SSH: `gh auth token` locally, a fine-grained PAT or a
  GitHub App installation token on the VPS. Work is found by polling, so no inbound port or
  webhook is needed on either host.
- **Agent auth through the supported headless flows**: `claude setup-token` producing
  `CLAUDE_CODE_OAUTH_TOKEN`, and `codex login --device-auth`. `review-loop doctor` verifies the
  effective route on both hosts and refuses to fall back to separately billed API keys silently.
- **A container image** in the tool's repository holds the coordinator plus the toolchain the
  registered repositories need (PHP 8.4 and extensions, composer, Node 22, sqlite; Chrome as an
  optional layer). Running one PR through that image on the Mac is the cloud-readiness gate: the
  VPS then runs the same image with the same config and a restored state directory.
- **State is a directory.** `review-loop backup` stops the coordinator and archives
  `~/.review-loop` (config, database, run artifacts, prompts, schemas); `restore` unpacks it on the
  other host. Worktrees are disposable and recreated from the registered checkout.

| Concern | Mac (now) | Linux VPS (later) |
|---|---|---|
| availability | while awake and the coordinator runs | continuous |
| agent auth | the CLIs' existing logins | `setup-token`, `device-auth`, renewal on expiry |
| project toolchain | already installed | the container image |
| supervision | foreground first, then `launchd` | `systemd`, or `docker compose` |
| contention | shares CPU and the pilot's test lock with desktop sessions | isolated |
| identity | one account, `COMMENT` verdicts | GitHub App: real `REQUEST_CHANGES` and `APPROVE`; the marker scheme needs no change |
| host | the Mac | a separate small box, never the production VPS |

One authoritative host per run. Migration: stop the coordinator, `backup`, `restore` on the box,
bootstrap auth, `doctor`, reconcile GitHub state, resume paused runs. Ordinary phases never need
SSH; on this profile every SSH attempt needs explicit approval, and the tool never issues one.

Ruled out: GitHub Actions (minutes are the merge-gate budget, and subscription CLIs on hosted
runners are awkward) and the vendors' hosted reviewers (fixed prompts, no round loop, no ledger, no
thread-aware alignment).

## 18. Prompts

Five files in the tool's config directory, with `{{placeholders}}` for PR, shas, round, and the
paths of the context packet, diff, ledger and alignment decisions:

- `review-initial.md`: the current init prompt, plus the contract statement, stable ids, the output
  schema, "read the discussion before the code", the reachability bar, `PROJECT.md` before verdicts
  on design, the repository's design-surface checklist when it registers one, and no AI
  provenance in any text bound for GitHub.
- `review-again.md`: the current next-pass prompt, plus the ledger rules (`supersedes`,
  `new_evidence`, `resolved_prior`, verify, withdraw, accept rejection, dispute) and the delta.
- `assess.md`: the current assess-and-resolve prompt minus the editing half, plus dispositions with
  evidence, `adjacent_findings`, the contract, and the over-claim rule.
- `fix.md`: accepted ids only, protected behaviours, scope boundary, change-to-finding reporting, no
  attribution trailers.
- `align.md`: the current oscillation question, plus the note's required parts, the design-gap
  question, and the disputed list.
- `arbitrate.md`: the finding, the contract, the evidence, the code, positions A and B, and the
  required `{decision, rationale, residual}` output; the prompt never names which agent held which
  position.

## 19. Milestones

### Milestone 0, spike (half a day, posts nothing)

1. Install the Codex CLI; confirm `codex login status` uses the desktop app's login and that
   `--ignore-user-config` keeps auth.
2. Create an external worktree for merged PR #984 and confirm Claude Code loads the project memory
   there; if not, worktrees go under the main checkout.
3. Run one review and one assessment into files. Confirm the JSON shapes from `--output-schema` and
   `--json-schema`, that `read-only` and the tool list block writes, and the wall-clock per phase.
4. Render the context packet for PR #984 and read it: do the eight manual rounds read clearly?

### Milestone 1, skeleton and durable runs

Tooling repository; `repo add`, `start`, `status`, `show`, `pause`, `resume`, `stop`; validated
config, SQLite migrations, artifacts, exclusive locks; full discussion retrieval; workspace
preparation; fake agent and GitHub adapters. Acceptance: a prepared run stops and resumes without
losing inputs or touching an unrelated checkout.

### Milestone 2, read-only review and assessment

Codex and Claude adapters with schema validation; context packets and author handoff; finding
identity and history; dispositions; proof that review and assessment cannot mutate source; an
inspect-only mode that writes artifacts and publishes nothing. Acceptance: both agents inspect the
whole repository and preserve disagreements without editing.

### Milestone 3, fix, verify, publish, rereview

Fix requests and scope inspection; the verification runner; verified commits, guarded pushes,
reviews and replies; the outbox and recovery; rereview, same-commit rereview, honest completion.
Acceptance: one real draft PR on the pilot repository finishes the loop with verified fixes and a traceable thread,
with the developer reading only the summaries.

### Milestone 4, inbox and alignment

Inbox storage, duplicates, status commands, completion summaries; the mechanical signals, the
exchange and blind arbitration; versioned decisions and the final report; budgets and
failure-specific pauses.
Acceptance: adjacent findings never widen a PR; an A-to-B-to-A reversal ends in a versioned
decision or a recorded exception with no developer prompt.

### Milestone 5, cloud-readiness gate

The container image; one PR run through it on the Mac; `backup` and `restore` round-trip; `doctor`
under `CLAUDE_CODE_OAUTH_TOKEN` and device-auth logins. Acceptance: the same image, config and
restored state finish a PR on a throwaway Linux box.

### Milestone 6, daily use

A handful of PRs, then a second registered repository with a different verification setup; restart
recovery, usage-limit pauses, CLI capability differences; quiet notifications; docs for install,
registration, recovery and auth. Acceptance: normal PRs need no copying between apps, and every
final report carries enough context to decide at merge time.

Later: `watch` with label enrollment and trusted PR-comment commands; the VPS move itself;
several concurrent runs; a small status UI; issue creation from selected inbox items; a separate
reviewer identity; configurable roles.

## 20. Verification of the tool itself

Develop against fake CLI processes and a fake GitHub adapter; the state-machine and recovery tests
are deterministic and consume no usage. Fixtures include a synthetic discussion shaped after PR #984 (all
three surfaces, replies, resolved state) and synthetic ledgers for each oscillation signal.

High-value tests:

1. A valid finding is accepted, fixed, checked, pushed, and verified by the rereview.
2. A rejected finding is accepted by the reviewer after explanation, with no new commit.
3. A rejected finding stays disputed and cannot produce a false completion.
4. An adjacent discovery lands in the inbox and causes no edit and no extra pass.
5. A second PR rediscovering the same problem keeps both source references.
6. An A-to-B-to-A reversal triggers alignment and keeps the eventual decision.
6a. Arbitration: matching decisions become a versioned decision; a split on an `ISSUE` becomes an
    exception and the run continues; a split on a `BLOCKER` ends the run `blocked`; the prompts
    sent to the two arbiters carry swapped labels and no role names.
6b. A decided dispute re-raised without new evidence produces no model call and one report line.
7. A branch update during review invalidates stale completion and blocks an unsafe push.
8. A new trusted alignment comment invalidates the pending assessment.
9. Two simultaneous starts cannot both own one workspace.
10. A crash after a post but before the receipt is reconciled without a duplicate.
11. A timed-out push is reconciled from remote state before retry.
12. Usage exhaustion or a crash mid-fix preserves partial work and pauses.
13. Missing, skipped, failed or stale checks cannot satisfy completion; a runner's "nothing selected" exit code counts as unavailable.
14. Malformed structured output triggers no edit and no publication.
15. Review and assessment phases cannot modify source through tools, MCP or inherited hooks.
16. Unexpected edits, an externally closed PR, and cancellation stop writes without deleting work.
17. Configuration or instruction files on the PR branch grant no coordinator permission.
18. A detached run outlives the shell that started it; a run that lost its coordinator is visible as such and resumable in place.
19. No phase starts on an agent that cannot serve its configured model.
18. Resumed sessions receive the same phase restrictions.
19. Inline placement: a finding on a line outside the diff lands in the body with a reference.
20. A commit with a forbidden trailer is refused before push.

Then real CLIs against a disposable fixture repository, and real publication only against a chosen
test repository, over HTTPS.

## 21. Provenance and what changed in the merge

Adopted from the coordinator-first draft: the generic tool with repository registration and trusted
config outside the branch; assessment and fix as separate phases with different capabilities; the
coordinator-run verification runner and the "not run is not a pass" rule; separate review, fix and
alignment budgets; the finding lifecycle and record; the publication outbox with reconciliation;
the guarded push that pauses on `head_changed`; preserving unexpected edits; the honest author
handoff instead of a claimed memory; the inbox with statuses and cross-PR matching; the decision
packet; the failure table; SQLite; Python; milestones with acceptance criteria and the test list.

Adopted from the pilot-grounded draft: the checked facts (installed versions, flags, the desktop
auth files, the self-review restriction, the CI trigger set); the concrete CLI invocations and
model pins; the mechanical guards (push URL, PATH and token removal, environment sanitiser, MCP off,
trailer refusal); the same-account marker attribution and human precedence in the discussion; the
prompt-level oscillation rules and the computable signals; the pilot registration example
(`worktree-setup.sh`, `run-tests.sh changed`, exit 3, `--js`, pint, no attribution); the PR-comment
mirror of the inbox; the PR #984 fixture; the spike.

Changed against both: the reviewer sandbox is `read-only` (the coordinator verifies, so the reviewer
need not run tests); the earlier auto-rebase on a moved remote is gone; label enrollment and PR
comment commands move behind the CLI start command; the `flip-flop` and `drift` signals join the
oscillation table; `--author-session` is new.

## 22. Decisions taken (2026-09-27)

1. Reviewer identity: one account, `COMMENT` verdicts. A GitHub App comes with the move to a VPS.
2. `--author-session`: yes, default `auto` (section 9).
3. Budgets and time limits: the section 4 values are defaults, configurable per repository in
   `config.toml` and per run (`--max-passes`, `--max-fix-attempts`, `--max-alignment`, `--phase-timeout`).
4. The tool lives in its own repository, with runtime state at `~/.review-loop/`;
   neither inside a registered repository, so the tool never changes with the branch it reviews and
   the desktop apps' worktree folders stay theirs.
5. The inbox (section 13) is the parking lot for adjacent findings: things an agent notices that lie
   outside the PR, including Claude's own "we should also" proposals. Mirrored as one pinned PR
   comment on the pilot's PRs.
6. (2026-09-27, second set) Alignment runs without the developer: exchange, then blind arbitration,
   then the severity rule; the residue goes in the final report (section 12).
7. Review budget 7 passes by default, from the measured maximum (appendix A); per repository and
   per run overrides.
8. Portable by construction (section 17): container image, state directory, host profile with two
   adapters, HTTPS tokens, polling; the cloud-readiness gate is Milestone 5.

## 23. Sources

- Codex non-interactive mode and CLI reference: learn.chatgpt.com/docs/non-interactive-mode,
  learn.chatgpt.com/docs/developer-commands?surface=cli (checked 2026-09-26).
- Codex sandboxing: learn.chatgpt.com/docs/sandboxing.
- Claude Code headless and CLI reference: code.claude.com/docs/en/headless, code.claude.com/docs/en/cli-reference;
  installed 2.1.278 `--help` output.
- Claude subscription notice: support.claude.com/en/articles/15036540 (2026-06-15 update).
- GitHub reviews API and the self-approval restriction: docs.github.com/en/rest/pulls/reviews,
  docs.github.com/en/pull-requests/how-tos/review-pull-requests/approving-a-pull-request-with-required-reviews.

## Appendix A. Measured review rounds, last 20 merged PRs (2026-09-27)

A pass is a review with a body; RC, AP and CO are the verdicts in the bodies. The raw
threads are private and stay out of this repository.

| PR | passes | verdicts | inline | summaries | hours |
|---|---|---|---|---|---|
| 1005 | 2 | RC AP | 2 | 1 | 18.8 |
| 1004 | 2 | RC AP | 3 | 0 | 0.2 |
| 1003 | 3 | RC RC AP | 10 | 3 | 0.5 |
| 1002 | 1 | CO | 0 | 0 | |
| 1001 | 4 | AP AP RC AP | 2 | 1 | 1.0 |
| 999 | 5 | RC RC RC AP AP | 8 | 4 | 28.9 |
| 997 | 3 | RC RC AP | 6 | 3 | 34.6 |
| 996 | 7 | RC RC RC RC RC RC AP | 16 | 7 | 30.6 |
| 995 | 3 | RC RC AP | 6 | 2 | 14.2 |
| 994 | 3 | RC RC AP | 8 | 3 | 11.5 |
| 993 | 6 | RC RC RC AP RC AP | 18 | 4 | 43.4 |
| 992 | 2 | RC AP | 2 | 1 | 9.5 |
| 991 | 2 | RC AP | 2 | 1 | 9.4 |
| 990 | 0 | | 0 | 0 | |
| 989 | 1 | CO | 0 | 1 | |
| 988 | 4 | RC CO CO AP | 10 | 3 | 0.8 |
| 987 | 4 | RC RC RC AP | 18 | 0 | 5.7 |
| 985 | 4 | RC RC RC RC | 11 | 4 | 2.4 |
| 984 | 7 | RC RC RC RC RC RC AP | 18 | 6 | 7.9 |
| 983 | 2 | RC AP | 2 | 1 | 1.1 |

Median 3 passes, mean 3.4, maximum 7; four PRs at 5 or more. The verdict sequences are almost all
monotone; the two approve-then-request-changes cases (1001, 993) follow new commits. No alignment
note or oscillation wording appears in any of the 20 threads.

---

# Part II. Engineering plan

Part I says what the tool does. Part II says how the code is shaped, how errors move, how the tool
is tested, and in what order the work happens. The order is test-first throughout: no production
code exists until a failing test asks for it, and every milestone below is a list of behaviours,
each entering the codebase through one red, green, refactor cycle.

## 24. Architecture and module boundaries

Python 3.12, standard library first. Two runtime facts drive the choice: `sqlite3`, `subprocess`,
`tomllib`, `json` and `argparse` are all in the standard library, and the tool must run unchanged
on the Mac and on a Linux box. Dependencies: `jsonschema` (agent output is validated against the
same schema files the CLIs are given) and `pytest` for development. Nothing else without a reason
written in this file.

```text
review_loop/
├── engine/            pure functions over small immutable types; no I/O, no clock, no subprocess
│   ├── discussion.py      role tags from markers, human precedence, digest hash, rendering
│   ├── findings.py        ids, fingerprints, matching (supersedes first), lifecycle transitions
│   ├── signals.py         re-raise, reversal, flip-flop, no convergence, drift
│   ├── placement.py       inline anchor validation against a unified diff; body fallback
│   ├── scope.py           changed-file inventory against accepted findings
│   ├── completion.py      complete, complete_with_exceptions, blocked; budget accounting
│   ├── arbitration.py     label swap, verdict combination, severity rule
│   └── trailers.py        forbidden commit trailer detection
├── services/          orchestration; imports engine, types, repositories, and adapter Protocols
│   ├── run_coordinator.py phase state machine, A to G, one function per phase
│   ├── workspace.py       worktree lifecycle and clean-tree checks through GitClient
│   ├── context_packet.py  assembles what a phase receives
│   ├── verification.py    runs configured checks through ProcessRunner; records evidence
│   ├── publication.py     outbox: record intent, perform, record receipt, reconcile
│   └── alignment.py       exchange and arbitration flow
├── adapters/          the only modules that touch the outside world
│   ├── github_gh.py       GitHubGateway over the gh CLI (REST and GraphQL, paginated)
│   ├── git_cli.py         GitClient over the git binary
│   ├── process.py         ProcessRunner: argv, cwd, env, timeout, process-group kill, captured output
│   ├── agents/codex.py    AgentAdapter for codex exec
│   ├── agents/claude.py   AgentAdapter for claude -p
│   ├── notify/macos.py    Notifier for the Mac
│   ├── notify/stdout.py   Notifier for tests and the VPS console
│   └── clock.py
├── repositories/      SQLite access, one module per table group, functions over a connection
│   ├── db.py              connection, migrations
│   ├── runs.py  findings.py  decisions.py  inbox.py  outbox.py  verification.py  phases.py
├── types/             dataclasses, enums, Result, error types, the Protocols
├── config/            TOML loading and validation, host profile, defaults
└── cli/               argparse commands; the composition root that wires adapters into services
prompts/   schemas/   fixtures/   tests/
```

**Import rule.** `engine` imports `types` only. `services` import `engine`, `types`, `repositories`
and the Protocols in `types`. `adapters` import `types`. `cli` imports everything and is the only
place a concrete adapter is constructed. Nothing imports `cli`. A test scans imports and fails on a
violation, so the rule holds without anyone remembering it.

**Protocols (dependency inversion).** Services receive these as constructor parameters; tests pass
fakes:

| Protocol | Surface |
|---|---|
| `AgentAdapter` | `run(PhaseRequest) -> Result[PhaseOutput, AgentFailure]` |
| `GitHubGateway` | `fetch_pull`, `fetch_discussion`, `post_review`, `reply`, `post_comment`, `edit_comment`, `resolve_thread`, `find_post_by_marker` |
| `GitClient` | `fetch`, `worktree_add`, `is_clean`, `reset_hard`, `diff`, `changed_files`, `blob_at`, `commit`, `push_guarded` |
| `ProcessRunner` | `run(argv, cwd, env, timeout, stdin) -> CompletedRun` |
| `Notifier` | `notify(Event)` |
| `Clock` | `now()` |

**Two-shape rule, applied.** `AgentAdapter` has two concrete shapes today (Codex, Claude);
`Notifier` has two (macOS now, stdout now, a PR comment or Telegram on the VPS); `GitHubGateway`
has one shape now (the `gh` CLI) and a named second (an App installation token over HTTPS), so the
Protocol is earned. The verification runner has one shape (configured commands) and no concrete
second, so it is a plain class with no Protocol; it gets one when a second shape exists. Repositories
are modules of functions over one SQLite connection, with no interface layer, because there is one
store.

**Phase functions.** Each phase in `run_coordinator.py` is a function from the run's current state
to a `Result[Transition]`. The coordinator loop applies transitions and persists them. A test can
drive one phase at a time with fakes and assert the transition, which keeps the state machine
testable without running a whole loop.

**Sizes and shapes.** Functions stay under about thirty lines; guard clauses replace nesting; names
say what a thing does and whether it writes. Every path, budget, model and command comes from
`config/`; nothing is hardcoded in a service.

## 25. Error policy

Expected failures are values. Every boundary returns `Result[T, E]` with a typed error, and the
caller decides the transition:

| Boundary | Error values |
|---|---|
| `AgentFailure` | `process_failed`, `timeout`, `malformed_output`, `schema_violation`, `usage_limit`, `auth_required` |
| `PublishFailure` | `head_changed`, `uncertain`, `rejected` |
| `VerificationOutcome` | `passed`, `failed`, `unavailable` |
| `WorkspaceProblem` | `dirty`, `locked`, `prepare_failed` |

Programmer errors and missing hard dependencies raise. `doctor` and `start` fail fast with the fix
in the message: `codex` absent on PATH ("install with `npm i -g @openai/codex`, then run
`codex login status`"), `gh` unauthenticated ("run `gh auth login`"), a registered prepare command
missing, a schema file missing, a Python below 3.11. A CLI that does not support a needed flag is
reported and the run pauses; the coordinator never parses prose in place of the structured output.

No silent truncation. Discussion bodies enter the packet whole. A packet above the configured size
is written to a file the agent is pointed at, never cut. A check that did not run is
`unavailable`, never `passed`.

## 26. Test strategy

| Layer | What the test asserts | How |
|---|---|---|
| `engine` | inputs to outputs | direct calls, no fakes |
| `services` | the coordination: which adapter was called, with what, in what order, and the resulting transition | hand-written fakes injected; in-memory SQLite with the real migrations |
| `adapters` | argv built for each CLI, parsing of recorded outputs, error mapping | `FakeProcessRunner` replays `gh` and CLI outputs from `fixtures/` and asserts argv |
| end to end | one full loop | all fakes, fixture-driven, offline |
| live | the real CLIs and a throwaway repository | marked `live`, opt-in, never in the default run |

Fakes live in `tests/fakes/` as small classes, so the contract they encode is readable and reusable:
`FakeAgent` returns canned `PhaseOutput`s and counts calls; `FakeGitHub` holds an in-memory PR and
can be told to lose the response of the next post; `FakeGit` can move the remote head between calls;
`FakeProcessRunner` maps argv prefixes to scripted results; `FakeClock` is set by the test. No
mocking library: a fake at the boundary is an assumption written down once.

Every test builds its own fakes and its own `:memory:` database. Test names are behaviour
sentences; bodies follow arrange, act, assert. Fixtures: `fixtures/prs/984/` (a seven-pass
thread) and `fixtures/agent_outputs/`, both synthetic and shaped after recorded runs.

```bash
python -m pytest -q            # default: offline, seconds
python -m pytest -q -m live    # opt-in: real CLIs, a throwaway repository, HTTPS
```

## 27. Test-first order of work

Each line is one behaviour: write the test, watch it fail, write the least code that passes,
refactor under green. A line that passes before any code is written is a wrong test, and gets
rewritten. Layers: E engine, S service, A adapter, R repository, C config or cli, X end to end.

### Milestone 1, skeleton and durable runs

1. C: a valid TOML file loads into typed settings with the section 4 defaults filled in.
2. C: a missing required key raises, naming the key and the file.
3. C: a command given as a string instead of an argument array is rejected.
4. R: migrations create the schema on an empty database and are idempotent.
5. S: `start` creates a run in `preparing` with head, base, merge base, budgets and versions recorded.
6. S: a second `start` for the same PR returns the active run and creates nothing.
7. S: two coordinators cannot both hold the lock for one repository and PR.
8. A: `fetch_discussion` returns reviews, inline threads with replies and resolved flags, and issue
   comments, from the fixtures, across pages.
9. E: rendering tags a post with the tool marker as reviewer or author and an unmarked post as human.
10. E: the digest hash changes when a reply is added and stays when fetch order changes.
11. S: `prepare` adds the worktree on the tool-owned branch, sets the worktree's own push URL to
    `DISABLED`, and runs the registered prepare command, in that order.
12. S: a dirty tree at phase start pauses the run with `workspace_dirty` and changes nothing.
13. S: an outbox entry is written before the gateway is called and the receipt after.
14. S: on restart, an entry with no receipt is reconciled by marker search before any re-send.
15. S: `pause` keeps the run resumable; `stop` ends it and keeps the worktree.
16. C: `status` and `show` render from the database with no adapter constructed.
17. E: the import scan fails on an `engine` module that imports outside `engine` or `types`.

Acceptance: a prepared run stops and resumes without losing inputs or touching another checkout.

### Milestone 2, read-only review and assessment

1. A: the Codex adapter builds argv with `-C`, `--sandbox read-only`, `--ignore-user-config`, the
   model and effort overrides, `--output-schema`, `-o`, `--json`, and sends the prompt on stdin.
2. A: a non-zero exit yields `process_failed` with captured stderr; a missing output file yields
   `malformed_output`.
3. A: output that violates the schema yields `schema_violation` naming the failing path.
4. A: a usage-limit message in the event stream yields `usage_limit`.
5. A: a timeout kills the process group and yields `timeout`.
6. A: the Claude assessment argv carries the read-only tool list, the deny flags and strict MCP
   config; the fix argv carries bypass plus the `gh` and push denies.
7. A: the Claude adapter reads the structured output field of the JSON result; absent yields
   `malformed_output`.
8. E: every file in `schemas/` is strict: all properties required, `additionalProperties` false.
9. S: the context packet contains PR metadata, instruction files, the rendered discussion, the
   ledger, decisions, verification results, and the phase's permitted actions.
10. S: `--author-session auto` picks the newest session file whose `gitBranch` equals the head
    branch, and falls back to the handoff packet when none matches.
11. E: ids are assigned `R<pass>-F<n>` in order; a fingerprint survives a line move.
12. E: `supersedes` matches a prior finding; a fingerprint match without it is flagged
    `possible_duplicate_of`.
13. E: the lifecycle table allows the section 11 transitions and raises on any other.
14. S: an assessment that misses a disposition for an active finding is rejected as malformed.
15. X: `--inspect-only` writes artifacts and the fake gateway records zero writes.

Acceptance: both agents inspect the whole repository and disagreements are preserved without an edit.

### Milestone 3, fix, verify, publish, rereview

1. S: the fix request lists only accepted ids with their protected behaviours and the scope boundary.
2. E: files no accepted finding names are listed as drift.
3. S: verification runs format then checks with argv from config and records exit codes and log
   paths; exit 3 is `unavailable`; `unavailable` never satisfies completion.
4. S: failed checks lead to one repair attempt, then `checks_failed`; the counter survives restart.
5. E: a commit message with a forbidden trailer is refused.
6. S: the guarded push carries the expected parent; a moved remote head yields `head_changed` and
   pauses; no argv ever contains a bare force flag.
7. E: a finding on a line inside a hunk becomes an inline comment; outside, a body entry with
   `file:line`.
8. S: one review per pass with markers; replies land in the thread of the finding's comment; the
   summary comment is posted once per pass.
9. S: a rereview on the same head with a new discussion digest publishes; the same head and digest
   does not.
10. S: rereview verdicts (verify, withdraw, accept rejection, dispute) apply to the lifecycle.
11. E: completion requires closed required findings, answered blocking questions, passed checks on
    the current tree, and a current digest.
12. X: a valid finding is accepted, fixed, verified, pushed and verified by the rereview.
13. X: a rejected finding is accepted by the reviewer without a new commit.
14. X: a PR closed externally stops writes and keeps history.

Acceptance: one real draft PR on the pilot repository finishes the loop with verified fixes and a traceable thread.

### Milestone 4, inbox and alignment

1. X: an adjacent finding from an assessment lands in the inbox with source PR, commit and run,
   status `new`, and causes no edit and no extra pass.
2. E: a clear duplicate across PRs adds evidence; an ambiguous one keeps both records with a suggestion.
3. S: the mirror comment is edited in place, never re-posted.
4. E: each signal fires on its synthetic ledger, and an approve followed by request-changes on new
   commits is not a reversal.
5. S: a signal pauses editing, requests the note, then the assessment; agreement becomes a
   versioned decision present in the next packet.
6. E: the two arbitration prompts carry swapped labels and no role names.
7. E: matching verdicts decide; a split on an `ISSUE` is an exception and the run continues; a
   split on a `BLOCKER` ends the run `blocked`; two `neither` fall to the severity rule.
8. S: a decided dispute re-raised without new evidence produces one report line and no agent call.
9. E: the final report lists every exception with both positions and both rationales.
10. S: the run stops after the configured passes with the report; a per-run override is honoured.

Acceptance: adjacent findings never widen a PR; an A-to-B-to-A reversal ends in a decision or a
recorded exception with no developer prompt.

### Milestone 5, cloud-readiness gate

1. S: `backup` archives the state directory and `restore` reproduces it in a fresh location.
2. C: `doctor` reports the effective auth route under `CLAUDE_CODE_OAUTH_TOKEN` and a device login.
3. live: one PR runs through the container image on the Mac.

### Milestone 6, daily use

Behaviour lists come from what the first PRs surface; each fix enters through a failing test that
reproduces the surprise.

1. S: after `prepare` on a real repository, the main checkout's push URL is unchanged and a push
   from the worktree is refused.
2. A: a push URL in the shared config does not reach through the worktree's lock.
3. A: a repository without `extensions.worktreeConfig` is refused with the command that turns it
   on, and its shared config is left as it was.
4. A: a lock that git reads back with any other push URL is refused.
5. C: `doctor` fails a registered repository without `extensions.worktreeConfig`, naming the command.

## 28. Skeleton and tooling

```text
review-loop/
├── PLAN.md                 this file
├── pyproject.toml          requires-python >= 3.11; deps: jsonschema; dev: pytest
├── review_loop/            the package (section 24)
├── prompts/                review-initial, review-again, assess, fix, align, arbitrate
├── schemas/                review, assessment, fix, alignment, arbitration (strict JSON Schema)
├── fixtures/               prs/, agent_outputs/ (synthetic, shaped after recorded runs)
└── tests/                  engine/, services/, adapters/, e2e/, live/, fakes/
```

Console script: `review-loop = review_loop.cli.main:main`. Setup: `python3 -m venv .venv`,
`.venv/bin/pip install -e '.[dev]'`. Verification of any change: `python -m pytest -q`. Version
control starts when the developer decides; until then the directory is the source of truth.

## 29. Standards checklist for every change

- A failing test exists before the code, and it failed for the right reason.
- The test names a behaviour and asserts an outcome, never an internal.
- External processes, GitHub and the clock are behind a Protocol and faked in tests.
- Expected failures are `Result` values; bugs and missing dependencies raise with the fix in the
  message; no silent fallback and no silent truncation anywhere.
- No path, budget, model or command is hardcoded outside `config/`.
- Functions stay short and flat; names say what they do and whether they write.
- Duplication is left inline until it appears a third time, then extracted; a domain abstraction
  needs two named shapes.
- The import scan passes.
- Any text bound for GitHub carries the marker and no AI provenance line.

## 30. Implementation notes (2026-09-27)

What the spike and the build settled, where they differ from the plan above:

- **Reviewer sandbox.** `codex exec --sandbox read-only` still runs commands; the PR #984 spike ran 39
  of them (PHP and Node reproductions) and reported honestly what it could not run. Read-only stays.
- **Codex flags.** `--ignore-user-config` keeps the ChatGPT login; the effort override is
  `-c 'model_reasoning_effort="ultra"'`; `--output-schema` accepted the strict draft-07 files; the
  thread id arrives in the `thread.started` event. `codex login status` prints to stderr.
- **Claude flags.** `--mcp-config` needs `{"mcpServers":{}}`; `--tools` is variadic, so the prompt goes
  on stdin; `--permission-prompts none` denies anything that would prompt; the result carries
  `structured_output`, `subtype`, `is_error`, `session_id`, `permission_denials`. The read-only
  assessment of PR #984 was denied 13 times when it reached for other worktrees, `gh`, and paths
  outside the checkout, and still produced two accepted findings and two adjacent ones.
- **Memory.** A worktree under `~/.review-loop/worktrees` loads the pilot's project memory, so the
  external location holds.
- **Manual-era posts.** Every historical review and reply is by one account with no marker. The
  renderer classifies unmarked posts by the PR author by shape (`EXECUTIVE SUMMARY`, `## Review
  response`, severity-tagged roots, `Accepted`, `Agreed`, `Taken`) and by alternation inside a thread
  the tool did not start; in a tool-started thread every unmarked reply is a human.
- **Silence closes.** A prior finding a rereview does not mention is verified (or its rejection
  accepted) by omission, recorded as such, so a forgetful reviewer cannot stall the loop.
- **A dispute without new evidence** is recorded as accepting the rejection, and a concern re-raised
  after a decision with no new evidence gets the standing decision again with no model call.
- **Not changed** by the fix phase becomes a pending rejection carrying the author's reason, so the
  reviewer judges it in the rereview instead of the loop repeating the fix request.
- **Commits.** The coordinator commits with the author's title and body, strips attribution lines,
  appends `Review response, pass N: <ids>`, and pushes with `ls-remote` plus `--force-with-lease` to
  the PR's head ref over the registered HTTPS remote; the worktree's own push URL is `DISABLED`.
- **Layout.** `services/phase_support.py` holds what every phase shares; `run_coordinator.py` has
  prepare, review and assess; `fix_verify.py`, `publish.py`, `alignment.py`, `completion_phase.py`
  the rest. Repositories are plain functions over one SQLite connection with two migrations.
- **Watching a run.** The reviewer's event stream is written to `runs/<id>/pass-<n>/review/events.jsonl`
  as it arrives, and the CLI prints every phase transition, so a long pass can be followed with
  `tail -f` on either. Thread resolution happens after a rereview closes a finding, once per thread.
- **Live tests.** `tests/live/` holds the opt-in checks against the real CLIs (`doctor` on this machine,
  the `gh` gateway reading the merged PR named in `REVIEW_LOOP_LIVE_PR`); the default run deselects them.
- **First live run (PR #1007, inspect-only).** The Codex review returned two evidenced ISSUE findings
  in nine minutes. The Claude assessment failed twice with zero dispositions: in `--permission-mode
  default` the Read tool is denied outside the checkout, and the packet lives under
  `~/.review-loop/runs`. Every agent request now grants the pass directory with `--add-dir`; the
  bounded retry and the `agent_failed` pause behaved as designed, and `resume` re-ran the phase.
- **Push lock scope (2026-09-28).** That run set the lock with `git remote set-url --push` inside its
  worktree, which wrote `remote.origin.pushurl = DISABLED` into the pilot's shared `.git/config` and
  stopped `git push origin` from every checkout and worktree. The test that covered it read the push
  URL only from the worktree, where it was `DISABLED` either way. The lock is now per worktree
  (section 8), and the tests check the main checkout too.

## 31. Adversarial review of the tool (2026-09-28)

Before the first publishing run, Codex (read-only, ultra) and an independent Claude reviewer were
each asked to find every way the tool could push, post, lose data or leak wrongly. Codex returned
44 findings, Claude 16; most overlapped. Every accepted finding entered through a failing test; the
suite grew from 196 to 277 tests. The ones that changed the design:

- **The push guard lived in the shared git config.** `git remote set-url --push` in a linked
  worktree rewrote the developer's own `origin`; their checkout could not push until it was
  restored by hand. Pushes are now disabled for agents through `GIT_CONFIG_COUNT/KEY/VALUE`
  variables, credential helpers are cut off (`GIT_CONFIG_GLOBAL` empty, `GIT_CONFIG_NOSYSTEM=1`,
  `GH_CONFIG_DIR` empty), and the coordinator pushes one SHA by explicit URL with a lease, no
  implicit tags or submodules, and re-reads the remote afterwards.
- **Inspect-only and the publication switches were prompts, not guards.** Mode is now a property of
  the run set at enrolment; the step loop refuses mutating phases under it; the three switches are
  read at each effect.
- **Control from outside did nothing.** The loop re-reads the run before every phase, a stop or
  manual pause recorded elsewhere wins over an in-flight transition, and Ctrl-C kills the agent's
  process group.
- **Replay could post twice.** The review phase persists its output, findings and pass number in
  one transaction before posting; every post checks its marker; `reconcile` runs before the first
  write of each publishing phase; a lost inbox comment id is recovered from its receipt; a push that
  landed before a crash is recognised on resume.
- **Verification could be satisfied by a formatter, a timed-out check or a tree that changed
  under the checks.** Required checks alone decide, timeouts are unavailable, the tree is hashed
  before and after the checks, and the pushed commit's tree must equal the verified tree.
- **Commits could carry someone else's history or a hook's trailer.** Hooks are disabled for the
  coordinator's commit, the fresh commit must sit on the expected parent, a recovered commit must
  carry the pass line and no trailer, the provenance policy covers ordinary wording, and every
  GitHub-bound body passes the same filter. Arbiters appear as "arbiter 1" and "arbiter 2".
- **The lifecycle had holes.** `answer` closes only a QUESTION; a review cannot resolve a finding it
  raised in the same pass; `verified` applies only to a fixed finding; a rereview must mention every
  pending BLOCKER (silence closes lesser findings only); standing decisions follow explicit
  `supersedes` at equal or lower severity and never except a BLOCKER; one "neither" on a BLOCKER is
  a split; a dispute on the last pass ends the run; the alignment budget is enforced per concern.
- **Enrolment and workspace.** Fork PRs are refused, repositories match on exact owner and name,
  the head is fetched before the merge base, a PR or a fix that changes a registered script pauses
  the run, a reused worktree must be on the tool's branch, the author session lookup is scoped to
  the registered checkout, and `--resume` forks the session instead of appending to the developer's.
- **Operability.** Each agent attempt has its own output directory and stale output is deleted; a
  write phase is never retried on a partial tree; gateway errors pause with the entry left
  uncertain; `restore` runs before any configuration exists; backups snapshot the database through
  the SQLite API and carry a patch per dirty worktree; prompts and schemas ship inside the package;
  a detached coordinator logs to the run directory, a crash pauses the run with its traceback, and
  the PR lock tells a driven run from one nobody drives.

Accepted residuals, documented rather than fixed: an agent under `bypassPermissions` can still
invoke a tool by absolute path, so containment is layered (environment, shim, denies, and the
head check that catches a rogue push at verification) rather than a boundary; registered project
scripts run with the coordinator's host privileges for allowed authors' PRs, mitigated by the
scripts-changed pause; the pilot's prepare step without `--js` symlinks built assets to the main checkout,
so an agent that runs a build writes there.

## 32. Configurable agents (2026-09-28)

Codex reviews and Claude writes by default; each repository can name another agent for either role
with `reviewer` and `author` under `[repositories.<name>.review]`. The registered agents are `claude`,
`codex`, `agy` (Antigravity CLI) and `opencode`. Adding one is an adapter behind the same
`run(PhaseRequest)` boundary plus an `AgentProfile` (binary, default model and effort, whether a
resumed session is forked). What changed around the adapters:

- **Defaults follow the agent.** An unset model or effort takes the chosen agent's default, and an
  empty one is not passed at all, so switching agents never inherits another CLI's model. Alignment
  and arbitration run at `high` unless the repository left effort to the CLI.
- **The coordinator checks read-only phases.** Headless `agy` allows file writes in the workspace, and
  opencode's read-only rules are configuration. Every read-only phase now records HEAD and the
  changed-file list before the agent runs and compares them after; any change pauses the run with
  `read_only_violated` and no retry, leaving the change for the developer. This covers Claude and
  Codex too.
- **Structured output without a schema flag.** opencode gets the schema appended to the prompt; the
  adapter takes the reply's last JSON object and the usual schema validation and bounded retry apply.
- **opencode permissions** are set on a tool-owned `review-loop` agent through
  `OPENCODE_CONFIG_CONTENT`, which outranks a reviewed repository's own `opencode.json`. Read-only
  denies edits, web access, subagents and every command but read-only git and `ls`; write denies `gh`,
  `git push` and `git commit`. Reading outside the checkout is limited to the pass directory.
- **Sessions.** Only Claude Code's desktop sessions are looked up on disk. An explicit
  `--author-session` is refused at `start` for an author that cannot fork (`codex`, `agy`), so the
  developer's own session is never appended to. Sessions the tool created are resumed as before.
- **Labels.** Arbitration records and inbox items carry the configured agent names; one agent in both
  roles arbitrates twice as `<name>-reviewer` and `<name>-author`.
- **Doctor and versions** check only the agents some repository uses.

Residuals: the worktree check does not see writes outside the worktree, content changes to a file that
was already dirty, or ignored files. `agy` and `opencode` load MCP servers from the developer's own
settings. Both adapters were written from the CLIs' published docs (the `agy` headless reference; a
community reference for opencode's `--format json` events) and tested offline only; the first
inspect-only run with each is their Milestone 0.
