cmake_minimum_required(VERSION 3.25)
# ============================================================
# nrv_dvs SDK: nrv_dvs V4L2 드라이버용 user-space C++ 라이브러리 (Jetson)
# ============================================================
#   cmake --preset GCC-arm64                          # configure (Ninja Multi-Config)
#   cmake --build --preset RelWithDebInfo-GCC-arm64   # 또는 Debug- / DebugLeakTest- / Release-
#   ctest --preset RelWithDebInfo-GCC-arm64
#
# 소스는 자동 수집됩니다: src/ 아래 .cpp, include/nrv_dvs/ 아래 공개 헤더를
# 추가하고 다시 빌드하면 됩니다(CONFIGURE_DEPENDS).
#
# 지원 플랫폼: Linux aarch64 (Jetson 네이티브 빌드), GCC.
# ============================================================

# ============================================================
# Policy
# ============================================================
# CMP0135: FetchContent/ExternalProject URL 다운로드 시 추출 파일의 타임스탬프를
#          다운로드 시각으로 설정(재빌드 판단이 안정적). 외부 의존성 추가 대비.
if(POLICY CMP0135)
    cmake_policy(SET CMP0135 NEW)
endif()

# ============================================================
# Project
# ============================================================
project(nrv_dvs VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD             20)
set(CMAKE_CXX_STANDARD_REQUIRED    ON)
set(CMAKE_CXX_EXTENSIONS          OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS  ON)
# NRV_DVS_API(생성되는 nrv_dvs/export.h)로 표시한 심볼만 .so 밖으로 나갑니다.
set(CMAKE_CXX_VISIBILITY_PRESET    hidden)
set(CMAKE_VISIBILITY_INLINES_HIDDEN ON)

# 프리셋 없이 단일 구성 생성기로 configure한 경우의 기본값
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE RelWithDebInfo CACHE STRING "Build type" FORCE)
endif()

option(NRV_DVS_BUILD_TESTS "SDK 테스트 빌드" ${PROJECT_IS_TOP_LEVEL})
# 드라이버/센서 없는 하드웨어에서 Stream·Pipe 흐름을 시험하도록 V4L2 장치를 흉내 냄
#   cmake --preset GCC-arm64 -DNRV_DVS_DUMMY_DEVICE=ON
option(NRV_DVS_DUMMY_DEVICE "더미 장치로 빌드 (테스트용)" OFF)

include(GNUInstallDirs)
include(GenerateExportHeader)

# Compressor 가 쓰는 zstd (libzstd-dev). 공유 라이브러리에만 링크되므로 SDK 사용자는
# 따로 찾을 필요 없음
find_package(zstd CONFIG REQUIRED)

# ============================================================
# Platform / architecture guard (Linux aarch64 전용)
# ============================================================
include("${CMAKE_CURRENT_SOURCE_DIR}/.cmake/architecture.cmake")

# ============================================================
# Compiler options (경고/새니타이저/ISA → nrv_dvs_build_options)
# ============================================================
include("${CMAKE_CURRENT_SOURCE_DIR}/.cmake/compiler_options.cmake")
include("${CMAKE_CURRENT_SOURCE_DIR}/.cmake/isa_features.cmake")

# ============================================================
# Output layout (Debug·DebugLeakTest: 개발용 / RelWithDebInfo·Release: 배포용)
# ============================================================
include("${CMAKE_CURRENT_SOURCE_DIR}/.cmake/output_layout.cmake")

# ============================================================
# Target – nrv_dvs
# ============================================================
file(GLOB_RECURSE NRV_DVS_SOURCES CONFIGURE_DEPENDS
     "${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp")
file(GLOB_RECURSE NRV_DVS_PUBLIC_HEADERS CONFIGURE_DEPENDS
     "${CMAKE_CURRENT_SOURCE_DIR}/include/nrv_dvs/*.h")

# 생성 헤더(export.h)는 <빌드트리>/include/nrv_dvs/ 에 둡니다.
set(NRV_DVS_GENERATED_INCLUDE_DIR "${CMAKE_CURRENT_BINARY_DIR}/include")

# ------------------------------------------------------------
# nrv_dvs_objects (OBJECT): 실제 컴파일 단위
# ------------------------------------------------------------
# 공유 라이브러리와 내부(white-box) 테스트가 같은 오브젝트를 씁니다.
# .so 는 NRV_DVS_API 심볼만 내보내므로(visibility hidden), 내부 테스트는 .so 대신
# 이 오브젝트를 실행 파일에 직접 링크해 숨겨진 내부 함수까지 호출합니다.
add_library(nrv_dvs_objects OBJECT ${NRV_DVS_SOURCES} ${NRV_DVS_PUBLIC_HEADERS})
set_target_properties(nrv_dvs_objects PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(nrv_dvs_objects
    PUBLIC
        "${CMAKE_CURRENT_SOURCE_DIR}/include"
        "${NRV_DVS_GENERATED_INCLUDE_DIR}"
        "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_link_libraries(nrv_dvs_objects PRIVATE nrv_dvs_build_options zstd::libzstd_shared)
# 미리 컴파일된 헤더: include/pch.h 의 표준 헤더를 모든 .cpp 에 강제 include.
# include/nrv_dvs/(공개 헤더) 밖이라 설치되지 않습니다. 공개 헤더는 사용자가 PCH 없이 include 하므로
# 필요한 헤더를 각자 직접 include 해야 합니다.
target_precompile_headers(nrv_dvs_objects PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/include/pch.h")
target_compile_definitions(nrv_dvs_objects PRIVATE
    # export.h: "이 라이브러리를 빌드 중" 분기(OBJECT 타겟엔 자동으로 안 붙음)
    nrv_dvs_EXPORTS
    # presets/ 설치 위치(기본 탐색 경로; 런타임에 덮어쓸 수 있음)
    NRV_DVS_PRESET_DIR="${CMAKE_INSTALL_FULL_DATADIR}/nrv_dvs/settings")
# 더미 장치: src/ 비공개 헤더(device.h)의 모양도 바뀌므로 그 헤더를 쓰는 내부
# 테스트까지 같은 정의로 빌드 (PUBLIC). 공개 헤더와 .so 사용자에게는 영향 없음.
target_compile_definitions(nrv_dvs_objects PUBLIC
    $<$<BOOL:${NRV_DVS_DUMMY_DEVICE}>:NRV_DVS_DUMMY_DEVICE>)

# ------------------------------------------------------------
# nrv_dvs (SHARED): 배포되는 라이브러리
# ------------------------------------------------------------
add_library(${PROJECT_NAME} SHARED $<TARGET_OBJECTS:nrv_dvs_objects>)
add_library(NrvDvs::${PROJECT_NAME} ALIAS ${PROJECT_NAME})

generate_export_header(${PROJECT_NAME}
    BASE_NAME NRV_DVS
    EXPORT_MACRO_NAME NRV_DVS_API
    EXPORT_FILE_NAME "${NRV_DVS_GENERATED_INCLUDE_DIR}/nrv_dvs/export.h")

set_target_properties(${PROJECT_NAME} PROPERTIES
    VERSION   ${PROJECT_VERSION}
    SOVERSION ${PROJECT_VERSION_MAJOR})
target_include_directories(${PROJECT_NAME}
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<BUILD_INTERFACE:${NRV_DVS_GENERATED_INCLUDE_DIR}>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>)
target_link_libraries(${PROJECT_NAME} PRIVATE $<BUILD_INTERFACE:nrv_dvs_build_options>
                                               zstd::libzstd_shared)

include("${CMAKE_CURRENT_SOURCE_DIR}/.cmake/install.cmake")
include("${CMAKE_CURRENT_SOURCE_DIR}/.cmake/dist.cmake")

# ============================================================
# Tests
# ============================================================
if(NRV_DVS_BUILD_TESTS)
    include(CTest)
    add_subdirectory(tests)
endif()

# ============================================================
# compile_commands.json copy (clangd/IDE intellisense용)
# ============================================================
# output/<preset>/compile_commands.json → output/compile_commands.json
# (.clangd 의 CompilationDatabase: output 이 이 파일을 읽습니다.)
#   (1) configure 시점: 이전 configure가 남긴 파일이 있으면 즉시 복사
#       (첫 configure에는 원본이 없어 조용히 실패 — 정상)
#   (2) 빌드 시점(ALL 타겟): 항상 최신 보장
if(PROJECT_IS_TOP_LEVEL AND CMAKE_EXPORT_COMPILE_COMMANDS)
    execute_process(
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                ${CMAKE_BINARY_DIR}/compile_commands.json
                ${CMAKE_BINARY_DIR}/../compile_commands.json
        OUTPUT_QUIET
        ERROR_QUIET
    )
    add_custom_target(copy_compile_commands ALL
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                ${CMAKE_BINARY_DIR}/compile_commands.json
                ${CMAKE_BINARY_DIR}/../compile_commands.json
        COMMENT "Copying compile_commands.json to output/"
        VERBATIM
    )
endif()
