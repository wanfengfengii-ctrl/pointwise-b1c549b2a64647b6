class PageStore:
    """Fixed-length physical pages belonging to one camera run."""

    def __init__(self, frame_length: int) -> None:
        if type(frame_length) is not int or frame_length < 1:
            raise ValueError('page length must be a positive integer')
        self._frame_length = frame_length
        self._pages: dict[int, bytearray] = {}
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError('page store is closed')

    def _check_address(self, address: int) -> None:
        self._require_open()
        if type(address) is not int or address < 0:
            raise ValueError('page address must be a nonnegative integer')

    @property
    def page_count(self) -> int:
        self._require_open()
        return len(self._pages)

    def write_page(self, address: int, data: bytes) -> None:
        self._check_address(address)
        if type(data) is not bytes or len(data) != self._frame_length:
            raise ValueError(f'page needs exactly {self._frame_length} bytes')
        page = self._pages.get(address)
        if page is None:
            self._pages[address] = bytearray(data)
        else:
            page[:] = data

    def read_page(self, address: int) -> bytes:
        self._check_address(address)
        return bytes(self._pages[address])

    def release_page(self, address: int) -> None:
        self._check_address(address)
        del self._pages[address]

    def close(self) -> None:
        self._closed = True
        self._pages.clear()
