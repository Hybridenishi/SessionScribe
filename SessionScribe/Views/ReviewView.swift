import SwiftUI

/// Sessions with proposals, on the left; the chosen session's cards on the right.
struct ReviewHomeView: View {
    let connection: HubConnection
    let player: any AudioClipPlaying
    @State private var sessions: [SessionSummary] = []
    @State private var selection: Int?

    private static let reviewStates: Set<String> = ["proposing", "review", "published"]

    var body: some View {
        NavigationSplitView {
            List(sessions.filter { Self.reviewStates.contains($0.state) }, selection: $selection) { s in
                VStack(alignment: .leading, spacing: 2) {
                    Text("Session \(s.number)").font(.body.weight(.medium))
                    Text(s.state == "review" ? (s.detail ?? "To review") : s.stateLabel)
                        .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
                .tag(s.number)
            }
            .overlay {
                if !sessions.contains(where: { Self.reviewStates.contains($0.state) }) {
                    ContentUnavailableView("Nothing to review", systemImage: "rectangle.stack",
                                           description: Text("Open a transcribed session in Sessions and press Propose changes."))
                }
            }
            .navigationSplitViewColumnWidth(min: 200, ideal: 240)
        } detail: {
            if let n = selection {
                ReviewView(viewModel: ReviewViewModel(number: n, connection: connection, player: player))
                    .id(n)
            } else {
                ContentUnavailableView("Pick a session", systemImage: "rectangle.stack.badge.person.crop")
            }
        }
        .navigationTitle("Review")
        .task {
            while !Task.isCancelled {
                if let client = connection.client, let list = try? await client.sessions() { sessions = list }
                try? await Task.sleep(for: .seconds(15))
            }
        }
    }
}

struct ReviewView: View {
    @State var viewModel: ReviewViewModel
    @State private var editing: Proposal?
    @State private var showRejected = false

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 18) {
                banners
                if viewModel.list?.batch == nil {
                    ContentUnavailableView {
                        Label("No proposals yet", systemImage: "rectangle.stack")
                    } description: {
                        Text("The agent reads the transcript and drafts every vault change as a card here.")
                    } actions: {
                        Button("Propose changes") { Task { await viewModel.propose() } }
                            .disabled(viewModel.isWorking)
                    }
                    .padding(.top, 40)
                }
                ForEach(viewModel.groups) { group in
                    VStack(alignment: .leading, spacing: 10) {
                        Text(group.entity).font(.title3.weight(.semibold))
                        ForEach(group.cards) { card in
                            ProposalCard(card: card, viewModel: viewModel,
                                         readOnly: viewModel.isPublished) { editing = card }
                        }
                    }
                }
                if !viewModel.rejectedByChecks.isEmpty {
                    DisclosureGroup("Rejected by checks (\(viewModel.rejectedByChecks.count))",
                                    isExpanded: $showRejected) {
                        ForEach(viewModel.rejectedByChecks) { p in
                            VStack(alignment: .leading, spacing: 2) {
                                Text("\(p.entity) · \(ReviewViewModel.describe(p))").font(.callout.weight(.medium))
                                Text(p.checkError ?? "").font(.caption).foregroundStyle(.orange)
                                Text(p.target).font(.caption.monospaced()).foregroundStyle(.secondary)
                            }
                            .padding(.vertical, 4)
                        }
                    }
                    .padding(12)
                    .background(.background, in: RoundedRectangle(cornerRadius: 10))
                }
            }
            .padding(20)
            .frame(maxWidth: 1_100, alignment: .leading)
        }
        .navigationTitle("Review · Session \(viewModel.number)")
        .toolbar { toolbar }
        .sheet(item: $editing) { card in
            EditSheet(card: card) { text in
                Task {
                    if await viewModel.saveEdit(card, text: text) { editing = nil }
                }
            } cancel: {
                editing = nil
            }
        }
        .task {
            while !Task.isCancelled {
                await viewModel.refresh()
                try? await Task.sleep(for: .seconds(20))
            }
        }
        .onDisappear { viewModel.stopPlayback() }
    }

    @ViewBuilder
    private var banners: some View {
        if let error = viewModel.errorMessage {
            Label(error, systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(.red).padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.red.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
        }
        if let notice = viewModel.notice {
            Label(notice, systemImage: "info.circle").padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.blue.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
        }
        if let b = viewModel.batch, !b.violations.isEmpty {
            Label("The agent edited \(b.violations.count) file(s) it isn't allowed to. The hub undid them: "
                  + b.violations.joined(separator: ", "), systemImage: "hand.raised.fill")
                .foregroundStyle(.orange).padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
        }
        if viewModel.batch != nil {
            HStack(spacing: 16) {
                Text("\(viewModel.count("pending")) to review")
                Text("\(viewModel.count("accepted")) accepted").foregroundStyle(.green)
                Text("\(viewModel.count("deferred")) deferred").foregroundStyle(.secondary)
                Text("\(viewModel.count("rejected")) rejected").foregroundStyle(.secondary)
                if viewModel.isPublished {
                    Label("Published", systemImage: "checkmark.seal.fill").foregroundStyle(.green)
                }
            }
            .font(.callout)
        }
        if let r = viewModel.lastPublish, !r.files.isEmpty {
            DisclosureGroup(r.dryRun ? "Files a publish would change (\(r.files.count))"
                                     : "Files changed (\(r.files.count))") {
                ForEach(r.files, id: \.self) { Text($0).font(.caption.monospaced()) }
            }
        }
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItemGroup {
            if viewModel.batch != nil && !viewModel.isPublished {
                Button("Re-run proposing") { Task { await viewModel.propose() } }
                    .help("Ask the agent again. Unpublished cards are replaced.")
                    .disabled(viewModel.isWorking)
                Button("Dry run") { Task { await viewModel.publish(dryRun: true) } }
                    .disabled(!viewModel.canPublish)
                    .help("See which vault files would change, without writing anything")
                Button("Publish \(viewModel.count("accepted")) to canon") {
                    Task { await viewModel.publish(dryRun: false) }
                }
                .buttonStyle(.borderedProminent)
                .disabled(!viewModel.canPublish)
                .help("Merge the accepted cards into the vault's main branch")
            }
        }
    }
}

struct ProposalCard: View {
    let card: Proposal
    let viewModel: ReviewViewModel
    let readOnly: Bool
    let edit: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(ReviewViewModel.describe(card)).font(.headline)
                if card.secret {
                    Label("DM only", systemImage: "lock.fill").font(.caption.weight(.semibold))
                        .foregroundStyle(.purple)
                }
                Spacer()
                Text(ReviewViewModel.stateLabel(card.state)).font(.caption.weight(.semibold))
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(stateColor.opacity(0.15), in: Capsule())
                    .foregroundStyle(stateColor)
            }
            Text(card.target).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
            Text(card.rationale)
            if let err = card.checkError {
                Label(err, systemImage: "exclamationmark.triangle").font(.callout).foregroundStyle(.orange)
            }
            changeView
            if card.editedAfter != nil {
                Label("Edited by you", systemImage: "pencil").font(.caption).foregroundStyle(.secondary)
            }
            if !card.evidence.isEmpty { evidence }
            if !card.conflicts.isEmpty {
                Label("Conflicts with: " + card.conflicts.map { [$0.target, $0.section].compactMap { $0 }
                    .joined(separator: " › ") }.joined(separator: "; "),
                      systemImage: "arrow.triangle.branch").font(.callout).foregroundStyle(.orange)
            }
            if !readOnly && card.state != "applied" { actions }
        }
        .padding(14)
        .background(.background, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(stateColor.opacity(card.state == "pending" ? 0 : 0.5)))
    }

    private var stateColor: Color {
        switch card.state {
        case "accepted", "applied": .green
        case "rejected": .red
        case "deferred": .gray
        default: .blue
        }
    }

    @ViewBuilder
    private var changeView: some View {
        switch card.op {
        case "move-note":
            Text("\(card.fromPath ?? "?")  →  \(card.target)").font(.callout.monospaced())
        case "create-note", "open-question":
            DiffText(text: TextDiff.rendered("", card.effectiveAfter).after, title: "New")
        default:
            let d = TextDiff.rendered(card.before ?? "", card.effectiveAfter)
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: 12) {
                    DiffText(text: d.before, title: "Before").frame(minWidth: 320, maxWidth: .infinity)
                    DiffText(text: d.after, title: "After").frame(minWidth: 320, maxWidth: .infinity)
                }
                VStack(alignment: .leading, spacing: 8) {
                    DiffText(text: d.before, title: "Before")
                    DiffText(text: d.after, title: "After")
                }
            }
        }
    }

    private var evidence: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Evidence").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            ForEach(card.evidence, id: \.self) { e in
                Button {
                    Task {
                        if viewModel.playingEvidence == e.utteranceId { viewModel.stopPlayback() }
                        else { await viewModel.play(e) }
                    }
                } label: {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Image(systemName: viewModel.playingEvidence == e.utteranceId ? "stop.circle.fill" : "play.circle.fill")
                        Text(SessionDetailViewModel.timestamp(e.startMs)).font(.caption.monospacedDigit())
                        Text(e.speaker).font(.caption.weight(.semibold))
                        Text("“\(e.text)”").font(.callout).lineLimit(3)
                    }
                    .padding(.horizontal, 8).padding(.vertical, 4)
                    .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 6))
                }
                .buttonStyle(.plain)
                .help("Play this moment")
            }
        }
    }

    private var actions: some View {
        HStack {
            Button("Accept") { Task { await viewModel.decide(card, "accept") } }
                .disabled(card.state == "accepted")
            Button("Edit…", action: edit)
            Button("Reject") { Task { await viewModel.decide(card, "reject") } }
                .disabled(card.state == "rejected")
            Button("Defer") { Task { await viewModel.decide(card, "defer") } }
                .disabled(card.state == "deferred")
            Spacer()
        }
        .disabled(viewModel.isWorking)
    }
}

/// Rendered text with removed words struck through in red and added words highlighted in green.
private struct DiffText: View {
    let text: AttributedString
    let title: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            Text(text.characters.isEmpty ? AttributedString("(empty)") : text)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(10)
                .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 8))
        }
    }
}

private struct EditSheet: View {
    let card: Proposal
    let save: (String) -> Void
    let cancel: () -> Void
    @State private var text = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Edit · \(card.entity)").font(.title3.weight(.semibold))
            Text(ReviewViewModel.describe(card) + " · " + card.target).font(.caption).foregroundStyle(.secondary)
            TextEditor(text: $text)
                .font(.body.monospaced())
                .frame(minWidth: 560, minHeight: 280)
            Text("Saving accepts the card. The hub checks your text the same way it checked the agent's.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel, action: cancel)
                Button("Save and accept") { save(text) }.keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .onAppear { text = card.effectiveAfter }
    }
}
