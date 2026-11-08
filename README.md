# meridian-it-lab

A portfolio lab for IT and security engineering at Meridian, a fictional, fully remote B2B SaaS company with 230 people in 14 countries. Meridian runs on Google Workspace, Okta, Slack, Jamf, Intune and Jira Service Management. It has ~140 SaaS apps (95 outside SSO), a 3-person IT team and SOC 2 and ISO 27001 to maintain. This lab builds the tools that team would need: an AI service desk agent, access defined as code, a radar for OAuth grants and automated audit evidence. Every project runs on one shared synthetic dataset with planted problems and is graded by precision and recall against its answer key.

> All data is synthetic. Nothing comes from any real employer.

**Status:** Phase 0, docs only.

## Structure

Folders are listed in build order. Each folder is created when its phase starts.

```
meridian-it-lab/
├── docs/             brief.md, decisions.md
├── dataset/          1. seeded generator: people, devices, apps, access, OAuth grants,
│                        tickets + planted problems + answer key + shared scorer
├── tier0/            2. AI service desk agent: answers, triages and acts through
│                        approval tiers A (auto), B (auto, logged), C (human)
├── access-as-code/   3. intended access in version control; detect drift and orphans
├── oauth-radar/      4. find risky third-party OAuth grants (Google, Slack)
└── evidence/         5. generate SOC 2 / ISO 27001 evidence from the data
```

## Docs

- [Brief](docs/brief.md): problem, fact sheet, top tickets, metrics, scope
- [Decisions](docs/decisions.md): decision log
