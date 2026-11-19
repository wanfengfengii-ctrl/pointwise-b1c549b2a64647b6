def exceeds_threshold(frame_total: int, threshold: int) -> bool:
    """A frame exactly at the configured limit stays within tolerance."""
    return frame_total > threshold
