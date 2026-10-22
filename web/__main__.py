import os

import uvicorn

from .app import create_app


def main() -> None:
    uvicorn.run(
        create_app(),
        host=os.environ.get("CONTROL_PANEL_HOST", "127.0.0.1"),
        port=int(os.environ.get("CONTROL_PANEL_PORT", os.environ.get("PORT", "8080"))),
        workers=1,
        timeout_graceful_shutdown=20,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
