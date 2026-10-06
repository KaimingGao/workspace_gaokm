"""子进程启动时限制 BLAS 线程。本模块不 import numpy。"""

import os


def limit_blas_threads() -> None:
    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        os.environ[key] = "1"
