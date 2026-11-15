"""The AI suggestions (AAC-006): code only. The model interprets, code enforces.

Every call goes through `ClaudeClient`, behind a spend cap and a per-run limit. A failure of
any kind is "no suggestion". Suggestions are map entries marked `reviewed: false`; nothing in
the desired or actual state uses one until a person has reviewed it.
"""
