class Raw2ZstdSource:
    """P1 adapter boundary; connect the existing C++ delta::Raw2Decoder.

    Must handle concatenated zstd frames and preserve SDK SensorFrame/EventGroup
    boundaries. Never reinterpret a snapshot or one VI buffer as a raw sensor frame.
    """
    def __init__(self, path):
        self.path = path

    def __iter__(self):
        raise NotImplementedError("RAW2 .zst SDK adapter NOT IMPLEMENTED / NOT TESTED (P1)")
