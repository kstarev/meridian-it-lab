"""In-process mocks shaped like the real APIs (ADR-012).

No ports and no network. Each mock resets cleanly between eval runs and logs
every read so the graders can check what the agent looked at (ADR-003).
"""
