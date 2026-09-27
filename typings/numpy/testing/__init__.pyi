from numpy import ndarray

def assert_allclose(
    actual: ndarray | float,
    desired: ndarray | float,
    rtol: float = ...,
    atol: float = ...,
    equal_nan: bool = ...,
) -> None: ...
