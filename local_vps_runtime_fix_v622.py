from __future__ import annotations


PATCH_MARKER = "V6.22 LOCAL VPS RUNTIME V1"


def install_local_vps_runtime() -> None:
    """Keep optimized Drive HTTP mode while routing local mode to VPS backend."""
    from drive_gateway import DriveGateway, DriveGatewayError

    if getattr(DriveGateway, "_qlda_local_vps_runtime", False):
        return
    optimized_remote_post = DriveGateway._post

    def hybrid_post(self, action: str, payload=None, session_token: str = ""):
        if getattr(getattr(self, "config", None), "local", False):
            try:
                from local_vps_backend_v622 import LocalVPSError, dispatch
                return dispatch(action, payload, session_token)
            except LocalVPSError as exc:
                raise DriveGatewayError(str(exc)) from exc
            except DriveGatewayError:
                raise
            except Exception as exc:
                raise DriveGatewayError(f"VPS Local Storage lỗi: {exc}") from exc
        return optimized_remote_post(self, action, payload, session_token)

    DriveGateway._post = hybrid_post
    DriveGateway._qlda_local_vps_runtime = True
    DriveGateway._qlda_local_vps_runtime_marker = PATCH_MARKER
