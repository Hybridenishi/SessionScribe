#pragma once

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <map>
#include <mutex>
#include <optional>
#include <span>
#include <string>
#include <vector>

namespace session_scribe::spike {

struct RtpHeader {
    uint16_t sequence;
    uint32_t timestamp;
};

[[nodiscard]] std::optional<RtpHeader> parseRtpHeader(std::span<const uint8_t> packet);

struct AudioPacket {
    std::string userID;
    std::optional<RtpHeader> rtp;
    std::vector<uint8_t> pcm;
};

class Recorder {
public:
    explicit Recorder(std::filesystem::path outputDirectory);
    ~Recorder();

    Recorder(const Recorder&) = delete;
    Recorder& operator=(const Recorder&) = delete;

    void start(const std::string& buildID);
    void connected();
    void disconnected(const std::string& reason);
    void participant(const std::string& userID, const std::string& displayName);
    void speaking(const std::string& userID, bool isActive);
    void record(AudioPacket packet);
    void finalize();

    [[nodiscard]] const std::filesystem::path& outputDirectory() const;

private:
    struct StreamState {
        std::optional<uint16_t> lastSequence;
    };

    struct Track {
        std::filesystem::path path;
        std::ofstream stream;
        uint64_t bytesWritten = 0;
    };

    [[nodiscard]] uint64_t elapsedMilliseconds() const;
    [[nodiscard]] std::string trackKey(const std::string& userID) const;
    [[nodiscard]] Track& trackFor(const std::string& userID);
    void emit(const std::string& json);
    void finalizeTrack(Track& track);
    void writeManifest();

    std::filesystem::path outputDirectory_;
    std::ofstream events_;
    std::map<std::string, StreamState> streams_;
    std::map<std::string, Track> tracks_;
    std::map<std::string, std::string> participants_;
    std::mutex mutex_;
    uint64_t epoch_ = 0;
    bool started_ = false;
    bool finalized_ = false;
    std::chrono::steady_clock::time_point startedAt_;
};

} // namespace session_scribe::spike
