import SwiftUI

/// Colored circle badge for a subway route letter/number.
struct SubwayBadge: View {
    let route: String

    var body: some View {
        Text(route)
            .font(.system(size: 11, weight: .bold, design: .rounded))
            .foregroundColor(.white)
            .frame(width: 20, height: 20)
            .background(routeColor)
            .clipShape(Circle())
    }

    private var routeColor: Color {
        switch route.uppercased() {
        // IRT lines
        case "1", "2", "3":        return Color(red: 238/255, green: 53/255, blue: 46/255)
        case "4", "5", "6":        return Color(red: 0/255, green: 147/255, blue: 60/255)
        case "7":                   return Color(red: 185/255, green: 51/255, blue: 173/255)
        // IND lines
        case "A", "C", "E":        return Color(red: 0/255, green: 57/255, blue: 166/255)
        case "B", "D", "F", "M":   return Color(red: 255/255, green: 99/255, blue: 25/255)
        case "G":                   return Color(red: 108/255, green: 190/255, blue: 69/255)
        // BMT lines
        case "J", "Z":             return Color(red: 153/255, green: 102/255, blue: 51/255)
        case "L":                   return Color(red: 167/255, green: 169/255, blue: 172/255)
        case "N", "Q", "R", "W":   return Color(red: 252/255, green: 204/255, blue: 10/255)
        // Shuttles
        case "S":                   return Color(red: 128/255, green: 129/255, blue: 131/255)
        default:                    return Color.secondary
        }
    }
}

struct SubwayRoutesView: View {
    let routes: [String]

    var body: some View {
        HStack(spacing: 3) {
            ForEach(routes, id: \.self) { route in
                SubwayBadge(route: route)
            }
        }
    }
}

#if DEBUG
#Preview {
    VStack(spacing: 8) {
        SubwayRoutesView(routes: ["1", "2", "3"])
        SubwayRoutesView(routes: ["A", "C", "E"])
        SubwayRoutesView(routes: ["N", "Q", "R", "W"])
        SubwayRoutesView(routes: ["L"])
        SubwayRoutesView(routes: ["4", "5", "6"])
    }
    .padding()
}
#endif
