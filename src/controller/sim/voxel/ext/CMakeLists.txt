cmake_minimum_required(VERSION 3.18)
project(voxel_mod CXX)

# ── Build configuration ───────────────────────────────────────────────────────
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# Optimise for release — the subtraction loop is hot
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

# ── Dependencies ──────────────────────────────────────────────────────────────

# nanobind: fetch if not installed system-wide.
# Run: pip install nanobind  (provides nanobind-config.cmake)
find_package(nanobind CONFIG REQUIRED)

# OpenVDB ≥ 10.0 (with TBB and Half/Imath)
# Ubuntu/Debian:  sudo apt install libopenvdb-dev libtbb-dev
# macOS:          brew install openvdb tbb
find_package(OpenVDB 10.0 REQUIRED COMPONENTS openvdb)

# ── Extension module ──────────────────────────────────────────────────────────
nanobind_add_module(voxel_mod
    STABLE_ABI   # build against the CPython stable ABI (Python 3.12+)
    NB_STATIC    # statically link nanobind runtime into the .so
    voxel_mod.cpp
)

target_link_libraries(voxel_mod PRIVATE
    OpenVDB::openvdb
)

target_compile_options(voxel_mod PRIVATE
    $<$<CXX_COMPILER_ID:GNU,Clang>:-O3 -march=native -fvisibility=hidden>
    $<$<CXX_COMPILER_ID:MSVC>:/O2>
)

# ── Install the .so next to the Python package ────────────────────────────────
# The parent CMakeLists.txt (or scikit-build-core) should set
# CMAKE_INSTALL_PREFIX to the site-packages directory so the extension lands
# alongside controller/sim/voxel/__init__.py.
install(TARGETS voxel_mod
    LIBRARY DESTINATION controller/sim/voxel
)

# ── Build instructions (printed at configure time) ───────────────────────────
message(STATUS "")
message(STATUS "=== voxel_mod build configuration ===")
message(STATUS "  Build type:   ${CMAKE_BUILD_TYPE}")
message(STATUS "  OpenVDB:      ${OpenVDB_VERSION}")
message(STATUS "  Python:       ${Python_VERSION}")
message(STATUS "")
message(STATUS "  To build and install into the current venv:")
message(STATUS "    cd src/controller/sim/voxel/ext")
message(STATUS "    cmake -B build -DCMAKE_BUILD_TYPE=Release .")
message(STATUS "    cmake --build build --parallel")
message(STATUS "    cmake --install build")
message(STATUS "  Or use scikit-build-core (see pyproject.toml) for a full wheel.")
message(STATUS "")
