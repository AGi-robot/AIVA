"""AIVA's registry for reversible telepathic signal encodings."""
from __future__ import annotations

from collections.abc import Callable
from cryptic_signal_core.telepathic_encoding import decode_text, encode_text


class CrypticSignalCore:
    """Select and run AIVA-approved signal encoding strategies.

    Add future schemes as named encoder/decoder pairs. Unknown scheme names
    fail closed so peers never silently decode a payload with the wrong method.
    """

    DEFAULT_SCHEME = "ternary_utf8_v1"

    def __init__(self) -> None:
        self._encoders: dict[str, Callable[[str], tuple[bytes, int]]] = {
            self.DEFAULT_SCHEME: encode_text,
        }
        self._decoders: dict[str, Callable[[bytes, int], str]] = {
            self.DEFAULT_SCHEME: decode_text,
        }

    def register_scheme(
        self,
        name: str,
        encoder: Callable[[str], tuple[bytes, int]],
        decoder: Callable[[bytes, int], str],
    ) -> None:
        """Register an encoding pair under a stable scheme name."""
        if not name or name in self._encoders:
            raise ValueError("Scheme name must be nonempty and unused")
        self._encoders[name] = encoder
        self._decoders[name] = decoder

    def encode(self, text: str, scheme: str | None = None) -> tuple[bytes, int]:
        """Encode text and return packed bytes and valid symbol count."""
        selected = scheme or self.DEFAULT_SCHEME
        try:
            encoder = self._encoders[selected]
        except KeyError as error:
            raise ValueError(f"Unsupported AIVA signal scheme: {selected}") from error
        return encoder(text)

    def default_scheme(self, scheme: str | None = None) -> str:
        """Return the selected scheme name for inclusion in message metadata."""
        selected = scheme or self.DEFAULT_SCHEME
        if selected not in self._encoders:
            raise ValueError(f"Unsupported AIVA signal scheme: {selected}")
        return selected

    def decode(self, signal: bytes, pulse: int, scheme: str | None = None) -> str:
        """Decode a signal with the selected scheme."""
        selected = scheme or self.DEFAULT_SCHEME
        try:
            decoder = self._decoders[selected]
        except KeyError as error:
            raise ValueError(f"Unsupported AIVA signal scheme: {selected}") from error
        return decoder(signal, pulse)
