import SwiftUI

struct DashboardCard<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    init(title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.medium) {
            Text(title.uppercased())
                .font(AppTypography.cardTitle)
                .tracking(0.8)
                .foregroundStyle(AppTheme.secondaryText)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .appCard()
    }
}
