# ============================================================
# Compiler Options
# ============================================================
# 출력:
#   nrv_dvs_build_options (INTERFACE 타겟)
#     라이브러리와 테스트가 PRIVATE(BUILD_INTERFACE)로 링크합니다.
#     설치/export 대상이 아니므로 SDK 사용자 코드에는 전이되지 않습니다.
#
# 옵션:
#   NRV_DVS_WARNINGS_AS_ERRORS  (ON)   경고를 에러로(-Werror)
#
# 구성:
#   Debug          -O0 -g, 새니타이저 없음 (gdb/VS Code cppdbg 용)
#   DebugLeakTest  -O1 -g + ASan/UBSan/LSan (메모리 오류·누수 검사용). -O0 이면 ASan 이
#                  함수 호출마다 비싸서(예: subspan + std::copy 조각 복사가 -O1 의 70배)
#                  수신을 따라가지 못하므로 -O1.
# ============================================================

if(NOT CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    message(FATAL_ERROR
        "nrv_dvs: 현재 GCC만 지원합니다 (감지: ${CMAKE_CXX_COMPILER_ID}).")
endif()

option(NRV_DVS_WARNINGS_AS_ERRORS "경고를 에러로 취급" ON)

if(NOT TARGET nrv_dvs_build_options)
    add_library(nrv_dvs_build_options INTERFACE)
endif()

# ============================================================
# Warnings
# ============================================================
target_compile_options(nrv_dvs_build_options INTERFACE
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
    $<$<BOOL:${NRV_DVS_WARNINGS_AS_ERRORS}>:-Werror>
)

# ============================================================
# DebugLeakTest: ASan/UBSan/LSan
# ============================================================
set(CMAKE_CXX_FLAGS_DEBUGLEAKTEST "-O1 -g")
set(_nrv_dvs_san_flags -fsanitize=address,undefined -fno-omit-frame-pointer)
target_compile_options(nrv_dvs_build_options INTERFACE
    "$<$<CONFIG:DebugLeakTest>:${_nrv_dvs_san_flags}>")
target_link_options(nrv_dvs_build_options INTERFACE
    "$<$<CONFIG:DebugLeakTest>:${_nrv_dvs_san_flags}>")
unset(_nrv_dvs_san_flags)

# ============================================================
# Release: 배포용 — 실행에 필요 없는 정보 제거
# ============================================================
# 공개 API 심볼(.dynsym)과 예외 처리용 .eh_frame 등 실행/링크에 필요한 것만 남깁니다.
#   -s                  링크 시 심볼 테이블·디버그 정보 제거(strip-all)
#   -Wl,--build-id=none 빌드 식별자(.note.gnu.build-id) 생성 안 함
#   -fno-ident          컴파일러 버전 문자열(.comment "GCC: ...") 생성 안 함
#   -ffile-prefix-map   __FILE__ 등에 들어가는 빌드 머신 절대 경로를 상대 경로로
target_compile_options(nrv_dvs_build_options INTERFACE
    "$<$<CONFIG:Release>:-fno-ident;-ffile-prefix-map=${CMAKE_SOURCE_DIR}/=>")
target_link_options(nrv_dvs_build_options INTERFACE
    "$<$<CONFIG:Release>:-s;-Wl,--build-id=none>")

message(STATUS
    "nrv_dvs: GCC ${CMAKE_CXX_COMPILER_VERSION}, Werror=${NRV_DVS_WARNINGS_AS_ERRORS}")
