"""Entrypoints layer: thin AWS Lambda handlers (composition root).

Each handler parses the event, builds adapters, calls a use case, and returns.
No business logic here.
"""
