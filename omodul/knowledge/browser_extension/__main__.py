"""Entry point: python -m omodul.knowledge.browser_extension [init|serve]"""
from __future__ import annotations

import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        from .auth import init_token
        token = init_token()
        print("Token generated and saved to ~/.stratum/secrets/browser_ext_token.txt")
        print("\nCopy this token into the browser extension Options page:\n")
        print(f"  {token}\n")
        return

    import uvicorn
    from .server import app

    print("Starting Stratum Browser Extension API on http://127.0.0.1:14567")
    print("Press Ctrl+C to stop.\n")
    uvicorn.run(app, host="127.0.0.1", port=14567, log_level="info")


if __name__ == "__main__":
    main()
