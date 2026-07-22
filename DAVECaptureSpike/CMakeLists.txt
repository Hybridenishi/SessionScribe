cmake_minimum_required(VERSION 3.20)

project(SessionScribeDAVECaptureSpike LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(PkgConfig REQUIRED)
pkg_check_modules(DPP REQUIRED IMPORTED_TARGET dpp)

add_library(session_scribe_spike_core
    src/recorder.cpp
)

target_include_directories(session_scribe_spike_core
    PUBLIC
        ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_options(session_scribe_spike_core
    PRIVATE
        -Wall
        -Wextra
        -Wpedantic
        -Werror
)

add_executable(session-scribe-dave-spike
    src/main.cpp
)

target_link_libraries(session-scribe-dave-spike
    PRIVATE
        session_scribe_spike_core
        PkgConfig::DPP
)

target_compile_options(session-scribe-dave-spike
    PRIVATE
        -Wall
        -Wextra
        -Wpedantic
        -Werror
)

enable_testing()

add_executable(session-scribe-dave-spike-tests
    tests/recorder_tests.cpp
)

target_link_libraries(session-scribe-dave-spike-tests
    PRIVATE
        session_scribe_spike_core
)

target_compile_options(session-scribe-dave-spike-tests
    PRIVATE
        -Wall
        -Wextra
        -Wpedantic
        -Werror
)

add_test(
    NAME session-scribe-dave-spike-tests
    COMMAND session-scribe-dave-spike-tests
)
