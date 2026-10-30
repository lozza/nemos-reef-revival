# Native Linux x86_64 build of the private JIT runtime and the compiled bridge.
#   cmake -S desktop/linux -B desktop/linux/build -DDYNARMIC_SOURCE_DIR=<pinned dynarmic checkout>
#   cmake --build desktop/linux/build -j && cmake --install desktop/linux/build --prefix <dir>
# Produces libjit_core.so, libjit_dispatch_v7.so and libnemo_bridge.so.
# Windows sources/DLLs under work/jit-runtime and native/ are untouched.
cmake_minimum_required(VERSION 3.20)
project(nemo_linux_native LANGUAGES C CXX)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_C_STANDARD 11)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_C_VISIBILITY_PRESET hidden)
set(CMAKE_CXX_VISIBILITY_PRESET hidden)
set(CMAKE_VISIBILITY_INLINES_HIDDEN ON)

set(DYNARMIC_SOURCE_DIR "" CACHE PATH "Pinned dynarmic checkout")
set(NEMO_LINUX_INSTALL_DIR "lib" CACHE STRING "Install directory for the .so files")
option(NEMO_STATIC_LIBSTDCXX "Link libstdc++/libgcc statically into libjit_core.so" OFF)
if(NOT EXISTS "${DYNARMIC_SOURCE_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "Pass -DDYNARMIC_SOURCE_DIR=<pinned dynarmic checkout>")
endif()

# Dynarmic: A32 only, bundled externals (Boost headers still come from the system), no tests.
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
set(DYNARMIC_FRONTENDS "A32" CACHE STRING "" FORCE)
set(DYNARMIC_TESTS OFF CACHE BOOL "" FORCE)
set(DYNARMIC_WARNINGS_AS_ERRORS OFF CACHE BOOL "" FORCE)
set(DYNARMIC_USE_PRECOMPILED_HEADERS OFF CACHE BOOL "" FORCE)
set(DYNARMIC_USE_BUNDLED_EXTERNALS ON CACHE BOOL "" FORCE)
add_subdirectory(${DYNARMIC_SOURCE_DIR} dynarmic EXCLUDE_FROM_ALL)

find_package(Threads REQUIRED)
set(NEMO_NATIVE ${CMAKE_CURRENT_SOURCE_DIR}/../native)
set(NEMO_LINK_FLAGS -Wl,--no-undefined -Wl,-z,noexecstack)

add_library(jit_core SHARED jit_core.cpp)
target_include_directories(jit_core PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(jit_core PRIVATE dynarmic Threads::Threads)
# Keep Dynarmic/fmt/zydis symbols private; only dp_* (DP_API) are exported.
target_link_options(jit_core PRIVATE ${NEMO_LINK_FLAGS} -Wl,--exclude-libs,ALL)
if(NEMO_STATIC_LIBSTDCXX)
    target_link_options(jit_core PRIVATE -static-libstdc++ -static-libgcc)
endif()

add_library(jit_dispatch_v7 SHARED jit_dispatch.c)
target_include_directories(jit_dispatch_v7 PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} ${NEMO_NATIVE})
target_link_libraries(jit_dispatch_v7 PRIVATE jit_core)
target_link_options(jit_dispatch_v7 PRIVATE ${NEMO_LINK_FLAGS})
# Resolve libjit_core.so from the same directory, like the DLL import on Windows.
set_target_properties(jit_dispatch_v7 PROPERTIES BUILD_RPATH "$ORIGIN" INSTALL_RPATH "$ORIGIN")

add_library(nemo_bridge SHARED
    ${NEMO_NATIVE}/native_bridge.c ${NEMO_NATIVE}/native_memory.c
    ${NEMO_NATIVE}/native_math.c ${NEMO_NATIVE}/native_draw.c)
target_include_directories(nemo_bridge PRIVATE ${NEMO_NATIVE})
target_link_libraries(nemo_bridge PRIVATE m)
target_link_options(nemo_bridge PRIVATE ${NEMO_LINK_FLAGS})

install(TARGETS jit_core jit_dispatch_v7 nemo_bridge
        LIBRARY DESTINATION ${NEMO_LINUX_INSTALL_DIR}
        RUNTIME DESTINATION ${NEMO_LINUX_INSTALL_DIR})
