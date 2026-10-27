// 녹화 파일(dvs_live, dvs_streamer 의 dump-*.bin.zst, event-*.bin.zst)을 센서 프레임 하나당
// 동영상 프레임 하나로 그려 저장하는 유틸리티. 실행 디렉터리의 zst/*.zst 를 읽어
// video/<이름>.mov 로 씀 (FFV1 무손실, 회색 배경, ON 흰색, OFF 검정). 프레임 시각은 센서
// 시각(us)이라 재생 시간 = 녹화 시간 (약 2000 fps, 화면에는 일부만 보임. VLC 의 프레임
// 넘기기로 센서 프레임을 하나씩 봄).
// 형식은 내용으로 정함: 앞 버퍼에 RAW2 머리말 열(0xCCCCCCCC 가 180 B 간격으로 둘)이
// 있으면 RAW2, 아니면 group AER. 파일은 StdSource(공개 API)로 넣음.
// 처리 중에는 kProgress 마다 읽은 압축 바이트 비율과 남은 시간 추정을 출력.
// 결과만 출력하는 유틸리티라 항상 성공으로 끝남.
#include "nrv_dvs/delta.h"
#include <chrono>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <memory>
#include <span>
#include <streambuf>
#include <vector>
#include <zstd.h>
extern "C" {
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
}

constexpr const char *kInput = "zst";
constexpr const char *kOutput = "video";
// SensorFrame::timestamp_us 가 0 으로 돌아가는 주기 (2^22 ms)
constexpr std::uint64_t kTimestampWrap = (std::uint64_t{1} << 22) * 1000;
constexpr std::uint8_t kBackground = 128, kOn = 255, kOff = 0;
constexpr auto kProgress = std::chrono::seconds(5);

// zstd 파일을 풀어 읽는 streambuf
class ZstdBuf final : public std::streambuf {
public:
  explicit ZstdBuf(const std::filesystem::path &path)
      : file_(path, std::ios::binary), in_(ZSTD_DStreamInSize()), out_(ZSTD_DStreamOutSize()) {}

  std::uint64_t consumed() const { return consumed_; } // 읽은 압축 바이트

private:
  int_type underflow() override {
    while (gptr() == egptr()) {
      if (input_.pos == input_.size) {
        if (!file_.read(in_.data(), static_cast<std::streamsize>(in_.size())) && !file_.gcount())
          return traits_type::eof();
        input_ = {in_.data(), static_cast<std::size_t>(file_.gcount()), 0};
        consumed_ += input_.size;
      }
      ZSTD_outBuffer output{out_.data(), out_.size(), 0};
      if (ZSTD_isError(ZSTD_decompressStream(dctx_.get(), &output, &input_)))
        return traits_type::eof();
      setg(out_.data(), out_.data(), out_.data() + output.pos);
    }
    return traits_type::to_int_type(*gptr());
  }

  std::ifstream file_;
  std::unique_ptr<ZSTD_DCtx, decltype(&ZSTD_freeDCtx)> dctx_{ZSTD_createDCtx(), &ZSTD_freeDCtx};
  std::vector<char> in_, out_;
  ZSTD_inBuffer input_{nullptr, 0, 0};
  std::uint64_t consumed_ = 0;
};

class ZstdStream final : public std::istream {
public:
  explicit ZstdStream(const std::filesystem::path &path) : std::istream(nullptr), buf_(path) {
    rdbuf(&buf_);
  }

  std::uint64_t consumed() const { return buf_.consumed(); }

private:
  ZstdBuf buf_;
};

// 앞 RAW2 버퍼 하나 안에 머리말 열 둘이 이어지면 RAW2
static delta::Format Detect(const std::filesystem::path &path) {
  std::vector<char> head(2880 * 61);
  ZstdStream in(path);
  in.read(head.data(), static_cast<std::streamsize>(head.size()));
  const std::size_t n = static_cast<std::size_t>(in.gcount());
  for (std::size_t i = 0; i + 184 <= n; ++i)
    if (!std::memcmp(&head[i], "\xCC\xCC\xCC\xCC", 4) &&
        !std::memcmp(&head[i + 180], "\xCC\xCC\xCC\xCC", 4))
      return delta::Format::Raw2;
  return delta::Format::Compressed;
}

// 인코더에서 나온 패킷을 모두 파일에 씀. 실패하면 false
static bool Drain(AVCodecContext *codec, AVFormatContext *format, AVStream *stream,
                  AVPacket *packet) {
  int rc = 0;
  while ((rc = avcodec_receive_packet(codec, packet)) == 0) {
    av_packet_rescale_ts(packet, codec->time_base, stream->time_base);
    packet->stream_index = stream->index;
    if (av_interleaved_write_frame(format, packet) < 0)
      return false;
  }
  return rc == AVERROR(EAGAIN) || rc == AVERROR_EOF;
}

// path 를 해석해 out 에 동영상으로 씀
static void Convert(const std::filesystem::path &path, const std::filesystem::path &out) {
  const auto start = std::chrono::steady_clock::now();
  const delta::Format format = Detect(path);
  const bool raw2 = format == delta::Format::Raw2;
  auto input = std::make_unique<ZstdStream>(path);
  // source 가 소유한 뒤에도 진행률을 읽음 (Read 를 부르는 이 스레드에서만 바뀜)
  const ZstdStream &progress = *input;
  std::error_code size_ec;
  const std::uint64_t total = std::filesystem::file_size(path, size_ec);
  delta::StdSource source(std::move(input), format);
  std::unique_ptr<delta::Decoder> decoder;
  if (raw2)
    decoder = std::make_unique<delta::Raw2Decoder>(std::make_unique<delta::WaitPipe>(source, 1));
  else
    decoder = std::make_unique<delta::GroupAerDecoder>(std::make_unique<delta::WaitPipe>(source, 1));
  delta::WaitPipe frames(*decoder, 1);

  AVFormatContext *container = nullptr;
  if (avformat_alloc_output_context2(&container, nullptr, "mov", out.c_str()) < 0) {
    std::printf("%s: cannot create %s\n", path.c_str(), out.c_str());
    return;
  }
  std::unique_ptr<AVFormatContext, decltype(&avformat_free_context)> container_owner(
      container, &avformat_free_context);
  std::unique_ptr<AVCodecContext, decltype([](AVCodecContext *c) { avcodec_free_context(&c); })>
      codec_owner(avcodec_alloc_context3(avcodec_find_encoder(AV_CODEC_ID_FFV1)));
  AVCodecContext *codec = codec_owner.get();
  codec->width = static_cast<int>(delta::kImageWidth);
  codec->height = static_cast<int>(delta::kImageHeight);
  codec->pix_fmt = AV_PIX_FMT_GRAY8;
  codec->time_base = {1, 1'000'000}; // 센서 시각 us
  codec->thread_count = 0; // 코어 수만큼 (FFV1 은 슬라이스를 나눠 인코딩)
  codec->slices = 24;
  if (container->oformat->flags & AVFMT_GLOBALHEADER)
    codec->flags |= AV_CODEC_FLAG_GLOBAL_HEADER;
  AVStream *stream = avformat_new_stream(container, nullptr);
  if (!stream || avcodec_open2(codec, codec->codec, nullptr) < 0 ||
      avcodec_parameters_from_context(stream->codecpar, codec) < 0) {
    std::printf("%s: cannot open FFV1 encoder\n", path.c_str());
    return;
  }
  stream->time_base = codec->time_base;
  if (avio_open(&container->pb, out.c_str(), AVIO_FLAG_WRITE) < 0 ||
      avformat_write_header(container, nullptr) < 0) {
    std::printf("%s: cannot write %s\n", path.c_str(), out.c_str());
    return;
  }
  std::unique_ptr<AVFrame, decltype([](AVFrame *f) { av_frame_free(&f); })> picture(av_frame_alloc());
  std::unique_ptr<AVPacket, decltype([](AVPacket *p) { av_packet_free(&p); })> packet(av_packet_alloc());
  picture->format = codec->pix_fmt;
  picture->width = codec->width;
  picture->height = codec->height;
  bool ok = av_frame_get_buffer(picture.get(), 0) == 0;

  std::vector<delta::Block> data;
  bool discontinuous = false;
  std::uint64_t written = 0, discontinuities = 0;
  std::uint64_t first_us = 0, previous_us = 0, wrapped_us = 0; // 시각이 0 으로 돌아간 만큼 더함
  std::error_code ec;
  auto report = start + kProgress;
  while (ok && !(ec = frames.Read(data, discontinuous, std::chrono::seconds(10)))) {
    if (const auto now = std::chrono::steady_clock::now(); now >= report && total) {
      report = now + kProgress;
      const double done = static_cast<double>(progress.consumed()) / static_cast<double>(total);
      const double elapsed = std::chrono::duration<double>(now - start).count();
      std::printf("%s: %.1f%%, %llu frames, %.0f s elapsed, about %.0f s left\n",
                  path.filename().c_str(), 100 * done, static_cast<unsigned long long>(written),
                  elapsed, done > 0 ? elapsed * (1 - done) / done : 0.0);
      std::fflush(stdout);
    }
    const std::span<const std::byte> bytes = std::as_bytes(std::span(data));
    delta::SensorFrame frame{};
    std::memcpy(&frame, bytes.data(), sizeof(frame));
    ok = av_frame_make_writable(picture.get()) == 0;
    for (int y = 0; ok && y < picture->height; ++y)
      std::memset(picture->data[0] + y * picture->linesize[0], kBackground,
                  static_cast<std::size_t>(picture->width));
    for (std::uint32_t g = 0; ok && g < frame.groups; ++g) {
      delta::EventGroup e{};
      std::memcpy(&e, bytes.data() + sizeof(frame) + g * sizeof(e), sizeof(e));
      for (int bit = 0; bit < 8; ++bit)
        if (e.mask >> bit & 1)
          picture->data[0][(e.y + bit) * picture->linesize[0] + e.x] = e.polarity ? kOff : kOn;
    }
    if (written && frame.timestamp_us < previous_us)
      wrapped_us += kTimestampWrap;
    previous_us = frame.timestamp_us;
    if (!written)
      first_us = frame.timestamp_us;
    picture->pts = static_cast<std::int64_t>(frame.timestamp_us + wrapped_us - first_us);
    ++written;
    discontinuities += discontinuous;
    ok = ok && avcodec_send_frame(codec, picture.get()) == 0 &&
         Drain(codec, container, stream, packet.get());
  }
  ok = ok && avcodec_send_frame(codec, nullptr) == 0 && Drain(codec, container, stream, packet.get());
  av_write_trailer(container);
  avio_closep(&container->pb);

  const delta::DecodeStats s = decoder->Stats();
  const double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
  const auto size = std::filesystem::file_size(out, size_ec);
  std::printf("%s (%s): %llu frames -> %s (%.1f MiB, %.1f s), discontinuous %llu, "
              "frame gaps %llu, invalid %llu, end: %s%s, took %.1f s\n",
              path.filename().c_str(), raw2 ? "RAW2" : "group AER",
              static_cast<unsigned long long>(written), out.c_str(),
              static_cast<double>(size_ec ? 0 : size) / (1 << 20),
              static_cast<double>(previous_us + wrapped_us - first_us) / 1e6,
              static_cast<unsigned long long>(discontinuities),
              static_cast<unsigned long long>(s.frame_gaps), static_cast<unsigned long long>(s.invalid),
              ec.message().c_str(), ok ? "" : " (video write failed)", seconds);
}

int main() {
  std::error_code ec;
  std::vector<std::filesystem::path> inputs;
  for (const auto &entry : std::filesystem::directory_iterator(kInput, ec))
    if (entry.path().extension() == ".zst")
      inputs.push_back(entry.path());
  if (inputs.empty()) {
    std::printf("no %s/*.zst to convert\n", kInput);
    return 0;
  }
  std::filesystem::create_directories(kOutput, ec);
  for (const auto &path : inputs)
    Convert(path, std::filesystem::path(kOutput) / path.stem().replace_extension(".mov"));
  return 0;
}
