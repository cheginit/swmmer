# macOS OpenMP handling for the vendored EPA SWMM engine submodule.
#
# The submodule's src/solver/CMakeLists.txt hardcodes `if(APPLE)
# include(../../cmake/openmp.cmake)`, and upstream that file FetchContent-builds
# LLVM libomp from source. That build fails under the conda/pixi toolchain (its
# injected -D_FORTIFY_SOURCE=2 -isystem flags break libomp's LLVM sources).
#
# For local/pixi dev we transiently patch that submodule file to a plain
# find_package(OpenMP) (links the conda-forge llvm-openmp already in the env),
# let add_subdirectory consume it at configure time -- which yields an *imported*
# target, so nothing re-reads the file at build time -- then restore it pristine,
# leaving the unmaintained submodule with a clean working tree.
#
# Under cibuildwheel we instead keep the pristine upstream file so the from-source
# build runs (via the Xcode generator; it targets CMAKE_OSX_DEPLOYMENT_TARGET so
# the bundled libomp does not pin the wheel to the runner's macOS version the way
# brew's libomp would). Linux/Windows use upstream find_package(OpenMP) directly.
#
# The write target cannot be relocated: the include path above is hardcoded
# relative to the submodule, so it must resolve to the submodule's own
# cmake/openmp.cmake -- hence patch-then-restore rather than a file elsewhere.
#
# This mutates a tracked submodule file in place during configure. To keep two
# concurrent macOS configures against the *same* working tree (e.g. parallel
# `pixi run -e test312`/`-e test314` editable builds) from clobbering each other's
# patch, the whole patch -> add_subdirectory -> restore window is serialized by a
# process-held file lock (acquired in _pre, released in _post; an aborted configure
# releases it when the cmake process exits). CI is unaffected (each runner has its
# own checkout, and cibuildwheel does not take the lock).

# Restore the pristine submodule cmake/openmp.cmake from the git index. Quiet on
# a non-git tree (an sdist has no .git gitlink), but warn on a genuine restore
# failure inside a real git tree so it is not silently swallowed (which would
# leave the submodule dirty -- the very thing this exists to avoid).
function(_swmm_openmp_restore dir)
  execute_process(
    COMMAND git -C ${dir} checkout -- cmake/openmp.cmake
    RESULT_VARIABLE _rc
    ERROR_QUIET)
  if(NOT _rc EQUAL 0 AND EXISTS ${dir}/.git)
    message(WARNING
      "Failed to restore pristine ${dir}/cmake/openmp.cmake (git rc=${_rc}); "
      "the SWMM submodule working tree may be left modified.")
  endif()
endfunction()

# Shared lock path (in the source root, gitignored) -- the same for every build
# tree configured from this checkout, so concurrent configures contend on it.
# CMAKE_SOURCE_DIR is a global, so it resolves the same inside _pre and _post.
set(_SWMM_OPENMP_LOCK "${CMAKE_SOURCE_DIR}/.swmm-openmp.lock")

# Call BEFORE add_subdirectory(<swmm>): patch the submodule openmp.cmake for
# local/pixi dev, or keep it pristine under cibuildwheel.
function(swmm_engine_openmp_pre dir)
  if(NOT APPLE)
    return()
  endif()
  if(DEFINED ENV{CIBUILDWHEEL})
    # Isolated per-runner build: no concurrency, so no lock -- just keep pristine
    # even if a prior local/pixi configure patched this tree.
    _swmm_openmp_restore(${dir})
    return()
  endif()
  # Serialize the patch -> add_subdirectory -> restore window (released in _post).
  # Fail loudly rather than patch without the lock: proceeding unserialized would
  # reintroduce the exact race this guards against (a concurrent configure could
  # read the wrong openmp.cmake). The 300s cap only trips if another configure of
  # this tree is genuinely stuck.
  file(LOCK ${_SWMM_OPENMP_LOCK} GUARD PROCESS TIMEOUT 300 RESULT_VARIABLE _lrc)
  if(_lrc AND NOT _lrc STREQUAL "0")
    message(FATAL_ERROR
      "swmm openmp: could not acquire the configure lock within 300s (${_lrc}). "
      "Another macOS configure of this tree may be running -- retry once it finishes.")
  endif()
  file(WRITE ${dir}/cmake/openmp.cmake "find_package(OpenMP REQUIRED C)\n")
endfunction()

# Call AFTER add_subdirectory(<swmm>): restore the transient local-dev patch so
# the submodule stays clean, then release the lock. No-op under cibuildwheel.
function(swmm_engine_openmp_post dir)
  if(NOT (APPLE AND NOT DEFINED ENV{CIBUILDWHEEL}))
    return()
  endif()
  _swmm_openmp_restore(${dir})
  file(LOCK ${_SWMM_OPENMP_LOCK} RELEASE RESULT_VARIABLE _rrc)
endfunction()
