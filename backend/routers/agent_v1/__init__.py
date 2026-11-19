"""The local Agent API's routes (``/agent/v1``), one module per group.

``backend/routers/local_agent.py`` is the router the app mounts; it includes
the routers here in a fixed order, which is the order FastAPI matches paths
in. ``common`` holds what the route modules share.
"""
