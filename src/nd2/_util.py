import re
from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple, Union

if TYPE_CHECKING:
    from ._legacy import LegacyND2Reader
    from ._sdk.latest import ND2Reader


NEW_HEADER_MAGIC = b"\xda\xce\xbe\n"
OLD_HEADER_MAGIC = b"\x00\x00\x00\x0c"
VERSION = re.compile(r"^ND2 FILE SIGNATURE CHUNK NAME01!Ver([\d\.]+)$")


def is_supported_file(path):
    with open(path, "rb") as fh:
        return fh.read(4) in (NEW_HEADER_MAGIC, OLD_HEADER_MAGIC)


def get_reader(path: str) -> Union["ND2Reader", "LegacyND2Reader"]:
    with open(path, "rb") as fh:
        magic_num = fh.read(4)
        if magic_num == NEW_HEADER_MAGIC:
            from ._sdk.latest import ND2Reader

            return ND2Reader(path)
        elif magic_num == OLD_HEADER_MAGIC:
            from ._legacy import LegacyND2Reader

            return LegacyND2Reader(path)
        raise OSError(
            f"file {path} not recognized as ND2.  First 4 bytes: {magic_num!r}"
        )


def is_new_format(path: str) -> bool:
    # TODO: this is just for dealing with missing test data
    with open(path, "rb") as fh:
        return fh.read(4) == NEW_HEADER_MAGIC


def jdn_to_datetime_local(jdn):
    return datetime.fromtimestamp((jdn - 2440587.5) * 86400.0)


def jdn_to_datetime_utc(jdn):
    return datetime.utcfromtimestamp((jdn - 2440587.5) * 86400.0)


def rgb_int_to_tuple(rgb):
    return ((rgb & 255), (rgb >> 8 & 255), (rgb >> 16 & 255))


DIMSIZE = re.compile(r"(\w+)'?\((\d+)\)")


def dims_from_description(desc) -> dict:
    if not desc:
        return {}
    match = re.search(r"Dimensions:\s?([^\r]+)\r?\n", desc)
    if not match:
        return {}
    dims = match.groups()[0]
    dims = dims.replace("λ", AXIS.CHANNEL)
    dims = dims.replace("XY", AXIS.POSITION)
    return {k: int(v) for k, v in DIMSIZE.findall(dims)}


class AXIS:
    X = "X"
    Y = "Y"
    Z = "Z"
    CHANNEL = "C"
    RGB = "S"
    TIME = "T"
    POSITION = "P"
    UNKNOWN = "U"

    _MAP = {
        "Unknown": UNKNOWN,
        "TimeLoop": TIME,
        "XYPosLoop": POSITION,
        "ZStackLoop": Z,
        "NETimeLoop": TIME,
    }


class VoxelSize(NamedTuple):
    x: float
    y: float
    z: float
