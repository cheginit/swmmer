cmake_minimum_required(VERSION 3.23)
project(swmmer_engine LANGUAGES C CXX)

# Vendored EPA SWMM engine source (git submodule, pinned rev). It builds the
# runswmm CLI, the swmm5 solver lib, and the swmm-output lib that swmmer
# ctypes-loads. scikit-build-core relocates the install under swmmer/_engine
# (see [tool.scikit-build] wheel.install-dir in pyproject.toml).
set(SWMM_DIR ${CMAKE_CURRENT_SOURCE_DIR}/extern/Stormwater-Management-Model)

if(NOT EXISTS ${SWMM_DIR}/CMakeLists.txt)
  message(FATAL_ERROR
    "EPA SWMM source missing at ${SWMM_DIR}. "
    "Run: git submodule update --init --recursive")
endif()

# macOS OpenMP handling: transiently patch the submodule's cmake/openmp.cmake to
# find_package (linking the conda llvm-openmp) for local/pixi dev, then restore it
# after add_subdirectory so the unmaintained submodule stays clean. See the module
# header for the full rationale (conda-toolchain FetchContent failure, cibuildwheel
# from-source build, and the concurrent-configure caveat).
include(${CMAKE_CURRENT_SOURCE_DIR}/cmake/swmm_openmp.cmake)
swmm_engine_openmp_pre(${SWMM_DIR})

# Windows: SWMM's install() calls InstallRequiredSystemLibraries, which vendors
# the MSVC runtime DLLs next to the engine. Also include the OpenMP runtime
# (vcomp140.dll), which swmm5.dll links via /openmp, so the wheel runs on a clean
# machine without the VC++ redistributable. (Set before add_subdirectory so the
# child scope's InstallRequiredSystemLibraries picks it up.)
if(WIN32)
  set(CMAKE_INSTALL_OPENMP_LIBRARIES ON)
endif()

# Build the engine. SWMM's own install() rules place runswmm in bin/ and the
# shared libs in lib/; wheel.install-dir prepends swmmer/_engine/, giving the
# bin/ + ../lib/ layout that runswmm's RPATH (@loader_path/../lib, $ORIGIN/../lib)
# and swmmer.run.find_output_lib already expect.
add_subdirectory(${SWMM_DIR} ${CMAKE_BINARY_DIR}/swmm-build)

# Restore the transient local-dev openmp.cmake patch now that add_subdirectory has
# consumed it (find_package is an imported target -- build never re-reads it).
swmm_engine_openmp_post(${SWMM_DIR})
