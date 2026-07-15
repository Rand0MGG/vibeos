"""Goal 01 modular-monolith foundation.

Only :mod:`vibeos.core.composition` is a production construction entry point.
The domain and application packages deliberately do not import legacy runtime
modules or concrete adapters.
"""
