import SwiftUI

struct HaloLogoView: View {
    var width: CGFloat = 78
    var height: CGFloat = 34

    var body: some View {
        Image("HaloLogo")
            .resizable()
            .scaledToFill()
            .frame(width: width, height: height)
            .clipped()
            .accessibilityLabel("Halo")
    }
}
