import AVFoundation
import Foundation

/// Plays a cited moment fetched from the hub (a short WAV).
@MainActor
protocol AudioClipPlaying: AnyObject {
    func play(_ wav: Data) throws
    func stop()
}

@MainActor
final class AVAudioClipPlayer: AudioClipPlaying {
    private var player: AVAudioPlayer?

    func play(_ wav: Data) throws {
        stop()
        let player = try AVAudioPlayer(data: wav)
        self.player = player
        player.play()
    }

    func stop() {
        player?.stop()
        player = nil
    }
}
