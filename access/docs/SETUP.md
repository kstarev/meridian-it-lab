# Access-as-Code: the owner's setup steps

Two things only the repository owner can do: create the GitHub App that opens the plan pull requests, and protect `main` with a ruleset ([AAC-002](adr/002-the-plan-is-a-pull-request.md), [AAC-008](adr/008-runs-on-github-only.md)). Until part A is done, the nightly `Access plan` workflow still builds and uploads the plan and skips the pull-request step with a notice. It doesn't fail.

Nothing here goes in the repository. The private key is stored only as an Actions secret.

## A. The GitHub App

What it can do: read and write the repository's contents (to push the bot branches `access/plan` and `access/plan-held`) and its pull requests (to open, update, comment on and close them). Nothing else. It has no webhook, it's installed on `meridian-it-lab` only, and each workflow run gets a token that lasts one hour.

### A1. Create the App

1. Open github.com and sign in.
2. Click your profile picture, top right.
3. Click **Settings**.
4. In the left sidebar, scroll down and click **Developer settings**.
5. Click **GitHub Apps**.
6. Click **New GitHub App**.
7. In **GitHub App name**, type `meridian-access-plan-bot`. If GitHub says the name is taken, add a suffix such as `-ks`.
8. In **Homepage URL**, type `https://github.com/kstarev/meridian-it-lab`.
9. Leave **Callback URL** empty.
10. Leave **Request user authorization (OAuth) during installation** unticked.
11. Leave **Enable Device Flow** unticked.
12. Leave **Setup URL** empty.
13. Under **Webhook**, untick **Active**.
14. Under **Permissions**, click **Repository permissions** to open it.
15. Set **Contents** to **Read and write**.
16. Set **Pull requests** to **Read and write**.
17. Check that **Metadata** shows **Read-only** (GitHub sets this itself).
18. Leave every other repository permission at **No access**.
19. Leave every **Organization permission** and **Account permission** at **No access**.
20. Under **Where can this GitHub App be installed?**, choose **Only on this account**.
21. Click **Create GitHub App**.

### A2. Note the App ID and make the private key

22. The App's **General** page opens. Find **App ID** near the top: it is a number. Copy it somewhere for step 37.
23. Scroll down to **Private keys**.
24. Click **Generate a private key**.
25. A `.pem` file downloads. Leave it where it is for now; you'll delete it in step 40.

### A3. Install it on meridian-it-lab only

26. In the left sidebar of the App's page, click **Install App**.
27. Click **Install** next to your account.
28. Choose **Only select repositories**.
29. Click **Select repositories** and pick **meridian-it-lab**.
30. Click **Install**.

### A4. Store the key and the App ID in the repository

31. Open `https://github.com/kstarev/meridian-it-lab/settings/secrets/actions`.
32. Stay on the **Secrets** tab and click **New repository secret**.
33. In **Name**, type `ACCESS_APP_PRIVATE_KEY`.
34. Open the downloaded `.pem` file in a text editor, select all of it, including the `-----BEGIN` and `-----END` lines, and paste it into **Secret**.
35. Click **Add secret**.
36. Click the **Variables** tab, then **New repository variable**.
37. In **Name**, type `ACCESS_APP_ID`. In **Value**, paste the App ID from step 22.
38. Click **Add variable**.
39. Close the text editor without saving.
40. Delete the `.pem` file, then empty the Trash. If you ever need a new key, generate one on the App's page and delete the old one there.

### A5. Try it

41. Open `https://github.com/kstarev/meridian-it-lab/actions/workflows/access-plan.yml`.
42. Click **Run workflow**, keep **main**, and click the green **Run workflow** button.
43. When the run finishes, all three jobs are green: **Build the plan for the tuning world**, **Check the plan bot is set up** and **Open, update or close the plan pull requests**.
44. Open the **Pull requests** tab. Two pull requests are open, both by `meridian-access-plan-bot[bot]`: **Access plan for 2026-12-01**, which touches only `access/plan/`, and **Access plan, held part, for 2026-12-01 (two approvals)**, which touches only `access/plan/held/`.
45. On each, **Lint and test** and **Plan approvals** have run. **Plan approvals** is red until it has enough code-owner approvals.

The held pull request needs two code-owner approvals. You are the only code owner, so it stays blocked. That is by design: there is no override and no stand-in approver. Merging the main plan pull request doesn't apply anything yet, because apply on merge isn't built and the `Access apply` workflow is still manual.

## B. The ruleset on main

This is what makes an approval mean something ([AAC-002](adr/002-the-plan-is-a-pull-request.md)): changes reach `main` only through a pull request, with a code owner's review, with the checks passing, and with approvals dismissed when new commits are pushed.

46. Open `https://github.com/kstarev/meridian-it-lab/settings/rules`.
47. Click **New ruleset**, then **New branch ruleset**.
48. In **Ruleset Name**, type `main`.
49. Set **Enforcement status** to **Active**.
50. Under **Bypass list**, click **Add bypass**, tick **Repository admin**, and click **Add selected**. See "Your own pull requests" below before you do this step.
51. Next to **Repository admin** in the list, change **Always allow** to **For pull requests only**.
52. Under **Target branches**, click **Add target**, then **Include default branch**.
53. Under **Branch rules**, leave **Restrict deletions** ticked.
54. Leave **Block force pushes** ticked.
55. Tick **Require a pull request before merging**.
56. Under it, set **Required approvals** to **1**.
57. Tick **Dismiss stale pull request approvals when new commits are pushed**.
58. Tick **Require review from Code Owners**.
59. Tick **Require status checks to pass**.
60. Leave **Require branches to be up to date before merging** unticked. The bot rebuilds its branches on `main` every night anyway.
61. Click **Add checks**, type `Lint and test`, and pick it.
62. Click **Add checks** again, type `Plan approvals`, and pick it.
63. Scroll down and click **Create**.

### Your own pull requests (step 50)

GitHub never lets the author of a pull request approve it, and you are the only code owner. With steps 56 and 58 and no bypass, no pull request you open, including the ones Claude opens under your account, could ever be merged.

Step 50 lets you, as repository admin, merge your own pull requests by ticking **Merge without waiting for requirements to be met (bypass rules)** on the merge button. "For pull requests only" means you still can't push to `main` directly, and every bypass is recorded in the ruleset's insights.

The cost: on a plan pull request you could tick the same box. Don't. The bot opens the plan pull requests, so you can approve the main plan normally. The held plan stays blocked, and apply checks the approvals again by itself ([AAC-007](adr/007-safety-rails.md)). If you would rather have no bypass at all, skip steps 50 and 51; then every pull request you author stays blocked until a second person is a code owner.

Known limit: Plan approvals fails closed on a PR that only deletes plan files. The owner merges such a PR with the bypass on purpose and records it in the rulings log.

## C. One more setting

64. Open `https://github.com/kstarev/meridian-it-lab/settings`.
65. Under **Pull Requests**, tick **Automatically delete head branches**. The bot deletes its own branch when it closes a plan pull request; this does the same when one is merged.

## What the bot does each night

- It builds the plan for the tuning world (simulated month, as of the policy's reference date) and uploads it as the `access-plan` artifact.
- For each part, main and held:
  - empty, or already exactly on `main`: it closes the open pull request with a comment and deletes its branch;
  - the same as the open pull request: it does nothing, so approvals stay;
  - changed: it force-pushes one new commit on top of `main`, which dismisses earlier approvals, replaces the description and comments with what changed;
  - none open: it opens one.
- Code: `access/src/meridian_access/plan_pr.py`; tests: `access/tests/test_plan_pr.py` and `access/tests/test_plan_workflow.py`.
