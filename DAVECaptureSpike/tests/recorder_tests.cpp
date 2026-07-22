#include "session_scribe_spike/recorder.hpp"

#include <array>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

std::string readFile(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    return { std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>() };
}

void testRtpHeader() {
    const std::array<uint8_t, 12> packet {
        0x80, 0x78, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0, 0, 0, 0,
    };
    const auto header = session_scribe::spike::parseRtpHeader(packet);
    expect(header.has_value(), "Expected a valid RTP header");
    expect(header->sequence == 258, "Unexpected RTP sequence");
    expect(header->timestamp == 50'595'078, "Unexpected RTP timestamp");
    expect(!session_scribe::spike::parseRtpHeader({}).has_value(), "Short packet must not parse");
}

void testRecorderFinalizesTracksAndGaps() {
    const std::filesystem::path output = std::filesystem::temp_directory_path() /
        ("session-scribe-spike-test-" + std::to_string(std::rand()));
    std::filesystem::remove_all(output);

    {
        session_scribe::spike::Recorder recorder(output);
        recorder.start("test-build");
        recorder.connected();
        recorder.participant("discord/user", "A \"Player\"");
        recorder.record({
            .userID = "discord/user",
            .rtp = session_scribe::spike::RtpHeader { 100, 4'800 },
            .pcm = std::vector<uint8_t>(16, 0x01),
        });
        recorder.record({
            .userID = "discord/user",
            .rtp = session_scribe::spike::RtpHeader { 103, 9'600 },
            .pcm = std::vector<uint8_t>(16, 0x02),
        });
        recorder.disconnected("test_reconnect");
        recorder.connected();
        recorder.record({
            .userID = "discord/user",
            .rtp = session_scribe::spike::RtpHeader { 104, 14'400 },
            .pcm = std::vector<uint8_t>(16, 0x03),
        });
        recorder.finalize();
    }

    const std::filesystem::path firstTrack = output / "audio" / "discord_user" / "epoch-1.wav";
    const std::filesystem::path secondTrack = output / "audio" / "discord_user" / "epoch-2.wav";
    const std::string firstTrackContents = readFile(firstTrack);
    const std::string events = readFile(output / "sidecar-events.ndjson");
    const std::string manifest = readFile(output / "spike-manifest.json");

    expect(firstTrackContents.size() == 76, "Finalized WAV must contain a 44-byte header and 32 bytes of audio");
    expect(firstTrackContents.substr(0, 4) == "RIFF", "WAV must start with RIFF");
    expect(firstTrackContents.substr(8, 4) == "WAVE", "WAV must declare WAVE format");
    expect(std::filesystem::exists(secondTrack), "Reconnect must create a new epoch track");
    expect(events.find("\"type\":\"gap\"") != std::string::npos, "Missing RTP sequence must emit a gap");
    expect(events.find("\"missing_sequence_start\":101") != std::string::npos, "Gap start is incorrect");
    expect(events.find("\"missing_sequence_end\":102") != std::string::npos, "Gap end is incorrect");
    expect(manifest.find("\"complete\": true") != std::string::npos, "Manifest must be finalized");
    expect(manifest.find("pcm_s16le_48000_stereo") != std::string::npos, "Manifest must not mislabel the audio format");

    std::filesystem::remove_all(output);
}

} // namespace

int main() {
    try {
        testRtpHeader();
        testRecorderFinalizesTracksAndGaps();
        std::cout << "All DAVE capture spike tests passed.\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Test failure: " << error.what() << '\n';
        return 1;
    }
}
