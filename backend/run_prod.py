"""Lance Uvicorn en production (Render / VPS) : 0.0.0.0 + PORT."""

from __future__ import annotations

import os

import uvicorn

from config import BACKEND_PORT, configure_utf8_stdio

configure_utf8_stdio()


def main() -> None:
    port = int(os.getenv("PORT", str(BACKEND_PORT)))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
