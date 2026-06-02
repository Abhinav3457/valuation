"""
Launcher: starts the dashboard at http://localhost:8000

Usage:
    python dashboard.py
    python dashboard.py --port 9000

On Render / cloud: PORT env var is auto-detected, binds to 0.0.0.0.
"""

from webapp.server import serve

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    args = p.parse_args()
    serve(args.host, args.port)

