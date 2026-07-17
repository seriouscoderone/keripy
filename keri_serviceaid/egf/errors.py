class EgfError(Exception):
    """Base for EGF resolution/validation failures. Fail closed — callers never fall back."""

class EgfNotFound(EgfError):
    def __init__(self, said: str, source: str):
        super().__init__(f"EGF artifact {said} not found in {source}")
        self.said, self.source = said, source

class EgfIntegrityError(EgfError):
    def __init__(self, said: str, computed_said: str):
        super().__init__(f"SAID mismatch: expected {said}, computed {computed_said}")
        self.said, self.computed_said = said, computed_said

class EgfDocumentError(EgfError):
    """Verifies by SAID but violates the egf-doc meta-schema / unsupported spec_version / unparseable."""

class NoAuthorityError(EgfError):
    """No authority matches (role, context) within the accepted phases."""
