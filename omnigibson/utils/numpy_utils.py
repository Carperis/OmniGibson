import numpy as np
import torch as th


class NumpyTypes:
    FLOAT32 = np.float32
    INT32 = np.int32
    UINT8 = np.uint8
    UINT32 = np.uint32


def vtarray_to_torch(vtarray, dtype=th.float32, device="cpu"):
    if device == "cpu":
        return th.from_numpy(np.array(vtarray)).to(dtype)
    else:
        assert device.startswith("cuda")
        return th.tensor(np.array(vtarray), dtype=dtype, device=device)
