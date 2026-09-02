cmake_minimum_required(VERSION 3.18)
project(cnc_controller CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# ── Python + nanobind ─────────────────────────────────────────────────────────
find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)
find_package(nanobind CONFIG REQUIRED)

# ── OpenVDB: optional — app runs without it (Python fallback) ─────────────────
find_package(OpenVDB QUIET COMPONENTS openvdb)

if(OpenVDB_FOUND)
    message(STATUS "OpenVDB ${OpenVDB_VERSION} found — building voxel_mod")
    add_subdirectory(src/controller/sim/voxel/ext)
else()
    message(WARNING
        "OpenVDB not found — voxel_mod will NOT be built.\n"
        "The application still runs; material-removal simulation is disabled.\n"
        "Install OpenVDB and re-run pip install -e . to enable it.\n"
        "  Windows: vcpkg install openvdb:x64-windows\n"
        "  macOS:   brew install openvdb\n"
        "  Linux:   sudo apt install libopenvdb-dev libtbb-dev"
    )
endif()
