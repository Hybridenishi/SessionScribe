#include "session_scribe_spike/recorder.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace session_scribe::spike {
namespace {

constexpr uint32_t pcmSampleRate = 48'000;
constexpr uint16_t pcmChannels = 2;
constexpr uint16_t pcmBitsPerSample = 16;
constexpr uint32_t pcmBytesPerSecond = pcmSampleRate * pcmChannels * (pcmBitsPerSample / 8);

std::string escapeJSON(const std::string& value) {
    std::string escaped;
    escaped.reserve(value.size());

    for (const unsigned char character : value) {
        switch (character) {
        case '"':
            escaped += "\\\"";
            break;
        case '\\':
            escaped += "\\\\";
            break;
        case '\b':
            escaped += "\\b";
            break;
        case '\f':
            escaped += "\\f";
            break;
        case '\n':
            escaped += "\\n";
            break;
        case '\r':
            escaped += "\\r";
            break;
        case '\t':
            escaped += "\\t";
            break;
        default:
            if (character < 0x20) {
                std::ostringstream codepoint;
                codepoint << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                          << static_cast<unsigned int>(character);
                escaped += codepoint.str();
            } else {
                escaped += static_cast<char>(character);
            }
            break;
        }
    }

    return escaped;
}

std::string safePathComponent(const std::string& value) {
    std::string result;
    result.reserve(value.size());

    for (const unsigned char character : value) {
        if ((character >= 'a' && character <= 'z') ||
            (character >= 'A' && character <= 'Z') ||
            (character >= '0' && character <= '9') ||
            character == '-' || character == '_') {
            result += static_cast<char>(character);
        } else {
            result += '_';
        }
    }

    return result.empty() ? "unknown" : result;
}

void writeLittleEndian16(std::ostream& output, uint16_t value) {
    const std::array<char, 2> bytes {
        static_cast<char>(value & 0xFF),
        static_cast<char>((value >> 8) & 0xFF),
    };
    output.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
}

void writeLittleEndian32(std::ostream& output, uint32_t value) {
    const std::array<char, 4> bytes {
        static_cast<char>(value & 0xFF),
        static_cast<char>((value >> 8) & 0xFF),
        static_cast<char>((value >> 16) & 0xFF),
        static_cast<char>((value >> 24) & 0xFF),
    };
    output.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
}

void writeWavHeader(std::ostream& output, uint32_t dataSize) {
    output.write("RIFF", 4);
    writeLittleEndian32(output, 36 + dataSize);
    output.write("WAVE", 4);
    output.write("fmt ", 4);
    writeLittleEndian32(output, 16);
    writeLittleEndian16(output, 1);
    writeLittleEndian16(output, pcmChannels);
    writeLittleEndian32(output, pcmSampleRate);
    writeLittleEndian32(output, pcmBytesPerSecond);
    writeLittleEndian16(output, pcmChannels * (pcmBitsPerSample / 8));
    writeLittleEndian16(output, pcmBitsPerSample);
    output.write("data", 4);
    writeLittleEndian32(output, dataSize);
}

std::string jsonString(const std::string& key, const std::string& value) {
    return "\"" + key + "\":\"" + escapeJSON(value) + "\"";
}

} // namespace

std::optional<RtpHeader> parseRtpHeader(const std::span<const uint8_t> packet) {
    if (packet.size() < 12 || (packet[0] >> 6) != 2) {
        return std::nullopt;
    }

    const uint16_t sequence = static_cast<uint16_t>(packet[2] << 8) | packet[3];
    const uint32_t timestamp = (static_cast<uint32_t>(packet[4]) << 24) |
                               (static_cast<uint32_t>(packet[5]) << 16) |
                               (static_cast<uint32_t>(packet[6]) << 8) |
                               packet[7];
    return RtpHeader { sequence, timestamp };
}

Recorder::Recorder(std::filesystem::path outputDirectory)
    : outputDirectory_(std::move(outputDirectory)) {}

Recorder::~Recorder() {
    try {
        finalize();
    } catch (...) {
        // A destructor cannot safely report I/O failure. The caller receives errors from explicit finalization.
    }
}

void Recorder::start(const std::string& buildID) {
    std::scoped_lock lock(mutex_);
    if (started_) {
        throw std::logic_error("Recorder has already started");
    }

    std::filesystem::create_directories(outputDirectory_ / "audio");
    events_.open(outputDirectory_ / "sidecar-events.ndjson", std::ios::binary | std::ios::trunc);
    if (!events_) {
        throw std::runtime_error("Unable to open sidecar event log");
    }

    startedAt_ = std::chrono::steady_clock::now();
    started_ = true;
    emit("{\"schema\":1,\"type\":\"hello\",\"protocol_version\":1," +
         jsonString("build_id", buildID) +
         ",\"capabilities\":[\"dave\",\"per_user_audio\",\"rtp_gap_reporting\"],"
         "\"audio_format\":\"pcm_s16le_48000_stereo\"}");
    emit("{\"schema\":1,\"type\":\"connection\",\"state\":\"connecting\",\"epoch\":0}");
}

void Recorder::connected() {
    std::scoped_lock lock(mutex_);
    if (!started_ || finalized_) {
        return;
    }

    if (epoch_ > 0) {
        emit("{\"schema\":1,\"type\":\"connection\",\"state\":\"disconnected\",\"epoch\":" +
             std::to_string(epoch_) + ",\"reason\":\"voice_ready_reconnection\"}");
    }
    ++epoch_;
    emit("{\"schema\":1,\"type\":\"connection\",\"state\":\"connected\",\"epoch\":" +
         std::to_string(epoch_) + "}");
}

void Recorder::disconnected(const std::string& reason) {
    std::scoped_lock lock(mutex_);
    if (!started_ || finalized_) {
        return;
    }

    emit("{\"schema\":1,\"type\":\"connection\",\"state\":\"disconnected\",\"epoch\":" +
         std::to_string(epoch_) + "," + jsonString("reason", reason) + "}");
}

void Recorder::participant(const std::string& userID, const std::string& displayName) {
    std::scoped_lock lock(mutex_);
    if (!started_ || finalized_ || participants_.contains(userID)) {
        return;
    }

    participants_.emplace(userID, displayName);
    emit("{\"schema\":1,\"type\":\"participant\"," + jsonString("user_id", userID) + "," +
         jsonString("display_name", displayName) + ",\"state\":\"joined\"}");
}

void Recorder::speaking(const std::string& userID, const bool isActive) {
    std::scoped_lock lock(mutex_);
    if (!started_ || finalized_) {
        return;
    }

    emit("{\"schema\":1,\"type\":\"speaking\"," + jsonString("user_id", userID) +
         ",\"active\":" + (isActive ? "true" : "false") + ",\"timestamp_ms\":" +
         std::to_string(elapsedMilliseconds()) + "}");
}

void Recorder::record(AudioPacket packet) {
    std::scoped_lock lock(mutex_);
    if (!started_ || finalized_ || epoch_ == 0 || packet.userID.empty() || packet.pcm.empty()) {
        return;
    }

    if (!participants_.contains(packet.userID)) {
        participants_.emplace(packet.userID, packet.userID);
        emit("{\"schema\":1,\"type\":\"participant\"," + jsonString("user_id", packet.userID) + "," +
             jsonString("display_name", packet.userID) + ",\"state\":\"joined\"}");
    }

    StreamState& stream = streams_[packet.userID];
    if (packet.rtp.has_value()) {
        if (stream.lastSequence.has_value()) {
            const uint16_t expected = static_cast<uint16_t>(*stream.lastSequence + 1);
            const uint16_t difference = static_cast<uint16_t>(packet.rtp->sequence - expected);
            if (difference > 0 && difference < 0x8000) {
                const uint16_t missingEnd = static_cast<uint16_t>(packet.rtp->sequence - 1);
                emit("{\"schema\":1,\"type\":\"gap\"," + jsonString("user_id", packet.userID) +
                     ",\"epoch\":" + std::to_string(epoch_) + ",\"missing_sequence_start\":" +
                     std::to_string(expected) + ",\"missing_sequence_end\":" + std::to_string(missingEnd) +
                     ",\"estimated_duration_ms\":" + std::to_string(static_cast<uint32_t>(difference) * 20) +
                     ",\"cause\":\"rtp_sequence_gap\"}");
            }

            if (difference < 0x8000) {
                stream.lastSequence = packet.rtp->sequence;
            }
        } else {
            stream.lastSequence = packet.rtp->sequence;
        }
    }

    Track& track = trackFor(packet.userID);
    track.stream.write(reinterpret_cast<const char*>(packet.pcm.data()),
                       static_cast<std::streamsize>(packet.pcm.size()));
    if (!track.stream) {
        throw std::runtime_error("Unable to write participant audio");
    }
    track.bytesWritten += packet.pcm.size();

    std::string event = "{\"schema\":1,\"type\":\"audio\"," + jsonString("user_id", packet.userID) +
                        ",\"epoch\":" + std::to_string(epoch_) + ",\"arrival_ms\":" +
                        std::to_string(elapsedMilliseconds()) + ",\"bytes\":" +
                        std::to_string(packet.pcm.size()) + "," + jsonString("audio_ref", track.path.lexically_relative(outputDirectory_).string());
    if (packet.rtp.has_value()) {
        event += ",\"rtp_sequence\":" + std::to_string(packet.rtp->sequence) +
                 ",\"rtp_timestamp\":" + std::to_string(packet.rtp->timestamp);
    }
    event += "}";
    emit(event);
}

void Recorder::finalize() {
    std::scoped_lock lock(mutex_);
    if (!started_ || finalized_) {
        return;
    }

    for (auto& [_, track] : tracks_) {
        finalizeTrack(track);
    }
    emit("{\"schema\":1,\"type\":\"connection\",\"state\":\"disconnected\",\"epoch\":" +
         std::to_string(epoch_) + ",\"reason\":\"capture_stopped\"}");
    writeManifest();
    events_.flush();
    events_.close();
    finalized_ = true;
}

const std::filesystem::path& Recorder::outputDirectory() const {
    return outputDirectory_;
}

uint64_t Recorder::elapsedMilliseconds() const {
    return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - startedAt_).count());
}

std::string Recorder::trackKey(const std::string& userID) const {
    return userID + ":" + std::to_string(epoch_);
}

Recorder::Track& Recorder::trackFor(const std::string& userID) {
    const std::string key = trackKey(userID);
    if (const auto existing = tracks_.find(key); existing != tracks_.end()) {
        return existing->second;
    }

    const std::filesystem::path path = outputDirectory_ / "audio" / safePathComponent(userID) /
        ("epoch-" + std::to_string(epoch_) + ".wav");
    std::filesystem::create_directories(path.parent_path());

    Track track;
    track.path = path;
    track.stream.open(path, std::ios::binary | std::ios::trunc);
    if (!track.stream) {
        throw std::runtime_error("Unable to create participant track");
    }
    writeWavHeader(track.stream, 0);

    const auto [iterator, _] = tracks_.emplace(key, std::move(track));
    return iterator->second;
}

void Recorder::emit(const std::string& json) {
    events_ << json << '\n';
    events_.flush();
    if (!events_) {
        throw std::runtime_error("Unable to write sidecar event log");
    }
}

void Recorder::finalizeTrack(Track& track) {
    if (track.bytesWritten > UINT32_MAX) {
        throw std::runtime_error("Participant track exceeds WAV size limit for this spike");
    }

    track.stream.seekp(0);
    writeWavHeader(track.stream, static_cast<uint32_t>(track.bytesWritten));
    track.stream.flush();
    track.stream.close();
}

void Recorder::writeManifest() {
    std::ofstream manifest(outputDirectory_ / "spike-manifest.json", std::ios::binary | std::ios::trunc);
    if (!manifest) {
        throw std::runtime_error("Unable to write spike manifest");
    }

    manifest << "{\n"
             << "  \"schema\": 1,\n"
             << "  \"complete\": true,\n"
             << "  \"kind\": \"dave_capture_spike\",\n"
             << "  \"audio_format\": \"pcm_s16le_48000_stereo\",\n"
             << "  \"noncanonical_reason\": \"DPP public receive API exposes decoded PCM; production canonical Opus preservation remains a separate gate.\",\n"
             << "  \"tracks\": [\n";

    bool first = true;
    for (const auto& [key, track] : tracks_) {
        const std::size_t separator = key.rfind(':');
        const std::string userID = key.substr(0, separator);
        const std::string epoch = key.substr(separator + 1);
        if (!first) {
            manifest << ",\n";
        }
        first = false;
        manifest << "    {\"user_id\": \"" << escapeJSON(userID) << "\", \"epoch\": " << epoch
                 << ", \"path\": \"" << escapeJSON(track.path.lexically_relative(outputDirectory_).string())
                 << "\", \"bytes\": " << track.bytesWritten << "}";
    }

    manifest << "\n  ]\n}\n";
}

} // namespace session_scribe::spike
