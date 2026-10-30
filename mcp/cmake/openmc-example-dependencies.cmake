# OpenMC 0.16's exported target names HDF5 targets but does not load their
# package itself. Load the real SDK dependency without changing tutorial source
# or fabricating linker names. CMAKE_PREFIX_PATH selects the configured SDK.
find_package(HDF5 CONFIG REQUIRED)
