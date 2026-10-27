# ============================================================
# ISA(instruction set) 활성화 플래그
# ============================================================
# 전제: 실행할 Jetson에서 직접 빌드한다(네이티브 빌드).
#   -mcpu=native 로 빌드 머신 CPU(Orin: Cortex-A78AE)의 명령어셋/튜닝을 켭니다.
#
# PUBLIC 전이를 하지 않습니다: SDK는 설치·export 되므로
# -mcpu=native 가 사용자 빌드로 새어나가면 안 됩니다. 라이브러리/테스트 빌드에만
# 걸리도록 nrv_dvs_build_options(INTERFACE, 비설치)에 붙입니다.
#
# 선행 조건: architecture.cmake, compiler_options.cmake 이후 include.

option(NRV_DVS_NATIVE_ISA "빌드 머신 CPU의 명령어셋 활성화(-mcpu=native)" ON)

set(NRV_DVS_ISA_FLAGS "")
if(NRV_DVS_NATIVE_ISA)
    set(NRV_DVS_ISA_FLAGS "-mcpu=native")
endif()

if(NRV_DVS_ISA_FLAGS)
    target_compile_options(nrv_dvs_build_options INTERFACE ${NRV_DVS_ISA_FLAGS})
endif()

message(STATUS "nrv_dvs: ISA flags='${NRV_DVS_ISA_FLAGS}'")
