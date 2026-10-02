import Foundation
import SwiftUI

/// Word-level differences between a section's text before and after a proposal, for the Review
/// cards: what stays, what is removed (shown on the "before" side) and what is added ("after").
enum TextDiff {
    enum Kind: Equatable, Sendable {
        case same, removed, added
    }

    struct Segment: Equatable, Sendable {
        var text: String
        var kind: Kind
    }

    /// Above this many tokens per side the comparison would be slow; the whole text is then
    /// marked as changed instead (rare: a section is usually a few hundred words).
    static let maxTokens = 3_000

    private enum CharClass { case space, word, other }

    private static func charClass(_ ch: Character) -> CharClass {
        if ch.isWhitespace { return .space }
        if ch.isLetter || ch.isNumber || ch == "'" || ch == "’" || ch == "-" { return .word }
        return .other
    }

    /// Words, runs of whitespace, and single punctuation marks, so "Tall." and "Tall," share "Tall".
    /// Joining the tokens gives the text back exactly.
    static func tokens(_ text: String) -> [String] {
        var out: [String] = []
        var current = ""
        var cls: CharClass?
        for ch in text {
            let c = charClass(ch)
            if let was = cls, was != c || c == .other {
                out.append(current)
                current = ""
            }
            current.append(ch)
            cls = c
        }
        if !current.isEmpty { out.append(current) }
        return out
    }

    /// Inline Markdown (bold, italics, links, code) rendered, with the differences highlighted on
    /// top: removed text struck through in red on the before side, added text in green on the
    /// after side. Line structure (lists, headings) is kept as written.
    static func rendered(_ before: String, _ after: String) -> (before: AttributedString, after: AttributedString) {
        let a = markdown(before), b = markdown(after)
        let d = diff(String(a.characters), String(b.characters))
        return (highlight(a, d.before), highlight(b, d.after))
    }

    static func markdown(_ text: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        return (try? AttributedString(markdown: text, options: options)) ?? AttributedString(text)
    }

    private static func highlight(_ text: AttributedString, _ segments: [Segment]) -> AttributedString {
        var out = text
        var offset = 0
        for s in segments {
            let length = s.text.count
            defer { offset += length }
            guard s.kind != .same, length > 0 else { continue }
            let start = out.characters.index(out.startIndex, offsetBy: offset)
            let end = out.characters.index(start, offsetBy: length)
            switch s.kind {
            case .removed:
                out[start ..< end].backgroundColor = .red.opacity(0.18)
                out[start ..< end].strikethroughStyle = .single
            case .added:
                out[start ..< end].backgroundColor = .green.opacity(0.22)
            case .same:
                break
            }
        }
        return out
    }

    /// (before side, after side). Joining a side's segment texts gives that text back exactly.
    static func diff(_ before: String, _ after: String) -> (before: [Segment], after: [Segment]) {
        let a = tokens(before), b = tokens(after)
        if a.count > maxTokens || b.count > maxTokens {
            return (a.isEmpty ? [] : [Segment(text: before, kind: .removed)],
                    b.isEmpty ? [] : [Segment(text: after, kind: .added)])
        }
        // Longest common subsequence over tokens.
        let n = a.count, m = b.count
        var lcs = Array(repeating: Array(repeating: 0, count: m + 1), count: n + 1)
        for i in stride(from: n - 1, through: 0, by: -1) {
            for j in stride(from: m - 1, through: 0, by: -1) {
                lcs[i][j] = a[i] == b[j] ? lcs[i + 1][j + 1] + 1 : max(lcs[i + 1][j], lcs[i][j + 1])
            }
        }
        var left: [Segment] = [], right: [Segment] = []
        func push(_ list: inout [Segment], _ text: String, _ kind: Kind) {
            if let last = list.last, last.kind == kind {
                list[list.count - 1].text += text
            } else {
                list.append(Segment(text: text, kind: kind))
            }
        }
        var i = 0, j = 0
        while i < n || j < m {
            if i < n, j < m, a[i] == b[j] {
                push(&left, a[i], .same)
                push(&right, b[j], .same)
                i += 1
                j += 1
            } else if j < m, i == n || lcs[i][j + 1] >= lcs[i + 1][j] {
                push(&right, b[j], .added)
                j += 1
            } else {
                push(&left, a[i], .removed)
                i += 1
            }
        }
        return (left, right)
    }
}
