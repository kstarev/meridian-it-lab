# Compliance mapping

This page maps tier0's design to regional rules. It is a mapping, never a claim of compliance, and not legal advice. Meridian and all its data are fictional.

| Region | Rule | How tier0's design maps to it |
|---|---|---|
| EU | AI Act Art. 50: people must be told they're dealing with an AI | The bot says it's an AI in its first message to each requester, and evals check this as a required fact. tier0 routes tickets by category, never by a person's behaviour or traits, and never evaluates or monitors employees. That keeps it out of the Act's high-risk employment uses (Annex III). |
| EU | GDPR | **Minimisation:** tools read only the fields a ticket needs, and traces hold no message content by default. **Residency:** an EU option keeps model calls and stored data in the EU. **Retention:** traces 30 days, audit log 1 year, tickets 2 years. |
| Germany | Works council co-determination (BetrVG §87(1) no. 6) | Co-determination applies to any tool able to monitor employees, so a rollout needs a works agreement. tier0's side of it: pseudonymised logs, with the re-identification key held separately and used only for security or audit cases, and no per-employee performance analytics for IT staff or requesters. |
| US | SOC 2, NIST AI RMF | **SOC 2:** approvals, just-in-time credentials and the audit log map to CC6 (logical access), and runbook pull requests to CC8 (change management). **AI RMF:** ADRs and approval tiers (Govern), the OWASP threat model (Map), evals (Measure), and the kill switch, rate limit and runbook gate (Manage). |
| Canada | PIPEDA and provincial law | PIPEDA principles are the baseline. PIPEDA covers employee data only at federally regulated businesses, so Meridian's employee data falls under provincial law where it exists: Alberta PIPA, BC PIPA and Quebec Law 25. |
| Singapore | PDPA | Employees are told what tier0 collects and why: the employment exception removes the need for consent, not for notice. Data sent out of Singapore, such as model calls, follows the transfer limitation obligation. |
