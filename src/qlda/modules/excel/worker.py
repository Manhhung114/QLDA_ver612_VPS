from __future__ import annotations

from qlda.runtime import legacy_import


def main() -> None:
    """Production worker entrypoint owned by the V6.25 Excel module."""
    legacy_import("excel_worker_v624").main()


if __name__ == "__main__":
    main()
