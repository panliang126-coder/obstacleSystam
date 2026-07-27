from pathlib import Path

import grpc_tools
import pytest
from grpc_tools import protoc


@pytest.mark.contract
def test_all_protobuf_contracts_compile(project_root: Path, tmp_path: Path) -> None:
    proto_root = project_root / "proto"
    google_include = Path(grpc_tools.__file__).resolve().parent / "_proto"
    proto_files = sorted((proto_root / "low_altitude" / "v1").glob("*.proto"))
    assert len(proto_files) == 9

    result = protoc.main(
        [
            "grpc_tools.protoc",
            f"-I{proto_root}",
            f"-I{google_include}",
            f"--python_out={tmp_path}",
            *(str(path) for path in proto_files),
        ]
    )

    assert result == 0
    assert len(list(tmp_path.rglob("*_pb2.py"))) == len(proto_files)
