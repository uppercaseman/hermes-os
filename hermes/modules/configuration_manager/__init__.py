"""Hermes Configuration Manager -- centralised, environment-safe
configuration for every other module: environment variables, local
config files, module- and provider-specific namespaces, feature flags,
dry-run defaults, validation, and redacted export, all behind one
synchronous in-memory lookup with event-publishing side effects on
change. See README.md for the full architecture and requirement map.
"""
