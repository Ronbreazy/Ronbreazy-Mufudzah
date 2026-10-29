"""
ai_errors.py
------------
One shared exception, used by every AI provider module (ai_provider.py for
ChatGPT, ollama_provider.py for Ollama, and any future provider). Keeping
it in its own tiny file means server.py can catch exactly one exception
type regardless of which provider is active, and adding a third provider
later doesn't require touching this file at all.
"""


class AIProviderError(Exception):
    """Raised for any problem talking to an AI provider -- missing
    config, network error, or a response we couldn't parse. server.py
    catches this specifically and falls back to the rule-based version.
    """
    pass
