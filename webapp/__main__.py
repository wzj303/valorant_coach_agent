"""Start the coach web UI.

    python -m webapp                # listen on 0.0.0.0:8765
    python -m webapp --port 9000    # choose another port
    python -m webapp --host 127.0.0.1  # local-only
"""
import argparse
import logging

from .server import DEFAULT_PORT, start_server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m webapp",
                                     description="VALORANT AI Coach web frontend")
    parser.add_argument("--host", default="0.0.0.0",
                        help="interface to listen on (default: 0.0.0.0, all interfaces)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default: {DEFAULT_PORT})")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        start_server(args.host, args.port)
    except OSError as exc:
        print(f"error: cannot start server on {args.host}:{args.port}: {exc}")
        return 1
    import threading
    try:
        threading.Event().wait()  # serve until Ctrl+C
    except KeyboardInterrupt:
        print("\nserver stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
