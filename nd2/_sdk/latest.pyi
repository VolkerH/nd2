from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple, Union

import numpy as np

from .. import structures

class ND2Reader:
    path: str
    def __init__(self, path: Union[str, Path]) -> None: ...
    def open(self) -> None: ...
    def close(self) -> None: ...
    def __enter__(self) -> ND2Reader: ...
    def __exit__(self, *args) -> None: ...
    def _attributes(self) -> dict: ...
    @property
    def attributes(self) -> structures.Attributes: ...
    def _metadata(self) -> dict: ...
    def metadata(self) -> structures.Metadata: ...
    def _frame_metadata(self, seq_index: int) -> dict: ...
    def text_info(self) -> Dict[str, Any]: ...
    def _description(self) -> str: ...
    def _experiment(self) -> list: ...
    def experiment(self) -> List[structures.ExpLoop]: ...
    def sizes(self) -> dict: ...
    def _seq_count(self) -> int: ...
    def _coord_size(self) -> int: ...
    def _seq_index_from_coords(self, coords: Sequence) -> int: ...
    def _coords_from_seq_index(self, seq_index: int) -> Tuple[int, ...]: ...
    def _seq_index_from_pycoords(self, coords: Sequence) -> int: ...
    def _pycoords_from_seq_index(self, seq_index: int) -> Tuple[int, ...]: ...
    def _coord_info(self) -> List[Tuple[int, str, int]]: ...
    def _image(self, seq_index: int) -> np.ndarray: ...
    def voxel_size(self) -> Tuple[float, float, float]: ...
    def _custom_data(self) -> Dict[str, Any]: ...
    def _read_image(self, index: int) -> np.ndarray: ...
    def channel_names(self) -> List[str]: ...
