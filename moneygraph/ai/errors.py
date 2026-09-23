"""Public failures contain only safe codes; provider bodies never reach callers."""


class AIUnavailable(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__("AI-объяснение недоступно")


class InvalidContext(ValueError):
    """The selected snapshot cannot supply a coherent client context."""


class InvalidExplanation(ValueError):
    """A model response failed structural or factual validation."""
