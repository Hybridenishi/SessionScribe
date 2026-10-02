import AppKit
import SwiftUI
import Testing
@testable import SessionScribe

/// Renders the Review screen to a PNG attached to the test results, for a human to look at.
/// Also proves the screen builds from real hub data without crashing.
@MainActor
struct RenderSnapshot {
    @Test
    func reviewScreen() async throws {
        let mock = MockHubClient()
        var list = try LiveHubClient.decoder.decode(ProposalList.self, from: Data(HubFixtures.proposals.utf8))
        let accepted = try LiveHubClient.decoder.decode(Proposal.self, from: Data(HubFixtures.decided.utf8))
        list = ProposalList(batch: list.batch, proposals: [accepted] + list.proposals.filter { $0.state != "pending" })
        mock.proposalsValue = list
        let vm = ReviewViewModel(number: 60, connection: PreviewHub.connection(client: mock), player: PreviewHub.Player())
        await vm.refresh()
        // ImageRenderer can't draw ScrollView content, so render the cards themselves
        let view = VStack(alignment: .leading, spacing: 12) {
            Text("Pat Alpha").font(.title3.weight(.semibold))
            ForEach(vm.groups.flatMap(\.cards)) { ProposalCard(card: $0, viewModel: vm, readOnly: false) {} }
            Text("Rejected by checks: \(vm.rejectedByChecks.first?.checkError ?? "")")
                .foregroundStyle(.orange)
        }
        .padding(20)
        .frame(width: 900)
        .background(Color(nsColor: .windowBackgroundColor))
        let renderer = ImageRenderer(content: view)
        renderer.scale = 1
        let image = try #require(renderer.nsImage)
        let tiff = try #require(image.tiffRepresentation)
        let rep = try #require(NSBitmapImageRep(data: tiff))
        // attached to the test results (the sandboxed test host can't write anywhere useful)
        let png = try #require(rep.representation(using: .png, properties: [:]))
        Attachment.record(png, named: "review.png")
    }
}
