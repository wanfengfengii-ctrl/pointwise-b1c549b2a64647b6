from collections.abc import Iterable


class CameraBindings:
    def __init__(self, camera_ids: Iterable[str]) -> None:
        if isinstance(camera_ids, str):
            raise ValueError('camera catalog must be an iterable of camera IDs')
        names = tuple(camera_ids)
        if any(type(name) is not str or not name for name in names):
            raise ValueError('camera IDs must be nonempty strings')
        self._registered = frozenset(names)
        self._active: dict[str, str] = {}

    def require_registered(self, camera_id: str) -> None:
        if type(camera_id) is not str or camera_id not in self._registered:
            raise KeyError(camera_id)

    def claim(self, camera_id: str, run_id: str) -> None:
        self.require_registered(camera_id)
        if camera_id in self._active:
            raise ValueError(f'camera {camera_id!r} already has an active run')
        self._active[camera_id] = run_id

    def release(self, camera_id: str, run_id: str) -> bool:
        self.require_registered(camera_id)
        if self._active.get(camera_id) != run_id:
            return False
        del self._active[camera_id]
        return True

    def clear(self) -> None:
        self._active.clear()
