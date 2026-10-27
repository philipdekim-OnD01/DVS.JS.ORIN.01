#include "compressor.h" // Compressor::Impl
#include <memory>
#include <nrv_dvs/compressor.h>
#include <stop_token>
#include <system_error>
#include <utility>

namespace delta {

Compressor::Compressor(std::unique_ptr<Pipe> upstream, int level) {
  if (!upstream)
    throw std::system_error(std::make_error_code(std::errc::invalid_argument),
                            "Compressor: no upstream pipe");
  ZSTD_CCtx *cctx = ZSTD_createCCtx();
  if (!cctx)
    throw std::system_error(std::make_error_code(std::errc::not_enough_memory),
                            "Compressor: ZSTD_createCCtx");
  (void)ZSTD_CCtx_setParameter(cctx, ZSTD_c_compressionLevel, level); // 범위 밖은 끝으로 맞춰짐
  impl_ = std::make_shared<Impl>(std::move(upstream), cctx, level);
}

Compressor::~Compressor() = default;
Compressor::Compressor(Compressor &&) noexcept = default;
Compressor &Compressor::operator=(Compressor &&) noexcept = default;

std::error_code Compressor::Work(std::stop_token stop) {
  if (!impl_)
    return std::make_error_code(std::errc::bad_file_descriptor);
  return static_cast<Impl &>(*impl_).Work(std::move(stop));
}

} // namespace delta
