from __future__ import annotations

from qlda.shared.legacy import call_main


def main() -> None:
    """Production worker entrypoint owned by the V6.25 Excel module."""
    call_main("excel_worker_v624")


if __name__ == "__main__":
    main()
