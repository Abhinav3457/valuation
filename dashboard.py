"""
Launcher: starts the dashboard at http://localhost:8000

Usage:
    python dashboard.py
    python dashboard.py --port 9000
"""

from webapp.server import serve

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    serve(args.host, args.port)
