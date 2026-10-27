# ============================================================
# Install / package export (find_package(NrvDvs) 지원)
# ============================================================
# 입력:
#   PROJECT_NAME, NRV_DVS_GENERATED_INCLUDE_DIR
# 산출(설치 prefix 기준):
#   lib/libnrv_dvs.so*, include/nrv_dvs/*, share/nrv_dvs/settings/*,
#   lib/cmake/NrvDvs/{NrvDvsConfig,NrvDvsConfigVersion,NrvDvsTargets}.cmake
# ============================================================
include(CMakePackageConfigHelpers)

set(NRV_DVS_CMAKE_INSTALL_DIR "${CMAKE_INSTALL_LIBDIR}/cmake/NrvDvs")

install(TARGETS ${PROJECT_NAME} EXPORT NrvDvsTargets
    LIBRARY  DESTINATION ${CMAKE_INSTALL_LIBDIR}
    ARCHIVE  DESTINATION ${CMAKE_INSTALL_LIBDIR}
    RUNTIME  DESTINATION ${CMAKE_INSTALL_BINDIR}
    INCLUDES DESTINATION ${CMAKE_INSTALL_INCLUDEDIR})
install(DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/include/nrv_dvs"
        DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
        PATTERN ".gitkeep" EXCLUDE)
install(FILES "${NRV_DVS_GENERATED_INCLUDE_DIR}/nrv_dvs/export.h"
        DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}/nrv_dvs)
install(DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/settings/"
        DESTINATION ${CMAKE_INSTALL_DATADIR}/nrv_dvs/settings
        PATTERN ".gitkeep" EXCLUDE)
install(EXPORT NrvDvsTargets NAMESPACE NrvDvs::
        DESTINATION ${NRV_DVS_CMAKE_INSTALL_DIR})

configure_package_config_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/.cmake/cmake_include/NrvDvsConfig.cmake.in"
    "${CMAKE_CURRENT_BINARY_DIR}/NrvDvsConfig.cmake"
    INSTALL_DESTINATION ${NRV_DVS_CMAKE_INSTALL_DIR})
write_basic_package_version_file(
    "${CMAKE_CURRENT_BINARY_DIR}/NrvDvsConfigVersion.cmake"
    COMPATIBILITY SameMajorVersion)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/NrvDvsConfig.cmake"
              "${CMAKE_CURRENT_BINARY_DIR}/NrvDvsConfigVersion.cmake"
        DESTINATION ${NRV_DVS_CMAKE_INSTALL_DIR})
