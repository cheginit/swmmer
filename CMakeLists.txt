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
# source (for the Xcode generator). For local/pixi dev we replace it with
# find_package so we link the conda-forge llvm-openmp already in the env, which
# is faster and needs no build-time network. Under cibuildwheel we keep the
# upstream from-source build (via the Xcode generator, see the darwin
# scikit-build override): it targets CMAKE_OSX_DEPLOYMENT_TARGET, so the bundled
# libomp does not pin the wheel to the runner's macOS version the way brew's
# libomp would. Linux/Windows already use find_package(OpenMP).
if(APPLE AND NOT DEFINED ENV{CIBUILDWHEEL})
  file(WRITE ${SWMM_DIR}/cmake/openmp.cmake "find_package(OpenMP REQUIRED C)\n")
endif()

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
