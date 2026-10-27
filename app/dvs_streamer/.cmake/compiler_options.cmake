# ============================================================
# Compiler Options (SDK 와 같은 경고·구성)
# ============================================================
# 출력:
#   dvs_streamer_build_options (INTERFACE 타겟)
#
# 구성:
#   Debug           -O0 -g, 새니타이저 없음 (gdb/VS Code cppdbg 용)
#   DebugLeakTest   -O1 -g + ASan/UBSan/LSan
#   RelWithDebInfo  -O2 -g
#   Release         -O3, 심볼·빌드 식별 정보 제거
# ============================================================

if(NOT CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    message(FATAL_ERROR
        "dvs_streamer: 현재 GCC만 지원합니다 (감지: ${CMAKE_CXX_COMPILER_ID}).")
endif()

add_library(dvs_streamer_build_options INTERFACE)

target_compile_options(dvs_streamer_build_options INTERFACE
    -Wall
    -Wextra
    -Wpedantic
    -Wconversion
    -Wsign-conversion
    -Wshadow
    -Wundef
    -Wunreachable-code
    -Wstrict-aliasing
    -Wnull-dereference
    -Wdouble-promotion
    -Wformat=2
    -Wcast-qual
    -Wcast-align
    -Wuseless-cast
    -Werror
    # 네이티브 빌드: 빌드 머신 CPU(Orin: Cortex-A78AE)의 명령어셋/튜닝
    -mcpu=native
)

# DebugLeakTest: ASan/UBSan/LSan
set(CMAKE_CXX_FLAGS_DEBUGLEAKTEST "-O1 -g")
set(_dvs_streamer_san_flags -fsanitize=address,undefined -fno-omit-frame-pointer)
target_compile_options(dvs_streamer_build_options INTERFACE
    "$<$<CONFIG:DebugLeakTest>:${_dvs_streamer_san_flags}>")
target_link_options(dvs_streamer_build_options INTERFACE
    "$<$<CONFIG:DebugLeakTest>:${_dvs_streamer_san_flags}>")
unset(_dvs_streamer_san_flags)

# Release: 실행에 필요 없는 정보 제거 (SDK 의 compiler_options.cmake 와 같음)
target_compile_options(dvs_streamer_build_options INTERFACE
    "$<$<CONFIG:Release>:-fno-ident;-ffile-prefix-map=${CMAKE_SOURCE_DIR}/=>")
target_link_options(dvs_streamer_build_options INTERFACE
    "$<$<CONFIG:Release>:-s;-Wl,--build-id=none>")
