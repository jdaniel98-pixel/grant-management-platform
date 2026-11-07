"""Business rules. Plain functions over the repository; no Streamlit, no SQL."""


class ValidationError(ValueError):
    """Raised with {field: message} so screens can show every problem at once."""

    def __init__(self, errors):
        self.errors = dict(errors)
        super().__init__("; ".join(f"{field}: {message}" for field, message in self.errors.items()))
