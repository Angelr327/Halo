import SwiftUI

struct SafetyStatusBadge: View {
    let level: SafetyLevel

    private var color: Color {
        switch level {
        case .safe: AppTheme.safe
        case .caution: AppTheme.caution
        case .danger: AppTheme.danger
        }
    }

    var body: some View {
        Text(level.rawValue)
            .font(.title2.weight(.black))
            .tracking(2)
            .foregroundStyle(color)
            .padding(.horizontal, 18)
            .padding(.vertical, 9)
            .background(color.opacity(0.13), in: Capsule())
            .overlay(Capsule().stroke(color.opacity(0.55)))
    }
}
