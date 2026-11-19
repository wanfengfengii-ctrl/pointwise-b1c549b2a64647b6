def grayscale_sum(data: bytes, frame_length: int) -> int:
    """Sum the unsigned eight-bit pixels of one complete frame."""
    if type(data) is not bytes or len(data) != frame_length:
        raise ValueError('pixel bytes do not match the frame shape')
    return sum(data)
