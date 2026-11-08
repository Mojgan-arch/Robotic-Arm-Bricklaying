"""Expected user-input outcomes, distinct from execution failures."""


class ClarificationRequired(RuntimeError):
    """Pause design without losing the questions or treating it as a crash."""

    def __init__(self, questions: list[str]) -> None:
        self.questions = list(dict.fromkeys(questions))
        super().__init__("Clarification required: " + "; ".join(self.questions))


def answered_brief(brief: str, answer: str) -> str:
    """Preserve the request and add the user's own clarification."""
    if not answer.strip():
        raise ValueError("Enter your answer before continuing.")
    return brief.rstrip() + "\n\nUser clarification:\n" + answer.strip()
