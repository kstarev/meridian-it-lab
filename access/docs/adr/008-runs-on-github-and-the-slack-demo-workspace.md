# AAC-008: Runs only on GitHub and Slack; summaries go to the Slack demo workspace

**Date:** 2026-11-14 · **Status:** Accepted; planned, not built. The Slack target was the user's choice (2026-11-14). Changes the lab's in-scope list, which allowed the Slack demo workspace for tier0's demo only, so it needs this decision (lab brief: "any change needs an ADR").

**Context:** Access-as-Code is a scheduled job: it should run every night without anyone's laptop. Its output is meant to be seen, in the pull request and in Slack, where an IT team would see it.

**Decision:**

- **Everything runs on GitHub Actions:** the nightly plan (a scheduled workflow), apply (on merge), CI (the existing lint and test workflow) and the paid model runs (by hand, with the owner's approval, [AAC-006](006-model-choice-and-spend-cap.md)). Nothing depends on a personal device.
- **Plan summaries are posted to the existing Slack demo workspace** (fake data only), in an `#it-access-plans` channel, through an incoming webhook. The post holds the summary, the counts and a link to the pull request, never a token or anything beyond what the pull request shows.
- **Secrets only in GitHub Actions secrets:** the Slack webhook URL, the Anthropic API key (in the approval-gated environment) and the GitHub App's private key. Nothing secret is in the repo, the `access-state` branch or a log. Push protection and secret scanning are on for the repo.
- **Workflows follow the lab's baseline** (lab ADR-011): actions pinned to commit SHAs, `permissions: {}` at the top and per-job grants, no credentials kept by checkout, one concurrency group for anything that writes to `access-state`.
- **Tests stay offline.** CI never posts to Slack or calls Claude; both have mocks, and the network guard blocks outbound calls in tests.

**Consequences:**

- The lab brief's in-scope line about the Slack demo workspace now includes Access-as-Code's plan summaries.
- A broken webhook or Slack outage doesn't block the plan; the pull request is the source of truth, and the post is best-effort.
- Scheduled workflows on GitHub can start late, so "nightly" means roughly nightly.
- The user creates the webhook and the secrets, in milestone 5.
