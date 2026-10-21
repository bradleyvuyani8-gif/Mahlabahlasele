import os

import uvicorn

from .api import TOKEN, app


def main():
    host = os.environ.get("MAHLA_HOST", "127.0.0.1")
    port = int(os.environ.get("MAHLA_PORT", "8000"))
    print("\n" + "=" * 56)
    print(" Mahlabahlasele  |  dashboard: http://%s:%d" % (host, port))
    print(" Dashboard token: %s" % TOKEN)
    print("=" * 56 + "\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()