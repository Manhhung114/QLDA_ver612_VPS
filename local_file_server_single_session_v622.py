from __future__ import annotations

from single_session_v622 import install_local_single_session

# Install before local_file_server_v622 imports backend functions by name. This
# makes every upload ticket dependent on the login session that created it.
install_local_single_session()

from local_file_server_v622 import main  # noqa: E402


if __name__ == "__main__":
    main()
