import SwiftUI

struct HazardIndicator: View {
    let side: SafetyEventSide
    let isActive: Bool

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: side == .left ? "arrow.left" : "arrow.right")
                .font(.system(size: 38, weight: .black))
            Text("\(side.rawValue.uppercased()) HAZARD")
                .font(.caption.weight(.black))
                .tracking(0.8)
        }
        .foregroundStyle(isActive ? .white : AppTheme.secondaryText)
        .frame(maxWidth: .infinity, minHeight: 112)
        .background(isActive ? AppTheme.danger : AppTheme.elevatedSurface, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).stroke(isActive ? AppTheme.danger.opacity(0.8) : .white.opacity(0.08), lineWidth: 2))
        .shadow(color: isActive ? AppTheme.danger.opacity(0.35) : .clear, radius: 14)
        .accessibilityLabel("\(side.rawValue) hazard")
        .accessibilityValue(isActive ? "Detected" : "Clear")
    }
}
