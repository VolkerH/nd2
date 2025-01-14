from __future__ import annotations

import mmap
from enum import Enum
from itertools import product
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Sequence, Set, Union, cast, overload

import numpy as np

from ._util import AXIS, VoxelSize, dims_from_description, get_reader, is_supported_file
from .structures import Attributes, ExpLoop, Metadata, XYPosLoop

try:
    from functools import cached_property
except ImportError:
    cached_property = property  # type: ignore


if TYPE_CHECKING:
    from typing import Any, Dict, List, Tuple

    import dask.array as da
    import xarray as xr
    from typing_extensions import Literal


Index = Union[int, slice]


class ReadMode(str, Enum):
    MMAP = "mmap"
    SDK = "sdk"


class ND2File:
    _memmap: mmap.mmap
    _is_legacy: bool

    def __init__(self, path: Union[Path, str]) -> None:
        self._path = str(path)
        self._rdr = get_reader(self._path)
        self._closed = False
        self._is_legacy = "Legacy" in type(self._rdr).__name__

    @staticmethod
    def is_supported_file(path) -> bool:
        return is_supported_file(path)

    @property
    def path(self):
        """Path of the image."""
        return self._path

    @property
    def is_legacy(self) -> bool:
        """Whether file is a legacy nd2 (JPEG2000) file."""
        return self._is_legacy

    def open(self) -> None:
        """open file for reading."""
        if self.closed:
            self._rdr.open()
            self._closed = False

    def close(self) -> None:
        """Close file (may cause segfault if read when closed in some cases)."""
        if not self.closed:
            self._rdr.close()
            self._closed = True

    def __getstate__(self):
        state = self.__dict__.copy()
        del state["_rdr"]
        return state

    def __setstate__(self, d):
        self.__dict__ = d
        self._rdr = get_reader(self._path)
        if not self._closed:
            self.open()

    def __enter__(self) -> ND2File:
        self.open()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    @property
    def closed(self) -> bool:
        """Whether the file is closed."""
        return self._closed

    @cached_property
    def attributes(self) -> Attributes:
        """Core image attributes"""
        return self._rdr.attributes

    @cached_property
    def text_info(self) -> Dict[str, Any]:
        """Misc text info."""
        return self._rdr.text_info()

    @cached_property
    def experiment(self) -> List[ExpLoop]:
        """Loop information for each nd axis"""
        return self._rdr.experiment()

    @cached_property
    def metadata(self) -> Union[Metadata, dict]:
        """Various metadata (will be dict if legacy format)."""
        return self._rdr.metadata()

    @cached_property
    def custom_data(self) -> Dict[str, Any]:
        """Dict of various unstructured custom metadata."""
        return self._rdr._custom_data()

    @cached_property
    def ndim(self) -> int:
        """number of dimensions"""
        return len(self.shape)

    @cached_property
    def shape(self) -> Tuple[int, ...]:
        """size of each axis"""
        return self._coord_shape + self._frame_shape

    @cached_property
    def sizes(self) -> Dict[str, int]:
        """names and sizes for each axis"""
        attrs = cast(Attributes, self.attributes)

        # often, the 'Description' field in textinfo is the best source of dimension
        # (dims are strangely missing from coord_info sometimes)
        # so we start there, and fall back to coord_info if ddims are empty
        ddims = dims_from_description(self.text_info.get("description"))
        # dims from coord info
        cdims = {AXIS._MAP[c[1]]: c[2] for c in self._rdr._coord_info()}

        # prefer the value in coord info if it exists. (it's usually more accurate)
        dims = {k: cdims.get(k, v) for k, v in ddims.items()} if ddims else cdims
        dims[AXIS.CHANNEL] = (
            dims.pop(AXIS.CHANNEL)
            if AXIS.CHANNEL in dims
            else (attrs.channelCount or 1)
        )
        dims[AXIS.Y] = attrs.heightPx
        dims[AXIS.X] = attrs.widthPx or -1
        if self.components_per_channel == 3:  # rgb
            dims[AXIS.RGB] = self.components_per_channel
        else:
            # if not exactly 3 channels, throw them all into monochrome channels
            dims[AXIS.CHANNEL] = attrs.componentCount
        return {k: v for k, v in dims.items() if v != 1}

    @property
    def is_rgb(self) -> bool:
        """Whether the image is rgb"""
        return self.components_per_channel in (3, 4)

    @property
    def components_per_channel(self) -> int:
        """Number of components per channel (e.g. 3 for rgb)"""
        attrs = cast(Attributes, self.attributes)
        return attrs.componentCount // (attrs.channelCount or 1)

    @property
    def size(self) -> int:
        """Total number of pixels in the volume."""
        return int(np.prod(self.shape))

    @property
    def nbytes(self) -> int:
        """Total bytes of image data."""
        return self.size * self.dtype.itemsize

    @cached_property
    def dtype(self) -> np.dtype:
        """Image data type"""
        attrs = self.attributes
        d = attrs.pixelDataType[0] if attrs.pixelDataType else "u"
        return np.dtype(f"{d}{attrs.bitsPerComponentInMemory // 8}")

    def voxel_size(self, channel: int = 0) -> VoxelSize:
        """XYZ voxel size."""
        return VoxelSize(*self._rdr.voxel_size())

    def asarray(self, position: Optional[int] = None) -> np.ndarray:
        """Read image into numpy array.

        Parameters
        ----------
        position : int, optional
            A specific XY position to extract, by default (None) reads all.

        Returns
        -------
        np.ndarray

        Raises
        ------
        IndexError
            if position is provided and is out of range
        """
        final_shape = list(self.shape)
        if position is None:
            seqs: Sequence[int] = range(self._frame_count)
        else:
            if isinstance(position, str):
                try:
                    position = self._position_names().index(position)
                except ValueError:
                    raise ValueError(f"{position!r} is not a valid position name")
            try:
                pidx = list(self.sizes).index(AXIS.POSITION)
            except ValueError:
                if position > 0:
                    raise IndexError(
                        f"Position {position} is out of range. "
                        f"Only 1 position available"
                    )
                seqs = range(self._frame_count)
            else:
                if position >= self.sizes[AXIS.POSITION]:
                    raise IndexError(
                        f"Position {position} is out of range. "
                        f"Only {self.sizes[AXIS.POSITION]} positions available"
                    )

                ranges: List[Union[range, tuple]] = [
                    range(x) for x in self._coord_shape
                ]
                ranges[pidx] = (position,)
                coords = list(zip(*product(*ranges)))
                seqs = self._seq_index_from_coords(coords)  # type: ignore
                final_shape[pidx] = 1

        arr = np.stack([self._get_frame(i) for i in seqs])
        return arr.reshape(final_shape)

    def __array__(self) -> np.ndarray:
        """array protocol"""
        return self.asarray()

    def to_dask(self, copy=True) -> da.Array:
        """Create dask array (delayed reader) representing image.

        Parameters
        ----------
        copy : bool, optional
            Whether to copy buffer when reading, by default True.
            (False may be faster in some cases, but is also prone to segfaults if the
            file is closed.)

        Returns
        -------
        da.Array
        """
        from dask.array import map_blocks

        from ._dask_proxy import DaskArrayProxy

        chunks = [(1,) * x for x in self._coord_shape]
        chunks += [(x,) for x in self._frame_shape]
        dask_arr = map_blocks(self._dask_block, copy, chunks=chunks, dtype=self.dtype)
        # this proxy allows the dask array to re-open the underlying
        # nd2 file on compute.
        return DaskArrayProxy(dask_arr, self)

    _NO_IDX = -1

    def _seq_index_from_coords(self, coords: Sequence) -> Union[int, Sequence[int]]:
        if not self._coord_shape:
            return self._NO_IDX
        return np.ravel_multi_index(coords, self._coord_shape)

    def _dask_block(self, copy, block_id: Tuple[int]) -> np.ndarray:
        if isinstance(block_id, np.ndarray):
            return

        ncoords = len(self._coord_shape)
        idx = self._seq_index_from_coords(block_id[:ncoords])

        if idx == self._NO_IDX:
            if any(block_id):
                raise ValueError(f"Cannot get chunk {block_id} for single frame image.")
            idx = 0
        data = self._get_frame(cast(int, idx))[(np.newaxis,) * ncoords]
        return data.copy() if copy else data

    def to_xarray(
        self, delayed: bool = True, squeeze: bool = True, position: Optional[int] = None
    ) -> xr.DataArray:
        """Create labeled xarray representing image.

        Parameters
        ----------
        delayed : bool, optional
            Whether the DataArray should be backed by dask array or numpy array,
            by default True (dask).
        squeeze : bool, optional
            Whether to squeeze singleton dimensions, by default True
        position : int, optional
            A specific XY position to extract, by default (None) reads all.

        Returns
        -------
        xr.DataArray
            xarray with all axes labeled.
        """
        import xarray as xr

        data = self.to_dask() if delayed else self.asarray(position)
        dims = list(self.sizes)
        coords = self._expand_coords(squeeze)
        if not squeeze:
            for missing_dim in set(coords).difference(dims):
                dims.insert(0, missing_dim)
            missing_axes = len(dims) - data.ndim
            if missing_axes > 0:
                data = data[(np.newaxis,) * missing_axes]

        if position is not None and not delayed and AXIS.POSITION in coords:
            # if it's delayed, we do this using isel below instead.
            coords[AXIS.POSITION] = [coords[AXIS.POSITION][position]]

        x = xr.DataArray(
            data,
            dims=dims,
            coords=coords,
            attrs={
                "metadata": {
                    "metadata": self.metadata,
                    "experiment": self.experiment,
                    "attributes": self.attributes,
                    "text_info": self.text_info,
                }
            },
        )
        if delayed and position is not None and AXIS.POSITION in coords:
            x = x.isel({AXIS.POSITION: [position]})
        return x.squeeze() if squeeze else x

    @property
    def _frame_coords(self) -> Set[str]:
        return {AXIS.X, AXIS.Y, AXIS.CHANNEL, AXIS.RGB}

    @property
    def _raw_frame_shape(self) -> Tuple[int, int, int, int]:
        """sizes of each frame coordinate, prior to reshape"""
        attr = self.attributes
        return (
            attr.heightPx,
            attr.widthPx or -1,
            attr.channelCount or 1,
            self.components_per_channel,
        )

    @property
    def _frame_shape(self) -> Tuple[int, ...]:
        """sizes of each frame coordinate, after reshape & squeeze"""
        return tuple(v for k, v in self.sizes.items() if k in self._frame_coords)

    @cached_property
    def _coord_shape(self) -> Tuple[int, ...]:
        """sizes of each *non-frame* coordinate"""
        return tuple(v for k, v in self.sizes.items() if k not in self._frame_coords)

    @property
    def _frame_count(self) -> int:
        return int(np.prod(self._coord_shape))

    def _get_frame(self, index: int) -> np.ndarray:
        frame = self._rdr._read_image(index)
        frame.shape = self._raw_frame_shape
        return frame.transpose((2, 0, 1, 3)).squeeze()

    def _expand_coords(self, squeeze=True) -> dict:
        """Return a dict that can be used as the coords argument to xr.DataArray"""
        dx, dy, dz = self.voxel_size()

        coords = {
            AXIS.Y: np.arange(self.attributes.heightPx) * dy,
            AXIS.X: np.arange(self.attributes.widthPx) * dx,
            AXIS.CHANNEL: self._channel_names,
            AXIS.POSITION: ["XYPos:0"],  # maybe overwritten below
        }

        for c in self.experiment:
            if squeeze and getattr(c, "count") <= 1:
                continue
            if c.type == "ZStackLoop":
                coords[AXIS.Z] = np.arange(c.count) * c.parameters.stepUm
            elif c.type == "TimeLoop":
                coords[AXIS.TIME] = np.arange(c.count) * c.parameters.periodMs
            elif c.type == "NETimeLoop":
                pers = [np.arange(p.count) * p.periodMs for p in c.parameters.periods]
                coords[AXIS.TIME] = np.hstack(pers)
            elif c.type == "XYPosLoop":
                coords[AXIS._MAP["XYPosLoop"]] = self._position_names(c)

        if self.components_per_channel > 1:
            coords[AXIS.RGB] = ["Red", "Green", "Blue", "alpha"][
                : self.components_per_channel
            ]

        # fix for Z axis missing from experiment:
        if AXIS.Z in self.sizes and AXIS.Z not in coords:
            coords[AXIS.Z] = np.arange(self.sizes[AXIS.Z]) * dz

        if squeeze:
            return {k: v for k, v in coords.items() if len(v) > 1}
        return coords

    def _position_names(self, loop: Optional[XYPosLoop] = None) -> List[str]:
        if loop is None:
            for c in self.experiment:
                if c.type == "XYPosLoop":
                    loop = c
                    break
        if loop is None:
            return ["XYPos:0"]
        return [p.name or f"XYPos:{i}" for i, p in enumerate(loop.parameters.points)]

    @property
    def _channel_names(self) -> List[str]:
        return self._rdr.channel_names()

    def __repr__(self) -> str:
        try:
            details = " (closed)" if self.closed else f" {self.dtype}: {self.sizes!r}"
            extra = f": {Path(self.path).name!r}{details}"
        except Exception:
            extra = ""
        return f"<ND2File at {hex(id(self))}{extra}>"


@overload
def imread(
    file: str, dask: Literal[False] = False, xarray: Literal[False] = False
) -> np.ndarray:
    ...


@overload
def imread(file: str, dask: bool = ..., xarray: Literal[True] = True) -> xr.DataArray:
    ...


@overload
def imread(file: str, dask: Literal[True] = ..., xarray=False) -> da.Array:
    ...


def imread(file: str, dask: bool = False, xarray: bool = False):
    with ND2File(file) as nd2:
        if xarray:
            return nd2.to_xarray(delayed=dask)
        elif dask:
            return nd2.to_dask()
        else:
            return nd2.asarray()
