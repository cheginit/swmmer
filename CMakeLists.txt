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

# macOS only: upstream cmake/openmp.cmake FetchContent-builds LLVM OpenMP from
# the network during the build. Replace it with find_package so we link the
# toolchain/system libomp instead (conda-forge llvm-openmp for local dev, brew
# libomp under cibuildwheel) — no build-time network, toolchain-consistent
# runtime. Mirrors the upstream conda recipe's build patch. Linux/Windows
# already use find_package(OpenMP), so this is macOS-only.
if(APPLE)
  file(WRITE ${SWMM_DIR}/cmake/openmp.cmake "find_package(OpenMP REQUIRED C)\n")
endif()

# Build the engine. SWMM's own install() rules place runswmm in bin/ and the
# shared libs in lib/; wheel.install-dir prepends swmmer/_engine/, giving the
# bin/ + ../lib/ layout that runswmm's RPATH (@loader_path/../lib, $ORIGIN/../lib)
# and swmmer.run.find_output_lib already expect.
add_subdirectory(${SWMM_DIR} ${CMAKE_BINARY_DIR}/swmm-build)
