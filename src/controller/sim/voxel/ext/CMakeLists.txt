# ── Extension module ─────────────────────────────────────────────────────────
# Called from the root CMakeLists.txt (via add_subdirectory) after the parent
# has already found Python, nanobind, and OpenVDB. Those find_package() calls
# must NOT be repeated here — variables are inherited from the parent scope.
#
# Standalone build (without scikit-build-core):
#   cd src/controller/sim/voxel/ext
#   cmake -B build -DCMAKE_BUILD_TYPE=Release .
#   cmake --build build --config Release
#
# When invoked standalone, re-run the required find_package() calls:
if(NOT DEFINED SKBUILD AND NOT TARGET nanobind-headers)
    cmake_minimum_required(VERSION 3.18)
    project(voxel_mod CXX)
    set(CMAKE_CXX_STANDARD 17)
    set(CMAKE_CXX_STANDARD_REQUIRED ON)
    set(CMAKE_POSITION_INDEPENDENT_CODE ON)
    find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)
    find_package(nanobind CONFIG REQUIRED)
    find_package(OpenVDB 10.0 REQUIRED COMPONENTS openvdb)
    if(NOT CMAKE_BUILD_TYPE)
        set(CMAKE_BUILD_TYPE Release)
    endif()
endif()

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

# ── Install the .so/.pyd next to the Python package ──────────────────────────
# scikit-build-core sets CMAKE_INSTALL_PREFIX to the wheel staging area.
# The DESTINATION is relative to that prefix, matching the importable path:
#   controller/sim/voxel/voxel_mod.cpython-311-...so
install(TARGETS voxel_mod
    LIBRARY  DESTINATION controller/sim/voxel   # Linux/macOS .so
    RUNTIME  DESTINATION controller/sim/voxel   # Windows     .pyd / .dll
)

# ── Build instructions (printed at configure time) ───────────────────────────
message(STATUS "")
message(STATUS "=== voxel_mod build configuration ===")
message(STATUS "  Build type:   ${CMAKE_BUILD_TYPE}")
message(STATUS "  OpenVDB:      ${OpenVDB_VERSION}")
message(STATUS "  Python:       ${Python_VERSION}")
message(STATUS "")
message(STATUS "  PowerShell (Windows):")
message(STATUS "    cd src\\controller\\sim\\voxel\\ext")
message(STATUS "    cmake -B build -DCMAKE_BUILD_TYPE=Release .")
message(STATUS "    cmake --build build --config Release")
message(STATUS "    cmake --install build")
message(STATUS "")
message(STATUS "  Bash (Linux/macOS):")
message(STATUS "    cd src/controller/sim/voxel/ext")
message(STATUS "    cmake -B build -DCMAKE_BUILD_TYPE=Release . && cmake --build build --parallel")
message(STATUS "")
message(STATUS "")
