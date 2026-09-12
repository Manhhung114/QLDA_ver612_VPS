from __future__ import annotations

from runtime_settings_bridge_v622 import install_runtime_settings_bridge
from single_session_v622 import install_local_single_session

# Install mutable Admin settings before the local file server imports backend
# functions by name. This lets the upload service read the same shared storage
# path/upload limits as Streamlit without editing qlda.env.
install_runtime_settings_bridge()

# Install before local_file_server_v622 imports backend functions by name. This
# makes every upload ticket dependent on the login session that created it.
install_local_single_session()

from local_file_server_v622 import main  # noqa: E402


if __name__ == "__main__":
    main()
