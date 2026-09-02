"""Your brain as a Strands agent tool (EMOTIV EPOC X via the Cortex API)."""

__version__ = "0.2.1"

# Lazy exports (PEP 562): `import strands_emotiv` stays dependency-free,
# heavy modules load on first attribute access.
_EXPORTS = {
    "CortexClient": "cortex",
    "CortexError": "cortex",
    "FakeCortex": "fake",
    "Sample": "types",
    "BrainState": "state",
    "Event": "events",
    "EventEngine": "events",
}

__all__ = ["__version__", *_EXPORTS]


def __getattr__(name: str):
    mod = _EXPORTS.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(f".{mod}", __name__), name)
